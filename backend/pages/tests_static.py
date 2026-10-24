"""Static files for the new shell (spec 6.3): served from backend/static, hashed and compressed in production."""
import gzip
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.templatetags.static import static
from django.test import SimpleTestCase


class StaticSettingsTests(SimpleTestCase):
    def test_pages_app_is_installed(self):
        self.assertTrue(apps.is_installed("pages"))

    def test_backend_static_is_the_first_static_dir(self):
        static_dir = Path(settings.BASE_DIR) / "static"
        self.assertEqual(Path(settings.STATICFILES_DIRS[0]), static_dir)
        self.assertTrue(static_dir.is_dir())

    def test_production_storage_hashes_and_compresses(self):
        # The test runner swaps in plain storage (config/test_runner.py), so read the settings module itself.
        from config import settings as project_settings

        self.assertEqual(
            project_settings.STORAGES["staticfiles"]["BACKEND"],
            "whitenoise.storage.CompressedManifestStaticFilesStorage",
        )

    def test_static_urls_stay_plain_in_tests(self):
        # Tests never run collectstatic, so the runner serves plain storage: no manifest needed.
        self.assertEqual(static("css/tokens.css"), "/static/css/tokens.css")


class ScriptBudgetTests(SimpleTestCase):
    def test_shell_and_behaviours_stay_under_10_kb_gzipped(self):
        js = Path(settings.BASE_DIR) / "static" / "js"
        files = [js / "shell.js", *sorted((js / "behaviors").glob("*.js"))]
        total = sum(len(gzip.compress(path.read_bytes(), 9)) for path in files)
        self.assertLessEqual(total, 10 * 1024,
                             f"shell.js and js/behaviors/*.js are {total} bytes gzipped; the budget is 10 KB (spec 8)")
