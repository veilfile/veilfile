"""The form layout and its pieces (spec 4.7, 6.2): layouts/form.html, the field and form_errors components, the CSS of the form column,
the main action, the secondary links, notices and the checkbox and radio rows, and the conventions every form page shares."""
import re
from html import escape
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template import engines
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.utils.safestring import mark_safe

from drop_core.forms import SignupForm
from pages import catalog
from pages.tests_chapters import BANNED
from pages.tests_head import request_for
from pages.tests_reading import declarations, rules
from pages.tests_shell import render_shell

BACKEND = Path(settings.BASE_DIR)
STATIC = BACKEND / "static"
TEMPLATES = BACKEND / "templates"
BASE_CSS = STATIC / "css" / "base.css"
FIELD_CSS = STATIC / "css" / "components" / "field.css"
ERRORS_CSS = STATIC / "css" / "components" / "form_errors.css"
OWN_TEMPLATES = ("layouts/form.html", "components/field.html", "components/form_errors.html")


class _Tags(HTMLParser):
    """Every start tag of the HTML fed to it, in order, as (name, {attribute: value}); an attribute written bare, such as required, has the value None."""

    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        self.found.append((tag, dict(attrs)))


def tags(html, name):
    """The attributes of every <name> in html, in order."""
    parser = _Tags()
    parser.feed(html)
    parser.close()
    return [attrs for tag, attrs in parser.found if tag == name]


def field(**inputs):
    """components/field.html as a page includes it: with these inputs and nothing else."""
    return render_to_string("components/field.html", {"id": "id_name", "name": "name", "label": "Name", **inputs})


def form_errors(**inputs):
    return render_to_string("components/form_errors.html", inputs)


def page(source, context=None):
    """A template written as a string, rendered for a request outside the registry (a form page has no registry entry)."""
    return engines["django"].from_string(source).render(context or {}, request_for("/app/components/"))


def without_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


INCLUDE_FIELD = re.compile(r"{%\s*include\s+[\"']components/field\.html[\"'](.*?)%}")
NOT_A_LITERAL = re.compile(r"\bextra=(?![\"'])")


def variable_extras(text):
    """The includes of the field component in text whose extra is not a string literal."""
    return [include for include in INCLUDE_FIELD.findall(text) if NOT_A_LITERAL.search(include)]


INCLUDE_PIECE = re.compile(r"{%\s*include\s+[\"']components/(?:field|form_errors)\.html[\"'](.*?)%}")
STRING_ERRORS = re.compile(r"\berrors=[\"']")
EXTENDS_FORM = re.compile(r"{%\s*extends\s+[\"']layouts/form\.html[\"']\s*%}")
TITLE_BLOCK = re.compile(r"{%\s*block title\s*%}(.*?){%\s*endblock\s*%}", re.S)
CLASS_ATTRIBUTE = re.compile(r'\bclass="([^"]*)"')


def without_template_comments(text):
    return re.sub(r"{#.*?#}", "", text, flags=re.S)


def string_errors(text):
    """The includes of the field or form_errors component in text whose errors is a string literal, which the component would walk a letter at a time."""
    return [include for include in INCLUDE_PIECE.findall(without_template_comments(text)) if STRING_ERRORS.search(include)]


def title_problem(text):
    """What is wrong with the title of a template on the form layout, or None: it must be written, as the page's name and then ' | Veilfile'."""
    text = without_template_comments(text)
    if not EXTENDS_FORM.search(text):
        return None
    block = TITLE_BLOCK.search(text)
    if block is None:
        return "has no {% block title %}, so its title is the bare word Veilfile"
    title = block.group(1).strip()
    if not re.fullmatch(r".*\S \| Veilfile", title, re.S) or "—" in title:
        return f"has the title {title!r}; write the page's name, then ' | Veilfile' (a vertical bar: the voice rules ban the em dash)"
    return None


def actions_without_interactive(text):
    """The class attributes of text that name an element's class action without interactive."""
    found = []
    for value in CLASS_ATTRIBUTE.findall(without_template_comments(text)):
        classes = re.sub(r"{%.*?%}|{{.*?}}", " ", value).split()
        if "action" in classes and "interactive" not in classes:
            found.append(value)
    return found


