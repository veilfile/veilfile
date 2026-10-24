"""Sign in, sign up, throttled and checkout confirm on the form layout (spec 4.7, 4.9).

The four pages keep their addresses, field names, hidden fields, status codes and the phrases the CLIs and
older tests match; they move onto the shell and layouts/form.html. A signed-in visit to sign in or sign up
goes on to the safe ?next= or the dashboard, and ?next= makes the whole trip through sign in and sign up.

Every class sets longMessage = False, so that a failure prints its own message, which names the file and
the fix, and not the page it searched.
"""
import json
import logging
import os
import re
from base64 import urlsafe_b64encode
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from drop_core import throttle
from drop_core.forms import SignupForm
from drop_core.models import EmailVerification, get_or_create_workspace
from drop_core.oauth import OAUTH_SCOPE_MCP_USE
from drop_core.plans import PLANS
from pages.tests_head import request_for

User = get_user_model()
PASSWORD = "pw12345678"
LOGIN = "/accounts/login/"
SIGNUP = "/accounts/signup/"
DEVICE_LINK = "/device?user_code=ABCD-EFGH"
TEMPLATES = Path(settings.BASE_DIR) / "templates"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
FAILED = "Invalid username, email, or password. Try again."


class Page(HTMLParser):
    """What these tests read in a page: its inputs, forms, buttons, links, headings, alerts, the label of each
    input and the text of every element that has an id."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.html = html
        self.inputs, self.forms, self.buttons, self.links = [], [], [], []
        self.headings, self.alerts, self.labels, self.texts = [], [], {}, {}
        self.alert_attrs = []   # the attributes of each role=alert element, in the order of self.alerts
        self._open = []
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input":
            self.inputs.append(attrs)
        if tag == "form":
            self.forms.append(attrs)
        if tag not in VOID:
            self._open.append([tag, attrs, []])

    def handle_data(self, data):
        for element in self._open:
            element[2].append(data)

    def handle_endtag(self, tag):
        names = [element[0] for element in self._open]
        if tag not in names:
            return
        depth = len(names) - 1 - names[::-1].index(tag)
        while len(self._open) > depth:
            name, attrs, parts = self._open.pop()
            text = " ".join("".join(parts).split())
            if "id" in attrs:
                self.texts[attrs["id"]] = text
            if attrs.get("role") == "alert":
                self.alerts.append(text)
                self.alert_attrs.append(attrs)
            if name == "label" and "for" in attrs:
                self.labels[attrs["for"]] = text
            if name == "button":
                self.buttons.append((attrs, text))
            if name == "a":
                self.links.append((attrs, text))
            if name == "h1":
                self.headings.append(text)

    def input(self, id):
        return next((attrs for attrs in self.inputs if attrs.get("id") == id), None)

    def href(self, text):
        return next((attrs.get("href") for attrs, found in self.links if found == text), None)

    def described(self, attrs):
        """The text of the elements an input's aria-describedby names, and the ids of them that the page lacks."""
        tokens = attrs.get("aria-describedby", "").split()
        return " ".join(self.texts.get(token, "") for token in tokens), [t for t in tokens if t not in self.texts]


def main_of(html):
    return html[html.index("<main"):html.index("</main>")]


def next_of(href):
    """The address a link carries in its ?next=, decoded, or None."""
    return (parse_qs(urlsplit(href or "").query).get("next") or [None])[0]


def pkce_challenge():
    return urlsafe_b64encode(sha256(b"a verifier for the tests").digest()).rstrip(b"=").decode("ascii")


# These tests make a few dozen accounts and sign-ins; the fast hasher keeps them to seconds. What they check is the
# pages, not the hash.
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PageCase(TestCase):
    longMessage = False

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        throttle_log = logging.getLogger("drop_core.throttle")
        self.addCleanup(throttle_log.setLevel, throttle_log.level)
        throttle_log.setLevel(logging.ERROR)

    def parse(self, response):
        return Page(response.content.decode())

    def assertSentTo(self, response, expected, message):
        """A 302 to exactly `expected`. A page that answered instead says what it answered, not a KeyError."""
        self.assertEqual((response.status_code, response.get("Location")), (302, expected), message)

    def field(self, page, id):
        found = page.input(id)
        if found is None:
            self.fail(f"the page has no input with id {id}: backend/templates/registration keeps every field id "
                      "(the labels, error ids and older tests find them)")
        return found

    def lock_out(self, *names):
        """Ten failures on record for each name, from the test client's address: the sign-in lock engages for each."""
        for name in names:
            for _ in range(10):
                throttle.record_login_failure("127.0.0.1", name)


