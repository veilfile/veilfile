"""The password reset and email verification pages on the form layout (spec 4.7).

Seven templates in backend/templates/registration/ (password_reset_form, password_reset_done, password_reset_confirm,
password_reset_complete, verify_email, verify_email_done, verify_email_invalid), seventeen states of them. Every state
is rendered by its real view and held to the same rules: a shell page on the form layout with one h1 and a title that
names it, a labelled input for every field, errors tied to their field, a CSRF token in every form, and the words, links and
field names the other tests and the visitors rely on. The mail templates (password_reset_email.html, verify_email_email.txt)
are not pages and are not under these rules.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it). A failed assertIn on a whole page
would otherwise print the page before the message that names the file and the fix; with it off only the message prints,
so each message here also says what was found.
"""
import logging
import re
from html.parser import HTMLParser
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from drop_core.email_verification import make_verification_token
from drop_core.forms import EmailUpdateForm
from drop_core.models import EmailVerification

User = get_user_model()
PASSWORD = "pw12345678"
NEW_PASSWORD = "a-new-pw-98765432"
WRONG_PASSWORD = "wrong-pw-24681357"
FILES = "backend/templates/registration/"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

RESET_FORM = ("reset form", "reset form, bad email", "reset form, empty email")
CONFIRM_FORM = ("reset confirm", "reset confirm, mismatch")
VERIFY_PENDING = ("verify, pending", "verify, link sent", "verify, wrong password", "verify, address taken")

# state -> (template, the page's name). The title is "<name> | Veilfile", as every page of the new site has, and the name is the h1.
PAGES = {
    **{state: ("password_reset_form.html", "Reset password") for state in RESET_FORM},
    "reset done": ("password_reset_done.html", "Check your inbox"),
    **{state: ("password_reset_confirm.html", "Choose a new password") for state in (*CONFIRM_FORM, "reset confirm, dead link")},
    **{state: ("password_reset_complete.html", "Password updated") for state in ("reset complete, signed in", "reset complete, signed out")},
    **{state: ("verify_email.html", "Verify email") for state in ("verify, no address yet", *VERIFY_PENDING, "verify, verified")},
    "verify link works": ("verify_email_done.html", "Email verified"),
    "verify link dead": ("verify_email_invalid.html", "Link not valid"),
}

# The inputs of each state that has any, in document order, without the CSRF token: (name, type, value of a hidden one).
RESET_INPUTS = [("email", "email", None)]
CONFIRM_INPUTS = [("new_password1", "password", None), ("new_password2", "password", None)]
VERIFY_FORM = [("action", "hidden", "update_email"), ("email", "email", None), ("password", "password", None)]
INPUTS = {
    **{state: RESET_INPUTS for state in RESET_FORM},
    **{state: CONFIRM_INPUTS for state in CONFIRM_FORM},
    "verify, no address yet": VERIFY_FORM,
    **{state: VERIFY_FORM + [("action", "hidden", "resend")] for state in VERIFY_PENDING},
}
TOKENS = {"id_email": "email", "id_new_password1": "new-password", "id_new_password2": "new-password", "id_password": "current-password"}
LABELS = {"id_email": "Email", "id_new_password1": "New password", "id_new_password2": "Confirm new password",
          "id_password": "Current password"}
