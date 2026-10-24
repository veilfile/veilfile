"""The Files section of the dashboard (spec 4.8): the list and its pager, the Copy and Revoke each live row offers, and the
confirmation page that revokes a file.

Every class sets longMessage = False, so that a failure prints its own message, which names the file and the fix, and not the
page it searched.
"""
import io
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone as dt_timezone
from html import unescape
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from dashboard import views as dashboard_views
from dashboard.file_rows import FLAG_WORDS, file_rows, flag_words, held_ids, state_of
from drop_core import account, secrets
from drop_core.models import Artifact, Workspace, get_or_create_workspace
from drop_core.storage import LocalStorage
from pages import catalog, tests_forms_on_the_shell
from pages.tests_chapters import BANNED
from pages.tests_reading import declarations

User = get_user_model()
PASSWORD = "pw12345678"
BACKEND = Path(settings.BASE_DIR)
TEMPLATES = BACKEND / "templates"
STATIC = BACKEND / "static"
OWN_TEMPLATES = ("components/file_list.html", "dashboard/files.html", "dashboard/file_revoke.html")
LABELS = ["File", "Size", "Added", "Expires", "Secret flags", "State", "Actions"]
LIST = "/app/files/"
ROW = re.compile(r'<tr class="files__row" data-state="([^"]+)">(.*?)</tr>', re.S)
CELL = re.compile(r'<td[^>]*?data-label="([^"]+)"[^>]*>(.*?)</td>', re.S)
HOSTILE = '"><img src=x onerror=alert(1)><script>alert(2)</script>'


def text_of(fragment):
    """The words a reader sees in a piece of markup."""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def rows_in(html):
    """Each file row of a page or a component, in order: its state, its cells' words by label, their markup by label, its markup."""
    found = []
    for state, markup in ROW.findall(html):
        cells = CELL.findall(markup)
        found.append({"state": state, "markup": markup, "html": dict(cells),
                      "cells": {label: text_of(cell) for label, cell in cells}})
    return found


def notices(html):
    """The words of each element with class notice and role status on a page, whatever its tag."""
    found = []
    for opening in re.finditer(r"<(\w+)\b([^>]*)>", html):
        tag, attrs = opening.groups()
        if re.search(r'class="[^"]*\bnotice\b', attrs) and 'role="status"' in attrs:
            found.append(text_of(html[opening.end():html.index(f"</{tag}>", opening.end())]))
    return found


def links_in(html):
    """[(attributes text, words)] of each <a> in a piece of markup."""
    return [(attrs, text_of(body)) for attrs, body in re.findall(r"<a\b([^>]*)>(.*?)</a>", html, re.S)]


def href_of(html, words):
    """The href of the first link whose words are `words`, or None."""
    for attrs, text in links_in(html):
        if text == words:
            return re.search(r'href="([^"]*)"', attrs).group(1)
    return None


def main_of(html):
    return html[html.index("<main"):html.index("</main>")]


def without_tokens(html):
    """A page with its CSRF token values blanked: Django masks a token differently on every render."""
    return re.sub(r'(name="csrfmiddlewaretoken" value=")[^"]*', r"\1", html)


class FailingDeleteStorage(LocalStorage):
    """A storage whose delete fails, as R2 does in an outage."""

    def delete(self, token):
        raise ConnectionError(f"R2 503 for https://r2.example/bucket/{token}")


class FilesCase(TestCase):
    """A signed-in owner with a workspace, and the means to give it files that have bytes in storage."""
    longMessage = False

    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user("owner", email="owner@example.com", password=PASSWORD)
        self.workspace = get_or_create_workspace(self.owner)
        self.client.force_login(self.owner)

    def make_file(self, name="report.pdf", *, workspace=None, size=1500, days=7, flags=(), held=False, created=None, data=b"bytes"):
        """A file of `workspace` (the owner's by default) with its bytes in storage; `days` before it expires, negative once it has."""
        artifact = Artifact.objects.create(
            workspace=workspace or self.workspace, filename=name, size_bytes=size, expires_at=timezone.now() + timedelta(days=days),
            secret_flags=list(flags), safety_status="held" if held else "clear")
        LocalStorage().save(artifact.token, io.BytesIO(data), "application/octet-stream")
        self.addCleanup(LocalStorage().delete, artifact.token)
        if created is not None:
            Artifact.objects.filter(pk=artifact.pk).update(created_at=created)
            artifact.refresh_from_db()
        return artifact

    def stored(self, artifact):
        return LocalStorage().exists(artifact.token)

    @contextmanager
    def storage_down(self):
        """Every storage delete fails, as in an outage; the error the revoke logs for it is expected."""
        with mock.patch("drop_core.services.get_storage", return_value=FailingDeleteStorage()), \
                self.assertLogs("drop_core.services", "ERROR"):
            yield

    def link_status(self, artifact):
        """What the public link of a file answers a stranger."""
        response = Client().get(f"/a/{artifact.token}")
        response.close()
        return response.status_code

    def other_user(self, name="other"):
        user = User.objects.create_user(name, email=f"{name}@example.com", password=PASSWORD)
        return user, get_or_create_workspace(user)

    def revoke_url(self, artifact):
        return reverse("app-file-revoke", args=[artifact.id])