class FormLayoutTests(PageCase):
    """The four pages are drawn by layouts/form.html on the shell, with the old includes gone."""
    longMessage = False

    def pages(self):
        User.objects.create_user("signedin", email="signedin@example.com", password=PASSWORD)
        member = Client()
        member.force_login(User.objects.get(username="signedin"))
        User.objects.create_user("victim", password=PASSWORD)
        self.lock_out("victim")
        return {
            "registration/login.html": self.client.get(LOGIN),
            "registration/signup.html": self.client.get(SIGNUP),
            "registration/checkout_confirm.html": member.get(SIGNUP + "?plan=team"),
            "registration/throttled.html": Client().post(LOGIN, {"username": "victim", "password": "nope"}),
        }

    def test_each_page_extends_the_form_layout_and_includes_nothing_old(self):
        for name, response in self.pages().items():
            with self.subTest(template=name):
                used = [template.name for template in response.templates]
                self.assertIn(name, used, f"{name} was not the template drawn for this page")
                self.assertIn("layouts/form.html", used, f"backend/templates/{name} must extend layouts/form.html")
                self.assertEqual([found for found in used if found.startswith("includes/")], [],
                                 f"backend/templates/{name}: the old includes (theme_boot, ambient, menu) are gone with "
                                 "backend/templates/includes/; this page draws the shell's own corners, menu and switches, "
                                 "so it includes nothing named includes/")
                html = response.content.decode()
                self.assertRegex(html, r'<body class="[^"]*\blayout-form\b',
                                 "<body> needs the class layout-form (backend/templates/layouts/form.html)")
                self.assertNotIn("data-layout", html,
                                 f"backend/templates/{name}: a form page changes nothing in place, so its <body> has no "
                                 "data-layout (a form page is not in the registry)")

    def test_each_page_has_the_shells_landmarks_and_one_h1_inside_main(self):
        for name, response in self.pages().items():
            with self.subTest(template=name):
                html = response.content.decode()
                self.assertIn('<a class="skip-link label" href="#main">', html, f"{name} lost the skip link")
                self.assertIn('<main id="main" class="shell-main">', html, f"{name} lost <main id=\"main\">")
                page = Page(html)
                self.assertEqual(len(page.headings), 1, f"backend/templates/{name} needs exactly one <h1>")
                self.assertEqual(len(re.findall(r"<h1[ >]", main_of(html))), 1, f"{name}: the <h1> belongs inside <main>")
                for element in ("<header", "<footer"):
                    self.assertNotIn(element, main_of(html), f"{name}: no {element}> inside <main>; use a div")

    def test_each_page_names_itself_in_its_title(self):
        titles = {
            "registration/login.html": "Log in | Veilfile",
            "registration/signup.html": "Sign up | Veilfile",
            "registration/checkout_confirm.html": "Checkout | Veilfile",
            "registration/throttled.html": "Too many sign-in attempts | Veilfile",
        }
        for name, response in self.pages().items():
            with self.subTest(template=name):
                self.assertIn(f"<title>{titles[name]}</title>", response.content.decode(),
                              f"backend/templates/{name}: {{% block title %}} names the page, then | Veilfile")

    def test_every_input_has_a_visible_label(self):
        for name, response in self.pages().items():
            page = self.parse(response)
            for attrs in page.inputs:
                if attrs.get("type") in ("hidden", "submit"):
                    continue
                with self.subTest(template=name, input=attrs.get("id")):
                    self.assertTrue(page.labels.get(attrs.get("id")),
                                    f"backend/templates/{name}: input {attrs.get('name')} needs a visible <label for=\"{attrs.get('id')}\"> "
                                    "(components/field.html draws one)")

    def test_no_page_leaves_a_template_comment_in_its_text(self):
        # Django 6.1 strips a one-line {# #} comment and prints a multi-line one as text.
        for name, response in self.pages().items():
            with self.subTest(template=name):
                html = response.content.decode()
                self.assertNotIn("{#", html, f"backend/templates/{name}: keep each {{# #}} comment on one line")
                self.assertNotIn("#}", html, f"backend/templates/{name}: keep each {{# #}} comment on one line")

    def test_no_page_takes_focus_for_the_visitor(self):
        # A page of several fields reads its heading first; only a page that exists to take one input autofocuses it.
        for name, response in self.pages().items():
            with self.subTest(template=name):
                for attrs in self.parse(response).inputs:
                    self.assertNotIn("autofocus", attrs,
                                     f"backend/templates/{name}: drop autofocus from {attrs.get('name')}; this page has "
                                     "several fields and a heading to read first")

    def test_every_button_and_link_in_the_page_answers_the_cursor(self):
        # Only .interactive gives a control the ink wash on hover and on a press (base.css), so the main action is
        # <button class="action interactive"> and a secondary link is <a class="label interactive"> in a p.form-links.
        for name, response in self.pages().items():
            with self.subTest(template=name):
                html = main_of(response.content.decode())
                page = Page(html)
                self.assertLessEqual(len(page.buttons), 1, f"backend/templates/{name}: one main action to a page")
                for attrs, text in page.buttons:
                    self.assertTrue({"action", "interactive"} <= set(attrs.get("class", "").split()) and attrs.get("type") == "submit",
                                    f"backend/templates/{name}: the main action {text!r} is <button class=\"action interactive\" "
                                    f"type=\"submit\">; this one has class {attrs.get('class')!r}")
                for attrs, text in page.links:
                    self.assertTrue({"label", "interactive"} <= set(attrs.get("class", "").split()),
                                    f"backend/templates/{name}: the link {text!r} is <a class=\"label interactive\">; this one has "
                                    f"class {attrs.get('class')!r}")
                self.assertEqual(len(re.findall(r'<p class="form-links[ "]', html)), 1,
                                 f"backend/templates/{name}: the secondary links sit in one <p class=\"form-links\">")

    def test_a_separator_between_secondary_links_starts_the_item_of_the_link_after_it(self):
        # In the 26rem column a line break can fall on either side of a separator; one that leaves the dot at the end of a line looks like a mistake. The dot is the first
        # thing in the .form-links__item (base.css: a flex row, whose parts do not wrap apart) that goes on to its link, so it wraps together with it.
        mark = r'<span aria-hidden="true">[\u00b7\u2022|]</span>'
        for name, response in self.pages().items():
            with self.subTest(template=name):
                paragraphs = re.findall(r'<p class="form-links[^"]*">(.*?)</p>', main_of(response.content.decode()), re.S)
                self.assertEqual(len(paragraphs), 1, f"backend/templates/{name}: the secondary links sit in one <p class=\"form-links\">")
                links = paragraphs[0]
                held = re.findall(r'<span class="form-links__item">' + mark + r' <a ', links)
                self.assertEqual(len(re.findall(mark, links)), len(held),
                                 f"backend/templates/{name}: write a separator as <span class=\"form-links__item\"><span aria-hidden=\"true\">·</span> <a ...>, "
                                 "so that it wraps with the link after it and never ends a line")
                self.assertNotRegex(re.sub(r"<[^>]*>", "", re.sub(mark, "", links)), r"[\u00b7\u2022|]",
                                    f"backend/templates/{name}: a separator outside a span aria-hidden is read out and can end a line")
                if name == "registration/throttled.html":
                    self.assertEqual(len(held), 1, "backend/templates/registration/throttled.html: Log in and Forgot password? are set off by one separator held with the second link")

    def test_no_price_is_typed_in_these_templates(self):
        for name in ("login", "signup", "throttled", "checkout_confirm"):
            with self.subTest(template=name):
                text = (TEMPLATES / "registration" / f"{name}.html").read_text(encoding="utf-8")
                self.assertIsNone(re.search(r"\$\s?\d", text),
                                  f"backend/templates/registration/{name}.html types a price: a plan's price is written in "
                                  "backend/drop_core/plans.py and reaches the page through the view (AGENTS.md, Pricing changes)")


