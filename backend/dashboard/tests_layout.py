"""The app layout and its family (spec 4.8, 6.2): the shell's entry for a section, the public head a section does not print, the body's family attributes and the faces."""
import re
from types import SimpleNamespace
from unittest import mock

from django.template.loader import render_to_string
from django.test import TestCase

from dashboard.tests_app import SECTIONS, headings, signed_in_client
from pages import registry
from pages.context_processors import shell_page
from pages.tests_head import request_for

APP_PATHS = "/app/ /app/files/ /app/keys/ /app/apps/ /app/billing/"
APP_FACES = "italic 300 1em Cormorant Garamond|1em Newsreader"
PUBLIC_HEAD = ('rel="canonical"', 'property="og:', 'name="twitter:card"', "application/ld+json", 'rel="alternate"', 'name="description"', "data-page-meta")


def head_and_body_tag(html):
    return html[:html.index("</head>")], re.search(r"<body[^>]*>", html).group(0)


class AppEntryTests(TestCase):
    longMessage = False

    def test_each_section_has_an_entry_in_the_app_layout_with_its_title_and_no_public_address(self):
        for name, address, label in SECTIONS:
            with self.subTest(section=name):
                entry = shell_page(request_for(address))["shell_page"]
                self.assertTrue(entry, f"backend/pages/context_processors.py: shell_page gives {name} an entry, or it joins no family")
                self.assertEqual(entry["title"], f"{label} | Veilfile")
                self.assertEqual(entry["layout"], "app")
                self.assertEqual(entry["layout_paths"], APP_PATHS, "backend/pages/registry.py: layout_paths('app') is the five sections' addresses, in menu order")
                self.assertEqual((entry["canonical"], entry["description"], entry["image"], entry["article"]), ("", "", "", None),
                                 "backend/pages/context_processors.py: a dashboard section has no canonical address, description or preview image")

    def test_the_entry_is_built_when_read_and_asks_for_no_static_file(self):
        request = request_for("/app/files/")
        with mock.patch("pages.context_processors.static", side_effect=ValueError("Missing staticfiles manifest entry")):
            entry = shell_page(request)["shell_page"]
            self.assertEqual(entry["title"], "Files | Veilfile", "backend/pages/context_processors.py: a dashboard entry needs no preview image, so no manifest entry")

    def test_a_dashboard_name_that_is_not_a_section_has_no_entry(self):
        # A confirmation page is listed in DASHBOARD_SECTIONS (it marks its section in the menu) but is a page of its own: it loads as an ordinary page.
        request = request_for("/app/")
        request.resolver_match = SimpleNamespace(url_name="app-file-revoke", kwargs={})
        with mock.patch.dict(registry.DASHBOARD_SECTIONS, {"app-file-revoke": "app-files"}):
            self.assertEqual(shell_page(request), {"shell_page": None}, "backend/pages/registry.py: dashboard_section() finds only the five sections of DASHBOARD_MENU")
        self.assertFalse(shell_page(request_for("/app/components/"))["shell_page"])

    def test_the_family_is_the_five_sections_and_no_chapter_or_reading_page(self):
        self.assertEqual(" ".join(registry.layout_paths("app")), APP_PATHS)
        for layout in ("chapter", "reading"):
            self.assertFalse(set(registry.layout_paths("app")) & set(registry.layout_paths(layout)))


class AppHeadTests(TestCase):
    longMessage = False

    def setUp(self):
        self.client = signed_in_client()

    def test_a_section_prints_none_of_the_public_head(self):
        for name, address, label in SECTIONS:
            with self.subTest(section=name):
                html = self.client.get(address).content.decode()
                head, _ = head_and_body_tag(html)
                self.assertIn(f"<title>{label} | Veilfile</title>", head)
                for needle in PUBLIC_HEAD:
                    self.assertNotIn(needle, head, f"backend/templates/shell.html: {address} is not a public page, so its head prints no {needle}; the public head is printed only when shell_page.canonical is set")

    def test_it_is_kept_out_of_search_by_a_tag_as_well_as_the_header(self):
        for _, address, _ in SECTIONS:
            with self.subTest(address=address):
                head, _ = head_and_body_tag(self.client.get(address).content.decode())
                self.assertIn('<meta name="robots" content="noindex, nofollow">', head, "backend/templates/layouts/app.html: the meta block carries the noindex tag")

    def test_the_body_names_the_family_its_paths_and_its_faces(self):
        for _, address, _ in SECTIONS:
            with self.subTest(address=address):
                _, body = head_and_body_tag(self.client.get(address).content.decode())
                self.assertEqual(body, f'<body class="layout-app" data-veil-src="/static/js/veil.js" data-layout="app" data-layout-paths="{APP_PATHS}" data-faces="{APP_FACES}">',
                                 "backend/templates/shell.html: data-layout, data-layout-paths and data-faces come from shell_page.layout; layouts/app.html gives the body class")

    def test_it_preloads_the_display_face_for_its_title_and_the_two_it_always_does(self):
        html = self.client.get("/app/").content.decode()
        self.assertEqual(set(re.findall(r'<link rel="preload" href="/static/([^"]+\.woff2)" as="font"', html)),
                         {"fonts/cormorant-garamond/italic-300-latin.woff2", "fonts/newsreader/normal-400-500-latin.woff2", "fonts/ibm-plex-mono/normal-400-latin.woff2"},
                         "backend/templates/layouts/app.html preloads italic 300 (the title); italic 400 is not set anywhere in the dashboard")

    def test_an_entry_with_a_layout_and_no_canonical_address_prints_no_empty_one(self):
        # The trap in a half-made entry: shell.html resolves a missing key to an empty string, so keyed on the entry itself it would print
        # <link rel="canonical" href=""> and blank Open Graph tags.
        request = request_for("/app/")
        entry = {"title": "Anything | Veilfile", "layout": "app", "layout_paths": "/app/"}
        head, body = head_and_body_tag(render_to_string("shell.html", {"shell_page": entry}, request=request))
        self.assertIn("<title>Anything | Veilfile</title>", head)
        for needle in PUBLIC_HEAD:
            self.assertNotIn(needle, head, f"backend/templates/shell.html: print {needle} only when shell_page.canonical is set")
        self.assertIn('data-layout="app"', body, "the family comes from shell_page.layout whatever the head prints")

    def test_a_public_page_still_prints_its_whole_head(self):
        head, body = head_and_body_tag(self.client.get("/about/").content.decode())
        for needle in PUBLIC_HEAD:
            self.assertIn(needle, head)
        self.assertNotIn(APP_FACES, body)


class AppLayoutTests(TestCase):
    longMessage = False

    def test_the_page_is_one_column_in_main_with_one_h1_and_the_shells_landmarks(self):
        html = signed_in_client().get("/app/").content.decode()
        main = html[html.index("<main"):html.index("</main>")]
        self.assertTrue(main.startswith('<main id="main" class="shell-main"><div class="app">'), "backend/templates/layouts/app.html: the column is <div class=\"app\"> inside <main>")
        self.assertEqual(headings(html), ["Overview"])
        self.assertNotRegex(main, r"<(header|footer)\b", "no header or footer element inside <main>")
        self.assertIn('class="skip-link label"', html)
