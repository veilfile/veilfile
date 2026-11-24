"""Daily export of live artifacts to the second R2 bucket (Option B backup).

Copies every unexpired Artifact's bytes from the live bucket to
``AWS_S3_BACKUP_BUCKET`` (default ``deadrop-artifacts-backup``), using
``CopyObject`` — the only second-copy operation R2 supports (no versioning,
no replication). An object already in the backup bucket with the same size
is skipped, so a re-run copies only what is new or changed; the job is
idempotent and safe to run daily from the entrypoint housekeeping loop.

Copy-only: expired or deleted objects are never copied, and nothing is
ever deleted from the backup bucket, so a bad run cannot destroy the
second copy. A restore drill (restore a sample from the backup bucket and
byte-compare) is a separate step; until it passes, no "tested restores"
claim is made about file bytes.

Prints ``objects=N copied=N skipped=N failed=N source=s3:... backup=s3:...``
and exits non-zero when any copy failed. Artifact tokens are bearer
credentials: failures name only a truncated SHA-256 fingerprint, never
the token itself (same pattern as ``reconcile_storage``).
"""

import os

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from drop_core.models import Artifact
from drop_core.signals import token_fingerprint
from drop_core.storage import S3Storage, get_storage

DEFAULT_BACKUP_BUCKET = "deadrop-artifacts-backup"


class Command(BaseCommand):
    help = "Copy live (unexpired) artifacts to the backup R2 bucket."

    def add_arguments(self, parser):
        parser.add_argument(
            "--backup-bucket",
            default=None,
            help=(
                "Destination bucket (default: $AWS_S3_BACKUP_BUCKET, else "
                f"{DEFAULT_BACKUP_BUCKET})."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be copied without copying anything.",
        )

    def handle(self, *args, **options):
        storage = get_storage()
        if not isinstance(storage, S3Storage):
            # Local/dev runs of the entrypoint loop have no R2 bucket to
            # export to; skipping keeps the daily loop green there.
            self.stdout.write(
                "skipping: storage is not S3/R2 "
                f"(DROP_STORAGE={os.environ.get('DROP_STORAGE', 'local')})"
            )
            return
        backup_bucket = options["backup_bucket"] or os.environ.get(
            "AWS_S3_BACKUP_BUCKET", DEFAULT_BACKUP_BUCKET
        )
        if backup_bucket == storage.bucket:
            raise CommandError(
                f"refusing: backup bucket {backup_bucket!r} is the live bucket"
            )
        backup = S3Storage(bucket=backup_bucket)

        total = copied = skipped = would_copy = failed = 0
        live = (
            Artifact.objects.filter(expires_at__gt=timezone.now())
            .order_by("created_at")
            .values_list("token", "size_bytes")
        )
        for token, size_bytes in live:
            total += 1
            fingerprint = token_fingerprint(token)
            try:
                existing = backup.head(token)
            except Exception as exc:
                failed += 1
                self.stderr.write(
                    f"backup check failed key_sha256={fingerprint}: "
                    f"{_scrubbed(exc, token, fingerprint)}"
                )
                continue
            if existing is not None and existing["size"] == size_bytes:
                skipped += 1
                continue
            if options["dry_run"]:
                would_copy += 1
                continue
            try:
                backup.copy_from(storage.bucket, token)
            except Exception as exc:
                failed += 1
                self.stderr.write(
                    f"copy failed key_sha256={fingerprint}: "
                    f"{_scrubbed(exc, token, fingerprint)}"
                )
                continue
            try:
                after = backup.head(token)
            except Exception as exc:
                failed += 1
                self.stderr.write(
                    f"backup verify failed key_sha256={fingerprint}: "
                    f"{_scrubbed(exc, token, fingerprint)}"
                )
                continue
            if after is None or after["size"] != size_bytes:
                failed += 1
                self.stderr.write(
                    f"backup verify failed key_sha256={fingerprint}: "
                    "copied object missing or wrong size"
                )
                continue
            copied += 1

        summary = (
            f"objects={total} copied={copied} skipped={skipped} "
            f"failed={failed} source=s3:{storage.bucket} backup=s3:{backup_bucket}"
        )
        if options["dry_run"]:
            summary += f" would_copy={would_copy} (dry run; nothing copied)"
        self.stdout.write(summary)
        if failed:
            raise CommandError(f"{failed} object(s) could not be exported")


def _scrubbed(exc, token, fingerprint):
    # Storage errors embed the object URL, so the error text is scrubbed
    # of the raw token before printing.
    return f"{type(exc).__name__}: {exc}".replace(token, fingerprint)
