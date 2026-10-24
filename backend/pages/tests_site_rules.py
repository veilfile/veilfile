"""Site rules (spec 7.2): internal links resolve, llms.txt names the chapters, templates and CSS keep the conventions."""
import importlib
import re
import tempfile
import types
import unittest
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from blog import loader as blog_loader
from docs import loader as docs_loader
from pages import catalog, registry

BACKEND = Path(settings.BASE_DIR)
REPO = BACKEND.parent
TEMPLATES = BACKEND / "templates"
CSS = BACKEND / "static" / "css"
# Paths that are linked but are not pages a GET can open, and why.
NOT_PAGES = {"/mcp": "the MCP endpoint answers POST from MCP clients"}


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href")
        if tag == "a" and href:
            self.hrefs.append(href)


EXTENDS_THE_SHELL = re.compile(r"{%\s*extends\s+[\"'](?:shell\.html|layouts/[\w-]+\.html)[\"']")


def shell_templates(root=TEMPLATES):
    """The templates under the shell rules (spec 7.2): shell.html, every component, and every template under root that
    extends the shell or a layout, in whatever directory it sits. A page is under the rules the moment it moves onto the shell."""
    paths = [root / "shell.html"] + sorted((root / "components").glob("*.html"))
    paths += [path for path in sorted(root.rglob("*.html"))
              if path not in paths and EXTENDS_THE_SHELL.search(path.read_text(encoding="utf-8"))]
    return [path for path in paths if path.is_file()]


TEMPLATE_REFERENCE = re.compile(r"{%\s*(?:include|extends)\s+[\"']([^\"']+)[\"']")


def missing_template_references(root):
    """(file, line, name) for every {% include %} or {% extends %} under root that names a template not under root."""
    missing = []
    for path in sorted(root.rglob("*.html")):
        for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
            missing += [(path.relative_to(root).as_posix(), number, name)
                        for name in TEMPLATE_REFERENCE.findall(line) if not (root / name).is_file()]
    return missing


class InternalLinksTests(TestCase):
    def test_every_internal_link_on_every_public_page_resolves(self):
        sources = [reverse(page.url_name) for page in registry.PAGES]
        sources += [reverse("docs-page", args=[doc.slug]) for doc in docs_loader.load_pages() if doc.slug != "index"]
        sources += [reverse("blog-post", args=[post.slug]) for post in blog_loader.load_posts()]
        broken = []
        for source in sources:
            response = self.client.get(source)
            self.assertEqual(response.status_code, 200, source)
            parser = _Links()
            parser.feed(response.content.decode())
            for href in parser.hrefs:
                path = href.split("#")[0]
                if not path.startswith("/") or path.startswith("//") or path.startswith("/static/") or path in NOT_PAGES:
                    continue
                status = self.client.get(path, follow=True).status_code
                if status != 200:
                    broken.append(f"{source} links to {href}, which answers {status}")
        self.assertEqual(broken, [], "\n" + "\n".join(broken))


class LlmsTxtTests(SimpleTestCase):
    def test_names_every_chapter_and_the_mcp_address(self):
        text = (REPO / "docs" / "llms.txt").read_text(encoding="utf-8")
        for link in registry.MENU[0]:
            with self.subTest(chapter=link.label):
                self.assertIn(registry.canonical_url(link.url_name), text)
        self.assertIn("https://YOUR-DEPLOY/mcp", text)

    def test_names_the_journal_and_its_feed(self):
        text = (REPO / "docs" / "llms.txt").read_text(encoding="utf-8")
        for name in ("blog-index", "blog-feed"):
            address = registry.SITE_ORIGIN + reverse(name)
            with self.subTest(address=address):
                self.assertTrue(address in text, f"docs/llms.txt: add {address} to 'Pages for people' (the Journal and its full-text feed)")


