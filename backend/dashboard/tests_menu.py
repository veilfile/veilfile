"""The menu inside the dashboard (spec 4.4), as data and as a component: the five sections in group 1, Docs, Journal and Site in group 2 and a Sign out button that ends it."""
import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.urls import get_resolver, reverse

from dashboard import urls as dashboard_urls
from pages import registry

MENU_COMPONENT = Path(settings.BASE_DIR) / "templates" / "components" / "menu.html"
SECTION_LABELS = ["Overview", "Files", "Keys", "Connected apps", "Billing"]
SECTION_NAMES = ["app", "app-files", "app-keys", "app-apps", "app-billing"]
QUIET_GROUP = ["Terms", "Privacy", "Report abuse", "llms.txt"]


def labels(groups):
    return [[item["label"] for item in group] for group in groups]


def marked(groups):
    return [(item["label"], item["current"]) for group in groups for item in group if item["current"]]


def inside(current="app"):
    return registry.menu_groups(signed_in=True, current=current, inside_dashboard=True)


class DashboardMenuDataTests(SimpleTestCase):
    longMessage = False

    def test_group_one_is_the_five_sections_in_the_order_the_spec_lists_them(self):
        self.assertEqual([link.label for link in registry.DASHBOARD_MENU], SECTION_LABELS, "backend/pages/registry.py: DASHBOARD_MENU is Overview, Files, Keys, Connected apps, Billing (spec 4.8)")
        self.assertEqual([link.url_name for link in registry.DASHBOARD_MENU], SECTION_NAMES)
        self.assertEqual([reverse(link.url_name) for link in registry.DASHBOARD_MENU], ["/app/", "/app/files/", "/app/keys/", "/app/apps/", "/app/billing/"])
        self.assertEqual({link.audience for link in registry.DASHBOARD_MENU}, {"everyone"}, "backend/pages/registry.py: a visitor inside the dashboard is signed in, so no section hides")

    def test_inside_the_dashboard_group_one_is_the_sections_group_two_gains_site_and_group_three_is_the_same(self):
        groups = inside()
        self.assertEqual(labels(groups), [SECTION_LABELS, ["Docs", "Journal", "Site"], QUIET_GROUP],
                         "backend/pages/registry.py: inside the dashboard group 1 is the sections, group 2 is Docs, Journal and Site (the Sign out button is the menu component's), group 3 is unchanged")
        self.assertEqual([item["href"] for item in groups[1]], ["/docs/", "/blog/", "/"])
        self.assertEqual(groups[2], registry.menu_groups(signed_in=True)[2])

    def test_outside_the_dashboard_nothing_changes(self):
        for signed_in in (False, True):
            with self.subTest(signed_in=signed_in):
                self.assertEqual(registry.menu_groups(signed_in=signed_in, current="plans"), registry.menu_groups(signed_in=signed_in, current="plans", inside_dashboard=False))
        self.assertEqual(labels(registry.menu_groups(signed_in=True)), [["About", "How it works", "Begin", "Promises", "Plans"], ["Docs", "Journal", "Dashboard"], QUIET_GROUP])
        self.assertEqual(registry.SECTIONS, {"docs-page": "docs-index", "blog-post": "blog-index"}, "backend/pages/registry.py: the public SECTIONS are not the dashboard's")

    def test_each_section_marks_itself_as_the_page(self):
        for name, label in zip(SECTION_NAMES, SECTION_LABELS):
            with self.subTest(section=name):
                self.assertEqual(marked(inside(name)), [(label, "page")])

    def test_a_page_inside_a_section_marks_the_section_and_an_unlisted_one_marks_nothing(self):
        with mock.patch.dict(registry.DASHBOARD_SECTIONS, {"app-file-revoke": "app-files"}):
            self.assertEqual(marked(inside("app-file-revoke")), [("Files", "true")],
                             "backend/pages/registry.py: a confirmation page named in DASHBOARD_SECTIONS marks its section aria-current=true")
        self.assertEqual(marked(inside("app-not-listed")), [], "a dashboard name that DASHBOARD_SECTIONS does not list marks nothing")

    def test_the_map_names_every_dashboard_route_and_only_routes_that_exist(self):
        routed = {pattern.name for pattern in dashboard_urls.urlpatterns}
        self.assertEqual(routed - set(registry.DASHBOARD_SECTIONS), set(),
                         "backend/pages/registry.py: add each new dashboard URL name to DASHBOARD_SECTIONS, with the section it marks, or its page draws the ordinary menu")
        for name, section in registry.DASHBOARD_SECTIONS.items():
            with self.subTest(name=name):
                self.assertTrue(get_resolver().reverse_dict.getlist(name), f"backend/pages/registry.py: DASHBOARD_SECTIONS names {name}, which no route has")
                self.assertIn(section, SECTION_NAMES, f"{name} marks {section}, which is not one of the five sections")


class MenuSignOutTests(SimpleTestCase):
    longMessage = False
    FORM = ('<form method="post" action="/accounts/logout/"><input type="hidden" name="csrfmiddlewaretoken" value="tok-123">'
            '<button class="label interactive" type="submit">Sign out</button></form>')

    def menu(self, **extra):
        return render_to_string("components/menu.html", {"groups": inside(), "list_id": "m", **extra})

    def test_without_a_token_there_is_no_button_and_no_form(self):
        html = self.menu()
        self.assertNotIn("<form", html, "backend/templates/components/menu.html: the Sign out form is drawn only when sign_out is given")
        self.assertNotIn("Sign out", html)

    def test_with_a_token_group_two_ends_with_a_post_form_that_carries_it(self):
        html = self.menu(sign_out="tok-123")
        groups = re.findall(r'<ul class="menu__group">(.*?)</ul>', html, flags=re.S)
        self.assertEqual(len(groups), 3)
        self.assertEqual(re.findall(r"<li>(.*?)</li>", groups[1], flags=re.S)[-1], self.FORM,
                         "backend/templates/components/menu.html: group 2 ends with <li><form method=post action=/accounts/logout/> holding the token and a button.label.interactive")
        self.assertEqual(len(re.findall(r"<li>", groups[1])), 4, "Docs, Journal, Site and Sign out")
        self.assertEqual(html.count("<form"), 1)
        self.assertEqual(html.count("Sign out"), 1)

    def test_the_menu_stays_one_nav_with_no_other_nav_inside(self):
        html = self.menu(sign_out="tok-123")
        self.assertEqual(html.count("<nav"), 1)
        self.assertEqual(html.count("</nav>"), 1)

    def test_the_token_is_escaped(self):
        html = self.menu(sign_out='"><script>alert(1)</script>')
        self.assertNotIn("<script>", html, "backend/templates/components/menu.html: print sign_out escaped")

    def test_the_component_writes_no_csrf_tag_of_its_own_and_says_what_it_takes(self):
        text = MENU_COMPONENT.read_text(encoding="utf-8")
        self.assertNotIn("csrf_token", text, "backend/templates/components/menu.html: a component included with only sees no token; the page passes it in as sign_out")
        self.assertIn("sign_out", text.split("\n")[1], "backend/templates/components/menu.html: line 2 lists the inputs, sign_out included")