class FieldTests(SimpleTestCase):
    longMessage = False  # a failure prints the message, which names the file and the fix
    FILE = "backend/templates/components/field.html"

    def test_a_blank_field_is_a_label_above_the_input_it_names(self):
        html = field(autocomplete="username")
        label, control = tags(html, "label"), tags(html, "input")
        self.assertEqual((len(label), len(control)), (1, 1), f"{self.FILE}: one <label> and one <input>")
        self.assertEqual(label[0]["for"], control[0]["id"], f"{self.FILE}: the label's for is the input's id, so a click on the label and a screen reader find the input")
        self.assertEqual(label[0]["class"], "field__label label", f'{self.FILE}: the label is class="field__label label" (mono, uppercase, muted: base.css .label)')
        self.assertLess(html.index("<label"), html.index("<input"), f"{self.FILE}: the label comes above the input")
        self.assertEqual((control[0]["name"], control[0]["type"], control[0]["autocomplete"]), ("name", "text", "username"), f"{self.FILE}: print name, type and autocomplete")
        for absent in ("value", "required", "autofocus", "aria-invalid", "aria-describedby"):
            self.assertNotIn(absent, control[0], f"{self.FILE}: print {absent} only when the page asks for it")
        self.assertNotIn("field__error", html, f"{self.FILE}: no error markup without errors")

    def test_the_type_is_text_unless_the_page_says_so(self):
        for given, expected in ((None, "text"), ("email", "email"), ("password", "password")):
            with self.subTest(type=given):
                html = field() if given is None else field(type=given)
                self.assertEqual(tags(html, "input")[0]["type"], expected, f"{self.FILE}: type is the page's type, text by default")

    def test_required_autofocus_and_autocomplete_are_printed_when_asked_for(self):
        control = tags(field(required=True, autofocus=True, autocomplete="current-password"), "input")[0]
        self.assertIn("required", control, f"{self.FILE}: print required when the page passes required=True")
        self.assertIn("autofocus", control, f"{self.FILE}: print autofocus when the page passes autofocus=True")
        self.assertEqual(control["autocomplete"], "current-password", f"{self.FILE}: keep the autocomplete token the page passes")
        plain = tags(field(required=False, autofocus=False), "input")[0]
        self.assertNotIn("required", plain, f"{self.FILE}: no required without required=True")
        self.assertNotIn("autofocus", plain, f"{self.FILE}: no autofocus without autofocus=True (a page that does not exist to take one input must not steal focus)")

    def test_focus_prints_autofocus_on_the_one_field_it_names(self):
        # A page asks {% focus_field %} which field takes focus and passes the answer to every field as `focus`: the one it names prints autofocus, so exactly one field has it.
        self.assertIn("autofocus", tags(field(name="email", focus="email"), "input")[0], f"{self.FILE}: print autofocus when focus is this field's name")
        for given in ({"focus": "password"}, {"focus": ""}, {}):
            with self.subTest(given=given):
                self.assertNotIn("autofocus", tags(field(name="email", **given), "input")[0], f"{self.FILE}: no autofocus when focus names another field, is empty or is absent")

    def test_a_value_is_kept_but_never_for_a_password(self):
        self.assertEqual(tags(field(type="email", value="ana@example.com"), "input")[0]["value"], "ana@example.com", f"{self.FILE}: keep the value after an error")
        secret = field(type="password", value="hunter2-secret")
        self.assertNotIn("hunter2-secret", secret, f"{self.FILE}: never print a password back, whatever the page passes")
        self.assertNotIn("value", tags(secret, "input")[0], f"{self.FILE}: no value attribute on a password")
        self.assertNotIn("value", tags(field(value=None), "input")[0], f"{self.FILE}: a value of None prints nothing, not the word None")
        awkward = 'a"b<c&d'
        html = field(value=awkward)
        self.assertEqual(tags(html, "input")[0]["value"], awkward, f"{self.FILE}: the value round-trips through the attribute")
        self.assertNotIn(awkward, html, f"{self.FILE}: print the value escaped")

    def test_the_help_sits_between_the_label_and_the_input_and_describes_it(self):
        html = field(help="We verify this address before issuing API keys.")
        box = [attrs for attrs in tags(html, "div") if attrs.get("class") == "field__help"]
        self.assertEqual([attrs["id"] for attrs in box], ["id_name_help"], f'{self.FILE}: <div class="field__help" id="ID_help">')
        self.assertEqual(tags(html, "input")[0]["aria-describedby"], "id_name_help", f"{self.FILE}: aria-describedby names the help")
        self.assertLess(html.index("<label"), html.index('class="field__help"'), f"{self.FILE}: the help comes after the label")
        self.assertLess(html.index('class="field__help"'), html.index("<input"),
                        f"{self.FILE}: the help comes above the input, where the on-screen keyboard does not cover it")

    def test_help_is_trusted_html_only_when_the_page_marks_it_so(self):
        listed = field(help=mark_safe("<ul><li>One.</li><li>Two.</li></ul>"))
        self.assertIn('<div class="field__help" id="id_name_help"><ul><li>One.</li><li>Two.</li></ul></div>', listed,
                      f"{self.FILE}: a safe help (a form field's help_text) keeps its list")
        plain = field(help="Fish & <b>chips</b>")
        self.assertIn("Fish &amp; &lt;b&gt;chips&lt;/b&gt;", plain, f"{self.FILE}: print help without |safe, so plain text is escaped")
        self.assertNotIn("<b>", plain)

    def test_errors_mark_the_input_invalid_and_tie_their_sentences_to_it(self):
        html = field(errors=["Enter a valid email address.", "It must have an at sign."])
        control = tags(html, "input")[0]
        self.assertEqual(control["aria-invalid"], "true", f'{self.FILE}: aria-invalid="true" on an input with errors')
        self.assertEqual(control["aria-describedby"], "id_name_error", f"{self.FILE}: aria-describedby names the error box")
        box = [attrs for attrs in tags(html, "div") if attrs.get("class") == "field__error"]
        self.assertEqual([attrs["id"] for attrs in box], ["id_name_error"], f'{self.FILE}: <div class="field__error" id="ID_error">')
        self.assertEqual(re.findall(r"<p>([^<]*)</p>", html), ["Enter a valid email address.", "It must have an at sign."],
                         f"{self.FILE}: one <p> for each error, in order")
        self.assertIn('<span class="field__flag label" aria-hidden="true">Check this</span>', html,
                      f"{self.FILE}: the mono label Check this sits above the sentences; it is for the eye, so a screen reader hears the sentences and invalid data only")
        self.assertLess(html.index("<input"), html.index("field__error"), f"{self.FILE}: the errors come under the input")
        self.assertIn('class="field field--error"', html, f"{self.FILE}: the wrapper says it holds an error")

    def test_help_and_errors_both_describe_the_input_help_first(self):
        control = tags(field(help="Some help.", errors=["Some error."]), "input")[0]
        self.assertEqual(control["aria-describedby"], "id_name_help id_name_error", f"{self.FILE}: aria-describedby is the help's id, a space, then the error's")

    def test_no_errors_draws_nothing_of_an_error(self):
        for errors in (None, [], ()):
            with self.subTest(errors=errors):
                html = field(errors=errors)
                for absent in ("field__error", "field--error", "aria-invalid", "Check this"):
                    self.assertNotIn(absent, html, f"{self.FILE}: with no errors print no {absent}")
        self.assertNotIn("Check this", field(), f"{self.FILE}: errors left out print no error markup")

    def test_extra_is_the_pages_exact_attribute_text_when_it_is_a_literal(self):
        source = ('{% include "components/field.html" with id="id_user_code" name="user_code" label="Device code" '
                  '''extra='placeholder="XXXX-XXXX" spellcheck="false"' only %}''')
        control = tags(page(source), "input")[0]
        self.assertEqual((control["placeholder"], control["spellcheck"]), ("XXXX-XXXX", "false"),
                         f"{self.FILE}: print extra as it is; a literal in the page's template is safe text, so its quotes survive")

    def test_extra_from_a_variable_cannot_close_the_tag(self):
        # Its quotes and brackets are escaped. It could still add an attribute (a space starts one), which is why a variable is not allowed at
        # all: FieldExtraConventionTests.
        source = '{% include "components/field.html" with id="id_x" name="x" label="X" extra=bad only %}'
        html = page(source, {"bad": '"><script>alert(1)</script>'})
        self.assertNotIn("<script>", html, f"{self.FILE}: never mark extra safe; text that came from anywhere but the template stays escaped")
        self.assertEqual(html.count("<input"), 1)

    def test_a_code_is_marked_mono(self):
        self.assertIn("field__input--mono", tags(field(mono=True), "input")[0]["class"], f"{self.FILE}: mono=True adds field__input--mono")
        self.assertEqual(tags(field(), "input")[0]["class"], "field__input", f"{self.FILE}: without mono the input has only field__input")

    def test_words_and_ids_are_escaped(self):
        html = field(label="<script>alert(1)</script>", id='a" onclick="x')
        self.assertNotIn("<script>", html, f"{self.FILE}: print the label escaped")
        self.assertNotIn("onclick", tags(html, "input")[0], f"{self.FILE}: print the id escaped")

    def test_the_component_holds_no_form_and_no_csrf_token(self):
        source = '{% include "components/field.html" with id="id_x" name="x" label="X" only %}'
        html = engines["django"].from_string(source).render({"csrf_token": "leaked-token"}, request_for("/app/components/"))
        for absent in ("csrfmiddlewaretoken", "leaked-token", "<form"):
            self.assertNotIn(absent, html, f"{self.FILE}: the page's own <form> holds the CSRF token; a component included with only never sees it")
        for name in ("field", "form_errors"):
            text = (TEMPLATES / "components" / f"{name}.html").read_text(encoding="utf-8")
            self.assertNotIn("csrf_token", text, f"backend/templates/components/{name}.html: leave the CSRF token to the page's own <form>")


