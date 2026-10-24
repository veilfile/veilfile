"""The legal pages (spec 4.6): the owner's documents on the reading layout, their words untouched.

backend/legal/documents/* is the owner's (AGENTS.md invariant 8). The page may dress the documents but
never change them: these tests hold every word on the page to the Markdown, in order, and every bold
run to a <strong>.

Every class sets longMessage = False, so that a failure prints its own message, which names the file
and the fix, and not the page it searched (tens of kilobytes of HTML).
"""
import json
import re
from html import unescape

from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils.html import escape

from blog.render import render_markdown, render_reading
from legal import views
from pages import registry

DOCUMENTS = tuple(views.DOCUMENTS)  # terms, privacy, report-abuse
LD_JSON = re.compile(r'<script type="application/ld\+json" data-page-meta>(.*?)</script>', re.DOTALL)
NAV = re.compile(r"<nav\b.*?</nav>", re.DOTALL)


def source_file(name):
    filename, _title = views.DOCUMENTS[name]
    return views.DOCUMENTS_DIR / filename


def source_of(name):
    return source_file(name).read_text(encoding="utf-8")


def words(html):
    """What a reader reads in a piece of HTML: tags removed, entities decoded, white space made single spaces."""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def first_difference(shown, written):
    """Where two lists first part, a few items of each around the place, so that a failure can name it."""
    index = next((i for i, pair in enumerate(zip(shown, written)) if pair[0] != pair[1]),
                 min(len(shown), len(written)))

    def near(items):
        return items[max(index - 3, 0):index + 4]

    return (f"at item {index} (the page has {len(shown)} items, the document {len(written)}): "
            f"the page reads {near(shown)!r}, the document reads {near(written)!r}")


class LegalPageCase(TestCase):
    longMessage = False

    def page(self, name):
        response = self.client.get(f"/{name}/")
        self.assertEqual(response.status_code, 200,
                         f"/{name}/ answered {response.status_code}, not 200 (backend/legal/urls.py, backend/legal/views.py)")
        return response.content.decode()

    def article(self, name):
        found = re.findall(r"<article\b[^>]*>(.*?)</article>", self.page(name), re.DOTALL)
        self.assertEqual(len(found), 1, f"/{name}/ should hold its document in one <article> "
                         f"(backend/templates/legal/document.html), found {len(found)}")
        return found[0]


