"""The component catalog (spec 4.8): staff-only, every component in Paper and Ink, Motion on and off."""
import re
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from pages import catalog

URL = "/app/components/"
COMPONENTS = Path(settings.BASE_DIR) / "templates" / "components"


class CatalogAccessTests(TestCase):
    def test_anonymous_visitors_get_not_found(self):
        self.assertEqual(self.client.get(URL).status_code, 404)

    def test_signed_in_visitors_who_are_not_staff_get_not_found(self):
        user = get_user_model().objects.create_user("reader", "reader@example.com", "a-test-only-password")
        self.client.force_login(user)
        self.assertEqual(self.client.get(URL).status_code, 404)


class CatalogPageTests(TestCase):
    def setUp(self):
        staff = get_user_model().objects.create_user("staff", "staff@example.com", "a-test-only-password", is_staff=True)
        self.client.force_login(staff)
        self.response = self.client.get(URL)
        self.html = self.response.content.decode()

    def test_preloads_the_display_face_for_its_heading(self):
        self.assertIn('<link rel="preload" href="/static/fonts/cormorant-garamond/italic-300-latin.woff2" '
                      'as="font" type="font/woff2" crossorigin>', self.html)

    def test_staff_see_every_component_in_four_frames(self):
        self.assertEqual(self.response.status_code, 200)
        for entry in catalog.entries():
            with self.subTest(component=entry.name):
                self.assertIn(f'id="component-{entry.name}"', self.html)
        self.assertEqual(self.html.count('class="catalog__frame"'), 4 * len(catalog.entries()))
        for theme, motion in (("light", "on"), ("dark", "on"), ("light", "off"), ("dark", "off")):
            self.assertIn(f'data-theme="{theme}" data-motion="{motion}"', self.html)

    def test_is_kept_out_of_search(self):
        self.assertEqual(self.response["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn('<meta name="robots" content="noindex, nofollow">', self.html)

    def test_ids_are_unique(self):
        repeated = [name for name, count in Counter(re.findall(r'\sid="([^"]+)"', self.html)).items() if count > 1]
        self.assertEqual(repeated, [])

    def test_uses_the_shell(self):
        self.assertIn('<script id="veilfile-shell-boot">', self.html)

    def test_the_reading_columns_components_are_shown_inside_it(self):
        # sections, pager and entries only ever sit in the reading column, whose rules (base.css) style their links.
        inside = {entry.name for entry in catalog.entries() if entry.within == "reading"}
        self.assertEqual(inside, {"sections", "pager", "entries"},
                         "pages/catalog.py: pass within=\"reading\" to the Entry of each component the reading column holds")
        self.assertEqual(self.html.count('<div class="reading">'), 4 * len(inside))


class CatalogSampleTests(TestCase):
    def test_the_plan_entry_shows_team_whatever_order_the_cards_come_in(self):
        entry = next(entry for entry in catalog.entries() if entry.name == "plan")
        self.assertEqual(entry.context["plan"]["slug"], "team")
        self.assertIn("here Team", entry.note)


class CatalogCoverageTests(TestCase):
    def test_every_component_has_an_entry(self):
        on_disk = {path.stem for path in COMPONENTS.glob("*.html")}
        self.assertEqual(on_disk, {entry.name for entry in catalog.entries()},
                         "add an Entry to pages/catalog.py for each component in templates/components/")
