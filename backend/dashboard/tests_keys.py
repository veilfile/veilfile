"""Keys and Connected apps (spec 4.8, AGENTS.md invariant 1): the list, the new key shown once, the one-time form token, the refusals, and the
confirmation pages that take a key or an app back.

Every class sets longMessage = False, so that a failure prints its own message, which names the file and the fix, and not the page it searched.
"""
import hashlib
import json
import logging
import re
import secrets
from contextlib import contextmanager
from datetime import timedelta
from html import unescape
from html.parser import HTMLParser
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application, RefreshToken

from dashboard import forms
from drop_core import account, funnel
from drop_core.models import APIKey, EmailVerification, FunnelEvent, get_or_create_workspace
from drop_core.oauth import OAUTH_SCOPE_MCP_USE
from drop_core.plans import PLANS
from pages import tests_forms_on_the_shell as form_pages
from pages.tests_forms import tags

User = get_user_model()
KEY = re.compile(r"vlf_[A-Za-z0-9_-]{32}")  # a raw key: "vlf_" and token_urlsafe(24)
KEYS_VIEW = "backend/dashboard/keys.py"
KEYS_PAGE = "backend/templates/dashboard/keys.html"
MADE_PAGE = "backend/templates/dashboard/key_made.html"
REVOKE_PAGE = "backend/templates/dashboard/key_revoke.html"
APPS_PAGE = "backend/templates/dashboard/apps.html"
DISCONNECT_PAGE = "backend/templates/dashboard/app_disconnect.html"
FORMS = "backend/dashboard/forms.py"
KEYS_URL = "/app/keys/"
APPS_URL = "/app/apps/"
LOGIN = "/accounts/login/"
REDIRECT_URI = "http://127.0.0.1:9876/callback"
NOINDEX = "noindex, nofollow"


def make_user(name, verified=True, plan="free"):
    """A signed-in-able person with a workspace on this plan. No password is set, so nothing here pays for a hash."""
    user = User.objects.create_user(name, email=f"{name}@example.com")
    if verified:
        EmailVerification.objects.create(user=user, email=user.email, is_verified=True, verified_at=timezone.now())
    workspace = get_or_create_workspace(user)
    workspace.plan = plan
    workspace.save(update_fields=["plan"])
    return user


def make_app(name="Claude Code", redirect_uris=REDIRECT_URI):
    return Application.objects.create(
        name=name,
        client_type=Application.CLIENT_PUBLIC,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
        redirect_uris=redirect_uris,
    )


def connect(user, app):
    """The user has authorized the app: a live mcp:use access token and its refresh token."""
    access = AccessToken.objects.create(
        user=user, application=app, token=secrets.token_urlsafe(32),
        expires=timezone.now() + timedelta(hours=1), scope=OAUTH_SCOPE_MCP_USE,
    )
    refresh = RefreshToken.objects.create(user=user, application=app, token=secrets.token_urlsafe(32), access_token=access)
    return access, refresh


def main_of(html):
    return html[html.index("<main"):html.index("</main>")]


def hidden_token(html):
    """The one-time token in the new-key form."""
    found = [attrs for attrs in tags(html, "input") if attrs.get("name") == "token"]
    assert len(found) == 1 and found[0].get("type") == "hidden", f"{KEYS_PAGE}: the form needs exactly one hidden input named token"
    return found[0]["value"]


class TableRows(HTMLParser):
    """The rows of a dashboard table: for each <tr> of its <tbody>, {data-label: the cell's words}, plus "links" (the hrefs in the row) and
    "times" (the attributes of each <time>)."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.rows, self._row, self._label, self._text, self._body = [], None, None, [], False
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tbody":
            self._body = True
        elif tag == "tr" and self._body:
            self._row = {"links": [], "times": []}
        elif tag == "td" and self._row is not None:
            self._label, self._text = attrs.get("data-label"), []
        elif tag == "a" and self._label is not None:
            self._row["links"].append(attrs.get("href"))
        elif tag == "time" and self._label is not None:
            self._row["times"].append(attrs)

    def handle_data(self, data):
        if self._label is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self._label is not None:
            self._row[self._label] = " ".join("".join(self._text).split())
            self._label = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag == "tbody":
            self._body = False


class Notices(HTMLParser):
    """The words of every .notice with role="status" on the page: how a message is shown (backend/templates/layouts/app.html), whatever the
    element and nesting it uses."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.found, self._open = [], []
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        attrs = dict(attrs)
        shown = "notice" in (attrs.get("class") or "").split() and attrs.get("role") == "status"
        self._open.append([tag, shown, []])

    def handle_data(self, data):
        for element in self._open:
            element[2].append(data)

    def handle_endtag(self, tag):
        names = [element[0] for element in self._open]
        if tag not in names:
            return
        depth = len(names) - 1 - names[::-1].index(tag)
        while len(self._open) > depth:
            _, shown, parts = self._open.pop()
            if shown:
                self.found.append(" ".join("".join(parts).split()))


class Recorded(logging.Handler):
    """Every record that reaches it, formatted the way a log line would be written (message, arguments and traceback)."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


@contextmanager
def watching_logs():
    """Every logger that could carry a line about the request, recording for the length of the block. The site's own loggers are set to DEBUG
    (they do not propagate to the root, config/settings.py LOGGING, so each is named); Django's keep the level they have."""
    recorded = Recorded()
    own = [logging.getLogger(name) for name in ("dashboard", "drop_core", "drop_billing")]
    others = [logging.getLogger(name) for name in ("", "pages", "django", "django.request", "django.security.csrf")]
    levels = [logger.level for logger in own]
    for logger in own + others:
        logger.addHandler(recorded)
    for logger in own:
        logger.setLevel(logging.DEBUG)
    try:
        yield recorded
    finally:
        for logger in own + others:
            logger.removeHandler(recorded)
        for logger, level in zip(own, levels):
            logger.setLevel(level)


class SignedInCase(TestCase):
    """A person with a verified email on the Team plan (room for five keys), signed in."""

    longMessage = False
    plan = "team"

    def setUp(self):
        self.user = make_user("owner", plan=self.plan)
        self.workspace = get_or_create_workspace(self.user)
        self.client.force_login(self.user)

    def keys_page(self):
        return self.client.get(KEYS_URL)

    def create(self, name="ci-runner", token=None, client=None):
        """Open the Keys page for its one-time token (unless one is given) and send the form as a browser would."""
        client = client or self.client
        if token is None:
            token = hidden_token(client.get(KEYS_URL).content.decode())
        return client.post(KEYS_URL, {"name": name, "token": token})

    def make_key(self, name="agent", active=True, **fields):
        key, raw = APIKey.create_key(self.workspace, name)
        if not active or fields:
            for field, value in {"is_active": active, **fields}.items():
                setattr(key, field, value)
            key.save()
        return key, raw


