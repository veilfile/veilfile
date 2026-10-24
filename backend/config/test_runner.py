"""Test runner that isolates ``manage.py test`` from the shell environment.

Storage, billing and safety code read os.environ at call time, so a shell
with production variables (a Render shell inside the deployed image, or a
sourced .env) would otherwise send test uploads to the real bucket
(DROP_STORAGE=s3) or change behavior through VEILFILE_*/STRIPE_* values.
For the whole run this runner:

- refuses to start when DEBUG is False (production settings);
- refuses a database server that is not on this machine (a Render shell
  has the production DATABASE_URL, and the run would create and drop
  test_<name> on that server) unless DJANGO_TEST_ALLOW_REMOTE_DB=True;
- forces DROP_STORAGE=local with DROP_LOCAL_DIR in a fresh temp dir, so a
  test that does not pick its own dir never writes into backend/media;
- removes VEILFILE_*, STRIPE_* and AWS_* variables (tests patch in the
  values they need);
- serves plain /static/ URLs in place of the manifest storage, so no test
  needs collectstatic.

Everything is restored when the run ends. --parallel workers inherit the
environment and re-apply the static storage themselves.
"""

import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test.runner import DiscoverRunner, ParallelTestSuite
from django.test.utils import override_settings

# Env prefixes that select live services or change product behavior.
SCRUBBED_ENV_PREFIXES = ("VEILFILE_", "STRIPE_", "AWS_")

# Database hosts that are this machine: empty (libpq's default local socket),
# loopback, or a Unix socket directory.
_LOCAL_DB_HOSTS = {"", "localhost", "127.0.0.1", "::1"}


def remote_test_databases(databases):
    """Return the aliases in ``databases`` (settings.DATABASES) whose server
    is not on this machine. SQLite is always local."""
    remote = []
    for alias, config in databases.items():
        if config.get("ENGINE") == "django.db.backends.sqlite3":
            continue
        # DATABASE_URL query parameters (?host=...) land in OPTIONS and
        # override HOST.
        host = str((config.get("OPTIONS") or {}).get("host") or config.get("HOST") or "")
        if host in _LOCAL_DB_HOSTS or host.startswith("/"):
            continue
        remote.append(alias)
    return remote


def isolated_environ(environ, local_dir):
    """Return a copy of ``environ`` that is safe for a test run."""
    env = {
        key: value
        for key, value in environ.items()
        if not key.startswith(SCRUBBED_ENV_PREFIXES)
    }
    env["DROP_STORAGE"] = "local"
    env["DROP_LOCAL_DIR"] = str(local_dir)
    return env


def _enable_plain_static_files():
    """Tests run with DEBUG=False, where the manifest storage raises for any file
    collectstatic has not hashed. Serve plain /static/ URLs in tests instead. Runs in
    the runner process and in every spawned --parallel worker (which rebuilds
    settings from scratch)."""
    storages = {**settings.STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    override = override_settings(STORAGES=storages)
    override.enable()
    return override


class VeilfileParallelTestSuite(ParallelTestSuite):
    process_setup = _enable_plain_static_files


class VeilfileTestRunner(DiscoverRunner):
    parallel_test_suite = VeilfileParallelTestSuite

    def refuse_unsafe_settings(self):
        """Raise ImproperlyConfigured before anything is created when the
        settings point at production."""
        if not settings.DEBUG:
            raise ImproperlyConfigured(
                "Refusing to run tests with DJANGO_DEBUG=False (production "
                "settings): tests create databases and must never reach "
                "production services. Run them with DJANGO_DEBUG=True."
            )
        remote = remote_test_databases(settings.DATABASES)
        if remote and os.environ.get("DJANGO_TEST_ALLOW_REMOTE_DB") != "True":
            raise ImproperlyConfigured(
                "Refusing to run tests against a database server that is not "
                f"on this machine (aliases: {', '.join(remote)}): the run "
                "creates and drops test_<name> databases there. Unset "
                "DATABASE_URL to use SQLite, or set DJANGO_TEST_ALLOW_REMOTE_DB"
                "=True if the server is a disposable test instance."
            )

    def setup_test_environment(self, **kwargs):
        self.refuse_unsafe_settings()
        self._run_dir = tempfile.mkdtemp(prefix="veilfile-tests-")
        self._environ = mock.patch.dict(
            os.environ,
            isolated_environ(os.environ, Path(self._run_dir) / "artifacts"),
            clear=True,
        )
        self._environ.start()
        self._static = _enable_plain_static_files()
        super().setup_test_environment(**kwargs)

    def teardown_test_environment(self, **kwargs):
        super().teardown_test_environment(**kwargs)
        self._static.disable()
        self._environ.stop()
        shutil.rmtree(self._run_dir, ignore_errors=True)