class FileRowsTests(FilesCase):
    """dashboard/file_rows.py: the list endpoint's rows with a state and flags in words."""

    def rows(self):
        body = account.list_artifacts(self.owner, limit=50).body
        return {row["name"]: row for row in file_rows(self.owner, body)}

    def test_every_scanner_flag_has_words(self):
        names = {name for name, _pattern, _check in secrets._PATTERNS}
        self.assertEqual(set(FLAG_WORDS), names,
                         "backend/dashboard/file_rows.py: FLAG_WORDS needs the words for each flag backend/drop_core/secrets.py can report, and for no other")

    def test_flags_are_said_in_words_once_each_in_the_order_reported(self):
        self.assertEqual(flag_words(["github_pat", "github_token", "stripe_key"]), ["GitHub token", "Stripe key"],
                         "backend/dashboard/file_rows.py: two flags that read the same are said once")
        self.assertEqual(flag_words(["aws_access_key", "generic_secret"]), ["AWS access key", "Password or secret"])
        self.assertEqual(flag_words([]), [])
        self.assertEqual(flag_words(None), [])

    def test_a_flag_the_table_lacks_is_still_readable(self):
        self.assertEqual(flag_words(["brand_new_thing"]), ["Brand new thing"],
                         "backend/dashboard/file_rows.py: an unknown flag reads as its name without underscores, never as a raw identifier")

    def test_a_row_is_live_expired_or_held(self):
        self.make_file("live.txt")
        self.make_file("old.txt", days=-1)
        self.make_file("held.txt", held=True)
        self.make_file("held-and-old.txt", held=True, days=-1)
        states = {name: row["state"] for name, row in self.rows().items()}
        self.assertEqual(states, {"live.txt": "live", "old.txt": "expired", "held.txt": "held", "held-and-old.txt": "expired"},
                         "backend/dashboard/file_rows.py: expired once expires_at has passed (held or not), else held, else live")

    def test_the_state_rule(self):
        now = datetime(2026, 10, 22, 12, 0, tzinfo=dt_timezone.utc)
        later, earlier = now + timedelta(seconds=1), now - timedelta(seconds=1)
        self.assertEqual([state_of(later, False, now), state_of(later, True, now), state_of(earlier, False, now), state_of(earlier, True, now),
                          state_of(now, False, now)],
                         ["live", "held", "expired", "expired", "expired"],
                         "backend/dashboard/file_rows.py: state_of: expired once expires_at is not after now (held or not), else held, else live")

    def test_only_a_live_row_carries_its_link(self):
        self.make_file("live.txt")
        self.make_file("old.txt", days=-1)
        self.make_file("held.txt", held=True)
        listed = {item["filename"]: item["url"] for item in account.list_artifacts(self.owner, limit=50).body}
        for name, row in self.rows().items():
            with self.subTest(file=name):
                self.assertEqual(row["url"], listed[name] if row["state"] == "live" else "",
                                 "backend/dashboard/file_rows.py: the url of a row that is not live stays out of the page; its link opens nothing")

    def test_a_row_keeps_what_the_endpoint_said(self):
        artifact = self.make_file("kept.txt", size=2048, flags=["private_key"], days=3)
        row = self.rows()["kept.txt"]
        self.assertEqual((row["id"], row["size"], row["flags"]), (str(artifact.id), 2048, ["Private key"]))
        self.assertEqual((row["added"], row["expires"]), (artifact.created_at, artifact.expires_at))
        self.assertIsNotNone(row["added"].tzinfo, "backend/dashboard/file_rows.py: times are aware, so utc_time can write them in UTC")

    def test_held_is_looked_for_among_the_owners_own_files_only(self):
        _other, other_workspace = self.other_user()
        theirs = self.make_file("theirs.txt", workspace=other_workspace, held=True)
        mine = self.make_file("mine.txt", held=True)
        self.assertEqual(held_ids(self.owner, [{"id": str(theirs.id)}, {"id": str(mine.id)}]), {str(mine.id)},
                         "backend/dashboard/file_rows.py: held_ids asks within the signed-in user's own workspace")
        self.assertEqual(held_ids(self.owner, []), set())


def sample(number, name="report.pdf", *, state="live", size=3400, flags=(), days=7):
    """A row as file_rows makes it, for the component."""
    added = datetime(2026, 10, 22, 13, 58, tzinfo=dt_timezone.utc)
    return {"id": f"00000000-0000-4000-8000-{number:012d}", "name": name, "size": size, "added": added,
            "expires": added + timedelta(days=days), "flags": list(flags), "state": state,
            "url": f"https://veilfile.com/a/sample-{number}" if state == "live" else ""}


def render_list(files, **inputs):
    context = {"files": files, "id_prefix": "t", "older_url": "", "newest_url": "", **inputs}
    return render_to_string("components/file_list.html", context)


