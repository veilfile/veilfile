"""Veilfile Django settings — env-driven, 12-factor style."""

import os
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


def _required_env(name):
    value = os.environ.get(name)
    if not value:
        raise ImproperlyConfigured(
            f"{name} must be set when DEBUG is False."
        )
    return value


DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True"

if DEBUG:
    SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-dev-only-key")
else:
    SECRET_KEY = _required_env("DJANGO_SECRET_KEY")

_allowed = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]
ALLOWED_HOSTS = _allowed or (["*"] if DEBUG else [])

# Render terminates TLS at its proxy and forwards plain HTTP to the
# container. Without this Django believes every request is insecure, which
# breaks CSRF origin checking (signup/login POSTs 403) and Secure cookies.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Production is HTTPS-only: the session cookie (dashboard, key minting,
# device/OAuth approval) and the CSRF cookie must never ride a plain-HTTP
# request, and browsers should refuse plain HTTP for the domain (HSTS).
# DEBUG keeps both off so local http://localhost logins work.
#
# SECURE_SSL_REDIRECT stays off on purpose: Render already redirects HTTP to
# HTTPS at its edge, so public traffic always arrives with X-Forwarded-Proto:
# https. The only plain-HTTP requests that reach the container are Render's
# own probes from inside its network, which must not be bounced to the
# public URL. security.W008 is silenced below for that reason.
#
# HSTS starts at one year without includeSubDomains/preload (www.veilfile.com
# is not verified on Render yet). Raise or extend it with the env vars once
# every subdomain serves HTTPS; HSTS is cached by browsers and hard to undo.
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = (
        os.environ.get("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", "False") == "True"
    )
    SECURE_HSTS_PRELOAD = os.environ.get("DJANGO_SECURE_HSTS_PRELOAD", "False") == "True"

SILENCED_SYSTEM_CHECKS = ["security.W008"]

# Django checks the Origin header on HTTPS POSTs against this list.
# Derived from the allowed hosts so the production domain works without an
# extra env var; DJANGO_CSRF_TRUSTED_ORIGINS overrides when set.
_explicit_csrf = [
    h.strip()
    for h in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if h.strip()
]
_derived_csrf = [
    f"https://{h}" for h in _allowed if h and h != "*" and "://" not in h
]
CSRF_TRUSTED_ORIGINS = _explicit_csrf or _derived_csrf
# A POST that fails the CSRF check gets the shell's "Form not sent" page (pages/errors.py, spec 4.9).
CSRF_FAILURE_VIEW = "pages.errors.csrf_failure"

# Public base URL used to build absolute artifact links (https://... in prod).
DROP_BASE_URL = os.environ.get("DROP_BASE_URL", "http://localhost:8000").rstrip("/")


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "oauth2_provider",
    "drop_core",
    "drop_billing",
    "drop_mcp",
    "pages.apps.PagesConfig",
    "dashboard.apps.DashboardConfig",
    # Production-configuration system checks (config.checks).
    "config.apps.PlatformConfig",
]

MIDDLEWARE = [
    # First: answers GET /healthz before host validation, sessions or
    # funnel tracking (config.health).
    "config.health.HealthCheckMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "pages.context_processors.shell_page",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Isolates `manage.py test` from the shell's environment: local artifact
# storage in a temp dir, no VEILFILE_*/STRIPE_*/AWS_* leaking in.
TEST_RUNNER = "config.test_runner.VeilfileTestRunner"


# Database — DATABASE_URL (postgres) or local sqlite fallback.

def _database_from_url(url):
    parts = urlparse(url)
    if parts.scheme in ("postgres", "postgresql"):
        # Credentials are percent-encoded in a URL (p%40ss -> p@ss); query
        # parameters such as ?sslmode=require are libpq connection options.
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parts.path.lstrip("/")),
            "USER": unquote(parts.username or ""),
            "PASSWORD": unquote(parts.password or ""),
            "HOST": parts.hostname or "",
            "PORT": str(parts.port) if parts.port else "",
            "OPTIONS": dict(parse_qsl(parts.query)),
        }
    if parts.scheme == "sqlite":
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": parts.path or str(BASE_DIR / "db.sqlite3"),
        }
    raise ImproperlyConfigured(f"Unsupported DATABASE_URL scheme: {parts.scheme!r}")


# Production must never run on SQLite: on Render the file lives on the
# container's throwaway disk, so every deploy or restart would silently wipe
# users, keys and the Stripe mapping. That covers both an unset DATABASE_URL
# and a sqlite:// one. DJANGO_ALLOW_SQLITE=True is the explicit escape hatch
# (e.g. a local DEBUG=False smoke run, or the Docker build's collectstatic,
# which never opens the database); veilfile.W006 still reports it.
_database_url = os.environ.get("DATABASE_URL")
_allow_sqlite = DEBUG or os.environ.get("DJANGO_ALLOW_SQLITE") == "True"
if _database_url:
    _default_db = _database_from_url(_database_url)