class TemplateRulesTests(SimpleTestCase):
    def test_no_inline_styles(self):
        for path in shell_templates():
            with self.subTest(template=path.name):
                self.assertNotRegex(path.read_text(encoding="utf-8"), r"\sstyle\s*=",
                                    f"{path.relative_to(REPO)}: move the style into the component's CSS file")

    def test_components_are_included_with_only(self):
        include = re.compile(r"{%\s*include\s+[\"']components/([\w-]+)\.html[\"'](.*?)%}")
        for path in shell_templates():
            for name, rest in include.findall(path.read_text(encoding="utf-8")):
                with self.subTest(template=path.name, component=name):
                    self.assertRegex(rest, r"\bonly\s*$", f"{path.relative_to(REPO)}: include components/{name}.html with ... only")

    def test_components_start_with_their_two_comments(self):
        for path in sorted((TEMPLATES / "components").glob("*.html")):
            lines = path.read_text(encoding="utf-8").split("\n")
            with self.subTest(component=path.stem):
                self.assertTrue(lines[0].startswith("{# Component: ") and lines[0].endswith("#}"), f"{path.name} line 1")
                self.assertTrue(lines[1].startswith("{# Inputs: ") and lines[1].endswith("#}"), f"{path.name} line 2")

    def test_every_component_has_a_stylesheet_and_a_catalog_entry(self):
        names = {path.stem for path in (TEMPLATES / "components").glob("*.html")}
        self.assertEqual(names, {entry.name for entry in catalog.entries()})
        shell = (TEMPLATES / "shell.html").read_text(encoding="utf-8")
        for name in names:
            with self.subTest(component=name):
                self.assertTrue((CSS / "components" / f"{name}.css").is_file(), f"add backend/static/css/components/{name}.css")
                self.assertIn(f"{{% static 'css/components/{name}.css' %}}", shell,
                              f"backend/templates/shell.html: add <link rel=\"stylesheet\" href=\"{{% static 'css/components/{name}.css' %}}\">")

    def test_every_static_path_in_a_template_exists(self):
        # In production a {% static %} path that names no file is a server error on every page that
        # uses it, and tests run on plain storage that never notices, so check the paths here.
        static = re.compile(r"{%\s*static\s+[\"']([^\"']+)[\"']")
        for path in sorted(BACKEND.rglob("templates/**/*.html")):
            for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
                for name in static.findall(line):
                    with self.subTest(template=str(path.relative_to(REPO)), static=name):
                        self.assertIsNotNone(finders.find(name), f"{path.relative_to(REPO)}:{number}: {{% static '{name}' %}} "
                                             "names no file under backend/static; fix the path or add the file")

    def test_colors_live_only_in_tokens_css(self):
        color = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch|lab|lch)\(")
        for path in sorted(CSS.rglob("*.css")):
            if path.name == "tokens.css":
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
                with self.subTest(file=path.name, line=number):
                    self.assertIsNone(color.search(line), f"{path.relative_to(REPO)}:{number}: use a token from tokens.css")

    def test_every_include_and_extends_names_a_template_that_exists(self):
        # A template that names a missing one is a server error on every page that renders it, and the tests only
        # notice for the pages they happen to render: deleting an old include must leave nothing pointing at it.
        missing = missing_template_references(TEMPLATES)
        self.assertEqual(missing, [], "\n" + "\n".join(
            f"backend/templates/{where}:{number}: names {name}, which is not a file under backend/templates; "
            "fix the name, or restore the file if it is still needed" for where, number, name in missing))

    def test_a_missing_include_is_reported_with_its_file_and_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "includes").mkdir()
            (root / "includes" / "there.html").write_text("there", encoding="utf-8")
            (root / "page.html").write_text(
                '{% extends "gone_base.html" %}\n{% include "includes/there.html" %}\n'
                '{% include "includes/gone.html" with a=1 only %}\n', encoding="utf-8")
            self.assertEqual(missing_template_references(root),
                             [("page.html", 1, "gone_base.html"), ("page.html", 3, "includes/gone.html")])

    def test_a_template_is_under_the_shell_rules_by_what_it_extends(self):
        # Spec 7.2: older templates are checked as they move onto the shell. A template that extends the shell or a
        # layout is under the rules wherever it sits, so moving a page onto the shell needs no list to extend.
        templates = {
            "shell.html": "<html>{% block main %}{% endblock %}</html>",
            "components/box.html": "{# Component: box #}\n{# Inputs: none #}\n<div></div>",
            "layouts/wide.html": '{% extends "shell.html" %}',
            "notes/entry.html": "{% extends 'layouts/wide.html' %}",
            "forms/login.html": "<!DOCTYPE html><html><body>a page with its own document</body></html>",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, text in templates.items():
                (root / name).parent.mkdir(exist_ok=True)
                (root / name).write_text(text, encoding="utf-8")
            covered = {path.relative_to(root).as_posix() for path in shell_templates(root)}
        self.assertEqual(covered, {"shell.html", "components/box.html", "layouts/wide.html", "notes/entry.html"},
                         "backend/pages/tests_site_rules.py: shell_templates() must cover shell.html, components/ and every "
                         "template that extends the shell or a layout, in any directory, and no other template")

    def test_no_header_or_footer_element_in_a_page_template(self):
        # base.css styles the bare header and footer elements for the shell's own top and bottom
        # (`:root:not(.js) footer { display: none }`), so one inside <main> would vanish without JavaScript.
        element = re.compile(r"<(?:header|footer)\b")
        for path in shell_templates():
            if path.name == "shell.html":
                continue
            with self.subTest(template=str(path.relative_to(REPO))):
                self.assertIsNone(element.search(path.read_text(encoding="utf-8")),
                                  f"{path.relative_to(REPO)}: use <section>, <div> or <nav> here; a page's <main> may not "
                                  "hold a <header> or <footer> (base.css styles those bare elements for the shell)")


# The two templates that are whole documents: the shell, and the 500 page, which stands alone so that it still renders when the shell cannot (spec 4.9).
WHOLE_DOCUMENTS = ("shell.html", "500.html")
DOCUMENT_TAG = re.compile(r"<!doctype|<html\b", re.IGNORECASE)
STYLE_ELEMENT = re.compile(r"<style\b", re.IGNORECASE)
UNCLOSED_COMMENT = re.compile(r"{#(?:(?!#}).)*$")
OUTLINE_REMOVED = re.compile(r"\boutline(?:-style|-width)?\s*:\s*(?:none|hidden|0(?![\d.]))")


def without_comments(text):
    """A template's text without its {# #} comments and {% comment %} blocks, so a word in a comment is not taken for markup."""
    text = re.sub(r"{%\s*comment\b.*?{%\s*endcomment\s*%}", "", text, flags=re.DOTALL)
    return re.sub(r"{#.*?#}", "", text)


def templates_matching(pattern, root=TEMPLATES, allowed=()):
    """The templates under root (as paths relative to it) whose text, comments left out, matches pattern, except those named in allowed."""
    names = [path.relative_to(root).as_posix() for path in sorted(root.rglob("*.html"))]
    return [name for name in names
            if name not in allowed and pattern.search(without_comments((root / name).read_text(encoding="utf-8")))]


def comments_that_span_lines(root=TEMPLATES):
    """(file, line) for every {# comment under root that does not close on the line it opens on."""
    found = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in (".html", ".txt"):
            for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
                if UNCLOSED_COMMENT.search(line):
                    found.append((path.relative_to(root).as_posix(), number))
    return found


def outline_removals(css, name):
    """(file, line) for every declaration in css that takes the focus outline away; comments are left out."""
    blank = re.sub(r"/\*.*?\*/", lambda comment: re.sub(r"[^\n]", " ", comment.group()), css, flags=re.DOTALL)
    return [(name, number) for number, line in enumerate(blank.split("\n"), start=1) if OUTLINE_REMOVED.search(line)]


class PageTemplateConventionsTests(SimpleTestCase):
    longMessage = False

    def test_only_the_shell_and_the_500_page_are_whole_documents(self):
        own = templates_matching(DOCUMENT_TAG, allowed=WHOLE_DOCUMENTS)
        self.assertEqual(own, [], "\n" + "\n".join(
            f"backend/templates/{name}: writes its own <html> or doctype. A page extends layouts/form.html (a form), layouts/chapter.html "
            "or layouts/reading.html and fills a block, so it gets the shell's head, boot script, skip link, menu and corners; only "
            "shell.html and 500.html (which must render with nothing else) are whole documents" for name in own))

    def test_only_the_500_page_carries_a_style_element(self):
        own = templates_matching(STYLE_ELEMENT, allowed=("500.html",))
        self.assertEqual(own, [], "\n" + "\n".join(
            f"backend/templates/{name}: has a <style> element. A page's CSS lives in backend/static/css (base.css for a layout, "
            "components/<name>.css for a component, pages/<name>.css for what only one page has, linked from its `styles` block) and uses "
            "the tokens; only 500.html, which must render with nothing else, carries its own <style>" for name in own))

    def test_every_template_comment_opens_and_closes_on_one_line(self):
        found = comments_that_span_lines()
        self.assertEqual(found, [], "\n" + "\n".join(
            f"backend/templates/{name}:{number}: a comment opened with {{# does not close on its line, and Django prints a comment "
            "that spans lines on the page as text. Write one {# #} per line, or wrap the lines in {% comment %} ... {% endcomment %}"
            for name, number in found))

    def test_the_conventions_find_what_they_name(self):
        files = {
            "shell.html": "<!DOCTYPE html><html><body></body></html>",
            "500.html": "<!doctype html><html><style>p { margin: 0 }</style></html>",
            "page.html": '{% extends "layouts/form.html" %}\n{# a note about <html> and <style> #}\n{% comment %}<html><style>{% endcomment %}',
            "own.html": "<!DOCTYPE html>\n<html>\n<style>\np { margin: 0 }\n</style>",
            "mail.txt": "{# fine #}\n{# opens here\n and goes on #}",
            "note.html": "{# one #} text {# two\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, text in files.items():
                (root / name).write_text(text, encoding="utf-8")
            self.assertEqual(templates_matching(DOCUMENT_TAG, root, WHOLE_DOCUMENTS), ["own.html"],
                             "backend/pages/tests_site_rules.py: templates_matching(DOCUMENT_TAG) must name a template that writes <html> or a doctype, "
                             "leave out the allowed ones, and not read a word in a {# #} comment or a {% comment %} block as markup")
            self.assertEqual(templates_matching(STYLE_ELEMENT, root, ("500.html",)), ["own.html"],
                             "backend/pages/tests_site_rules.py: templates_matching(STYLE_ELEMENT) must name a template that has a <style> element "
                             "and leave out the allowed one")
            self.assertEqual(comments_that_span_lines(root), [("mail.txt", 2), ("note.html", 1)],
                             "backend/pages/tests_site_rules.py: comments_that_span_lines() must name each {# that does not close on its line, "
                             "in .html and .txt templates, and no comment that does")


class FocusRingTests(SimpleTestCase):
    longMessage = False

    def test_base_css_draws_the_focus_outline_for_the_keyboard(self):
        css = (CSS / "base.css").read_text(encoding="utf-8")
        self.assertIsNotNone(re.search(r":focus-visible\s*\{[^}]*\boutline\s*:\s*[^;}]*\bsolid\b", css),
                             "backend/static/css/base.css: keep the global `:focus-visible` rule that draws the 1px outline (spec 3.4, 9). "
                             "Every control, a form's inputs included, shows keyboard focus with it")

    def test_no_stylesheet_takes_the_outline_away(self):
        found = []
        for path in sorted(CSS.rglob("*.css")):
            found += outline_removals(path.read_text(encoding="utf-8"), path.relative_to(REPO).as_posix())
        self.assertEqual(found, [], "\n" + "\n".join(
            f"{name}:{number}: takes the focus outline away. Keyboard focus must stay visible (spec 3.4, 9): leave the outline "
            "that `:focus-visible` in base.css draws, or give the control an outline of its own, never none" for name, number in found))

    def test_the_outline_check_finds_what_it_names(self):
        css = ("a { outline: none; }\n/* outline: 0 in a comment */\nb { outline: 0; }\nc { outline-offset: 0; }\n"
               "d { outline: 1px solid; }\ne { outline-width: 0.5px; }\nf { outline-style: none }\n")
        self.assertEqual(outline_removals(css, "x.css"), [("x.css", 1), ("x.css", 3), ("x.css", 7)],
                         "backend/pages/tests_site_rules.py: outline_removals() must name each declaration that sets the outline, "
                         "its style or its width to none, hidden or 0, and not a comment, an offset or a width of 0.5px")


# The test modules whose assertions compare whole pages. Each of their classes sets longMessage = False (ReadableFailuresTests).
READING_TEST_MODULES = ("pages.tests_reading_family", "pages.tests_reading", "pages.tests_legal", "pages.tests_docs_reading",
                        "blog.tests_journal", "blog.tests_feed", "pages.tests_forms", "drop_core.tests_sign_in_pages", "drop_core.tests_reset_verify_pages",
                        "drop_core.tests_device_pages",
                        "pages.tests_meter", "dashboard.tests_overview", "drop_core.tests_password_change_pages",
                        "drop_core.tests_oauth_consent",
                        "pages.tests_errors", "pages.tests_artifact_miss", "drop_core.tests_throttled_pages",
                        "pages.tests_form_fixtures",
                        "pages.tests_forms_on_the_shell",
                        "dashboard.tests_app", "dashboard.tests_menu_pages", "dashboard.tests_layout",
                        "dashboard.tests_files",
                        "dashboard.tests_keys",
                        "dashboard.tests_billing",
                        "pages.tests_dashboard_fixtures",
                        "dashboard.tests_recipe")


def cases_defined_in(module):
    """{name: class} for every unittest TestCase class the module defines itself, not one it imports."""
    return {name: value for name, value in vars(module).items()
            if isinstance(value, type) and issubclass(value, unittest.TestCase) and value.__module__ == module.__name__}


def cases_that_print_whole_pages(module):
    """The names of the module's TestCase classes whose longMessage is not False (a subclass inherits it from its base)."""
    return sorted(name for name, case in cases_defined_in(module).items() if case.longMessage is not False)


class ReadableFailuresTests(SimpleTestCase):
    def test_every_test_class_of_the_reading_pages_prints_only_its_message(self):
        for dotted in READING_TEST_MODULES:
            path = "backend/" + dotted.replace(".", "/") + ".py"
            with self.subTest(module=dotted):
                module = importlib.import_module(dotted)
                self.assertTrue(cases_defined_in(module),
                                f"{path}: defines no TestCase class, so this check sees nothing; if the module was renamed or "
                                "moved, fix READING_TEST_MODULES in backend/pages/tests_site_rules.py")
                loud = cases_that_print_whole_pages(module)
                self.assertEqual(loud, [], f"{path}: {', '.join(loud)} must set `longMessage = False` in the class body. unittest "
                                 "prints both sides of assertIn and assertEqual before the message, which for a page is up to "
                                 "160 KB, and the message that names the file and the fix then comes last, out of reach")

    def test_a_class_that_prints_whole_pages_is_found_by_name(self):
        module = types.ModuleType("fake_reading_tests")

        def define(name, base, **attributes):
            return type(name, (base,), {"__module__": module.__name__, **attributes})

        module.QuietTests = define("QuietTests", SimpleTestCase, longMessage=False)
        module.InheritsQuietTests = define("InheritsQuietTests", module.QuietTests)
        module.LoudTests = define("LoudTests", SimpleTestCase)
        module.SimpleTestCase = SimpleTestCase  # imported, not defined in the module: never counted
        module.not_a_case = lambda: None
        self.assertEqual(sorted(cases_defined_in(module)), ["InheritsQuietTests", "LoudTests", "QuietTests"],
                         "backend/pages/tests_site_rules.py: cases_defined_in() must list the TestCase classes a module defines, "
                         "and none it imports")
        self.assertEqual(cases_that_print_whole_pages(module), ["LoudTests"],
                         "backend/pages/tests_site_rules.py: a class is reported when its longMessage is not False; "
                         "a subclass of a quiet class is quiet")