class FileListComponentTests(SimpleTestCase):
    """components/file_list.html: what it draws for the rows it is given."""
    longMessage = False
    FILE = "backend/templates/components/file_list.html"

    def five(self):
        return [sample(1, "release-notes.md"), sample(2, "deploy.env", size=912, flags=["Stripe key", "Password or secret"]),
                sample(3, "screenshots.zip", state="expired", size=48_300_000, days=-1),
                sample(4, "export.csv", state="held", size=2_150_000_000), sample(5, "x" * 508 + ".txt")]

    def test_a_table_of_stacked_rows_with_a_caption_and_scoped_column_headers(self):
        html = render_list(self.five())
        self.assertEqual(html.count("<table"), 1)
        self.assertIn('<table class="rows files">', html, f"{self.FILE}: the list is the dashboard's stacked-rows table, <table class=\"rows\">")
        self.assertRegex(html, r"<caption>[^<]+</caption>", f"{self.FILE}: a table has a caption (spec 9)")
        headers = re.findall(r'<th scope="col">(.*?)</th>', html, re.S)
        self.assertEqual([text_of(header) for header in headers], LABELS,
                         f"{self.FILE}: seven column headers with scope=\"col\"; the last, for the controls, is visually hidden")
        self.assertEqual(len(re.findall(r"<th\b", html)), 7)
        self.assertIn('<th scope="col"><span class="visually-hidden">Actions</span></th>', html)

    def test_every_cell_keeps_its_label_in_every_row(self):
        rows = rows_in(render_list(self.five()))
        self.assertEqual(len(rows), 5)
        for row in rows:
            with self.subTest(file=row["cells"].get("File", "")[:12]):
                self.assertEqual(list(row["cells"]), LABELS,
                                 f"{self.FILE}: each <td> carries data-label, so a phone's stacked row keeps the name of every value (spec 4.10)")

    def test_a_name_is_escaped_in_its_cell_and_in_the_names_of_its_controls(self):
        html = render_list([sample(1, HOSTILE)])
        self.assertNotIn("<img", html, f"{self.FILE}: {{{{ file.name }}}} is escaped wherever it is written")
        self.assertNotIn("<script", html)
        self.assertEqual(rows_in(html)[0]["cells"]["File"], HOSTILE)
        for control in re.findall(r'aria-label="([^"]*)"', html):
            self.assertIn("&quot;", control, f"{self.FILE}: the file's name is escaped in aria-label too")

    def test_the_whole_of_a_long_name_is_in_the_cell(self):
        name = "x" * 508 + ".txt"
        self.assertEqual(len(name), 512)
        self.assertEqual(rows_in(render_list([sample(1, name)]))[0]["cells"]["File"], name)

    def test_times_are_written_for_the_visitors_zone(self):
        row = rows_in(render_list([sample(1)]))[0]
        self.assertIn('<time datetime="2026-10-22T13:58Z" data-local>', row["html"]["Added"],
                      f"{self.FILE}: Added goes through |utc_time, which writes <time datetime data-local> in UTC for behaviors/local-time.js")
        self.assertIn('<time datetime="2026-10-29T13:58Z" data-local>', row["html"]["Expires"])
        self.assertIn("UTC", row["cells"]["Added"], "with no script the reader still sees the time, and the zone it is in")

    def test_sizes_go_through_the_bytes_filter(self):
        sizes = [row["cells"]["Size"] for row in rows_in(render_list(self.five()))]
        self.assertEqual(sizes, ["3.3 KB", "912 B", "46.1 MB", "2.0 GB", "3.3 KB"], f"{self.FILE}: Size is {{{{ file.size|bytes }}}}")

    def test_secret_flags_are_words_or_none(self):
        flags = [row["cells"]["Secret flags"] for row in rows_in(render_list(self.five()))]
        self.assertEqual(flags, ["None", "Stripe key, Password or secret", "None", "None", "None"])

    def test_the_state_is_a_word_in_its_cell_and_on_its_row(self):
        rows = rows_in(render_list(self.five()))
        self.assertEqual([(row["state"], row["cells"]["State"]) for row in rows],
                         [("live", "Live"), ("live", "Live"), ("expired", "Expired"), ("held", "Held"), ("live", "Live")],
                         f"{self.FILE}: data-state on the row is the state's key (the stylesheet and the browser checks read it); the cell says it in a word")

    def test_only_a_live_row_has_copy_and_revoke(self):
        rows = rows_in(render_list(self.five()))
        for row in rows:
            with self.subTest(state=row["state"], file=row["cells"]["File"][:12]):
                actions = row["html"]["Actions"]
                if row["state"] == "live":
                    self.assertEqual(len(re.findall(r"<button\b", actions)), 1)
                    self.assertEqual([text for _attrs, text in links_in(actions)], ["Revoke"])
                else:
                    self.assertEqual(row["cells"]["Actions"], "None")
                    self.assertNotIn("<button", actions)
                    self.assertNotIn("<a ", actions, f"{self.FILE}: a file whose link has stopped working offers neither Copy nor Revoke")

    def test_copy_reads_the_link_in_its_own_row(self):
        for row in rows_in(render_list(self.five())):
            if row["state"] != "live":
                continue
            actions = row["html"]["Actions"]
            target = re.search(r'<button class="files__copy label interactive" type="button" data-copy="([^"]+)"', actions).group(1)
            holder = re.search(rf'<code class="visually-hidden" id="{re.escape(target)}" aria-hidden="true">([^<]*)</code>', actions)
            with self.subTest(target=target):
                self.assertIsNotNone(holder, f"{self.FILE}: the button's data-copy names a <code> with that id in the same cell, which copy.js reads")
                self.assertRegex(holder.group(1), r"^https://veilfile\.com/a/sample-\d$")
        html = render_list(self.five())
        ids = re.findall(r'\sid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)), f"{self.FILE}: every id on the list is unique")
        self.assertTrue(all(found.startswith("t-url-") for found in ids), f"{self.FILE}: ids are made from the id_prefix input")

    def test_copy_is_named_by_its_file_and_keeps_the_visible_word(self):
        html = render_list([sample(1, "plans.pdf")])
        self.assertIn('aria-label="Copy link for plans.pdf">Copy</button>', html,
                      f"{self.FILE}: Copy's name says whose link it copies and starts with the visible word (WCAG 2.5.3)")
        self.assertIn('aria-label="Revoke plans.pdf">Revoke</a>', html)

    def test_the_link_is_hidden_from_a_screen_reader_and_never_a_link_to_open(self):
        html = render_list(self.five())
        self.assertEqual(html.count("https://veilfile.com/a/"), 3, "only live rows write their link, once each")
        self.assertNotIn('href="https://veilfile.com/a/', html, f"{self.FILE}: a file's link is copied, never offered as a link to open")

    def test_copy_speaks_in_the_one_status_of_the_page(self):
        html = render_list(self.five())
        self.assertEqual(html.count("data-copy-status"), 1,
                         f"{self.FILE}: copy.js falls back to the first [data-copy-status] in the document, so the list has one status for all its rows")
        self.assertIn('<p class="visually-hidden" role="status" data-copy-status></p>', html)
        for scope in ("data-tabs", "data-code-block"):
            self.assertNotIn(scope, html, f"{self.FILE}: copy.js looks for the nearest [data-tabs] or [data-code-block] first; a row inside one would speak there")

    def test_copy_js_falls_back_to_the_document_for_its_status(self):
        script = (STATIC / "js/behaviors/copy.js").read_text(encoding="utf-8")
        self.assertIn('button.closest("[data-tabs], [data-code-block]") || document', script,
                      "backend/static/js/behaviors/copy.js: the Files list has one status and no block around its rows, so Copy must still look in the document; "
                      "change the list's markup together with this line")

    def test_revoke_goes_to_the_confirmation_page_of_its_file(self):
        html = render_list([sample(7, "a.txt")])
        self.assertEqual(href_of(html, "Revoke"), "/app/files/00000000-0000-4000-8000-000000000007/revoke/",
                         f"{self.FILE}: Revoke is a link to {{% url 'app-file-revoke' file.id %}}; a GET never revokes")
        self.assertNotIn("<form", html, f"{self.FILE}: the list holds no form; revoking is a POST from the confirmation page")

    def test_the_pager_offers_only_the_ways_that_exist(self):
        files = [sample(1)]
        for older, newest, expected in (("", "", []), ("/app/files/?cursor=1-2", "", ["Older files"]), ("", "/app/files/", ["Newest"]),
                                        ("/app/files/?cursor=1-2", "/app/files/", ["Newest", "Older files"])):
            with self.subTest(older=older, newest=newest):
                html = render_list(files, older_url=older, newest_url=newest)
                found = re.findall(r'<nav class="files__pager" aria-label="More files">(.*?)</nav>', html, re.S)
                self.assertEqual([text for _attrs, text in links_in(found[0])] if found else [], expected)
                if older:
                    self.assertEqual(href_of(html, "Older files"), older)
                    self.assertIn('rel="next"', html)
                if newest:
                    self.assertEqual(href_of(html, "Newest"), newest)

    def test_no_files_says_so_and_points_to_keys(self):
        html = render_list([])
        self.assertNotIn("<table", html, f"{self.FILE}: an empty list is words, not a table with no rows")
        self.assertIn("No files yet. Create a key under Keys, point your agent at its variable name, and uploads will appear here.", html)
        self.assertEqual(href_of(html, "Go to Keys"), "/app/keys/")

    def test_a_page_past_the_oldest_file_says_so_and_offers_the_way_back(self):
        html = render_list([], newest_url="/app/files/")
        self.assertIn("There are no older files.", html)
        self.assertNotIn("No files yet", html)
        self.assertEqual(href_of(html, "Newest"), "/app/files/")

    def test_a_rows_controls_are_buttons_and_links_that_answer_the_cursor(self):
        html = render_list([sample(1)], older_url="/app/files/?cursor=1-2", newest_url="/app/files/")
        for tag in re.findall(r"<(?:button|a)\b[^>]*>", html):
            self.assertIn("interactive", tag, f"{self.FILE}: {tag} needs the class interactive, which gives a control the ink wash on hover and a press")


class FilesPageTests(FilesCase):
    """GET /app/files/."""

    def test_the_addresses_are_the_ones_the_menu_and_the_links_use(self):
        self.assertEqual(reverse("app-files"), LIST)
        self.assertEqual(reverse("app-file-revoke", args=["00000000-0000-4000-8000-000000000001"]),
                         "/app/files/00000000-0000-4000-8000-000000000001/revoke/",
                         "backend/dashboard/urls.py: app-files is /app/files/ and app-file-revoke is /app/files/<uuid:artifact_id>/revoke/")

    def test_signed_out_goes_to_sign_in_and_back(self):
        response = Client().get(LIST)
        self.assertEqual((response.status_code, response["Location"]), (302, "/accounts/login/?next=/app/files/"),
                         "backend/dashboard/views.py: files is wrapped in @dashboard_view, which needs a signed-in user")

    def test_the_page_is_files_on_the_app_layout(self):
        response = self.client.get(LIST)
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("<title>Files | Veilfile</title>", html, "backend/pages/context_processors.py: the entry for app-files has the title Files | Veilfile")
        self.assertEqual(re.findall(r"<h1[^>]*>(.*?)</h1>", main_of(html), re.S), ["Files"], "backend/templates/dashboard/files.html: one h1, Files, inside <main>")
        self.assertRegex(html, r'<body class="[^"]*\blayout-app\b', "backend/templates/dashboard/files.html extends layouts/app.html")
        self.assertIn('data-layout="app"', html, "the five sections are one layout family (backend/pages/context_processors.py), so moving among them is in place")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn('<meta name="robots" content="noindex, nofollow">', html, "backend/templates/layouts/app.html: the meta block carries noindex, nofollow")

    def test_the_page_is_never_cached_because_it_holds_bearer_links(self):
        self.make_file()
        cache_control = self.client.get(LIST)["Cache-Control"]
        self.assertIn("no-store", cache_control, "backend/dashboard/views.py: files carries @never_cache; a live file's link is a bearer credential")

    def test_the_list_holds_no_form_because_every_action_is_a_page_of_its_own(self):
        self.make_file()
        html = main_of(self.client.get(LIST).content.decode())
        self.assertNotIn("<form", html, "backend/templates/components/file_list.html: Revoke is a link to its confirmation page; a GET never changes anything")
        self.assertNotIn("csrfmiddlewaretoken", html)

    def test_the_page_shows_what_the_endpoint_lists_in_its_order(self):
        for number in range(4):
            self.make_file(f"f{number}.txt", created=timezone.now() - timedelta(hours=number))
        body = account.list_artifacts(self.owner, limit=50).body
        rows = rows_in(self.client.get(LIST).content.decode())
        self.assertEqual([row["cells"]["File"] for row in rows], [item["filename"] for item in body])
        self.assertEqual(re.findall(r"/app/files/([0-9a-f-]{36})/revoke/", main_of(self.client.get(LIST).content.decode())), [item["id"] for item in body])

    def test_it_lists_the_owners_files_newest_first_with_their_words(self):
        base = timezone.now() - timedelta(hours=3)
        self.make_file("first.txt", size=100, created=base)
        self.make_file("second.txt", size=2048, flags=["stripe_key"], created=base + timedelta(hours=1))
        self.make_file("gone.zip", size=3 * 1024 ** 3, days=-1, created=base + timedelta(hours=2))
        rows = rows_in(self.client.get(LIST).content.decode())
        self.assertEqual([(row["cells"]["File"], row["cells"]["Size"], row["cells"]["Secret flags"], row["state"]) for row in rows],
                         [("gone.zip", "3.0 GB", "None", "expired"), ("second.txt", "2.0 KB", "Stripe key", "live"), ("first.txt", "100 B", "None", "live")])

    def test_a_held_file_is_held_and_has_no_controls(self):
        self.make_file("held.txt", held=True)
        row = rows_in(self.client.get(LIST).content.decode())[0]
        self.assertEqual((row["state"], row["cells"]["State"], row["cells"]["Actions"]), ("held", "Held", "None"),
                         "backend/dashboard/file_rows.py: held_ids marks the files the safety review holds, which the list body does not say")

    def test_only_the_owners_files_are_listed(self):
        _other, other_workspace = self.other_user()
        theirs = self.make_file("theirs-secret-name.txt", workspace=other_workspace)
        self.make_file("mine.txt")
        html = self.client.get(LIST).content.decode()
        self.assertIn("mine.txt", html)
        self.assertNotIn("theirs-secret-name.txt", html, "backend/drop_core/account.py: list_artifacts reads the signed-in user's workspace")
        self.assertNotIn(theirs.token, html)
        self.assertNotIn(str(theirs.id), html)

    def test_a_workspace_with_no_files_says_so(self):
        html = self.client.get(LIST).content.decode()
        self.assertIn("No files yet.", html)
        self.assertEqual(href_of(html, "Go to Keys"), "/app/keys/")
        self.assertEqual((href_of(html, "Older files"), href_of(html, "Newest")), (None, None))

    def test_a_user_who_has_never_had_a_workspace_gets_the_empty_page(self):
        fresh = User.objects.create_user("fresh", password=PASSWORD)
        client = Client()
        client.force_login(fresh)
        response = client.get(LIST)
        self.assertEqual(response.status_code, 200)
        self.assertIn("No files yet.", response.content.decode())

    def test_the_page_names_what_a_flag_and_a_closed_link_mean(self):
        html = main_of(self.client.get(LIST).content.decode())
        self.assertIn("A secret flag means the scan found text shaped like a secret: the link still works until the file expires or you revoke it.", html)
        self.assertIn("The link of an expired or held file no longer opens.", html)

    def test_a_revoked_file_message_shows_once_as_a_notice(self):
        artifact = self.make_file()
        page = self.client.post(self.revoke_url(artifact), follow=True)
        self.assertEqual(notices(page.content.decode()), ["File revoked."],
                         "backend/templates/layouts/app.html shows each message as <p class=\"notice\" role=\"status\"> at the top of <main>")
        self.assertEqual(notices(self.client.get(LIST).content.decode()), [], "a message is shown once")

    def test_other_methods_are_not_allowed(self):
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                self.assertEqual(getattr(self.client, method)(LIST).status_code, 405,
                                 "backend/dashboard/views.py: files answers GET and HEAD only; a state change is a POST to its own address")

    def test_head_is_answered_without_a_body(self):
        response = self.client.head(LIST)
        self.assertEqual((response.status_code, response.content), (200, b""))


class FilesPaginationTests(FilesCase):
    """?cursor=: fifty to a page, newest first, Older files and Newest."""

    def setUp(self):
        super().setUp()
        base = timezone.now() - timedelta(days=1)
        # 53 files, two to a minute, so the boundary of a page falls between two files that share a time.
        self.names = []
        for number in range(53):
            name = f"file-{number:02d}.txt"
            self.make_file(name, created=base + timedelta(minutes=number // 2), data=b"x")
            self.names.append(name)
        # Two files share each minute, and the list breaks the tie by id, newest id first: the order to expect is the database's.
        self.newest_first = list(Artifact.objects.order_by("-created_at", "-id").values_list("filename", flat=True))
        self.assertEqual(sorted(self.newest_first), self.names)

    def names_on(self, path):
        html = self.client.get(path).content.decode()
        return [row["cells"]["File"] for row in rows_in(html)], html

    def test_a_page_holds_fifty_and_offers_older_files(self):
        names, html = self.names_on(LIST)
        self.assertEqual(len(names), 50, "backend/dashboard/views.py: FILES_PER_PAGE is 50")
        older = href_of(html, "Older files")
        self.assertIsNotNone(older, "backend/templates/components/file_list.html: Older files when the endpoint gave an X-Next-Cursor")
        self.assertIsNone(href_of(html, "Newest"), "the first page has no Newest")
        self.assertEqual(urlsplit(older).path, LIST)
        self.assertEqual(list(parse_qs(urlsplit(older).query)), ["cursor"])

    def test_following_older_files_walks_every_file_once_newest_first(self):
        seen, path = [], LIST
        for _step in range(5):
            names, html = self.names_on(path)
            seen += names
            older = href_of(html, "Older files")
            if older is None:
                break
            path = unescape(older)
        self.assertEqual(seen, self.newest_first, "backend/drop_core/account.py: the cursor is a keyset on (created_at, id), so a shared time loses and repeats nothing")

    def test_the_last_page_has_newest_and_no_older_files(self):
        _names, html = self.names_on(LIST)
        names, last = self.names_on(unescape(href_of(html, "Older files")))
        self.assertEqual(names, self.newest_first[50:])
        self.assertEqual(href_of(last, "Newest"), LIST, "backend/templates/components/file_list.html: Newest goes back to the first page")
        self.assertIsNone(href_of(last, "Older files"))

    def test_a_cursor_taken_from_another_users_page_shows_only_the_askers_files(self):
        # The cursor is a position in the list, not a pass to anyone's files.
        _names, html = self.names_on(LIST)
        older = unescape(href_of(html, "Older files"))
        asker, asker_workspace = self.other_user()
        self.make_file("asker.txt", workspace=asker_workspace, created=timezone.now() - timedelta(days=30))
        client = Client()
        client.force_login(asker)
        page = client.get(older).content.decode()
        self.assertEqual([row["cells"]["File"] for row in rows_in(page)], ["asker.txt"],
                         "backend/drop_core/account.py: list_artifacts filters on the signed-in user's workspace before it applies the cursor")
        self.assertNotIn("file-00.txt", page)

    def test_a_cursor_past_the_oldest_file_is_an_empty_page_with_the_way_back(self):
        html = self.client.get(LIST, {"cursor": "1-" + "0" * 32}).content.decode()
        self.assertEqual(rows_in(html), [])
        self.assertIn("There are no older files.", html)
        self.assertEqual(href_of(html, "Newest"), LIST)

    def test_a_cursor_the_list_never_gave_shows_the_newest_files_and_says_so(self):
        for bad in ("abc", "1-zzz", "-", "x" * 5000, "9" * 40 + "-" + "0" * 32, "1" * 4000 + "-" + "0" * 32, "-1-" + "0" * 32, "1-" + "0" * 31, " ", "\x00", "../../etc/passwd", HOSTILE):
            with self.subTest(cursor=bad[:20]):
                response = self.client.get(LIST, {"cursor": bad})
                html = response.content.decode()
                self.assertEqual(response.status_code, 200,
                                 "backend/dashboard/views.py: a cursor the list did not give shows page one with a sentence; it is never a 400 or a 500")
                self.assertEqual([row["cells"]["File"] for row in rows_in(html)], self.newest_first[:50])
                self.assertIn("That link to older files is not valid. Here are your newest files.", notices(html))
                self.assertIsNone(href_of(html, "Newest"), "the page it shows is the first, so it has no Newest")
                self.assertNotIn("<img src=x", html)

    def test_an_empty_cursor_is_the_first_page_without_a_sentence(self):
        html = self.client.get(LIST, {"cursor": ""}).content.decode()
        self.assertEqual(len(rows_in(html)), 50)
        self.assertEqual(notices(html), [])

    def test_the_pager_links_stay_in_the_family(self):
        _names, html = self.names_on(LIST)
        paths = re.search(r'data-layout-paths="([^"]*)"', html).group(1).split()
        older = unescape(href_of(html, "Older files"))
        self.assertIn(urlsplit(older).path, paths,
                      "backend/pages/context_processors.py: /app/files/ is in the app family's layout_paths, so ?cursor= changes in place (shell.js compares the path only)")
        _names, last = self.names_on(older)
        self.assertIn(urlsplit(href_of(last, "Newest")).path, paths)


class FileRevokePageTests(FilesCase):
    """GET /app/files/<id>/revoke/ shows a confirmation and changes nothing."""

    def test_it_names_the_file_and_asks_once(self):
        artifact = self.make_file("quarterly-report.pdf")
        response = self.client.get(self.revoke_url(artifact))
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.S), ["Revoke this file?"])
        self.assertIn("<p><strong>quarterly-report.pdf</strong></p>", html, "backend/templates/dashboard/file_revoke.html names the file")
        self.assertIn("Its link stops working at once and the file is deleted. This cannot be undone.", html)
        self.assertEqual(main_of(html).count("<form"), 1, "backend/templates/dashboard/file_revoke.html: one form in <main>")
        self.assertRegex(html, r'<form method="post">\s*<input type="hidden" name="csrfmiddlewaretoken" value="[^"]+">\s*'
                               r'<button class="action interactive" type="submit">Revoke file</button>\s*</form>',
                         "backend/templates/dashboard/file_revoke.html: one POST form with {% csrf_token %} and one main action, posting to its own address")
        self.assertEqual(href_of(html, "Keep it"), LIST, "a link back to Files that changes nothing")

    def test_it_is_a_form_page_on_the_shell(self):
        artifact = self.make_file()
        tests_forms_on_the_shell.FormPagesAreOnTheShellTests.check(self, self.revoke_url(artifact))

    def test_it_is_inside_the_dashboard_with_files_marked(self):
        artifact = self.make_file()
        html = self.client.get(self.revoke_url(artifact)).content.decode()
        menu = html[html.index('<nav class="menu"'):html.index("</nav>")]
        self.assertRegex(menu, r'<a [^>]*href="/app/files/"[^>]*aria-current="true"',
                         "backend/pages/registry.py: DASHBOARD_SECTIONS maps app-file-revoke to app-files, so the menu inside the dashboard marks Files on this page")
        for word in ("Overview", "Keys", "Connected apps", "Billing"):
            self.assertIn(f">{word}</a>", menu)
        self.assertIn(">Sign out</button>", menu, "a page inside the dashboard has the Sign out button, with the page's token, outside <main>")
        self.assertEqual(main_of(html).count("csrfmiddlewaretoken"), 1, "the page's own form carries one token inside <main>")

    def test_it_is_titled_for_what_it_asks_and_kept_out_of_search(self):
        artifact = self.make_file()
        response = self.client.get(self.revoke_url(artifact))
        html = response.content.decode()
        self.assertIn("<title>Revoke file | Veilfile</title>", html)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow", "backend/dashboard/views.py: file_revoke is wrapped in @dashboard_view")
        self.assertIn('<meta name="robots" content="noindex, nofollow">', html)

    def test_a_hostile_name_is_escaped(self):
        artifact = self.make_file(HOSTILE)
        html = self.client.get(self.revoke_url(artifact)).content.decode()
        self.assertNotIn("<img src=x", html, "backend/templates/dashboard/file_revoke.html: {{ artifact.filename }} is escaped")
        self.assertNotIn("<script>alert(2)", html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)

    def test_the_whole_of_a_512_character_name_is_on_the_page(self):
        name = "x" * 508 + ".txt"
        artifact = self.make_file(name)
        self.assertIn(f"<strong>{name}</strong>", self.client.get(self.revoke_url(artifact)).content.decode())

    def test_a_get_changes_nothing(self):
        artifact = self.make_file()
        before = Artifact.objects.values().get(pk=artifact.pk)
        for query in ("", "?confirm=1", "?revoke=yes", "?_method=POST"):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(self.revoke_url(artifact) + query).status_code, 200)
        self.assertEqual(Artifact.objects.values().get(pk=artifact.pk), before, "a GET only shows the page; it never revokes (AGENTS.md, invariants)")
        self.assertTrue(self.stored(artifact))
        self.assertEqual(self.client.head(self.revoke_url(artifact)).status_code, 200)
        self.assertTrue(Artifact.objects.filter(pk=artifact.pk).exists())

    def test_other_methods_are_not_allowed(self):
        artifact = self.make_file()
        for method in ("put", "patch", "delete"):
            with self.subTest(method=method):
                self.assertEqual(getattr(self.client, method)(self.revoke_url(artifact)).status_code, 405)
        self.assertTrue(Artifact.objects.filter(pk=artifact.pk).exists())

    def test_a_user_who_has_never_had_a_workspace_is_told_there_is_no_such_file(self):
        fresh = User.objects.create_user("fresh", password=PASSWORD)
        client = Client()
        client.force_login(fresh)
        for method in ("get", "post"):
            with self.subTest(method=method):
                response = getattr(client, method)("/app/files/00000000-0000-4000-8000-000000000001/revoke/")
                self.assertEqual(response.status_code, 404, "backend/dashboard/views.py: _own_file answers a user with no files the shell's 404, not an error")