class LegalWordsTests(LegalPageCase):
    longMessage = False

    def test_the_page_says_what_the_document_says_and_nothing_else(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                shown = words(NAV.sub(" ", self.article(name))).split(" ")
                written = words(render_markdown(source_of(name))).split(" ")
                self.assertEqual(
                    shown, written,
                    f"The words on /{name}/ differ from {source_file(name).name} rendered the way it always was, "
                    f"{first_difference(shown, written)}. "
                    "The legal text is the owner's (AGENTS.md invariant 8): change the template, the view or the "
                    "stylesheet, never the document.")

    def test_every_bold_run_is_still_a_strong(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                written = re.findall(r"\*\*(.+?)\*\*", source_of(name))
                shown = [words(run) for run in re.findall(r"<strong>(.*?)</strong>", self.article(name), re.DOTALL)]
                self.assertEqual(
                    shown, written,
                    f"/{name}/: every **bold** run of {source_file(name).name} must stay a <strong> element, "
                    f"{first_difference(shown, written)}. "
                    "Its weight is the stylesheet's business (backend/static/css), not a reason to flatten it.")

    def test_the_last_updated_line_stays_a_plain_paragraph(self):
        for name in DOCUMENTS:
            for line in (line for line in source_of(name).split("\n") if line.startswith("Last updated:")):
                with self.subTest(document=name):
                    self.assertIn(f"<p>{line}</p>", self.article(name),
                                  f"/{name}/: the line '{line}' must stay a plain <p>, with no class and no wrapper")

    def test_no_document_holds_a_code_block(self):
        # The comparison above is made against render_markdown, which draws no Copy button. The reading column
        # draws every code block with one (components/code_block.html), and its word is not the document's.
        for name in DOCUMENTS:
            with self.subTest(document=name):
                self.assertNotIn(
                    "<pre", render_markdown(source_of(name)),
                    f"{source_file(name).name} now holds a code block. The reading column draws a code block with a Copy "
                    "button (backend/templates/components/code_block.html), a word the document does not hold, so the "
                    "word comparison in this file would count it. If the owner meant the code block, make words() in "
                    "this file leave the button out; do not edit the document.")


class LegalReadingLayoutTests(LegalPageCase):
    longMessage = False

    def test_every_document_has_a_registry_page_in_the_reading_layout(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                self.assertEqual(registry.page(name).layout, "reading",
                                 f'backend/pages/registry.py: the Page named "{name}" needs layout="reading"')

    def test_each_document_is_drawn_by_the_reading_layout(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                response = self.client.get(f"/{name}/")
                self.assertIn("layouts/reading.html", [template.name for template in response.templates],
                              "backend/templates/legal/document.html must extend layouts/reading.html")
                html = response.content.decode()
                self.assertRegex(html, r'<body class="[^"]*\blayout-reading\b',
                                 f"/{name}/: <body> needs the class layout-reading (backend/templates/layouts/reading.html)")
                self.assertIn('data-layout="reading"', html,
                              f'/{name}/: <body> needs data-layout="reading" (the Page in backend/pages/registry.py '
                              'has layout="reading" and backend/templates/shell.html writes it)')
                found = re.search(r'data-layout-paths="([^"]*)"', html)
                self.assertIsNotNone(found, f"/{name}/: <body> carries no data-layout-paths")
                for path in ("/terms/", "/privacy/", "/report-abuse/"):
                    self.assertIn(path, found.group(1).split(),
                                  f"/{name}/: in-place changes must reach {path} (layout_paths in backend/pages/registry.py)")

    def test_the_head_comes_from_the_registry_and_says_website(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                page, html = registry.page(name), self.page(name)
                self.assertIn(f"<title>{escape(page.title)}</title>", html,
                              f"/{name}/: the <title> must be the Page's title in backend/pages/registry.py")
                self.assertIn(f'<link rel="canonical" href="{registry.canonical_url(name)}" data-page-meta>', html,
                              f"/{name}/: the head must come from shell.html, which reads the Page in backend/pages/registry.py")
                self.assertIn('<meta property="og:type" content="website" data-page-meta>', html,
                              f"/{name}/ is not an article: og:type is website (backend/templates/shell.html)")
                kinds = [json.loads(block)["@type"] for block in LD_JSON.findall(html)]
                self.assertEqual(kinds, ["Organization"], f"/{name}/ is not an article: it carries Organization data only")

    def test_landmarks_survive_the_move(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                html = self.page(name)
                self.assertIn('<a class="skip-link label" href="#main">', html,
                              f"/{name}/ lost the skip link: backend/templates/legal/document.html must extend layouts/reading.html")
                self.assertIn('<main id="main" class="shell-main">', html,
                              f"/{name}/ lost its <main id=\"main\">: backend/templates/legal/document.html must extend layouts/reading.html")
                main = html[html.index('<main id="main"'):html.index("</main>")]
                self.assertEqual(len(re.findall(r"<h1[ >]", html)), 1,
                                 f"/{name}/ should have exactly one h1, the document's own # heading")
                self.assertEqual(len(re.findall(r"<h1[ >]", main)), 1, f"/{name}/: the h1 belongs inside <main>")
                for element in ("<header", "<footer"):
                    self.assertNotIn(element, main, f"/{name}/: no {element}> inside <main>; "
                                     "backend/static/css/base.css styles the bare element, so use a div")

    def test_the_report_abuse_address_is_still_a_mailto_link(self):
        self.assertIn('<a href="mailto:abuse@veilfile.com">abuse@veilfile.com</a>', self.article("report-abuse"),
                      "/report-abuse/: the address must stay a mailto link, as report-abuse.md writes it")


class LegalSectionListTests(LegalPageCase):
    longMessage = False

    def test_every_document_opens_with_its_own_heading(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                self.assertTrue(self.article(name).lstrip().startswith("<h1"),
                                f"/{name}/: the document's own # heading must come first in the <article>")

    def test_a_long_document_lists_its_sections_right_before_the_first_of_them(self):
        for name in ("terms", "privacy"):
            with self.subTest(document=name):
                article = self.article(name)
                navs = NAV.findall(article)
                self.assertEqual(len(navs), 1, f"/{name}/ should show one section list inside its <article>")
                self.assertIn("Contents", navs[0], f"/{name}/: the section list is labelled Contents "
                              "(backend/templates/components/sections.html)")
                nav = NAV.search(article)
                before, after = article[:nav.start()], article[nav.end():]
                self.assertRegex(before, r"</h1>\s*<p>Last updated: [^<]*</p>\s*$",
                                 f"/{name}/ should read: the h1 title, the 'Last updated' paragraph, the section list, then "
                                 "the first section (split_before_sections in backend/legal/views.py)")
                self.assertNotIn("<h2", before, f"/{name}/: the section list goes before the first section")
                self.assertTrue(after.lstrip().startswith("<h2"),
                                f"/{name}/: the first h2 must follow the section list directly "
                                "(backend/templates/legal/document.html)")
                sections = NAV.sub(" ", article)
                ids = re.findall(r'<h2\b[^>]*\bid="([^"]+)"', sections)
                self.assertEqual(len(ids), sections.count("<h2"),
                                 f"/{name}/: every ## heading needs an id (render_reading in backend/blog/render.py)")
                self.assertEqual(len(set(ids)), len(ids), f"/{name}/: two headings share an id")
                self.assertEqual(re.findall(r'href="#([^"]+)"', navs[0]), ids,
                                 f"/{name}/: the section list must link to every ## heading, in order "
                                 "(backend/templates/components/sections.html)")
                titles = [words(title) for title in re.findall(r"<h2\b[^>]*>(.*?)</h2>", sections, re.DOTALL)]
                listed = [words(title) for title in re.findall(r'<a\b[^>]*href="#[^"]*"[^>]*>(.*?)</a>', navs[0], re.DOTALL)]
                self.assertEqual(listed, titles, f"/{name}/: the section list must name the sections as the document does")

    def test_report_abuse_has_no_section_list(self):
        self.assertNotIn("<nav", self.article("report-abuse"),
                         "/report-abuse/ has no ## headings, so it shows no section list "
                         "(backend/templates/components/sections.html shows nothing for fewer than two)")


class LegalViewTests(SimpleTestCase):
    longMessage = False

    def test_the_registry_is_the_only_source_of_a_legal_pages_head(self):
        self.assertFalse(hasattr(views, "DOCUMENT_META"),
                         "backend/legal/views.py: a legal page's title, description and address come from its Page in "
                         "backend/pages/registry.py; delete DOCUMENT_META")

    def test_an_unknown_document_is_a_404(self):
        with self.assertRaises(Http404):
            views.legal_document(RequestFactory().get("/nope/"), "nope")

    def test_the_document_is_split_before_its_first_section_without_losing_a_character(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                html = render_reading(source_of(name)).html
                intro, rest = views.split_before_sections(html)
                self.assertEqual(intro + rest, html,
                                 f"{name}: split_before_sections in backend/legal/views.py must cut the page in two "
                                 "without losing or adding a character")
                self.assertTrue(intro.startswith("<h1"),
                                f"{name}: the opening must start with the document's h1, it starts {intro[:40]!r}")
                self.assertNotIn("<h2", intro, f"{name}: the opening ends before the first <h2")
                self.assertTrue(rest == "" or rest.startswith("<h2"),
                                f"{name}: the rest must start at the first <h2, it starts {rest[:40]!r}")

    def test_a_document_without_sections_is_all_intro(self):
        self.assertEqual(views.split_before_sections("<p>Words.</p>"), ("<p>Words.</p>", ""),
                         "backend/legal/views.py: with no <h2 the whole page is the opening and the rest is empty")
        html = render_reading(source_of("report-abuse")).html
        self.assertEqual(views.split_before_sections(html), (html, ""),
                         "backend/legal/views.py: report-abuse.md has no ## heading, so all of it is the opening")
