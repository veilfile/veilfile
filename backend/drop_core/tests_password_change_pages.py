"""The password change pages on the form layout (spec 4.7): /accounts/password_change/ and the page it ends on.

Both were the Django admin's pages, in the admin's skin, and nothing linked to them. Overview now links to the first, so they are pages of the site: the shell, one h1,
labelled fields with their errors tied to them, one main action and a quiet way back. The view does not change (drop_core.views.RevokingPasswordChangeView: wrong old passwords
share the login failure budget, and a change disconnects the OAuth apps and keeps the API keys), and its tests (drop_core.tests_fix_auth) pass as they are.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it): a failed assertIn on a page would print the page before the message, so each message
names the file and the fix.
"""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase

from drop_core.tests_reset_verify_pages import Tree, in_main, inside, visible_inputs, words
from pages.tests_chapters import BANNED

User = get_user_model()
PASSWORD = "pw12345678"
NEW_PASSWORD = "a-new-pw-98765432"
WRONG_PASSWORD = "wrong-pw-24681357"
FILES = "backend/templates/registration/"
FORM, DONE = "password_change_form.html", "password_change_done.html"
ADDRESS, DONE_ADDRESS = "/accounts/password_change/", "/accounts/password_change/done/"

# state -> (template, the page's name). The title is "<name> | Veilfile" and the name is the h1.
PAGES = {
    "form": (FORM, "Change password"),
    "wrong old password": (FORM, "Change password"),
    "mismatch": (FORM, "Change password"),
    "too short": (FORM, "Change password"),
    "done": (DONE, "Password changed"),
}
FORM_STATES = ("form", "wrong old password", "mismatch", "too short")
INPUTS = [("old_password", "password"), ("new_password1", "password"), ("new_password2", "password")]
LABELS = {"id_old_password": "Current password", "id_new_password1": "New password", "id_new_password2": "Confirm new password"}
TOKENS = {"id_old_password": "current-password", "id_new_password1": "new-password", "id_new_password2": "new-password"}
ERRORS = {  # state -> {input id: a part of the sentence tied to it}; Django's own words, so only the part that matters is held
    "wrong old password": {"id_old_password": "Your old password was entered incorrectly."},
    "mismatch": {"id_new_password2": "The two password fields didn\N{RIGHT SINGLE QUOTATION MARK}t match."},
    "too short": {"id_new_password2": "This password is too short."},
}
INTRO = "Changing your password signs out your other sessions and disconnects your connected apps. API keys keep working."
DONE_WORDS = "Your password is changed. Other sessions were signed out and connected apps were disconnected."


def sign_in(user):
    client = Client()
    client.force_login(user)
    return client


class PageTestCase(TestCase):
    longMessage = False

    def setUp(self):
        super().setUp()
        cache.clear()  # the login failure budget lives in the cache
        self.addCleanup(cache.clear)


