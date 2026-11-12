"""One module decides which files a person may see and revoke (drop_core/file_access.py): every surface agrees, and
nothing else reads files by workspace."""
import ast
import json
import re
import shutil
import tempfile
import uuid
from datetime import timedelta
from unittest import mock
from urllib.parse import urlsplit

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from dashboard.file_rows import held_ids
from . import account
from .file_access import NotYourFile, file_details, file_to_revoke, files_visible_to, set_visibility
from .models import (
    FILE_VISIBILITY_ALWAYS_ONLY_ME,
    FILE_VISIBILITY_ALWAYS_TEAM,
    VISIBILITY_ONLY_ME,
    VISIBILITY_TEAM,
    APIKey,
    Artifact,
    Membership,
    Workspace,
    administrator_of,
    current_membership,
    get_or_create_workspace,
)
from .services import mint_upload_url, upload_artifact, workspace_storage_bytes
from .tests import _use_local_dir
from .tests_members import BACKEND, _issue_token, _make_app, make_user, second_member

NOT_YOURS = "Only the person who uploaded a file, or the administrator of a team it is shared with, can revoke it."


def make_file(workspace, member, name, visibility=VISIBILITY_ONLY_ME, **fields):
    fields.setdefault("expires_at", timezone.now() + timedelta(days=1))
    return Artifact.objects.create(
        workspace=workspace, member=member, filename=name, size_bytes=fields.pop("size_bytes", 1),
        visibility=visibility, **fields,
    )


