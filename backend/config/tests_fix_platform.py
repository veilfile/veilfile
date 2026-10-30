"""Platform regression tests: production settings, the shared cache,
health check, system checks, log redaction, test-run isolation and the
deploy files (Dockerfile, entrypoint, render.yaml, requirements)."""

import importlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock, skipUnless

from django.conf import settings
from django.core.cache import cache
from django.core.checks import run_checks
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import OperationalError, connection, transaction
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

BACKEND_DIR = Path(settings.BASE_DIR)
REPO_DIR = BACKEND_DIR.parent
SETTINGS_PATH = BACKEND_DIR / "config" / "settings.py"

PROD_ENV = {
    "DJANGO_DEBUG": "False",
    "DJANGO_SECRET_KEY": "prod-like-secret-" + "x" * 40,
    "DJANGO_ALLOWED_HOSTS": "veilfile.com",
    "DATABASE_URL": "postgresql://veil:pw@db.example:5432/veil",
}
DEV_ENV = {"DJANGO_DEBUG": "True"}


def load_settings(env):
    """Execute config/settings.py as a fresh module under exactly ``env``.

    settings.py only reads the environment and defines constants, so this
    shows what a production (or dev) boot would configure without touching
    the settings this test run is using.
    """
    spec = importlib.util.spec_from_file_location("_veilfile_settings_probe", SETTINGS_PATH)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, env, clear=True):
        spec.loader.exec_module(module)
    return module


class ProductionSecuritySettingsTests(SimpleTestCase):
    """auth-10 / web-3 / deploy-7: Secure cookies and HSTS in production."""

    def test_cookies_are_secure_in_production(self):
        prod = load_settings(PROD_ENV)
        self.assertIs(getattr(prod, "SESSION_COOKIE_SECURE", False), True)
        self.assertIs(getattr(prod, "CSRF_COOKIE_SECURE", False), True)

    def test_hsts_defaults_to_one_year_without_subdomains_or_preload(self):
        prod = load_settings(PROD_ENV)
        self.assertEqual(getattr(prod, "SECURE_HSTS_SECONDS", 0), 31536000)
        self.assertIs(getattr(prod, "SECURE_HSTS_INCLUDE_SUBDOMAINS", False), False)
        self.assertIs(getattr(prod, "SECURE_HSTS_PRELOAD", False), False)

    def test_hsts_is_env_configurable(self):
        prod = load_settings(
            {
                **PROD_ENV,
                "DJANGO_SECURE_HSTS_SECONDS": "3600",
                "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS": "True",
            }
        )
        self.assertEqual(prod.SECURE_HSTS_SECONDS, 3600)
        self.assertIs(prod.SECURE_HSTS_INCLUDE_SUBDOMAINS, True)

    def test_no_app_level_ssl_redirect(self):
        # Render redirects at the edge and probes health over plain HTTP.
        prod = load_settings(PROD_ENV)
        self.assertIs(getattr(prod, "SECURE_SSL_REDIRECT", False), False)

    def test_debug_keeps_plain_http_cookies_for_local_dev(self):
        dev = load_settings(DEV_ENV)
        self.assertIs(getattr(dev, "SESSION_COOKIE_SECURE", False), False)
        self.assertIs(getattr(dev, "CSRF_COOKIE_SECURE", False), False)
        self.assertEqual(getattr(dev, "SECURE_HSTS_SECONDS", 0), 0)

    def test_login_over_https_sets_secure_cookies_and_hsts(self):
        prod = load_settings(PROD_ENV)
        with override_settings(
            SESSION_COOKIE_SECURE=prod.SESSION_COOKIE_SECURE,
            CSRF_COOKIE_SECURE=prod.CSRF_COOKIE_SECURE,
            SECURE_HSTS_SECONDS=prod.SECURE_HSTS_SECONDS,
        ):
            response = Client().get(
                "/accounts/login/", HTTP_X_FORWARDED_PROTO="https"
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.cookies["csrftoken"]["secure"])
        self.assertEqual(response["Strict-Transport-Security"], "max-age=31536000")


