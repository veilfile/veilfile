"""The faces a page loads (spec 8): a page preloads the files its first screen sets text in, and a layout names the faces any of its pages may show,
which shell.js loads before a change swaps one in, so that no change waits on a font (spec 4.5)."""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from pages import registry
from pages.tests_fonts import faces

STATIC = Path(settings.BASE_DIR) / "static"
ITALIC_300 = "fonts/cormorant-garamond/italic-300-latin.woff2"   # a page's title (every chapter and every reading page)
ITALIC_400 = "fonts/cormorant-garamond/italic-400-latin.woff2"   # --font-heading: a chapter's headings, a plan's name, a section's and an entry's title
READING = "fonts/newsreader/normal-400-500-latin.woff2"
MONO = "fonts/ibm-plex-mono/normal-400-latin.woff2"
# What each page's first screen sets text in, by what it really uses (a browser's loaded faces, read at 375 and 1280 wide): About sets its title in
# italic 300 and its words in Newsreader and has no heading below the title; the threshold has only its line and the labels; How, Begin, Promises and Plans
# set headings in italic 400; the Docs index has its sections further down.
EXPECTED = {
    "landing": {ITALIC_300, MONO},
    "about": {ITALIC_300, MONO, READING},
    "how": {ITALIC_300, ITALIC_400, MONO, READING},
    "begin": {ITALIC_300, ITALIC_400, MONO, READING},
    "promises": {ITALIC_300, ITALIC_400, MONO, READING},
    "plans": {ITALIC_300, ITALIC_400, MONO, READING},
    "docs-index": {ITALIC_300, MONO, READING},
}
# The faces each layout names on <body> (data-faces), which shell.js loads at the menu's opening and when a change begins. Only the reading
# column sets the body's face in italic (emphasis, quotations); a chapter never changes in place to a reading page (another family), so it
# does not fetch that 147 KB file for nothing.
CHAPTER_FACES = "italic 300 1em Cormorant Garamond|italic 400 1em Cormorant Garamond|1em Newsreader"
# The dashboard sets its title in italic 300, its words in Newsreader and its labels in IBM Plex Mono (preloaded on every page): no heading of its own in
# italic 400, so a change in place among its sections does not fetch that file. A section that needs it adds it here and to templates/shell.html.
APP_FACES = "italic 300 1em Cormorant Garamond|1em Newsreader"
LAYOUT_FACES = {"chapter": CHAPTER_FACES, "reading": CHAPTER_FACES + "|italic 1em Newsreader", "app": APP_FACES}


def preloaded(html):
    """The font files a page's head preloads, as paths under static/."""
    return set(re.findall(r'<link rel="preload" href="/static/([^"]+\.woff2)" as="font"', html))


class FirstScreenPreloadTests(TestCase):
    longMessage = False

    def test_a_chapter_preloads_the_faces_its_first_screen_uses_and_no_more(self):
        for page in registry.PAGES:
            if page.layout != "chapter":
                continue
            with self.subTest(page=page.url_name):
                html = self.client.get(reverse(page.url_name)).content.decode()
                self.assertEqual(preloaded(html), EXPECTED[page.url_name],
                                 f"templates/chapters/{page.url_name}.html: the page preloads {sorted(preloaded(html))} where its first screen sets text in "
                                 f"{sorted(EXPECTED[page.url_name])} (about and the threshold override block preload_heading with nothing: they have no italic 400)")

    def test_a_reading_page_preloads_italic_400_when_it_has_a_section_or_an_entry_to_show(self):
        # Italic 400 sets a `##` section's heading (an h2) and the titles of an entries list. So: the Journal's list shows its entries on the first
        # screen; a post, a Docs page and a legal document have it only when their words have a `##` section (report-abuse.md has none); the Docs index has
        # its list of pages below the fold. What a page's words have is read from the page itself, not listed here.
        paths = [path for path in registry.layout_paths("reading")]
        self.assertGreater(len(paths), 8, "the reading layout lists the Journal, its posts, the Docs and the legal documents")
        for path in paths:
            with self.subTest(path=path):
                html = self.client.get(path).content.decode()
                main = html[html.index("<main"):html.index("</main>")]
                if path in (reverse("docs-index"), reverse("docs-page", args=["index"])):   # the same page twice
                    wanted = EXPECTED["docs-index"]
                elif path == reverse("blog-index"):
                    wanted = {ITALIC_300, ITALIC_400, MONO, READING}
                else:
                    wanted = {ITALIC_300, MONO, READING} | ({ITALIC_400} if "<h2" in main else set())
                self.assertEqual(preloaded(html), wanted,
                                 f"{path} preloads {sorted(preloaded(html))}; its first screen sets text in {sorted(wanted)} (layouts/reading.html preloads italic 400 in block "
                                 f"preload_heading; a post, a Docs page and a legal document keep it only with a `##` section, the Docs index not at all)")

    def test_the_templates_that_show_words_preload_italic_400_by_whether_the_words_have_a_section(self):
        # The pages' own words decide (a legal document is the owner's, and a Docs page can lose its last `##`), so the condition is tried on the templates
        # with words that have a section and words that have none.
        from django.contrib.auth.models import AnonymousUser
        from django.template.loader import render_to_string
        from django.test import RequestFactory
        from types import SimpleNamespace
        with_section, without = "<h1>T</h1><h2 id=\"a\">A</h2><p>words</p>", "<h1>T</h1><p>words</p>"
        cases = (("legal/document.html", "/terms/", {"intro_html": "", "sections": []}),
                 ("docs/page.html", "/docs/quickstart/", {"page": SimpleNamespace(title="Quickstart"), "sections": [], "previous_page": None, "next_page": None}))
        for template, path, context in cases:
            for words, has in ((with_section, True), (without, False)):
                with self.subTest(template=template, section=has):
                    request = RequestFactory().get(path)
                    request.user = AnonymousUser()
                    html = render_to_string(template, {**context, "body_html": words}, request=request)
                    self.assertEqual(ITALIC_400 in preloaded(html), has,
                                     f"{template}: with {'a' if has else 'no'} `##` section in its words it {'must' if has else 'must not'} preload italic 400 "
                                     f"(preloaded: {sorted(preloaded(html))})")

    def test_the_preloaded_files_are_the_ones_the_stylesheet_declares(self):
        for path in (ITALIC_300, ITALIC_400, READING, MONO):
            with self.subTest(path=path):
                self.assertTrue((STATIC / path).is_file(), f"static/{path} is preloaded but is not there")
                self.assertIn(f"../{path}", (STATIC / "css/fonts.css").read_text(encoding="utf-8"), f"static/css/fonts.css never uses static/{path}")


