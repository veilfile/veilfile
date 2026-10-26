"""The error pages (spec 4.9): 404, 403, a failed CSRF check and 500, what they never print, and what they do when they cannot be drawn."""
import re
from html import unescape
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import get_resolver, path

from config.urls import urlpatterns as site_urlpatterns
from pages import errors
from pages.tests_chapters import BANNED

BACKEND = Path(settings.BASE_DIR)
TEMPLATES = BACKEND / "templates"
ERROR_TEMPLATES = ("404.html", "403.html", "403_csrf.html", "500.html")
SECRET = "SECRET-DETAIL-4f9c"  # what a failure carries that no error page may print


def boom(request):
    raise RuntimeError(SECRET)


def denied(request):
    raise PermissionDenied(SECRET)


def missing(request):
    raise Http404(SECRET)


def registry_page_that_is_gone(request):
    raise Http404(SECRET)


# The site's own routes, and four that fail on purpose. ROOT_URLCONF names this module in the tests below, so the handlers
# are the site's: a test of config.urls (HandlersAreRegisteredTests) keeps these three lines equal to its own.
urlpatterns = [
    path("boom/", boom),
    path("denied/", denied),
    path("missing/", missing),
    # Named like a registry page, so the context processor would give the 404 that page's head.
    path("about-is-gone/", registry_page_that_is_gone, name="about"),
] + site_urlpatterns
handler403 = "pages.errors.permission_denied"
handler404 = "pages.errors.not_found"
handler500 = "pages.errors.server_error"


def main(html):
    """What a visitor reads: the page from <main> to </main>."""
    return html[html.index("<main"):html.index("</main>")]


def marked_links(html):
    """{label: aria-current} for every menu link that is marked as this page or its section."""
    found = re.findall(r'<a class="label interactive" href="[^"]*" aria-current="([^"]*)">([^<]*)</a>', html)
    return {label: mark for mark, label in found}


def templates_with_processors(processors):
    """settings.TEMPLATES with these context processors in place of the site's."""
    engine = dict(settings.TEMPLATES[0])
    engine["OPTIONS"] = {**engine["OPTIONS"], "context_processors": processors}
    return [engine]


