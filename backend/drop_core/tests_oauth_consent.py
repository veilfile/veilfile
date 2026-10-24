"""OAuth consent on the form layout (spec 4.7): the page names the app that is asking, frames it as unverified, says what
the mcp:use scope lets it do, and has two forms, Authorize and Deny, that post the fields django-oauth-toolkit expects.
A request DOT refuses, and the verified-email gate, show the error page and never a consent form.

Every class sets longMessage = False. A failed assertIn on a whole page would otherwise print the page before the message that
names the file and the fix; with it off only the message prints, so each message here also says what was found."""
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.test import Client, SimpleTestCase
from django.urls import reverse
from oauth2_provider.models import get_access_token_model, get_grant_model

from drop_core import tests_oauth
from drop_core.models import EmailVerification
from pages.tests_chapters import BANNED
from pages.tests_site_rules import shell_templates

BACKEND = Path(settings.BASE_DIR)
PAGE = BACKEND / "templates" / "oauth" / "authorize.html"
PARTIAL = BACKEND / "templates" / "oauth" / "includes" / "grant_summary_oauth.html"
STYLES = BACKEND / "static" / "css" / "pages" / "consent.css"
TITLE = "<title>Authorize app | Veilfile</title>"


class _Page(HTMLParser):
    """The parts of a page these tests read: its h1 elements, its forms with their inputs and buttons, every id, its links and metas."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.h1s, self.forms, self.ids, self.hrefs, self.metas = [], [], [], [], []
        self.in_main, self._h1, self._form, self._button = False, None, None, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "main":
            self.in_main = True
        elif tag == "h1":
            self._h1 = {"text": "", "in_main": self.in_main}
            self.h1s.append(self._h1)
        elif tag == "form":
            self._form = {"attrs": attrs, "inputs": [], "buttons": []}
            self.forms.append(self._form)
        elif tag == "input" and self._form is not None:
            self._form["inputs"].append(attrs)
        elif tag == "button" and self._form is not None:
            self._button = {"attrs": attrs, "text": ""}
            self._form["buttons"].append(self._button)
        elif tag == "a" and "href" in attrs:
            self.hrefs.append(attrs["href"])
        elif tag == "meta":
            self.metas.append(attrs)

    def handle_endtag(self, tag):
        if tag == "main":
            self.in_main = False
        elif tag == "h1":
            self._h1 = None
        elif tag == "form":
            self._form = None
        elif tag == "button":
            self._button = None

    def handle_data(self, data):
        if self._h1 is not None:
            self._h1["text"] += data
        if self._button is not None:
            self._button["text"] += data


def read(response):
    found = _Page()
    found.feed(response.content.decode())
    found.close()
    return found


def form_with(found, label):
    """The form whose one button says `label`, and that button."""
    for form in found.forms:
        for button in form["buttons"]:
            if button["text"].strip() == label:
                return form, button
    raise AssertionError(f"backend/templates/oauth/authorize.html: no form has a button that says {label}; "
                         f"the page has {[b['text'].strip() for f in found.forms for b in f['buttons']]}")


def submission(form, button):
    """What a browser posts for this form: every named input, then the pressed button's name and value."""
    data = {field["name"]: field.get("value", "") for field in form["inputs"] if "name" in field}
    if "name" in button["attrs"]:
        data[button["attrs"]["name"]] = button["attrs"].get("value", "")
    return data


class ConsentTestCase(tests_oauth.OAuthBaseTestCase):
    longMessage = False
    NAME = "Some MCP app"

    def setUp(self):
        super().setUp()
        self.client_id = self._register_client(self.NAME).json()["client_id"]
        self.verifier, self.challenge = tests_oauth._pkce_pair()
        self.params = tests_oauth._authorize_params(self.client_id, self.challenge)

    def consent(self, client=None, params=None):
        """GET the consent page as the verified user."""
        client = client or self.client
        client.force_login(self.user)
        return client.get(tests_oauth._AUTHORIZE_URL, params or self.params)

    def no_grants(self, why):
        self.assertEqual(get_grant_model().objects.count(), 0, why)
        # The registration access token DOT makes for each registered client has no user; a token for the user would be a grant's.
        self.assertEqual(get_access_token_model().objects.filter(user__isnull=False).count(), 0, why)


