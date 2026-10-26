"""The device flow's pages on the shell (spec 4.7): the code entry, the approval, its two results and the refusal.

The five templates in backend/templates/device/ extend layouts/form.html. Their words are the voice pass's, so what these tests hold is what must not change with them: the form's fields and
addresses, the escaping of what a stranger's terminal sent, the confirmation the approval needs, the keys offered at the plan's cap, and the
two other flows that meet these pages (the CLI's link, and the OAuth consent's refusal, which renders device/device_error.html).

Every class sets longMessage = False, so that a failure prints its own message, which names the file and the fix, and not the
page it searched (tens of kilobytes of HTML). pages.tests_site_rules.READING_TEST_MODULES holds this module to it.
"""
import re
from datetime import timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from drop_core import tests_oauth
from drop_core.models import APIKey, DeviceCode, EmailVerification, get_or_create_workspace

User = get_user_model()

DEVICE_TEMPLATES = Path(settings.BASE_DIR) / "templates" / "device"
DEVICE_CSS = Path(settings.BASE_DIR) / "static" / "css" / "pages" / "device.css"
PAGE_TEMPLATES = {"device_enter_code.html", "device_consent.html", "device_success.html", "device_denied.html",
                  "device_error.html"}
DASH = "\N{EM DASH}"  # the voice rules ban it, in a stylesheet's comments too
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
# The <title> of each state a visitor can meet: the voice pass's words, in the shell's form (Name | Veilfile).
TITLES = {
    "enter": "Enter device code | Veilfile",
    "enter, wrong code": "Enter device code | Veilfile",
    "consent": "Approve terminal login | Veilfile",
    "consent, refused": "Approve terminal login | Veilfile",
    "invalid": "Invalid code | Veilfile",
    "expired": "This code has expired | Veilfile",
    "too many": "Too many attempts | Veilfile",
    "unverified": "Verify your email first | Veilfile",
    "success": "Terminal approved | Veilfile",
    "denied": "Terminal denied | Veilfile",
}
STATUSES = {"enter": 200, "enter, wrong code": 200, "consent": 200, "consent, refused": 400, "invalid": 400,
            "expired": 410, "too many": 429, "unverified": 403, "success": 200, "denied": 200}
CHECK_MARKS = ("\N{CHECK MARK}", "&#10003;", "&#x2713;", "&check;", "&checkmark;")


class Tags(HTMLParser):
    """The start tags of some HTML, in order, each as (name, attributes). A boolean attribute has the value None."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def find(self, tag, where=None):
        where = where or {}
        return [attrs for name, attrs in self.tags if name == tag and all(attrs.get(k) == v for k, v in where.items())]


def main_of(html):
    return html[html.index('<main id="main"'):html.index("</main>")]


def text_of(html):
    """What a reader reads in a piece of HTML: tags removed, entities decoded, white space made single spaces."""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def element_with_id(html, element_id):
    """The inner HTML of the element that carries this id, or None."""
    found = re.search(rf'<(\w+)\b[^>]*\bid="{re.escape(element_id)}"[^>]*>(.*?)</\1>', html, re.DOTALL)
    return found.group(2) if found else None


def buttons_of(html):
    """[(attributes, text)] for each button, in order."""
    return [(Tags(tag).find("button")[0], text_of(text))
            for tag, text in re.findall(r"(<button\b[^>]*>)(.*?)</button>", html, re.DOTALL)]


def check_rows_of(html):
    """The inner HTML of each <label class="check"> row, in order: Task 1's row of a checkbox or a radio and its words."""
    return re.findall(r'<label class="check">(.*?)</label>', html, re.DOTALL)


def raw_input_tag(html, name):
    """The input tag of this name as written, to count attributes that a parser would merge."""
    return next((tag for tag in re.findall(r"<input\b[^>]*>", html) if f'name="{name}"' in tag), "")