class SignInPageTests(PageCase):
    longMessage = False

    def test_the_form_posts_to_its_own_address_with_a_token(self):
        page = self.parse(self.client.get(LOGIN))
        self.assertEqual(len(page.forms), 1, "backend/templates/registration/login.html holds one form")
        self.assertEqual(page.forms[0].get("method", "").lower(), "post")
        self.assertNotIn("action", page.forms[0],
                         "backend/templates/registration/login.html: a form with an action would drop ?next= on the way "
                         "to the server; post to the page's own address")
        self.assertTrue(any(attrs.get("name") == "csrfmiddlewaretoken" for attrs in page.inputs),
                        "backend/templates/registration/login.html needs {% csrf_token %} inside the form")

    def test_the_fields_keep_their_names_labels_and_tokens(self):
        page = self.parse(self.client.get(LOGIN))
        username, password = self.field(page, "id_username"), self.field(page, "id_password")
        self.assertEqual((username.get("name"), username.get("type"), username.get("autocomplete")),
                         ("username", "text", "username"))
        self.assertEqual((password.get("name"), password.get("type"), password.get("autocomplete")),
                         ("password", "password", "current-password"))
        self.assertIn("required", username)
        self.assertIn("required", password)
        self.assertEqual(page.labels["id_username"], "Username or email")
        self.assertEqual(page.labels["id_password"], "Password")
        self.assertEqual((username.get("autocapitalize"), username.get("spellcheck")), ("none", "false"),
                         "backend/templates/registration/login.html: the name field passes extra='autocapitalize=\"none\" "
                         "spellcheck=\"false\"', or a phone capitalises the first letter of a name that is case-sensitive")

    def test_a_first_visit_announces_nothing(self):
        self.assertEqual(self.parse(self.client.get(LOGIN)).alerts, [],
                         "backend/templates/registration/login.html: the failure sentence is drawn only {% if form.errors %}")

    def test_a_failed_sign_in_is_one_alert_with_one_sentence_whatever_was_wrong(self):
        User.objects.create_user("real", password=PASSWORD)
        inactive = User.objects.create_user("gone", password=PASSWORD)
        inactive.is_active = False
        inactive.save()
        for name, password in (("real", "wrong"), ("nobody", "wrong"), ("gone", PASSWORD), ("", "")):
            with self.subTest(username=name):
                response = self.client.post(LOGIN, {"username": name, "password": password})
                self.assertEqual(response.status_code, 200)
                alerts = self.parse(response).alerts
                self.assertEqual(len(alerts), 1,
                                 "backend/templates/registration/login.html: a failed sign-in is one role=alert summary "
                                 "(components/form_errors.html)")
                self.assertIn(FAILED, alerts[0], "backend/templates/registration/login.html: the one sentence is the message of "
                              "form_errors, whatever failed")

    def test_a_failed_sign_in_keeps_the_name_and_never_the_password(self):
        page = self.parse(self.client.post(LOGIN, {"username": "someone", "password": "secret-guess"}))
        self.assertEqual(self.field(page, "id_username").get("value"), "someone")
        self.assertNotIn("value", self.field(page, "id_password"))
        self.assertNotIn("secret-guess", page.html, "a password is never put back in the page")

    def test_the_sign_up_link_carries_a_safe_next_and_only_a_safe_one(self):
        for query, expected in (
            ("", None),
            ("?next=" + DEVICE_LINK.replace("?", "%3F").replace("=", "%3D"), DEVICE_LINK),
            ("?next=https://evil.example/", None),
            ("?next=//evil.example/", None),
            ("?next=javascript:alert(1)", None),
        ):
            with self.subTest(query=query):
                page = self.parse(self.client.get(LOGIN + query))
                href = page.href("Sign up")
                self.assertEqual(next_of(href), expected,
                                 "backend/templates/registration/login.html: the Sign up link carries the safe next "
                                 "(LoginView puts the safe next in the context) and nothing else")
                self.assertEqual(urlsplit(href).path, SIGNUP, "backend/templates/registration/login.html: the Sign up link goes to the sign-up page")

    def test_a_failed_attempt_keeps_next_on_its_links(self):
        response = self.client.post(LOGIN + "?next=/app/files/", {"username": "someone", "password": "wrong"})
        self.assertEqual(next_of(self.parse(response).href("Sign up")), "/app/files/",
                         "backend/templates/registration/login.html: a page drawn again after a failed attempt keeps the safe next on its Sign up link")

    def test_what_the_page_puts_back_is_escaped(self):
        hostile = '"><script>alert(1)</script>'
        response = self.client.post(LOGIN + "?" + urlencode({"next": "/x" + hostile}),
                                    {"username": hostile, "password": "wrong"})
        self.assertEqual(response.status_code, 200)
        page = self.parse(response)
        self.assertNotIn("<script>alert(1)</script>", page.html,
                         "a typed name or a ?next= must be escaped where the page puts it back "
                         "(components/field.html for the name, |urlencode in login.html for next)")
        self.assertEqual(self.field(page, "id_username").get("value"), hostile)
        self.assertEqual(next_of(page.href("Sign up")), "/x" + hostile,
                         "backend/templates/registration/login.html: the Sign up link carries next through |urlencode, so it reads back as it went in")

    def test_the_other_link_goes_to_password_reset(self):
        self.assertEqual(self.parse(self.client.get(LOGIN)).href("Forgot password?"), "/accounts/password_reset/")

    def test_the_menu_marks_this_page(self):
        html = self.client.get(LOGIN).content.decode()
        self.assertRegex(html, r'<a [^>]*href="/accounts/login/"[^>]*aria-current="page"[^>]*>Sign in</a>',
                         "the shell's menu marks Sign in as the current page on /accounts/login/")


