"""Send-log IP addresses are cleared after 90 days, as the privacy policy says."""

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from .email_verification import EMAIL_IP_RETENTION_DAYS, clear_old_send_ips
from .models import EmailSendLog


class EmailIpRetentionTests(TestCase):
    def _log(self, days_old, ip="81.2.69.160"):
        row = EmailSendLog.objects.create(
            email="someone@example.com",
            ip_address=ip,
            purpose=EmailSendLog.PURPOSE_SIGNUP_ATTEMPT,
        )
        EmailSendLog.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(days=days_old)
        )
        return row.pk

    def test_policy_window_is_90_days(self):
        self.assertEqual(EMAIL_IP_RETENTION_DAYS, 90)

    def test_clears_only_ips_older_than_the_window(self):
        old = self._log(91)
        recent = self._log(89)
        self.assertEqual(clear_old_send_ips(), 1)
        self.assertIsNone(EmailSendLog.objects.get(pk=old).ip_address)
        self.assertEqual(EmailSendLog.objects.get(pk=recent).ip_address, "81.2.69.160")

    def test_keeps_the_rest_of_the_row(self):
        old = self._log(120)
        clear_old_send_ips()
        row = EmailSendLog.objects.get(pk=old)
        self.assertEqual(row.email, "someone@example.com")
        self.assertEqual(row.purpose, EmailSendLog.PURPOSE_SIGNUP_ATTEMPT)

    def test_idempotent(self):
        self._log(100)
        self.assertEqual(clear_old_send_ips(), 1)
        self.assertEqual(clear_old_send_ips(), 0)

    def test_command_reports_and_validates(self):
        self._log(200)
        out = StringIO()
        call_command("prune_email_ips", stdout=out)
        self.assertEqual(out.getvalue().strip(), "cleared_ips=1 days=90")
        with self.assertRaises(CommandError):
            call_command("prune_email_ips", "--days", "0")