class ConsentPageTests(ConsentTestCase):
    def test_the_page_is_a_form_page_with_one_h1_inside_main(self):
        response = self.consent()
        self.assertEqual(response.status_code, 200, f"the consent page answers 200 for a verified user and a valid request, not {response.status_code}")
        html = response.content.decode()
        self.assertRegex(html, r'<body class="[^"]*\blayout-form\b',
                         "backend/templates/oauth/authorize.html: extend layouts/form.html, the narrow form column (body class layout-form)")
        found = read(response)
        self.assertEqual([(h1["text"].strip(), h1["in_main"]) for h1 in found.h1s], [("Authorize this app", True)],
                         f"backend/templates/oauth/authorize.html: one h1, \"Authorize this app\", inside <main>; found {found.h1s}")
        self.assertIn(TITLE, html, "backend/templates/oauth/authorize.html: the title is Authorize app | Veilfile, as every page of the shell names itself")

    def test_the_page_is_not_for_search_engines(self):
        found = read(self.consent())
        robots = [meta for meta in found.metas if meta.get("name") == "robots"]
        self.assertEqual([meta.get("content") for meta in robots], ["noindex"],
                         f"backend/templates/oauth/authorize.html: one <meta name=\"robots\" content=\"noindex\"> (it holds a request's state); "
                         f"found {robots}. If layouts/form.html writes one already, drop it from the page")

    def test_the_app_name_is_escaped_and_shown_once(self):
        hostile = "<b>Evil</b> & Co <script>alert(1)</script>"
        client_id = self._register_client(hostile).json()["client_id"]
        html = self.consent(params={**self.params, "client_id": client_id}).content.decode()
        fix = "backend/templates/oauth/authorize.html: print {{ application.name }} auto-escaped; the name is the app's own claim"
        self.assertIn("&lt;b&gt;Evil&lt;/b&gt; &amp; Co &lt;script&gt;alert(1)&lt;/script&gt;", html, fix)
        self.assertNotIn("<b>Evil</b>", html, fix)
        self.assertNotIn("<script>alert(1)", html, fix)
        self.assertEqual(html.count("Evil"), 1, "the name is shown once, framed as unverified, and nowhere else (not in the title or the heading)")

    def test_an_app_with_no_name_is_called_unnamed(self):
        body = {"redirect_uris": [tests_oauth._REDIRECT_URI], "grant_types": ["authorization_code"], "token_endpoint_auth_method": "none"}
        client_id = self.client.post(tests_oauth._DCR_URL, data=json.dumps(body), content_type="application/json").json()["client_id"]
        html = self.consent(params={**self.params, "client_id": client_id}).content.decode()
        self.assertIn('<p class="consent__name">Unnamed app</p>', html,
                      "backend/templates/oauth/authorize.html: an app registered with no name is shown as Unnamed app, as the dashboard shows it")

    def test_a_name_of_the_longest_length_is_shown_whole(self):
        # DCR accepts a client_name of up to 255 characters; one with no space in it must wrap on a phone (consent.css), not be cut.
        name = "W" * 255
        client_id = self._register_client(name).json()["client_id"]
        html = self.consent(params={**self.params, "client_id": client_id}).content.decode()
        self.assertIn(f'<p class="consent__name">{name}</p>', html, "the whole name is printed; backend/static/css/pages/consent.css makes it wrap")

    def test_the_app_is_framed_as_unverified(self):
        html = self.consent().content.decode()
        for sentence in ("An unverified third-party app is asking for access to your workspace.",
                         "<strong>Unverified app:</strong> this name is what the app claims to be, not something we checked."):
            self.assertIn(sentence, html, f"backend/templates/oauth/authorize.html: keep the sentence {sentence!r} (AGENTS.md invariant on consent phishing; docs/API.md)")
        self.assertLess(html.index(self.NAME), html.index("Unverified app:"),
                        "backend/templates/oauth/authorize.html: the framing comes after the name it frames")

    def test_the_page_says_where_the_user_goes_next_and_only_the_host(self):
        fix = "backend/templates/oauth/authorize.html: keep the redirect-host sentence, with redirect_host (host and port) in <code>"
        self.assertIn("It will send you back to <code>127.0.0.1:9876</code> after you decide.", self.consent().content.decode(), fix)
        other = "https://app.example.com:8443/oauth/callback"
        client_id = self._register_client("Hosted app", redirect_uri=other).json()["client_id"]
        html = self.consent(params={**self.params, "client_id": client_id, "redirect_uri": other}).content.decode()
        self.assertIn("It will send you back to <code>app.example.com:8443</code> after you decide.", html, fix)
        shown = re.sub(r"<input[^>]*>", "", html)  # the hidden redirect_uri field holds the whole address; nothing a reader sees does
        self.assertNotIn("/oauth/callback", shown, "only the host is shown, never the path: the host is the anchor, the path is the app's")

    def test_the_page_says_in_plain_words_what_mcp_use_lets_the_app_do(self):
        response = self.consent()
        html = response.content.decode()
        for words in ("Approving lets the app act on your workspace. It can:",
                      "Upload files and create share links, list your artifacts, and revoke them",
                      "See your plan, usage, and account email address",
                      "It cannot create, list, or revoke API keys, disconnect your other apps, or touch billing or your account settings.",
                      "Access lasts until you disconnect it",
                      "<strong>Connected apps</strong> on your dashboard"):
            self.assertIn(words, html, f"backend/templates/oauth/includes/grant_summary_oauth.html: keep {words!r}; it is how the page says what mcp:use lets an app do")
        self.assertEqual(response.context["scopes"], ["mcp:use"], "the words describe the one scope DOT asks for (backend/config/settings.py, OAUTH2_PROVIDER SCOPES)")
        authorize, _ = form_with(read(response), "Authorize")
        self.assertEqual({f["name"]: f.get("value") for f in authorize["inputs"]}["scope"], "mcp:use", "the form posts the scope the words describe")

    def test_the_stylesheet_is_linked_and_the_page_leaks_no_template_comment(self):
        html = self.consent().content.decode()
        self.assertIn('<link rel="stylesheet" href="/static/css/pages/consent.css">', html,
                      "backend/templates/oauth/authorize.html: link css/pages/consent.css in the styles block")
        self.assertNotIn("{#", html, "a template comment spans lines and shows as text: keep each {# #} on one line")
        self.assertNotIn("#}", html, "a template comment spans lines and shows as text: keep each {# #} on one line")

    def test_the_response_cannot_be_framed(self):
        # The only clickjacking protection a consent page has (XFrameOptionsMiddleware, settings.MIDDLEWARE).
        self.assertEqual(self.consent()["X-Frame-Options"], "DENY",
                         "backend/config/settings.py: keep XFrameOptionsMiddleware and X_FRAME_OPTIONS at DENY; nothing may exempt the consent page")


