"""Blank IP addresses on email send-log rows older than the retention window.
Idempotent."""

from django.core.management.base import BaseCommand, CommandError

from drop_core.email_verification import EMAIL_IP_RETENTION_DAYS, clear_old_send_ips


class Command(BaseCommand):
    help = (
        "Clear EmailSendLog.ip_address on rows older than --days "
        f"(default: {EMAIL_IP_RETENTION_DAYS}, the privacy policy's promise)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=EMAIL_IP_RETENTION_DAYS,
            help="Keep IP addresses for this many days.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days < 1:
            # Non-zero exit, so a scheduler sees the misconfiguration.
            raise CommandError("--days must be at least 1")
        cleared = clear_old_send_ips(days)
        self.stdout.write(f"cleared_ips={cleared} days={days}")