class FieldExtraConventionTests(SimpleTestCase):
    """extra is attribute text printed as it is. A literal in a template is the author's own; a variable could carry a visitor's words, and
    a space in it starts a new attribute (onfocus=...), which escaping does not stop. So every page passes it as a string literal."""
    longMessage = False

    def test_the_check_tells_a_literal_from_a_variable(self):
        literal = '''{% include "components/field.html" with id="x" extra='spellcheck="false"' only %}'''
        double = '{% include "components/field.html" with id="x" extra="inputmode=numeric" only %}'
        variable = '{% include "components/field.html" with id="x" extra=bad only %}'
        self.assertEqual(variable_extras(literal + double), [], "backend/pages/tests_forms.py: variable_extras must accept a string literal in either quote")
        self.assertEqual(len(variable_extras(variable)), 1, "backend/pages/tests_forms.py: variable_extras must find extra=name")

    def test_no_template_passes_extra_a_variable(self):
        for path in sorted(TEMPLATES.rglob("*.html")):
            found = variable_extras(path.read_text(encoding="utf-8"))
            self.assertEqual(found, [], f"backend/templates/{path.relative_to(TEMPLATES)}: pass the field's extra as a string literal "
                                        "(extra='placeholder=\"XXXX-XXXX\"'), never a variable: a space in a variable starts a new attribute, "
                                        "so a visitor's words could add an onfocus handler")


class FormTemplateConventionTests(SimpleTestCase):
    """What every form page shares, held for every template of the site, so a page written later that breaks one fails here and its
    message names its file: errors are a list (never a string), a form page is titled `Name | Veilfile`, and the main action is interactive."""
    longMessage = False

    def test_the_checks_tell_the_right_from_the_wrong(self):
        where = "backend/pages/tests_forms.py"
        for source in ('{% include "components/field.html" with id="x" errors="Bad." only %}',
                       "{% include 'components/form_errors.html' with errors='Bad.' only %}"):
            self.assertEqual(len(string_errors(source)), 1, f"{where}: string_errors must find errors given as a string literal in either quote")
        for source in ('{% include "components/field.html" with id="x" errors=form.email.errors only %}',
                       '{% include "components/form_errors.html" with message="The username, email, or password is incorrect. Try again." only %}',
                       '{# {% include "components/field.html" with errors="Bad." only %} #}'):
            self.assertEqual(string_errors(source), [], f"{where}: string_errors must pass a variable, a message and a comment")

        def form(title):
            return '{% extends "layouts/form.html" %}{% block title %}' + title + "{% endblock %}"

        for good in ("Log in | Veilfile", "{{ title }} | Veilfile", "{% if x %}A{% else %}B{% endif %} | Veilfile"):
            self.assertIsNone(title_problem(form(good)), f"{where}: title_problem must accept {good!r}")
        for bad in ("Log in — Veilfile", "Log in", "| Veilfile", "Veilfile", ""):
            self.assertIsNotNone(title_problem(form(bad)), f"{where}: title_problem must refuse {bad!r}")
        self.assertIsNotNone(title_problem('{% extends "layouts/form.html" %}'), f"{where}: title_problem must refuse a form page with no title block")
        self.assertIsNone(title_problem('{% extends "layouts/chapter.html" %}{% block title %}Not found | Veilfile{% endblock %}'),
                          f"{where}: title_problem looks at the form layout's pages only")
        self.assertIsNone(title_problem("<p>no layout</p>"), f"{where}: title_problem looks at the form layout's pages only")
        for good in ('<button class="action interactive" type="submit">', '<a class="action label interactive" href="/">', '<p class="device-actions">',
                     '{# <button class="action"> #}', '<div class="form-actions">'):
            self.assertEqual(actions_without_interactive(good), [], f"{where}: actions_without_interactive must accept {good!r}")
        for bad in ('<button class="action" type="submit">', '<a class="action{% if x %} wide{% endif %}" href="/">'):
            self.assertEqual(len(actions_without_interactive(bad)), 1, f"{where}: actions_without_interactive must find {bad!r}")

    def test_no_template_passes_errors_a_string(self):
        for path in sorted(TEMPLATES.rglob("*.html")):
            found = string_errors(path.read_text(encoding="utf-8"))
            self.assertEqual(found, [], f"backend/templates/{path.relative_to(TEMPLATES)}: pass errors as a list of sentences (form.field.errors, "
                                        "form.non_field_errors, or a list the view builds), never a string literal: the component walks it a letter "
                                        "at a time. For one fixed sentence of the page's own, form_errors takes message=")

    def test_every_form_page_is_titled_with_its_name_and_veilfile(self):
        for path in sorted(TEMPLATES.rglob("*.html")):
            problem = title_problem(path.read_text(encoding="utf-8"))
            self.assertIsNone(problem, f"backend/templates/{path.relative_to(TEMPLATES)}: {problem}")

    def test_every_main_action_is_interactive(self):
        for path in sorted(TEMPLATES.rglob("*.html")):
            found = actions_without_interactive(path.read_text(encoding="utf-8"))
            self.assertEqual(found, [], f'backend/templates/{path.relative_to(TEMPLATES)}: write the main action as class="action interactive" '
                                        "(a button or a link). Only .interactive gives it the ink wash and the focus fill the owner ruled for it")