def manifest_with_nothing_in_it():
    """STORAGES as production has them after a bad collectstatic: the manifest storage, and no entry for any file. With
    DEBUG off, {% static %} then raises ValueError on every page that uses it (the tests otherwise run on plain storage)."""
    return {**settings.STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"}}


def exploding_processor(request):
    raise RuntimeError(SECRET)


@override_settings(DEBUG=False, ROOT_URLCONF="pages.tests_errors")
class NotFoundPageTests(TestCase):
    longMessage = False  # a failure prints the message that names the file and the fix, not the page

    def test_an_unknown_address_is_a_404_on_the_shell(self):
        response = self.client.get("/no-such-page/")
        html = response.content.decode()
        self.assertEqual(response.status_code, 404, "backend/pages/errors.py: not_found() must answer with status 404")
        self.assertIn('<main id="main"', html, "backend/templates/404.html: it must extend a layout, which extends shell.html")
        self.assertIn('class="skip-link', html, "backend/templates/404.html: it must extend a layout, which extends shell.html")
        self.assertIn("data-menu", html, "backend/templates/404.html: it must extend a layout, which extends shell.html")
        self.assertEqual(len(re.findall(r"<h1[ >]", html)), 1, "backend/templates/404.html: one <h1>, inside <main>")
        self.assertIn("<h1", main(html), "backend/templates/404.html: the <h1> belongs inside <main>")
        self.assertIn("Not found", main(html))
        self.assertIn("<title>Not found | Veilfile</title>", html)
        self.assertIn('<meta name="robots" content="noindex">', html, "backend/templates/404.html: an error page is never indexed")

    def test_it_has_no_registry_head_and_no_in_place_changes(self):
        # about-is-gone is routed under the name "about", a registry page: its head (canonical, Open Graph, layout and the
        # family's paths) would claim the error is that page, and shell.js would change in place to and from it.
        response = self.client.get("/about-is-gone/")
        html = response.content.decode()
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("data-page-meta", html,
                         "backend/pages/errors.py: shell_error_page() must hand the template shell_page=None, so an error page has no head entry")
        self.assertNotIn("data-layout", html,
                         "backend/pages/errors.py: shell_error_page() must hand the template shell_page=None, so an error page has no layout family")
        self.assertIn("<title>Not found | Veilfile</title>", html)

    def test_it_prints_nothing_of_the_request_or_the_failure(self):
        for address in ("/missing/", f"/{SECRET}-path/", f"/missing/?q={SECRET}-query"):
            with self.subTest(address=address):
                response = self.client.get(address)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(SECRET, response.content.decode(),
                                 "backend/templates/404.html: never print the exception, the path or the query; "
                                 "a 404 must read the same whatever raised it")

    def test_a_missing_docs_page_still_marks_docs_in_the_menu(self):
        # The menu follows the URL's name (docs-page, blog-post), not the head entry, which an unknown slug does not have.
        for address, label in (("/docs/no-such-page/", "Docs"), ("/blog/no-such-post/", "Journal")):
            with self.subTest(address=address):
                response = self.client.get(address)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(marked_links(response.content.decode()), {label: "true"},
                                 f"backend/pages/errors.py: not_found() must draw the page with the request, so the menu marks {label} for {address}")

    def test_an_address_in_no_section_marks_nothing(self):
        self.assertEqual(marked_links(self.client.get("/no-such-page/").content.decode()), {})
        self.assertEqual(marked_links(self.client.get("/a/x/y").content.decode()), {})

    def test_the_menu_follows_who_is_signed_in(self):
        anonymous = self.client.get("/no-such-page/").content.decode()
        self.assertIn(">Sign in<", anonymous)
        self.assertNotIn(">Dashboard<", anonymous)
        self.client.force_login(self.reader())
        signed_in = self.client.get("/no-such-page/").content.decode()
        self.assertIn(">Dashboard<", signed_in, "backend/pages/errors.py: not_found() must draw the page with the request")
        self.assertNotIn(">Sign in<", signed_in)

    def test_a_post_gets_the_same_page(self):
        response = self.client.post("/no-such-page/", {"a": "b"})
        self.assertEqual(response.status_code, 404)
        self.assertIn("Not found", response.content.decode())

    def reader(self):
        return get_user_model().objects.create_user(username="reader", password="pw12345678")


@override_settings(DEBUG=False, ROOT_URLCONF="pages.tests_errors")
class PermissionDeniedPageTests(TestCase):
    longMessage = False

    def test_a_denied_view_gets_the_403_page_and_none_of_its_reason(self):
        response = self.client.get("/denied/")
        html = response.content.decode()
        self.assertEqual(response.status_code, 403, "backend/pages/errors.py: permission_denied() must answer with status 403")
        self.assertIn("<title>Not allowed | Veilfile</title>", html)
        self.assertIn("Not allowed", main(html))
        self.assertEqual(len(re.findall(r"<h1[ >]", html)), 1)
        self.assertIn('<meta name="robots" content="noindex">', html)
        self.assertNotIn(SECRET, html, "backend/templates/403.html: never print the exception")
        self.assertIn('href="/accounts/login/"', main(html), "backend/templates/403.html: the page says where to sign in")

    def test_it_has_no_registry_head(self):
        html = self.client.get("/denied/").content.decode()
        self.assertNotIn("data-page-meta", html)
        self.assertNotIn("data-layout", html)


@override_settings(DEBUG=False)
class CsrfFailurePageTests(TestCase):
    longMessage = False

    def post_without_a_token(self):
        client = Client(enforce_csrf_checks=True)
        with self.assertLogs("django.security.csrf", level="WARNING") as logs:
            response = client.post("/accounts/verify-email/SECRET_VERIFY_TOKEN/")
        return response, logs

    def test_a_failed_check_gets_the_form_not_sent_page(self):
        response, _logs = self.post_without_a_token()
        html = response.content.decode()
        self.assertEqual(response.status_code, 403, "backend/pages/errors.py: csrf_failure() must answer with status 403")
        self.assertIn("<title>Form not sent | Veilfile</title>", html)
        self.assertIn("Form not sent", main(html))
        self.assertEqual(len(re.findall(r"<h1[ >]", html)), 1)
        self.assertIn('<meta name="robots" content="noindex">', html)
        self.assertIn('<main id="main"', html, "backend/templates/403_csrf.html: it must extend a layout, which extends shell.html")

    def test_it_prints_neither_the_reason_nor_the_address(self):
        response, _logs = self.post_without_a_token()
        html = response.content.decode()
        for text in ("SECRET", "CSRF", "cookie not set", "token missing", "Origin"):
            with self.subTest(text=text):
                self.assertNotIn(text.lower(), main(html).lower(),
                                 "backend/templates/403_csrf.html: never print the reason Django gives or the address; "
                                 "it can quote the Origin header")

    def test_the_failure_is_still_logged_once_and_redacted(self):
        response, logs = self.post_without_a_token()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn("SECRET", logs.output[0])

    def test_the_site_names_the_view(self):
        self.assertEqual(settings.CSRF_FAILURE_VIEW, "pages.errors.csrf_failure",
                         "backend/config/settings.py: CSRF_FAILURE_VIEW must be pages.errors.csrf_failure")


@override_settings(DEBUG=False, ROOT_URLCONF="pages.tests_errors")
class ServerErrorPageTests(TestCase):
    longMessage = False

    def test_a_view_that_raises_gets_the_500_page_and_none_of_the_failure(self):
        client = Client(raise_request_exception=False)
        with self.assertLogs("django.request", level="ERROR"):
            response = client.get("/boom/")
        html = response.content.decode()
        self.assertEqual(response.status_code, 500)
        self.assertIn("<title>Something went wrong | Veilfile</title>", html)
        self.assertIn("<h1>Something went wrong</h1>", html)
        self.assertIn("support@veilfile.com", html)
        self.assertNotIn(SECRET, html, "backend/templates/500.html: never print the exception")

    def test_it_is_drawn_with_no_request_at_all(self):
        # Django's own server_error() is called with the request and draws with none; so does this one.
        response = errors.server_error(None)
        self.assertEqual(response.status_code, 500)
        self.assertIn("<h1>Something went wrong</h1>", response.content.decode())

    def test_it_is_drawn_when_the_static_manifest_is_missing(self):
        with override_settings(STORAGES=manifest_with_nothing_in_it()):
            # The same settings break the shell: this is what the test would otherwise not notice.
            with self.assertRaises(ValueError):
                render_to_string("shell.html")
            response = errors.server_error(RequestFactory().get("/"))
        self.assertEqual(response.status_code, 500)
        self.assertIn("<h1>Something went wrong</h1>", response.content.decode(),
                      "backend/templates/500.html: no {% static %} tag; it must draw when the static manifest is missing")

    def test_it_is_drawn_when_every_context_processor_raises(self):
        with override_settings(TEMPLATES=templates_with_processors(["pages.tests_errors.exploding_processor"])):
            with self.assertRaises(RuntimeError):
                render_to_string("shell.html", request=RequestFactory().get("/"))
            client = Client(raise_request_exception=False)
            with self.assertLogs("django.request", level="ERROR"):
                response = client.get("/boom/")
        self.assertEqual(response.status_code, 500)
        self.assertIn("<h1>Something went wrong</h1>", response.content.decode(),
                      "backend/pages/errors.py: server_error() must draw templates/500.html with no request, so no context processor runs")

    def test_its_own_words_can_be_replaced_and_are_escaped(self):
        html = render_to_string("500.html", {"title": "A <b>title</b>", "message": "Words & <i>more</i>."})
        self.assertIn("<h1>A &lt;b&gt;title&lt;/b&gt;</h1>", html)
        self.assertIn("Words &amp; &lt;i&gt;more&lt;/i&gt;.", html)

    def test_with_no_words_given_it_says_its_own(self):
        html = render_to_string("500.html")
        self.assertIn("<h1>Something went wrong</h1>", html)
        self.assertIn("<title>Something went wrong | Veilfile</title>", html)


@override_settings(DEBUG=False, ROOT_URLCONF="pages.tests_errors")
class AnErrorPageThatCannotBeDrawnTests(TestCase):
    longMessage = False

    def shell_that_raises(self):
        # The shell's menu is the first thing in <body> that reads the request; the site's real failures (a database that
        # is down under the session, a static file missing from the manifest) raise from the same render.
        return mock.patch("pages.registry.menu_groups", side_effect=RuntimeError(SECRET))

    def assert_bare(self, response, status, title):
        html = response.content.decode()
        self.assertEqual(response.status_code, status,
                         f"backend/pages/errors.py: shell_error_page() must keep the status ({status}) when the shell cannot be drawn, not turn it into a 500")
        self.assertIn(f"<h1>{title}</h1>", html, "backend/pages/errors.py: the fallback is templates/500.html with BARE_WORDS")
        self.assertNotIn('<main id="main"', html)
        self.assertNotIn(SECRET, html)

    def test_a_404_stays_a_404(self):
        with self.shell_that_raises(), self.assertLogs("pages.errors", level="ERROR") as logs:
            response = self.client.get("/no-such-page/")
        self.assert_bare(response, 404, "Not found")
        self.assertIn("404.html could not be drawn on the shell", logs.output[0])
        self.assertNotIn("no-such-page", logs.output[0], "backend/pages/errors.py: do not log the path; it can be a bearer token")

    def test_a_403_stays_a_403(self):
        with self.shell_that_raises(), self.assertLogs("pages.errors", level="ERROR"):
            response = self.client.get("/denied/")
        self.assert_bare(response, 403, "Not allowed")

    def test_a_failed_csrf_check_stays_a_403(self):
        client = Client(enforce_csrf_checks=True)
        with self.shell_that_raises(), self.assertLogs("pages.errors", level="ERROR"), self.assertLogs("django.security.csrf", level="WARNING"):
            response = client.post("/accounts/verify-email/SECRET_VERIFY_TOKEN/")
        self.assert_bare(response, 403, "Form not sent")

    def test_a_missing_static_manifest_does_not_turn_a_404_into_a_500(self):
        with override_settings(STORAGES=manifest_with_nothing_in_it()), self.assertLogs("pages.errors", level="ERROR"):
            response = self.client.get("/no-such-page/")
        self.assert_bare(response, 404, "Not found")

    def test_a_context_processor_that_raises_does_not_turn_a_404_into_a_500(self):
        with override_settings(TEMPLATES=templates_with_processors(["pages.tests_errors.exploding_processor"])):
            with self.assertLogs("pages.errors", level="ERROR"):
                response = self.client.get("/no-such-page/")
        self.assert_bare(response, 404, "Not found")

    def test_when_even_the_bare_page_cannot_be_drawn_one_line_of_text_keeps_the_status(self):
        with mock.patch("pages.errors.render", side_effect=RuntimeError(SECRET)), \
                mock.patch("pages.errors.render_to_string", side_effect=RuntimeError(SECRET)), \
                self.assertLogs("pages.errors", level="ERROR"):
            response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(response.content, b"Something went wrong.")

    def test_the_500_view_cannot_raise_either(self):
        with mock.patch("pages.errors.render_to_string", side_effect=RuntimeError(SECRET)), self.assertLogs("pages.errors", level="ERROR"):
            response = errors.server_error(None)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content, b"Something went wrong.")