class ProductionDatabaseSettingsTests(SimpleTestCase):
    """deploy-5 / ops-2: no silent SQLite in production; URL parsing."""

    def test_missing_database_url_is_refused_in_production(self):
        env = {k: v for k, v in PROD_ENV.items() if k != "DATABASE_URL"}
        with self.assertRaisesMessage(ImproperlyConfigured, "DATABASE_URL"):
            load_settings(env)

    def test_allow_sqlite_escape_hatch(self):
        env = {k: v for k, v in PROD_ENV.items() if k != "DATABASE_URL"}
        prod = load_settings({**env, "DJANGO_ALLOW_SQLITE": "True"})
        self.assertEqual(
            prod.DATABASES["default"]["ENGINE"], "django.db.backends.sqlite3"
        )

    def test_sqlite_database_url_is_refused_in_production(self):
        env = {**PROD_ENV, "DATABASE_URL": "sqlite:////tmp/prod.sqlite3"}
        with self.assertRaisesMessage(ImproperlyConfigured, "SQLite"):
            load_settings(env)
        allowed = load_settings({**env, "DJANGO_ALLOW_SQLITE": "True"})
        self.assertEqual(
            allowed.DATABASES["default"]["ENGINE"], "django.db.backends.sqlite3"
        )
        dev = load_settings({**DEV_ENV, "DATABASE_URL": "sqlite:////tmp/dev.sqlite3"})
        self.assertEqual(dev.DATABASES["default"]["ENGINE"], "django.db.backends.sqlite3")

    def test_debug_still_falls_back_to_sqlite(self):
        dev = load_settings(DEV_ENV)
        self.assertEqual(dev.DATABASES["default"]["ENGINE"], "django.db.backends.sqlite3")

    def test_database_url_decodes_credentials_and_passes_query_options(self):
        prod = load_settings(
            {
                **PROD_ENV,
                "DATABASE_URL": (
                    "postgresql://veil%2Buser:p%40ss%2Fw0rd@db.example:5433/veil"
                    "?sslmode=require&connect_timeout=5"
                ),
            }
        )
        db = prod.DATABASES["default"]
        self.assertEqual(db["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(db["USER"], "veil+user")
        self.assertEqual(db["PASSWORD"], "p@ss/w0rd")
        self.assertEqual(db["HOST"], "db.example")
        self.assertEqual(db["PORT"], "5433")
        self.assertEqual(db["NAME"], "veil")
        self.assertEqual(db["OPTIONS"], {"sslmode": "require", "connect_timeout": "5"})

    def test_connections_are_reused_with_health_checks(self):
        prod = load_settings(PROD_ENV)
        self.assertEqual(prod.DATABASES["default"]["CONN_MAX_AGE"], 60)
        self.assertIs(prod.DATABASES["default"]["CONN_HEALTH_CHECKS"], True)
        tuned = load_settings({**PROD_ENV, "DJANGO_CONN_MAX_AGE": "0"})
        self.assertEqual(tuned.DATABASES["default"]["CONN_MAX_AGE"], 0)


class SharedCacheSettingsTests(SimpleTestCase):
    """deploy-6 / ctr-6: rate limits must be shared across workers."""

    def test_production_uses_database_cache(self):
        from django.core.cache.backends.db import DatabaseCache
        from django.utils.module_loading import import_string

        prod = load_settings(PROD_ENV)
        default = prod.CACHES["default"]
        # A DatabaseCache whose incr() keeps the counter window (below).
        self.assertEqual(default["BACKEND"], "config.cache.VeilfileDatabaseCache")
        self.assertTrue(issubclass(import_string(default["BACKEND"]), DatabaseCache))
        self.assertEqual(default["LOCATION"], "veilfile_cache")

    def test_debug_keeps_in_process_cache(self):
        dev = load_settings(DEV_ENV)
        self.assertEqual(
            dev.CACHES["default"]["BACKEND"],
            "django.core.cache.backends.locmem.LocMemCache",
        )


_DB_CACHE = {
    "default": {
        "BACKEND": "config.cache.VeilfileDatabaseCache",
        "LOCATION": "veilfile_test_cache",
    }
}


@contextmanager
def clock_ahead(seconds):
    """Make the database cache's expiry checks see a later 'now'."""
    later = timezone.now() + timedelta(seconds=seconds)
    with mock.patch("django.core.cache.backends.db.tz_now", return_value=later), \
            mock.patch("config.cache.tz_now", return_value=later):
        yield


def _cache_rows():
    """{cache_key: seconds until expiry} straight from the cache table."""
    from django.db import models

    expression = models.Expression(output_field=models.DateTimeField())
    converters = connection.ops.get_db_converters(
        expression
    ) + expression.get_db_converters(connection)
    table = connection.ops.quote_name(_DB_CACHE["default"]["LOCATION"])
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT cache_key, expires FROM {table}")
        rows = cursor.fetchall()
    result = {}
    for key, expires in rows:
        for converter in converters:
            expires = converter(expires, expression, connection)
        result[key] = (expires - timezone.now()).total_seconds()
    return result


@override_settings(CACHES=_DB_CACHE)
class DatabaseCacheWindowTests(TestCase):
    """deploy-6 / ctr-6: on the production cache a rate-limit counter keeps
    the window it was created with. Django's DatabaseCache.incr() is
    get() + set() with the default timeout, so every increment moved the
    expiry to 300 s from now and an hourly limit lifted after five minutes."""

    def setUp(self):
        call_command("createcachetable", verbosity=0)

    def _expires_in(self, key):
        return _cache_rows()[cache.make_key(key)]

    def test_incr_keeps_the_window_from_set_and_add(self):
        cache.set("rl:set", 1, timeout=3600)  # device/oauth pattern
        cache.add("rl:add", 0, timeout=600)  # auth throttle / device _bump
        for _ in range(3):
            cache.incr("rl:set")
            cache.incr("rl:add")
        self.assertEqual(cache.get("rl:set"), 4)
        self.assertEqual(cache.get("rl:add"), 3)
        self.assertGreater(self._expires_in("rl:set"), 3590)
        self.assertGreater(self._expires_in("rl:add"), 590)
        self.assertEqual(cache.decr("rl:set"), 3)
        self.assertGreater(self._expires_in("rl:set"), 3590)

    def test_incr_of_a_missing_or_expired_key_raises(self):
        with self.assertRaises(ValueError):
            cache.incr("rl:missing")
        cache.set("rl:old", 5, timeout=60)
        with clock_ahead(61), self.assertRaises(ValueError):
            cache.incr("rl:old")

    def test_device_code_limit_lasts_the_whole_hour(self):
        from drop_core import device

        ip = "203.0.113.7"
        for _ in range(device._CODE_ISSUE_LIMIT_PER_HOUR):
            self.assertEqual(device._code_issue_allowed(ip), (True, 0))
        allowed, retry_after = device._code_issue_allowed(ip)
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 3590)
        self.assertGreater(self._expires_in(f"device-code-rate:{ip}"), 3590)
        # Five minutes on, the limit still holds (it used to lift here) and
        # Retry-After counts down what is left of the hour.
        with clock_ahead(301):
            allowed, retry_after = device._code_issue_allowed(ip)
        self.assertFalse(allowed)
        self.assertTrue(3290 < retry_after <= 3300, retry_after)
        with clock_ahead(3601):
            self.assertEqual(device._code_issue_allowed(ip), (True, 0))

    def test_oauth_ip_limit_keeps_its_window(self):
        from drop_core import oauth

        request = RequestFactory().post("/o/register/", REMOTE_ADDR="198.51.100.4")
        for _ in range(3):
            self.assertEqual(oauth._ip_allowed(request, "dcr-test", 20), (True, 0))
        self.assertEqual(cache.get("dcr-test:198.51.100.4"), 3)
        self.assertGreater(self._expires_in("dcr-test:198.51.100.4"), 3590)

    def test_ttl_reports_the_remaining_window(self):
        cache.set("rl:ttl", 1, timeout=120)
        self.assertTrue(115 < cache.ttl("rl:ttl") <= 120)
        self.assertEqual(cache.ttl("rl:absent"), 0)
        with clock_ahead(121):
            self.assertEqual(cache.ttl("rl:ttl"), 0)

    def test_housekeeping_purges_expired_rows_only(self):
        cache.set("rl:live", 1, timeout=3600)
        cache.set("device-key:1", "vlf_not_a_real_key", timeout=600)
        out = io.StringIO()
        with clock_ahead(601):
            call_command("purge_expired_cache", stdout=out)
        self.assertEqual(out.getvalue().strip(), "purged=1")
        self.assertEqual(list(_cache_rows()), [cache.make_key("rl:live")])


class EmailSettingsTests(SimpleTestCase):
    """deploy-8: a stalled SMTP server must not hold a request forever."""

    def test_email_timeout_defaults_to_ten_seconds(self):
        self.assertEqual(getattr(load_settings(PROD_ENV), "EMAIL_TIMEOUT", None), 10)


_HEALTHY_PROD = dict(
    DEBUG=False,
    EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
    DROP_BASE_URL="https://veilfile.com",
    CACHES={
        "default": {
            "BACKEND": "config.cache.VeilfileDatabaseCache",
            "LOCATION": "veilfile_cache",
        }
    },
)
_HEALTHY_ENV = {"VEILFILE_SAFETY_MODE": "enforce", "DROP_STORAGE": "s3"}


@contextmanager
def production_like(settings_overrides=None, env=None,
                    engine="django.db.backends.postgresql"):
    """Settings and env of a healthy production boot, plus overrides. The
    test database is SQLite, so the default engine is reported as Postgres
    unless a test asks otherwise (the checks only read the setting)."""
    with override_settings(**{**_HEALTHY_PROD, **(settings_overrides or {})}), \
            mock.patch.dict(os.environ, {**_HEALTHY_ENV, **(env or {})}), \
            mock.patch.dict(settings.DATABASES["default"], {"ENGINE": engine}):
        yield


_CONSOLE_EMAIL = {"EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend"}


class ProductionConfigurationCheckTests(SimpleTestCase):
    """deploy-8 / ops-2: dev fallbacks in production are reported, not
    silent (and never crash the boot)."""

    def _problem_ids(self):
        from config.checks import check_production_configuration

        return [m.id for m in check_production_configuration(None)]

    def test_healthy_production_reports_nothing(self):
        with production_like():
            self.assertEqual(self._problem_ids(), [])

    def test_console_email_backend_warns(self):
        with production_like(_CONSOLE_EMAIL):
            self.assertEqual(self._problem_ids(), ["veilfile.W001"])

    def test_safety_mode_unset_or_off_warns(self):
        # "enforced" is a typo that safety_mode() silently treats as off.
        for value in (None, "off", "enforced"):
            with self.subTest(value=value), production_like():
                del os.environ["VEILFILE_SAFETY_MODE"]
                if value is not None:
                    os.environ["VEILFILE_SAFETY_MODE"] = value
                self.assertEqual(self._problem_ids(), ["veilfile.W002"])

    def test_local_storage_http_base_url_and_locmem_cache_warn(self):
        settings_ = {
            "DROP_BASE_URL": "http://localhost:8000",
            "CACHES": {
                "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
            },
        }
        with production_like(settings_, env={"DROP_STORAGE": "local"}):
            self.assertEqual(
                self._problem_ids(), ["veilfile.W003", "veilfile.W004", "veilfile.W005"]
            )

    def test_sqlite_in_production_warns(self):
        # Only reachable through DJANGO_ALLOW_SQLITE=True; still reported.
        with production_like(engine="django.db.backends.sqlite3"):
            self.assertEqual(self._problem_ids(), ["veilfile.W006"])

    def test_debug_reports_nothing(self):
        with production_like({"DEBUG": True, **_CONSOLE_EMAIL},
                             engine="django.db.backends.sqlite3"):
            self.assertEqual(self._problem_ids(), [])

    def test_registered_as_deploy_check(self):
        with production_like(_CONSOLE_EMAIL):
            deploy_ids = {
                m.id for m in run_checks(tags=["veilfile"], include_deployment_checks=True)
            }
            plain_ids = {
                m.id for m in run_checks(tags=["veilfile"], include_deployment_checks=False)
            }
        self.assertIn("veilfile.W001", deploy_ids)
        self.assertNotIn("veilfile.W001", plain_ids)

    def test_web_app_start_logs_problems_at_error(self):
        with production_like(_CONSOLE_EMAIL):
            with self.assertLogs("config.checks", level="ERROR") as logs:
                if "config.wsgi" in sys.modules:
                    importlib.reload(sys.modules["config.wsgi"])
                else:
                    importlib.import_module("config.wsgi")
        self.assertEqual(len(logs.output), 1)
        self.assertIn("veilfile.W001", logs.output[0])


class HealthCheckTests(TestCase):
    """deploy-11: /healthz proves the database answers."""

    def test_healthz_ok_after_select_1(self):
        with CaptureQueriesContext(connection) as queries:
            response = Client().get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(json.loads(response.content), {"status": "ok"})
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual([q["sql"] for q in queries.captured_queries], ["SELECT 1"])

    def test_head_is_supported(self):
        self.assertEqual(Client().head("/healthz").status_code, 200)

    def test_post_is_rejected(self):
        self.assertEqual(Client().post("/healthz").status_code, 405)

    def test_database_failure_returns_503(self):
        with mock.patch(
            "config.health.connection.cursor",
            side_effect=OperationalError("connection refused"),
        ), self.assertLogs("config.health", level="ERROR"), \
                self.assertLogs("django.request", level="ERROR"):
            response = Client().get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.content), {"status": "unavailable"})

    def test_no_session_storage_or_funnel_side_effects(self):
        from drop_core.models import FunnelEvent

        with mock.patch("drop_core.storage.get_storage") as get_storage:
            response = Client().get("/healthz")
        self.assertEqual(response.status_code, 200)
        get_storage.assert_not_called()
        self.assertEqual(response.cookies, {})
        self.assertFalse(FunnelEvent.objects.exists())

    @override_settings(ALLOWED_HOSTS=["veilfile.com"])
    def test_probe_host_header_is_not_validated(self):
        # Render sends whichever verified custom domain it picks (or the
        # onrender.com host) as Host; the probe must not 400 on it.
        response = Client().get("/healthz", HTTP_HOST="deadrop.onrender.com")
        self.assertEqual(response.status_code, 200)
        # Every other path still enforces ALLOWED_HOSTS.
        with self.assertLogs("django.security.DisallowedHost", level="ERROR"):
            other = Client().get("/llms.txt", HTTP_HOST="deadrop.onrender.com")
        self.assertEqual(other.status_code, 400)