class FieldWithADjangoFormTests(TestCase):
    """What every form page does: hand a field's errors and value to the component straight from the Django form."""
    longMessage = False
    SOURCE = (
        '{% include "components/form_errors.html" with errors=form.non_field_errors only %}'
        '{% include "components/field.html" with id="id_email" name="email" label="Email" type="email" value=form.email.value '
        'autocomplete="email" required=True errors=form.email.errors only %}'
        '{% include "components/field.html" with id="id_password2" name="password2" label="Confirm password" type="password" '
        'autocomplete="new-password" required=True errors=form.password2.errors only %}'
    )

    def setUp(self):
        get_user_model().objects.create_user("taken", "ana@example.com", "a-test-only-password")
        self.form = SignupForm(data={"username": "ana", "email": "Ana@Example.com",
                                     "password1": "a-test-only-password-1", "password2": "a-different-password"})
        self.assertFalse(self.form.is_valid())
        self.html = page(self.SOURCE, {"form": self.form})
        self.inputs = {attrs["id"]: attrs for attrs in tags(self.html, "input")}

    def test_a_field_error_is_tied_to_its_input_the_way_the_old_pages_pinned(self):
        control = self.inputs["id_password2"]
        self.assertEqual(control["aria-invalid"], "true", 'backend/templates/components/field.html: aria-invalid="true" when form.field.errors is not empty')
        self.assertEqual(control["aria-describedby"], "id_password2_error",
                         "backend/templates/components/field.html: aria-describedby is the id of the error box, id_<field>_error (drop_core.tests.AccessibilityTests pinned this)")
        self.assertIn('id="id_password2_error"', self.html)
        self.assertIn(escape(self.form.errors["password2"][0]), self.html, "backend/templates/components/field.html: print each of the field's errors")

    def test_the_value_is_kept_and_a_password_is_not(self):
        self.assertEqual(self.inputs["id_email"]["value"], "Ana@Example.com", "backend/templates/components/field.html: keep value=form.email.value after an error")
        self.assertIn("An account with this email address already exists.", self.html)
        for typed in ("a-different-password", "a-test-only-password-1"):
            self.assertNotIn(typed, self.html, "backend/templates/components/field.html: a password is never printed back")

    def test_non_field_errors_become_an_alert(self):
        self.form.add_error(None, "Sign-up is paused for this network.")
        html = page(self.SOURCE, {"form": self.form})
        self.assertIn('<div class="form-errors" role="alert">', html, 'backend/templates/components/form_errors.html: role="alert" on the summary')
        self.assertIn("<p>Sign-up is paused for this network.</p>", html)


class FocusFieldTests(TestCase):
    """{% focus_field %} (pages/templatetags/shell.py): the field a form page puts focus on when it loads."""
    longMessage = False
    FILE = "backend/pages/templatetags/shell.py"
    NAMES = ("username", "email", "password1", "password2")
    GOOD = {"username": "focusana", "email": "focusana@example.com", "password1": "a-test-only-password-1", "password2": "a-test-only-password-1"}

    def focus(self, data, default=""):
        form = SignupForm(data=data) if data is not None else SignupForm()
        return page('{% load shell %}{% focus_field form "username" "email" "password1" "password2" default=default as focus %}[{{ focus }}]',
                    {"form": form, "default": default})

    def test_it_is_the_first_field_in_error_in_the_order_the_page_draws_them(self):
        for change, expected in (({"password2": "different-1"}, "password2"), ({"email": "nope", "password2": "different-1"}, "email"),
                                 ({"username": "", "password2": "different-1"}, "username"), ({"password1": "", "password2": ""}, "password1"),
                                 ({"password1": "x", "password2": "x"}, "password2")):   # Django reports a weak password on the second field
            with self.subTest(change=change):
                self.assertEqual(self.focus({**self.GOOD, **change}), f"[{expected}]", f"{self.FILE}: focus_field is the first of its names that the form has an error for")

    def test_a_form_without_errors_gives_the_default_or_nothing(self):
        for data in (None, self.GOOD):
            with self.subTest(bound=data is not None):
                self.assertEqual(self.focus(data), "[]", f"{self.FILE}: with no error and no default, nothing takes focus")
                self.assertEqual(self.focus(data, default="email"), "[email]", f"{self.FILE}: with no error the page's default field takes focus")

    def test_an_error_replaces_the_default(self):
        self.assertEqual(self.focus({**self.GOOD, "password2": "different-1"}, default="username"), "[password2]",
                         f"{self.FILE}: the field in error takes focus from the page's usual field, so one field has autofocus")

    def test_an_error_of_the_form_alone_gives_focus_to_the_summary_not_to_a_field(self):
        form = SignupForm(data=self.GOOD)
        self.assertTrue(form.is_valid())
        form.add_error(None, "Sign-up is paused for this network.")
        self.assertEqual(page('{% load shell %}{% focus_field form "username" "email" default="username" as focus %}[{{ focus }}]', {"form": form}), "[]",
                         f"{self.FILE}: an error that belongs to no field puts focus on the summary (focus_summary), so no field, the page's usual one included, has autofocus")

    def test_a_page_without_a_form_gives_the_default(self):
        self.assertEqual(page('{% load shell %}{% focus_field form "email" default="email" as focus %}[{{ focus }}]', {"form": None}), "[email]",
                         f"{self.FILE}: a page whose link is dead has no form and no error")