class DeviceCase(TestCase):
    longMessage = False

    def says(self, haystack, needle, where):
        self.assertIn(needle, haystack, f"{where} must still hold {needle!r}")

    def omits(self, haystack, needle, where):
        self.assertNotIn(needle, haystack, f"{where} must not hold {needle!r}")

    def setUp(self):
        cache.clear()  # the lookup limits are counted in the cache
        self.user = User.objects.create_user(username="dev", email="dev@example.com", password="pw12345678")
        self.workspace = get_or_create_workspace(self.user)
        EmailVerification.objects.create(user=self.user, email=self.user.email, is_verified=True,
                                         verified_at=timezone.now())
        self.terminal = APIClient()
        self.browser = APIClient()
        self.browser.force_login(self.user)

    def start(self, **extra):
        response = self.terminal.post("/api/v1/device/code", {}, format="json", **extra)
        self.assertEqual(response.status_code, 200, "POST /api/v1/device/code must answer 200 (backend/drop_core/device.py)")
        return response.json()

    def unverified_browser(self):
        other = User.objects.create_user(username="newbie", email="newbie@example.com", password="pw12345678")
        get_or_create_workspace(other)
        client = APIClient()
        client.force_login(other)
        return client

    def at_the_key_cap(self, name="prod-agent"):
        """The free plan allows one key: with it made, an approval must name a key to replace."""
        key, _raw = APIKey.create_key(self.workspace, name)
        return key

    def consent(self, **extra):
        grant = self.start(**extra)
        response = self.browser.get("/device", {"user_code": grant["user_code"]})
        self.assertEqual(response.status_code, 200, "GET /device?user_code=... for a live code answers 200")
        return grant, main_of(response.content.decode())

    def every_page(self):
        """label -> response, for each state of the flow a visitor can meet (see TITLES and STATUSES)."""
        grant = self.start()
        pages = {
            "enter": self.browser.get("/device"),
            "enter, wrong code": self.browser.get("/device", {"user_code": "NOPE-NOPE"}),
            "consent": self.browser.get("/device", {"user_code": grant["user_code"]}),
            "consent, refused": self.browser.post("/device/approve", {"user_code": grant["user_code"]}),
            "invalid": self.browser.post("/device/approve", {"user_code": "NOPE-NOPE"}),
        }
        expired = self.start()
        DeviceCode.objects.filter(user_code=expired["user_code"].replace("-", "")).update(
            expires_at=timezone.now() - timedelta(minutes=1))
        pages["expired"] = self.browser.get("/device", {"user_code": expired["user_code"]})
        pages["unverified"] = self.unverified_browser().post(
            "/device/approve", {"user_code": grant["user_code"], "confirm": "yes"})
        pages["success"] = self.browser.post("/device/approve", {"user_code": grant["user_code"], "confirm": "yes"})
        pages["denied"] = self.browser.post("/device/deny", {"user_code": self.start()["user_code"]})
        for _ in range(12):  # ten misses a window are allowed; the next lookup is refused
            response = self.browser.get("/device", {"user_code": "NOPE-NOPE"})
            if response.status_code == 429:
                break
        pages["too many"] = response
        return pages


class DevicePageFrameTests(DeviceCase):
    """What every page of the flow shares: the form layout, one h1, no index, its title and its buttons."""

    def test_every_state_answers_as_it_did(self):
        pages = self.every_page()
        self.assertEqual({label: response.status_code for label, response in pages.items()}, STATUSES,
                         "the device pages keep their status codes (backend/drop_core/device.py); only the templates moved")
        self.assertEqual(sorted(TITLES), sorted(STATUSES),
                         "backend/drop_core/tests_device_pages.py: TITLES and STATUSES must name the same states")

    def test_every_page_is_drawn_by_the_form_layout(self):
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                html = response.content.decode()
                self.assertIn("layouts/form.html", [template.name for template in response.templates],
                              f"{label}: its template in backend/templates/device/ must extend layouts/form.html")
                self.assertRegex(html, r'<body class="[^"]*\blayout-form\b',
                                 f"{label}: <body> needs the class layout-form (backend/templates/layouts/form.html)")
                self.omits(html, "data-layout", f"{label}: a form page has no layout family (it is not in "
                           "backend/pages/registry.py), so no in-place change; its page")
                self.says(html, '<a class="skip-link label" href="#main">', f"{label}: the page")
                main = main_of(html)
                self.assertEqual(len(re.findall(r"<h1[ >]", html)), 1, f"{label}: exactly one h1")
                self.assertEqual(len(re.findall(r"<h1[ >]", main)), 1, f"{label}: the h1 belongs inside <main>")
                for element in ("<header", "<footer"):
                    self.omits(main, element, f"{label}: <main> (use a div: backend/static/css/base.css styles the bare element)")

    def test_every_page_has_its_title(self):
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                self.says(response.content.decode(), f"<title>{TITLES[label]}</title>",
                          f"{label}: the title block of its template in backend/templates/device/ (the voice pass's words, "
                          "then ' | Veilfile' as on every page of the shell); the page")

    def test_every_page_is_kept_out_of_search(self):
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                self.says(response.content.decode(), '<meta name="robots" content="noindex">',
                          f"{label}: it holds a one-time code or state, so the meta block of its template in "
                          "backend/templates/device/ needs it; the page")

    def test_a_page_links_the_device_stylesheet_exactly_when_it_uses_a_class_of_it(self):
        # Only the approval and its success draw pieces the form layout lacks (the large code, who asked, what the key can
        # do, the mark). The entry, the denial and the refusal (which the OAuth consent shares) are the layout's and the
        # components' alone, so they load nothing more.
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                html = response.content.decode()
                uses = re.search(r'class="[^"]*\bdevice-[a-z_]+', html) is not None
                self.assertEqual(html.count("css/pages/device.css"), 1 if uses else 0,
                                 f"{label}: a page that uses a device-* class links css/pages/device.css once in its styles "
                                 "block ({{ block.super }} first), and a page that does not leaves it out: "
                                 "backend/templates/device/*.html")

    def test_only_the_code_entry_and_a_refusal_take_focus(self):
        # autofocus is for a page that exists to take one input: the entry, with no code typed and after a wrong one (the field is then the one in error); and for the sentence
        # of a page that has a refusal to say and no field in error, the approval sent back (its summary, components/form_errors.html). No other page has anything to focus.
        wanted = {"enter": 1, "enter, wrong code": 1, "consent, refused": 1}
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                count = response.content.decode().count("autofocus")
                self.assertEqual(count, wanted.get(label, 0),
                                 f"{label}: autofocus appears {count} times; only the entry ('enter', and 'enter, wrong code') sets it, in "
                                 "backend/templates/device/device_enter_code.html, and the refused approval's summary, in "
                                 "backend/templates/device/device_consent.html")

    def test_every_button_is_the_forms_main_action_with_its_ink_wash(self):
        # .interactive is what gives a control the ink wash on hover and press (backend/static/css/base.css).
        seen = 0
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                for attributes, text in buttons_of(main_of(response.content.decode())):
                    seen += 1
                    self.assertTrue({"action", "interactive"} <= set(attributes.get("class", "").split())
                                    and attributes.get("type") == "submit",
                                    f'{label}: the {text!r} button must be <button class="action interactive" type="submit"> '
                                    "(components of the form layout, backend/templates/device/*.html)")
        self.assertEqual(seen, 6, "backend/drop_core/tests_device_pages.py: the two entry states have the Continue button "
                         "and the two approval states have Approve and Deny; no other page has a button")

    def test_no_page_shows_a_template_comment(self):
        # Django 6.1 prints a {# comment #} that spans lines as text: each in the device templates stays on one line.
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                html = response.content.decode()
                for mark in ("{#", "#}"):
                    self.omits(html, mark, f"{label}: a template comment shows, so keep each {{# #}} on one line; the page")

    def test_no_page_has_a_check_mark_character(self):
        # U+2713 is in none of the site's three fonts, so it would be drawn in whatever font the system falls back to.
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                html = response.content.decode()
                for mark in CHECK_MARKS:
                    self.omits(html, mark, f"{label}: draw the mark in CSS (.device-mark in "
                               "backend/static/css/pages/device.css); the page")

    def test_nothing_in_main_carries_an_aria_label(self):
        # axe (aria-prohibited-attr): a div or p with no role cannot carry aria-label, which the code's div had.
        for label, response in self.every_page().items():
            with self.subTest(page=label):
                self.omits(main_of(response.content.decode()), "aria-label", f"{label}: <main> (a visible label, class "
                           "label, names things in backend/templates/device/*.html; a role-less element cannot be named)")


