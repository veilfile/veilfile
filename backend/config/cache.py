"""Production cache backend (settings.CACHES when DEBUG is False).

Every rate limit in the app stores a counter with an explicit window
(``cache.add``/``cache.set(key, 1, timeout=3600)``) and then ``incr()``s it,
relying on the increment leaving that window alone. LocMemCache (development
and tests) behaves that way. Django's DatabaseCache does not implement
``incr()``: it inherits BaseCache.incr, a get() followed by set() with the
*default* timeout, so each increment moved the counter's expiry to 300 s
from now (an hourly limit became a five-minute one), and two threads
incrementing at once could both write the same value.

VeilfileDatabaseCache fixes that and adds two helpers:

- ``incr()``/``decr()`` change the value only, under a row lock where the
  database supports one (Postgres), and keep the row's expiry;
- ``ttl()`` returns the seconds left on a key, which the rate limiters
  already ask for (``cache.ttl``) to fill in Retry-After;
- ``delete_expired()`` removes expired rows; the daily housekeeping loop
  runs it through ``manage.py purge_expired_cache``. DatabaseCache itself
  only deletes an expired row when it is read again or when the table
  passes MAX_ENTRIES.
"""

import base64
import math
import pickle

from django.core.cache.backends.db import DatabaseCache
from django.db import connections, models, router, transaction
from django.utils.timezone import now as tz_now


class VeilfileDatabaseCache(DatabaseCache):
    def _to_datetime(self, connection, expires):
        # Same conversion DatabaseCache.get_many applies to the column.
        expression = models.Expression(output_field=models.DateTimeField())
        converters = connection.ops.get_db_converters(
            expression
        ) + expression.get_db_converters(connection)
        for converter in converters:
            expires = converter(expires, expression, connection)
        return expires

    def incr(self, key, delta=1, version=None):
        """Add ``delta`` to a live value without changing when it expires.
        Raise ValueError when the key is missing or expired, like every
        Django cache backend."""
        cache_key = self.make_and_validate_key(key, version=version)
        db = router.db_for_write(self.cache_model_class)
        connection = connections[db]
        quote_name = connection.ops.quote_name
        table = quote_name(self._table)
        select = "SELECT %s, %s FROM %s WHERE %s = %%s" % (
            quote_name("value"),
            quote_name("expires"),
            table,
            quote_name("cache_key"),
        )
        if connection.features.has_select_for_update:
            # Concurrent increments of the same counter queue on the row
            # instead of overwriting each other.
            select += " FOR UPDATE"

        with transaction.atomic(using=db), connection.cursor() as cursor:
            cursor.execute(select, [cache_key])
            row = cursor.fetchone()
            if row is None or self._to_datetime(connection, row[1]) < tz_now():
                raise ValueError("Key '%s' not found" % key)
            # Same encoding as DatabaseCache.get_many/_base_set: the table is
            # written only by this cache backend, never from request data.
            value = connection.ops.process_clob(row[0])
            value = pickle.loads(base64.b64decode(value.encode(), validate=True))
            new_value = value + delta
            pickled = pickle.dumps(new_value, self.pickle_protocol)
            cursor.execute(
                "UPDATE %s SET %s = %%s WHERE %s = %%s"
                % (table, quote_name("value"), quote_name("cache_key")),
                [base64.b64encode(pickled).decode("latin1"), cache_key],
            )
        return new_value

    def ttl(self, key, version=None):
        """Seconds until ``key`` expires, rounded up; 0 when it is missing or
        already expired."""
        cache_key = self.make_and_validate_key(key, version=version)
        db = router.db_for_read(self.cache_model_class)
        connection = connections[db]
        quote_name = connection.ops.quote_name
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT %s FROM %s WHERE %s = %%s"
                % (
                    quote_name("expires"),
                    quote_name(self._table),
                    quote_name("cache_key"),
                ),
                [cache_key],
            )
            row = cursor.fetchone()
        if row is None:
            return 0
        remaining = (self._to_datetime(connection, row[0]) - tz_now()).total_seconds()
        return max(0, math.ceil(remaining))

    def delete_expired(self):
        """Delete every expired row. Returns the number deleted."""
        db = router.db_for_write(self.cache_model_class)
        connection = connections[db]
        quote_name = connection.ops.quote_name
        now = tz_now().replace(microsecond=0)
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM %s WHERE %s < %%s"
                % (quote_name(self._table), quote_name("expires")),
                [connection.ops.adapt_datetimefield_value(now)],
            )
            return cursor.rowcount
