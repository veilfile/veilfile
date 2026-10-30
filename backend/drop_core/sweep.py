"""Expired-artifact sweeping.

The sweep deletes expired artifacts' files (R2/local) and DB rows. It is
idempotent. Three layers run it:

- ``sweep_expired()`` — run the sweep now; returns a ``SweepResult``
  (swept, freed bytes, failed deletes, whether every expired row was
  visited). The ``sweep_expired`` command exits non-zero when any delete
  failed.
- ``maybe_sweep()`` — run a small, time-boxed sweep at most once every 24h
  (sooner while a backlog remains); safe to call on every upload, and never
  holds an upload up for more than about one batch.
- The container entrypoint (``entrypoint.sh``) runs it at startup and then
  every 24h in a background loop, so expiry is honored even with no uploads.

A storage-delete failure keeps the DB row so the next run retries; the row
is only deleted after the file is gone, so files are never orphaned.
Revoked artifacts whose storage delete failed are kept expired, so the
sweep retries those too. Sweep failures never propagate from maybe_sweep.
"""

import logging
import time
from collections import namedtuple

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 24 * 60 * 60
_CACHE_KEY = "drop_last_sweep_ts"

# Request-path sweep bounds. Each delete is one storage round-trip, so a
# day's backlog must not land on one upload (gunicorn kills a sync worker
# after 30s). The background loop does the bulk; this is the backstop.
LAZY_SWEEP_BATCH_SIZE = 50
LAZY_SWEEP_TIME_BUDGET_SECONDS = 2.0
# When a lazy sweep leaves work behind (batch full, budget spent, or a
# storage failure), the next one may run this soon instead of in 24h.
LAZY_SWEEP_RETRY_SECONDS = 10 * 60

SweepResult = namedtuple("SweepResult", "swept freed failed complete")


def sweep_expired(limit=None, time_budget=None, stop_on_failure=False):
    """Delete expired artifacts, oldest expiry first.

    ``limit`` caps how many rows are visited, ``time_budget`` (seconds)
    stops starting new deletes once spent, and ``stop_on_failure`` stops at
    the first storage error (in an outage every later delete would fail
    too). ``complete`` is False when any of these cut the run short.
    """
    from .models import Artifact, PendingUpload
    from .signals import mark_bytes_handled
    from .storage import get_storage

    storage = get_storage()
    started = time.monotonic()
    expired = Artifact.objects.filter(expires_at__lte=timezone.now()).order_by(
        "expires_at"
    )
    if limit is not None:
        expired = expired[:limit]
    swept = freed = failed = visited = 0
    complete = True
    for artifact in expired.iterator():
        if time_budget is not None and time.monotonic() - started >= time_budget:
            complete = False
            break
        visited += 1
        try:
            storage.delete(artifact.token)
        except Exception as exc:
            # Keep the row so the next run retries. Deleting the row here
            # would orphan the file in storage with no metadata pointing
            # at it.
            failed += 1
            logger.warning(
                "sweep: storage delete failed for id=%s workspace=%s (%s); "
                "will retry",
                artifact.id,
                artifact.workspace_id,
                type(exc).__name__,
            )
            if stop_on_failure:
                complete = False
                break
            continue
        freed += artifact.size_bytes or 0
        mark_bytes_handled(artifact)
        artifact.delete()
        swept += 1
    if limit is not None and visited >= limit:
        # A full batch: more expired rows may be waiting.
        complete = False
    if failed:
        logger.error(
            "sweep: %d storage delete(s) failed; rows kept for the next run",
            failed,
        )
    # One-time upload URLs: expired rows are inert (the claim query ignores
    # them) but should not accumulate.
    PendingUpload.objects.filter(expires_at__lte=timezone.now()).delete()
    # Stale device-login grants; a failure here must not fail the sweep.
    try:
        from .device import purge_stale_device_codes

        purge_stale_device_codes()
    except Exception:
        logger.exception("sweep: purging stale device codes failed")
    return SweepResult(swept, freed, failed, complete)


def maybe_sweep():
    """Run a bounded sweep if one hasn't run in the last 24h. Never raises."""
    try:
        now = time.time()
        last = cache.get(_CACHE_KEY)
        if last and now - last < SWEEP_INTERVAL_SECONDS:
            return
        # Claim the slot before sweeping, so concurrent uploads don't all
        # start a sweep and a crash mid-sweep doesn't retry on every upload.
        cache.set(_CACHE_KEY, now, timeout=SWEEP_INTERVAL_SECONDS * 2)
        result = sweep_expired(
            limit=LAZY_SWEEP_BATCH_SIZE,
            time_budget=LAZY_SWEEP_TIME_BUDGET_SECONDS,
            stop_on_failure=True,
        )
        if not result.complete:
            # Backlog left (or storage failing): come back in minutes.
            cache.set(
                _CACHE_KEY,
                now - SWEEP_INTERVAL_SECONDS + LAZY_SWEEP_RETRY_SECONDS,
                timeout=SWEEP_INTERVAL_SECONDS * 2,
            )
        if result.swept:
            logger.info(
                "sweep: removed %d expired artifacts (%d bytes)",
                result.swept,
                result.freed,
            )
    except Exception:
        logger.exception("sweep: unexpected failure")
