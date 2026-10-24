"""site-tests/make_staff_session.py plants a staff login, so it must refuse any database but a local or CI one."""
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings

SCRIPT = Path(settings.BASE_DIR).parent / "site-tests" / "make_staff_session.py"


class MakeStaffSessionTests(TestCase):
    def run_script(self):
        with tempfile.TemporaryDirectory() as directory:
            session_file = Path(directory) / "session.txt"
            with mock.patch.dict(os.environ, {"SITE_SESSION_FILE": str(session_file)}):
                exec(SCRIPT.read_text(encoding="utf-8"), {"__name__": "make_staff_session"})
            return session_file.read_text(encoding="utf-8")

    def assert_refused(self):
        with self.assertRaises(SystemExit) as refusal:
            self.run_script()
        self.assertIn("unset DATABASE_URL", str(refusal.exception))
        self.assertFalse(get_user_model().objects.filter(username="site-checks").exists())

    def test_refuses_when_debug_is_off(self):
        # Production runs with DEBUG off; tests do too, so no override is needed here.
        self.assert_refused()

    @override_settings(DEBUG=True)
    def test_refuses_a_database_on_another_host(self):
        with mock.patch.object(connection, "vendor", "postgresql"), \
                mock.patch.dict(connection.settings_dict, {"HOST": "db.example.com"}):
            self.assert_refused()

    @override_settings(DEBUG=True)
    def test_creates_a_staff_session_on_a_local_database(self):
        session_key = self.run_script()
        user = get_user_model().objects.get(username="site-checks")
        self.assertTrue(user.is_staff)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(len(session_key), 32)

    @override_settings(DEBUG=True)
    def test_allows_the_ci_postgres_on_localhost(self):
        with mock.patch.object(connection, "vendor", "postgresql"), \
                mock.patch.dict(connection.settings_dict, {"HOST": "localhost"}):
            self.run_script()
        self.assertTrue(get_user_model().objects.filter(username="site-checks", is_staff=True).exists())