class KeyTokenTests(SimpleTestCase):
    """dashboard/forms.py: the one-time token. A session is a dict here; Django's is one too, as far as the helpers go."""

    longMessage = False

    def test_a_token_is_issued_once_and_spent_once(self):
        session = {}
        token = forms.issue_key_token(session)
        self.assertTrue(token and token == token.strip(), f"{FORMS}: issue_key_token returns a non-empty URL-safe string")
        self.assertTrue(forms.spend_key_token(session, token), f"{FORMS}: an issued token is spendable")
        self.assertFalse(forms.spend_key_token(session, token), f"{FORMS}: a spent token must not be spendable again: a reload resends the POST")

    def test_two_posts_of_one_token_that_both_pass_the_session_check_spend_it_once(self):
        # A double click: both requests read the token from their own copy of the session before either saves. The session list cannot tell
        # them apart; the cache's add() can, because only one add of a key succeeds.
        token = forms.issue_key_token({})
        first, second = ({forms.KEY_TOKENS: [token]}, {forms.KEY_TOKENS: [token]})
        self.assertTrue(forms.spend_key_token(first, token), f"{FORMS}: the first spend of a token succeeds")
        self.assertFalse(forms.spend_key_token(second, token),
                         f"{FORMS}: spend_key_token must also claim the token with cache.add, so that a second request that still sees it in its session is refused")

    def test_tokens_are_random(self):
        session = {}
        self.assertEqual(len({forms.issue_key_token(session) for _ in range(20)}), 20, f"{FORMS}: issue_key_token must use secrets, one token per call")

    def test_what_was_never_issued_is_not_spendable(self):
        session = {}
        forms.issue_key_token(session)
        for token in ("", None, "made-up", 12, ["x"], "x" * 500):
            with self.subTest(token=token):
                self.assertFalse(forms.spend_key_token(session, token), f"{FORMS}: spend_key_token must refuse {token!r}, which this session never issued")
        self.assertEqual(len(session[forms.KEY_TOKENS]), 1, f"{FORMS}: a refused token must leave the unspent ones alone")

    def test_a_session_with_nothing_or_junk_spends_nothing_and_does_not_raise(self):
        for held in (None, "a string", 7, {"a": 1}):
            with self.subTest(held=held):
                session = {forms.KEY_TOKENS: held}
                self.assertFalse(forms.spend_key_token(session, "x"), f"{FORMS}: spend_key_token must read a session value that is not a list as no tokens")
        self.assertFalse(forms.spend_key_token({}, "x"), f"{FORMS}: spend_key_token must read a session with no tokens as none")

    def test_two_tabs_both_work_and_the_sixth_page_retires_the_oldest(self):
        session = {}
        issued = [forms.issue_key_token(session) for _ in range(forms.KEPT_TOKENS + 1)]
        self.assertEqual(len(session[forms.KEY_TOKENS]), forms.KEPT_TOKENS, f"{FORMS}: keep the last {forms.KEPT_TOKENS} unspent tokens, no more")
        self.assertFalse(forms.spend_key_token(session, issued[0]), f"{FORMS}: the oldest token goes when the sixth is issued")
        for token in reversed(issued[1:]):
            self.assertTrue(forms.spend_key_token(session, token), f"{FORMS}: the last {forms.KEPT_TOKENS} tokens stay spendable, in any order, so two tabs work")

    def test_junk_already_in_the_session_is_dropped_when_a_token_is_issued(self):
        for held, kept in ((["fine", 5, None], ["fine"]), ("abc", []), (7, []), (None, []), ({"a": 1}, [])):
            with self.subTest(held=held):
                session = {forms.KEY_TOKENS: held}
                token = forms.issue_key_token(session)
                self.assertEqual(session[forms.KEY_TOKENS], [*kept, token],
                                 f"{FORMS}: issue_key_token keeps the strings of a list and nothing else (a string is not walked a character at a time)")


class KeyFormTests(SimpleTestCase):
    longMessage = False

    def check(self, name):
        form = forms.KeyForm({"name": name})
        return form, form.is_valid()

    def test_a_name_is_optional_and_stripped(self):
        for given, expected in (("", ""), ("   ", ""), ("  ci-runner \n", "ci-runner"), ("x" * 255, "x" * 255)):
            with self.subTest(name=given[:20]):
                form, valid = self.check(given)
                self.assertTrue(valid, f"{FORMS}: {given[:20]!r} is a fine key name")
                self.assertEqual(form.cleaned_data["name"], expected, f"{FORMS}: the name is stripped; blank stays blank (the account function makes it Untitled key)")

    def test_a_name_over_the_models_length_is_refused_in_a_sentence(self):
        form, valid = self.check("x" * 256)
        self.assertFalse(valid, f"{FORMS}: a 256-character name must be refused: the column holds {forms.NAME_LENGTH}")
        self.assertEqual(form.errors["name"], ["Use 255 characters or fewer."], f"{FORMS}: say the limit in the page's voice")

    def test_a_name_that_is_not_one_plain_line_is_refused_in_a_sentence(self):
        for name in ("two\nlines", "tab\there", "bell\x07", "nul\x00byte"):
            with self.subTest(name=repr(name)):
                form, valid = self.check(name)
                self.assertFalse(valid, f"{FORMS}: {name!r} holds a control character, which the JSON endpoint refuses too")
                self.assertEqual(form.errors["name"], ["Use a single line of plain text."], f"{FORMS}: one sentence for every control character, NUL included")