class FocusSummaryTests(TestCase):
    """{% focus_summary %} (pages/templatetags/shell.py): whether the error summary takes focus when a form page loads, which is when the form has an error of its own and none of the
    page's fields has one. It is the other half of focus_field: between them the page has one autofocus."""
    longMessage = False
    FILE = "backend/pages/templatetags/shell.py"
    GOOD = {"username": "summaryana", "email": "summaryana@example.com", "password1": "a-test-only-password-1", "password2": "a-test-only-password-1"}

    def summary(self, form):
        return page('{% load shell %}{% focus_summary form "username" "email" "password1" "password2" as summary %}[{% if summary %}summary{% endif %}]', {"form": form})

    def with_error_of_its_own(self, data):
        form = SignupForm(data=data)
        form.is_valid()
        form.add_error(None, "Sign-up is paused for this network.")
        return form

    def test_it_is_true_for_an_error_of_the_form_alone(self):
        self.assertEqual(self.summary(self.with_error_of_its_own(self.GOOD)), "[summary]",
                         f"{self.FILE}: focus_summary is true when the form has an error that belongs to no field and none of its names has one")

    def test_a_field_in_error_wins(self):
        form = self.with_error_of_its_own({**self.GOOD, "password2": "different-1"})
        self.assertEqual(self.summary(form), "[]", f"{self.FILE}: with a field in error the field takes focus, so the summary does not (one autofocus on the page)")

    def test_it_is_false_without_an_error_of_the_form(self):
        for label, form in (("unbound", SignupForm()), ("valid", SignupForm(data=self.GOOD)), ("a field in error", SignupForm(data={**self.GOOD, "email": "nope"})),
                            ("no form", None)):
            with self.subTest(form=label):
                self.assertEqual(self.summary(form), "[]", f"{self.FILE}: nothing to put focus on when the form has no error of its own")

    def test_it_agrees_with_focus_field_about_which_one_has_focus(self):
        source = ('{% load shell %}{% focus_field form "username" "email" "password1" "password2" default="username" as focus %}'
                  '{% focus_summary form "username" "email" "password1" "password2" as summary %}[{{ focus }}|{% if summary %}summary{% endif %}]')
        cases = {"nothing wrong": (SignupForm(data=self.GOOD), "[username|]"), "unbound": (SignupForm(), "[username|]"),
                 "a field": (SignupForm(data={**self.GOOD, "password2": "different-1"}), "[password2|]"),
                 "the form": (self.with_error_of_its_own(self.GOOD), "[|summary]"),
                 "both": (self.with_error_of_its_own({**self.GOOD, "email": "nope"}), "[email|]")}
        for label, (form, expected) in cases.items():
            with self.subTest(case=label):
                self.assertEqual(page(source, {"form": form}), expected, f"{self.FILE}: exactly one of the field and the summary takes focus, or neither on a page with nothing wrong and no usual field")


class FormErrorsTests(SimpleTestCase):
    longMessage = False
    FILE = "backend/templates/components/form_errors.html"

    def test_nothing_is_drawn_when_there_is_nothing_to_say(self):
        for inputs in ({}, {"errors": None}, {"errors": []}, {"errors": ()}, {"message": ""}):
            with self.subTest(inputs=inputs):
                self.assertEqual(form_errors(**inputs).strip(), "", f"{self.FILE}: draw nothing when errors and message are empty or absent")

    def test_the_sentences_are_an_alert_under_the_mark(self):
        html = form_errors(errors=["Your current password is incorrect.", "Try again."])
        box = tags(html, "div")[0]
        self.assertEqual((box["class"], box["role"]), ("form-errors", "alert"), f'{self.FILE}: <div class="form-errors" role="alert">')
        self.assertIn('<span class="form-errors__flag label" aria-hidden="true">Check this</span>', html)
        self.assertEqual(re.findall(r"<p>([^<]*)</p>", html), ["Your current password is incorrect.", "Try again."], f"{self.FILE}: one <p> for each error, in order")

    def test_a_message_is_one_sentence_before_the_errors(self):
        html = form_errors(message="The username, email, or password is incorrect. Try again.", errors=["Second."])
        self.assertEqual(re.findall(r"<p>([^<]*)</p>", html), ["The username, email, or password is incorrect. Try again.", "Second."],
                         f"{self.FILE}: message first, then each error")

    def test_sentences_are_escaped(self):
        html = form_errors(errors=["<b>No</b> & no"])
        self.assertNotIn("<b>", html, f"{self.FILE}: print each error escaped")
        self.assertIn("&lt;b&gt;No&lt;/b&gt; &amp; no", html)

    def test_the_page_can_ask_for_the_summary_to_take_focus(self):
        # A sentence that is on the page when it loads is announced inconsistently, so a page whose only error is the form's own focuses it; tabindex -1 is what lets a div take focus.
        for inputs in ({"errors": ["Your current password is incorrect."]}, {"message": "The username, email, or password is incorrect. Try again."}):
            with self.subTest(inputs=list(inputs)):
                box = tags(form_errors(focus=True, **inputs), "div")[0]
                self.assertEqual(box.get("tabindex"), "-1", f'{self.FILE}: tabindex="-1" on the summary when focus is true')
                self.assertIn("autofocus", box, f"{self.FILE}: autofocus on the summary when focus is true")
                self.assertEqual((box["class"], box["role"]), ("form-errors", "alert"), f"{self.FILE}: still the same alert")

    def test_the_summary_takes_no_focus_unless_the_page_asks(self):
        # A field in error has the page's one autofocus, and a page that was not asked leaves focus where it was.
        for inputs in ({}, {"focus": False}, {"focus": ""}, {"focus": None}):
            with self.subTest(inputs=inputs):
                box = tags(form_errors(errors=["Your current password is incorrect."], **inputs), "div")[0]
                self.assertNotIn("autofocus", box, f"{self.FILE}: no autofocus on the summary unless focus is true")
                self.assertNotIn("tabindex", box, f"{self.FILE}: no tabindex on the summary unless focus is true: it is not a stop in the page's order")

    def test_nothing_takes_focus_when_there_is_nothing_to_say(self):
        for inputs in ({"focus": True}, {"focus": True, "errors": []}, {"focus": True, "message": ""}):
            with self.subTest(inputs=inputs):
                html = form_errors(**inputs)
                self.assertEqual(html.strip(), "", f"{self.FILE}: with nothing to say the summary is not drawn, so focus cannot go to an empty box")