class ErrorFocusTests(PageCase):
    """A page sent back with errors puts focus where the problem is, and a form whose only error is the whole form's still announces it."""
    longMessage = False

    def focused(self, response):
        return [attrs.get("id") for attrs in self.parse(response).inputs if "autofocus" in attrs]

    def test_a_failed_sign_in_announces_its_one_error_and_focuses_it(self):
        # The failure is the form's own (no field is named: a failed attempt reads the same whatever was wrong, and the page draws no field error), so there is no field to focus. An alert
        # that is on the page when it loads is announced inconsistently, so the summary takes focus itself (components/form_errors.html `focus`): tabindex -1 and autofocus, the only autofocus.
        for name, password in (("nobody", "wrong"), ("", "")):
            with self.subTest(username=name):
                response = self.client.post(LOGIN, {"username": name, "password": password})
                page = self.parse(response)
                self.assertEqual(len(page.alerts), 1, "backend/templates/registration/login.html: a failed sign-in is one role=\"alert\" summary (components/form_errors.html)")
                self.assertIn(FAILED, page.alerts[0], "backend/templates/registration/login.html: the alert says the one sentence")
                self.assertEqual(self.focused(response), [], "backend/templates/registration/login.html: no field is in error, so none takes autofocus")
                summary = page.alert_attrs[0]
                self.assertEqual((summary.get("tabindex"), "autofocus" in summary), ("-1", True),
                                 "backend/templates/registration/login.html: pass focus=True to form_errors, so the summary has tabindex=\"-1\" and autofocus and a failed sign-in "
                                 "does not leave focus at the top of the page")
                self.assertEqual(len(re.findall(r"\bautofocus\b", response.content.decode())), 1, "one element of the page has autofocus")

    def test_a_first_visit_to_sign_in_focuses_nothing(self):
        html = self.client.get(LOGIN).content.decode()
        self.assertEqual(re.findall(r"\bautofocus\b", html), [], "backend/templates/registration/login.html: a first visit reads its heading first; only the failure takes focus")
        self.assertNotIn("tabindex", main_of(html), "backend/templates/registration/login.html: nothing on the page is made a stop with tabindex")

    def test_sign_up_focuses_the_first_field_in_error(self):
        good = {"username": "focusana", "email": "focusana@example.com", "password1": "a-test-only-password-1", "password2": "a-test-only-password-1"}
        for label, change, expected in (
            ("only the confirmation differs", {"password2": "a-different-password"}, ["id_password2"]),
            ("the email is not one", {"email": "not-an-email"}, ["id_email"]),
            ("the username and the confirmation are wrong", {"username": "", "password2": "a-different-password"}, ["id_username"]),
            ("every field is wrong", {"username": "", "email": "nope", "password1": "x", "password2": "y"}, ["id_username"]),
        ):
            with self.subTest(case=label):
                response = self.client.post(SIGNUP, {**good, **change})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.focused(response), expected,
                                 "backend/templates/registration/signup.html: autofocus goes on the first field in error and on no other "
                                 "({% focus_field form \"username\" \"email\" \"password1\" \"password2\" as focus %}, passed to each field as focus=focus)")

    def test_a_blank_sign_up_takes_focus_nowhere(self):
        self.assertEqual(self.focused(self.client.get(SIGNUP)), [], "backend/templates/registration/signup.html: a page of four fields reads its heading first")

    def test_a_sign_up_with_only_an_error_of_the_form_announces_it_and_focuses_it(self):
        form = SignupForm({"username": "focusbo", "email": "focusbo@example.com", "password1": "a-test-only-password-1", "password2": "a-test-only-password-1"})
        self.assertTrue(form.is_valid())
        form.add_error(None, "Sign-up is paused for this network.")
        html = render_to_string("registration/signup.html", {"form": form}, request=request_for(SIGNUP))
        page = Page(html)
        self.assertEqual(len(page.alerts), 1, "backend/templates/registration/signup.html: a form's own error is a role=\"alert\" summary above the form")
        self.assertIn("Sign-up is paused for this network.", page.alerts[0])
        self.assertEqual([attrs.get("id") for attrs in page.inputs if "autofocus" in attrs], [], "no field is in error, so none takes autofocus")
        summary = page.alert_attrs[0]
        self.assertEqual((summary.get("tabindex"), "autofocus" in summary), ("-1", True),
                         "backend/templates/registration/signup.html: no field is in error, so the summary takes focus "
                         "({% focus_summary form \"username\" \"email\" \"password1\" \"password2\" as summary %}, passed to form_errors as focus=summary)")
        self.assertEqual(len(re.findall(r"\bautofocus\b", html)), 1, "one element of the page has autofocus")

    def test_a_sign_up_with_errors_of_both_kinds_focuses_the_field_and_not_the_summary(self):
        form = SignupForm({"username": "focusbo", "email": "focusbo@example.com", "password1": "a-test-only-password-1", "password2": "a-different-password"})
        self.assertFalse(form.is_valid())
        form.add_error(None, "Sign-up is paused for this network.")
        html = render_to_string("registration/signup.html", {"form": form}, request=request_for(SIGNUP))
        page = Page(html)
        self.assertEqual(len(page.alerts), 1, "the form's own error is still announced")
        self.assertEqual([attrs.get("id") for attrs in page.inputs if "autofocus" in attrs], ["id_password2"],
                         "backend/templates/registration/signup.html: the field in error takes focus")
        summary = page.alert_attrs[0]
        self.assertEqual(("tabindex" in summary, "autofocus" in summary), (False, False),
                         "backend/templates/registration/signup.html: a field in error wins, so the summary has neither autofocus nor tabindex (a page never has two autofocus)")
        self.assertEqual(len(re.findall(r"\bautofocus\b", html)), 1, "one element of the page has autofocus")