AUTOFOCUS = {**{state: "id_email" for state in RESET_FORM}, **{state: "id_new_password1" for state in CONFIRM_FORM}}
ERRORS = {  # state -> {input id: the sentence tied to it}
    "reset form, bad email": {"id_email": "Enter a valid email address."},
    "reset form, empty email": {"id_email": "This field is required."},
    "reset confirm, mismatch": {"id_new_password2": "The two password fields didn’t match."},
    "verify, wrong password": {"id_password": "Your current password is incorrect."},
    "verify, address taken": {"id_email": "This email address can't be used. Try another."},
}
# The one hairline-box action of each state that has one. Every other control on the page is a quiet label.
MAIN_ACTION = {
    **{state: "Send reset link" for state in RESET_FORM},
    **{state: "Set new password" for state in CONFIRM_FORM},
    "reset confirm, dead link": "Request a new one",
    "reset complete, signed in": "Go to your dashboard",
    "reset complete, signed out": "Log in",
    "verify, no address yet": "Send verification link",
    **{state: "Update and resend link" for state in VERIFY_PENDING},
    "verify, verified": "Back to dashboard",
    "verify link works": "Go to dashboard",
    "verify link dead": "Request a new link",
}
BUTTONS = {  # the buttons inside <main>
    **{state: ["Send reset link"] for state in RESET_FORM},
    **{state: ["Set new password"] for state in CONFIRM_FORM},
    "verify, no address yet": ["Send verification link"],
    **{state: ["Update and resend link", "Resend verification link"] for state in VERIFY_PENDING},
}
LINKS = {  # the links inside <main>: (text, href)
    **{state: [("Back to log in", "/accounts/login/")] for state in (*RESET_FORM, "reset done")},
    "reset confirm, dead link": [("Request a new one", "/accounts/password_reset/")],
    "reset complete, signed in": [("Go to your dashboard", "/app/")],
    "reset complete, signed out": [("Log in", "/accounts/login/")],
    **{state: [("Back to dashboard", "/app/")] for state in ("verify, no address yet", *VERIFY_PENDING, "verify, verified")},
    "verify link works": [("Go to dashboard", "/app/")],
    "verify link dead": [("Request a new link", "/accounts/verify-email/")],
}
WORDS = {  # the sentences each state says, word for word
    "reset form": ["Enter the email on your account. We will send a reset link."],
    "reset done": ["If an account exists for that email, a reset link is on its way. It expires in 3 days."],
    "reset confirm, dead link": ["This reset link is invalid or has expired."],
    "reset complete, signed in": [
        "Your password is set, and you are signed in. Other sessions were signed out and connected apps were disconnected."],
    "reset complete, signed out": ["Your password is set. Log in with the new one."],
    "verify, no address yet": [
        "Add an email address to your account. We will send a verification link; API key creation stays locked until you verify."],
    "verify, pending": ["We sent a verification link to pending-verify@example.com. Click it to verify. "
                        "API key creation stays locked until you do."],
    "verify, link sent": ["We sent the verification link. Check your inbox."],
    "verify, verified": ["Your email address is verified."],
    "verify link works": ["Your email address is verified. You can now create API keys."],
    "verify link dead": ["This verification link is invalid or has expired."],
}


