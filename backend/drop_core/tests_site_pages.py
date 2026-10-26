"""The Docs quickstart's shell-setup flow (approved redesign, 2026-10-06)."""

from pathlib import Path

from django.test import TestCase

BEHAVIOURS = Path(__file__).resolve().parent.parent / "static" / "js" / "behaviors"


class DocsSetupFlowTests(TestCase):
    """The full setup flow lives in the docs quickstart (approved redesign)."""

    def _html(self):
        return self.client.get("/docs/quickstart/").content.decode()

    def test_quickstart_shell_switcher_markup(self):
        html = self._html()
        self.assertIn('role="tablist"', html)
        self.assertIn('role="tabpanel"', html)
        for shell in ("bash", "zsh", "fish", "powershell", "cmd"):
            with self.subTest(shell=shell):
                self.assertIn(f'id="shelltab-{shell}"', html)
                self.assertIn(f'id="shellpanel-{shell}"', html)

    def test_quickstart_shell_commands_exactly_as_built(self):
        html = self._html()
        # the immediate-effect one-liners from the shipped shell switcher
        self.assertIn("source ~/.bashrc", html)
        self.assertIn("source ~/.zshrc", html)
        self.assertIn("set -Ux VEILFILE_API_KEY", html)
        self.assertIn("SetEnvironmentVariable", html)
        self.assertIn("setx VEILFILE_API_KEY", html)
        self.assertIn("C:\\&gt;", html)  # cmd prompt, escaped

    def test_quickstart_switcher_keyboard_behavior(self):
        # The keys are tabs.js's, which the switcher's data-tabs hands it to; the page carries no script.
        html = self._html()
        self.assertIn("data-tabs", html)
        self.assertIn("js/behaviors/tabs.js", html)
        self.assertIn('aria-selected="true"', html)
        tabs = (BEHAVIOURS / "tabs.js").read_text()
        for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
            with self.subTest(key=key):
                self.assertIn(key, tabs)

    def test_quickstart_copy_buttons_named(self):
        html = self._html()
        self.assertIn('aria-label="Copy bash setup command"', html)
        self.assertIn('aria-label="Copy upload command"', html)
        # Copy is copy.js's: it reads data-copy, writes to the clipboard and tells screen readers through the status line.
        self.assertIn('data-copy="shellcmd-bash"', html)
        self.assertIn('role="status" data-copy-status', html)
        self.assertIn("js/behaviors/copy.js", html)
        self.assertIn("navigator.clipboard.writeText", (BEHAVIOURS / "copy.js").read_text())

    def test_quickstart_upload_line(self):
        html = self._html()
        self.assertIn(
            "curl -X POST https://veilfile.com/api/v1/artifacts", html
        )

    def test_quickstart_key_name_note(self):
        html = self._html()
        self.assertIn("Your agent gets the variable name", html)
        self.assertIn("never gets the value", html)

    def test_quickstart_limits(self):
        html = self._html()
        self.assertIn("25 MiB", html)
        self.assertIn("Each API key or connected app can make 100 uploads per hour", html)
        for fmt in ("png", "jpg", "jpeg", "webp", "gif", "txt", "md", "log", "json", "har", "pdf"):
            with self.subTest(fmt=fmt):
                self.assertIn(fmt, html)
