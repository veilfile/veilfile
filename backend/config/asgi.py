"""ASGI config for the Veilfile project."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_asgi_application()

# Report production fallbacks (console email, safety mode off, ...) in every
# worker's boot log; see config.checks.
from config.checks import log_production_problems  # noqa: E402

log_production_problems()