class SignUpPageTests(PageCase):
    longMessage = False

    FIELDS = {
        "id_username": ("username", "text", "username", "Username"),
        "id_email": ("email", "email", "email", "Email"),
        "id_password1": ("password1", "password", "new-password", "Password"),
        "id_password2": ("password2", "password", "new-password", "Confirm password"),
    }

    def test_the_fields_keep_their_names_labels_and_tokens(self):
        page = self.parse(self.client.get(SIGNUP))
        self.assertEqual(len(page.forms), 1)
        self.assertNotIn("action", page.forms[0],
                         "backend/templates/registration/signup.html: post to the page's own address so ?plan= and ?next= ride along")
        for id, (name, kind, token, label) in self.FIELDS.items():
            with self.subTest(field=id):
                attrs = self.field(page, id)
                self.assertEqual((attrs.get("name"), attrs.get("type"), attrs.get("autocomplete")), (name, kind, token))
                self.assertIn("required", attrs)
                self.assertEqual(page.labels[id], label)
                self.assertNotIn("aria-invalid", attrs, "a blank form has no mistakes to mark")

    def test_the_username_is_not_capitalised_or_spell_checked(self):
        username = self.field(self.parse(self.client.get(SIGNUP)), "id_username")
        self.assertEqual((username.get("autocapitalize"), username.get("spellcheck")), ("none", "false"),
                         "backend/templates/registration/signup.html: the name field passes extra='autocapitalize=\"none\" "
                         "spellcheck=\"false\"', or a phone capitalises the first letter of a name that is case-sensitive")

    def test_the_help_lines_are_tied_to_their_fields(self):
        page = self.parse(self.client.get(SIGNUP))
        said, missing = page.described(self.field(page, "id_email"))
        self.assertEqual(missing, [], "the email field's aria-describedby names an id the page lacks (components/field.html)")
        self.assertIn("We verify this address before issuing API keys. It is how we reach you about your account.", said,
                      "backend/templates/registration/signup.html: the email field keeps its help line")
        said, missing = page.described(self.field(page, "id_password1"))
        self.assertEqual(missing, [], "the password field's aria-describedby names an id the page lacks (components/field.html)")
        self.assertIn("at least 8 characters", said, "the password's help is the validators' own list (form.password1.help_text)")
        self.assertNotIn("aria-describedby", self.field(page, "id_password2"), "the confirmation has no help line to describe it")

    def test_a_mistake_is_tied_to_its_field_and_the_typed_values_are_kept(self):
        response = self.client.post(SIGNUP, {"username": "a11yuser", "password1": "s3cur3pass!!",
                                             "password2": "different!!", "email": "a11y@example.com"})
        self.assertEqual(response.status_code, 200)
        page = self.parse(response)
        confirm = self.field(page, "id_password2")
        self.assertEqual(confirm.get("aria-invalid"), "true")
        said, missing = page.described(confirm)
        self.assertEqual(missing, [], "aria-describedby names an id the page lacks (components/field.html)")
        self.assertIn("match", said, "the sentence under the field says what Django found: the passwords do not match")
        for id in ("id_username", "id_email", "id_password1"):
            self.assertNotIn("aria-invalid", self.field(page, id), f"{id} had no mistake")
        self.assertEqual(self.field(page, "id_username").get("value"), "a11yuser")
        self.assertEqual(self.field(page, "id_email").get("value"), "a11y@example.com")
        for id in ("id_password1", "id_password2"):
            self.assertNotIn("value", self.field(page, id), "a password is never put back in the page")

    def test_a_taken_email_is_said_under_the_email_field(self):
        User.objects.create_user("first", email="taken@example.com", password=PASSWORD)
        response = self.client.post(SIGNUP, {"username": "second", "password1": "s3cur3pass!!",
                                             "password2": "s3cur3pass!!", "email": "TAKEN@example.com"})
        page = self.parse(response)
        email = self.field(page, "id_email")
        self.assertEqual(email.get("aria-invalid"), "true")
        self.assertIn("An account with this email address already exists.", page.described(email)[0],
                      "a taken address is said under the email field, tied to it by aria-describedby")

    def test_the_plain_sentence_when_no_plan_was_picked(self):
        for query in ("", "?plan=free", "?plan=bogus"):
            with self.subTest(query=query):
                html = self.client.get(SIGNUP + query).content.decode()
                self.assertIn("Create your account. A workspace is provisioned automatically.", html,
                              "backend/templates/registration/signup.html: the plain sentence shows when no plan sentence does")
                self.assertNotIn("Team plan", html, "a plan nobody picked is not named")
                self.assertNotIn("Scale plan", html, "a plan nobody picked is not named")

    def test_the_plan_sentence_survives_a_mistake(self):
        for plan in ("team", "scale"):
            with self.subTest(plan=plan):
                response = self.client.post(f"{SIGNUP}?plan={plan}", {"username": "x", "password1": "a", "password2": "b"})
                self.assertEqual(response.status_code, 200)
                self.assertIn(f"{plan.title()} plan", main_of(response.content.decode()),
                              "backend/templates/registration/signup.html shows plan_blurb after a mistake too")

    def test_what_the_page_puts_back_is_escaped(self):
        hostile = '"><script>alert(1)</script>'
        response = self.client.post(SIGNUP + "?" + urlencode({"next": "/x" + hostile}),
                                    {"username": hostile, "email": hostile, "password1": "a", "password2": "b"})
        self.assertEqual(response.status_code, 200)
        page = self.parse(response)
        self.assertNotIn("<script>alert(1)</script>", page.html,
                         "a typed value or a ?next= must be escaped where the page puts it back "
                         "(components/field.html for the values, |urlencode in signup.html for next)")
        self.assertEqual(self.field(page, "id_username").get("value"), hostile)
        self.assertEqual(next_of(page.href("Log in")), "/x" + hostile,
                         "backend/templates/registration/signup.html: the Log in link carries next through |urlencode, so it reads back as it went in")

    def test_the_log_in_link_carries_a_safe_next(self):
        for query, expected in (("", None), ("?next=/app/files/", "/app/files/"), ("?next=https://evil.example/", None)):
            with self.subTest(query=query):
                href = self.parse(self.client.get(SIGNUP + query)).href("Log in")
                self.assertEqual(next_of(href), expected,
                                 "backend/templates/registration/signup.html: the Log in link carries the safe next (signup() puts it in the context) and nothing else")
                self.assertEqual(urlsplit(href).path, LOGIN, "backend/templates/registration/signup.html: the Log in link goes to the sign-in page")