elif _allow_sqlite:
    _default_db = {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}
else:
    raise ImproperlyConfigured(
        "DATABASE_URL must be set when DEBUG is False "
        "(set DJANGO_ALLOW_SQLITE=True to use the local SQLite file deliberately)."
    )
if _default_db["ENGINE"] == "django.db.backends.sqlite3" and not _allow_sqlite:
    raise ImproperlyConfigured(
        "DATABASE_URL points at SQLite while DEBUG is False; production needs "
        "Postgres (set DJANGO_ALLOW_SQLITE=True to use SQLite deliberately)."
    )
# Reuse connections across requests (each gunicorn thread keeps its own) and
# check them before reuse so a Postgres restart costs one reconnect, not a 500.
_default_db["CONN_MAX_AGE"] = int(os.environ.get("DJANGO_CONN_MAX_AGE", "60"))
_default_db["CONN_HEALTH_CHECKS"] = True
DATABASES = {"default": _default_db}


# Cache — rate limits, throttles and the lazy-sweep marker live here, so in
# production they must be shared by every gunicorn worker and instance
# (a per-process LocMemCache multiplies every limit by the worker count and
# forgets it on restart). A database cache needs no extra infrastructure; the
# table is created by `manage.py createcachetable` in entrypoint.sh, and
# expired rows are purged daily there. config.cache.VeilfileDatabaseCache is
# Django's DatabaseCache with an incr() that keeps each counter's window (the
# stock one resets it to 300 s on every increment). DEBUG and tests keep the
# in-process cache.
if DEBUG:
    CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "config.cache.VeilfileDatabaseCache",
            "LOCATION": "veilfile_cache",
            # The default (300) would cull live rate-limit counters under a
            # burst of distinct keys; culling deletes expired rows first.
            "OPTIONS": {"MAX_ENTRIES": 20000},
        }
    }


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# Static files: WhiteNoise serves what collectstatic gathers from backend/static.

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# backend/static holds the shell's CSS, JS, fonts and images, and there is no build step: it is the only static source.
STATICFILES_DIRS = [BASE_DIR / "static"]

# Hashed, compressed static files in production (long-cacheable). Local runs with DEBUG on get
# plain names; the test runner swaps in plain storage (config/test_runner.py).
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}


# Django REST Framework — session auth by default; API-key auth is
# opt-in per view (drop_core.auth.APIKeyAuthentication). No global
# IsAuthenticated: the landing page and /a/<token> are public.

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        # APIKeyAuthentication first: it handles both vlf_ API keys and
        # OAuth bearer tokens (Phase 2), resolving either to the workspace
        # principal. It returns None (no attempt) when no Authorization
        # header is present, so session-based dashboard traffic falls
        # through to SessionAuthentication unchanged, including its CSRF
        # enforcement. Views that must stay session-only (billing) pin
        # authentication_classes explicitly.
        "drop_core.auth.APIKeyAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    # Rewrite DRF's {"detail": ...} failures (auth, throttle, validation)
    # into the {"error", "code"} envelope. Statuses and headers unchanged.
    "EXCEPTION_HANDLER": "drop_core.errors.veilfile_exception_handler",
}