class DeviceTemplateSourceTests(SimpleTestCase):
    longMessage = False

    def sources(self):
        return {path.relative_to(DEVICE_TEMPLATES).as_posix(): path.read_text(encoding="utf-8")
                for path in sorted(DEVICE_TEMPLATES.rglob("*.html"))}

    def stylesheet(self):
        self.assertTrue(DEVICE_CSS.is_file(), "backend/static/css/pages/device.css is the device pages' stylesheet")
        return DEVICE_CSS.read_text(encoding="utf-8")

    def test_the_five_pages_and_the_summary_are_the_folders_files(self):
        self.assertEqual(set(self.sources()), PAGE_TEMPLATES | {"includes/grant_summary.html"},
                         "backend/templates/device/ holds the five pages and includes/grant_summary.html; "
                         "drop_core/device.py and drop_core/oauth.py render them by name")

    def test_each_page_extends_the_form_layout_and_nothing_old_is_left(self):
        for name, text in self.sources().items():
            with self.subTest(template=name):
                if name in PAGE_TEMPLATES:
                    self.assertTrue(text.startswith('{% extends "layouts/form.html" %}'),
                                    f'backend/templates/device/{name} must start with {{% extends "layouts/form.html" %}}')
                for old in ("includes/theme_boot", "includes/ambient", "includes/menu", "<!DOCTYPE", "<style", "<script"):
                    self.assertNotIn(old, text, f"backend/templates/device/{name} holds {old}, which belongs to the old "
                                     "page; the shell and the form layout give it everything")
                self.assertNotRegex(text, r"\sstyle\s*=", f"backend/templates/device/{name}: no inline style; "
                                    "put it in backend/static/css/pages/device.css")

    def test_no_template_holds_a_check_mark(self):
        for name, text in self.sources().items():
            with self.subTest(template=name):
                for mark in CHECK_MARKS:
                    self.assertNotIn(mark, text, f"backend/templates/device/{name} holds {mark!r}, which none of the "
                                     "site's fonts has; the success page draws its mark in CSS")

    def test_the_stylesheet_is_tokens_only_and_has_no_em_dash(self):
        text = self.stylesheet()
        self.assertNotRegex(text, r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch|lab|lch)\(",
                            "backend/static/css/pages/device.css: colours come only from tokens.css")
        self.assertNotIn(DASH, text, "backend/static/css/pages/device.css: no em dash, not even in a comment")

    def test_the_stylesheet_styles_only_what_the_forms_pieces_do_not(self):
        # The field, the checkbox and radio rows, the actions and the notice are the form layout's (base.css and
        # components/field.css): every form page has the same ones, so a rule for one here would make this page differ.
        rules = re.sub(r"/\*.*?\*/", "", self.stylesheet(), flags=re.DOTALL)
        found = re.findall(r"\.(?:field|check|form-actions|form-links|action|notice)(?![a-z0-9-])\S*|#id_user_code", rules)
        self.assertEqual(found, [], f"backend/static/css/pages/device.css styles {found}: change the piece in "
                         "backend/static/css/base.css or components/field.css instead, or pass the field's mono input")

    def test_what_a_stranger_or_a_user_wrote_wraps_so_a_phone_never_scrolls_sideways(self):
        # The client string and the account's address have no length we choose, and nor has a key's name. The client string
        # and the address are in a grid cell that may shrink (min-width: 0) and break anywhere; the code is nine characters
        # at a tracked size and breaks too. A key's name is in a check row, which the form layout lets wrap. The browser
        # checks look at 320px.
        text = self.stylesheet()
        for selector, shrinks in ((".device-request dd", True), (".device-code__value", False)):
            with self.subTest(selector=selector):
                block = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", text)
                self.assertIsNotNone(block, f"backend/static/css/pages/device.css needs a rule for {selector}")
                self.assertIn("overflow-wrap: anywhere", block.group(1),
                              f"backend/static/css/pages/device.css: {selector} needs overflow-wrap: anywhere")
                if shrinks:
                    self.assertIn("min-width: 0", block.group(1), f"backend/static/css/pages/device.css: {selector} is a "
                                  "grid child, so it needs min-width: 0 to be allowed to shrink and wrap")


