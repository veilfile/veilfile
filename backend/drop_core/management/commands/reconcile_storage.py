"""Find (and optionally delete) stored objects that no Artifact row points at.

Backstop for bytes the normal paths could not delete: a storage outage
while a row was removed by the admin or a user/workspace cascade (logged at
ERROR by drop_core.signals), or rows lost to a database restore. Dry run by
default; ``--delete`` removes the orphans. Objects younger than
``--grace-hours`` are skipped so an in-flight upload whose row is not yet
committed is never touched, and keys that are not artifact tokens are never
considered.

``--delete`` is irreversible, so it refuses to run when the database looks
wrong for the storage it is pointed at: S3/R2 storage with a database that
is not PostgreSQL (DATABASE_URL unset falls back to a local SQLite file),
an empty Artifact table, or more than a fifth of the artifact objects
without a row. Run the dry run, check the counts and the ``db=`` and
``storage=`` fields, then pass ``--force`` to delete anyway. Do not run
``--delete`` unattended.

Prints ``objects=N orphans=N deleted=N failed=N db=... storage=...`` and
exits non-zero when any delete failed. Orphan keys are printed: with no row
behind them they no longer serve anything.
"""

import datetime
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from drop_core.models import Artifact

# Artifact tokens are secrets.token_urlsafe(32): 43 URL-safe characters.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
_BATCH = 1000
# More orphans than this share of the artifact objects past the grace
# period means a wrong or restored database, not a few failed deletes.
_MAX_ORPHAN_SHARE = 0.2


class Command(BaseCommand):
    help = (
        "List stored objects with no matching Artifact row; pass --delete "
        "to remove them."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Delete the orphaned objects (default: report only).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "With --delete: delete even when the Artifact table is empty "
                "or more than 20%% of the objects are orphans."
            ),
        )
        parser.add_argument(
            "--grace-hours",
            type=float,
            default=24.0,
            help="Skip objects modified more recently than this (min 1, default 24).",
        )

    def handle(self, *args, **options):
        grace_hours = options["grace_hours"]
        if grace_hours < 1:
            raise CommandError("--grace-hours must be at least 1")
        cutoff = timezone.now() - datetime.timedelta(hours=grace_hours)
        from drop_core.storage import S3Storage, get_storage

        storage = get_storage()
        target = f"db={_db_label()} storage={_storage_label(storage)}"
        if (
            options["delete"]
            and isinstance(storage, S3Storage)
            and connection.vendor != "postgresql"
        ):
            raise CommandError(
                f"refusing --delete: S3/R2 storage is only reconciled against "
                f"PostgreSQL ({target}); is DATABASE_URL set?"
            )

        total = candidates = 0
        orphans = []
        batch = []
        for key, modified in storage.list_objects():
            total += 1
            if not _TOKEN_RE.match(key) or modified > cutoff:
                continue
            candidates += 1
            batch.append(key)
            if len(batch) >= _BATCH:
                orphans.extend(self._without_rows(batch))
                batch = []
        if batch:
            orphans.extend(self._without_rows(batch))

        problem = self._suspicious(candidates, len(orphans))
        if problem and options["delete"] and not options["force"]:
            self.stdout.write(
                f"objects={total} orphans={len(orphans)} deleted=0 failed=0 {target}"
            )
            raise CommandError(
                f"refusing --delete: {problem}. Is this the right database? "
                "Check the dry run, then pass --force if this is expected."
            )
        if problem:
            self.stderr.write(f"warning: {problem}")

        deleted = failed = 0
        for key in orphans:
            if not options["delete"]:
                self.stdout.write(f"orphan {key}")
                continue
            try:
                storage.delete(key)
            except Exception as exc:
                failed += 1
                self.stderr.write(f"delete failed {key}: {type(exc).__name__}: {exc}")
                continue
            deleted += 1
            self.stdout.write(f"deleted {key}")

        summary = (
            f"objects={total} orphans={len(orphans)} deleted={deleted} "
            f"failed={failed} {target}"
        )
        if not options["delete"] and orphans:
            summary += " (dry run; pass --delete to remove)"
        self.stdout.write(summary)
        if failed:
            raise CommandError(f"{failed} orphaned object(s) could not be deleted")

    @staticmethod
    def _without_rows(keys):
        known = set(
            Artifact.objects.filter(token__in=keys).values_list("token", flat=True)
        )
        return [key for key in keys if key not in known]

    @staticmethod
    def _suspicious(candidates, orphans):
        if not orphans:
            return None
        if not Artifact.objects.exists():
            return (
                f"the Artifact table is empty but storage holds {candidates} "
                "artifact object(s)"
            )
        if orphans > candidates * _MAX_ORPHAN_SHARE:
            return (
                f"{orphans} of {candidates} artifact objects "
                f"({orphans / candidates:.0%}) have no row"
            )
        return None


def _db_label():
    # Vendor, name and host only: never the user or password.
    settings = connection.settings_dict
    label = f"{connection.vendor}:{settings.get('NAME')}"
    if settings.get("HOST"):
        label += f"@{settings['HOST']}"
    return label


def _storage_label(storage):
    bucket = getattr(storage, "bucket", None)
    if bucket:
        return f"s3:{bucket}"
    return f"local:{getattr(storage, 'root', '?')}"