class SignedInVisitTests(PageCase):
    longMessage = False

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("member", email="member@example.com", password=PASSWORD)
        self.client.force_login(self.user)

    def test_a_signed_in_visit_goes_to_the_dashboard(self):
        for path in (LOGIN, SIGNUP, SIGNUP + "?plan=free", SIGNUP + "?plan=bogus"):
            with self.subTest(path=path):
                self.assertSentTo(self.client.get(path), "/app/",
                                  f"{path}: backend/drop_core/views.py (SignInView and signup) sends a signed-in visitor "
                                  "to signed_in_destination()")

    def test_a_signed_in_visit_goes_to_a_safe_next(self):
        for path in (LOGIN, SIGNUP):
            for target in (DEVICE_LINK, "/o/authorize/?client_id=abc&scope=mcp%3Ause", "/app/files/"):
                with self.subTest(path=path, target=target):
                    self.assertSentTo(self.client.get(path, {"next": target}), target,
                                      f"{path}: a signed-in visitor goes on to a safe ?next= (signed_in_destination() in "
                                      "backend/drop_core/views.py)")

    def test_a_next_that_leaves_the_site_is_not_followed(self):
        for path in (LOGIN, SIGNUP):
            for target in ("https://evil.example/", "//evil.example/", "/\\evil.example", "javascript:alert(1)", "",
                           "http:evil.example"):
                with self.subTest(path=path, target=target):
                    self.assertSentTo(self.client.get(path, {"next": target}), "/app/",
                                      "backend/drop_core/views.py: safe_next_url() keeps only an address on this site")

    def test_a_next_that_is_the_page_itself_is_not_followed(self):
        for path in (LOGIN, SIGNUP):
            with self.subTest(path=path):
                message = (f"{path}: signed_in_destination() in backend/drop_core/views.py must not send a visitor "
                           "back to the page they asked for")
                self.assertSentTo(self.client.get(path, {"next": path}), "/app/", message)
                self.assertSentTo(self.client.get(path, {"next": path + "?next=/app/files/"}), "/app/", message)

    def test_a_head_request_is_sent_on_too(self):
        self.assertSentTo(self.client.head(LOGIN), "/app/", "SignInView.get answers HEAD as well (django's View.head is get)")

    def test_a_picked_paid_plan_still_shows_the_checkout_step(self):
        for plan in ("team", "scale"):
            with self.subTest(plan=plan):
                response = self.client.get(f"{SIGNUP}?plan={plan}")
                self.assertEqual(response.status_code, 200)
                self.assertIn("registration/checkout_confirm.html", [template.name for template in response.templates])

    def test_a_signed_in_post_to_sign_in_signs_in_as_the_account_named(self):
        other = User.objects.create_user("other", password=PASSWORD)
        response = self.client.post(LOGIN, {"username": "other", "password": PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/app/")
        self.assertEqual(int(self.client.session["_auth_user_id"]), other.pk)

    def test_a_signed_in_post_counts_toward_the_lock_like_any_other(self):
        # throttle_login reads a redirect as a successful sign-in and clears that account's count. If a signed-in POST were
        # answered with a redirect, whoever holds an account could clear the count of any other by naming it.
        User.objects.create_user("victim", password=PASSWORD)
        for i in range(9):
            Client().post(LOGIN, {"username": "victim", "password": "wrong"}, REMOTE_ADDR=f"198.51.100.{i}")
        response = self.client.post(LOGIN, {"username": "victim", "password": "wrong"})
        self.assertEqual(response.status_code, 200,
                         "backend/drop_core/views.py: SignInView redirects a signed-in GET only; a POST is a sign-in attempt")
        locked = Client().post(LOGIN, {"username": "victim", "password": PASSWORD})
        self.assertEqual(locked.status_code, 429, "the tenth failure locks the account, whoever made it")

    def test_a_signed_in_post_to_sign_up_still_makes_the_account(self):
        response = self.client.post(SIGNUP, {"username": "second", "password1": "s3cur3pass!!",
                                             "password2": "s3cur3pass!!", "email": "second@example.com"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="second").exists())


class SignUpReturnsToNextTests(PageCase):
    longMessage = False

    def post(self, query, **extra):
        data = {"username": "newcomer", "password1": "s3cur3pass!!", "password2": "s3cur3pass!!", "email": "newcomer@example.com"}
        return Client().post(SIGNUP + query, data, **extra)

    def test_a_safe_next_is_where_a_new_account_goes(self):
        for target in (DEVICE_LINK, "/app/files/"):
            with self.subTest(target=target):
                User.objects.filter(username="newcomer").delete()
                response = self.post("?" + urlencode({"next": target}))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], target,
                                 "backend/drop_core/views.py: a free sign-up goes to the safe next, so that a device or OAuth trip is not lost")

    def test_an_unsafe_next_is_not_followed(self):
        for target in ("https://evil.example/", "//evil.example/", "javascript:alert(1)"):
            with self.subTest(target=target):
                User.objects.filter(username="newcomer").delete()
                self.assertEqual(self.post("?" + urlencode({"next": target}))["Location"], "/app/",
                                 "backend/drop_core/views.py: safe_next_url() keeps only an address on this site")

    def test_no_next_still_goes_to_the_dashboard(self):
        self.assertEqual(self.post("")["Location"], "/app/", "backend/drop_core/views.py: a free sign-up with no next goes to the dashboard")

    def test_a_paid_plan_goes_to_checkout_not_next(self):
        from drop_billing.tests import TEST_ENV, make_stripe_mock

        with mock.patch.dict(os.environ, TEST_ENV, clear=False), mock.patch("drop_billing.views.stripe", make_stripe_mock()):
            response = self.post("?" + urlencode({"plan": "team", "next": DEVICE_LINK}))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://checkout.stripe.com/"),
                        "backend/drop_core/views.py: a paid plan's sign-up goes to its checkout; next is for the free path")