class ConsentFormsTests(ConsentTestCase):
    def values(self):
        return {"redirect_uri": tests_oauth._REDIRECT_URI, "scope": "mcp:use", "nonce": "", "client_id": self.client_id,
                "state": "test-state-123", "response_type": "code", "code_challenge": self.challenge,
                "code_challenge_method": "S256", "claims": "", "resource": ""}

    def post_path(self, response):
        return response.wsgi_request.get_full_path()

    def test_two_post_forms_with_no_action_carry_every_field_dot_expects(self):
        response = self.consent()
        found = read(response)
        self.assertEqual(len(found.forms), 2, f"backend/templates/oauth/authorize.html: two forms, Authorize and Deny; found {len(found.forms)}")
        for form in found.forms:
            self.assertEqual(form["attrs"].get("method"), "post", "backend/templates/oauth/authorize.html: both forms are method=\"post\"")
            self.assertNotIn("action", form["attrs"],
                             "backend/templates/oauth/authorize.html: no action attribute: the POST returns to this address with its query string")
            posted = {field["name"]: field.get("value", "") for field in form["inputs"]}
            token = posted.pop("csrfmiddlewaretoken", "")
            self.assertTrue(token, "backend/templates/oauth/authorize.html: each form needs {% csrf_token %} (not inside a component)")
            self.assertEqual(posted, self.values(), "backend/templates/oauth/authorize.html: each form carries the hidden fields of DOT's AllowForm, with the request's values")
        repeated = sorted({name for name in found.ids if found.ids.count(name) > 1})
        self.assertEqual(repeated, [], f"backend/templates/oauth/authorize.html: these ids are used twice: {repeated}; render the hidden fields without ids, as the page does")
        self.assertIn("csrftoken", response.cookies, "the page must set the csrftoken cookie: backend/scripts/oauth_mcp_proof.py posts the cookie's value")

    def test_the_two_answers_are_equal_actions_in_one_row(self):
        response = self.consent()
        html = response.content.decode()
        found = read(response)
        fix = ("backend/templates/oauth/authorize.html: Deny and Authorize are two equal actions: each form holds one "
               '<button class="action interactive" type="submit">, and the two forms sit in <div class="form-actions"> (layouts/form.html, base.css)')
        row = re.search(r'<div class="form-actions">\s*(<form method="post">.*?</form>)\s*(<form method="post">.*?</form>)\s*</div>', html, re.S)
        self.assertIsNotNone(row, f"{fix}; found no such row")
        self.assertEqual(["Deny" in row.group(1), "Authorize" in row.group(2)], [True, True], f"{fix}; Deny comes first, as it always has, then Authorize")
        for label in ("Deny", "Authorize"):
            _, button = form_with(found, label)
            self.assertEqual(button["attrs"].get("type"), "submit", f"{fix}; the {label} button is type=\"submit\"")
            self.assertEqual(sorted(button["attrs"].get("class", "").split()), ["action", "interactive"],
                             f"{fix}; the {label} button has the classes action and interactive, which give it the hairline box and the ink wash")

    def test_only_authorize_posts_allow(self):
        found = read(self.consent())
        authorize, button = form_with(found, "Authorize")
        self.assertEqual((button["attrs"].get("type"), button["attrs"].get("name"), button["attrs"].get("value")), ("submit", "allow", "Authorize"),
                         "backend/templates/oauth/authorize.html: Authorize is <button type=\"submit\" name=\"allow\" value=\"Authorize\">: DOT reads allow")
        deny, answer = form_with(found, "Deny")
        names = [field.get("name") for field in deny["inputs"]] + [answer["attrs"].get("name")]
        self.assertNotIn("allow", names, "backend/templates/oauth/authorize.html: Deny must not post allow, or it would approve")
        self.assertEqual((len(authorize["buttons"]), len(deny["buttons"])), (1, 1), "backend/templates/oauth/authorize.html: one button in each form")

    def test_authorize_posts_what_the_page_holds_and_returns_a_code(self):
        client = Client(enforce_csrf_checks=True)
        response = self.consent(client=client)
        form, button = form_with(read(response), "Authorize")
        posted = client.post(self.post_path(response), submission(form, button))
        self.assertEqual(posted.status_code, 302, f"the Authorize form, posted as the page has it, must be accepted; it answered {posted.status_code}")
        location = urlparse(posted["Location"])
        query = parse_qs(location.query)
        self.assertEqual(f"{location.scheme}://{location.netloc}{location.path}", tests_oauth._REDIRECT_URI, "Authorize sends the user back to the app's redirect address")
        self.assertEqual(query.get("state"), ["test-state-123"], f"Authorize echoes the app's state; the redirect was {posted['Location']}")
        self.assertEqual(len(query.get("code", [])), 1, f"Authorize returns a code to the app; the redirect was {posted['Location']}")
        self.assertEqual(get_grant_model().objects.count(), 1, "Authorize creates the grant")

    def test_deny_posts_what_the_page_holds_and_returns_access_denied(self):
        client = Client(enforce_csrf_checks=True)
        response = self.consent(client=client)
        form, button = form_with(read(response), "Deny")
        posted = client.post(self.post_path(response), submission(form, button))
        self.assertEqual(posted.status_code, 302, f"the Deny form, posted as the page has it, must be accepted; it answered {posted.status_code}")
        query = parse_qs(urlparse(posted["Location"]).query)
        self.assertEqual(query.get("error"), ["access_denied"], f"Deny sends the user back with access_denied; the redirect was {posted['Location']}")
        self.assertEqual(query.get("state"), ["test-state-123"], f"Deny echoes the app's state; the redirect was {posted['Location']}")
        self.assertNotIn("code", query, "Deny returns no code")
        self.no_grants("Deny creates no grant and no token")

    def test_a_post_without_the_csrf_token_is_refused(self):
        client = Client(enforce_csrf_checks=True)
        response = self.consent(client=client)
        form, button = form_with(read(response), "Authorize")
        data = submission(form, button)
        del data["csrfmiddlewaretoken"]
        refused = client.post(self.post_path(response), data)
        self.assertEqual(refused.status_code, 403, "a consent POST with no CSRF token must be refused: an app could post it from another site")
        self.no_grants("a refused POST creates no grant")