class FormLayoutTests(SimpleTestCase):
    longMessage = False
    FILE = "backend/templates/layouts/form.html"

    def setUp(self):
        self.html = page('{% extends "layouts/form.html" %}{% block form_page %}<h1>Form</h1><p>Words.</p>{% endblock %}')

    def test_the_body_wears_the_layouts_class(self):
        self.assertIn('<body class="layout-form" data-veil-src="/static/js/veil.js">', self.html,
                      f"{self.FILE}: the body_class block is layout-form, and the body has no data-layout: a form page is in no layout family")

    def test_the_words_sit_in_the_form_column_in_the_one_main(self):
        self.assertEqual((self.html.count("<main"), self.html.count("</main>")), (1, 1), f"{self.FILE}: one <main>, the shell's")
        self.assertIn('<main id="main" class="shell-main"><div class="form-page"><h1>Form</h1><p>Words.</p></div></main>', self.html,
                      f'{self.FILE}: the main block is <div class="form-page"> around a form_page block')

    def test_a_form_page_changes_nothing_in_place(self):
        self.assertNotIn("data-layout", self.html, "backend/templates/shell.html: data-layout comes only from a registry page's layout; a form page has none")
        self.assertNotIn("data-faces", self.html)

    def test_it_preloads_the_display_face_of_its_title_and_not_the_heading_face(self):
        self.assertIn('<link rel="preload" href="/static/fonts/cormorant-garamond/italic-300-latin.woff2" as="font" type="font/woff2" crossorigin>', self.html,
                      f"{self.FILE}: preload the display face in its preload_display block")
        self.assertNotIn("italic-400-latin", self.html, f"{self.FILE}: no form page sets a heading in italic 400")

    def test_the_csrf_token_stays_in_the_pages_own_form(self):
        html = page('{% extends "layouts/form.html" %}{% block form_page %}<form method="post">{% csrf_token %}'
                    '{% include "components/field.html" with id="id_x" name="x" label="X" only %}</form>{% endblock %}')
        self.assertEqual(html.count("csrfmiddlewaretoken"), 1, "a page with one form has one CSRF token: in the page's own <form>, not in the layout or a component")
        self.assertTrue(html.index("<form") < html.index("csrfmiddlewaretoken") < html.index("</form>"))

    def test_the_templates_keep_the_voice_and_put_no_header_or_footer_in_main(self):
        for name in OWN_TEMPLATES:
            text = (TEMPLATES / name).read_text(encoding="utf-8")
            words = re.sub(r"{%.*?%}|{#.*?#}|{{.*?}}", "", text)  # a template tag may say != ; the words may not shout
            where = f"backend/templates/{name}"
            with self.subTest(template=name):
                self.assertNotRegex(text, r"<(header|footer)\b", f"{where}: base.css hides a bare <footer> without JavaScript; use a <div> or a <nav>")
                self.assertNotIn("—", text, f"{where}: no em dashes; use a colon, a comma or two sentences")
                self.assertNotIn("!", words, f"{where}: no exclamation marks")
                for word in BANNED:
                    self.assertNotIn(word, text.lower(), f"{where}: avoid {word.strip()!r} (AGENTS.md, voice)")

    def test_the_component_stylesheets_come_after_base_css(self):
        # The form column's element rules and a component's class rules weigh the same; the later stylesheet wins.
        html = render_shell()
        base = html.index('href="/static/css/base.css"')
        for name in ("field", "form_errors"):
            link = f'href="/static/css/components/{name}.css"'
            with self.subTest(component=name):
                self.assertIn(link, html, f"backend/templates/shell.html: link css/components/{name}.css")
                self.assertGreater(html.index(link), base, f"backend/templates/shell.html: link css/components/{name}.css after css/base.css, so the component's classes override the form column's element rules")