# django-oauth-toolkit (Phase 2: MCP-spec OAuth authorization server).
# Access tokens are a parallel credential to vlf_ API keys: a token's user
# resolves to the same Workspace principal (drop_core.oauth_adapter).
OAUTH2_PROVIDER = {
    "SCOPES": {
        "mcp:use": (
            "Use Veilfile MCP tools and the agent API (upload, list, revoke "
            "artifacts; read plan usage and the account email)"
        ),
    },
    "DEFAULT_SCOPES": ["mcp:use"],
    "PKCE_REQUIRED": True,  # S256 only for DCR/public clients; plain rejected
    # RFC 9700 best practice: refuse the deprecated PKCE "plain" method
    # (S256 only).
    "COMPLIANT_BCP_RFC9700_PKCE_METHOD": True,
    # RFC 9700: refuse the password and implicit grants outright, even for
    # a client row registered with them. Both bypass the consent page and
    # its email-verification gate. DCR only registers authorization_code
    # clients (drop_core.oauth), so these are belt and braces.
    "COMPLIANT_BCP_RFC9700_PASSWORD_GRANT": True,
    "COMPLIANT_BCP_RFC9700_IMPLICIT_GRANT": True,
    # Stock validator, plus: a deactivated user's code or refresh token
    # mints no tokens (suspension stops refresh rotation too).
    "OAUTH2_VALIDATOR_CLASS": "drop_core.oauth.VeilfileOAuth2Validator",
    # RFC 8414 discovery advertises only what the server accepts.
    "OAUTH2_GRANT_TYPES_SUPPORTED": ["authorization_code", "refresh_token"],
    "OAUTH2_RESPONSE_TYPES_SUPPORTED": ["code"],
    # Native MCP clients (e.g. Claude Code) complete the flow on a loopback
    # redirect URI; allow http://localhost for those.
    "ALLOW_LOCALHOST_LOOPBACK": True,
    "DCR_ENABLED": True,  # RFC 7591 dynamic client registration for MCP clients
    # MCP clients register anonymously (Claude Code has no account here);
    # the per-IP throttle on /o/register/ is the abuse control.
    "DCR_REGISTRATION_PERMISSION_CLASSES": (
        "oauth2_provider.dcr.AllowAllDCRPermission",
    ),
    "ACCESS_TOKEN_EXPIRE_SECONDS": 3600,
    "REFRESH_TOKEN_EXPIRE_SECONDS": 1209600,  # 14 days
    "ROTATE_REFRESH_TOKEN": True,
    # Anchor issuer + protected-resource metadata at the public base URL so
    # RFC 8414/9728 documents are stable across requests (prod: veilfile.com).
    "OAUTH2_PROTECTED_RESOURCE_IDENTIFIER": f"{DROP_BASE_URL}/mcp",
    "OAUTH2_PROTECTED_RESOURCE_AUTHORIZATION_SERVERS": [DROP_BASE_URL],
    "OAUTH2_PROTECTED_RESOURCE_NAME": "Veilfile MCP",
}

# Client IP for per-IP throttles (drop_core.email_verification.client_ip).
# Number of proxies in front of Django that append to X-Forwarded-For.
# Render sits behind Cloudflare: Cloudflare appends the visitor's address,
# then Render's proxy appends the Cloudflare edge's, so the visitor is the
# 2nd entry from the right (Render-internal private hops after it are
# skipped). Everything further left is client-supplied and ignored.
# That count applies only when the last public entry is a Cloudflare edge: a
# request that reached Render without passing Cloudflare has the client's own
# entries to the left of the one Render appended, so its last entry is used.
# 0 ignores X-Forwarded-For and uses REMOTE_ADDR.
# VEILFILE_CLIENT_IP_HEADER names a header the CDN sets (and overwrites) with
# the visitor IP; Cloudflare's CF-Connecting-IP. It wins when it parses as an
# IP and the request reached the platform from a Cloudflare edge address (a
# direct-to-origin request could forge it), so the hop count above only
# matters as a fallback. Empty disables it.
VEILFILE_CLIENT_IP_HEADER = os.environ.get("VEILFILE_CLIENT_IP_HEADER", "CF-Connecting-IP")
VEILFILE_TRUSTED_PROXY_HOPS = int(os.environ.get("VEILFILE_TRUSTED_PROXY_HOPS", "2"))

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/app/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

# The login form's identifier field accepts a username or an email
# address (EmailOrUsernameBackend, a ModelBackend subclass — username
# behavior is identical, including the admin and the reset-link sign-in).
# Exactly one backend is configured so login(request, user) without an
# explicit backend keeps working (Django raises with two or more).
AUTHENTICATION_BACKENDS = ["drop_core.backends.EmailOrUsernameBackend"]

# Email — console backend locally; SMTP in production when EMAIL_HOST is set.
# Production values live in Render env vars (never in code):
#   EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
#   EMAIL_HOST=smtp.gmail.com EMAIL_PORT=587 EMAIL_USE_TLS=True
#   EMAIL_HOST_USER=<gmail> EMAIL_HOST_PASSWORD=<app password>
#   DEFAULT_FROM_EMAIL=support@veilfile.com
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True") == "True"
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "support@veilfile.com")
# Bound every SMTP connect/send so a stalled mail server cannot hold a
# request thread until gunicorn's timeout.
EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", "10"))
# A console backend with DEBUG=False is reported by the veilfile.W001 system
# check (manage.py check --deploy) and logged at ERROR when the web app
# starts (config.checks), rather than refusing to boot.

# Logging — stdout so Render captures it; app errors at INFO+.
# django.request logs the request path on errors ("Internal Server Error:
# /a/<token>") and django.security.csrf on rejected POSTs ("Forbidden (...):
# /accounts/verify-email/<token>/"); redact_paths masks capability tokens
# (config.log_redaction).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact_paths": {"()": "config.log_redaction.RedactPathsFilter"}},
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "drop_core": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "drop_billing": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "drop_mcp": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
            "filters": ["redact_paths"],
        },
        "django.security.csrf": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
            "filters": ["redact_paths"],
        },
    },
}