class Tree(HTMLParser):
    """Every element of a page in document order, with its attributes, its ancestors and all the text inside it."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.stack, self.elements = [], []
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        element = {"tag": tag, "attrs": dict(attrs), "parents": tuple(self.stack), "text": ""}
        self.elements.append(element)
        if tag not in VOID:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.elements.append({"tag": tag, "attrs": dict(attrs), "parents": tuple(self.stack), "text": ""})

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for element in self.stack:
            element["text"] += data

    def all(self, tag):
        return [element for element in self.elements if element["tag"] == tag]

    def ids(self):
        return {element["attrs"]["id"]: element for element in self.elements if "id" in element["attrs"]}

    def announced(self, role):
        """The words of each element of <main> with this role."""
        return [words(e) for e in self.elements if in_main(e) and e["attrs"].get("role") == role]


def words(element):
    """The text inside an element with its white space made single, as a reader sees it."""
    return " ".join(element["text"].split())


def inside(element, ancestor):
    return any(parent is ancestor for parent in element["parents"])


def in_main(element):
    return any(parent["tag"] == "main" for parent in element["parents"])


def visible_inputs(tree):
    return [e for e in tree.all("input") if e["attrs"].get("type") != "hidden"]


def sign_in(user):
    client = Client()
    client.force_login(user)
    return client


class PageTestCase(TestCase):
    longMessage = False

    def setUp(self):
        super().setUp()
        cache.clear()  # the throttle's counters live in the cache
        self.addCleanup(cache.clear)
        throttle_logger = logging.getLogger("drop_core.throttle")
        self.addCleanup(throttle_logger.setLevel, throttle_logger.level)
        throttle_logger.setLevel(logging.ERROR)


class EveryStateTests(PageTestCase):
    """Each of the seventeen states, rendered by its real view."""

    @classmethod
    def setUpTestData(cls):
        cache.clear()
        cls.html, cls.status = {}, {}

        def keep(state, response):
            cls.html[state] = response.content.decode()
            cls.status[state] = response.status_code

        # Password reset. The link is made as the view mails it; following it stores the token in the session.
        resetter = User.objects.create_user(username="resetpages", email="resetpages@example.com")
        anonymous = Client()
        keep("reset form", anonymous.get("/accounts/password_reset/"))
        keep("reset form, bad email", anonymous.post("/accounts/password_reset/", {"email": "not-an-email"}))
        keep("reset form, empty email", anonymous.post("/accounts/password_reset/", {"email": ""}))
        keep("reset done", anonymous.get("/accounts/password_reset/done/"))
        link = "/accounts/reset/{}/{}/".format(
            urlsafe_base64_encode(force_bytes(resetter.pk)), default_token_generator.make_token(resetter))
        first = anonymous.get(link, follow=True)
        keep("reset confirm", first)
        set_password = first.redirect_chain[-1][0]
        keep("reset confirm, mismatch", anonymous.post(
            set_password, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD + "-other"}))
        keep("reset confirm, dead link", Client().get("/accounts/reset/NA/bogus-token/", follow=True))
        keep("reset complete, signed in", anonymous.post(
            set_password, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD}, follow=True))
        keep("reset complete, signed out", Client().get("/accounts/reset/done/"))

        # Email verification.
        legacy = User.objects.create_user(username="legacy-verify")  # no address and no record
        keep("verify, no address yet", sign_in(legacy).get("/accounts/verify-email/"))
        pending = User.objects.create_user(username="pending-verify", email="pending-verify@example.com", password=PASSWORD)
        EmailVerification.objects.create(user=pending, email=pending.email)
        owner = sign_in(pending)
        keep("verify, pending", owner.get("/accounts/verify-email/"))
        keep("verify, link sent", owner.get("/accounts/verify-email/?sent=1"))
        keep("verify, wrong password", owner.post("/accounts/verify-email/", {
            "action": "update_email", "email": "newer@example.com", "password": WRONG_PASSWORD}))
        User.objects.create_user(username="holds-address", email="held@example.com")
        keep("verify, address taken", owner.post("/accounts/verify-email/", {
            "action": "update_email", "email": "held@example.com", "password": PASSWORD}))
        done = User.objects.create_user(username="done-verify", email="done-verify@example.com")
        EmailVerification.objects.create(user=done, email=done.email, is_verified=True, verified_at=timezone.now())
        keep("verify, verified", sign_in(done).get("/accounts/verify-email/"))
        linked = User.objects.create_user(username="link-verify", email="link-verify@example.com")
        EmailVerification.objects.create(user=linked, email=linked.email)
        keep("verify link works", Client().get(f"/accounts/verify-email/{make_verification_token(linked, linked.email)}/"))
        keep("verify link dead", Client().get("/accounts/verify-email/bogus-token/"))
        cache.clear()

    def states(self):
        """(state, the template's path for messages, the page's name, its HTML, its parsed tree) for each state."""
        return [(state, FILES + template, name, self.html[state], Tree(self.html[state]))
                for state, (template, name) in PAGES.items()]

    def test_every_state_answers_200(self):
        for state, status in self.status.items():
            with self.subTest(state=state):
                self.assertEqual(status, 200, f"{FILES}{PAGES[state][0]}: the '{state}' state answers {status}, not 200; "
                                              "a page keeps its view's status code")

    def test_every_state_is_a_page_of_the_shell_on_the_form_layout(self):
        for state, where, _name, page, _tree in self.states():
            with self.subTest(state=state):
                self.assertIn('<a class="skip-link label" href="#main">', page,
                              f"{where}: extend layouts/form.html, which extends shell.html; the shell brings the skip link")
                self.assertRegex(page, r'<main id="main" class="shell-main">\s*<div class="form-page">',
                                 f"{where}: the page's words go in {{% block form_page %}} of layouts/form.html")
                self.assertRegex(page, r'<body class="[^"]*\blayout-form\b', f"{where}: the form layout gives the body the class layout-form")
                self.assertNotIn("data-layout", page, f"{where}: a form page has no registry layout, so it makes no in-place changes")

    def test_no_old_chrome_is_left_on_any_state(self):
        old = ('<style', 'id="veilfile-theme-boot"', 'site-menu-wrap', 'class="ambient"', 'class="brand"', 'class="card"',
               'class="errors"', 'class="ok"', 'class="foot"', 'class="sub"', 'class="skip-link"', '{#', '#}')
        for state, where, _name, page, _tree in self.states():
            with self.subTest(state=state):
                for text in old:
                    self.assertNotIn(text, page, f"{where}: found {text!r}, which belongs to the old standalone pages. "
                                                 "Fix: extend layouts/form.html and use components/field.html and form_errors.html")

    def test_one_h1_inside_main_named_as_the_title_and_nothing_main_may_not_hold(self):
        for state, where, name, _page, tree in self.states():
            with self.subTest(state=state):
                h1s = tree.all("h1")
                self.assertEqual(len(h1s), 1, f"{where}: a page has exactly one h1, found {len(h1s)}")
                self.assertTrue(in_main(h1s[0]), f"{where}: the h1 belongs inside <main>")
                self.assertEqual(words(h1s[0]), name, f"{where}: the h1 is the page's name, {name!r}")
                for element in tree.elements:
                    if element["tag"] in ("header", "footer", "script"):
                        self.assertFalse(in_main(element), f"{where}: no <{element['tag']}> inside <main>")

    def test_every_state_is_titled_with_its_name(self):
        for state, where, name, page, _tree in self.states():
            with self.subTest(state=state):
                found = re.search(r"<title>(.*?)</title>", page, re.S).group(1)
                self.assertEqual(found, f"{name} | Veilfile", f"{where}: the title is {name!r} and ' | Veilfile' (no em dash: the voice rules), found {found!r}")

    def test_every_page_but_the_reset_form_is_kept_out_of_search(self):
        noindex = '<meta name="robots" content="noindex">'
        for state, where, _name, page, _tree in self.states():
            with self.subTest(state=state):
                if state.startswith("reset form"):
                    self.assertNotIn(noindex, page, f"{where}: the reset form is a plain public form and takes no noindex")
                else:
                    self.assertIn(noindex, page, f"{where}: the page holds a token, an address or a one-time state; "
                                                 "add {% block meta %}" + noindex + "{% endblock %}")

    def test_every_form_posts_to_its_own_address_with_its_own_csrf_token(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                forms = tree.all("form")
                wanted = 2 if state in VERIFY_PENDING else (1 if state in INPUTS else 0)
                self.assertEqual(len(forms), wanted, f"{where}: this state has {wanted} forms, found {len(forms)}")
                for form in forms:
                    self.assertEqual(form["attrs"].get("method"), "post", f"{where}: a form that changes state posts")
                    self.assertNotIn("action", form["attrs"],
                                     f"{where}: no action attribute; the form posts to the address it came from, "
                                     "which for the reset link is /accounts/reset/<uid>/set-password/")
                    tokens = [e for e in tree.all("input") if inside(e, form) and e["attrs"].get("name") == "csrfmiddlewaretoken"]
                    self.assertEqual(len(tokens), 1, f"{where}: every POST form holds {{% csrf_token %}} itself, "
                                                     f"never through a component; found {len(tokens)}")

    def test_ids_are_unique_on_every_state(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                seen = [e["attrs"]["id"] for e in tree.elements if "id" in e["attrs"]]
                repeated = sorted({name for name in seen if seen.count(name) > 1})
                self.assertEqual(repeated, [], f"{where}: these ids are used twice: {repeated}. A label's `for`, aria-describedby and the "
                                               "shell's own ids need one element each")

    def test_the_fields_keep_their_names_types_and_hidden_values(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                found = [(e["attrs"].get("name"), e["attrs"].get("type"),
                          e["attrs"].get("value") if e["attrs"].get("type") == "hidden" else None)
                         for e in tree.all("input") if e["attrs"].get("name") != "csrfmiddlewaretoken"]
                self.assertEqual(found, INPUTS.get(state, []),
                                 f"{where}: the views read these field names and these hidden values; found {found}")

    def test_every_input_has_one_visible_label_with_its_words(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                for field in visible_inputs(tree):
                    name = field["attrs"].get("id")
                    labels = [label for label in tree.all("label") if label["attrs"].get("for") == name]
                    self.assertEqual(len(labels), 1, f"{where}: the input {name!r} needs exactly one <label for>, found {len(labels)}")
                    self.assertTrue(words(labels[0]).startswith(LABELS[name]),
                                    f"{where}: the label of {name} reads {LABELS[name]!r}, found {words(labels[0])!r}")

    def test_autocomplete_tokens_are_kept(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                for field in visible_inputs(tree):
                    name = field["attrs"].get("id")
                    self.assertEqual(field["attrs"].get("autocomplete"), TOKENS[name],
                                     f"{where}: {name} keeps autocomplete={TOKENS[name]!r} (password managers, and the tests count them)")

    def test_autofocus_is_the_first_field_in_error_or_the_one_input_a_page_exists_to_take(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                focused = [e["attrs"].get("id") for e in tree.elements if "autofocus" in e["attrs"]]
                in_error = [e["attrs"].get("id") for e in visible_inputs(tree) if e["attrs"].get("id") in ERRORS.get(state, {})]
                wanted = in_error[:1] or ([AUTOFOCUS[state]] if state in AUTOFOCUS else [])
                self.assertEqual(focused, wanted,
                                 f"{where}: a page sent back with an error puts autofocus on the first field in error (components/field.html `focus`, "
                                 "{% focus_field %} in the template), and otherwise only the reset form and the new-password form focus their first input; "
                                 f"found {focused}, wanted {wanted}")

    def test_after_a_mismatch_focus_is_on_the_confirmation_not_on_the_first_password(self):
        # Django reports a mismatch (and a weak password) on the second field, where the page's usual focus is on the first.
        tree = Tree(self.html["reset confirm, mismatch"])
        focused = [e["attrs"].get("id") for e in tree.elements if "autofocus" in e["attrs"]]
        self.assertEqual(focused, ["id_new_password2"],
                         f"{FILES}password_reset_confirm.html: the field in error takes focus (focus_field), replacing the page's usual autofocus on id_new_password1; found {focused}")

    def test_a_field_error_is_tied_to_its_field_and_a_clean_field_is_not_marked(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                ids = tree.ids()
                for element in tree.elements:
                    for token in element["attrs"].get("aria-describedby", "").split():
                        self.assertIn(token, ids, f"{where}: aria-describedby names {token!r}, which is no id on the page")
                invalid = {e["attrs"].get("id"): e for e in visible_inputs(tree) if e["attrs"].get("aria-invalid") == "true"}
                wanted = ERRORS.get(state, {})
                self.assertEqual(set(invalid), set(wanted),
                                 f"{where}: aria-invalid=\"true\" belongs on the fields with an error ({sorted(wanted)}), found {sorted(invalid)}")
                for name, sentence in wanted.items():
                    described = " ".join(words(ids[token]) for token in invalid[name]["attrs"].get("aria-describedby", "").split())
                    self.assertIn(sentence, described,
                                  f"{where}: the error under {name} is {sentence!r} and aria-describedby must point at it; it points at {described!r}")

    def test_a_typed_password_is_never_sent_back(self):
        for state, typed in (("reset confirm, mismatch", (NEW_PASSWORD, NEW_PASSWORD + "-other")), ("verify, wrong password", (WRONG_PASSWORD,))):
            for secret in typed:
                with self.subTest(state=state):
                    self.assertNotIn(secret, self.html[state], f"{FILES}{PAGES[state][0]}: a password field never gets a value attribute")

    def test_the_pages_say_what_they_said_before(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                text = words(tree.all("main")[0])
                for sentence in WORDS.get(state, []):
                    self.assertIn(sentence, text, f"{where}: keep the sentence {sentence!r}")

    def test_the_buttons_and_links_keep_their_words_and_addresses(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                buttons = [words(e) for e in tree.all("button") if in_main(e)]
                self.assertEqual(buttons, BUTTONS.get(state, []), f"{where}: the buttons in <main> read {buttons}")
                links = [(words(e), e["attrs"].get("href")) for e in tree.all("a") if in_main(e)]
                self.assertEqual(links, LINKS.get(state, []), f"{where}: the links in <main> are {links}")

    def test_each_page_has_at_most_one_main_action_and_every_other_control_is_a_quiet_label(self):
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                actions = [e for e in tree.elements if in_main(e) and "action" in e["attrs"].get("class", "").split()]
                found = [words(e) for e in actions]
                wanted = [MAIN_ACTION[state]] if state in MAIN_ACTION else []
                self.assertEqual(found, wanted,
                                 f"{where}: the page's one main action is the hairline box (class=\"action interactive\"); "
                                 f"every other link and button, such as the Resend button, is a plain `label interactive`. Found {found}, wanted {wanted}")
                for action in actions:
                    classes = action["attrs"].get("class", "").split()
                    self.assertEqual(classes, ["action", "interactive"],
                                     f"{where}: the main action {words(action)!r} is exactly class=\"action interactive\" (`interactive` brings the ink wash "
                                     f"and the press fill; a `label` class would add a bare label's padding and negative margin); found {classes}")

    def test_every_link_and_button_takes_the_ink_wash(self):
        # Spec 3.4: every interactive element gets the effect, form buttons included. The cloth inks `.interactive`.
        for state, where, _name, _page, tree in self.states():
            with self.subTest(state=state):
                for control in (e for e in tree.elements if in_main(e) and e["tag"] in ("a", "button")):
                    self.assertIn("interactive", control["attrs"].get("class", "").split(),
                                  f"{where}: the {words(control)!r} control needs class `interactive`, which the fill and the ink wash answer to")

    def test_a_dead_link_shows_nothing_of_the_link(self):
        for state in ("reset confirm, dead link", "verify link dead"):
            with self.subTest(state=state):
                self.assertNotIn("bogus-token", self.html[state], f"{FILES}{PAGES[state][0]}: never print the token from the address")

    def test_the_status_lines_are_announced(self):
        cases = {"verify, link sent": ["We sent the verification link. Check your inbox."], "verify, verified": ["Your email address is verified."],
                 "verify, pending": [], "verify, no address yet": [], "verify, wrong password": [], "verify, address taken": []}
        for state, wanted in cases.items():
            with self.subTest(state=state):
                found = Tree(self.html[state]).announced("status")
                self.assertEqual(found, wanted, f"{FILES}verify_email.html: a notice is a role=\"status\" line, and only when the link was just "
                                                f"sent or the address is verified; found {found}")

    def test_the_verified_page_has_no_form_and_the_others_no_verified_notice(self):
        self.assertEqual(Tree(self.html["verify, verified"]).all("form"), [], f"{FILES}verify_email.html: a verified address has nothing to submit")
        for state in ("verify, no address yet", "verify, pending", "verify, wrong password", "verify, address taken"):
            with self.subTest(state=state):
                self.assertNotIn("Your email address is verified.", self.html[state],
                                 f"{FILES}verify_email.html: the verified notice is only for a verified address")


class FormErrorsTests(PageTestCase):
    """A form's own errors (not a field's) show in an alert on every page that has a form, so none is dropped."""

    def test_a_non_field_error_is_shown_in_an_alert_on_each_form(self):
        sentence = "This is an error of the form itself."
        owner = User.objects.create_user(username="formerrors", email="formerrors@example.com", password=PASSWORD)
        cases = (
            ("password_reset_form.html", PasswordResetForm({"email": "formerrors@example.com"}), {}),
            ("password_reset_confirm.html", SetPasswordForm(owner, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD}),
             {"validlink": True}),
            ("verify_email.html", EmailUpdateForm(owner, {"email": "formerrors2@example.com", "password": PASSWORD}),
             {"verified": False, "sent": False, "record": None}),
        )
        for template, form, extra in cases:
            with self.subTest(template=template):
                self.assertTrue(form.is_valid())
                form.add_error(None, sentence)
                request = RequestFactory().get("/")
                request.user = AnonymousUser()
                page = render_to_string("registration/" + template, {"form": form, **extra}, request=request)
                alerts = Tree(page).announced("alert")
                self.assertEqual(len([a for a in alerts if sentence in a]), 1,
                                 f"{FILES}{template}: show {{% include \"components/form_errors.html\" with errors=form.non_field_errors only %}} "
                                 f"above the form; the alerts found are {alerts}")
                self.assertEqual(self.focused(page), ["alert"],
                                 f"{FILES}{template}: an error of the form alone is no field in error, so the summary takes focus (pass focus=summary from "
                                 "{% focus_summary %} to form_errors) and no field has autofocus, the page's usual first input included; "
                                 f"found {self.focused(page)}")
                summary = next(e for e in Tree(page).elements if e["attrs"].get("role") == "alert")
                self.assertEqual(summary["attrs"].get("tabindex"), "-1", f'{FILES}{template}: the focused summary has tabindex="-1"')

    def focused(self, page):
        """What has autofocus in a page: the ids of its fields, and `alert` for the error summary."""
        return [e["attrs"].get("id") or ("alert" if e["attrs"].get("role") == "alert" else e["tag"]) for e in Tree(page).elements if "autofocus" in e["attrs"]]

    def test_a_field_in_error_wins_over_an_error_of_the_form(self):
        sentence = "This is an error of the form itself."
        owner = User.objects.create_user(username="bothkinds", email="bothkinds@example.com", password=PASSWORD)
        cases = (
            ("password_reset_form.html", PasswordResetForm({"email": "bothkinds@example.com"}), "email", {}, "id_email"),
            ("password_reset_confirm.html", SetPasswordForm(owner, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD}), "new_password2",
             {"validlink": True}, "id_new_password2"),
            ("verify_email.html", EmailUpdateForm(owner, {"email": "bothkinds2@example.com", "password": PASSWORD}), "password",
             {"verified": False, "sent": False, "record": None}, "id_password"),
        )
        for template, form, field, extra, wanted in cases:
            with self.subTest(template=template):
                self.assertTrue(form.is_valid())
                form.add_error(None, sentence)
                form.add_error(field, "This field has its own error.")
                request = RequestFactory().get("/")
                request.user = AnonymousUser()
                page = render_to_string("registration/" + template, {"form": form, **extra}, request=request)
                self.assertEqual(self.focused(page), [wanted],
                                 f"{FILES}{template}: a field in error takes focus and the summary does not, so the page has one autofocus; found {self.focused(page)}")
                summary = next(e for e in Tree(page).elements if e["attrs"].get("role") == "alert")
                self.assertNotIn("tabindex", summary["attrs"], f"{FILES}{template}: the summary is not a stop of its own when a field has focus")


class ResetRequestTests(PageTestCase):
    def test_the_done_page_is_the_same_for_a_known_and_an_unknown_address(self):
        # The reset form answers a request for an address with no account exactly as it answers one with an account.
        User.objects.create_user(username="known", email="known@example.com")
        known = Client().post("/accounts/password_reset/", {"email": "known@example.com"}, follow=True)
        unknown = Client().post("/accounts/password_reset/", {"email": "unknown@example.com"}, follow=True)
        self.assertEqual((known.status_code, unknown.status_code), (200, 200))
        self.assertEqual(known.content, unknown.content,
                         f"{FILES}password_reset_done.html: show nothing that depends on the address or on whether it has an account "
                         "(no address, no token, no per-visitor value)")


class ResetConfirmTests(PageTestCase):
    def test_a_validator_error_lands_under_the_confirm_field(self):
        user = User.objects.create_user(username="shortpw", email="shortpw@example.com")
        link = "/accounts/reset/{}/{}/".format(urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user))
        set_password = self.client.get(link, follow=True).redirect_chain[-1][0]
        page = self.client.post(set_password, {"new_password1": "short1", "new_password2": "short1"}).content.decode()
        ids = Tree(page).ids()
        confirm = ids["id_new_password2"]
        self.assertEqual(confirm["attrs"].get("aria-invalid"), "true",
                         f"{FILES}password_reset_confirm.html: Django attaches the password validators' errors to new_password2; "
                         "pass errors=form.new_password2.errors to its field")
        described = " ".join(words(ids[token]) for token in confirm["attrs"].get("aria-describedby", "").split())
        self.assertIn("too short", described, f"{FILES}password_reset_confirm.html: the validators' sentence is shown under the field")
        self.assertNotIn("short1", page, f"{FILES}password_reset_confirm.html: a password is never sent back")


class VerifyEmailTests(PageTestCase):
    def test_it_renders_with_no_request(self):
        # The one render with no request: the shell and the form layout must not need one.
        page = render_to_string("registration/verify_email.html", {"verified": True, "sent": False})
        found = [words(e) for e in Tree(page).elements if e["attrs"].get("role") == "status"]
        self.assertEqual(found, ["Your email address is verified."],
                         f"{FILES}verify_email.html: the notice is a role=\"status\" line even with no request; found {found}")

    def test_the_address_is_escaped_in_the_sentence_and_in_the_field(self):
        user = User.objects.create_user(username="escaper", email="escaper@example.com")
        EmailVerification.objects.create(user=user, email='x&y"<b>@example.com')
        page = sign_in(user).get("/accounts/verify-email/").content.decode()
        self.assertNotIn("<b>@", page, f"{FILES}verify_email.html: the address is printed escaped, never as markup")
        self.assertGreaterEqual(page.count("x&amp;y&quot;&lt;b&gt;@example.com"), 2,
                                f"{FILES}verify_email.html: the escaped address is in the sentence and in the email field's value")

    def test_a_very_long_address_is_shown_whole(self):
        address = "l" * 64 + "@" + ".".join(["d" * 63, "d" * 63, "d" * 57]) + ".com"
        self.assertEqual(len(address), 254)
        user = User.objects.create_user(username="longaddress", email="longaddress@example.com")
        EmailVerification.objects.create(user=user, email=address)
        tree = Tree(sign_in(user).get("/accounts/verify-email/").content.decode())
        self.assertEqual(tree.ids()["id_email"]["attrs"].get("value"), address, f"{FILES}verify_email.html: the field holds the whole address")
        self.assertIn(f"We sent a verification link to {address}.", words(tree.all("main")[0]),
                      f"{FILES}verify_email.html: the sentence names the whole address")

    def test_nothing_is_offered_to_resend_before_an_address_is_on_record(self):
        legacy = User.objects.create_user(username="norecord")
        page = sign_in(legacy).get("/accounts/verify-email/").content.decode()
        self.assertNotIn('value="resend"', page, f"{FILES}verify_email.html: the resend form is only for an address on record")


class LinkPageTests(PageTestCase):
    def test_a_dead_verification_link_says_the_same_whatever_the_reason(self):
        user = User.objects.create_user(username="deadlinks", email="deadlinks@example.com")
        EmailVerification.objects.create(user=user, email=user.email)
        token = make_verification_token(user, user.email)
        tampered = f"{token[:-2]}xx"
        bodies = {"unknown": self.client.get("/accounts/verify-email/bogus-token/").content,
                  "tampered": self.client.get(f"/accounts/verify-email/{tampered}/").content}
        with mock.patch("drop_core.email_verification.TOKEN_MAX_AGE", -1):
            bodies["expired"] = self.client.get(f"/accounts/verify-email/{token}/").content
        self.assertEqual(len(set(bodies.values())), 1,
                         f"{FILES}verify_email_invalid.html: the view passes `reason`; the page must not use it, so a visitor "
                         f"cannot tell why a link failed. These reasons gave different pages: {sorted(bodies)}")
        self.assertNotIn(tampered.encode(), bodies["tampered"], f"{FILES}verify_email_invalid.html: never print the token")
