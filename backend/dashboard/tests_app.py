"""The dashboard app (spec 4.8): its five addresses, who may open them, what every /app/ answer carries, and the shell's 404 for the rest."""
import re

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import path, resolve, reverse

import config.urls
from config.urls import urlpatterns as site_urlpatterns
from dashboard import urls as dashboard_urls
from dashboard.views import dashboard_view

User = get_user_model()
PASSWORD = "a-test-only-password"
# The five sections: URL name, address and the h1 each shows (the menu's word for it).
SECTIONS = (
    ("app", "/app/", "Overview"),
    ("app-files", "/app/files/", "Files"),
    ("app-keys", "/app/keys/", "Keys"),
    ("app-apps", "/app/apps/", "Connected apps"),
    ("app-billing", "/app/billing/", "Billing"),
)
NOINDEX = "noindex, nofollow"


def leave_a_message(request):
    """A stand-in for a view that has just done something: it leaves one line and goes to Files."""
    messages.success(request, request.GET.get("line", "File revoked."))
    return redirect("app-files")


def gone(request):
    raise Http404("SECRET-DETAIL")


def forbidden(request):
    raise PermissionDenied("SECRET-DETAIL")


# The site's routes and three more: ROOT_URLCONF names this module in the classes below. The two that fail are named like dashboard
# addresses, so the page that answers them is drawn for a request whose URL name is one of the dashboard's.
urlpatterns = [
    path("leave-a-message/", leave_a_message),
    path("app/files/gone/", gone, name="app-files"),
    path("app/keys/forbidden/", forbidden, name="app-keys"),
] + site_urlpatterns
handler403 = "pages.errors.permission_denied"
handler404 = "pages.errors.not_found"
handler500 = "pages.errors.server_error"


def signed_in_client(username="reader", **client_arguments):
    """A client signed in as a new user, who has no password (the session is made directly, so nothing is hashed)."""
    client = Client(**client_arguments)
    client.force_login(User.objects.create_user(username, f"{username}@example.com"))
    return client


def headings(html):
    return re.findall(r"<h1[^>]*>(.*?)</h1>", html, flags=re.S)


class RoutesTests(TestCase):
    longMessage = False

    def test_the_five_sections_have_their_names_and_addresses(self):
        for name, address, _ in SECTIONS:
            with self.subTest(name=name):
                self.assertEqual(reverse(name), address, f"backend/dashboard/urls.py: {name} is {address}")
                self.assertEqual(resolve(address).url_name, name)

    def test_the_react_catch_all_is_gone_and_the_catalog_stays_ahead_of_the_dashboard(self):
        self.assertFalse(hasattr(config.urls, "_AppTemplateView"),
                         "backend/config/urls.py: the React catch-all (_AppTemplateView and its re_path) is gone; the dashboard's own routes answer /app/")
        routes = [str(pattern.pattern) for pattern in config.urls.urlpatterns]
        self.assertIn("app/", routes, "backend/config/urls.py: path('app/', include('dashboard.urls')) mounts the dashboard")
        self.assertLess(routes.index("app/components/"), routes.index("app/"),
                        "backend/config/urls.py: app/components/ comes before the dashboard's include")

    def test_the_component_catalog_is_still_staff_only_at_its_own_address(self):
        self.assertEqual(resolve("/app/components/").url_name, "component-catalog")
        self.assertEqual(Client().get("/app/components/").status_code, 404)
        self.assertEqual(signed_in_client().get("/app/components/").status_code, 404)
        staff = Client()
        staff.force_login(User.objects.create_user("staff", "staff@example.com", PASSWORD, is_staff=True))
        self.assertEqual(staff.get("/app/components/").status_code, 200)

    def test_a_section_without_its_slash_is_sent_to_the_one_with(self):
        for _, address, _ in SECTIONS:
            with self.subTest(address=address):
                response = Client().get(address.rstrip("/"))
                self.assertEqual(response.status_code, 301, "settings.APPEND_SLASH sends /app and /app/files to the address with a slash")
                self.assertEqual(response["Location"], address)

    def test_any_other_address_under_app_is_the_shells_not_found(self):
        for who, client in (("signed in", signed_in_client()), ("signed out", Client())):
            for address in ("/app/x/", "/app/files/x/", "/app/components/x", "/app/keys/1/", "/app/billing/nothing/", "/app/files/not-a-uuid/revoke/"):
                with self.subTest(visitor=who, address=address):
                    response = client.get(address)
                    html = response.content.decode()
                    self.assertEqual(response.status_code, 404,
                                     "backend/dashboard/urls.py: only the dashboard's own addresses answer under /app/; the rest are the shell's 404")
                    self.assertIn("<title>Not found | Veilfile</title>", html)

    def test_every_dashboard_route_is_named_and_its_view_is_wrapped(self):
        for pattern in dashboard_urls.urlpatterns:
            with self.subTest(route=str(pattern.pattern)):
                self.assertTrue(pattern.name, "backend/dashboard/urls.py: name every route")
                self.assertTrue(getattr(pattern.callback, "__wrapped__", None), "backend/dashboard/views.py: wrap every view in @dashboard_view")