class EveryStateTests(PageTestCase):
    """The form, three ways of being sent back, and the page it ends on, each drawn by the real view."""

    @classmethod
    def setUpTestData(cls):
        cache.clear()
        cls.html, cls.status = {}, {}

        def keep(state, response):
            cls.html[state] = response.content.decode()
            cls.status[state] = response.status_code

        def change(old=PASSWORD, new1=NEW_PASSWORD, new2=NEW_PASSWORD):
            return {"old_password": old, "new_password1": new1, "new_password2": new2}

        user = User.objects.create_user(username="pwpages", email="pwpages@example.com", password=PASSWORD)
        client = sign_in(user)
        keep("form", client.get(ADDRESS))
        keep("wrong old password", client.post(ADDRESS, change(old=WRONG_PASSWORD)))
        keep("mismatch", client.post(ADDRESS, change(new2=NEW_PASSWORD + "-other")))
        keep("too short", client.post(ADDRESS, change(new1="short1", new2="short1")))
        keep("done", client.post(ADDRESS, change(), follow=True))
        cache.clear()

    def states(self):
        """(state, the template's path for messages, the page's name, its HTML, its parsed tree) for each state."""
        return [(state, FILES + template, name, self.html[state], Tree(self.html[state])) for state, (template, name) in PAGES.items()]

    def test_every_state_answers_200(self):
        for state, status in self.status.items():
            with self.subTest(state=state):
                self.assertEqual(status, 200, f"{FILES}{PAGES[state][0]}: the '{state}' state answers {status}, not 200; the view keeps its status codes")

    def test_every_state_is_a_page_of_the_shell_on_the_form_layout_and_not_the_admins(self):
        for state, where, _name, page, _tree in self.states():
            with self.subTest(state=state):
                self.assertIn('<a class="skip-link label" href="#main">', page, f"{where}: extend layouts/form.html, which extends shell.html; the shell brings the skip link")
                self.assertRegex(page, r'<main id="main" class="shell-main">\s*<div class="form-page">', f"{where}: the page's words go in {{% block form_page %}} of layouts/form.html")
                self.assertRegex(page, r'<body class="[^"]*\blayout-form\b', f"{where}: the form layout gives the body the class layout-form")
                self.assertNotIn("data-layout", page, f"{where}: a form page has no registry layout, so it makes no in-place changes")
                for admin in ("Django administration", 'id="branding"', 'id="header"', "django-admin", "admin/css"):
                    self.assertNotIn(admin, page, f"{where}: found {admin!r}, which is the Django admin's page. Without a template of its own at templates/registration/ Django uses the admin's")

    def test_one_h1_inside_main_named_as_the_title(self):
        for state, where, name, page, tree in self.states():
            with self.subTest(state=state):
                h1s = tree.all("h1")
                self.assertEqual([words(h) for h in h1s], [name], f"{where}: one h1, the page's name, {name!r}")
                self.assertTrue(in_main(h1s[0]), f"{where}: the h1 belongs inside <main>")
                found = re.search(r"<title>(.*?)</title>", page, re.S).group(1)
                self.assertEqual(found, f"{name} | Veilfile", f"{where}: the title is {name!r} and ' | Veilfile' (no em dash), found {found!r}")
                for element in tree.elements:
                    if element["tag"] in ("header", "footer"):
                        self.assertFalse(in_main(element), f"{where}: no <{element['tag']}> inside <main>")

    def test_every_page_is_kept_out_of_search(self):
        for state, where, _name, page, _tree in self.states():
            with self.subTest(state=state):
                self.assertIn('<meta name="robots" content="noindex">', page, f'{where}: add {{% block meta %}}<meta name="robots" content="noindex">{{% endblock %}}; the page is a signed-in account page')

    def test_the_form_posts_to_its_own_address_with_its_own_csrf_token(self):
        for state in FORM_STATES:
            with self.subTest(state=state):
                tree = Tree(self.html[state])
                forms = tree.all("form")
                self.assertEqual(len(forms), 1, f"{FILES}{FORM}: one form, found {len(forms)}")
                self.assertEqual(forms[0]["attrs"].get("method"), "post")
                self.assertNotIn("action", forms[0]["attrs"], f"{FILES}{FORM}: no action attribute: the form posts to the address it came from")
                tokens = [e for e in tree.all("input") if inside(e, forms[0]) and e["attrs"].get("name") == "csrfmiddlewaretoken"]
                self.assertEqual(len(tokens), 1, f"{FILES}{FORM}: {{% csrf_token %}} goes in the page's own <form>, never in a component; found {len(tokens)}")
        self.assertEqual(Tree(self.html["done"]).all("form"), [], f"{FILES}{DONE}: the page it ends on has nothing to submit")

    def test_the_fields_keep_their_names_and_types_in_the_order_the_form_has_them(self):
        for state in FORM_STATES:
            with self.subTest(state=state):
                found = [(e["attrs"].get("name"), e["attrs"].get("type")) for e in Tree(self.html[state]).all("input") if e["attrs"].get("name") != "csrfmiddlewaretoken"]
                self.assertEqual(found, INPUTS, f"{FILES}{FORM}: the view's form reads these field names; found {found}")

    def test_every_input_has_one_visible_label_the_autocomplete_token_and_is_required(self):
        for state in FORM_STATES:
            with self.subTest(state=state):
                tree = Tree(self.html[state])
                for field in visible_inputs(tree):
                    name = field["attrs"]["id"]
                    labels = [label for label in tree.all("label") if label["attrs"].get("for") == name]
                    self.assertEqual(len(labels), 1, f"{FILES}{FORM}: the input {name!r} needs exactly one <label for>, found {len(labels)}")
                    self.assertTrue(words(labels[0]).startswith(LABELS[name]), f"{FILES}{FORM}: the label of {name} reads {LABELS[name]!r}, found {words(labels[0])!r}")
                    self.assertEqual(field["attrs"].get("autocomplete"), TOKENS[name], f"{FILES}{FORM}: {name} keeps autocomplete={TOKENS[name]!r} (password managers use them)")
                    self.assertIn("required", field["attrs"], f"{FILES}{FORM}: {name} is required")

    def test_the_new_password_field_shows_what_a_password_must_be(self):
        ids = Tree(self.html["form"]).ids()
        self.assertIn("id_new_password1_help", ids, f"{FILES}{FORM}: pass help=form.new_password1.help_text to the new password field")
        self.assertIn("at least 8 characters", words(ids["id_new_password1_help"]), f"{FILES}{FORM}: the help is the password validators' own rules")

    def test_a_field_error_is_tied_to_its_field_and_a_clean_field_is_not_marked(self):
        for state in FORM_STATES:
            with self.subTest(state=state):
                tree = Tree(self.html[state])
                ids = tree.ids()
                for element in tree.elements:
                    for token in element["attrs"].get("aria-describedby", "").split():
                        self.assertIn(token, ids, f"{FILES}{FORM}: aria-describedby names {token!r}, which is no id on the page")
                invalid = {e["attrs"]["id"]: e for e in visible_inputs(tree) if e["attrs"].get("aria-invalid") == "true"}
                wanted = ERRORS.get(state, {})
                self.assertEqual(set(invalid), set(wanted), f'{FILES}{FORM}: aria-invalid="true" belongs on the fields with an error ({sorted(wanted)}), found {sorted(invalid)}')
                for name, sentence in wanted.items():
                    described = " ".join(words(ids[token]) for token in invalid[name]["attrs"].get("aria-describedby", "").split())
                    self.assertIn(sentence, described, f"{FILES}{FORM}: the error under {name} reads {sentence!r} and aria-describedby must point at it; it points at {described!r}")

    def test_focus_goes_to_the_first_field_in_error_and_to_none_on_a_clean_form(self):
        wanted = {"form": [], "wrong old password": ["id_old_password"], "mismatch": ["id_new_password2"], "too short": ["id_new_password2"]}
        for state, ids in wanted.items():
            with self.subTest(state=state):
                focused = [e["attrs"].get("id") for e in Tree(self.html[state]).elements if "autofocus" in e["attrs"]]
                self.assertEqual(focused, ids, f"{FILES}{FORM}: {{% focus_field form \"old_password\" \"new_password1\" \"new_password2\" as focus %}} with no default (a page of several fields "
                                               f"takes none of its own), and focus=focus on each field; found {focused}")

    def test_a_typed_password_is_never_sent_back(self):
        for state in ("wrong old password", "mismatch", "too short"):
            for secret in (PASSWORD, NEW_PASSWORD, WRONG_PASSWORD, "short1"):
                with self.subTest(state=state, secret=secret):
                    self.assertNotIn(f'value="{secret}', self.html[state], f"{FILES}{FORM}: a password field never gets a value attribute")

    def test_the_words_the_buttons_and_the_links(self):
        for state in FORM_STATES:
            with self.subTest(state=state):
                tree = Tree(self.html[state])
                self.assertIn(INTRO, words(tree.all("main")[0]), f"{FILES}{FORM}: say what a change does; every part of it is true (tests_fix_auth holds the revoking, BehaviourTests below the sessions)")
                self.assertEqual([words(e) for e in tree.all("button") if in_main(e)], ["Change password"])
                links = [(words(e), e["attrs"].get("href"), e["attrs"].get("class")) for e in tree.all("a") if in_main(e)]
                self.assertEqual(links, [("Back to dashboard", "/app/", "label interactive")], f"{FILES}{FORM}: one quiet way back; found {links}")
                actions = [(e["tag"], e["attrs"]["class"]) for e in tree.elements if in_main(e) and "action" in e["attrs"].get("class", "").split()]
                self.assertEqual(actions, [("button", "action interactive")], f'{FILES}{FORM}: the one main action is <button class="action interactive" type="submit">; found {actions}')
        done = Tree(self.html["done"])
        self.assertIn(DONE_WORDS, words(done.all("main")[0]), f"{FILES}{DONE}: keep the sentence {DONE_WORDS!r}")
        links = [(words(e), e["attrs"].get("href"), e["attrs"].get("class")) for e in done.all("a") if in_main(e)]
        self.assertEqual(links, [("Go to dashboard", "/app/", "action interactive")], f"{FILES}{DONE}: the next step is the main action; found {links}")

    def test_the_templates_keep_the_voice(self):
        for template in (FORM, DONE):
            text = (Path(settings.BASE_DIR) / "templates" / "registration" / template).read_text(encoding="utf-8")
            words_only = re.sub(r"{%.*?%}|{#.*?#}|{{.*?}}", "", text)  # a template tag may say != ; the words may not shout
            with self.subTest(template=template):
                self.assertNotIn("\N{EM DASH}", text, f"{FILES}{template}: no em dashes; use a colon, a comma or two sentences")
                self.assertNotIn("!", words_only, f"{FILES}{template}: no exclamation marks")
                for word in BANNED:
                    self.assertNotIn(word, text.lower(), f"{FILES}{template}: avoid {word.strip()!r} (AGENTS.md, voice)")

    def test_the_menu_is_the_signed_in_menu_of_the_site(self):
        for state in ("form", "done"):
            with self.subTest(state=state):
                self.assertIn('href="/app/">Dashboard</a>', self.html[state], f"{FILES}{PAGES[state][0]}: the shell's menu offers the Dashboard to a signed-in visitor")
                self.assertNotIn(">Sign in</a>", self.html[state])


