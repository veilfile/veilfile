"""Delete expired rows from the database cache table. Idempotent."""

from django.conf import settings
from django.core.cache import caches
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Delete expired entries from every cache that supports it (the production DatabaseCache)."

    def handle(self, *args, **options):
        purged = 0
        for alias in settings.CACHES:
            delete_expired = getattr(caches[alias], "delete_expired", None)
            if delete_expired is not None:
                purged += delete_expired()
        self.stdout.write(f"purged={purged}")