class DeviceEnterCodeTests(DeviceCase):
    def entry(self, **query):
        return main_of(self.browser.get("/device", query).content.decode())

    def test_the_form_is_the_get_that_the_clis_link_is(self):
        main = self.entry()
        tags = Tags(main)
        forms = tags.find("form")
        self.assertEqual(len(forms), 1, "the entry page has one form (backend/templates/device/device_enter_code.html)")
        self.assertEqual((forms[0].get("method"), forms[0].get("action")), ("get", "/device"),
                         "the entry form is a GET to /device, so a typed code reaches /device?user_code=..., the "
                         "address the CLI opens; changing it breaks that link")
        fields = tags.find("input", {"name": "user_code"})
        self.assertEqual(len(fields), 1, "one input named user_code")
        field = fields[0]
        self.assertEqual({key: field.get(key) for key in ("id", "type", "autocomplete", "spellcheck", "placeholder")}, {
            "id": "id_user_code", "type": "text", "autocomplete": "off", "spellcheck": "false",
            "placeholder": "XXXX-XXXX"}, "the code input keeps its id, type, autocomplete, spellcheck and placeholder")
        self.assertIn("required", field, "the code input is required")
        self.assertIn("field__input--mono", field.get("class", "").split(),
                      "the code input is the field component's mono variant: pass mono=True to components/field.html "
                      "in backend/templates/device/device_enter_code.html (no rule of this page's own)")
        label = re.search(r'<label\b[^>]*\bfor="id_user_code"[^>]*>(.*?)</label>', main, re.DOTALL)
        self.assertIsNotNone(label, 'the code input needs a <label for="id_user_code"> (components/field.html)')
        self.assertEqual(text_of(label.group(1)), "Device code", "the label's visible words are 'Device code'")
        [(button, text)] = buttons_of(main)
        self.assertEqual((button.get("type"), text), ("submit", "Continue"))

    def test_a_typed_code_reaches_the_consent_page_in_any_spelling_the_form_can_send(self):
        grant = self.start()
        raw = grant["user_code"].replace("-", "")
        for typed in (grant["user_code"], raw, raw.lower(), f"{raw[:4].lower()} {raw[4:].lower()}"):
            with self.subTest(typed=typed):
                main = self.entry(user_code=typed)
                self.says(main, "Approve terminal login", "a code typed in the entry form finds its grant "
                          "(_normalize_user_code in backend/drop_core/device.py); the page")

    def test_with_no_code_the_field_is_focused_and_the_intro_is_read_with_it(self):
        main = self.entry()
        tag = raw_input_tag(main, "user_code")
        self.assertIn(" autofocus", tag, "the empty entry page focuses its one input (device_enter_code.html)")
        self.assertEqual(tag.count("aria-describedby="), 1,
                         "one aria-describedby on the code input: components/field.html writes its own only with errors "
                         "or help; if it now writes one always, pass the intro as its help input and drop it from extra")
        self.assertEqual(Tags(tag).find("input")[0].get("aria-describedby"), "device-intro",
                         "focus skips the paragraph above the field, so the field is described by it")
        intro = element_with_id(main, "device-intro")
        self.assertIsNotNone(intro, "the intro paragraph needs id=device-intro: it is the field's description")
        self.assertIn("Enter the code shown in your terminal. Enter only a code from a veilfile login that you started yourself, just now.",
                      text_of(intro), "the intro keeps the voice pass's words")

    def test_after_a_wrong_code_the_sentence_is_tied_to_the_field_and_the_field_takes_focus(self):
        main = self.entry(user_code="NOPE-NOPE")
        tag = raw_input_tag(main, "user_code")
        attributes = Tags(tag).find("input")[0]
        self.assertEqual(attributes.get("value"), "NOPE-NOPE", "the typed code stays in the field")
        self.assertEqual(attributes.get("aria-invalid"), "true", "a wrong code marks the field invalid")
        self.assertIn(" autofocus", tag, "after a wrong code focus goes to the field in error, which is read with its sentence by aria-describedby "
                      "(device_enter_code.html): a page sent back with an error does not leave focus at the top")
        described = [element_with_id(main, name) for name in attributes.get("aria-describedby", "").split()]
        self.assertTrue(described and all(described), "aria-describedby must name elements of the page")
        self.assertIn("We couldn't find that code.", text_of(" ".join(described)),
                      "the field's sentence is the view's error (the 'errors' list in backend/drop_core/device.py)")
        self.says(text_of(main), "does not match a pending login", "the paragraph above the field")
        self.omits(main, "Enter the code shown in your terminal.", "the page after a wrong code")
        self.omits(main, 'id="device-intro"', "the page after a wrong code (the explanation is the paragraph)")

    def test_a_code_typed_with_markup_comes_back_as_text(self):
        # A wrong code returns in the field's value from the query string, which anyone can write.
        typed = '"><img src=x onerror=alert(1)>'
        main = self.entry(user_code=typed)
        self.omits(main, "<img src=x", "the page after a wrong code: components/field.html must escape the value "
                   "(auto-escaping, never |safe)")
        self.assertEqual(Tags(raw_input_tag(main, "user_code")).find("input")[0].get("value"), typed,
                         "the field shows the typed code as text, so the visitor can correct it")

    def test_the_unverified_notice_is_a_status_line_that_links_the_resend_page(self):
        main = main_of(self.unverified_browser().get("/device").content.decode())
        found = re.search(r'<p class="notice" role="status">(.*?)</p>', main, re.DOTALL)
        self.assertIsNotNone(found, 'an unverified visitor sees a <p class="notice" role="status"> (device_enter_code.html)')
        self.assertIn("Verify your email first.", text_of(found.group(1)))
        self.assertIn(f'href="{reverse("email-verification")}"', found.group(1),
                      "the notice links to the page that re-sends the verification email")
        self.omits(self.entry(), "Verify your email first", "the page of a verified visitor")