class SignInReturnsToConsentTests(ConsentTestCase):
    def test_a_signed_out_visit_signs_in_and_comes_back_to_the_consent_page(self):
        client = Client()
        bounced = client.get(tests_oauth._AUTHORIZE_URL, self.params)
        self.assertEqual(bounced.status_code, 302, f"a signed-out visit is sent to sign in; it answered {bounced.status_code}")
        login = bounced["Location"]
        self.assertTrue(login.startswith("/accounts/login/?next=/o/authorize/"), f"a signed-out visit goes to sign in with ?next=; it went to {login}")
        self.assertEqual(client.get(login).status_code, 200, "the sign-in page opens with ?next= kept")
        # The sign-in form has no action, so it posts to this same address and ?next= rides along.
        signed_in = client.post(login, {"username": "oauthuser", "password": "pw12345678"})
        self.assertEqual(signed_in.status_code, 302, f"signing in redirects; it answered {signed_in.status_code}. Fix: backend/templates/registration/login.html posts to its own address")
        back = urlparse(signed_in["Location"])
        self.assertEqual(back.path, "/o/authorize/", f"sign in returns to ?next=; it went to {signed_in['Location']}")
        self.assertEqual(parse_qs(back.query), {key: [value] for key, value in self.params.items()},
                         "the request survives the round trip through sign in, field for field")
        html = client.get(signed_in["Location"]).content.decode()
        self.assertIn(self.NAME, html, "after sign in the consent page names the app")
        self.assertIn("Unverified app:", html, "after sign in the consent page frames the app as unverified")