class PrivacyMatrixTests(TestCase):
    """Administrator Ada, members Grace and Lin. Each person has one "only me" file and one "team" file."""

    # (viewer, file, may they see it, may they revoke it)
    TABLE = [
        ("ada", "ada-private", True, True),
        ("ada", "ada-shared", True, True),
        ("ada", "grace-private", False, False),
        ("ada", "grace-shared", True, True),
        ("grace", "ada-private", False, False),
        ("grace", "ada-shared", True, False),
        ("grace", "grace-private", True, True),
        ("grace", "grace-shared", True, True),
        ("grace", "lin-private", False, False),
        ("grace", "lin-shared", True, False),
    ]

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        _use_local_dir(self, self.tmpdir)
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        ada = make_user("ada")
        self.workspace = get_or_create_workspace(ada)
        Workspace.objects.filter(pk=self.workspace.pk).update(plan="team")
        self.people = {
            "ada": administrator_of(self.workspace),
            "grace": second_member(self.workspace, "grace"),
            "lin": second_member(self.workspace, "lin"),
        }
        self.keys = {name: APIKey.create_key(self.workspace, name, member=m)[1] for name, m in self.people.items()}
        app = _make_app()
        self.tokens = {name: _issue_token(m.user, app)[0] for name, m in self.people.items()}

    def fresh_files(self):
        """Every person's two files again, as the row before may have revoked one."""
        Artifact.objects.all().delete()
        files = {}
        for name, member in self.people.items():
            files[f"{name}-private"] = make_file(self.workspace, member, f"{name}-private.txt")
            files[f"{name}-shared"] = make_file(self.workspace, member, f"{name}-shared.txt", VISIBILITY_TEAM)
        return files

    def rows(self):
        for viewer, name, seen, revocable in self.TABLE:
            with self.subTest(viewer=viewer, file=name):
                yield viewer, name, seen, revocable, self.fresh_files()[name]

    def bearer(self, secret):
        return {"HTTP_AUTHORIZATION": f"Bearer {secret}"}

    def assert_listed(self, response, artifact, seen):
        text = response.content.decode()
        self.assertEqual(artifact.filename in text, seen, text)

    def assert_revoke(self, artifact, revocable, seen, delete, missing):
        response = delete(artifact.id)
        if revocable:
            self.assertEqual(response.status_code, 204)
            self.assertFalse(Artifact.objects.filter(pk=artifact.pk).exists())
            return
        if seen:
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json(), {"error": NOT_YOURS, "code": "not_your_file"})
        else:
            gone = missing(uuid.uuid4())
            self.assertEqual((response.status_code, response.content), (gone.status_code, gone.content))
            self.assertNotIn(artifact.filename, response.content.decode())
        self.assertTrue(Artifact.objects.filter(pk=artifact.pk).exists())

    def test_only_the_uploader_can_set_visibility(self):
        for viewer in self.people:
            for name in ("ada-private", "ada-shared", "grace-private", "grace-shared", "lin-private", "lin-shared"):
                with self.subTest(viewer=viewer, file=name):
                    artifact = self.fresh_files()[name]
                    target = VISIBILITY_ONLY_ME if artifact.visibility == VISIBILITY_TEAM else VISIBILITY_TEAM
                    changed = set_visibility(self.people[viewer], artifact.id, target)
                    artifact.refresh_from_db()
                    mine = name.startswith(viewer)
                    self.assertEqual((changed, artifact.visibility == target), (mine, mine))

    def test_nothing_changes_when_the_team_decides_or_the_file_is_not_live(self):
        for setting in (FILE_VISIBILITY_ALWAYS_TEAM, FILE_VISIBILITY_ALWAYS_ONLY_ME):
            Workspace.objects.filter(pk=self.workspace.pk).update(file_visibility=setting)
            artifact = self.fresh_files()["grace-private"]
            self.assertFalse(set_visibility(self.people["grace"], artifact.id, VISIBILITY_TEAM), setting)
        Workspace.objects.filter(pk=self.workspace.pk).update(file_visibility="members_choose")
        artifact = self.fresh_files()["grace-private"]
        self.assertFalse(set_visibility(self.people["grace"], artifact.id, "everyone"))
        Artifact.objects.filter(pk=artifact.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(set_visibility(self.people["grace"], artifact.id, VISIBILITY_TEAM))
        Artifact.objects.filter(pk=artifact.pk).update(expires_at=timezone.now() + timedelta(days=1), safety_status="held")
        self.assertFalse(set_visibility(self.people["grace"], artifact.id, VISIBILITY_TEAM))

    def test_details_say_who_uploaded_and_what_may_be_done(self):
        files = self.fresh_files()
        ids = [artifact.id for artifact in files.values() if artifact.member_id != self.people["lin"].pk or artifact.visibility == VISIBILITY_TEAM]
        details = file_details(self.people["grace"], ids)
        mine, theirs = details[files["grace-private"].id], details[files["ada-shared"].id]
        self.assertEqual((mine["uploaded_by"], mine["visibility"], mine["can_revoke"], mine["can_change_visibility"]), ("You", VISIBILITY_ONLY_ME, True, True))
        self.assertEqual((theirs["uploaded_by"], theirs["visibility"], theirs["can_revoke"], theirs["can_change_visibility"]), ("ada@example.com", VISIBILITY_TEAM, False, False))
        admin = file_details(self.people["ada"], [files["grace-shared"].id])[files["grace-shared"].id]
        self.assertEqual((admin["uploaded_by"], admin["can_revoke"], admin["can_change_visibility"]), ("grace@example.com", True, False))
        self.assertNotIn(files["grace-private"].id, file_details(self.people["ada"], [files["grace-private"].id]))

    def test_rest_list_with_a_key(self):
        for viewer, _, seen, _, artifact in self.rows():
            response = self.client.get("/api/v1/artifacts/", **self.bearer(self.keys[viewer]))
            self.assert_listed(response, artifact, seen)

    def test_rest_list_with_a_session(self):
        for viewer, _, seen, _, artifact in self.rows():
            self.client.force_login(self.people[viewer].user)
            self.assert_listed(self.client.get("/api/v1/artifacts/"), artifact, seen)

    def test_rest_revoke_with_a_key(self):
        for viewer, _, seen, revocable, artifact in self.rows():
            delete = lambda pk: self.client.delete(f"/api/v1/artifacts/{pk}/", **self.bearer(self.keys[viewer]))
            self.assert_revoke(artifact, revocable, seen, delete, delete)

    def test_rest_revoke_with_a_session(self):
        for viewer, _, seen, revocable, artifact in self.rows():
            self.client.force_login(self.people[viewer].user)
            delete = lambda pk: self.client.delete(f"/api/v1/artifacts/{pk}/")
            self.assert_revoke(artifact, revocable, seen, delete, delete)

    def mcp(self, name, arguments, secret):
        response = self.client.post(
            "/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}),
            content_type="application/json",
            **self.bearer(secret),
        )
        self.assertEqual(response.status_code, 200)
        return response

    def test_mcp_list_with_a_key(self):
        for viewer, _, seen, _, artifact in self.rows():
            self.assert_listed(self.mcp("list_artifacts", {}, self.keys[viewer]), artifact, seen)

    def test_mcp_list_with_an_oauth_token(self):
        for viewer, _, seen, _, artifact in self.rows():
            self.assert_listed(self.mcp("list_artifacts", {}, self.tokens[viewer]), artifact, seen)

    def test_mcp_revoke(self):
        for viewer, _, _, revocable, artifact in self.rows():
            response = self.mcp("revoke_artifact", {"id": str(artifact.id)}, self.keys[viewer])
            result = json.loads(response.json()["result"]["content"][0]["text"])
            self.assertEqual(result["revoked"], revocable)
            self.assertEqual(Artifact.objects.filter(pk=artifact.pk).exists(), not revocable)

    def test_dashboard_files_page(self):
        for viewer, _, seen, _, artifact in self.rows():
            self.client.force_login(self.people[viewer].user)
            self.assert_listed(self.client.get(reverse("app-files")), artifact, seen)

    def test_dashboard_revoke_page(self):
        for viewer, _, _, revocable, artifact in self.rows():
            self.client.force_login(self.people[viewer].user)
            url = reverse("app-file-revoke", args=[artifact.id])
            page = self.client.get(url)
            self.assertEqual(page.status_code, 200 if revocable else 404)
            if not revocable:
                self.assertNotIn(artifact.filename, page.content.decode())
            self.assertTrue(Artifact.objects.filter(pk=artifact.pk).exists())
            posted = self.client.post(url)
            self.assertEqual(posted.status_code, 302 if revocable else 404)
            self.assertNotIn(artifact.filename, posted.content.decode())
            self.assertEqual(Artifact.objects.filter(pk=artifact.pk).exists(), not revocable)

    def test_dashboard_held_label(self):
        grace_held = make_file(self.workspace, self.people["grace"], "held.txt", safety_status="held")
        body = [{"id": str(grace_held.id)}]
        self.assertEqual(held_ids(self.people["ada"].user, body), set())
        self.assertEqual(held_ids(self.people["grace"].user, body), {str(grace_held.id)})


class FileAccessTests(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        _use_local_dir(self, self.tmpdir)
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.ada_user = make_user("ada")
        self.workspace = get_or_create_workspace(self.ada_user)
        Workspace.objects.filter(pk=self.workspace.pk).update(plan="team")
        self.ada = administrator_of(self.workspace)
        self.grace = second_member(self.workspace, "grace")

    def test_a_members_pages_show_only_what_they_may_see(self):
        base = timezone.now() - timedelta(hours=1)
        for i in range(12):
            owner = self.grace if i % 2 == 0 else self.ada
            row = make_file(self.workspace, owner, f"f{i}.txt")
            Artifact.objects.filter(pk=row.pk).update(created_at=base + timedelta(minutes=i))
        _, raw = APIKey.create_key(self.workspace, "agent", member=self.grace)
        auth = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
        expected = [f"f{i}.txt" for i in range(10, -1, -2)]
        for header in ("X-Next-Cursor", "Link"):
            with self.subTest(header=header):
                names, response = [], self.client.get("/api/v1/artifacts/?limit=2", **auth)
                for _ in range(10):
                    self.assertEqual(response.status_code, 200)
                    names += [item["filename"] for item in response.json()]
                    if header == "X-Next-Cursor":
                        cursor = response.headers.get("X-Next-Cursor")
                        url = f"/api/v1/artifacts/?limit=2&cursor={cursor}" if cursor else None
                    else:
                        link = response.headers.get("Link")
                        url = re.search(r"<([^>]+)>", link).group(1) if link else None
                    if url is None:
                        break
                    response = self.client.get(url, **auth)
                self.assertEqual(names, expected)

    def test_files_left_behind_stay_their_uploaders(self):
        grace = make_user("gracie")
        old = get_or_create_workspace(grace)
        old_row = administrator_of(old)
        kept = make_file(old, old_row, "kept.txt", size_bytes=7)
        team_row = Membership.objects.create(workspace=self.workspace, user=grace, role=Membership.MEMBER)
        Membership.objects.filter(pk=old_row.pk).update(removed_at=timezone.now())
        self.assertEqual(current_membership(grace), team_row)
        self.assertEqual(list(files_visible_to(team_row)), [kept])
        self.assertEqual(file_to_revoke(team_row, kept.id), kept)
        self.assertEqual(workspace_storage_bytes(self.workspace), 0)
        self.assertEqual(workspace_storage_bytes(old), 7)
        self.client.force_login(grace)
        self.assertEqual([r["filename"] for r in self.client.get("/api/v1/artifacts/").json()], ["kept.txt"])
        self.assertEqual(self.client.delete(f"/api/v1/artifacts/{kept.id}/").status_code, 204)
        self.assertFalse(Artifact.objects.filter(pk=kept.pk).exists())

        private = make_file(self.workspace, team_row, "private.txt")
        shared = make_file(self.workspace, team_row, "shared.txt", VISIBILITY_TEAM)
        Membership.objects.filter(pk=team_row.pk).update(removed_at=timezone.now())
        back = current_membership(grace)
        self.assertEqual(back.workspace, old)
        self.assertEqual(set(files_visible_to(back)), {private, shared})
        self.assertEqual(file_to_revoke(back, private.id), private)
        self.assertEqual(set(files_visible_to(self.ada)), {shared})
        teams = make_file(self.workspace, self.ada, "teams.txt", VISIBILITY_TEAM)
        with self.assertRaises(NotYourFile):
            file_to_revoke(self.grace, teams.id)
        self.assertIsNone(file_to_revoke(self.ada, private.id))
        self.assertEqual(file_to_revoke(self.ada, shared.id), shared)

    def test_a_file_from_an_upload_url_belongs_to_the_person_who_minted_it(self):
        key, _ = APIKey.create_key(self.workspace, "agent", member=self.grace)
        pending = mint_upload_url(self.workspace, key, "a.txt", member=self.grace)
        response = self.client.put(f"/api/v1/uploads/{pending.token}", b"hi", content_type="application/octet-stream")
        self.assertEqual(response.status_code, 201)
        artifact = Artifact.objects.get()
        self.assertEqual(artifact.member, self.grace)
        self.assertEqual(list(files_visible_to(self.grace)), [artifact])
        self.assertEqual(list(files_visible_to(self.ada)), [])

    def test_the_page_answers_404_for_any_refused_revoke(self):
        artifact = make_file(self.workspace, self.ada, "mine.txt")
        refused = account.Answer(403, {"error": NOT_YOURS, "code": "not_your_file"})
        self.client.force_login(self.ada_user)
        with mock.patch("dashboard.views.account.revoke_artifact", return_value=refused):
            response = self.client.post(reverse("app-file-revoke", args=[artifact.id]))
        self.assertEqual(response.status_code, 404)

    def test_a_second_workspace_of_one_owner_stays_hidden(self):
        second = Workspace.objects.create(owner=self.ada_user, name="second")
        row = administrator_of(second)
        hidden = make_file(second, row, "second.txt")
        self.assertEqual(current_membership(self.ada_user), self.ada)
        self.assertEqual(list(files_visible_to(self.ada)), [])
        self.assertIsNone(file_to_revoke(self.ada, hidden.id))
        self.client.force_login(self.ada_user)
        self.assertEqual(self.client.get("/api/v1/artifacts/").json(), [])
        self.assertEqual(self.client.delete(f"/api/v1/artifacts/{hidden.id}/").status_code, 404)

    def test_a_file_with_no_member_is_shown_to_nobody(self):
        artifact = upload_artifact(self.workspace, None, "stray.txt", b"hello", "text/plain")
        Artifact.objects.filter(pk=artifact.pk).update(member=None, visibility=VISIBILITY_ONLY_ME)
        self.assertEqual(list(files_visible_to(self.ada)), [])
        self.assertIsNone(file_to_revoke(self.ada, artifact.id))
        self.client.force_login(self.ada_user)
        self.assertEqual(self.client.get("/api/v1/artifacts/").json(), [])
        self.assertEqual(self.client.delete(f"/api/v1/artifacts/{artifact.id}/").status_code, 404)
        self.assertEqual(self.client.get(f"/a/{artifact.token}").status_code, 200)

    def test_a_one_person_list_is_byte_identical(self):
        base = timezone.now() - timedelta(hours=1)
        for i in range(3):
            row = make_file(self.workspace, self.ada, f"f{i}.txt", mime_type="text/plain", size_bytes=i)
            Artifact.objects.filter(pk=row.pk).update(created_at=base + timedelta(minutes=i))
        expected = [
            {
                "id": str(a.id), "filename": a.filename, "mime_type": a.mime_type, "size_bytes": a.size_bytes,
                "url": account.artifact_url(a), "expires_at": a.expires_at.isoformat(),
                "secret_flags": a.secret_flags, "created_at": a.created_at.isoformat(),
            }
            for a in Artifact.objects.filter(workspace=self.workspace).order_by("-created_at")
        ]
        self.client.force_login(self.ada_user)
        body = self.client.get("/api/v1/artifacts/").content.decode()
        self.assertEqual(json.dumps(json.loads(body)), json.dumps(expected))

    def test_the_list_runs_no_join(self):
        make_file(self.workspace, self.grace, "a.txt")
        with CaptureQueriesContext(connection) as queries:
            list(files_visible_to(self.grace))
        table = Artifact._meta.db_table
        selects = [q["sql"] for q in queries if f'FROM "{table}"' in q["sql"]]
        self.assertEqual(len(selects), 1, [q["sql"] for q in queries])
        self.assertNotIn("JOIN", selects[0].upper())


def non_test_sources():
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if "/migrations/" in rel or path.name.startswith("tests") or "/tests" in rel or rel.startswith("tests"):
            continue
        yield rel, path.read_text()


class OnlyFileAccessReadsFilesTests(SimpleTestCase):
    # Every other read of Artifact and PendingUpload, counted per file, each with what those reads are.
    REMAINING = {
        "drop_billing/account.py": 1,  # an exists, for the allowance check
        "drop_core/management/commands/funnel_report.py": 1,  # a count of workspaces that have a file
        "drop_core/management/commands/reconcile_storage.py": 2,  # a token lookup and an exists, for the storage sweep
        "drop_core/models.py": 1,  # reopening a row marks its unused upload URLs used, and returns nothing
        "drop_core/services.py": 5,  # the pending upload create, two storage sums, and revoke_artifact's lookup and expiry update
        "drop_core/signals.py": 1,  # prose in a docstring, no read
        "drop_core/sweep.py": 2,  # the expiry sweep and the pending upload cleanup, no person involved
        "drop_core/teams.py": 2,  # joining marks the unused upload URLs of the person's own place used, and returns nothing; member_rows sums the live files' sizes for each person
        "drop_core/views.py": 7,  # two upload-number counts, the pending upload steps, and serve_artifact's lookup by token
        "drop_mcp/views.py": 1,  # the upload number, a count
    }

    def test_no_other_code_reads_files_by_workspace(self):
        pattern = re.compile(r"\b(?:Artifact|PendingUpload)\.objects\b|\.artifacts\b")
        found = {}
        for rel, text in non_test_sources():
            if rel == "drop_core/file_access.py":
                continue
            count = len(pattern.findall(text))
            if count:
                found[rel] = count
        self.assertEqual(
            found,
            self.REMAINING,
            "Something reads files by workspace. List and revoke through drop_core/file_access.py "
            "(AGENTS.md, People inside an account).",
        )

    def test_only_file_access_callers_reach_the_service_revoke(self):
        callers = {}
        for rel, text in non_test_sources():
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("services") and any(
                    alias.name == "revoke_artifact" for alias in node.names
                ):
                    callers[rel] = text
        self.assertEqual(set(callers), {"drop_core/account.py", "drop_mcp/views.py"})
        for rel, text in callers.items():
            self.assertIn("file_to_revoke(", text, rel)
            self.assertLess(text.index("file_to_revoke("), text.index("(artifact.workspace"), rel)