class DeviceConsentTests(DeviceCase):
    def test_the_code_is_text_under_a_visible_label(self):
        grant, main = self.consent()
        self.assertRegex(main, r'<p class="label">Device code</p>\s*<p class="device-code__value">'
                         + re.escape(grant["user_code"]) + r"</p>",
                         "the code is a paragraph under a visible 'Device code' label (device_consent.html)")

    def test_who_asked_is_four_terms_and_their_values(self):
        _grant, main = self.consent(REMOTE_ADDR="198.51.100.7", HTTP_USER_AGENT="veilfile-cli/0.3.0 (test)")
        pairs = re.findall(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", main, re.DOTALL)
        self.assertEqual([term for term, _value in pairs], ["Requested", "From IP", "Client", "Signed in as"],
                         "the request's details keep their four terms, in order (device_consent.html)")
        values = [text_of(value) for _term, value in pairs]
        self.assertEqual(values[1:], ["198.51.100.7", "veilfile-cli/0.3.0 (test)", "dev@example.com"])
        self.assertRegex(values[0], r"^\d+ minutes? ago$")

    def test_what_the_terminal_sent_is_escaped_and_cut(self):
        # The client string comes from an unauthenticated request. It is shown as text, never as markup, and a long
        # one is cut at 120 characters.
        _grant, main = self.consent(HTTP_USER_AGENT="<img src=x onerror=alert(1)>" + "x" * 250)
        self.omits(main, "<img src=x", "the page: the client string must be escaped (auto-escaping in "
                   "backend/templates/device/device_consent.html, never |safe)")
        self.says(main, "<code>&lt;img src=x onerror=alert(1)&gt;", "the page: the client string, in <code>")
        client = re.search(r"<dt[^>]*>Client</dt>\s*<dd[^>]*>(.*?)</dd>", main, re.DOTALL).group(1)
        self.assertLessEqual(len(text_of(client)), 120, "the client string is cut with truncatechars:120")
        self.assertTrue(text_of(client).endswith(ELLIPSIS), "a cut client string ends in an ellipsis")

    def test_the_warning_and_the_grant_summary_keep_their_words(self):
        _grant, main = self.consent()
        words = text_of(main)
        for needle in (
            "Approve only if you just ran veilfile login yourself and the code below matches the one in your terminal. "
            "If someone else sent you this link or asked you to approve it, choose Deny. Denying issues no key.",
            "A terminal is asking for an API key to your workspace. Approving gives it that key.",
            "Approving creates an API key for your workspace. Whoever holds the key can do these things:",
            "Upload files and create share links",
            "List every artifact in the workspace and download each one through its link",
            "Revoke artifacts",
            "List the workspace's API keys and revoke only the key in use",
            "See the account email and plan usage",
            "The key does not expire: it works until you revoke it in the dashboard. It counts toward your plan's limit "
            "of 1 API key. It is sent only to the terminal; this browser never sees it.",
        ):
            self.says(words, needle, "backend/templates/device/device_consent.html or includes/grant_summary.html "
                      "(the words are the voice pass's, and the summary must stay accurate)")

    def test_the_form_posts_the_same_fields_to_the_same_two_addresses(self):
        grant, main = self.consent()
        tags = Tags(main)
        forms = tags.find("form")
        self.assertEqual(len(forms), 1, "the approval page has one form")
        self.assertEqual((forms[0].get("method"), forms[0].get("action")), ("post", "/device/approve/"))
        self.assertEqual([attrs.get("value") for attrs in tags.find("input", {"type": "hidden", "name": "user_code"})],
                         [grant["user_code"]], "the hidden user_code carries the code as XXXX-XXXX")
        self.assertEqual(len(tags.find("input", {"type": "hidden", "name": "csrfmiddlewaretoken"})), 1,
                         "{% csrf_token %} stays in the page's own form (a component included with only cannot see it)")
        self.says(main, 'class="check__input" type="checkbox" name="confirm" value="yes" required',
                  "the page: the confirmation checkbox (the approval is refused without it)")
        confirm = [row for row in check_rows_of(main) if 'name="confirm"' in row]
        self.assertEqual(len(confirm), 1, 'the confirmation is one <label class="check"> row: the whole row is the label '
                         "(backend/templates/device/device_consent.html)")
        self.assertEqual(text_of(confirm[0]), "I just ran veilfile login myself, and this code matches my terminal.")
        self.assertRegex(confirm[0], r'<span class="check__text">I just ran .*</span>\s*$',
                         "the words of the row are its check__text")
        actions = re.search(r'<div class="form-actions">(.*?)</div>', main, re.DOTALL)
        self.assertIsNotNone(actions, 'Approve and Deny are two actions of equal weight: <div class="form-actions"> '
                             "(backend/templates/device/device_consent.html)")
        buttons = buttons_of(main)
        self.assertEqual(buttons_of(actions.group(1)), buttons, "both buttons sit in the one form-actions div")
        self.assertEqual([text for _attrs, text in buttons], ["Approve", "Deny"],
                         "two submit buttons, Approve first and Deny after it, both in the one form")
        (approve, _), (deny, _) = buttons
        self.assertEqual((approve.get("type"), approve.get("formaction")), ("submit", None),
                         "Approve posts to the form's own action, /device/approve/")
        self.assertEqual((deny.get("type"), deny.get("formaction")), ("submit", "/device/deny/"))
        self.assertIn("formnovalidate", deny, "Deny skips the browser's required-field check")

    def test_below_the_cap_there_is_no_key_to_replace(self):
        _grant, main = self.consent()
        for text in ("replace_key", "Choose a key to replace"):
            self.omits(main, text, "the approval page of a workspace under its plan's key limit")

    def test_at_the_cap_each_key_is_a_radio_row_with_its_id_as_the_value(self):
        first = self.at_the_key_cap("prod-agent")
        APIKey.objects.filter(pk=first.pk).update(last_used_at=None)
        _grant, main = self.consent()
        self.assertRegex(main, r"<fieldset>\s*<legend>Choose a key to replace</legend>",
                         "the keys are one fieldset with the legend 'Choose a key to replace'")
        radios = Tags(main).find("input", {"type": "radio"})
        self.assertEqual([(radio.get("name"), radio.get("value")) for radio in radios], [("replace_key", str(first.pk))],
                         "one radio per active key, named replace_key, its value the key's id as plain digits")
        self.says(main, f'type="radio" name="replace_key" value="{first.pk}"', "the page: the radio, attributes in this order")
        self.assertIn("required", radios[0], "a key must be chosen before the form can be sent")
        self.assertNotIn("checked", radios[0], "no key is chosen on the first view")
        rows = [row for row in check_rows_of(main) if 'name="replace_key"' in row]
        self.assertEqual(len(rows), 1, 'each key is one <label class="check"> row: the whole row is the label '
                         "(backend/templates/device/device_consent.html)")
        self.assertIn('class="check__input"', rows[0], "the radio is the form layout's check__input")
        self.assertRegex(rows[0], r'<span class="check__text">prod-agent<span class="check__detail">.*</span></span>\s*$',
                         "the key's name, then its details in check__detail")
        words = text_of(rows[0])
        for needle in ("prod-agent", first.key_prefix, "last used never"):
            self.assertIn(needle, words, f"the key's row tells {needle!r}")
        self.says(text_of(main), "Your plan allows 1 active API key, and this workspace has 1.", "the page")

    def test_a_key_name_is_shown_as_text(self):
        self.at_the_key_cap("<b>boom</b>")
        _grant, main = self.consent()
        self.omits(main, "<b>boom</b>", "the page: a key's name is the user's own text, so it is escaped")
        self.says(main, "&lt;b&gt;boom&lt;/b&gt;", "the page: the key's name, as text")

    def test_a_refused_approval_says_why_first_and_keeps_the_choice(self):
        key = self.at_the_key_cap("prod-agent")
        grant = self.start()
        response = self.browser.post("/device/approve", {"user_code": grant["user_code"], "replace_key": str(key.pk)})
        self.assertEqual(response.status_code, 400)
        main = main_of(response.content.decode())
        self.assertEqual(main.count('role="alert"'), 1, "a refused approval has one alert (components/form_errors.html)")
        summary = Tags(main).find("div", {"role": "alert"})[0]
        self.assertEqual(summary.get("tabindex"), "-1", "the refused approval's summary can take focus (device_consent.html passes focus=True to form_errors)")
        self.assertIn("autofocus", summary, "the refused approval puts focus on its summary, so it is read out and not left above the fold (device_consent.html passes focus=True)")
        self.assertEqual(main.count("autofocus"), 1, "one element of the page has autofocus")
        before_the_form = text_of(main[main.index('role="alert"'):main.index("<form")])
        self.says(before_the_form, "Confirm that you ran veilfile login yourself and that the code matches your terminal.",
                  "the alert, which comes before the form where the visitor lands (at the foot of this long page it is "
                  "below the fold; backend/templates/device/device_consent.html)")
        chosen = Tags(main).find("input", {"name": "replace_key"})[0]
        self.assertIn("checked", chosen, 'the key chosen stays chosen (selected_key is compared with key.id|stringformat:"d")')
        self.says(main, f'name="user_code" value="{grant["user_code"]}"', "the page: the hidden code")

    def test_an_unverified_visitor_is_warned_on_the_approval_too(self):
        grant = self.start()
        main = main_of(self.unverified_browser().get("/device", {"user_code": grant["user_code"]}).content.decode())
        self.assertRegex(main, r'<p class="notice" role="status"><strong>Verify your email first\.</strong>',
                         'an unverified visitor sees a <p class="notice" role="status"> on the approval too (device_consent.html)')
        self.says(main, f'href="{reverse("email-verification")}"', "the notice: the link to re-send")
        self.omits(self.consent()[1], "Verify your email first", "the approval page of a verified visitor")


class DeviceResultPageTests(DeviceCase):
    def test_the_success_page_draws_its_mark_and_says_it_with_words(self):
        key = self.at_the_key_cap("old <i>key</i>")
        grant = self.start()
        response = self.browser.post("/device/approve", {"user_code": grant["user_code"], "confirm": "yes",
                                                         "replace_key": str(key.pk)})
        self.assertEqual(response.status_code, 200)
        main = main_of(response.content.decode())
        self.says(main, '<span class="device-mark" aria-hidden="true"></span>',
                  "backend/templates/device/device_success.html: the mark, decoration drawn in CSS; the heading and "
                  "the first sentence say it")
        self.says(main, "<h1>Terminal approved</h1>", "backend/templates/device/device_success.html")
        self.omits(main, 'role="img"', "backend/templates/device/device_success.html (the old mark was an image)")
        words = text_of(main)
        self.says(words, "You approved this login. Return to your terminal: it receives its key on its next check, within a few seconds. "
                  "You can close this window.", "backend/templates/device/device_success.html")
        self.says(words, "When it does, old <i>key</i> (", "device_success.html: the replaced key's name, as text")
        self.says(words, f"{key.key_prefix}{ELLIPSIS} ) is revoked.", "device_success.html: the replaced key's prefix")
        self.omits(main, "<i>key</i>", "device_success.html: the replaced key's name is escaped; the page")
        self.says(words, "If the terminal already stopped waiting, no key is created, so run veilfile login again. You can revoke the key at any time in the dashboard.",
                  "backend/templates/device/device_success.html")

    def test_the_denied_page(self):
        response = self.browser.post("/device/deny", {"user_code": self.start()["user_code"]})
        main = main_of(response.content.decode())
        self.says(main, "<h1>Terminal denied</h1>", "backend/templates/device/device_denied.html")
        self.says(text_of(main), "You denied this request, so no key was issued and nothing in your account changed. "
                  "If you did not start this login, you can close this window. To check your account, review your API keys in the dashboard.", "backend/templates/device/device_denied.html")

    def test_the_error_page_is_its_title_and_its_message(self):
        response = self.browser.post("/device/approve", {"user_code": "NOPE-NOPE"})
        self.assertEqual(response.status_code, 400)
        main = main_of(response.content.decode())
        self.says(main, "<h1>Invalid code</h1>", "backend/templates/device/device_error.html")
        self.says(text_of(main), "We couldn't find that device code. Check the terminal and try again.",
                  "backend/templates/device/device_error.html")
        self.omits(main, "Re-send the verification email", "device_error.html without show_resend; the page")

    def test_the_unverified_refusal_offers_the_resend_link(self):
        grant = self.start()
        response = self.unverified_browser().post("/device/approve", {"user_code": grant["user_code"], "confirm": "yes"})
        self.assertEqual(response.status_code, 403)
        main = main_of(response.content.decode())
        self.says(main, "<h1>Verify your email first</h1>", "backend/templates/device/device_error.html")
        self.assertRegex(main, rf'<p class="form-links"><a class="label interactive" '
                         rf'href="{re.escape(reverse("email-verification"))}">Re-send the verification email</a></p>',
                         "device_error.html shows the resend link, a secondary action (a.label.interactive in a "
                         "p.form-links), when show_resend is set")
        self.assertEqual(DeviceCode.objects.get(user_code=grant["user_code"].replace("-", "")).status,
                         DeviceCode.STATUS_PENDING, "the refusal grants nothing")

    def test_the_throttled_page_is_the_error_page_and_holds_no_form(self):
        response = self.every_page()["too many"]
        self.assertEqual(response.status_code, 429)
        main = main_of(response.content.decode())
        self.says(main, "<h1>Too many attempts</h1>", "backend/templates/device/device_error.html")
        self.says(main, "Wait 10 minutes", "backend/templates/device/device_error.html")
        self.omits(main, "csrfmiddlewaretoken", "a refusal page")


class OAuthRefusalOnTheDeviceErrorPageTests(tests_oauth.OAuthBaseTestCase):
    """device/device_error.html is also the OAuth consent's 403 (VeilfileAuthorizationView.dispatch in drop_core/oauth.py)."""

    longMessage = False

    def test_an_unverified_visitor_gets_the_shared_page_in_the_oauth_words(self):
        EmailVerification.objects.filter(user=self.user).update(is_verified=False)
        client_id = self._register_client().json()["client_id"]
        self.client.force_login(self.user)
        _verifier, challenge = tests_oauth._pkce_pair()
        response = self.client.get(tests_oauth._AUTHORIZE_URL, tests_oauth._authorize_params(client_id, challenge))
        self.assertEqual(response.status_code, 403)
        self.assertIn("layouts/form.html", [template.name for template in response.templates],
                      "backend/templates/device/device_error.html (shared with OAuth) must extend layouts/form.html")
        html = response.content.decode()
        main = main_of(html)
        self.assertIn("<h1>Verify your email first</h1>", main, "the OAuth refusal's heading")
        self.assertIn("Authorizing an app requires a verified email address, so we can reach you if the grant is ever "
                      "abused. Check your inbox for the verification link, or send a fresh one.", text_of(main),
                      "the OAuth refusal's sentence (backend/drop_core/oauth.py)")
        self.assertIn(f'href="{reverse("email-verification")}"', main, "the OAuth refusal links the resend page")
        for word in ("terminal", "veilfile login", "device"):
            self.assertNotIn(word, text_of(main).lower(),
                             f"device_error.html is shared with the OAuth consent: nothing on it may say '{word}'")
        self.assertNotIn("device.css", html, "the shared refusal page draws nothing of the device flow's, so it loads "
                         "nothing of it (backend/templates/device/device_error.html)")
        self.assertIn('<meta name="robots" content="noindex">', html, "the OAuth refusal is kept out of search")
        self.assertIn("<title>Verify your email first | Veilfile</title>", html, "the OAuth refusal's title")


class DeviceAddressAndRoundTripTests(DeviceCase):
    def test_every_spelling_of_every_address_answers(self):
        for path in ("/device", "/device/"):
            with self.subTest(path=path):
                grant = self.start()
                self.says(self.browser.get(path).content.decode(), "<h1>Enter device code</h1>", f"GET {path}")
                self.says(self.browser.get(path, {"user_code": grant["user_code"]}).content.decode(),
                          "<h1>Approve terminal login</h1>", f"GET {path}?user_code=...")
        for path in ("/device/approve", "/device/approve/"):
            with self.subTest(path=path):
                response = self.browser.post(path, {"user_code": self.start()["user_code"], "confirm": "yes"})
                self.says(response.content.decode(), "<h1>Terminal approved</h1>", f"POST {path}")
        for path in ("/device/deny", "/device/deny/"):
            with self.subTest(path=path):
                response = self.browser.post(path, {"user_code": self.start()["user_code"]})
                self.says(response.content.decode(), "<h1>Terminal denied</h1>", f"POST {path}")

    def test_the_pages_use_the_names_not_a_spelling(self):
        self.assertEqual((reverse("device"), reverse("device-approve"), reverse("device-deny")),
                         ("/device", "/device/approve/", "/device/deny/"),
                         "backend/config/urls.py: the device templates use {% url %} on these three names")

    def test_a_signed_out_visit_to_the_clis_link_returns_to_it_after_sign_in(self):
        grant = self.start()
        link = f"/device?user_code={grant['user_code']}"
        visitor = APIClient()
        bounce = visitor.get(link)
        self.assertEqual(bounce.status_code, 302)
        self.assertEqual(bounce["Location"], f"/accounts/login/?next={quote(link, safe='/')}",
                         "login_required sends the visitor to sign in, with the device link as next")
        signed_in = visitor.post(bounce["Location"], {"username": "dev", "password": "pw12345678"})
        self.assertEqual(signed_in.status_code, 302, "sign in at the address the bounce gave: the form has no action")
        self.assertEqual(signed_in["Location"], link, "sign in returns to /device?user_code=...: the sign-in form must "
                         "post to its own address and keep ?next= (backend/templates/registration/login.html)")
        landed = visitor.get(signed_in["Location"])
        self.assertEqual(landed.status_code, 200)
        main = main_of(landed.content.decode())
        self.says(main, "<h1>Approve terminal login</h1>", "the page the CLI's link opens after sign in")
        self.says(main, grant["user_code"], "the page the CLI's link opens after sign in")