class FormCssTests(SimpleTestCase):
    longMessage = False

    def setUp(self):
        self.base = BASE_CSS.read_text(encoding="utf-8")
        self.field = FIELD_CSS.read_text(encoding="utf-8")
        self.errors = ERRORS_CSS.read_text(encoding="utf-8")
        self.tokens = (STATIC / "css" / "tokens.css").read_text(encoding="utf-8")

    def declared(self, css, selector, where):
        found = declarations(css, selector)
        self.assertTrue(found, f"{where}: add the rule `{selector} {{ ... }}` (spec 4.7)")
        return found

    def test_the_column_is_form_measure_wide_and_wraps_what_will_not_fit(self):
        column = self.declared(self.base, ".form-page", "static/css/base.css")
        for name, value in (("width", "100%"), ("min-width", "0"), ("max-width", "var(--form-measure)"), ("overflow-wrap", "anywhere")):
            self.assertEqual(column.get(name), value,
                             f"static/css/base.css: `.form-page` needs {name}: {value}; a long email, user agent or app name must wrap, not scroll the page sideways at 320")
        self.assertRegex(self.tokens, r"--form-measure:\s*[\d.]+rem;", "static/css/tokens.css: --form-measure is the form column's width")

    def test_a_short_form_is_centred_from_64rem_and_starts_at_the_top_below(self):
        self.assertRegex(self.base, r"\.layout-form \.shell-main \{[^}]*align-content: start;",
                         "static/css/base.css: `.layout-form .shell-main` starts at the top, so the on-screen keyboard does not move a form about")
        self.assertRegex(self.base, r"@media \(min-width: 64rem\) \{\s*\.layout-form \.shell-main \{\s*align-content: center;",
                         "static/css/base.css: from 64rem a short form is centred between the corners, as a chapter is")

    def test_inputs_are_at_least_16px_so_ios_does_not_zoom(self):
        for selector in (".field__input", ".field__input--mono"):
            size = self.declared(self.field, selector, "static/css/components/field.css").get("font-size", "")
            self.assertRegex(size, r"^max\(16px,", f"static/css/components/field.css: `{selector}` needs font-size: max(16px, ...); under 16px iOS zooms the page when the field takes focus")

    def test_a_field_is_a_muted_line_round_a_box_with_ink_text(self):
        box = self.declared(self.field, ".field__input", "static/css/components/field.css")
        self.assertEqual(box.get("border"), "1px solid var(--muted)",
                         "static/css/components/field.css: `.field__input` needs border: 1px solid var(--muted). The line is --muted, not --hair (1.45:1 on Paper), so the "
                         "field's boundary meets WCAG 1.4.11 (3:1)")
        self.assertNotIn("--hair", without_comments(self.field),
                         "static/css/components/field.css: draw nothing of a field in --hair; a control's boundary needs 3:1 (WCAG 1.4.11) and --hair is 1.45:1 on Paper, --muted 6.6:1")
        for name, value in (("background", "var(--box)"), ("color", "var(--ink)"), ("border-radius", "0"),
                            ("appearance", "none"), ("-webkit-appearance", "none"), ("width", "100%"), ("min-width", "0")):
            self.assertEqual(box.get(name), value, f"static/css/components/field.css: `.field__input` needs {name}: {value}")
        placeholder = self.declared(self.field, ".field__input::placeholder", "static/css/components/field.css")
        self.assertEqual((placeholder.get("color"), placeholder.get("opacity")), ("var(--muted)", "1"),
                         "static/css/components/field.css: a placeholder is --muted at full opacity (Firefox dims it otherwise)")

    def test_focus_is_visible_and_a_focused_field_clears_the_top_fade(self):
        ring = self.declared(self.base, ":focus-visible", "static/css/base.css")
        self.assertEqual(ring.get("outline"), "var(--outline-width) solid var(--muted)",
                         "static/css/base.css: the global `:focus-visible` outline is the focus ring of a field, whose line is --muted already; restore `outline: var(--outline-width) solid var(--muted)` (spec 3.4, 9)")
        self.assertTrue(self.declared(self.field, ".field__input", "static/css/components/field.css").get("scroll-margin-top"),
                        "static/css/components/field.css: `.field__input` needs scroll-margin-top, so a focused field stops with its label below the phone's top fade")
        for where, css in (("static/css/components/field.css", self.field), ("static/css/components/form_errors.css", self.errors)):
            self.assertNotRegex(without_comments(css), r"outline:\s*(none|0)\b", f"{where}: never remove the focus outline (spec 3.4, 9)")

    def test_an_error_is_a_solid_ink_line_and_a_full_ink_sentence_with_no_colour(self):
        where = "static/css/components/field.css"
        self.assertEqual(self.declared(self.field, '.field__input[aria-invalid="true"]', where).get("border-color"), "var(--ink)", f"{where}: an invalid field's line is solid --ink")
        self.assertEqual(self.declared(self.field, ".field__error p", where).get("color"), "var(--ink)", f"{where}: the error sentence is full --ink, not --muted")
        self.assertEqual(self.declared(self.field, ".field__flag", where).get("color"), "var(--ink)", f"{where}: the Check this label is --ink")
        self.assertEqual(self.declared(self.errors, ".form-errors", "static/css/components/form_errors.css").get("border-left"), "2px solid var(--ink)",
                         "static/css/components/form_errors.css: the summary has a solid --ink line down its left")

    def test_the_browser_finds_fault_only_after_interaction_and_in_its_own_rule(self):
        self.assertEqual(self.declared(self.field, ".field__input:user-invalid", "static/css/components/field.css").get("border-color"), "var(--ink)")
        self.assertNotRegex(without_comments(self.field), r"(?<!user-):invalid",
                            "static/css/components/field.css: use :user-invalid, never :invalid, which matches a required empty field before anyone has touched it and makes every form load looking wrong")
        for selectors, _ in rules(self.field):
            joined = " ".join(selectors)
            self.assertFalse(":user-invalid" in joined and "aria-invalid" in joined,
                             "static/css/components/field.css: give :user-invalid a rule of its own; a browser that does not know it drops the whole rule it is in, aria-invalid's line with it")

    def test_autofill_is_held_off_in_both_spellings_and_when_motion_is_off(self):
        where = "static/css/components/field.css"
        for selector in (".field__input:-webkit-autofill", ".field__input:autofill"):
            held = self.declared(self.field, selector, where)
            self.assertIn("background-color 600000s", held.get("transition", ""),
                          f"{where}: `{selector}` holds the browser's tint off with a transition delay that never ends (its !important background cannot be beaten by a rule)")
            self.assertEqual(held.get("-webkit-text-fill-color"), "var(--ink)", f"{where}: `{selector}` keeps the text --ink")
            self.assertEqual(held.get("filter"), "none", f"{where}: `{selector}` turns off Firefox's tint")
            quiet = self.declared(self.field, f'[data-motion="off"] {selector}', where)
            self.assertIn("var(--paper)", quiet.get("box-shadow", ""),
                          f"{where}: with Motion off every transition is off (base.css), so `{selector}` is painted --paper over the tint with an inset box-shadow")
        for selectors, _ in rules(self.field):
            joined = " ".join(selectors)
            self.assertFalse(":autofill" in joined and ":-webkit-autofill" in joined,
                             f"{where}: one rule for each spelling; a browser that does not know one drops the whole selector list")

    def test_the_main_action_is_a_hairline_box_on_the_box(self):
        action = self.declared(self.base, ".action", "static/css/base.css")
        for name, value in (("border", "1px solid var(--hair)"), ("background", "var(--box)"), ("color", "var(--ink)"), ("border-radius", "0"),
                            ("font-family", "var(--font-mono)"), ("text-transform", "uppercase")):
            self.assertEqual(action.get(name), value, f"static/css/base.css: `.action` needs {name}: {value} (the owner's main action: a mono label in a 1px --hair box on --box)")
        self.assertEqual(self.declared(self.base, ".action::before", "static/css/base.css").get("inset"), "0",
                         "static/css/base.css: the action's fill (.interactive::before) is inset to the box's own edge")
        self.assertTrue(declarations(self.base, ".action.interactive"), "static/css/base.css: `.action.interactive` shares .action's rule, so button.label and .label.interactive cannot undo the box")

    def test_actions_of_equal_weight_share_a_row_in_one_width(self):
        where = "static/css/base.css"
        self.assertEqual(self.declared(self.base, ".form-actions > *", where).get("flex"), "1 1 9rem", f"{where}: each item of `.form-actions` is flex: 1 1 9rem, so two share a row and wrap below 19rem")
        direct = self.declared(self.base, ".form-actions > .action", where)
        self.assertEqual(direct.get("margin"), "0", f"{where}: an .action directly in `.form-actions` has no margin of its own (`.action.interactive` weighs more than `.form-actions > *`)")
        self.assertNotIn("flex", direct, f"{where}: an .action directly in `.form-actions` keeps its item's flex, or Approve and Deny are two widths")
        self.assertEqual(self.declared(self.base, ".form-actions > * > .action", where).get("flex"), "1 1 auto", f"{where}: an .action in a <form> item fills the form")

    def test_secondary_links_and_notices_are_quiet(self):
        links = self.declared(self.base, ".form-links", "static/css/base.css")
        for name, value in (("color", "var(--muted)"), ("font-family", "var(--font-mono)"), ("text-transform", "uppercase")):
            self.assertEqual(links.get(name), value, f"static/css/base.css: `.form-links` needs {name}: {value} (quieter than the main action)")
        notice = self.declared(self.base, ".notice", "static/css/base.css")
        self.assertEqual((notice.get("color"), notice.get("border-left")), ("var(--muted)", "2px solid var(--hair)"),
                         "static/css/base.css: `.notice` is a quiet --muted line with the reading column's 2px hairline down its left")

    def test_a_separator_and_the_link_after_it_are_one_flex_item_that_does_not_wrap_inside(self):
        where = "static/css/base.css"
        item = self.declared(self.base, ".form-links__item", where)
        self.assertEqual(item.get("display"), "flex", f"{where}: `.form-links__item` is a flex row, so its parts never wrap apart (a separator left at the end of a line)")
        self.assertIn(item.get("flex-wrap", "nowrap"), ("nowrap",), f"{where}: `.form-links__item` must not wrap its parts onto lines of their own")
        self.assertEqual(item.get("align-items"), "baseline", f"{where}: `.form-links__item` aligns its parts on the baseline, as `.form-links` does")

    def test_stacked_secondary_links_are_a_column_from_the_left(self):
        where = "static/css/base.css"
        stacked = self.declared(self.base, ".form-links--stacked", where)
        self.assertEqual((stacked.get("flex-direction"), stacked.get("align-items")), ("column", "flex-start"),
                         f"{where}: `.form-links--stacked` puts the links one above the other, from the left (the sign-in page's, which would not fit one line with a separator)")

    def test_checkbox_and_radio_rows_draw_their_own_boxes_and_marks(self):
        where = "static/css/base.css"
        self.assertRegex(self.base, r"\.check__input \{[^}]*\n  appearance: none;", f"{where}: `.check__input` draws its own box (appearance: none)")
        self.assertEqual(self.declared(self.base, ".check__input", where).get("border"), "1px solid var(--muted)", f"{where}: `.check__input` draws its box in --muted")
        self.assertIn("var(--ink)", self.declared(self.base, ".check__input:checked", where).get("background-image", ""), f"{where}: a checked box holds a square in --ink")
        self.assertIn("var(--ink)", self.declared(self.base, '.check__input[type="radio"]:checked', where).get("background-image", ""), f"{where}: a checked radio holds a dot in --ink")
        self.assertEqual(self.declared(self.base, '.check__input[type="radio"]', where).get("border-radius"), "50%")
        self.assertRegex(self.base, r"@media \(forced-colors: active\) \{\s*\.check__input \{[^}]*appearance: auto;",
                         f"{where}: in forced colours the cloth is off and the system's own box and mark are right")

    def test_nothing_climbs_above_the_corners_or_makes_main_a_backdrop_root(self):
        # AGENTS.md, Site checks: nothing inside <main> uses a z-index above 2 (the corners are at 3), and <main> has no filter, opacity,
        # mask or will-change outside a change's cross-fade, the narrow top fade and the phone sheet's mask.
        ours = (".form-page", ".layout-form", ".action", ".form-actions", ".form-links", ".notice", ".check")
        found = [(selectors, properties) for selectors, properties in rules(self.base) if any(selector.startswith(ours) for selector in selectors)]
        found += rules(self.field) + rules(self.errors)
        for selectors, properties in found:
            z_index = properties.get("z-index", "0")
            if re.fullmatch(r"-?\d+", z_index):
                self.assertLessEqual(int(z_index), 2, f"static/css: `{selectors[0]}` has z-index {z_index}; nothing inside <main> may use more than 2")
        main = declarations(self.base, ".layout-form .shell-main")
        for name in ("filter", "backdrop-filter", "opacity", "mask-image", "-webkit-mask-image", "will-change"):
            self.assertNotIn(name, main, f"static/css/base.css: `.layout-form .shell-main` must not set {name}: it would make <main> a backdrop root (AGENTS.md, Site checks)")

    def test_a_hover_waits_for_a_pointer_that_can_hover(self):
        # CI's Chromium reports hover: none, so a hover rule is gated on `not all and (pointer: coarse)`, never on (hover: hover).
        self.assertRegex(self.base, r"@media not all and \(pointer: coarse\) \{\s*\.form-page :is\(p, li, dd, \.notice\) a:not\(\.interactive, \.label\):hover \{",
                         "static/css/base.css: gate the form column's link hover on `not all and (pointer: coarse)`")