class KeysPageTests(SignedInCase):
    def test_a_signed_out_visit_goes_to_sign_in(self):
        client = Client()
        for method in (client.get, client.post):
            response = method(KEYS_URL)
            self.assertEqual((response.status_code, response.get("Location")), (302, f"{LOGIN}?next={KEYS_URL}"),
                             f"{KEYS_VIEW}: Keys is a dashboard view (dashboard_view): signed out, a GET and a POST go to sign in")
        self.assertEqual(APIKey.objects.count(), 0, f"{KEYS_VIEW}: a signed-out POST makes no key")

    def test_the_page_is_titled_headed_and_kept_from_search(self):
        response = self.keys_page()
        html = response.content.decode()
        self.assertEqual(response.status_code, 200, f"{KEYS_VIEW}: the Keys page answers 200")
        self.assertEqual(response.get("X-Robots-Tag"), NOINDEX, f"{KEYS_VIEW}: dashboard_view adds the noindex header")
        self.assertIn("no-store", response.get("Cache-Control", ""), f"{KEYS_VIEW}: the page carries a one-time token; send Cache-Control: no-store so Back does not show a spent one")
        self.assertIn("<title>Keys | Veilfile</title>", html, "backend/pages/context_processors.py: the Keys section's title is Keys | Veilfile")
        self.assertEqual(re.findall(r"<h1[^>]*>(.*?)</h1>", main_of(html)), ["Keys"], f"{KEYS_PAGE}: one h1, Keys, inside <main>")
        self.assertEqual(html.count('<meta name="robots" content="noindex, nofollow">'), 1, f"{KEYS_PAGE}: every dashboard page has the robots meta in its meta block, once (the layout may write it too: a page's block replaces the layout's)")

    def test_an_account_with_no_workspace_yet_gets_an_empty_page(self):
        client = Client()
        client.force_login(User.objects.create_user("fresh", email="fresh@example.com"))
        response = client.get(KEYS_URL)
        self.assertEqual(response.status_code, 200, f"{KEYS_VIEW}: a person's first visit to Keys makes the workspace (get_or_create_workspace), as the JSON endpoint does")
        self.assertIn("You have no keys yet.", response.content.decode())

    def test_the_page_says_what_a_key_is_for(self):
        main = main_of(self.keys_page().content.decode())
        self.assertIn("<p>Keys start with <code>vlf_</code>. Give each agent the variable name, not the value; revoke individually when a run ends.</p>", main,
                      f"{KEYS_PAGE}: keep the React dashboard's sentence about keys (the plan's copy rule)")

    def test_a_workspace_without_keys_is_told_so(self):
        main = main_of(self.keys_page().content.decode())
        self.assertIn("You have no keys yet. Create one above to get started.", main, f"{KEYS_PAGE}: say there are no keys when there are none")
        self.assertNotIn("<table", main, f"{KEYS_PAGE}: no table without rows")

    def test_the_list_shows_every_key_with_its_state_and_times(self):
        used, _ = self.make_key("used", last_used_at=timezone.now() - timedelta(hours=3))
        self.make_key("idle")
        self.make_key("gone", active=False)
        html = self.keys_page().content.decode()
        rows = {row["Name"]: row for row in TableRows(html).rows}
        self.assertEqual(set(rows), {"used", "idle", "gone"}, f"{KEYS_PAGE}: list every key of the workspace, revoked ones too, one row each")
        self.assertEqual([row["State"] for row in (rows["used"], rows["idle"], rows["gone"])], ["Active", "Active", "Revoked"],
                         f"{KEYS_PAGE}: the State cell says Active or Revoked")
        self.assertEqual(rows["used"]["Prefix"], used.key_prefix, f"{KEYS_PAGE}: the Prefix cell is the key's first 12 characters, key_prefix")
        self.assertEqual(rows["idle"]["Last used"], "Never", f"{KEYS_PAGE}: a key that was never used says Never")
        self.assertEqual([len(row["times"]) for row in (rows["used"], rows["idle"], rows["gone"])], [2, 1, 1],
                         f"{KEYS_PAGE}: Created and a Last used that exists are <time> elements (the filter utc_time), so the page can write them in the visitor's zone")
        for time in rows["used"]["times"]:
            self.assertIn("data-local", time, f"{KEYS_PAGE}: each <time> carries data-local, the mark behaviors/local-time.js looks for")
            self.assertRegex(time["datetime"], r"^\d{4}-\d\d-\d\dT\d\d:\d\dZ$", f"{KEYS_PAGE}: the datetime is UTC")
        self.assertEqual(rows["used"]["links"], [f"{KEYS_URL}{used.id}/revoke/"], f"{KEYS_PAGE}: an active key links to its confirmation page")
        self.assertEqual(rows["gone"]["links"], [], f"{KEYS_PAGE}: a revoked key has no Revoke link")

    def test_the_table_is_named_and_its_headers_are_column_headers(self):
        self.make_key("one")
        html = self.keys_page().content.decode()
        self.assertIn("<caption>API keys</caption>", html, f"{KEYS_PAGE}: a table has a caption (spec 9)")
        headers = tags(html, "th")
        self.assertEqual([header.get("scope") for header in headers], ["col"] * 6, f"{KEYS_PAGE}: every header cell has scope=col")
        self.assertEqual(tags(html, "table")[0]["class"], "rows", f"{KEYS_PAGE}: the table is class rows, the stacked-rows pattern of base.css")
        cells = tags(main_of(html), "td")
        self.assertTrue(cells and all(cell.get("data-label") for cell in cells), f"{KEYS_PAGE}: every td has its data-label, so a phone's stacked row keeps each value's label")

    def test_a_revoke_link_names_its_key(self):
        self.make_key("deploy")
        row = TableRows(self.keys_page().content.decode()).rows[0]
        self.assertEqual(row["Actions"], "Revoke deploy", f"{KEYS_PAGE}: the link's words are Revoke and, hidden, the key's name, so a list of links is not a list of identical ones")

    def test_only_this_workspaces_keys_are_listed(self):
        self.make_key("mine")
        other = make_user("other")
        APIKey.create_key(get_or_create_workspace(other), "theirs")
        names = [row["Name"] for row in TableRows(self.keys_page().content.decode()).rows]
        self.assertEqual(names, ["mine"], f"{KEYS_VIEW}: list_keys is the signed-in person's workspace and no other")

    def test_a_name_is_printed_whole_and_escaped(self):
        name = "<script>alert(1)</script>" + "k" * 230
        self.make_key(name)
        html = self.keys_page().content.decode()
        self.assertNotIn("<script>alert(1)", html, f"{KEYS_PAGE}: a key's name is a visitor's words: print it escaped")
        self.assertEqual(TableRows(html).rows[0]["Name"], name, f"{KEYS_PAGE}: a 255-character name is printed whole")

    def test_the_new_key_form(self):
        html = self.keys_page().content.decode()
        forms_in_main = tags(main_of(html), "form")
        self.assertEqual(len(forms_in_main), 1, f"{KEYS_PAGE}: one form, the new key's")
        self.assertEqual(forms_in_main[0]["method"], "post", f"{KEYS_PAGE}: the form is a POST")
        self.assertEqual(len(re.findall(r'name="csrfmiddlewaretoken"', main_of(html))), 1, f"{KEYS_PAGE}: the form holds the CSRF token (the page's own <form>, never a component)")
        self.assertIn('<label class="field__label label" for="id_name">Key name</label>', html, f"{KEYS_PAGE}: a visible label for the name field")
        self.assertNotIn("autofocus", main_of(html), f"{KEYS_PAGE}: a page that is not only a form takes focus nowhere on a plain visit")
        self.assertIn('class="action interactive" type="submit">Create key</button>', html, f"{KEYS_PAGE}: the main action reads Create key")
        self.assertEqual(self.client.session[forms.KEY_TOKENS], [hidden_token(html)], f"{KEYS_VIEW}: the token in the form is the one kept in the session")

    def test_every_visit_issues_a_new_token_and_five_are_kept(self):
        seen = [hidden_token(self.keys_page().content.decode()) for _ in range(forms.KEPT_TOKENS + 1)]
        self.assertEqual(len(set(seen)), len(seen), f"{KEYS_VIEW}: each visit gets a token of its own")
        self.assertEqual(self.client.session[forms.KEY_TOKENS], seen[1:], f"{FORMS}: the session keeps the last {forms.KEPT_TOKENS} tokens")

    def test_other_methods_are_refused_and_change_nothing(self):
        key, _ = self.make_key("keep")
        for method in (self.client.put, self.client.delete, self.client.patch):
            self.assertEqual(method(KEYS_URL).status_code, 405, f"{KEYS_VIEW}: Keys takes GET, HEAD and POST only")
        key.refresh_from_db()
        self.assertTrue(key.is_active)


class KeysTableShapeTests(SignedInCase):
    def test_every_row_has_as_many_cells_as_the_header_row_for_active_and_revoked_keys(self):
        self.make_key("live")
        self.make_key("gone", active=False)
        main = main_of(self.keys_page().content.decode())
        table = main[main.index("<table"):main.index("</table>")]
        header = len(re.findall(r"<th\b", table))
        rows = re.findall(r"<tr>(.*?)</tr>", table[table.index("<tbody"):], flags=re.S)
        self.assertEqual(len(rows), 2, f"{KEYS_PAGE}: a row for each key, revoked ones too")
        for row in rows:
            with self.subTest(row=row[:40]):
                self.assertEqual(len(re.findall(r"<td\b", row)), header, f"{KEYS_PAGE}: each row needs one <td> for each header cell; a revoked key's Actions cell is empty: <td data-label=\"Actions\"></td>")
        self.assertIn('<td data-label="Actions"></td>', table, f"{KEYS_PAGE}: write the empty cell with no whitespace inside, as the rows pattern requires")


