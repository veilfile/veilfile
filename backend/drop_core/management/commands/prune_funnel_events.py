"""Delete funnel events older than the retention window. Idempotent."""

from django.core.management.base import BaseCommand, CommandError

from drop_core import funnel


class Command(BaseCommand):
    help = (
        "Delete FunnelEvent rows older than --days (default: "
        f"${funnel.RETENTION_ENV} or {funnel.DEFAULT_RETENTION_DAYS})."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Keep this many days of events.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days is not None and days < 1:
            # Non-zero exit, so a scheduler sees the misconfiguration.
            raise CommandError("--days must be at least 1")
        days = days or funnel.retention_days()
        deleted = funnel.prune_events(days)
        self.stdout.write(f"pruned={deleted} days={days}")