class FormCatalogTests(TestCase):
    longMessage = False

    def setUp(self):
        staff = get_user_model().objects.create_user("staff", "staff@example.com", "a-test-only-password", is_staff=True)
        self.client.force_login(staff)
        self.html = self.client.get("/app/components/").content.decode()

    def test_the_field_is_shown_in_each_of_its_states_in_every_frame(self):
        for state in ("blank", "help", "error", "code"):
            for frame in range(4):
                with self.subTest(state=state, frame=frame):
                    self.assertIn(f'id="catalog-field-{state}-{frame}"', self.html,
                                  f"backend/pages/catalog.py: the field's Entry shows a {state} state; give each state an id of its own")

    def test_the_error_state_is_the_only_invalid_field_and_shows_its_sentence(self):
        self.assertEqual(self.html.count('aria-invalid="true"'), 4, "backend/pages/catalog.py: exactly one state of the field has errors, in four frames")
        self.assertEqual(self.html.count("An account with this email address already exists."), 4)
        self.assertEqual(self.html.count('aria-describedby="catalog-field-error-0_error"'), 1)

    def test_extra_arrives_as_written(self):
        self.assertEqual(self.html.count('placeholder="XXXX-XXXX" spellcheck="false"'), 4,
                         "backend/pages/catalog.py: pass the code state's extra through mark_safe, as a literal in a page's template is, or its quotes print escaped")

    def test_both_components_are_shown_inside_the_form_column(self):
        self.assertEqual(self.html.count('<div class="form-page">'), 4 * 2,
                         'backend/pages/catalog.py: pass within="form-page" to the field and form_errors Entries, so the catalog draws them in the column the page does')

    def test_the_form_errors_are_alerts_in_both_states(self):
        self.assertEqual(self.html.count('<div class="form-errors" role="alert">'), 4 * 2)
        self.assertIn("The username, email, or password is incorrect. Try again.", self.html)


class CatalogStatesTests(SimpleTestCase):
    longMessage = False

    def test_each_state_of_a_component_has_an_id_of_its_own(self):
        for entry in catalog.entries():
            samples = (entry.context, *entry.states)
            ids = [sample["id"] for sample in samples if "id" in sample]
            with self.subTest(component=entry.name):
                self.assertEqual(len(ids), len(set(ids)), "backend/pages/catalog.py: each state of a component needs an id of its own, or the catalog repeats an id")

    def test_the_field_shows_blank_help_error_and_code(self):
        entry = next(entry for entry in catalog.entries() if entry.name == "field")
        samples = (entry.context, *entry.states)
        self.assertEqual(len(samples), 4, "backend/pages/catalog.py: the field's Entry shows four states: blank, with help, with an error and a code")
        self.assertFalse(any(key in samples[0] for key in ("help", "errors", "mono")))
        self.assertTrue(samples[1]["help"] and samples[2]["errors"] and samples[3]["mono"])
