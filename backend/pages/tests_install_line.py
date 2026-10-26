"""The install line (spec 4.2, 9): a proper tablist over one box with Copy, and each tab's line after Copy."""
import re

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from pages.install import INSTALL_TABS


class InstallLineTests(SimpleTestCase):
    def setUp(self):
        self.html = render_to_string("components/install_line.html", {"tabs": INSTALL_TABS, "id_prefix": "t"})

    def test_mcp_comes_first_then_npm_and_pip(self):
        self.assertEqual(re.findall(r'role="tab"[^>]*>([^<]+)</button>', self.html), ["MCP", "npm", "pip"])

    def test_the_tabs_are_a_proper_tablist(self):
        self.assertIn('role="tablist" aria-label="Connect with"', self.html)
        for tab in INSTALL_TABS:
            key = tab["key"]
            with self.subTest(tab=key):
                self.assertIn(f'id="t-tab-{key}" aria-controls="t-panel-{key}"', self.html)
                self.assertIn(f'role="tabpanel" id="t-panel-{key}" aria-labelledby="t-tab-{key}"', self.html)
        self.assertIn('aria-selected="true" tabindex="0">MCP</button>', self.html)
        self.assertEqual(self.html.count('aria-selected="false" tabindex="-1"'), 2)
        self.assertEqual(self.html.count(" hidden>"), 2)  # npm and pip start hidden

    def test_copy_takes_the_command_without_its_prompt(self):
        self.assertIn('data-copy="t-command-npm"', self.html)
        self.assertIn('<span class="install-line__prompt" aria-hidden="true">$</span><span data-copy-text>npm install -g veilfile</span>',
                      self.html)
        self.assertIn("<span data-copy-text>https://veilfile.com/mcp</span>", self.html)

    def test_every_tab_has_a_line_after_copy_and_copy_is_announced(self):
        self.assertIn("Add this address to your agent as a remote MCP server, and sign in if the agent asks.", self.html)
        self.assertEqual(self.html.count("Then run <code>veilfile login</code>"), 2)
        self.assertIn('role="status" data-copy-status', self.html)
