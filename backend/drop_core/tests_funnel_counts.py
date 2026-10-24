"""Tests for the funnel_counts management command (cio-10: daily funnel counters)."""

import io
import re
from datetime import timedelta

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from drop_core import funnel
from drop_core.models import FunnelEvent


def _day_row(output, day):
    """Return the row for one date as [signups, keys, first_uploads]."""
    for line in output.splitlines():
        if line.startswith(str(day)):
            return [int(x) for x in line[len(str(day)):].split()]
    return None


class FunnelCountsTests(TestCase):
    def _event(self, stage, age_days, upload_number=None):
        event = FunnelEvent.objects.create(stage=stage, upload_number=upload_number)
        FunnelEvent.objects.filter(pk=event.pk).update(
            created_at=timezone.now() - timedelta(days=age_days)
        )
        return event

    def _run(self, *args):
        out = io.StringIO()
        call_command("funnel_counts", *args, stdout=out)
        return out.getvalue()

    def test_counts_events_per_stage_per_day(self):
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        self._event(funnel.SIGNUP_COMPLETED, 0)
        self._event(funnel.SIGNUP_COMPLETED, 0)
        self._event(funnel.SIGNUP_COMPLETED, 1)
        self._event(funnel.KEY_ISSUED, 1)
        self._event(funnel.UPLOAD_COMPLETED, 0, upload_number=1)
        self._event(funnel.UPLOAD_COMPLETED, 0, upload_number=2)

        output = self._run("--days", "2")
        self.assertEqual(_day_row(output, today), [2, 0, 1])
        self.assertEqual(_day_row(output, yesterday), [1, 1, 0])

    def test_totals_line(self):
        self._event(funnel.SIGNUP_COMPLETED, 0)
        self._event(funnel.KEY_ISSUED, 0)
        output = self._run("--days", "3")
        total_line = [line for line in output.splitlines() if line.startswith("TOTAL")]
        self.assertEqual(len(total_line), 1)
        self.assertIn("1", total_line[0])
        counts = [int(x) for x in re.findall(r"\d+", total_line[0])]
        self.assertEqual(counts, [1, 1, 0])

    def test_is_read_only(self):
        before = list(FunnelEvent.objects.values_list("pk", flat=True))
        self._run("--days", "7")
        after = list(FunnelEvent.objects.values_list("pk", flat=True))
        self.assertEqual(before, after)

    def test_bad_days_refused(self):
        for days in ("0", "-5"):
            with self.subTest(days=days):
                with self.assertRaisesMessage(CommandError, "--days must be at least 1"):
                    self._run("--days", days)