class LayoutFacesTests(TestCase):
    longMessage = False

    def test_each_page_of_a_layout_names_the_faces_any_of_its_pages_may_show(self):
        dashboard = Client()   # the dashboard's sections are for a signed-in visitor
        dashboard.force_login(get_user_model().objects.create_user("reader", "reader@example.com", "a-test-only-password"))
        for layout in ("chapter", "reading", "app"):
          for path in registry.layout_paths(layout):
            with self.subTest(path=path):
                html = (dashboard if layout == "app" else self.client).get(path).content.decode()
                self.assertIn(f'data-faces="{LAYOUT_FACES[layout]}"', html[:html.index("</body>")] if "</body>" in html else html,
                              f"templates/shell.html: {path} names the layout's faces on <body> (data-faces), which shell.js loads at the menu's opening and when a change begins")

    def test_the_reading_columns_emphasis_and_quotations_have_their_italic_named(self):
        # `.reading em, i, cite` and a blockquote are set in Newsreader's italic (the body's face, base.css), which no page's first screen need preload but a
        # reading page with emphasis in its words shows once its content is laid out: unnamed, the swap would wait for its file.
        base = re.sub(r"/\*.*?\*/", "", (STATIC / "css/base.css").read_text(encoding="utf-8"), flags=re.S)
        italic = re.findall(r"(\.reading :where\([^)]*\)) \{[^}]*font-style: italic;", base)
        for rule in (".reading :where(em, i, cite)", ".reading :where(blockquote)"):
            self.assertIn(rule, italic, f"static/css/base.css: {rule} is set in italic, in the reading font (the body's): data-faces names Newsreader's italic for it")
        self.assertRegex(base, r"body \{[^}]*font-family: var\(--font-reading\)")
        self.assertIn("italic 1em Newsreader", LAYOUT_FACES["reading"].split("|"), "the reading font's italic is a face of the reading layout")
        self.assertNotIn("italic 1em Newsreader", LAYOUT_FACES["chapter"].split("|"),
                         "a chapter shows no Newsreader italic and never changes in place to a reading page: it must not fetch that file")

    def test_the_dashboard_names_the_title_and_the_reading_face_and_not_the_headings_italic_400(self):
        self.assertEqual(LAYOUT_FACES["app"].split("|"), ["italic 300 1em Cormorant Garamond", "1em Newsreader"])
        self.assertNotIn("italic 400 1em Cormorant Garamond", LAYOUT_FACES["app"].split("|"),
                         "templates/shell.html: the dashboard sets no heading in --font-heading (italic 400), so it does not fetch that file; "
                         "if a section needs it, name it in shell.html and here together")

    def test_a_page_with_no_layout_names_none(self):
        # nothing changes in place there, so nothing is loaded ahead
        from django.template.loader import render_to_string
        from django.test import RequestFactory
        from django.contrib.auth.models import AnonymousUser
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        self.assertNotIn("data-faces", render_to_string("shell.html", {}, request=request))

    def test_every_named_face_is_declared_in_the_stylesheet(self):
        # document.fonts.load("italic 300 1em Cormorant Garamond") finds a face by family, style and weight: one the stylesheet does not declare loads nothing
        declared = [(face["font-family"], face["font-style"], face["font-weight"]) for face in faces() if "unicode-range" in face]
        for named in LAYOUT_FACES["reading"].split("|"):   # the reading layout names every face the chapters do, and one more
            with self.subTest(face=named):
                found = re.fullmatch(r"(?:(italic) )?(?:(\d+) )?1em (.+)", named)
                self.assertTrue(found, f"{named!r}: a font shorthand of a style (optional), a weight (optional), 1em and the family")
                style, weight, family = found.group(1) or "normal", found.group(2) or "400", found.group(3)
                matching = [entry for entry in declared if entry[0] == family and entry[1] == style
                            and (entry[2] == weight or (" " in entry[2] and int(entry[2].split()[0]) <= int(weight) <= int(entry[2].split()[1])))]
                self.assertTrue(matching, f"static/css/fonts.css declares no {style} {weight} {family}, so loading {named!r} loads nothing")