class FormErrorsTests(PageTestCase):
    """An error of the form itself, which belongs to no field, is an alert, and it has focus when no field is in error."""

    def render(self, form):
        request = RequestFactory().get(ADDRESS)
        request.user = AnonymousUser()
        return render_to_string("registration/" + FORM, {"form": form}, request=request)

    def test_a_non_field_error_is_shown_in_an_alert_that_takes_focus(self):
        sentence = "This is an error of the form itself."
        owner = User.objects.create_user(username="pwalert", email="pwalert@example.com", password=PASSWORD)
        form = PasswordChangeForm(owner, {"old_password": PASSWORD, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        self.assertTrue(form.is_valid())
        form.add_error(None, sentence)
        tree = Tree(self.render(form))
        alerts = tree.announced("alert")
        self.assertEqual([a for a in alerts if sentence in a], [f"Check this {sentence}"],
                         f"{FILES}{FORM}: show {{% include \"components/form_errors.html\" with errors=form.non_field_errors focus=summary only %}} above the form; the alerts found are {alerts}")
        focused = [e["attrs"].get("id") or ("alert" if e["attrs"].get("role") == "alert" else e["tag"]) for e in tree.elements if "autofocus" in e["attrs"]]
        self.assertEqual(focused, ["alert"], f"{FILES}{FORM}: with only an error of its own the summary takes focus ({{% focus_summary %}}) and no field does; found {focused}")

    def test_a_field_in_error_takes_focus_before_the_summary(self):
        owner = User.objects.create_user(username="pwboth", email="pwboth@example.com", password=PASSWORD)
        form = PasswordChangeForm(owner, {"old_password": PASSWORD, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        self.assertTrue(form.is_valid())
        form.add_error(None, "This is an error of the form itself.")
        form.add_error("new_password2", "This field has its own error.")
        focused = [e["attrs"].get("id") or e["tag"] for e in Tree(self.render(form)).elements if "autofocus" in e["attrs"]]
        self.assertEqual(focused, ["id_new_password2"], f"{FILES}{FORM}: one autofocus on the page, the field in error; found {focused}")


class BehaviourTests(PageTestCase):
    """What the view does has not changed with the page."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="pwbehave", email="pwbehave@example.com", password=PASSWORD)

    def test_a_signed_out_visitor_is_sent_to_log_in_and_back(self):
        for address in (ADDRESS, DONE_ADDRESS):
            with self.subTest(address=address):
                response = Client().get(address)
                self.assertEqual((response.status_code, response["Location"]), (302, f"/accounts/login/?next={address}"), "the pages need a session, as they did")

    def test_a_good_change_goes_to_the_done_page_and_sets_the_password(self):
        client = sign_in(self.user)
        response = client.post(ADDRESS, {"old_password": PASSWORD, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        self.assertEqual((response.status_code, response["Location"]), (302, DONE_ADDRESS))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        self.assertEqual(client.get(DONE_ADDRESS).status_code, 200)

    def test_the_session_that_changes_the_password_stays_and_the_others_are_signed_out(self):
        # The intro and the done page say so: "signs out your other sessions" is what Django's PasswordChangeView does (update_session_auth_hash).
        here, elsewhere = sign_in(self.user), sign_in(self.user)
        self.assertEqual(elsewhere.get(ADDRESS).status_code, 200)
        here.post(ADDRESS, {"old_password": PASSWORD, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        self.assertEqual(here.get(ADDRESS).status_code, 200, "the session that changed it is still signed in")
        self.assertEqual(elsewhere.get(ADDRESS).status_code, 302, "another session of the same user is signed out by the change")

    def test_a_wrong_old_password_changes_nothing(self):
        client = sign_in(self.user)
        response = client.post(ADDRESS, {"old_password": WRONG_PASSWORD, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))
