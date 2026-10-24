"""Delete stored bytes when Artifact rows are deleted by any path.

revoke_artifact and the sweeper delete the file first and only then the
row (keeping the row when storage fails, so they can retry). Every other
delete (Django admin, ``Artifact.objects...delete()``, and the CASCADE from
deleting a user or workspace) goes straight through the ORM; this receiver
covers those. The storage call runs after the transaction commits, so a
rolled-back delete never loses bytes, and a missing object is not an error.

Consecutive deletes in one transaction (a cascade) share one on_commit
callback, which removes their objects in batched storage calls (1000 keys
per S3 DeleteObjects call) instead of one round-trip per artifact.

A failure here cannot be retried from the database (the row is gone), so
it is logged at ERROR with the storage key; ``manage.py reconcile_storage``
finds and removes such orphans.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Artifact

logger = logging.getLogger(__name__)

_BYTES_HANDLED = "_veilfile_bytes_handled"

# Per-artifact ERROR lines per cleanup run; any further failures are
# summed up in one line, so an outage during a large cascade cannot flood
# the logs.
_MAX_FAILURE_LOGS = 50


def mark_bytes_handled(artifact):
    """Tell the receiver the caller already dealt with this artifact's
    stored bytes (deleted them, or nothing was stored), so deleting the
    row must not trigger a second storage call or a second error log."""
    setattr(artifact, _BYTES_HANDLED, True)


class _PendingDeletes:
    """An on_commit callback deleting the stored bytes of several rows."""

    def __init__(self, item):
        # (artifact id, workspace id, token), captured at delete time:
        # Django clears instance.pk once the delete finishes.
        self.items = [item]
        self.done = False

    def __call__(self):
        self.done = True
        items, self.items = self.items, []
        _delete_bytes(items)


def _open_batch(connection):
    """The pending callback this delete can join, or None.

    Joining the most recently registered callback is the same as
    registering a new one right after it, as long as the savepoint stack is
    unchanged: Django discards on_commit callbacks by savepoint when one
    rolls back, and these would be discarded together.
    """
    if not connection.in_atomic_block:
        return None
    try:
        sids, func = connection.run_on_commit[-1][:2]
    except (AttributeError, IndexError, TypeError, ValueError):
        return None
    if (
        isinstance(func, _PendingDeletes)
        and not func.done
        and sids == set(connection.savepoint_ids)
    ):
        return func
    return None


@receiver(post_delete, sender=Artifact, dispatch_uid="drop_core.delete_artifact_bytes")
def delete_artifact_bytes(sender, instance, using, **kwargs):
    if getattr(instance, _BYTES_HANDLED, False):
        return
    item = (instance.id, instance.workspace_id, instance.token)
    batch = _open_batch(transaction.get_connection(using))
    if batch is not None:
        batch.items.append(item)
        return
    # robust: a failure here must not stop other commit callbacks.
    transaction.on_commit(_PendingDeletes(item), using=using, robust=True)


def _delete_bytes(items):
    # Imported per call so tests (and get_storage's env lookup) apply.
    from .storage import get_storage

    if len(items) > 1:
        # Logged first, so a worker killed mid-cleanup still leaves a trace.
        logger.info("deleting stored bytes of %d deleted artifacts", len(items))
    tokens = [token for _, _, token in items]
    try:
        failures = _delete_tokens(get_storage(), tokens)
    except Exception as exc:
        failures = dict.fromkeys(tokens, f"{type(exc).__name__}: {exc}")
    failed = 0
    for artifact_id, workspace_id, token in items:
        error = failures.get(token)
        if error is None:
            continue
        failed += 1
        if failed > _MAX_FAILURE_LOGS:
            continue
        # The row is already gone, so the key no longer serves anything and
        # is safe to log; an operator needs it to remove the object.
        logger.error(
            "storage delete failed for deleted artifact id=%s workspace=%s "
            "key=%s (%s); object orphaned, run reconcile_storage",
            artifact_id,
            workspace_id,
            token,
            error,
        )
    if failed > _MAX_FAILURE_LOGS:
        logger.error(
            "storage delete failed for %d more deleted artifacts; objects "
            "orphaned, run reconcile_storage",
            failed - _MAX_FAILURE_LOGS,
        )


def _delete_tokens(storage, tokens):
    delete_many = getattr(storage, "delete_many", None)
    if delete_many is not None:
        return delete_many(tokens)
    # A backend without delete_many: one call per object.
    failures = {}
    for token in tokens:
        try:
            storage.delete(token)
        except Exception as exc:
            failures[token] = f"{type(exc).__name__}: {exc}"
    return failures
