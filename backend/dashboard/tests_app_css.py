"""The dashboard's CSS (spec 4.8, 4.10): the column, its type, the rows that stack when the column is narrow, and the markup that pattern needs."""
import re
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

STATIC = Path(settings.BASE_DIR) / "static"
TEMPLATES = Path(settings.BASE_DIR) / "templates"


class RowsMarkup(HTMLParser):
    """Reads a template's text for the rows pattern (base.css): every table is table.rows with a name, every header cell has scope="col", every td has a data-label."""

    def __init__(self):
        super().__init__()
        self.problems, self.tables, self.inside, self.named = [], 0, False, False

    def handle_starttag(self, tag, attrs):
        found = dict(attrs)
        if tag == "table":
            if "rows" not in (found.get("class") or "").split():
                self.problems.append("a <table> that is not class=\"rows\"")
                return
            self.tables, self.inside, self.named = self.tables + 1, True, bool(found.get("aria-label"))
        elif self.inside and tag == "caption":
            self.named = True
        elif self.inside and tag == "th" and found.get("scope") != "col":
            self.problems.append("a <th> without scope=\"col\"")
        elif self.inside and tag == "td" and not found.get("data-label"):
            self.problems.append("a <td> without a data-label")

    def handle_endtag(self, tag):
        if tag == "table" and self.inside:
            self.inside = False
            if not self.named:
                self.problems.append("a table.rows with no <caption> and no aria-label")


def rows_problems(text):
    parser = RowsMarkup()
    parser.feed(text)
    return parser.problems


class AppCssTests(SimpleTestCase):
    longMessage = False

    def setUp(self):
        self.base = re.sub(r"/\*.*?\*/", "", (STATIC / "css" / "base.css").read_text(encoding="utf-8"), flags=re.S)
        self.tokens = (STATIC / "css" / "tokens.css").read_text(encoding="utf-8")

    def rule(self, selector):
        """The declarations of the first rule whose selector is exactly `selector` (as written, spaces folded)."""
        for found, body in re.findall(r"([^{}]+)\{([^{}]*)\}", self.base):
            if " ".join(found.split()) == selector:
                return " ".join(body.split())
        self.fail(f"backend/static/css/base.css: no rule for {selector}")

    def test_the_column_starts_at_the_top_scrolls_with_the_page_and_is_a_container(self):
        self.assertIn("display: grid;", self.rule(".layout-app .shell-main"))
        self.assertIn("align-content: start;", self.rule(".layout-app .shell-main"))
        column = self.rule(".app")
        for declaration in ("container: app / inline-size;", "width: 100%;", "min-width: 0;", "max-width: var(--app-measure);", "overflow-wrap: anywhere;"):
            self.assertIn(declaration, column, f"backend/static/css/base.css: .app needs {declaration}")
        self.assertIn("--app-measure: 60rem;", self.tokens, "backend/static/css/tokens.css: --app-measure is the dashboard's column")

    def test_the_title_is_a_chapters_title_and_the_smaller_headings_are_mono_labels(self):
        self.assertIn("font-style: italic; font-weight: 300;", self.rule(".app :where(h1)"))
        label = self.rule(".app :where(h2, h3, dt, legend, caption)")
        for declaration in ("font-family: var(--font-mono);", "text-transform: uppercase;", "color: var(--muted);"):
            self.assertIn(declaration, label)

    def test_no_dashboard_rule_sets_text_in_the_heading_face_the_layout_does_not_name(self):
        # data-faces (APP_FACES) names italic 300 and Newsreader: a change in place loads those before it swaps. Italic 400 (--font-heading) is not named,
        # so nothing in the dashboard may be set in it; if a section needs it, name it in templates/shell.html and tests_faces.LAYOUT_FACES together.
        read = 0
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", self.base):
            if re.search(r"\.(app|rows|layout-app)\b", selector):
                read += 1
                self.assertNotIn("--font-heading", body, f"backend/static/css/base.css: {' '.join(selector.split())} sets text in --font-heading, which the app layout's data-faces does not name")
        self.assertGreater(read, 20, "backend/dashboard/tests_app_css.py: this check read no dashboard rules; .app, .rows and .layout-app are in backend/static/css/base.css")

    def test_link_hover_waits_for_a_pointer_that_can_hover(self):
        self.assertRegex(self.base, r"@media not all and \(pointer: coarse\) \{\s*\.app :is\(p, li, dd, \.notice\) a:not\(\.interactive, \.label\):hover \{",
                         "backend/static/css/base.css: gate the dashboard link hover on `not all and (pointer: coarse)`, never (hover: hover): CI's Chromium reports hover: none")

    def test_rows_are_a_table_that_stacks_when_the_column_is_narrow_not_the_window(self):
        stacked = re.search(r"@container app \(max-width: 39\.99rem\) \{(.*?)\n\}", self.base, flags=re.S)
        self.assertTrue(stacked, "backend/static/css/base.css: rows stack in @container app (max-width: 39.99rem), a query on the column: between the corners it is 394px at 1024 and 624px at 1280")
        block = " ".join(stacked.group(1).split())
        for needle in (".rows thead { position: absolute;", "clip-path: inset(50%);", ".rows, .rows tbody, .rows tr, .rows td { display: block; }",
                       ".rows caption { display: block; }", ".rows td::before { content: attr(data-label); display: block;", ".rows td:empty { display: none; }"):
            self.assertIn(needle, block, f"backend/static/css/base.css: the stacked rows need {needle}")
        windows = re.findall(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", self.base)
        self.assertEqual([window[:80] for window in windows if ".rows" in window], [], "backend/static/css/base.css: .rows follows its column (@container), never the window (@media)")

    def test_the_table_wraps_a_name_anywhere_and_every_other_cell_at_its_spaces(self):
        self.assertIn("overflow-wrap: normal;", self.rule(".rows :where(th, td)"))
        self.assertIn("overflow-wrap: anywhere;", self.rule(".rows :where(td:first-child, td[data-wrap])"))


class RowsMarkupTests(SimpleTestCase):
    longMessage = False

    def test_every_table_in_a_dashboard_template_follows_the_rows_pattern(self):
        for path in sorted((TEMPLATES / "dashboard").rglob("*.html")):
            with self.subTest(template=path.name):
                self.assertEqual(rows_problems(path.read_text(encoding="utf-8")), [],
                                 f"backend/templates/dashboard/{path.name}: a table is <table class=\"rows\"> with a <caption> (or an aria-label), <th scope=\"col\"> headers and a "
                                 "data-label on every <td>, so that it stacks into rows that keep each value's label on a phone (backend/static/css/base.css, spec 4.10)")

    def test_the_check_finds_what_it_names(self):
        good = ('<table class="rows"><caption>Files</caption><thead><tr><th scope="col">Name</th></tr></thead>'
                '<tbody><tr><td data-label="Name">a</td></tr></tbody></table>')
        self.assertEqual(rows_problems(good), [], "backend/dashboard/tests_app_css.py: rows_problems() must accept the pattern")
        self.assertEqual(rows_problems(good.replace(' class="rows"', ' class="plain"')), ['a <table> that is not class="rows"'])
        self.assertEqual(rows_problems(good.replace("<caption>Files</caption>", "")), ["a table.rows with no <caption> and no aria-label"])
        self.assertEqual(rows_problems(good.replace("<caption>Files</caption>", "").replace('class="rows"', 'class="rows" aria-label="Files"')), [])
        self.assertEqual(rows_problems(good.replace('<th scope="col">', "<th>")), ['a <th> without scope="col"'])
        self.assertEqual(rows_problems(good.replace(' data-label="Name"', "")), ["a <td> without a data-label"])
