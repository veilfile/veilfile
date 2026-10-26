"""The registry of public pages (spec 6.5): one place for titles, descriptions and the menu."""
import re
from html.parser import HTMLParser
from pathlib import Path

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from pages import registry


class _Head(HTMLParser):
    """Reads <title>, the meta description and the canonical link (unescaping entities)."""

    def __init__(self):
        super().__init__()
        self.title, self.description, self.canonical, self._in_title = "", None, None, False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta" and attributes.get("name") == "description":
            self.description = attributes.get("content")
        elif tag == "link" and attributes.get("rel") == "canonical":
            self.canonical = attributes.get("href")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


class RegistryTests(SimpleTestCase):
    def test_every_page_reverses_once(self):
        names = [page.url_name for page in registry.PAGES]
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            with self.subTest(page=name):
                reverse(name)

    def test_titles_and_descriptions_fit_search_results(self):
        for page in registry.PAGES:
            with self.subTest(page=page.url_name):
                self.assertLessEqual(len(page.title), 60)
                self.assertGreaterEqual(len(page.description), 70)
                self.assertLessEqual(len(page.description), 160)

    def test_how_it_works_says_the_link_stops_before_the_file_is_deleted(self):
        # Ruled during PR 2: in search results the order matters (the link stops, then a sweep deletes the
        # file), and the length test alone would let the sentence drift to something that fits and is wrong.
        self.assertIn("When its days run out, the link stops working and the file is deleted soon after.",
                      registry.page("how").description)

    def test_canonical_urls_use_the_public_origin(self):
        self.assertEqual(registry.canonical_url("about"), "https://veilfile.com/about/")
        self.assertEqual(registry.canonical_url("landing"), "https://veilfile.com/")

    def test_menu_links_reverse_and_name_a_known_audience(self):
        for group in registry.MENU:
            for link in group:
                with self.subTest(link=link.label):
                    reverse(link.url_name)
                    self.assertIn(link.audience, {"everyone", "signed-in", "signed-out"})

    def test_signed_out_menu(self):
        labels = [[item["label"] for item in group] for group in registry.menu_groups(signed_in=False)]
        self.assertEqual(labels, [
            ["About", "How it works", "Begin", "Promises", "Plans"],
            ["Docs", "Journal", "Sign in"],
            ["Terms", "Privacy", "Report abuse", "llms.txt"],
        ])

    def test_signed_in_menu_offers_the_dashboard(self):
        group = registry.menu_groups(signed_in=True)[1]
        self.assertEqual([item["label"] for item in group], ["Docs", "Journal", "Dashboard"])
        self.assertEqual(group[2]["href"], "/app/")

    def test_the_current_page_is_marked(self):
        groups = registry.menu_groups(signed_in=False, current="plans")
        marked = [(item["label"], item["current"]) for group in groups for item in group if item["current"]]
        self.assertEqual(marked, [("Plans", "page")])

    def test_a_page_inside_a_section_marks_the_section(self):
        # A Docs page or a Journal post is not the Docs or Journal page itself, so its link gets
        # aria-current="true" (in this section), not "page", and the menu does not open by itself.
        for current, label in (("docs-page", "Docs"), ("blog-post", "Journal")):
            with self.subTest(current=current):
                groups = registry.menu_groups(signed_in=False, current=current)
                marked = [(item["label"], item["current"]) for group in groups for item in group if item["current"]]
                self.assertEqual(marked, [(label, "true")])

    def test_the_menu_component_writes_the_mark(self):
        html = render_to_string("components/menu.html", {
            "groups": registry.menu_groups(signed_in=False, current="docs-page"), "list_id": "m"})
        self.assertIn('href="/docs/" aria-current="true">Docs</a>', html)
        html = render_to_string("components/menu.html", {
            "groups": registry.menu_groups(signed_in=False, current="plans"), "list_id": "m"})
        self.assertIn('href="/plans/" aria-current="page">Plans</a>', html)

    def test_page_lookup(self):
        self.assertEqual(registry.page("plans").title, "Plans | Veilfile")
        with self.assertRaises(KeyError):
            registry.page("nope")


class RegistryMatchesRenderedPagesTests(TestCase):
    def test_each_page_renders_its_registry_metadata(self):
        for page in registry.PAGES:
            with self.subTest(page=page.url_name):
                response = self.client.get(reverse(page.url_name))
                self.assertEqual(response.status_code, 200)
                head = _Head()
                head.feed(response.content.decode())
                self.assertEqual(head.title.strip(), page.title)
                self.assertEqual(head.description, page.description)
                self.assertEqual(head.canonical, registry.canonical_url(page.url_name))


class DocsIndexDescriptionTests(SimpleTestCase):
    def test_the_docs_index_description_matches_its_front_matter(self):
        text = (Path(__file__).resolve().parent.parent / "docs" / "pages" / "index.md").read_text(encoding="utf-8")
        front_matter = re.search(r"^description:[ \t]*(.+?)[ \t]*$", text, re.MULTILINE).group(1)
        self.assertEqual(
            registry.page("docs-index").description, front_matter,
            "backend/pages/registry.py and backend/docs/pages/index.md: the Docs index description must be the same words in both")
