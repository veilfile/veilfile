"""The menu inside the dashboard, drawn on real pages (spec 4.4): the five sections, the Sign out button that posts the page's token, and the pages that never get the button."""
import re

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import resolve

from dashboard.tests_app import SECTIONS, signed_in_client
from dashboard.tests_menu import QUIET_GROUP, SECTION_LABELS
from drop_core import throttle

User = get_user_model()

NAV = re.compile(r'<nav class="menu".*?</nav>', re.S)
ANCHOR = re.compile(r'<a class="label interactive" href="[^"]+"[^>]*>([^<]+)</a>')
TOKEN = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')


def nav_of(html):
    return NAV.search(html).group(0)


class DashboardMenuPageTests(TestCase):
    longMessage = False

    def setUp(self):
        self.client = signed_in_client()

    def test_every_section_draws_the_dashboards_menu(self):
        for name, address, _ in SECTIONS:
            with self.subTest(section=name):
                html = self.client.get(address).content.decode()
                nav = nav_of(html)
                self.assertEqual(ANCHOR.findall(nav), SECTION_LABELS + ["Docs", "Journal", "Site"] + QUIET_GROUP,
                                 f"backend/pages/templatetags/shell.py: shell_menu draws the dashboard's groups on {address} (inside_dashboard)")
                self.assertEqual(html.count('<nav class="menu"'), 1, "the menu is one nav, with no other nav inside it")
                self.assertEqual(nav.count("<form"), 1)
                self.assertEqual(nav.count(">Sign out</button>"), 1)

    def test_each_section_marks_itself_current_and_only_itself(self):
        for name, address, label in SECTIONS:
            with self.subTest(section=name):
                nav = nav_of(self.client.get(address).content.decode())
                self.assertEqual(re.findall(r'aria-current="([^"]*)">([^<]*)</a>', nav), [("page", label)],
                                 f"backend/pages/registry.py: {address} marks {label} aria-current=page and nothing else")

    def test_the_sign_out_button_posts_the_pages_token_to_the_logout_address(self):
        nav = nav_of(self.client.get("/app/files/").content.decode())
        self.assertRegex(nav, r'<form method="post" action="/accounts/logout/"><input type="hidden" name="csrfmiddlewaretoken" value="[^"]{20,}"><button class="label interactive" type="submit">Sign out</button></form>')

    def test_sign_out_works_from_every_section_and_lands_on_sign_in(self):
        for name, address, _ in SECTIONS:
            with self.subTest(section=name):
                client = signed_in_client(f"leaver-{name}", enforce_csrf_checks=True)
                token = TOKEN.search(nav_of(client.get(address).content.decode())).group(1)
                response = client.post("/accounts/logout/", {"csrfmiddlewaretoken": token})
                self.assertEqual((response.status_code, response["Location"]), (302, "/accounts/login/"),
                                 f"backend/templates/components/menu.html: the Sign out form on {address} posts to /accounts/logout/ with the page's token")
                self.assertEqual(client.get(address)["Location"], f"/accounts/login/?next={address}", "the session is over")

    def test_a_sign_out_without_the_token_is_the_form_not_sent_page_and_the_session_stays(self):
        client = signed_in_client("careless", enforce_csrf_checks=True)
        with self.assertLogs("django.security.csrf", level="WARNING"):
            response = client.post("/accounts/logout/")
        self.assertEqual(response.status_code, 403)
        self.assertIn("Form not sent.", response.content.decode())
        self.assertEqual(client.get("/app/").status_code, 200)

    def test_a_signed_out_visitor_is_never_given_the_dashboards_menu(self):
        request = RequestFactory().get("/app/")
        request.user = type("Nobody", (), {"is_authenticated": False})()
        request.resolver_match = resolve("/app/")
        html = render_to_string("shell.html", {}, request=request)
        self.assertIn(">Sign in</a>", html)
        self.assertNotIn("Sign out", html)
        self.assertNotIn("csrfmiddlewaretoken", html)