class SignedOutTests(TestCase):
    longMessage = False

    def test_every_section_sends_a_signed_out_visitor_to_sign_in_and_back(self):
        for name, address, _ in SECTIONS:
            with self.subTest(name=name):
                response = Client().get(address)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], f"/accounts/login/?next={address}",
                                 "backend/dashboard/views.py: dashboard_view must send a signed-out visitor to sign in with ?next=")

    def test_the_redirect_is_kept_out_of_search_too_and_sets_no_cookie(self):
        for _, address, _ in SECTIONS:
            with self.subTest(address=address):
                response = Client().get(address)
                self.assertEqual(response.headers.get("X-Robots-Tag"), NOINDEX,
                                 "backend/dashboard/views.py: dashboard_view sets X-Robots-Tag on every answer, the redirect to sign in included")
                self.assertEqual(response.content, b"")
                self.assertNotIn("csrftoken", response.cookies)

    def test_signing_in_returns_to_the_section(self):
        User.objects.create_user("returner", "returner@example.com", PASSWORD)
        response = Client().post("/accounts/login/?next=/app/files/", {"username": "returner", "password": PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/app/files/")


class SectionTests(TestCase):
    longMessage = False

    def setUp(self):
        self.client = signed_in_client()

    def test_each_section_is_a_page_with_its_own_h1(self):
        for name, address, h1 in SECTIONS:
            with self.subTest(name=name):
                response = self.client.get(address)
                html = response.content.decode()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(headings(html), [h1], f"backend/templates/dashboard: {address} has one <h1>, {h1}, the menu's word for the section")
                self.assertLess(html.index("<main"), html.index("<h1"), "the <h1> belongs inside <main>")
                self.assertIn("layouts/app.html", [template.name for template in response.templates],
                              f"backend/templates/dashboard: {address} extends layouts/app.html")

    def test_each_answer_is_kept_out_of_search_and_sets_the_csrf_cookie(self):
        for _, address, _ in SECTIONS:
            with self.subTest(address=address):
                response = self.client.get(address)
                self.assertEqual(response.headers.get("X-Robots-Tag"), NOINDEX, "backend/dashboard/views.py: dashboard_view sets X-Robots-Tag: noindex, nofollow on every answer")
                self.assertIn("csrftoken", response.cookies, "backend/dashboard/views.py: dashboard_view sets the CSRF cookie, so a form on any page can post")

    def test_a_section_answers_a_get_and_a_head_and_nothing_else(self):
        for name, address, _ in SECTIONS:
            if name == "app-keys":
                continue  # the key page takes a POST of its own
            with self.subTest(name=name):
                self.assertEqual(self.client.head(address).status_code, 200)
                response = self.client.post(address)
                self.assertEqual(response.status_code, 405, f"backend/dashboard/views.py: {address} is read-only")
                self.assertEqual(response.headers.get("X-Robots-Tag"), NOINDEX, "the 405 carries the header too")

    def test_the_decorator_wraps_login_the_cookie_and_the_header_around_any_view(self):
        @dashboard_view
        def plain(request):
            return HttpResponse("ok")

        request = RequestFactory().get("/app/x/")
        request.user = AnonymousUser()
        refused = plain(request)
        self.assertEqual((refused.status_code, refused.headers.get("X-Robots-Tag")), (302, NOINDEX))
        request.user = User.objects.create_user("direct", "direct@example.com", PASSWORD)
        allowed = plain(request)
        self.assertEqual((allowed.status_code, allowed.content, allowed.headers.get("X-Robots-Tag")), (200, b"ok", NOINDEX))
        self.assertIn("csrftoken", allowed.cookies, "backend/dashboard/views.py: dashboard_view wraps ensure_csrf_cookie, so the cookie is set whatever the page draws")
        self.assertEqual(plain.__name__, "plain", "backend/dashboard/views.py: dashboard_view uses functools.wraps")


@override_settings(ROOT_URLCONF="dashboard.tests_app")
class MessagesTests(TestCase):
    longMessage = False

    def test_a_line_left_by_a_view_is_a_status_notice_at_the_top_of_the_page_and_is_read_once(self):
        client = signed_in_client()
        html = client.get("/leave-a-message/", follow=True).content.decode()
        notice = '<p class="notice" role="status">File revoked.</p>'
        self.assertIn(notice, html, "backend/templates/layouts/app.html: every message is a .notice with role=status, at the top of the page")
        self.assertLess(html.index("<main"), html.index(notice))
        self.assertLess(html.index(notice), html.index("<h1"), "the notice comes before the heading")
        self.assertNotIn("File revoked.", client.get("/app/files/").content.decode(), "a message is shown once")

    def test_the_line_is_escaped(self):
        client = signed_in_client()
        html = client.get("/leave-a-message/", {"line": "<b>x</b> & y"}, follow=True).content.decode()
        self.assertIn('role="status">&lt;b&gt;x&lt;/b&gt; &amp; y</p>', html, "backend/templates/layouts/app.html: print a message escaped")
        self.assertNotIn("<b>x</b>", html)

    def test_whichever_dashboard_page_reads_it_first_shows_it(self):
        client = signed_in_client()
        client.get("/leave-a-message/")
        self.assertNotIn("File revoked.", client.get("/about/").content.decode(), "a public page does not draw messages")
        self.assertIn("File revoked.", client.get("/app/apps/").content.decode(), "backend/templates/layouts/app.html: every section reads the messages, not only the one a view redirects to")


@override_settings(DEBUG=False, ROOT_URLCONF="dashboard.tests_app")
class ErrorsUnderAppTests(TestCase):
    longMessage = False

    def test_the_shells_404_under_app_is_kept_out_of_search_by_header_too(self):
        for who, client in (("signed in", signed_in_client()), ("signed out", Client())):
            for address in ("/app/x/", "/app/files/gone/"):
                with self.subTest(visitor=who, address=address):
                    response = client.get(address)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.headers.get("X-Robots-Tag"), NOINDEX,
                                     "backend/pages/errors.py: shell_error_page() sets X-Robots-Tag on an error page drawn for an address under /app/")

    def test_a_404_elsewhere_gets_no_such_header(self):
        self.assertNotIn("X-Robots-Tag", Client().get("/no-such-page/"))

    def test_a_403_and_a_failed_form_check_under_app_carry_it_as_well(self):
        client = signed_in_client()
        denied = client.get("/app/keys/forbidden/")
        self.assertEqual((denied.status_code, denied.headers.get("X-Robots-Tag")), (403, NOINDEX))
        checked = signed_in_client("checked", enforce_csrf_checks=True)
        with self.assertLogs("django.security.csrf", level="WARNING"):
            response = checked.post("/app/files/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers.get("X-Robots-Tag"), NOINDEX)
        self.assertIn("Form not sent", response.content.decode())