class RefusedRequestTests(ConsentTestCase):
    def assert_error_page(self, response, status, description):
        found = read(response)
        html = response.content.decode()
        fix = "backend/drop_core/oauth.py: render_to_response shows device/device_error.html for a request DOT refuses"
        self.assertEqual(response.status_code, status, f"{fix}; expected {status}, got {response.status_code}")
        self.assertEqual([h1["text"].strip() for h1 in found.h1s], ["Invalid request"], f"{fix}; found headings {[h1['text'].strip() for h1 in found.h1s]}")
        self.assertIn(f"{description} Nothing was authorized. Go back to the app and start again.", html, f"{fix}, with the library's description of the error")
        self.assertEqual(found.forms, [], f"{fix}: an error page has no form, there is nothing to authorize")
        self.assertNotIn("csrfmiddlewaretoken", html, f"{fix}: no form, so no token")
        self.assertNotIn("nverified", html, f"{fix}: no consent wording on a page that has no app")
        self.assertEqual(response["X-Frame-Options"], "DENY", "backend/config/settings.py: keep XFrameOptionsMiddleware at DENY")
        self.no_grants("a refused request creates no grant")

    def test_an_unknown_client_gets_the_error_page(self):
        response = self.consent(params={**self.params, "client_id": "no-such-client"})
        self.assert_error_page(response, 400, "Invalid client_id parameter value.")

    def test_a_missing_client_gets_the_error_page(self):
        params = dict(self.params)
        del params["client_id"]
        self.assert_error_page(self.consent(params=params), 400, "Missing client_id parameter.")

    def test_a_redirect_uri_the_client_did_not_register_gets_the_error_page(self):
        response = self.consent(params={**self.params, "redirect_uri": "https://other.example.com/callback"})
        self.assert_error_page(response, 400, "Mismatching redirect URI.")
        self.assertNotIn("other.example.com", response.content.decode(), "backend/drop_core/oauth.py: the page never echoes the request's redirect_uri")

    def test_nothing_from_the_request_is_echoed(self):
        response = self.consent(params={**self.params, "client_id": '"><script>alert(1)</script>', "state": "<img src=x>"})
        self.assertEqual(response.status_code, 400, f"an unknown client answers 400, not {response.status_code}")
        html = response.content.decode()
        for needle in ("alert(1)", "<img", "test-state-123"):
            self.assertNotIn(needle, html, f"backend/drop_core/oauth.py: the error page shows only the library's fixed description, never {needle!r} from the request")

    def test_a_post_with_fields_missing_gets_the_error_page(self):
        self.client.force_login(self.user)
        response = self.client.post(tests_oauth._AUTHORIZE_URL, {"allow": "Authorize"})
        self.assert_error_page(response, 200, "This request is incomplete.")

    def test_an_error_the_app_can_be_told_about_still_goes_back_to_the_app(self):
        # DOT redirects these (not a fatal client error); render_to_response must not turn them into pages.
        response = self.consent(params={**self.params, "scope": "not-a-scope"})
        self.assertEqual(response.status_code, 302, f"backend/drop_core/oauth.py: an unknown scope still redirects to the app; it answered {response.status_code}")
        query = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(query.get("error"), ["invalid_scope"], f"the redirect names the error; it was {response['Location']}")
        self.assertEqual(query.get("state"), ["test-state-123"], f"the redirect echoes the app's state; it was {response['Location']}")