class OtherPagesKeepTheOrdinaryMenuTests(TestCase):
    longMessage = False

    def test_a_signed_in_visitor_on_a_public_page_has_no_button_and_no_token(self):
        client = signed_in_client()
        for address in ("/about/", "/plans/", "/docs/", "/blog/", "/terms/", "/accounts/verify-email/"):
            with self.subTest(address=address):
                response = client.get(address)
                self.assertEqual(response.status_code, 200)
                nav = nav_of(response.content.decode())
                self.assertIn(">Dashboard</a>", nav)
                self.assertNotIn("<form", nav, f"backend/pages/templatetags/shell.py: {address} is not a dashboard address, so its menu has no Sign out form")
                self.assertNotIn("Sign out", nav)

    def test_a_public_page_sets_no_cookie_and_carries_no_token_for_a_signed_in_visitor(self):
        client = signed_in_client()
        for address in ("/about/", "/plans/", "/"):
            with self.subTest(address=address):
                response = client.get(address)
                self.assertNotIn("csrftoken", response.cookies, f"backend/templates/shell.html: {address} asks for no CSRF token, so it sets no CSRF cookie")
                self.assertNotIn("csrfmiddlewaretoken", response.content.decode())

    def test_the_staff_catalog_keeps_the_ordinary_menu_in_the_corner(self):
        staff = Client()
        staff.force_login(User.objects.create_user("cataloguer", "cataloguer@example.com", "a-test-only-password", is_staff=True))
        nav = nav_of(staff.get("/app/components/").content.decode())  # the first menu on the page is the shell's own
        self.assertIn(">Dashboard</a>", nav)
        self.assertNotIn("<form", nav, "backend/pages/registry.py: /app/components/ is not in DASHBOARD_SECTIONS, so the staff catalog keeps the ordinary menu")


@override_settings(DEBUG=False, ROOT_URLCONF="dashboard.tests_app")
class PagesThatCarryNothingOfAPersonTests(TestCase):
    """The error pages and the throttled page are the same for everyone and carry no token, even when they answer a dashboard address."""
    longMessage = False

    def assertOrdinary(self, html, why):
        nav = nav_of(html)
        self.assertIn(">Dashboard</a>", nav, why)
        self.assertNotIn("<form", nav, why)
        self.assertNotIn("Sign out", html, why)
        self.assertNotIn("csrfmiddlewaretoken", html, why)
        self.assertNotIn("data-layout", html, why)

    def test_a_404_and_a_403_at_a_dashboard_address_draw_the_ordinary_menu(self):
        client = signed_in_client()
        for address in ("/app/files/gone/", "/app/keys/forbidden/"):
            with self.subTest(address=address):
                self.assertOrdinary(client.get(address).content.decode(),
                                    "backend/pages/errors.py: shell_error_page() passes plain_menu, so an error page at a dashboard address has no Sign out form and no token")

    def test_a_failed_form_check_at_a_dashboard_address_draws_the_ordinary_menu(self):
        client = signed_in_client("unchecked", enforce_csrf_checks=True)
        with self.assertLogs("django.security.csrf", level="WARNING"):
            response = client.post("/app/files/")
        self.assertEqual(response.status_code, 403)
        self.assertOrdinary(response.content.decode(), "backend/pages/errors.py: csrf_failure() is drawn by shell_error_page, which passes plain_menu")

    def test_the_throttled_page_at_a_dashboard_address_draws_the_ordinary_menu(self):
        request = RequestFactory().get("/app/keys/")
        request.user = User.objects.create_user("throttled", "throttled@example.com", "a-test-only-password")
        request.resolver_match = resolve("/app/keys/")
        response = throttle.throttled_response(request, "Too many tries", "Slow down.", 900)
        self.assertEqual(response.status_code, 429)
        self.assertOrdinary(response.content.decode(),
                            "backend/drop_core/throttle.py: throttled_response() passes shell_page=None and plain_menu, so a throttled page is the same bytes whoever asks and wherever")

    def test_a_bare_page_with_no_request_never_gets_the_button(self):
        self.assertNotIn("Sign out", render_to_string("404.html", {"gone_link": True}), "pages/errors.py: artifact_miss() draws with no request, so no session and no token")


class CatalogMenuTests(TestCase):
    longMessage = False

    def setUp(self):
        staff = User.objects.create_user("catalogue", "catalogue@example.com", "a-test-only-password", is_staff=True)
        self.client.force_login(staff)
        self.html = self.client.get("/app/components/").content.decode()

    def test_the_menu_is_shown_inside_the_dashboard_as_well_in_every_frame(self):
        self.assertEqual(self.html.count(">Sign out</button>"), 4, "backend/pages/catalog.py: the menu's Entry has a state with sign_out and the dashboard's groups")
        for frame in range(4):
            self.assertIn(f'id="catalog-menu-dashboard-{frame}"', self.html)
        self.assertEqual(self.html.count('value="catalog-not-a-token"'), 4)

    def test_the_catalog_draws_the_sample_with_a_made_up_value_and_not_the_staff_users_token(self):
        response = self.client.get("/app/components/")
        self.assertNotIn("csrftoken", response.cookies, "backend/pages/catalog.py: the sample's sign_out is a literal; the catalog is drawn without a request and asks for no token")
