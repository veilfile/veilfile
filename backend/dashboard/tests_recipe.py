"""The dashboard's recipe and the spec's account of it stay true to the code.

AGENTS.md ("Add or change a dashboard section") and the spec (4.4, 4.8, 6.2) are what the next agent reads before it adds a page to the dashboard. A recipe that
names a file that was renamed, or a table of addresses that lacks the one a task just routed, sends it the wrong way, so each claim they make about names, files
and addresses is read against the code. No document may go on describing the dashboard as React, or as the one page outside the shell: it was both until the
dashboard became Django views (spec 11, step 6), or say that CI's jobs run in parallel (they run one at a time on one runner). The one rule about Billing that a
customer sees, a fixed sentence with a reference and Stripe's message in the log, is held against billing_page.py. Every failure names the file and the fix.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from dashboard import urls as dashboard_urls
from pages import registry

ROOT = Path(settings.BASE_DIR).parent
AGENTS = ROOT / "AGENTS.md"
BILLING_PAGE = ROOT / "backend" / "dashboard" / "billing_page.py"
REFUSAL_LINE = "billing: dashboard refusal ref="
SPEC = ROOT / "docs" / "superpowers" / "specs" / "2026-10-08-site-redesign-design.md"
SPEC_NAME = "docs/superpowers/specs/2026-10-08-site-redesign-design.md"
HEADING = "### Add or change a dashboard section"


def read(path):
    return Path(path).read_text(encoding="utf-8")


def between(text, start, end):
    """The text from `start` up to (not including) the next `end` after it, or "" when either is missing."""
    if start not in text:
        return ""
    begin = text.index(start)
    finish = text.find(end, begin + len(start))
    return text[begin:] if finish == -1 else text[begin:finish]


def recipe():
    return between(read(AGENTS), HEADING, "\n## ")


class RecipeTests(SimpleTestCase):
    longMessage = False

    # What the recipe leans on, and the file that defines it. The recipe must still say the name, and the file must still have it.
    NAMES = (
        ("dashboard_view", "backend/dashboard/views.py"),
        ("DASHBOARD_MENU", "backend/pages/registry.py"),
        ("DASHBOARD_SECTIONS", "backend/pages/registry.py"),
        ("layout_paths", "backend/pages/registry.py"),
        ("LAYOUT_FACES", "backend/pages/tests_faces.py"),
        ("SECTIONS", "backend/dashboard/tests_app.py"),
        ("SECTION_LABELS", "backend/dashboard/tests_menu.py"),
        ("SECTION_NAMES", "backend/dashboard/tests_menu.py"),
        ("APP_PATHS", "backend/dashboard/tests_layout.py"),
        ("RowsMarkupTests", "backend/dashboard/tests_app_css.py"),
        ("FilterConventionTests", "backend/dashboard/tests_filters.py"),
        ("utc_time", "backend/dashboard/templatetags/dashboard.py"),
        ("local-time.js", "backend/templates/shell.html"),
        ("answer_response", "backend/drop_core/errors.py"),
        ("dashboard_key_tokens", "backend/dashboard/forms.py"),
        ("RawKeyIsKeptNowhereTests", "backend/dashboard/tests_keys.py"),
        ("FixBillingBase", "backend/drop_billing/tests_fix_billing.py"),
        ("shell_sign_out", "backend/pages/templatetags/shell.py"),
        ("plain_menu", "backend/pages/errors.py"),
        ("not_found", "backend/pages/errors.py"),
        ("get_or_create_workspace", "backend/drop_core/models.py"),
        ("FormPagesAreOnTheShellTests", "backend/pages/tests_forms_on_the_shell.py"),
        ("READING_TEST_MODULES", "backend/pages/tests_site_rules.py"),
        ("NoBuildStepTests", "backend/config/tests_fix_platform.py"),
        ("StoredChoicesTests", "backend/pages/tests_shell.py"),
    )

    def test_the_recipe_is_under_site_recipes_and_comes_once(self):
        text = read(AGENTS)
        self.assertEqual(text.count(HEADING), 1, f"AGENTS.md: there is one `{HEADING}` recipe, under `## Site recipes`")
        self.assertTrue(text.index("## Site recipes") < text.index(HEADING) < text.index("## Error pages"),
                        f"AGENTS.md: `{HEADING}` belongs among the site recipes, before `## Error pages`")
        self.assertGreater(len(recipe().splitlines()), 40, "AGENTS.md: the dashboard recipe is a numbered list of steps, not a sentence")

    def test_every_file_and_folder_the_recipe_names_exists(self):
        found = sorted(set(re.findall(r"`((?:backend|site-tests)/[A-Za-z0-9_./<>-]*[A-Za-z0-9_>/])`", recipe())))
        self.assertGreaterEqual(len(found), 12, "AGENTS.md: the dashboard recipe names the files a section touches, in full from the repo root (backend/..., site-tests/...)")
        for name in found:
            with self.subTest(path=name):
                matches = list(ROOT.glob(re.sub(r"<[^>]*>", "*", name).rstrip("/")))
                self.assertTrue(matches, f"AGENTS.md, {HEADING}: `{name}` is not in the repo; the recipe names a file that was renamed or never made: fix the path")

    def test_every_name_the_recipe_leans_on_is_still_where_it_says(self):
        text = recipe()
        for name, where in self.NAMES:
            word = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)")   # SECTIONS is not DASHBOARD_SECTIONS
            with self.subTest(name=name):
                self.assertTrue(word.search(text), f"AGENTS.md, {HEADING}: the recipe no longer says `{name}`; if it was renamed in {where}, say the new name there and in RecipeTests.NAMES")
                self.assertTrue(word.search(read(ROOT / where)), f"{where}: no longer has `{name}`, which the dashboard recipe in AGENTS.md leans on; restore it or change the recipe and RecipeTests.NAMES")


class SpecTests(SimpleTestCase):
    longMessage = False

    ADDRESS_ROW = re.compile(r"^\| `(app[a-z-]*)` \| `(/app/[^`]*)` \| ([^|]+?) \| (.+?) \|$", re.M)
    SECTION_ROW = re.compile(r"^\| ([A-Z][A-Za-z ]+) \| `(/app/[^`]*)` \| ", re.M)

    def spec_48(self):
        return between(read(SPEC), "### 4.8 The dashboard", "### 4.9")

    def routes(self):
        return {pattern.name: "/app/" + str(pattern.pattern) for pattern in dashboard_urls.urlpatterns}

    def test_the_table_of_addresses_is_the_dashboards_routes(self):
        rows = self.ADDRESS_ROW.findall(self.spec_48())
        named = {name: address for name, address, _methods, _page in rows}
        routed = self.routes()
        self.assertGreaterEqual(len(rows), 11, f"{SPEC_NAME}, section 4.8: the table of addresses (Name, Address, Methods, Page) lists every route of backend/dashboard/urls.py")
        self.assertEqual(len(rows), len(named), f"{SPEC_NAME}, section 4.8: a name is listed twice in the table of addresses")
        self.assertEqual(sorted(set(routed) - set(named)), [], f"{SPEC_NAME}, section 4.8: add a row to the table of addresses for each of these routes of backend/dashboard/urls.py")
        self.assertEqual(sorted(set(named) - set(routed)), [], f"{SPEC_NAME}, section 4.8: these rows of the table of addresses name no route in backend/dashboard/urls.py: take them out or route them")
        for name, address in routed.items():
            self.assertEqual(named.get(name), address, f"{SPEC_NAME}, section 4.8: the table gives {name} as {named.get(name)}, but backend/dashboard/urls.py routes it at {address}")
        for _name, _address, methods, _page in rows:
            self.assertRegex(methods, r"^(GET|POST)", f"{SPEC_NAME}, section 4.8: the Methods column of the table of addresses starts with GET or POST, not {methods!r}")

    def test_the_table_of_sections_is_the_menu(self):
        sections = {label: address for label, address in self.SECTION_ROW.findall(self.spec_48())}
        routed = self.routes()
        expected = {link.label: routed[link.url_name] for link in registry.DASHBOARD_MENU}
        self.assertEqual(sections, expected, f"{SPEC_NAME}, section 4.8: the table of sections (Section, Address, Contents) is DASHBOARD_MENU in backend/pages/registry.py, in the menu's words")

    def test_the_menu_inside_the_dashboard_says_what_the_registry_does(self):
        text = between(read(SPEC), "- **Inside the dashboard**", "### 4.5")
        self.assertTrue(text, f"{SPEC_NAME}, section 4.4: the bullet that begins `- **Inside the dashboard**` describes the menu inside it")
        for link in (*registry.DASHBOARD_MENU, *registry.DASHBOARD_SITE_LINKS):
            self.assertTrue(link.label in text, f"{SPEC_NAME}, section 4.4: the menu inside the dashboard has {link.label!r} (backend/pages/registry.py), and the bullet does not say so")
        self.assertTrue("Sign out" in text and "/accounts/logout/" in text and "CSRF" in text,
                        f"{SPEC_NAME}, section 4.4: Sign out is a button in a form that POSTs to /accounts/logout/ with the page's CSRF token; say so")

    def test_what_a_customer_reads_when_billing_refuses_is_what_billing_page_does(self):
        code = read(BILLING_PAGE)
        self.assertTrue('logging.getLogger("drop_billing")' in code and REFUSAL_LINE in code,
                        f"backend/dashboard/billing_page.py: a refusal logs `{REFUSAL_LINE}<REF> ...` through logging.getLogger(\"drop_billing\"), which the spec (4.8) and the recipe (step 10) say; "
                        "if the line or the logger changed, change both documents with it")
        spec = self.spec_48()
        self.assertTrue(REFUSAL_LINE in spec and "Reference: " in spec,
                        f"{SPEC_NAME}, section 4.8: the Billing bullet says what a customer reads when Billing refuses (a fixed sentence, and `Reference: 4F9A2C` when the page cannot explain it) "
                        f"and that the `{REFUSAL_LINE}` line of the drop_billing log holds Stripe's message")
        self.assertTrue(REFUSAL_LINE in recipe() and "Reference: " in recipe(),
                        f"AGENTS.md, {HEADING}: step 10 (Stripe) says the customer reads a fixed sentence with a reference, and Stripe's message is in the `{REFUSAL_LINE}` log line")

    def test_the_template_tree_names_only_files_that_exist(self):
        tree = between(read(SPEC), "backend/templates/\n", "```")
        self.assertTrue(tree, f"{SPEC_NAME}, section 6.2: the tree that begins `backend/templates/`")
        folder, shown = "", 0
        for line in tree.splitlines()[1:]:
            if not line.strip():
                continue
            indent, first = len(line) - len(line.lstrip()), line.split()[0]
            if first.endswith("/"):
                folder, names = first, re.findall(r"\b[a-z_]+\.html\b", line)
            elif indent == 2:
                folder, names = "", [first]
            else:
                names = [first]
            for name in names:
                shown += 1
                with self.subTest(file=folder + name):
                    self.assertTrue((ROOT / "backend" / "templates" / folder / name).is_file(),
                                    f"{SPEC_NAME}, section 6.2: the tree names backend/templates/{folder}{name}, which is not there; name what exists")
        self.assertGreater(shown, 20, f"{SPEC_NAME}, section 6.2: the tree of templates names the layouts, the chapters, the dashboard's pages and the components")


class NothingStaleTests(SimpleTestCase):
    """The dashboard is Django views on the shell. Nothing that is read as a rule may still say it is React, or that it is the page outside the shell."""

    longMessage = False

    # Words of what went with React in PR 6 (spec 6.1). The spec itself, the plans and the tests that forbid these words are not scanned: they are history.
    REMOVED = re.compile(r"\bReact\b|\bVite\b|\.jsx\b|\bfrontend/|FRONTEND_DIR|FRONTEND_DIST|frontend-build|npm run build|config/test_templates")
    GONE = ("AGENTS.md", "README.md", "SPEC.md", "docs/API.md", "docs/llms.txt", "docs/REGISTRIES.md", "Dockerfile", ".dockerignore",
            ".gitignore", "render.yaml", ".github/workflows/ci.yml", "backend/config/settings.py", "backend/config/urls.py", "backend/config/test_runner.py")
    STALE = re.compile(r"one page not on the shell|not on the shell yet|until the dashboard moves onto the shell", re.I)
    SHELL = GONE + ("backend/static/css/tokens.css", SPEC_NAME)

    def files(self, names, globs=()):
        paths = [ROOT / name for name in names] + [path for pattern in globs for path in sorted(ROOT.glob(pattern))]
        return [path for path in paths if path.is_file()]

    def test_no_document_or_setting_still_names_react_vite_or_the_build_that_went_with_them(self):
        paths = self.files(self.GONE, ("backend/docs/pages/*.md",))
        self.assertGreater(len(paths), 10, "backend/dashboard/tests_recipe.py: GONE names the documents and settings to read; most of them are missing")
        found = self.hits(paths, self.REMOVED)
        self.assertFalse(found, "These lines name React, Vite or the build that went with them. The dashboard is Django views since PR 6 and frontend/, Vite and the Node build "
                                f"are gone: say what is true now (AGENTS.md, {HEADING}) or delete the line.\n" + "\n".join(found))

    def test_no_text_still_calls_the_dashboard_the_page_outside_the_shell(self):
        found = self.hits(self.files(self.SHELL), self.STALE)
        self.assertFalse(found, "These lines call the dashboard the one page outside the shell. Every page is on the shell now, the dashboard's included: reword each.\n" + "\n".join(found))

    @staticmethod
    def hits(paths, pattern):
        """['path:line: the line (the match)', ...] for every line of the files that the pattern finds."""
        return [f"{path.relative_to(ROOT)}:{number}: {line.strip()[:110]!r} ({pattern.search(line).group(0)!r})"
                for path in paths for number, line in enumerate(read(path).splitlines(), 1) if pattern.search(line)]