class UnverifiedEmailTests(ConsentTestCase):
    def test_an_unverified_user_gets_the_error_page_and_no_grant(self):
        EmailVerification.objects.filter(user=self.user).update(is_verified=False)
        response = self.consent()
        html = response.content.decode()
        fix = "backend/drop_core/oauth.py: the gate shows device/device_error.html (Task 4's page) with error_title as its h1"
        self.assertEqual(response.status_code, 403, f"{fix}; an unverified user gets 403, not {response.status_code}")
        found = read(response)
        self.assertEqual([h1["text"].strip() for h1 in found.h1s], ["Verify your email first"], f"{fix}; found {[h1['text'].strip() for h1 in found.h1s]}")
        self.assertIn("Authorizing an app requires a verified email address", html, f"{fix}, with error_message")
        self.assertIn(reverse("email-verification"), found.hrefs, f"{fix}, with show_resend: a link to the verification page")
        self.assertEqual(found.forms, [], f"{fix}: nothing on this page can authorize")
        self.assertNotIn("Unverified app", html, f"{fix}: no consent wording on a page that has no app")
        self.assertEqual(response["X-Frame-Options"], "DENY", "backend/config/settings.py: keep XFrameOptionsMiddleware at DENY")
        self.no_grants("an unverified user's visit creates no grant")


class ConsentTemplateTests(SimpleTestCase):
    longMessage = False

    def test_the_page_is_under_the_shell_rules(self):
        # Under shell_templates() the page gets the template rules (no inline style, components included with only, no header or
        # footer, every character in all three fonts) because it extends a layout.
        self.assertIn(PAGE, shell_templates(),
                      "backend/templates/oauth/authorize.html must extend layouts/form.html (or the shell), so that pages.tests_site_rules covers it")

    def test_every_form_has_its_own_csrf_token_and_the_page_has_no_script(self):
        text = PAGE.read_text(encoding="utf-8")
        self.assertEqual((text.count("<form"), text.count("{% csrf_token %}")), (2, 2),
                         "backend/templates/oauth/authorize.html: each of the two forms renders {% csrf_token %} itself")
        self.assertNotIn("<script", text.lower(), "backend/templates/oauth/authorize.html: the consent page needs no script")

    def test_the_words_keep_the_voice(self):
        for path in (PAGE, PARTIAL):
            text = path.read_text(encoding="utf-8")
            where = f"backend/{path.relative_to(BACKEND)}"
            with self.subTest(template=where):
                self.assertNotIn("—", text, f"{where}: no em dashes; use a colon, a comma or two sentences")
                self.assertNotIn("!", text, f"{where}: no exclamation marks")
                for word in BANNED:
                    self.assertNotIn(word, text.lower(), f"{where}: avoid {word.strip()!r} (AGENTS.md, voice)")

    def test_the_partial_is_plain_ascii(self):
        # The partial extends nothing, so the rule that holds every shell template to the fonts' characters does not read it.
        self.assertTrue(PARTIAL.read_text(encoding="utf-8").isascii(),
                        "backend/templates/oauth/includes/grant_summary_oauth.html: write plain ASCII; a character outside it may not be in all three fonts")

    def test_the_stylesheet_wraps_what_an_app_chooses(self):
        css = STYLES.read_text(encoding="utf-8")
        self.assertRegex(css, r"\.consent\s*\{[^}]*overflow-wrap:\s*anywhere",
                         "backend/static/css/pages/consent.css: `.consent` needs overflow-wrap: anywhere, or an app name or a host with no space "
                         "in it scrolls the page sideways on a 320px phone")

    def test_every_class_the_page_uses_has_a_rule(self):
        css = STYLES.read_text(encoding="utf-8")
        used = set()
        for path in (PAGE, PARTIAL):
            for value in re.findall(r'class="([^"]+)"', path.read_text(encoding="utf-8")):
                used.update(name for name in value.split() if name.startswith(("consent", "grant")))
        self.assertTrue(used, "backend/templates/oauth/authorize.html and its partial use no consent or grant class; their styles are in backend/static/css/pages/consent.css")
        for name in sorted(used):
            with self.subTest(css_class=name):
                self.assertIn(f".{name}", css, f"backend/static/css/pages/consent.css: add a rule for .{name}, which the consent page uses")