class LogRedactionTests(TestCase):
    """Capability URLs (artifact, upload, verification and reset links),
    query strings and Referer paths never reach the logs."""

    def test_redact_path(self):
        from config.log_redaction import redact_path

        redacted = {
            "/a/Zx9_secretArtifactToken-abc123": "/a/<redacted>",
            "/api/v1/uploads/upTok_ABCdef123456789012/": "/api/v1/uploads/<redacted>/",
            "/accounts/verify-email/eyJ1IjoxfQ:1t2abc:SiGnAtUrE/":
                "/accounts/verify-email/<redacted>/",
            "/accounts/reset/MQ/c2a1fz-0123456789abcdef/":
                "/accounts/reset/<redacted>/<redacted>/",
            "//a/doubleSlashToken": "/a/<redacted>",
            "/o/authorize/?client_id=abc&code_challenge=xyz": "/o/authorize/?<redacted>",
            "/device?user_code=ABCD-EFGH": "/device?<redacted>",
            "/somewhere/AbCdEfGhIjKlMnOpQrStUv12": "/somewhere/<redacted>",
        }
        for path, expected in redacted.items():
            with self.subTest(path=path):
                self.assertEqual(redact_path(path), expected)
        for path in (
            "/accounts/verify-email/",
            "/api/v1/artifacts/3f2a8c1e-9b7d-4e6f-8a5b-1c2d3e4f5a6b",
            "/static/assets/index-DiwrgQ0zAbCdEfGhIj.js",
            "/blog/how-to-share-files-with-ai-agents/",
            "/healthz",
            "/",
        ):
            with self.subTest(path=path):
                self.assertEqual(redact_path(path), path)

    def test_gunicorn_access_line_has_no_secrets(self):
        import logging

        from gunicorn.config import Config

        from config.gunicorn_logging import RedactingLogger

        for name in ("gunicorn.error", "gunicorn.access"):
            logger = logging.getLogger(name)
            self.addCleanup(setattr, logger, "handlers", list(logger.handlers))
            self.addCleanup(setattr, logger, "propagate", logger.propagate)
        cfg = Config()
        log = RedactingLogger(cfg)
        referer = "https://veilfile.com/a/REFERER_SECRET_TOKEN?x=SECRET_Q"
        environ = {
            "REQUEST_METHOD": "GET",
            "RAW_URI": "/a/SECRET_ARTIFACT_TOKEN_abc123?dl=SECRET_Q",
            "PATH_INFO": "/a/SECRET_ARTIFACT_TOKEN_abc123",
            "QUERY_STRING": "dl=SECRET_Q",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "REMOTE_ADDR": "10.0.0.1",
            "HTTP_REFERER": referer,
            "HTTP_USER_AGENT": "curl/8.7",
            "HTTP_AUTHORIZATION": "Bearer vlf_SECRET_KEY",
            "HTTP_COOKIE": "sessionid=SECRET_SESSION",
        }
        req = SimpleNamespace(headers=[
            ("REFERER", referer),
            ("AUTHORIZATION", "Bearer vlf_SECRET_KEY"),
            ("COOKIE", "sessionid=SECRET_SESSION"),
        ])
        resp = SimpleNamespace(
            status="404 Not Found", sent=179,
            headers=[("Set-Cookie", "csrftoken=SECRET_CSRF")],
        )
        atoms = log.atoms_wrapper_class(
            log.atoms(resp, req, environ, timedelta(microseconds=1500))
        )
        line = cfg.access_log_format % atoms
        self.assertIn(
            '"GET /a/<redacted>?<redacted> HTTP/1.1" 404 179 '
            '"https://veilfile.com" "curl/8.7"',
            line,
        )
        # A custom --access-logformat cannot pull secrets back in either.
        custom = (
            "%(U)s %(q)s %({authorization}i)s %({cookie}i)s %({referer}i)s "
            "%({set-cookie}o)s %({raw_uri}e)s %({query_string}e)s "
            "%({http_cookie}e)s %({http_authorization}e)s"
        )
        for formatted in (line, custom % atoms):
            self.assertNotIn("SECRET", formatted)

    def test_entrypoint_uses_the_redacting_logger(self):
        script = (BACKEND_DIR / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("--logger-class config.gunicorn_logging.RedactingLogger", script)

    def test_django_request_log_lines_are_redacted(self):
        # 4xx/5xx lines carry the path ("Not Found: /a/<token>").
        with self.assertLogs("django.request", level="WARNING") as logs:
            response = Client().get("/a/SECRET_ARTIFACT_TOKEN_abc123")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(logs.output, ["WARNING:django.request:Not Found: /a/<redacted>"])

    def test_csrf_rejection_log_lines_are_redacted(self):
        # A POST to a token URL without a CSRF cookie is logged at WARNING
        # by django.security.csrf, which production prints.
        client = Client(enforce_csrf_checks=True)
        with self.assertLogs("django.security.csrf", level="WARNING") as logs:
            response = client.post("/accounts/verify-email/SECRET_VERIFY_TOKEN/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn("SECRET", logs.output[0])
        self.assertIn("/accounts/verify-email/<redacted>/", logs.output[0])


class TestRunIsolationTests(TestCase):
    """ops-9 and the backend/media test leak: the whole run uses local
    storage in a temp dir and does not inherit live-service env."""

    def test_run_forces_local_storage_in_a_temp_dir(self):
        from drop_core.storage import LocalStorage, get_storage

        self.assertEqual(os.environ.get("DROP_STORAGE"), "local")
        local_dir = os.environ.get("DROP_LOCAL_DIR")
        self.assertTrue(local_dir)
        temp_root = os.path.realpath(tempfile.gettempdir())
        self.assertTrue(os.path.realpath(local_dir).startswith(temp_root))
        storage = get_storage()
        self.assertIsInstance(storage, LocalStorage)
        self.assertNotIn(str(BACKEND_DIR), str(storage.root))

    def test_live_service_env_is_scrubbed(self):
        from config.test_runner import isolated_environ

        env = isolated_environ(
            {
                "PATH": "/usr/bin",
                "DROP_STORAGE": "s3",
                "DROP_LOCAL_DIR": "/srv/media",
                "AWS_S3_BUCKET": "prod-bucket",
                "AWS_SECRET_ACCESS_KEY": "secret",
                "STRIPE_SECRET_KEY": "sk_live_x",
                "VEILFILE_SAFETY_MODE": "enforce",
                "VEILFILE_BASE_URL": "http://127.0.0.1:9",
            },
            "/tmp/veilfile-tests/artifacts",
        )
        self.assertEqual(
            env,
            {
                "PATH": "/usr/bin",
                "DROP_STORAGE": "local",
                "DROP_LOCAL_DIR": "/tmp/veilfile-tests/artifacts",
            },
        )
        for prefix in ("VEILFILE_", "STRIPE_", "AWS_"):
            self.assertFalse([k for k in os.environ if k.startswith(prefix)], prefix)

    def test_refuses_to_run_with_production_settings(self):
        from config.test_runner import VeilfileTestRunner

        with override_settings(DEBUG=False):
            with self.assertRaisesMessage(ImproperlyConfigured, "DJANGO_DEBUG"):
                VeilfileTestRunner(verbosity=0).setup_test_environment()

    def test_remote_database_detection(self):
        from config.test_runner import remote_test_databases

        pg = "django.db.backends.postgresql"
        databases = {
            "sqlite": {"ENGINE": "django.db.backends.sqlite3", "HOST": "db.example"},
            "default_socket": {"ENGINE": pg, "HOST": ""},
            "socket_dir": {"ENGINE": pg, "HOST": "/tmp/pg"},
            "loopback": {"ENGINE": pg, "HOST": "127.0.0.1"},
            "render": {"ENGINE": pg, "HOST": "dpg-abc123-a"},
            "via_options": {"ENGINE": pg, "HOST": "", "OPTIONS": {"host": "db.example"}},
        }
        self.assertEqual(remote_test_databases(databases), ["render", "via_options"])

    def test_refuses_a_remote_database_unless_opted_in(self):
        # A Render shell has the production DATABASE_URL; the run would
        # create and drop test_<name> on the production server.
        from config.test_runner import VeilfileTestRunner

        runner = VeilfileTestRunner(verbosity=0)
        remote = {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": "dpg-abc123-a.oregon-postgres.render.com",
            "OPTIONS": {},
        }
        # DEBUG as `manage.py test` sees it (the runner itself sets
        # DEBUG=False while tests run).
        with override_settings(DEBUG=True):
            with mock.patch.dict(settings.DATABASES["default"], remote):
                with self.assertRaisesMessage(
                    ImproperlyConfigured, "DJANGO_TEST_ALLOW_REMOTE_DB"
                ):
                    runner.refuse_unsafe_settings()
                with mock.patch.dict(os.environ, {"DJANGO_TEST_ALLOW_REMOTE_DB": "True"}):
                    runner.refuse_unsafe_settings()
            runner.refuse_unsafe_settings()

    def test_core_test_classes_restore_previous_drop_local_dir(self):
        # Their tearDown used to pop DROP_LOCAL_DIR, so every later test
        # class wrote artifacts into backend/media/artifacts.
        from drop_core import tests as core_tests

        sentinel = tempfile.mkdtemp(prefix="veilfile-sentinel-")
        self.addCleanup(os.rmdir, sentinel)
        classes = [
            core_tests.VeilfileCoreTests,
            core_tests.TrailingSlashTests,
            core_tests.SafetyTests,
            core_tests.FunnelTests,
            core_tests.DeviceFlowTests,
        ]
        with mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": sentinel}):
            for cls in classes:
                name = next(n for n in dir(cls) if n.startswith("test_"))
                case = cls(methodName=name)
                with self.subTest(cls=cls.__name__), transaction.atomic():
                    case.setUp()
                    self.assertNotEqual(os.environ.get("DROP_LOCAL_DIR"), sentinel)
                    case.tearDown()
                    case.doCleanups()
                    self.assertEqual(os.environ.get("DROP_LOCAL_DIR"), sentinel)
                    transaction.set_rollback(True)


# The repo-root files (the Dockerfile, the workflows, the ignore files) are in a source checkout and not in the image.
ON_A_CHECKOUT = skipUnless(
    (REPO_DIR / "Dockerfile").exists(),
    "repo-root deploy files are only present in a source checkout, not the image",
)


class NoBuildStepTests(SimpleTestCase):
    """The site has no build step (spec 2 "Stack", 6.1): React, Vite and Node were taken out of the build, so a page is a template under
    backend/templates and a style or a script is a file under backend/static, in a test run as in the image. Each test shuts one door
    that a build output would come back through: a setting, the test runner, the Dockerfile, the ignore files and the backend job of CI.
    Node stays for site-tests/ (axe-core) and npm/ (the CLI), whose own jobs are not looked at here."""

    longMessage = False

    def read(self, relpath):
        return (REPO_DIR / relpath).read_text(encoding="utf-8")

    def test_no_setting_names_a_frontend_build(self):
        names = [name for name in ("FRONTEND_DIR", "FRONTEND_DIST") if hasattr(settings, name)]
        self.assertEqual(names, [], "backend/config/settings.py: delete " + ", ".join(names) + ". There is no frontend directory to point at, and a "
                         "build output used as a template or static directory is a second place a page can come from")

    def test_templates_and_static_files_come_from_backend_alone(self):
        self.assertEqual([Path(d) for d in settings.TEMPLATES[0]["DIRS"]], [BACKEND_DIR / "templates"],
                         "backend/config/settings.py: TEMPLATES[0]['DIRS'] is [BASE_DIR / 'templates'] and backend/config/test_runner.py adds nothing to it. "
                         "A template that only a test run can find hides a page that the image cannot render")
        self.assertEqual([Path(d) for d in settings.STATICFILES_DIRS], [BACKEND_DIR / "static"],
                         "backend/config/settings.py: STATICFILES_DIRS is [BASE_DIR / 'static'] and nothing else, because collectstatic publishes what it names")

    def test_the_test_runner_has_no_stand_in_template(self):
        from config import test_runner

        self.assertFalse((BACKEND_DIR / "config" / "test_templates" / "index.html").exists(),
                         "backend/config/test_templates/index.html: delete it. It stood in for the build's index.html, and no page is rendered from it any more")
        self.assertFalse(hasattr(test_runner, "STUB_TEMPLATE_DIR"),
                         "backend/config/test_runner.py: delete STUB_TEMPLATE_DIR and _enable_stub_templates; the runner only swaps in plain static storage")

    @ON_A_CHECKOUT
    def test_there_is_no_frontend_directory_to_build(self):
        left = [name for name in ("package.json", "package-lock.json", "vite.config.js", "index.html", "src") if (REPO_DIR / "frontend" / name).exists()]
        self.assertEqual(left, [], "frontend/: " + ", ".join(left) + " is still there. `git rm -r frontend`; the dashboard is Django views "
                         "(backend/dashboard) and nothing is built")

    @ON_A_CHECKOUT
    def test_the_dockerfile_is_one_python_stage_with_no_node(self):
        dockerfile = self.read("Dockerfile")
        self.assertEqual([line for line in dockerfile.splitlines() if line.startswith("FROM ")], ["FROM python:3.12-slim"],
                         "Dockerfile: one stage, FROM python:3.12-slim. The site has no build step, so there is no stage to build in and nothing to COPY --from")
        for word in ("node", "npm", "frontend", "vite"):
            self.assertIsNone(re.search(rf"\b{word}\b", dockerfile, re.IGNORECASE), f"Dockerfile: mentions {word}. Nothing in the image is built with it")

    @ON_A_CHECKOUT
    def test_every_copy_in_the_dockerfile_names_a_path_in_the_repo(self):
        # docker build cannot run in a test, or in CI, which does not build the image. A COPY of a path that is not in the repository is what
        # stops it, and this reads every one.
        instructions = self.read("Dockerfile").replace("\\\n", " ").splitlines()
        copies = [line.split()[1:] for line in instructions if line.startswith("COPY ")]
        self.assertTrue(copies, "Dockerfile: has no COPY, so the image would hold no code; this test reads them")
        for arguments in copies:
            if any(argument.startswith("--from") for argument in arguments):
                continue
            for source in [argument for argument in arguments if not argument.startswith("--")][:-1]:
                self.assertTrue((REPO_DIR / source).exists(),
                                f"Dockerfile: COPY {source} names a path that is not in the repository, so docker build stops there. Fix the path or delete the line")

    @ON_A_CHECKOUT
    def test_the_ignore_files_name_no_frontend(self):
        for name in (".dockerignore", ".gitignore"):
            self.assertNotIn("frontend", self.read(name), f"{name}: delete the line that names frontend/; the directory is gone")

    @ON_A_CHECKOUT
    def test_the_backend_job_of_ci_builds_nothing_with_node(self):
        from pages.tests_site_parts import workflow_jobs

        workflow = self.read(".github/workflows/ci.yml")
        self.assertNotIn("frontend", workflow, ".github/workflows/ci.yml: nothing builds a frontend; delete the line that names it")
        backend = workflow_jobs(workflow)["backend"]
        for word in ("setup-node", "npm"):
            self.assertNotIn(word, backend, f".github/workflows/ci.yml: the `backend` job runs Django's tests and builds nothing, so it has no {word}. "
                             "Node is for the site-* jobs (axe-core) and npm-cli only")


@skipUnless(
    (REPO_DIR / "Dockerfile").exists(),
    "repo-root deploy files are only present in a source checkout, not the image",
)
class DeployFilesTests(SimpleTestCase):
    """deploy-4/9/11/12/13/14/15, ops-15: the container and blueprint."""

    def _read(self, relpath):
        return (REPO_DIR / relpath).read_text(encoding="utf-8")

    def test_entrypoint_is_valid_shell(self):
        result = subprocess.run(
            ["sh", "-n", str(BACKEND_DIR / "entrypoint.sh")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_entrypoint_runs_threaded_gunicorn_with_access_log(self):
        script = self._read("backend/entrypoint.sh")
        for fragment in (
            "exec gunicorn config.wsgi:application",
            '--bind "0.0.0.0:${PORT:-8000}"',
            '--workers "${WEB_CONCURRENCY:-1}"',
            "--worker-class gthread",
            '--threads "${GUNICORN_THREADS:-4}"',
            '--timeout "${GUNICORN_TIMEOUT:-120}"',
            "--graceful-timeout",
            "--access-logfile -",
            "--logger-class config.gunicorn_logging.RedactingLogger",
        ):
            self.assertIn(fragment, script)
        # gunicorn 23's gthread worker drops accepted-but-unserved
        # connections when --max-requests recycles it.
        self.assertNotIn("\n    --max-requests", script)

    def test_entrypoint_prepares_cache_and_allows_skipping_migrate(self):
        script = self._read("backend/entrypoint.sh")
        self.assertIn("SKIP_MIGRATE", script)
        self.assertIn("python manage.py createcachetable", script)
        self.assertIn("python manage.py clearsessions", script)
        self.assertIn("python manage.py purge_expired_cache", script)
        self.assertLess(
            script.index("createcachetable"), script.index("exec gunicorn")
        )

    def test_entrypoint_bounds_the_sweep_pass_and_reports_its_failure(self):
        # A hung sweep must not stall the loop, and a failed pass must say so
        # (R-02 Option A) instead of being swallowed by `|| true`.
        script = self._read("backend/entrypoint.sh")
        self.assertIn(
            'timeout 900 python manage.py sweep_expired || echo "sweep pass failed rc=$?"',
            script,
        )
        self.assertNotIn("python manage.py sweep_expired || true", script)
        self.assertIn("sleep 86400", script)

    def test_dockerfile_runs_unprivileged_and_collects_static_without_a_database(self):
        dockerfile = self._read("Dockerfile")
        user_lines = [l for l in dockerfile.splitlines() if l.startswith("USER ")]
        self.assertTrue(user_lines)
        self.assertNotIn(user_lines[-1].split()[1], ("root", "0"))
        # The build has no DATABASE_URL; collectstatic must still import
        # settings now that production refuses a silent SQLite fallback.
        instructions = dockerfile.replace("\\\n", " ").splitlines()
        collect = next(
            l for l in instructions if l.startswith("RUN ") and "collectstatic" in l
        )
        self.assertIn("DJANGO_ALLOW_SQLITE=True", collect)

    def test_dockerignore_excludes_nested_local_state(self):
        patterns = set(self._read(".dockerignore").split())
        for pattern in (
            "**/db.sqlite3",
            "**/media/",
            "**/staticfiles/",
            "**/__pycache__/",
            "**/node_modules/",
            "**/.env",
        ):
            self.assertIn(pattern, patterns)

    def test_render_blueprint_matches_live_service(self):
        blueprint = self._read("render.yaml")
        self.assertIn("name: deadrop", blueprint)
        self.assertIn("plan: starter", blueprint)
        self.assertNotIn("plan: free", blueprint)
        self.assertIn("healthCheckPath: /healthz", blueprint)
        for key in ("DATABASE_URL", "EMAIL_BACKEND", "EMAIL_HOST", "EMAIL_HOST_PASSWORD"):
            self.assertIn(f"- key: {key}", blueprint)

    def test_requirements_pin_transitive_dependencies(self):
        pins = {}
        for line in self._read("backend/requirements.txt").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                name, sep, version = line.partition("==")
                self.assertEqual(sep, "==", line)
                pins[name.split("[")[0].lower()] = version
        for name in (
            "oauthlib",
            "jwcrypto",
            "cryptography",
            "requests",
            "urllib3",
            "botocore",
            "s3transfer",
            "sqlparse",
            "asgiref",
        ):
            self.assertIn(name, pins)