class ReturnToTheNextPageTests(PageCase):
    """The trip a CLI or an MCP client starts: its page needs a sign-in, the visitor signs in or signs up, and the page is
    where they land (spec 4.7)."""
    longMessage = False

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("returning", email="returning@example.com", password=PASSWORD)
        get_or_create_workspace(self.user)
        EmailVerification.objects.create(user=self.user, email="returning@example.com", is_verified=True,
                                         verified_at=timezone.now())

    def device_link(self):
        grant = Client().post("/api/v1/device/code", {}, content_type="application/json").json()
        return f"/device?user_code={grant['user_code']}"

    def authorize_link(self):
        registration = Client().post("/o/register/", data=json.dumps({
            "client_name": "Round trip client",
            "redirect_uris": ["http://127.0.0.1:9876/callback"],
            "grant_types": ["authorization_code"],
            "token_endpoint_auth_method": "none",
        }), content_type="application/json")
        self.assertEqual(registration.status_code, 201)
        return "/o/authorize/?" + urlencode({
            "response_type": "code", "client_id": registration.json()["client_id"],
            "redirect_uri": "http://127.0.0.1:9876/callback", "scope": OAUTH_SCOPE_MCP_USE,
            "state": "round-trip", "code_challenge": pkce_challenge(), "code_challenge_method": "S256",
        })

    def sign_in_trip(self, target):
        browser = Client()
        bounce = browser.get(target)
        self.assertEqual(bounce.status_code, 302)
        self.assertEqual(urlsplit(bounce["Location"]).path, LOGIN)
        self.assertEqual(next_of(bounce["Location"]), target)
        page = browser.get(bounce["Location"])
        self.assertEqual(page.status_code, 200)
        self.assertEqual(next_of(self.parse(page).href("Sign up")), target,
                         "backend/templates/registration/login.html: the Sign up link carries next")
        done = browser.post(bounce["Location"], {"username": "returning", "password": PASSWORD})
        self.assertEqual(done.status_code, 302)
        self.assertEqual(done["Location"], target,
                         "signing in must land on the page that asked for it, query string and all")
        self.assertEqual(browser.get(target).status_code, 200)

    def sign_up_trip(self, target):
        browser = Client()
        login_url = browser.get(target)["Location"]
        signup_url = self.parse(browser.get(login_url)).href("Sign up")
        signup_page = browser.get(signup_url)
        self.assertEqual(signup_page.status_code, 200)
        self.assertEqual(next_of(self.parse(signup_page).href("Log in")), target,
                         "backend/templates/registration/signup.html: the Log in link carries next, so the way back keeps it")
        done = browser.post(signup_url, {"username": "newcomer", "password1": "s3cur3pass!!",
                                         "password2": "s3cur3pass!!", "email": "newcomer@example.com"})
        self.assertEqual(done.status_code, 302)
        self.assertEqual(done["Location"], target,
                         "signing up must land on the page that asked for it (backend/drop_core/views.py, signup)")

    def test_the_device_page_survives_signing_in(self):
        self.sign_in_trip(self.device_link())

    def test_the_device_page_survives_signing_up(self):
        self.sign_up_trip(self.device_link())

    def test_oauth_consent_survives_signing_in(self):
        self.sign_in_trip(self.authorize_link())

    def test_oauth_consent_survives_signing_up(self):
        self.sign_up_trip(self.authorize_link())


