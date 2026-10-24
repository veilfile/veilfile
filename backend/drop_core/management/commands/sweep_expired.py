"""Delete expired artifacts (storage files + rows). Idempotent.

Prints ``swept=N bytes=N failed=N`` and exits non-zero when any storage
delete failed, so cron jobs and agents notice an R2 outage or a bad
credential instead of reading it as "nothing expired". ``--limit`` and
``--time-budget`` bound a run (e.g. at container start, before gunicorn
binds); the rest is left for the next run.
"""

from django.core.management.base import BaseCommand, CommandError

from drop_core.sweep import sweep_expired


class Command(BaseCommand):
    help = (
        "Delete expired artifacts from storage and the database. Exits "
        "non-zero when any storage delete failed (those rows are kept and "
        "retried on the next run)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Visit at most this many expired artifacts (default: all).",
        )
        parser.add_argument(
            "--time-budget",
            type=float,
            default=None,
            help="Stop starting new deletes after this many seconds (default: none).",
        )

    def handle(self, *args, **options):
        result = sweep_expired(
            limit=options["limit"], time_budget=options["time_budget"]
        )
        summary = f"swept={result.swept} bytes={result.freed} failed={result.failed}"
        if not result.complete:
            summary += " (stopped early; the next run continues)"
        self.stdout.write(summary)
        if result.failed:
            raise CommandError(
                f"{result.failed} expired artifact(s) could not be deleted from "
                "storage; their rows were kept and the next run retries them"
            )