class FileRevokeTests(FilesCase):
    """POST /app/files/<id>/revoke/."""

    def test_it_revokes_the_file_and_goes_back_to_the_list(self):
        artifact = self.make_file("old-draft.txt")
        other = self.make_file("keep-me.txt")
        response = self.client.post(self.revoke_url(artifact))
        self.assertEqual((response.status_code, response["Location"]), (302, LIST),
                         "backend/dashboard/views.py: a revoke goes back to Files with one line, as every dashboard action does")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow", "backend/dashboard/views.py: file_revoke is wrapped in @dashboard_view, so its redirect carries the header too")
        self.assertFalse(Artifact.objects.filter(pk=artifact.pk).exists())
        self.assertFalse(self.stored(artifact), "revoking deletes the bytes from storage as well as the row")
        self.assertTrue(Artifact.objects.filter(pk=other.pk).exists() and self.stored(other), "only the file named is revoked")

    def test_its_link_stops_working_at_once(self):
        artifact = self.make_file()
        self.assertEqual(self.link_status(artifact), 200)
        self.client.post(self.revoke_url(artifact))
        self.assertEqual(self.link_status(artifact), 404)

    def test_the_list_then_says_file_revoked_and_no_longer_has_the_file(self):
        artifact = self.make_file("old-draft.txt")
        self.make_file("keep-me.txt")
        page = self.client.post(self.revoke_url(artifact), follow=True)
        html = page.content.decode()
        self.assertEqual(page.redirect_chain, [(LIST, 302)])
        self.assertEqual(notices(html), ["File revoked."])
        self.assertEqual([row["cells"]["File"] for row in rows_in(html)], ["keep-me.txt"])

    def test_a_message_never_carries_a_name_or_a_link(self):
        artifact = self.make_file(HOSTILE)
        html = self.client.post(self.revoke_url(artifact), follow=True).content.decode()
        self.assertEqual(notices(html), ["File revoked."], "nothing of the file goes into a message: it is stored in the session or a cookie")
        self.assertNotIn(artifact.token, html)

    def test_a_second_post_for_the_same_file_is_not_found(self):
        artifact = self.make_file()
        self.assertEqual(self.client.post(self.revoke_url(artifact)).status_code, 302)
        again = self.client.post(self.revoke_url(artifact))
        self.assertEqual(again.status_code, 404, "backend/dashboard/views.py: a resent form finds no file, as a missing id does, and changes nothing")

    def test_only_the_workspace_the_list_reads_is_revoked_from(self):
        # The list and account.revoke_artifact read get_or_create_workspace(user), the oldest workspace the user owns. The page agrees:
        # a file of a second workspace is not found, for the confirmation page as for the revoke, so no page offers what no revoke does.
        second = Workspace.objects.create(owner=self.owner, name="second")
        artifact = self.make_file("elsewhere.txt", workspace=second)
        self.assertEqual(self.client.get(self.revoke_url(artifact)).status_code, 404,
                         "backend/dashboard/views.py: _own_file looks in get_or_create_workspace(user), the workspace the account functions read")
        self.assertEqual(self.client.post(self.revoke_url(artifact)).status_code, 404)
        self.assertTrue(Artifact.objects.filter(pk=artifact.pk).exists() and self.stored(artifact))

    def test_a_storage_failure_keeps_the_row_and_says_the_file_is_not_gone_yet(self):
        artifact = self.make_file("stuck.txt", data=b"still here")
        with self.storage_down():
            page = self.client.post(self.revoke_url(artifact), follow=True)
        html = page.content.decode()
        self.assertEqual(page.redirect_chain, [(LIST, 302)])
        self.assertEqual(notices(html), ["File revoked. Its link has stopped working, but storage did not confirm the delete. The file will be removed in a later cleanup."],
                         "backend/dashboard/views.py: REVOKED_PENDING, when revoke_artifact kept the row because the storage delete failed")
        kept = Artifact.objects.get(pk=artifact.pk)
        self.assertTrue(kept.is_expired(), "services.revoke_artifact kills the link first, then keeps the row for the sweep")
        self.assertTrue(self.stored(artifact), "the bytes are still in storage; the sentence does not say they are gone")
        self.assertEqual(self.link_status(artifact), 404)
        row = rows_in(html)[0]
        self.assertEqual((row["state"], row["cells"]["State"], row["cells"]["Actions"]), ("expired", "Expired", "None"),
                         "the list then shows the kept row as expired, with no controls")

    def test_the_two_outcomes_have_two_sentences(self):
        revoked, pending = dashboard_views.REVOKED, dashboard_views.REVOKED_PENDING
        self.assertEqual(revoked, "File revoked.")
        self.assertNotEqual(revoked, pending)
        self.assertTrue(pending.startswith(revoked), "the pending sentence still begins File revoked.: the link is dead")

    def test_a_row_kept_after_a_storage_failure_is_left_to_the_sweep(self):
        artifact = self.make_file()
        with self.storage_down():
            self.client.post(self.revoke_url(artifact), follow=True)
        page = self.client.post(self.revoke_url(artifact), follow=True)
        self.assertEqual(notices(page.content.decode()), [dashboard_views.NOT_LIVE],
                         "backend/dashboard/views.py: the kept row is expired, so a second revoke is the not-live sentence; the sweep deletes it")
        self.assertTrue(Artifact.objects.filter(pk=artifact.pk).exists() and self.stored(artifact))
        call_command("sweep_expired", stdout=io.StringIO())
        self.assertFalse(Artifact.objects.filter(pk=artifact.pk).exists(), "the cleanup the sentence promises: the sweep retries the delete")
        self.assertFalse(self.stored(artifact))

    def test_a_post_without_the_csrf_token_is_form_not_sent_and_changes_nothing(self):
        artifact = self.make_file()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        with self.assertLogs("django.security.csrf", "WARNING"):
            response = client.post(self.revoke_url(artifact))
        self.assertEqual(response.status_code, 403)
        self.assertIn("Form not sent.", response.content.decode(), "backend/pages/errors.py: CSRF_FAILURE_VIEW draws the shell's Form not sent page")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow", "backend/pages/errors.py: an error page drawn for an address under /app/ carries the header")
        self.assertTrue(Artifact.objects.filter(pk=artifact.pk).exists() and self.stored(artifact))
        self.assertFalse(Artifact.objects.get(pk=artifact.pk).is_expired())

    def test_a_post_with_a_wrong_token_is_refused_too(self):
        artifact = self.make_file()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        client.get(self.revoke_url(artifact))
        with self.assertLogs("django.security.csrf", "WARNING"):
            response = client.post(self.revoke_url(artifact), {"csrfmiddlewaretoken": "wrong"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Artifact.objects.filter(pk=artifact.pk).exists())

    def test_the_forms_own_token_is_accepted(self):
        artifact = self.make_file()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        page = client.get(self.revoke_url(artifact)).content.decode()
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', main_of(page)).group(1)
        response = client.post(self.revoke_url(artifact), {"csrfmiddlewaretoken": token})
        self.assertEqual((response.status_code, response["Location"]), (302, LIST),
                         "backend/templates/dashboard/file_revoke.html: {% csrf_token %} sits inside the page's own <form>")
        self.assertFalse(Artifact.objects.filter(pk=artifact.pk).exists())

    def test_signed_out_requests_go_to_sign_in_and_change_nothing(self):
        artifact = self.make_file()
        anonymous = Client()
        for method in ("get", "post"):
            with self.subTest(method=method):
                response = getattr(anonymous, method)(self.revoke_url(artifact))
                self.assertEqual((response.status_code, response["Location"]), (302, f"/accounts/login/?next=/app/files/{artifact.id}/revoke/"))
        self.assertTrue(Artifact.objects.filter(pk=artifact.pk).exists() and self.stored(artifact))


class FilesThatAreNotLiveTests(FilesCase):
    """An expired file and a held file are the owner's, but they are not revoked here: the list offers Revoke on live files only, and a
    GET or a POST for any other goes back to the list with a sentence and changes nothing."""

    def test_a_get_and_a_post_go_back_to_the_list_with_a_sentence_and_change_nothing(self):
        for label, kwargs in (("expired", {"days": -1}), ("held", {"held": True}), ("held and expired", {"held": True, "days": -1})):
            artifact = self.make_file(f"{label}.txt", **kwargs)
            before = Artifact.objects.values().get(pk=artifact.pk)
            for method in ("get", "post"):
                with self.subTest(file=label, method=method):
                    page = getattr(self.client, method)(self.revoke_url(artifact), follow=True)
                    self.assertEqual(page.redirect_chain, [(LIST, 302)],
                                     "backend/dashboard/views.py: file_revoke sends a file that is not live back to Files (state_of in backend/dashboard/file_rows.py)")
                    self.assertEqual(notices(page.content.decode()), [dashboard_views.NOT_LIVE])
                    self.assertEqual(Artifact.objects.values().get(pk=artifact.pk), before)
                    self.assertTrue(self.stored(artifact), "nothing is deleted from storage for a file that is not live")

    def test_a_file_that_expired_while_the_page_was_open_is_not_revoked(self):
        artifact = self.make_file()
        confirm = self.client.get(self.revoke_url(artifact))
        self.assertEqual(confirm.status_code, 200)
        Artifact.objects.filter(pk=artifact.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        page = self.client.post(self.revoke_url(artifact), follow=True)
        self.assertEqual(notices(page.content.decode()), [dashboard_views.NOT_LIVE])
        self.assertTrue(Artifact.objects.filter(pk=artifact.pk).exists())

    def test_the_sentence_says_what_is_true_of_both(self):
        sentence = dashboard_views.NOT_LIVE
        self.assertIn("link has already stopped working", sentence)
        self.assertNotIn("expired", sentence, "backend/dashboard/views.py: NOT_LIVE covers a held file too, so it names neither state")


class FilesAreTheirOwnersTests(FilesCase):
    """Another user's file, and a file that does not exist, are the same 404 for a GET and a POST, and nothing changes."""

    def setUp(self):
        super().setUp()
        self.intruder, self.intruder_workspace = self.other_user("intruder")
        self.client = Client()
        self.client.force_login(self.intruder)

    def test_another_users_file_is_not_found_and_stays(self):
        theirs = self.make_file("private.txt", days=3)
        before = Artifact.objects.values().get(pk=theirs.pk)
        for method in ("get", "post"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.revoke_url(theirs))
                self.assertEqual(response.status_code, 404,
                                 "backend/dashboard/views.py: _own_file looks the file up in the signed-in user's own workspace; anyone else's is a 404, never a 403")
                self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow", "backend/pages/errors.py: the shell's 404 under /app/ carries the header")
                self.assertNotIn("private.txt", response.content.decode())
        self.assertEqual(Artifact.objects.values().get(pk=theirs.pk), before)
        self.assertTrue(self.stored(theirs), "a POST from someone else deletes nothing")
        self.assertEqual(self.link_status(theirs), 200)

    def test_a_strangers_expired_or_held_file_is_the_same_404_and_not_the_not_live_sentence(self):
        # The state check comes after the ownership check, or the sentence would tell a stranger that an id exists.
        for label, kwargs in (("expired", {"days": -1}), ("held", {"held": True})):
            theirs = self.make_file(f"{label}.txt", **kwargs)
            for method in ("get", "post"):
                with self.subTest(file=label, method=method):
                    response = getattr(self.client, method)(self.revoke_url(theirs))
                    self.assertEqual(response.status_code, 404,
                                     "backend/dashboard/views.py: file_revoke looks the file up (_own_file) before it asks whether the file is live")
                    self.assertEqual(notices(response.content.decode()), [])
            self.assertTrue(Artifact.objects.filter(pk=theirs.pk).exists() and self.stored(theirs))

    def test_the_answer_is_the_same_as_for_a_file_that_does_not_exist(self):
        theirs = self.make_file("private.txt")
        missing = "00000000-0000-4000-8000-00000000dead"
        for method in ("get", "post"):
            with self.subTest(method=method):
                found = getattr(self.client, method)(self.revoke_url(theirs))
                absent = getattr(self.client, method)(f"/app/files/{missing}/revoke/")
                self.assertEqual((found.status_code, without_tokens(found.content.decode())), (absent.status_code, without_tokens(absent.content.decode())),
                                 "backend/dashboard/views.py: another user's file must answer exactly as a missing one, or the answer tells which ids exist")

    def test_a_malformed_id_is_not_found(self):
        for path in ("/app/files/not-a-uuid/revoke/", "/app/files/123/revoke/", "/app/files/%00/revoke/", f"/app/files/{'a' * 300}/revoke/"):
            for method in ("get", "post"):
                with self.subTest(path=path[:30], method=method):
                    self.assertEqual(getattr(self.client, method)(path).status_code, 404)

    def test_staff_have_no_more_than_anyone_else(self):
        staff = User.objects.create_user("staffer", password=PASSWORD, is_staff=True, is_superuser=True)
        theirs = self.make_file("private.txt")
        client = Client()
        client.force_login(staff)
        self.assertEqual(client.get(self.revoke_url(theirs)).status_code, 404)
        self.assertEqual(client.post(self.revoke_url(theirs)).status_code, 404)
        self.assertTrue(Artifact.objects.filter(pk=theirs.pk).exists())

    def test_the_intruders_own_file_can_be_revoked(self):
        mine = self.make_file("mine.txt", workspace=self.intruder_workspace)
        self.assertEqual(self.client.post(self.revoke_url(mine)).status_code, 302)
        self.assertFalse(Artifact.objects.filter(pk=mine.pk).exists())


class FilesConventionTests(SimpleTestCase):
    """What the Files templates, stylesheet and catalog entry must keep."""
    longMessage = False

    def test_the_words_keep_the_voice(self):
        for name in OWN_TEMPLATES:
            text = (TEMPLATES / name).read_text(encoding="utf-8")
            words = re.sub(r"{%.*?%}|{#.*?#}|{{.*?}}", "", text)
            where = f"backend/templates/{name}"
            with self.subTest(template=name):
                self.assertNotIn("—", text, f"{where}: no em dashes; use a colon, a comma or two sentences")
                self.assertNotIn("!", words, f"{where}: no exclamation marks")
                for word in BANNED:
                    self.assertNotIn(word, text.lower(), f"{where}: avoid {word.strip()!r} (AGENTS.md, voice)")
                self.assertIsNone(re.search(r"\$\s?\d", text), f"{where}: no price is typed in a template (AGENTS.md, Pricing changes)")
                self.assertNotRegex(text, r"<(header|footer)\b", f"{where}: a page's <main> holds no <header> or <footer>")

    def test_the_stylesheet_hides_copy_without_a_script_and_wraps_a_long_name(self):
        css = (STATIC / "css/components/file_list.css").read_text(encoding="utf-8")
        self.assertRegex(css, r":root:not\(\.js\) \.files__copy \{\s*display: none;",
                         "backend/static/css/components/file_list.css: Copy needs a script, so it is not shown without one (code_block.css does the same)")
        self.assertEqual(declarations(css, ".files__name").get("overflow-wrap"), "anywhere",
                         "backend/static/css/components/file_list.css: a 512-character name has no place to break and must wrap, or the page scrolls sideways at 320")
        self.assertEqual(declarations(css, ".files__pager .files__older").get("margin-left"), "auto",
                         "backend/static/css/components/file_list.css: Older files stays at the right; the selector weighs what base.css's .label.interactive does")
        self.assertEqual(declarations(css, '.files__row[data-state="expired"]').get("color"), "var(--muted)")
        self.assertEqual(declarations(css, '.files__row[data-state="held"]').get("color"), "var(--muted)")

    def test_the_stylesheet_is_linked_from_the_shell_after_base_css(self):
        shell = (TEMPLATES / "shell.html").read_text(encoding="utf-8")
        link = "{% static 'css/components/file_list.css' %}"
        self.assertIn(link, shell, "backend/templates/shell.html: link file_list.css; an in-place change from another section brings no stylesheet of its own")
        self.assertGreater(shell.index(link), shell.index("{% static 'css/base.css' %}"))
        for name in ("files.html", "file_revoke.html"):
            text = (TEMPLATES / "dashboard" / name).read_text(encoding="utf-8")
            self.assertNotIn("{% block styles %}", text, f"backend/templates/dashboard/{name}: a stylesheet in a page's styles block would be missing after an in-place change; use the component's")

    def test_the_catalog_shows_the_list_in_each_of_its_states(self):
        entry = next((entry for entry in catalog.entries() if entry.name == "file_list"), None)
        self.assertIsNotNone(entry, "backend/pages/catalog.py: add an Entry for the file_list component")
        self.assertEqual(entry.within, "app",
                         "backend/pages/catalog.py: pass within=\"app\" to the file_list Entry; the rows stack by a container query on .app, which the catalog draws only when it wraps the sample")
        samples = (entry.context, *entry.states)
        self.assertEqual(len(samples), 3, "backend/pages/catalog.py: the file_list Entry shows the list, no files at all, and no older files")
        listed = entry.context["files"]
        self.assertEqual(sorted({row["state"] for row in listed}), ["expired", "held", "live"])
        self.assertTrue(any(len(row["name"]) == 512 for row in listed), "a 512-character name is in the catalog, where the browser checks look at it at 320")
        self.assertTrue(any(row["flags"] for row in listed))
        prefixes = [sample["id_prefix"] for sample in samples]
        self.assertEqual(len(prefixes), len(set(prefixes)), "each state needs an id_prefix of its own, or the catalog repeats ids")
        for sample_context in samples:
            render_to_string("components/file_list.html", sample_context)