class ThrottledPageTests(PageCase):
    longMessage = False

    def setUp(self):
        super().setUp()
        User.objects.create_user("victim", email="victim@example.com", password=PASSWORD)

    def locked(self, visitor=None, name="victim"):
        self.lock_out("victim", "no-such-user")
        return (visitor or Client()).post(LOGIN, {"username": name, "password": "nope"})

    def test_it_is_a_429_on_the_form_layout_with_the_retry_header(self):
        response = self.locked()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "900")
        self.assertIn("layouts/form.html", [template.name for template in response.templates],
                      "backend/templates/registration/throttled.html must extend layouts/form.html")
        page = self.parse(response)
        self.assertEqual(page.headings, ["Too many sign-in attempts"])
        self.assertIn("<title>Too many sign-in attempts | Veilfile</title>", page.html,
                      "backend/templates/registration/throttled.html: {% block title %} is the title, then | Veilfile")
        said = main_of(page.html)
        self.assertIn("Sign-in is paused for this account or network after repeated failed attempts.", said,
                      "the message is the throttle's (backend/drop_core/throttle.py)")
        self.assertIn("Wait about 15 minutes, then try again.", said,
                      "backend/templates/registration/throttled.html says how long to wait, from retry_wait (drop_core/throttle.py: wait_in_words)")
        self.assertEqual(page.href("Log in"), LOGIN)
        self.assertEqual(page.href("Forgot password?"), "/accounts/password_reset/")
        self.assertEqual(len(page.alerts), 1, "the message is announced as an alert, once")

    def test_a_real_and_a_missing_account_get_the_same_page(self):
        visitors = {"signed out": Client(), "signed in": Client()}
        visitors["signed in"].force_login(User.objects.create_user("bystander", email="bystander@example.com", password=PASSWORD))
        for label, visitor in visitors.items():
            with self.subTest(visitor=label):
                cache.clear()
                self.lock_out("victim", "no-such-user")
                real = visitor.post(LOGIN, {"username": "victim", "password": "nope"})
                fake = visitor.post(LOGIN, {"username": "no-such-user", "password": "nope"})
                self.assertEqual((real.status_code, fake.status_code), (429, 429))
                self.assertEqual(real.content, fake.content,
                                 "backend/templates/registration/throttled.html: two addresses must get the same bytes, so "
                                 "the page can say nothing of the account asked about")
                self.assertEqual(sorted(real.items()), sorted(fake.items()), "headers must match too")

    def test_it_carries_no_token_and_nothing_of_the_visitor(self):
        visitor = Client()
        visitor.force_login(User.objects.create_user("bystander", email="bystander@example.com", password=PASSWORD))
        response = self.locked(visitor)
        html = response.content.decode()
        self.assertEqual(response.status_code, 429)
        for private in ("csrfmiddlewaretoken", "bystander", "bystander@example.com", "victim", "no-such-user"):
            self.assertNotIn(private, html, f"backend/templates/registration/throttled.html shows {private!r}")
        self.assertNotIn("csrftoken", response.cookies, "a throttled page sets no CSRF cookie: it has no form")
        self.assertEqual(self.parse(response).forms, [], "a throttled page has no form")

    def test_a_signed_in_asker_sees_the_same_page_but_for_the_shells_menu(self):
        # The page is shown to a signed-in visitor too (a wrong password on the email form or the password form, or a
        # sign-in posted while signed in). Only the shell's menu may tell them from a signed-out one: Dashboard for Sign in.
        member = Client()
        member.force_login(User.objects.create_user("bystander", email="bystander@example.com", password=PASSWORD))
        self.lock_out("victim")
        signed_out = Client().post(LOGIN, {"username": "victim", "password": "nope"}).content.decode()
        signed_in = member.post(LOGIN, {"username": "victim", "password": "nope"}).content.decode()
        without_menu = lambda html: re.sub(r'<nav class="menu".*?</nav>', "", html, flags=re.S)
        self.assertNotEqual(signed_out, signed_in, "the menu says Dashboard to a signed-in visitor and Sign in to the rest")
        self.assertEqual(without_menu(signed_out), without_menu(signed_in),
                         "backend/templates/registration/throttled.html: nothing but the shell's menu may differ between a "
                         "signed-in and a signed-out visitor")
        self.assertIn(">Dashboard</a>", signed_in, "the shell's menu shows a signed-in visitor Dashboard")
        self.assertNotIn(">Sign in</a>", signed_in, "the shell's menu does not offer a signed-in visitor Sign in")

    def test_the_template_reads_nothing_of_the_visitor_or_the_request(self):
        source = (TEMPLATES / "registration" / "throttled.html").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\b(user|request|csrf\w*)\b", source),
                          "backend/templates/registration/throttled.html reads the user, the request or the CSRF token: the "
                          "same page is served for any address asked about, so it may use only what the throttle fixes for a kind of "
                          "refusal (title, message, the wait), never the user, the request or a token")

    def test_the_admin_sign_in_gets_the_same_page(self):
        User.objects.create_superuser("boss", "boss@example.com", PASSWORD)
        for _ in range(10):
            self.client.post("/admin/login/", {"username": "boss", "password": "nope", "next": "/admin/"})
        response = self.client.post("/admin/login/", {"username": "boss", "password": PASSWORD, "next": "/admin/"})
        self.assertEqual(response.status_code, 429)
        self.assertIn("layouts/form.html", [template.name for template in response.templates],
                      "backend/templates/registration/throttled.html must extend layouts/form.html")
        self.assertEqual(self.parse(response).headings, ["Too many sign-in attempts"])

    def test_a_password_reset_limit_uses_the_same_page(self):
        for _ in range(3):
            self.client.post("/accounts/password_reset/", {"email": "victim@example.com"})
        response = self.client.post("/accounts/password_reset/", {"email": "victim@example.com"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "3600")
        page = self.parse(response)
        self.assertEqual(page.headings, ["Too many password reset requests"])
        self.assertIn("Wait about 60 minutes, then try again.", main_of(page.html),
                      "backend/templates/registration/throttled.html says how long to wait, from retry_wait (drop_core/throttle.py: wait_in_words)")


class CheckoutConfirmPageTests(PageCase):
    longMessage = False

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("payer", email="payer@example.com", password=PASSWORD)
        self.client.force_login(self.user)

    def test_it_names_the_plan_and_its_price_from_plans_py(self):
        for plan in ("team", "scale"):
            with self.subTest(plan=plan):
                response = self.client.get(f"{SIGNUP}?plan={plan}")
                self.assertEqual(response.status_code, 200)
                page = self.parse(response)
                self.assertEqual(page.headings,
                                 [f"Subscribe to the {plan.title()} plan (${PLANS[plan]['price_usd']}/mo, tax included)."])
                self.assertIn("<title>Checkout | Veilfile</title>", page.html,
                              "backend/templates/registration/checkout_confirm.html: {% block title %} is Checkout, then | Veilfile")

    def test_it_asks_before_anything_is_charged(self):
        page = self.parse(self.client.get(f"{SIGNUP}?plan=team"))
        self.assertIn("You're signed in as payer. Checkout opens on Stripe, and nothing is charged until you confirm there.",
                      main_of(page.html).replace("&#x27;", "'"),
                      "backend/templates/registration/checkout_confirm.html says who is signed in and that nothing is charged yet")
        self.assertEqual(len(page.forms), 1)
        self.assertEqual(page.forms[0].get("method", "").lower(), "post")
        self.assertNotIn("action", page.forms[0])
        self.assertTrue(any(attrs.get("name") == "csrfmiddlewaretoken" for attrs in page.inputs),
                        "backend/templates/registration/checkout_confirm.html needs {% csrf_token %} inside the form")
        self.assertEqual([text for _attrs, text in page.buttons if text == "Continue to checkout"], ["Continue to checkout"])
        self.assertEqual(page.href("Go to your dashboard"), "/app/")

    def test_it_is_kept_out_of_search_results(self):
        html = self.client.get(f"{SIGNUP}?plan=team").content.decode()
        self.assertIn('<meta name="robots" content="noindex">', html,
                      "backend/templates/registration/checkout_confirm.html: {% block meta %} carries the noindex tag")

    def test_the_username_is_escaped(self):
        hostile = User.objects.create_user("<b>x</b>", email="hostile@example.com", password=PASSWORD)
        self.client.force_login(hostile)
        html = self.client.get(f"{SIGNUP}?plan=team").content.decode()
        self.assertNotIn("<b>x</b>", html, "backend/templates/registration/checkout_confirm.html must escape the username")
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html, "the escaped username is on the page")


class SignOutTests(PageCase):
    longMessage = False

    def test_signing_out_goes_to_the_sign_in_page_and_draws_no_page(self):
        self.client.force_login(User.objects.create_user("leaver", password=PASSWORD))
        response = self.client.post("/accounts/logout/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], LOGIN,
                         "backend/config/settings.py: LOGOUT_REDIRECT_URL sends every sign-out to the sign-in page")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_hand_made_post_that_names_the_logout_page_as_its_next_still_gets_a_page(self):
        # LogoutView draws a "Logged out" page, not a redirect, only when the address to go to is the logout address itself.
        # No form on the site sends that; with the project's page gone, Django's own admin page answers it.
        self.client.force_login(User.objects.create_user("leaver", password=PASSWORD))
        response = self.client.post("/accounts/logout/", {"next": "/accounts/logout/"})
        self.assertEqual(response.status_code, 200,
                         "a POST to /accounts/logout/ that names itself as next must be answered with a page, not an error")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_the_old_logged_out_page_is_gone(self):
        self.assertFalse((TEMPLATES / "registration" / "logged_out.html").exists(),
                         "backend/templates/registration/logged_out.html was removed on purpose: LOGOUT_REDIRECT_URL "
                         "redirects every sign-out, so nothing draws it, and it carried the old includes")
