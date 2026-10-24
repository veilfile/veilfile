"""Production-configuration checks.

Settings fall back to developer defaults (console email, local storage, a
localhost base URL, safety scanning off, an in-process cache) so that
``runserver`` works with no environment. With DEBUG=False each fallback is
almost always a lost or mistyped env var, so they are reported two ways
instead of refusing to boot:

- as Django system check warnings (``veilfile.W001``..``W006``) under
  ``manage.py check --deploy`` (``--tag veilfile`` for just these);
- as ERROR log lines when the web app starts (config.wsgi / config.asgi),
  so they show up in Render's logs.

SQLite is the exception: settings.py refuses to start on it (an unset or
sqlite:// DATABASE_URL), because the file is lost on the next deploy. If
DJANGO_ALLOW_SQLITE=True overrides that, veilfile.W006 still reports it.
"""

import logging
import os

from django.conf import settings
from django.core.checks import Warning, register

logger = logging.getLogger(__name__)

# Backends that accept a message and never deliver it.
_UNDELIVERED_EMAIL_BACKENDS = {
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
    "django.core.mail.backends.filebased.EmailBackend",
    "django.core.mail.backends.locmem.EmailBackend",
}

_PROCESS_LOCAL_CACHES = {
    "django.core.cache.backends.locmem.LocMemCache",
    "django.core.cache.backends.dummy.DummyCache",
}


def production_problems():
    """Return [(check id, message, hint)] for every dev fallback in effect
    while DEBUG is False; empty in development."""
    if settings.DEBUG:
        return []
    from drop_core.safety import MODE_OFF, VALID_MODES

    problems = []
    if settings.EMAIL_BACKEND in _UNDELIVERED_EMAIL_BACKENDS:
        problems.append((
            "veilfile.W001",
            f"EMAIL_BACKEND is {settings.EMAIL_BACKEND!r}: verification and "
            "password-reset emails are not delivered, so new users cannot "
            "verify, mint API keys, approve CLI logins or authorize MCP clients.",
            "Set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend "
            "and the EMAIL_HOST/EMAIL_PORT/EMAIL_HOST_USER/EMAIL_HOST_PASSWORD "
            "env vars.",
        ))
    raw_mode = os.environ.get("VEILFILE_SAFETY_MODE", "")
    mode = raw_mode.strip().lower()
    if not mode:
        detail = "VEILFILE_SAFETY_MODE is unset (defaults to 'off')"
    elif mode not in VALID_MODES:
        detail = (f"VEILFILE_SAFETY_MODE={raw_mode!r} is not one of "
                  f"{', '.join(VALID_MODES)} and is treated as 'off'")
    elif mode == MODE_OFF:
        detail = "VEILFILE_SAFETY_MODE is 'off'"
    else:
        detail = None
    if detail:
        problems.append((
            "veilfile.W002",
            f"{detail}: uploads are not screened against the blocked-hash list.",
            "Set VEILFILE_SAFETY_MODE=log (record verdicts) or enforce "
            "(refuse blocked uploads).",
        ))
    if os.environ.get("DROP_STORAGE", "local").lower() != "s3":
        problems.append((
            "veilfile.W003",
            "DROP_STORAGE is not 's3': artifacts are written to the "
            "container's local disk, which is lost on every deploy or restart.",
            "Set DROP_STORAGE=s3 and the AWS_S3_*/AWS_* env vars (Cloudflare R2).",
        ))
    if not settings.DROP_BASE_URL.startswith("https://"):
        problems.append((
            "veilfile.W004",
            f"DROP_BASE_URL is {settings.DROP_BASE_URL!r}: artifact links, the "
            "OAuth issuer and Stripe return URLs are built from it.",
            "Set DROP_BASE_URL to the public https:// origin "
            "(https://veilfile.com).",
        ))
    if settings.CACHES["default"]["BACKEND"] in _PROCESS_LOCAL_CACHES:
        problems.append((
            "veilfile.W005",
            "The default cache is process-local: rate limits are multiplied "
            "by the number of gunicorn workers and reset on every restart.",
            "Use the shared database cache (config.cache.VeilfileDatabaseCache "
            "plus `manage.py createcachetable`).",
        ))
    if settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
        problems.append((
            "veilfile.W006",
            "The default database is SQLite (DJANGO_ALLOW_SQLITE=True): on "
            "Render the file is on the container's disk, so users, keys and "
            "the Stripe mapping are lost on every deploy or restart.",
            "Set DATABASE_URL to the Postgres connection string and remove "
            "DJANGO_ALLOW_SQLITE.",
        ))
    return problems


@register("veilfile", deploy=True)
def check_production_configuration(app_configs, **kwargs):
    return [
        Warning(message, hint=hint, id=check_id)
        for check_id, message, hint in production_problems()
    ]


def log_production_problems():
    """Log each production fallback at ERROR (called when the web app
    starts, so the problem is visible in the service logs)."""
    for check_id, message, hint in production_problems():
        logger.error("Production misconfiguration (%s): %s %s", check_id, message, hint)