class NewKeyTests(SignedInCase):
    def test_the_post_answers_with_the_key_in_a_code_block_and_nowhere_else(self):
        response = self.create("ci-runner")
        html = response.content.decode()
        raw = KEY.findall(html)
        self.assertEqual(response.status_code, 200, f"{KEYS_VIEW}: a POST that makes a key answers 200 with the key")
        self.assertEqual(len(raw), 1, f"{MADE_PAGE}: the raw key is written once on the page, in the code block")
        self.assertEqual(html.count(raw[0]), 1, f"{MADE_PAGE}: the raw key appears in one place only (not in an attribute, a title or a script)")
        self.assertIn(f'<pre tabindex="0" id="new-key"><code>{raw[0]}</code></pre>', html, f"{MADE_PAGE}: the key is the code of the code_block with id new-key")
        self.assertIn('data-copy="new-key" aria-label="Copy the new API key">Copy</button>', html, f"{MADE_PAGE}: the Copy button is named Copy the new API key")
        key = APIKey.objects.get()
        self.assertEqual((key.name, key.is_active, key.key_prefix), ("ci-runner", True, raw[0][:12]), f"{KEYS_VIEW}: the key is made with the name typed")

    def test_the_key_is_a_real_key(self):
        raw = KEY.findall(self.create().content.decode())[0]
        answer = Client().get("/api/v1/usage/", HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(answer.status_code, 200, f"{KEYS_VIEW}: the key on the page must authenticate an agent: it comes from account.create_key")

    def test_the_answer_is_never_cached_or_indexed(self):
        response = self.create()
        html = response.content.decode()
        self.assertIn("no-store", response.get("Cache-Control", ""), f"{KEYS_VIEW}: _key_made must send Cache-Control: no-store; the key is on this page")
        self.assertEqual(response.get("X-Robots-Tag"), NOINDEX, f"{KEYS_VIEW}: dashboard_view adds the noindex header")
        self.assertIn('<meta name="robots" content="noindex, nofollow">', html, f"{MADE_PAGE}: the meta block has the robots tag")
        self.assertFalse(response.has_header("Location"), f"{KEYS_VIEW}: the key is not carried across a redirect: the answer is the page")

    def test_the_page_warns_and_takes_focus_on_the_warning(self):
        html = self.create().content.decode()
        main = main_of(html)
        self.assertIn('<p id="key-warning" tabindex="-1" autofocus>Copy this key now. You won\'t see it again.</p>', main,
                      f"{MADE_PAGE}: the warning is the React dashboard's sentence, focused on load (tabindex=-1 autofocus), so a screen reader reads it first")
        self.assertEqual(main.count("autofocus"), 1, f"{MADE_PAGE}: one element takes focus on load")
        self.assertLess(main.index('id="key-warning"'), main.index('id="new-key"'), f"{MADE_PAGE}: the warning comes before the key in the reading order")

    def test_the_page_is_a_form_page_with_its_own_title_and_a_way_back(self):
        html = self.create("shown").content.decode()
        main = main_of(html)
        self.assertIn("<title>New API key | Veilfile</title>", html, f"{MADE_PAGE}: title the page New API key | Veilfile")
        self.assertEqual(re.findall(r"<h1[^>]*>(.*?)</h1>", main), ["Your new API key"], f"{MADE_PAGE}: one h1 inside <main>")
        self.assertRegex(html, r'<body class="[^"]*\blayout-form\b', f"{MADE_PAGE}: extend layouts/form.html: the key is not on a page that changes in place")
        self.assertNotIn("data-layout", html, f"{MADE_PAGE}: no layout family: the answer to a POST loads as an ordinary page")
        self.assertIn('<a class="action interactive" href="/app/keys/">Done</a>', main, f"{MADE_PAGE}: Done goes back to Keys, as the main action")
        self.assertIn("Name: <strong>shown</strong>", main, f"{MADE_PAGE}: say which key this is")

    def test_a_blank_name_is_untitled_key_and_a_name_is_stripped(self):
        self.create("   ")
        self.create("  padded  ")
        self.assertEqual(sorted(APIKey.objects.values_list("name", flat=True)), ["Untitled key", "padded"],
                         f"{KEYS_VIEW}: the name goes through KeyForm and account.create_key: stripped, and Untitled key when blank")

    def test_a_255_character_name_is_accepted_and_shown_whole(self):
        name = "n" * 255
        response = self.create(name)
        self.assertEqual(response.status_code, 200, f"{FORMS}: 255 characters is the longest name the model takes")
        self.assertIn(f"Name: <strong>{name}</strong>", response.content.decode(), f"{MADE_PAGE}: print the name whole")

    def test_a_name_is_escaped_on_the_page_that_shows_the_key(self):
        html = self.create("<img src=x onerror=alert(1)>").content.decode()
        self.assertNotIn("<img src=x", html, f"{MADE_PAGE}: the name is a visitor's words: print it escaped")

    def test_making_a_key_is_recorded_in_the_funnel(self):
        self.create()
        self.assertTrue(FunnelEvent.objects.filter(stage=funnel.KEY_ISSUED, workspace=self.workspace).exists(),
                        "backend/drop_core/account.py: create_key records the key_issued funnel event, as the JSON endpoint does")

    def test_the_funnel_event_keeps_the_browsers_anonymous_id(self):
        self.client.cookies["vf_funnel"] = "a" * 32 + ".l"
        self.create()
        event = FunnelEvent.objects.get(stage=funnel.KEY_ISSUED, workspace=self.workspace)
        self.assertEqual(event.anonymous_id, "a" * 32, f"{KEYS_VIEW}: _create passes request=request to account.create_key, so funnel.record sees the browser's vf_funnel cookie")

    def test_the_next_visit_lists_the_key_by_its_prefix_only(self):
        raw = KEY.findall(self.create("listed").content.decode())[0]
        html = self.keys_page().content.decode()
        row = TableRows(html).rows[0]
        self.assertEqual((row["Name"], row["Prefix"], row["State"]), ("listed", raw[:12], "Active"), f"{KEYS_PAGE}: the new key is listed with its prefix")
        self.assertNotIn(raw, html, f"{KEYS_PAGE}: a later visit never shows the whole key")


class RawKeyIsKeptNowhereTests(SignedInCase):
    """AGENTS.md invariant 1: the raw key exists only in the answer to the POST that made it."""

    def make(self):
        """Make a key with the logs watched; return the answer, the raw key, the logs and the token the POST carried."""
        token = hidden_token(self.keys_page().content.decode())
        with watching_logs() as logs, self.assertNoLogs("dashboard", level="DEBUG"):
            response = self.create("secretive", token=token)
        raw = KEY.findall(response.content.decode())
        self.assertEqual(len(raw), 1, "the POST must show the key once, or these checks prove nothing")
        return response, raw[0], logs, token

    def test_not_in_the_session(self):
        _, raw, _, _ = self.make()
        self.assertNotIn(raw, json.dumps(dict(self.client.session)), f"{KEYS_VIEW}: never put a key in the session (invariant 1)")
        for row in Session.objects.all():
            self.assertNotIn(raw, json.dumps(SessionStore().decode(row.session_data)), f"{KEYS_VIEW}: no stored session holds the key (invariant 1)")

    def test_not_in_a_message(self):
        response, raw, _, _ = self.make()
        self.assertEqual([str(message) for message in get_messages(response.wsgi_request)], [],
                         f"{KEYS_VIEW}: a new key sends no message at all; nothing secret goes into one (invariant 1)")
        later = self.keys_page()
        self.assertNotIn(raw, later.content.decode(), f"{KEYS_VIEW}: no message carries the key to the next page")

    def test_not_in_a_cookie_or_a_header(self):
        response, raw, _, _ = self.make()
        jars = [response.cookies, self.client.cookies]
        for jar in jars:
            for name, morsel in jar.items():
                self.assertNotIn(raw, morsel.value, f"{KEYS_VIEW}: the cookie {name} must not hold the key (invariant 1)")
        for name, value in response.headers.items():
            self.assertNotIn(raw, value, f"{KEYS_VIEW}: the header {name} must not hold the key (invariant 1)")

    def test_not_in_any_log_record(self):
        _, raw, logs, _ = self.make()
        self.assertEqual([line for line in logs.lines if raw in line], [], f"{KEYS_VIEW}: nothing logs the key (invariant 1)")
        self.assertEqual([line for line in logs.lines if raw[4:] in line], [], f"{KEYS_VIEW}: nothing logs the part of the key after vlf_ either")

    def test_not_in_the_cache(self):
        _, raw, _, _ = self.make()
        for name, blob in cache._cache.items():  # the in-process cache tests run with (DEBUG): its values are pickles
            self.assertNotIn(raw.encode(), blob, f"{KEYS_VIEW}: the cache entry {name} must not hold the key (invariant 1)")

    def test_not_in_the_database_beyond_its_hash(self):
        _, raw, _, _ = self.make()
        key = APIKey.objects.get()
        self.assertEqual(key.key_hash, hashlib.sha256(raw.encode()).hexdigest(), "backend/drop_core/models.py: only the SHA-256 hash is stored")
        for field in key._meta.concrete_fields:
            self.assertNotIn(raw, str(getattr(key, field.attname)), f"backend/drop_core/models.py: APIKey.{field.name} must not hold the raw key")

    def test_not_in_the_next_page_or_a_reload(self):
        _, raw, _, token = self.make()
        for url in (KEYS_URL, "/app/", APPS_URL, f"{KEYS_URL}?again=1"):
            self.assertNotIn(raw, self.client.get(url).content.decode(), f"{KEYS_VIEW}: {url} must not show the key after the POST that made it")
        resent = self.client.post(KEYS_URL, {"name": "secretive", "token": token})
        self.assertNotIn(raw, resent.content.decode(), f"{KEYS_VIEW}: a reload (the POST sent again) must not show the key")

    def test_a_failure_after_the_token_is_spent_leaves_it_spent(self):
        """SessionMiddleware saves nothing after a 500. Without the explicit save, a crash once the key exists would let a retry make a second one."""
        client = Client(raise_request_exception=False)
        client.force_login(self.user)
        token = hidden_token(client.get(KEYS_URL).content.decode())
        with mock.patch("dashboard.keys.account.create_key", side_effect=RuntimeError("database went away")), \
                self.assertLogs("django.request", level="ERROR"):
            response = client.post(KEYS_URL, {"name": "x", "token": token})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(token, client.session.get(forms.KEY_TOKENS, []), f"{KEYS_VIEW}: _create saves the session once the token is spent (request.session.save())")


class ResentPostTests(SignedInCase):
    """A reload resends the POST. The token it carries is spent, so it makes no second key and says why."""

    def test_the_same_token_twice_makes_one_key(self):
        token = hidden_token(self.keys_page().content.decode())
        first = self.create(token=token)
        second = self.create(token=token)
        self.assertEqual(first.status_code, 200, f"{KEYS_VIEW}: the first POST makes the key")
        self.assertEqual(second.status_code, 409, f"{KEYS_VIEW}: a spent token is a conflict: 409, and never 200, which means a key was made")
        self.assertEqual(APIKey.objects.count(), 1, f"{KEYS_VIEW}: a resent POST makes no second key")
        self.assertEqual(KEY.findall(second.content.decode()), [], f"{KEYS_VIEW}: the second answer shows no key")

    def test_the_answer_says_no_key_was_made_and_why(self):
        token = hidden_token(self.keys_page().content.decode())
        self.create(token=token)
        html = self.create(token=token).content.decode()
        from dashboard import keys  # imported here so that the forms' tests run before keys.py exists
        self.assertIn(unescape(keys.SPENT), unescape(html), f"{KEYS_VIEW}: SPENT is the sentence a spent token gets")
        self.assertIn('<div class="form-errors" role="alert" tabindex="-1" autofocus>', html, f"{KEYS_PAGE}: the sentence is the form_errors alert, and takes focus")
        self.assertEqual(main_of(html).count("autofocus"), 1, f"{KEYS_PAGE}: one element takes focus: the alert")
        self.assertEqual(re.findall(r"<h1[^>]*>(.*?)</h1>", main_of(html)), ["Keys"], f"{KEYS_PAGE}: the answer is the Keys page")
        self.assertEqual(TableRows(html).rows[0]["Name"], "ci-runner", f"{KEYS_PAGE}: the list is there, with the key that was made")
        self.assertIn("no-store", self.create(token=token).get("Cache-Control", ""), f"{KEYS_VIEW}: this page carries a token too")

    def test_the_answer_has_a_fresh_form_that_works(self):
        token = hidden_token(self.keys_page().content.decode())
        self.create(token=token)
        again = self.create(token=token)
        fresh = hidden_token(again.content.decode())
        self.assertNotEqual(fresh, token, f"{KEYS_VIEW}: the page after a refusal carries a new token")
        self.assertEqual(self.create(token=fresh).status_code, 200, f"{KEYS_VIEW}: the new token makes a key")
        self.assertEqual(APIKey.objects.count(), 2)

    def test_a_token_nobody_issued_makes_no_key(self):
        for data in ({"name": "x", "token": "made-up"}, {"name": "x", "token": ""}, {"name": "x"}):
            with self.subTest(data=data):
                self.keys_page()
                response = self.client.post(KEYS_URL, data)
                self.assertEqual(response.status_code, 409, f"{KEYS_VIEW}: a POST without a token this session issued is refused with 409")
        self.assertEqual(APIKey.objects.count(), 0, f"{KEYS_VIEW}: no token, no key")

    def test_another_sessions_token_makes_no_key(self):
        other = Client()
        other.force_login(make_user("someone", plan="team"))
        theirs = hidden_token(other.get(KEYS_URL).content.decode())
        response = self.client.post(KEYS_URL, {"name": "x", "token": theirs})
        self.assertEqual(response.status_code, 409, f"{FORMS}: a token is good in the session that was issued it only")
        self.assertEqual(APIKey.objects.count(), 0)

    def test_two_tabs_each_make_a_key(self):
        one = hidden_token(self.keys_page().content.decode())
        two = hidden_token(self.keys_page().content.decode())
        self.assertEqual(self.create(token=two).status_code, 200, f"{FORMS}: the second tab's token works")
        self.assertEqual(self.create(token=one).status_code, 200, f"{FORMS}: and the first tab's still works: the session keeps the last {forms.KEPT_TOKENS}")
        self.assertEqual(APIKey.objects.count(), 2)

    def test_the_sixth_page_retires_the_first_form(self):
        first = hidden_token(self.keys_page().content.decode())
        for _ in range(forms.KEPT_TOKENS):
            self.keys_page()
        self.assertEqual(self.create(token=first).status_code, 409, f"{FORMS}: a form six pages old is out of date")
        self.assertEqual(APIKey.objects.count(), 0)

    def test_a_missing_csrf_token_is_the_form_not_sent_page_and_makes_no_key(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        token = hidden_token(client.get(KEYS_URL).content.decode())
        with self.assertLogs("django.security.csrf", level="WARNING"):
            response = client.post(KEYS_URL, {"name": "x", "token": token})
        self.assertEqual(response.status_code, 403, "backend/config/settings.py: CSRF_FAILURE_VIEW answers a POST without the CSRF token with 403")
        self.assertIn("Form not sent", response.content.decode(), "backend/pages/errors.py: the shell's Form not sent page")
        self.assertEqual(APIKey.objects.count(), 0, f"{KEYS_VIEW}: a POST without the CSRF token makes no key")
        self.assertEqual(client.session[forms.KEY_TOKENS], [token], f"{KEYS_VIEW}: and does not spend the token: the form on the page still works")


class RefusalTests(SignedInCase):
    """Each refusal answers the Keys page with the status the JSON endpoint gives it, a sentence of its own and no key. A 200 means a key was made."""

    def setUp(self):
        super().setUp()
        self.keys_before = 0

    def refused(self, response, status):
        """The answer is the Keys page with this status, shows no key and made none (keys_before is how many there were)."""
        self.assertEqual(response.status_code, status, f"{KEYS_VIEW}: a refusal has the JSON endpoint's status")
        self.assertEqual(KEY.findall(response.content.decode()), [], f"{KEYS_VIEW}: a refusal shows no key")
        self.assertEqual(APIKey.objects.count(), self.keys_before, f"{KEYS_VIEW}: a refusal makes no key")
        self.assertEqual(re.findall(r"<h1[^>]*>(.*?)</h1>", main_of(response.content.decode())), ["Keys"], f"{KEYS_PAGE}: a refusal answers the Keys page")
        self.assertIn("no-store", response.get("Cache-Control", ""), f"{KEYS_VIEW}: the page after a refusal carries a token, so it is never cached")

    def test_an_unverified_email_is_403_with_a_sentence_and_a_way_to_verify(self):
        EmailVerification.objects.filter(user=self.user).update(is_verified=False)
        response = self.create("waiting")
        html = response.content.decode()
        self.refused(response, 403)
        self.assertIn("Verify your email address before creating API keys. Check your inbox for the verification link.", html,
                      f"{KEYS_VIEW}: say the endpoint's sentence")
        self.assertIn('<a class="label interactive" href="/accounts/verify-email/">Verify your email</a>', html, f"{KEYS_PAGE}: link to /accounts/verify-email/")
        self.assertIn('<div class="form-errors" role="alert" tabindex="-1" autofocus>', html, f"{KEYS_PAGE}: the refusal is the alert and takes focus")
        self.assertIn('value="waiting"', html, f"{KEYS_PAGE}: the name typed is kept")

    def test_the_name_kept_after_a_refusal_is_escaped(self):
        EmailVerification.objects.filter(user=self.user).update(is_verified=False)
        html = self.create('a" autofocus onfocus="alert(1)').content.decode()
        field = next(attrs for attrs in tags(html, "input") if attrs.get("id") == "id_name")
        self.assertEqual(field.get("value"), 'a" autofocus onfocus="alert(1)', f"{KEYS_PAGE}: the field component prints value escaped: the quote stays inside the attribute")
        self.assertNotIn("onfocus", {name for name in field}, f"{KEYS_PAGE}: a name must not become an attribute of the input")

    def test_the_plans_key_cap_is_402_and_names_the_limit(self):
        self.make_key("one")
        self.workspace.plan = "free"
        self.workspace.save()
        self.keys_before = 1
        response = self.create("second")
        html = response.content.decode()
        self.refused(response, 402)
        cap = PLANS["free"]["keys"]
        self.assertIn(f"Your Free plan allows {cap} active key. Revoke one below to make room, or change plans.", html, f"{KEYS_VIEW}: _refusal says the plan's cap, from plans.py")
        self.assertIn('<a class="label interactive" href="/app/billing/">Billing</a>', html, f"{KEYS_PAGE}: link to Billing")

    def test_a_cap_above_one_says_keys(self):
        for index in range(PLANS["team"]["keys"]):
            self.make_key(f"k{index}")
        self.keys_before = PLANS["team"]["keys"]
        response = self.create("one more")
        self.refused(response, 402)
        self.assertIn(f"Your Team plan allows {PLANS['team']['keys']} active keys.", response.content.decode(), f"{KEYS_VIEW}: plural for more than one")

    def test_a_workspace_over_its_cap_after_a_downgrade_is_still_refused(self):
        for index in range(3):
            self.make_key(f"k{index}")
        self.workspace.plan = "free"
        self.workspace.save()
        self.keys_before = 3
        self.refused(self.create("more"), 402)

    def test_a_revoked_key_makes_room(self):
        self.workspace.plan = "free"
        self.workspace.save()
        self.make_key("old", active=False)
        self.assertEqual(self.create("fresh").status_code, 200, "backend/drop_core/key_caps.py: only active keys count toward the cap")

    def test_a_name_that_is_too_long_is_a_field_error(self):
        response = self.create("x" * 256)
        html = response.content.decode()
        self.refused(response, 400)
        field = next(attrs for attrs in tags(html, "input") if attrs.get("id") == "id_name")
        self.assertEqual(field.get("aria-invalid"), "true", f"{KEYS_PAGE}: the name field is marked invalid")
        self.assertIn("id_name_error", field.get("aria-describedby", ""), f"{KEYS_PAGE}: and tied to its error")
        self.assertIn('<div class="field__error" id="id_name_error">', html, f"{KEYS_PAGE}: the error is the field component's")
        self.assertIn("<p>Use 255 characters or fewer.</p>", html, f"{FORMS}: say the limit")
        self.assertIn("autofocus", field, f"{KEYS_PAGE}: focus goes to the field in error (focus_field)")
        self.assertEqual(main_of(html).count("autofocus"), 1, f"{KEYS_PAGE}: one element takes focus")
        self.assertNotIn('role="alert"', main_of(html), f"{KEYS_PAGE}: an error of the field is not also a summary")
        self.assertIn('value="' + "x" * 256 + '"', html, f"{KEYS_PAGE}: the name typed is kept")

    def test_a_name_with_a_line_break_is_a_field_error(self):
        response = self.create("two\nlines")
        self.refused(response, 400)
        self.assertIn("<p>Use a single line of plain text.</p>", response.content.decode(), f"{FORMS}: say what to do")

    def test_a_refusal_has_a_form_that_works(self):
        EmailVerification.objects.filter(user=self.user).update(is_verified=False)
        html = self.create("first").content.decode()
        EmailVerification.objects.filter(user=self.user).update(is_verified=True)
        response = self.client.post(KEYS_URL, {"name": "second", "token": hidden_token(html)})
        self.assertEqual(response.status_code, 200, f"{KEYS_VIEW}: the page after a refusal carries a new token, so the person can send it again")

    def test_only_a_key_made_answers_200(self):
        self.workspace.plan = "free"
        self.workspace.save()
        statuses = [self.create("a").status_code, self.create("b").status_code, self.create("c" * 256).status_code]
        self.assertEqual(statuses, [200, 402, 400], f"{KEYS_VIEW}: 200 is the key being made; a refusal never answers 200")

    def test_an_answer_this_page_does_not_know_is_shown_with_its_own_message(self):
        unknown = account.Answer(429, {"error": "Slow down.", "code": "rate_limited"})
        with mock.patch("dashboard.keys.account.create_key", return_value=unknown):
            response = self.create("x")
        self.refused(response, 429)
        self.assertIn("Slow down.", response.content.decode(), f"{KEYS_VIEW}: _refusal falls back to the endpoint's message and status: never a 500, never a blank page")


class RevokeKeyTests(SignedInCase):
    def url(self, key):
        return f"{KEYS_URL}{key.id}/revoke/"

    def test_the_confirmation_page_asks_and_changes_nothing(self):
        key, raw = self.make_key("deploy")
        response = self.client.get(self.url(key))
        html = response.content.decode()
        main = main_of(html)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(re.findall(r"<h1[^>]*>(.*?)</h1>", main), ["Do you want to revoke this API key?"], f"{REVOKE_PAGE}: the React dashboard's question is the heading")
        self.assertIn("<p>Agents using it will fail.</p>", main, f"{REVOKE_PAGE}: and its warning")
        self.assertIn("<dd>deploy</dd>", main, f"{REVOKE_PAGE}: name the key")
        self.assertIn(f"<dd><code>{key.key_prefix}</code></dd>", main, f"{REVOKE_PAGE}: and its prefix")
        self.assertIn("<title>Revoke key | Veilfile</title>", html, f"{REVOKE_PAGE}: title the page Revoke key | Veilfile")
        self.assertEqual(response.get("X-Robots-Tag"), NOINDEX, f"{KEYS_VIEW}: dashboard_view adds the noindex header")
        self.assertIn('<meta name="robots" content="noindex, nofollow">', html, f"{REVOKE_PAGE}: the robots meta")
        self.assertEqual(len(re.findall('name="csrfmiddlewaretoken"', main)), 1, f"{REVOKE_PAGE}: the form holds the CSRF token")
        self.assertIn('<button class="action interactive" type="submit">Revoke key</button>', main, f"{REVOKE_PAGE}: the main action")
        self.assertIn('<a class="label interactive" href="/app/keys/">Keep this key</a>', main, f"{REVOKE_PAGE}: the quiet way out")
        key.refresh_from_db()
        self.assertTrue(key.is_active, f"{KEYS_VIEW}: a GET never revokes")
        self.assertEqual(APIKey.verify(raw), key, f"{KEYS_VIEW}: the key still works after a GET")

    def test_it_is_a_form_page_like_the_others(self):
        key, _ = self.make_key("deploy")
        form_pages.FormPagesAreOnTheShellTests.check(self, self.url(key))

    def test_the_post_revokes_and_goes_back_with_one_line(self):
        key, raw = self.make_key("deploy")
        response = self.client.post(self.url(key))
        self.assertEqual((response.status_code, response.get("Location")), (302, KEYS_URL), f"{KEYS_VIEW}: a revoke redirects to Keys")
        key.refresh_from_db()
        self.assertFalse(key.is_active, f"{KEYS_VIEW}: the POST revokes the key")
        self.assertIsNone(APIKey.verify(raw), "the revoked key authenticates no more")
        html = self.client.get(KEYS_URL).content.decode()
        self.assertEqual(Notices(html).found, ["The key is revoked."], "backend/templates/layouts/app.html: a message is a .notice with role=status at the top of <main>")
        row = TableRows(html).rows[0]
        self.assertEqual((row["State"], row["links"]), ("Revoked", []), f"{KEYS_PAGE}: the key stays listed, as Revoked")

    def test_the_line_is_shown_once(self):
        key, _ = self.make_key("deploy")
        self.client.post(self.url(key))
        self.client.get(KEYS_URL)
        self.assertNotIn("The key is revoked.", self.client.get(KEYS_URL).content.decode(), f"{KEYS_VIEW}: a message is read once; a reload does not repeat it")

    def test_another_workspaces_key_is_a_404_and_stays_active(self):
        theirs, raw = APIKey.create_key(get_or_create_workspace(make_user("other", plan="team")), "theirs")
        for method in (self.client.get, self.client.post):
            response = method(self.url(theirs))
            self.assertEqual(response.status_code, 404, f"{KEYS_VIEW}: key_revoke looks the key up in the signed-in person's workspace: another's is the shell's 404")
            self.assertIn("Not found", response.content.decode(), "backend/templates/404.html: the shell's 404 page")
            self.assertEqual(response.get("X-Robots-Tag"), NOINDEX, f"{KEYS_VIEW}: return the 404 (do not raise it) so dashboard_view adds the noindex header")
        theirs.refresh_from_db()
        self.assertTrue(theirs.is_active)
        self.assertEqual(APIKey.verify(raw), theirs)

    def test_a_missing_or_malformed_id_is_a_404_never_a_500(self):
        for path in (f"{KEYS_URL}999999/revoke/", f"{KEYS_URL}{10**30}/revoke/", f"{KEYS_URL}abc/revoke/", f"{KEYS_URL}-1/revoke/", f"{KEYS_URL}1.5/revoke/"):
            for method in (self.client.get, self.client.post):
                with self.subTest(path=path, method=method.__name__):
                    self.assertEqual(method(path).status_code, 404, f"backend/dashboard/urls.py: {path} is the shell's 404")

    def test_a_key_already_revoked_says_so_and_changes_nothing(self):
        key, _ = self.make_key("old", active=False)
        for method in (self.client.get, self.client.post):
            response = method(self.url(key))
            self.assertEqual((response.status_code, response.get("Location")), (302, KEYS_URL), f"{KEYS_VIEW}: key_revoke sends a revoked key back to Keys")
            self.assertEqual(Notices(self.client.get(KEYS_URL).content.decode()).found, ["That key was already revoked."], f"{KEYS_VIEW}: and says so")

    def test_a_post_without_the_csrf_token_revokes_nothing(self):
        key, raw = self.make_key("deploy")
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        with self.assertLogs("django.security.csrf", level="WARNING"):
            response = client.post(self.url(key))
        self.assertEqual(response.status_code, 403)
        self.assertIn("Form not sent", response.content.decode(), "backend/pages/errors.py: the shell's Form not sent page")
        self.assertEqual(APIKey.verify(raw), key, f"{KEYS_VIEW}: no CSRF token, no revoke")

    def test_a_signed_out_visit_goes_to_sign_in_and_revokes_nothing(self):
        key, raw = self.make_key("deploy")
        for method in (Client().get, Client().post):
            response = method(self.url(key))
            self.assertEqual((response.status_code, response.get("Location")), (302, f"{LOGIN}?next={self.url(key)}"), f"{KEYS_VIEW}: dashboard_view sends a signed-out visit to sign in")
        self.assertEqual(APIKey.verify(raw), key)

    def test_other_methods_are_refused_and_change_nothing(self):
        key, raw = self.make_key("deploy")
        for method in (self.client.put, self.client.delete, self.client.patch):
            self.assertEqual(method(self.url(key)).status_code, 405, f"{KEYS_VIEW}: key_revoke takes GET, HEAD and POST only")
        self.assertEqual(APIKey.verify(raw), key)

    def test_a_name_is_escaped_on_the_confirmation_page(self):
        key, _ = self.make_key("<b>bold</b>")
        html = self.client.get(self.url(key)).content.decode()
        self.assertNotIn("<b>bold</b>", html, f"{REVOKE_PAGE}: a key's name is a visitor's words: print it escaped")

    def test_a_255_character_name_is_printed_whole_on_the_confirmation_page(self):
        name = "k" * 255
        key, _ = self.make_key(name)
        response = self.client.get(self.url(key))
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"<dd>{name}</dd>", response.content.decode(), f"{REVOKE_PAGE}: print the name whole; the form column wraps it (base.css .form-page)")

    def test_a_key_the_terminal_login_made_can_be_revoked_like_any_other(self):
        key, raw = self.make_key("Terminal login", device_issued=True)
        self.client.post(self.url(key))
        self.assertIsNone(APIKey.verify(raw), "backend/drop_core/device.py: a device-issued key is a key of the workspace and is revoked here too")


class ConnectedAppsTests(SignedInCase):
    def test_a_signed_out_visit_goes_to_sign_in(self):
        response = Client().get(APPS_URL)
        self.assertEqual((response.status_code, response.get("Location")), (302, f"{LOGIN}?next={APPS_URL}"), f"{KEYS_VIEW}: apps is a dashboard view")

    def test_the_page_is_titled_headed_and_explains_itself(self):
        response = self.client.get(APPS_URL)
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get("X-Robots-Tag"), NOINDEX)
        self.assertIn("<title>Connected apps | Veilfile</title>", html, "backend/pages/context_processors.py: the section's title")
        self.assertEqual(html.count('<meta name="robots" content="noindex, nofollow">'), 1, f"{APPS_PAGE}: the robots meta, once, in the page's meta block")
        self.assertEqual(re.findall(r"<h1[^>]*>(.*?)</h1>", main_of(html)), ["Connected apps"], f"{APPS_PAGE}: one h1, Connected apps")
        self.assertIn("<p>These are the MCP clients you authorized with OAuth, such as Claude Code. They can upload, list, and revoke artifacts, but cannot manage API keys. "
                      "Revoking an API key does not disconnect them.</p>", main_of(html), f"{APPS_PAGE}: keep the React dashboard's sentence")

    def test_no_apps_says_so(self):
        main = main_of(self.client.get(APPS_URL).content.decode())
        self.assertIn("You have no connected apps.", main, f"{APPS_PAGE}: say so when there are none")
        self.assertNotIn("<table", main)

    def test_each_app_is_a_row_with_its_host_and_when_it_was_authorized(self):
        app = make_app("Claude Code")
        connect(self.user, app)
        html = self.client.get(APPS_URL).content.decode()
        self.assertIn("<caption>Connected apps</caption>", html, f"{APPS_PAGE}: the table has a caption")
        self.assertEqual([header.get("scope") for header in tags(html, "th")], ["col"] * 4, f"{APPS_PAGE}: every header cell has scope=col")
        row = TableRows(html).rows[0]
        self.assertEqual((row["App"], row["Returns to"]), ("Claude Code", "127.0.0.1:9876"), f"{APPS_PAGE}: the app's name and the host it returns to")
        self.assertEqual(len(row["times"]), 1, f"{APPS_PAGE}: Last authorized is a <time> (utc_time)")
        self.assertIn("data-local", row["times"][0])
        self.assertEqual(row["links"], [f"{APPS_URL}{app.pk}/disconnect/"], f"{APPS_PAGE}: Disconnect links to the confirmation page")
        self.assertEqual(row["Actions"], "Disconnect Claude Code", f"{APPS_PAGE}: the link names its app")
        self.assertTrue(all(cell.get("data-label") for cell in tags(main_of(html), "td")), f"{APPS_PAGE}: every td has its data-label")

    def test_an_app_that_returns_nowhere_says_so(self):
        connect(self.user, make_app("Bare", redirect_uris=""))
        self.assertEqual(TableRows(self.client.get(APPS_URL).content.decode()).rows[0]["Returns to"], "Not given", f"{APPS_PAGE}: say Not given for no host")

    def test_only_this_persons_apps_are_listed(self):
        connect(self.user, make_app("Mine"))
        connect(make_user("other"), make_app("Theirs"))
        names = [row["App"] for row in TableRows(self.client.get(APPS_URL).content.decode()).rows]
        self.assertEqual(names, ["Mine"], f"{KEYS_VIEW}: account.list_grants is the signed-in person's grants")

    def test_an_apps_name_is_its_own_words_printed_escaped_and_whole(self):
        name = "<script>alert(1)</script>" + "a" * 230
        connect(self.user, make_app(name))
        html = self.client.get(APPS_URL).content.decode()
        self.assertNotIn("<script>alert(1)", html, f"{APPS_PAGE}: any client can register under any name: print it escaped")
        self.assertEqual(TableRows(html).rows[0]["App"], name, f"{APPS_PAGE}: a 255-character name is printed whole")

    def test_a_dead_grant_is_not_listed(self):
        app = make_app("Gone")
        _, refresh = connect(self.user, app)
        refresh.revoke()
        self.assertNotIn("<table", main_of(self.client.get(APPS_URL).content.decode()), "backend/drop_core/oauth.py: a revoked app is not a connected one")

    def test_other_methods_are_refused(self):
        self.assertEqual(self.client.post(APPS_URL).status_code, 405, f"{KEYS_VIEW}: apps takes GET and HEAD only")