class HandlersAreRegisteredTests(SimpleTestCase):
    longMessage = False

    def test_the_site_registers_all_three(self):
        resolver = get_resolver("config.urls")
        for status, name in ((403, "permission_denied"), (404, "not_found"), (500, "server_error")):
            with self.subTest(status=status):
                self.assertIs(resolver.resolve_error_handler(status), getattr(errors, name),
                              f"backend/config/urls.py: handler{status} must be \"pages.errors.{name}\"")

    def test_the_test_urlconf_uses_the_sites_handlers(self):
        # The routes above are tried with ROOT_URLCONF = this module: it must name the handlers config.urls does.
        import config.urls

        for status in (403, 404, 500):
            with self.subTest(status=status):
                self.assertEqual(globals()[f"handler{status}"], getattr(config.urls, f"handler{status}"),
                                 f"backend/pages/tests_errors.py: handler{status} must equal config.urls.handler{status}")


class ErrorTemplateRulesTests(SimpleTestCase):
    longMessage = False

    def source(self, name):
        """The template's text with its {# comments #} taken out: the code and the words, not the notes."""
        return re.sub(r"{#.*?#}", "", (TEMPLATES / name).read_text(encoding="utf-8"), flags=re.S)

    def test_every_error_template_exists(self):
        for name in ERROR_TEMPLATES:
            with self.subTest(template=name):
                self.assertTrue((TEMPLATES / name).is_file(), f"backend/templates/{name}: add it (pages/errors.py draws it)")

    def test_no_error_template_prints_the_request_or_the_failure(self):
        # Django hands 404.html `exception` and `request_path`, and 403.html `exception`; a CSRF failure's reason is not
        # passed at all. A page that printed any of them would tell two kinds of failure apart, and /a/<token> must not.
        names = re.compile(r"\b(?:exception|request_path|reason|request|csrf_token)\b|\.META\b|\bGET\b")
        for name in ERROR_TEMPLATES:
            with self.subTest(template=name):
                self.assertIsNone(names.search(self.source(name)),
                                  f"backend/templates/{name}: an error page prints nothing of the request or the failure "
                                  "(exception, request_path, reason, request, csrf_token); remove it")

    def test_the_voice(self):
        for name in ERROR_TEMPLATES:
            text = self.source(name).replace("<!DOCTYPE", "")
            with self.subTest(template=name):
                self.assertNotIn("—", text, f"backend/templates/{name}: no em dashes; use a colon, a comma or two sentences")
                self.assertNotIn("!", text, f"backend/templates/{name}: no exclamation marks")
                for word in BANNED:
                    self.assertNotIn(word, text.lower(), f"backend/templates/{name}: do not use \"{word}\"")

    def test_the_500_is_self_contained(self):
        text = self.source("500.html")
        self.assertTrue(text.isascii(), "backend/templates/500.html: ASCII only; it loads no font and must not depend on a charset header")
        for pattern, why in (
            (r"{%\s*(?:extends|include|load|static|url)\b", "no extends, include, load, static or url tag: they need the template engine's other files, a manifest or the URL conf"),
            (r"<link\b", "no <link>: every style is inline"),
            (r"<script\b[^>]*\bsrc\s*=", "no external script"),
            (r"@import|\burl\(", "no @import and no url(): nothing is fetched"),
            (r"<img\b", "no image"),
        ):
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text), f"backend/templates/500.html: {why}")
        self.assertIn("<style>", text, "backend/templates/500.html: the page's styles are inline in a <style> element")

    def test_the_500s_colours_are_the_tokens(self):
        # It cannot link tokens.css, so it repeats three of its values; a new palette must reach it.
        tokens = (BACKEND / "static" / "css" / "tokens.css").read_text(encoding="utf-8").lower()
        colours = set(re.findall(r"#[0-9a-f]{3,8}\b", self.source("500.html").lower()))
        self.assertTrue(colours, "backend/templates/500.html: it draws in Paper and Ink, so it holds their colours")
        stray = sorted(colour for colour in colours if colour not in tokens)
        self.assertEqual(stray, [], f"backend/templates/500.html: {', '.join(stray)} is not in backend/static/css/tokens.css; use the token's value")

    def test_the_fallback_says_what_the_templates_say(self):
        # pages/errors.py repeats the words of 404.html, 403.html for the moment the shell cannot be drawn. Each sentence
        # must appear word for word in the template's visible text: tags removed, spaces collapsed (a link is still words).
        def visible(html):
            return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html[html.index("<main"):html.index("</main>")])))

        gone = render_to_string("404.html", {"gone_link": True})
        found = render_to_string("404.html")
        denied = render_to_string("403.html")
        csrf = render_to_string("403_csrf.html")
        for words, html in ((errors.GONE_WORDS, gone), (errors.BARE_WORDS["404.html"], found),
                            (errors.BARE_WORDS["403.html"], denied), (errors.BARE_WORDS["403_csrf.html"], csrf)):
            for sentence in words:
                with self.subTest(sentence=sentence):
                    self.assertIn(sentence, visible(html), f"backend/pages/errors.py: \"{sentence}\" is not what the template says; change them together")
