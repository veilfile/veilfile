"""Print daily counts for the three funnel stages: signups -> keys -> first uploads.

Read-only: counts FunnelEvent rows grouped by day. Day boundaries follow
the server timezone (UTC), so a day is a UTC day. Older than the funnel retention
window, the pruned days simply show as zeros.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from drop_core import funnel
from drop_core.models import FunnelEvent


def _events_per_day(stage, start, upload_number=None):
    """Map of date -> event count for one funnel stage since ``start``."""
    qs = FunnelEvent.objects.filter(stage=stage, created_at__gte=start)
    if upload_number is not None:
        qs = qs.filter(upload_number=upload_number)
    return dict(
        qs.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(n=Count("id"))
        .values_list("day", "n")
    )


class Command(BaseCommand):
    help = (
        "Print daily funnel counts (signups, API keys issued, first uploads). "
        "Read-only."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Number of days to show, ending today (default: 30).",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days < 1:
            raise CommandError("--days must be at least 1")

        today = timezone.localdate()
        start_date = today - timedelta(days=days - 1)
        start = timezone.make_aware(
            timezone.datetime.combine(start_date, timezone.datetime.min.time())
        )

        signups = _events_per_day(funnel.SIGNUP_COMPLETED, start)
        keys = _events_per_day(funnel.KEY_ISSUED, start)
        first_uploads = _events_per_day(
            funnel.UPLOAD_COMPLETED, start, upload_number=1
        )

        self.stdout.write(
            f"Veilfile funnel — daily counts (days end {today}, "
            f"server timezone {timezone.get_current_timezone_name()})"
        )
        self.stdout.write(f"{'date':<12}{'signups':>9}{'keys':>7}{'first_uploads':>14}")
        totals = {"signups": 0, "keys": 0, "first_uploads": 0}
        for offset in range(days):
            day = today - timedelta(days=days - 1 - offset)
            counts = {
                "signups": signups.get(day, 0),
                "keys": keys.get(day, 0),
                "first_uploads": first_uploads.get(day, 0),
            }
            for name, count in counts.items():
                totals[name] += count
            self.stdout.write(
                f"{day.isoformat():<12}{counts['signups']:>9}{counts['keys']:>7}"
                f"{counts['first_uploads']:>14}"
            )
        self.stdout.write(
            f"{'TOTAL':<12}{totals['signups']:>9}{totals['keys']:>7}"
            f"{totals['first_uploads']:>14}"
        )