class DisconnectAppTests(SignedInCase):
    def setUp(self):
        super().setUp()
        self.app = make_app("Claude Code")
        self.access, self.refresh = connect(self.user, self.app)
        self.url = f"{APPS_URL}{self.app.pk}/disconnect/"

    def test_the_confirmation_page_asks_and_changes_nothing(self):
        response = self.client.get(self.url)
        html = response.content.decode()
        main = main_of(html)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(re.findall(r"<h1[^>]*>(.*?)</h1>", main), ["Do you want to disconnect Claude Code?"], f"{DISCONNECT_PAGE}: the React dashboard's question is the heading")
        self.assertIn("<p>It loses access now and must ask you again to reconnect.</p>", main, f"{DISCONNECT_PAGE}: and its sentence")
        self.assertIn("<p>It returns to <strong>127.0.0.1:9876</strong>.</p>", main, f"{DISCONNECT_PAGE}: say where the app returns to: the name is the app's own words")
        self.assertIn("<title>Disconnect app | Veilfile</title>", html, f"{DISCONNECT_PAGE}: title the page Disconnect app | Veilfile")
        self.assertEqual(response.get("X-Robots-Tag"), NOINDEX)
        self.assertIn('<meta name="robots" content="noindex, nofollow">', html)
        self.assertEqual(len(re.findall('name="csrfmiddlewaretoken"', main)), 1, f"{DISCONNECT_PAGE}: the form holds the CSRF token")
        self.assertIn('<button class="action interactive" type="submit">Disconnect</button>', main, f"{DISCONNECT_PAGE}: the main action")
        self.assertIn('<a class="label interactive" href="/app/apps/">Keep connected</a>', main, f"{DISCONNECT_PAGE}: the quiet way out")
        self.refresh.refresh_from_db()
        self.assertIsNone(self.refresh.revoked, f"{KEYS_VIEW}: a GET never disconnects")
        self.assertTrue(AccessToken.objects.filter(pk=self.access.pk).exists())

    def test_it_is_a_form_page_like_the_others(self):
        form_pages.FormPagesAreOnTheShellTests.check(self, self.url)

    def test_the_post_disconnects_and_goes_back_with_one_line(self):
        other = make_app("Other client")
        other_access, other_refresh = connect(self.user, other)
        response = self.client.post(self.url)
        self.assertEqual((response.status_code, response.get("Location")), (302, APPS_URL), f"{KEYS_VIEW}: a disconnect redirects to Connected apps")
        self.refresh.refresh_from_db()
        self.assertIsNotNone(self.refresh.revoked, "backend/drop_core/oauth.py: the app's refresh token is revoked")
        self.assertFalse(AccessToken.objects.filter(pk=self.access.pk).exists(), "and its access token goes")
        self.assertTrue(Application.objects.filter(pk=self.app.pk).exists(), "the app's client row stays, so it can ask again")
        other_refresh.refresh_from_db()
        self.assertIsNone(other_refresh.revoked, "another app of the same person is untouched")
        html = self.client.get(APPS_URL).content.decode()
        self.assertEqual(Notices(html).found, ["Claude Code is disconnected."], f"{KEYS_VIEW}: the line is '<App> is disconnected.', shown as a notice")
        self.assertEqual([row["App"] for row in TableRows(html).rows], ["Other client"], f"{APPS_PAGE}: the app is gone from the list")

    def test_a_255_character_name_is_printed_whole_in_the_question(self):
        name = "a" * 255
        app = make_app(name)
        connect(self.user, app)
        response = self.client.get(f"{APPS_URL}{app.pk}/disconnect/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(re.findall(r"<h1[^>]*>(.*?)</h1>", main_of(response.content.decode())), [f"Do you want to disconnect {name}?"],
                         f"{DISCONNECT_PAGE}: print the name whole; the form column wraps it (base.css .form-page)")

    def test_the_apps_name_in_the_line_is_escaped(self):
        app = make_app("<b>bold</b>")
        connect(self.user, app)
        self.client.post(f"{APPS_URL}{app.pk}/disconnect/")
        html = self.client.get(APPS_URL).content.decode()
        self.assertIn("&lt;b&gt;bold&lt;/b&gt; is disconnected.", html, "backend/templates/layouts/app.html: print a message escaped: an app's name is its own words")
        self.assertNotIn("<b>bold</b>", html)

    def test_a_long_name_still_gets_its_line(self):
        """A message in a cookie is limited to 2,048 bytes; Django falls back to the session, so a 255-character name is not lost."""
        name = "\u00e9" * 255
        app = make_app(name)
        connect(self.user, app)
        self.client.post(f"{APPS_URL}{app.pk}/disconnect/")
        self.assertEqual(Notices(self.client.get(APPS_URL).content.decode()).found, [f"{name} is disconnected."],
                         f"{KEYS_VIEW}: the line must reach the page whatever the app's name")

    def test_the_line_is_shown_once(self):
        self.client.post(self.url)
        self.client.get(APPS_URL)
        self.assertNotIn("disconnected.", self.client.get(APPS_URL).content.decode(), f"{KEYS_VIEW}: a message is read once")

    def test_another_persons_app_is_a_404_and_stays_connected(self):
        other = make_user("other")
        theirs = make_app("Theirs")
        access, refresh = connect(other, theirs)
        for method in (self.client.get, self.client.post):
            response = method(f"{APPS_URL}{theirs.pk}/disconnect/")
            self.assertEqual(response.status_code, 404, f"{KEYS_VIEW}: app_disconnect acts on the signed-in person's own grants: another's is the shell's 404")
            self.assertEqual(response.get("X-Robots-Tag"), NOINDEX, f"{KEYS_VIEW}: return the 404 so dashboard_view adds the noindex header")
        refresh.refresh_from_db()
        self.assertIsNone(refresh.revoked)
        self.assertTrue(AccessToken.objects.filter(pk=access.pk).exists())

    def test_an_app_that_is_not_connected_is_a_404(self):
        unused = make_app("Never connected")
        for method in (self.client.get, self.client.post):
            self.assertEqual(method(f"{APPS_URL}{unused.pk}/disconnect/").status_code, 404, f"{KEYS_VIEW}: an app with no grant of this person is the shell's 404")

    def test_a_missing_or_malformed_id_is_a_404_never_a_500(self):
        for path in (f"{APPS_URL}999999/disconnect/", f"{APPS_URL}{10**30}/disconnect/", f"{APPS_URL}abc/disconnect/", f"{APPS_URL}-1/disconnect/"):
            for method in (self.client.get, self.client.post):
                with self.subTest(path=path, method=method.__name__):
                    self.assertEqual(method(path).status_code, 404, f"backend/dashboard/urls.py: {path} is the shell's 404")

    def test_a_second_disconnect_is_a_404_and_does_no_harm(self):
        self.client.post(self.url)
        self.assertEqual(self.client.post(self.url).status_code, 404, f"{KEYS_VIEW}: once disconnected, the app is not one of this person's")

    def test_a_post_without_the_csrf_token_disconnects_nothing(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        with self.assertLogs("django.security.csrf", level="WARNING"):
            response = client.post(self.url)
        self.assertEqual(response.status_code, 403)
        self.assertIn("Form not sent", response.content.decode())
        self.refresh.refresh_from_db()
        self.assertIsNone(self.refresh.revoked, f"{KEYS_VIEW}: no CSRF token, no disconnect")

    def test_a_signed_out_visit_goes_to_sign_in_and_disconnects_nothing(self):
        for method in (Client().get, Client().post):
            response = method(self.url)
            self.assertEqual((response.status_code, response.get("Location")), (302, f"{LOGIN}?next={self.url}"))
        self.refresh.refresh_from_db()
        self.assertIsNone(self.refresh.revoked)

    def test_other_methods_are_refused_and_change_nothing(self):
        for method in (self.client.put, self.client.delete):
            self.assertEqual(method(self.url).status_code, 405, f"{KEYS_VIEW}: app_disconnect takes GET, HEAD and POST only")
        self.refresh.refresh_from_db()
        self.assertIsNone(self.refresh.revoked)
