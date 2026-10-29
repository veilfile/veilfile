"""drop_core.account: usage, API keys, files and connected apps as plain functions.

The JSON endpoints and the dashboard call these functions, so each test here calls one directly and reads its Answer:
status, body and headers. tests_json_answers.py holds the endpoints to the bytes clients read; these tests hold the
functions to the rules the dashboard leans on: whose objects a function can reach, what it refuses and in which
order, what it changes, and that the raw key of a new API key exists only in its answer.

Every class sets longMessage = False, so a failure prints its own message, which names the file and the fix.
"""

import ast
import hashlib
import os
import re
import secrets
import shutil
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.utils import timezone
from django.utils.functional import SimpleLazyObject
from oauth2_provider.models import (
    get_access_token_model,
    get_application_model,
    get_grant_model,
    get_refresh_token_model,
)
from rest_framework.test import APIClient

from drop_core import account
from drop_core.account import Answer
from drop_core.errors import answer_response
from drop_core.models import (
    APIKey,
    Artifact,
    EmailVerification,
    FunnelEvent,
    UsageCounter,
    Workspace,
    get_or_create_workspace,
)
from drop_core.oauth import OAUTH_SCOPE_MCP_USE, resolve_oauth_credential
from drop_core.plans import PLANS
from drop_core.services import upload_artifact
from drop_core.storage import LocalStorage
from drop_core.tests_json_answers import ShortFailures

User = get_user_model()
WHERE = "backend/drop_core/account.py"
ACCOUNT = (
    f"{WHERE}: the JSON endpoints and the dashboard share this function. Fix the function; an answer that clients "
    "read changes only with their release order planned (AGENTS.md, invariant 4)"
)
REDIRECT = "http://127.0.0.1:9876/callback"


def make_user(username, verified=True, plan="free"):
    user = User.objects.create_user(username=username, email=f"{username}@example.com")
    EmailVerification.objects.create(
        user=user,
        email=user.email,
        is_verified=verified,
        verified_at=timezone.now() if verified else None,
    )
    workspace = get_or_create_workspace(user)
    if plan != "free":
        workspace.plan = plan
        workspace.save()
    return user


class FailingDeleteStorage(LocalStorage):
    def delete(self, token):
        raise OSError("storage is down")


class AccountCase(ShortFailures, TestCase):
    longMessage = False

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-account-")
        env = mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": self.tmpdir, "DROP_STORAGE": "local"})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        # Mark a sweep as just run, so that an upload does not start one.
        cache.clear()
        self.addCleanup(cache.clear)
        cache.set("drop_last_sweep_ts", time.time(), 3600)
        self.user = make_user("owner")
        self.workspace = get_or_create_workspace(self.user)
        self.stranger = make_user("stranger")
        self.strangers_workspace = get_or_create_workspace(self.stranger)

    def files(self, workspace, n, expires=None):
        base = timezone.now() - timedelta(hours=1)
        expires = expires or timezone.now() + timedelta(days=3)
        rows = []
        for i in range(n):
            row = Artifact.objects.create(
                workspace=workspace, filename=f"f{i}.txt", mime_type="text/plain", size_bytes=i * 1000, expires_at=expires
            )
            Artifact.objects.filter(pk=row.pk).update(created_at=base + timedelta(minutes=i))
            row.refresh_from_db()
            rows.append(row)
        return rows

    def app(self, name="Claude Code"):
        return get_application_model().objects.create(
            name=name,
            client_type="public",
            authorization_grant_type="authorization-code",
            redirect_uris=REDIRECT,
        )

    def connect(self, user, app):
        """An access token and a refresh token of ``user`` for ``app``; returns the raw access token."""
        raw = secrets.token_urlsafe(32)
        token = get_access_token_model().objects.create(
            user=user, application=app, token=raw, expires=timezone.now() + timedelta(hours=1), scope=OAUTH_SCOPE_MCP_USE
        )
        refresh = get_refresh_token_model().objects.create(
            user=user, application=app, token=secrets.token_urlsafe(32), access_token=token
        )
        return raw, refresh


class AnswerTests(AccountCase):
    def test_an_answer_is_a_status_a_body_and_headers_that_default_to_none_and_cannot_be_written(self):
        answer = Answer(204, {})
        status, body, headers = answer
        self.assertEqual((status, body, dict(headers)), (204, {}, {}), f"{WHERE}: Answer(status, body, headers)")
        with self.assertRaises(TypeError, msg=f"{WHERE}: the default headers are one shared mapping; keep it read-only"):
            answer.headers["X-Next-Cursor"] = "x"

    def test_answer_response_sends_the_status_the_body_and_the_headers(self):
        response = answer_response(Answer(200, [{"a": 1}], {"X-Next-Cursor": "c1"}))
        self.assertEqual((response.status_code, response.data, response["X-Next-Cursor"]), (200, [{"a": 1}], "c1"),
                         "backend/drop_core/errors.py: answer_response sends an Answer as the Response of a JSON endpoint")

    def test_answer_response_sends_no_body_with_a_204(self):
        response = answer_response(Answer(204, {"storage_deleted": False}))
        self.assertEqual((response.status_code, response.data), (204, None),
                         "backend/drop_core/errors.py: a 204 has no body; the dashboard reads the Answer's, the JSON API sends none")


class ModuleRulesTests(AccountCase):
    def source(self):
        return (Path(settings.BASE_DIR) / "drop_core" / "account.py").read_text()

    def test_it_imports_no_rest_framework_and_no_http_layer(self):
        tree = ast.parse(self.source())
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                found.append(node.module or "")
        banned = [name for name in found if name.split(".")[0] == "rest_framework" or name in ("django.http", "django.shortcuts")]
        self.assertEqual(banned, [],
                         f"{WHERE} is plain functions that the JSON endpoints and the dashboard share: it must not import "
                         "Django REST framework or Django's HTTP layer. Take what it needs as arguments, and send the Answer "
                         "from the view (backend/drop_core/errors.py: answer_response)")

    def test_it_types_no_price(self):
        self.assertEqual(re.findall(r"\$\s?\d", self.source()), [],
                         f"{WHERE} must not type a price: plans and prices come from backend/drop_core/plans.py (AGENTS.md, Pricing changes)")


class UsageTests(AccountCase):
    def test_a_free_workspace_with_a_counter_a_key_and_a_file(self):
        UsageCounter.objects.create(workspace=self.workspace, year_month=timezone.now().strftime("%Y-%m"), uploads=7)
        UsageCounter.objects.create(workspace=self.workspace, year_month="2001-01", uploads=99)
        APIKey.create_key(self.workspace, "agent")
        Artifact.objects.create(workspace=self.workspace, filename="a", size_bytes=1500, expires_at=timezone.now() + timedelta(days=1))
        Artifact.objects.create(workspace=self.workspace, filename="gone", size_bytes=9000, expires_at=timezone.now() - timedelta(days=1))
        plan = PLANS["free"]
        answer = account.usage(self.user)
        self.assertEqual(answer.status, 200, ACCOUNT)
        self.assertEqual(
            answer.body,
            {
                "plan": "free",
                "uploads_used": 7,
                "uploads_cap": plan["uploads"],
                "ttl_days": plan["ttl_days"],
                "keys_used": 1,
                "keys_cap": plan["keys"],
                "storage_used_bytes": 1500,
                "storage_cap_bytes": plan["storage_gb"] * 1024**3,
                "email": "owner@example.com",
                "email_verified": True,
            },
            f"{WHERE}: usage() is the body of GET /api/v1/usage/ (this month's counter, live bytes only)",
        )

    def test_scale_has_no_key_cap(self):
        self.workspace.plan = "scale"
        self.workspace.save()
        body = account.usage(self.user).body
        self.assertEqual((body["plan"], body["keys_cap"], body["uploads_cap"]), ("scale", None, PLANS["scale"]["uploads"]), ACCOUNT)

    def test_an_unverified_email_is_reported_not_hidden(self):
        user = make_user("unverified", verified=False)
        body = account.usage(user).body
        self.assertEqual((body["email"], body["email_verified"], body["uploads_used"]), ("unverified@example.com", False, 0), ACCOUNT)

    def test_it_reads_the_users_own_workspace_and_a_named_workspace_when_a_credential_names_one(self):
        self.workspace.plan = "team"
        self.workspace.save()
        second = Workspace.objects.create(owner=self.user, name="second", plan="scale")
        self.assertEqual(account.usage(self.user).body["plan"], "team", f"{WHERE}: the user's oldest workspace")
        self.assertEqual(account.usage(self.user, workspace=second).body["plan"], "scale", f"{WHERE}: workspace= is the credential's own")

    def test_a_dashboard_passes_request_user_which_is_a_lazy_object(self):
        lazy = SimpleLazyObject(lambda: self.user)
        self.assertEqual(account.usage(lazy).body["email"], "owner@example.com", f"{WHERE}: it must accept request.user as the dashboard passes it")


class ListKeysTests(AccountCase):
    def test_every_key_of_the_workspace_newest_first_revoked_ones_included_and_never_the_hash(self):
        old, _raw = APIKey.create_key(self.workspace, "old")
        new, _raw = APIKey.create_key(self.workspace, "new")
        APIKey.objects.filter(pk=old.pk).update(is_active=False, last_used_at=timezone.now())
        APIKey.create_key(self.strangers_workspace, "theirs")
        answer = account.list_keys(self.user)
        self.assertEqual(answer.status, 200, ACCOUNT)
        self.assertEqual([row["name"] for row in answer.body], ["new", "old"], f"{WHERE}: newest first, the other workspace's key absent")
        self.assertEqual([row["is_active"] for row in answer.body], [True, False], ACCOUNT)
        self.assertEqual(
            list(answer.body[0]), ["id", "name", "key_prefix", "created_at", "last_used_at", "is_active"], f"{WHERE}: the fields of GET /api/v1/keys/"
        )
        self.assertIsNone(answer.body[0]["last_used_at"], ACCOUNT)
        self.assertIsInstance(answer.body[1]["last_used_at"], str, ACCOUNT)
        self.assertEqual(answer.body[0]["key_prefix"], new.key_prefix, ACCOUNT)

    def test_a_workspace_with_no_keys_is_an_empty_list(self):
        self.assertEqual(account.list_keys(self.user), Answer(200, []), ACCOUNT)

    def test_a_named_workspace_is_the_one_listed(self):
        APIKey.create_key(self.strangers_workspace, "theirs")
        names = [row["name"] for row in account.list_keys(self.user, workspace=self.strangers_workspace).body]
        self.assertEqual(names, ["theirs"], f"{WHERE}: workspace= is for a credential's own workspace")


class CreateKeyTests(AccountCase):
    def setUp(self):
        super().setUp()
        self.workspace.plan = "team"
        self.workspace.save()

    def test_the_answer_holds_the_raw_key_and_it_is_kept_nowhere_else(self):
        with self.assertNoLogs("drop_core", level="DEBUG"):
            answer = account.create_key(self.user, "ci runner")
        self.assertEqual(answer.status, 201, ACCOUNT)
        self.assertEqual(list(answer.body), ["id", "name", "key_prefix", "api_key"], ACCOUNT)
        raw = answer.body["api_key"]
        self.assertTrue(raw.startswith("vlf_"), ACCOUNT)
        self.assertEqual(answer.body["key_prefix"], raw[:12], ACCOUNT)
        row = APIKey.objects.get(pk=answer.body["id"])
        self.assertEqual(row.key_hash, hashlib.sha256(raw.encode()).hexdigest(), f"{WHERE}: only the hash is stored (AGENTS.md, invariant 1)")
        for field in APIKey._meta.fields:
            self.assertNotIn(raw, str(getattr(row, field.attname)), f"{WHERE}: the raw key must not be stored in {field.name}")
        self.assertNotIn(raw, str(account.list_keys(self.user).body), f"{WHERE}: a later answer must not carry the raw key")
        self.assertEqual(APIKey.verify(raw).pk, row.pk, ACCOUNT)

    def test_the_name_is_trimmed_and_an_empty_one_is_untitled(self):
        for name, kept in (("  ok  ", "ok"), (None, "Untitled key"), ("", "Untitled key"), ("   ", "Untitled key"), ("caf\u00e9", "caf\u00e9")):
            with self.subTest(name=name):
                self.assertEqual(account.create_key(self.user, name).body["name"], kept, ACCOUNT)

    def test_255_characters_are_a_name_and_256_are_not(self):
        self.assertEqual(account.create_key(self.user, "n" * 255).status, 201, ACCOUNT)
        self.assertEqual(account.create_key(self.user, " " + "n" * 255 + " ").status, 201, ACCOUNT)
        answer = account.create_key(self.user, "n" * 256)
        self.assertEqual(
            answer, Answer(400, {"error": "The name is longer than 255 characters. Use a shorter name.", "code": "invalid_request"}), ACCOUNT
        )

    def test_a_name_that_is_not_text_or_holds_a_control_character_is_a_400(self):
        for name, error in (
            (123, "The name must be a string. Send name as text, or leave it out."),
            (["a"], "The name must be a string. Send name as text, or leave it out."),
            (False, "The name must be a string. Send name as text, or leave it out."),
            (0, "The name must be a string. Send name as text, or leave it out."),
            ("a\x00b", "The name contains control characters. Remove them and try again."),
            ("a\tb", "The name contains control characters. Remove them and try again."),
        ):
            with self.subTest(name=name):
                self.assertEqual(account.create_key(self.user, name), Answer(400, {"error": error, "code": "invalid_request"}), ACCOUNT)
        self.assertFalse(APIKey.objects.filter(workspace=self.workspace).exists(), f"{WHERE}: a refused name makes no key")

    def test_a_body_that_is_not_an_object_is_a_400_where_a_bad_name_would_be(self):
        answer = account.create_key(self.user, None, body_is_object=False)
        self.assertEqual(answer, Answer(400, {"error": "The request body must be a JSON object. Send the body as an object, such as one with a name field.", "code": "invalid_request"}), ACCOUNT)

    def test_an_unverified_email_is_refused_first_whatever_else_is_wrong(self):
        user = make_user("unverified", verified=False, plan="team")
        expected = Answer(
            403,
            {
                "error": "Verify your email address before creating API keys. Check your inbox for the verification link.",
                "code": "email_verification_required",
            },
        )
        for name, kwargs in (("fine", {}), (123, {}), (None, {"body_is_object": False})):
            with self.subTest(name=name):
                self.assertEqual(account.create_key(user, name, **kwargs), expected, f"{WHERE}: the verified-email gate comes first")
        self.assertEqual(account.verification_required(user), expected, ACCOUNT)
        self.assertIsNone(account.verification_required(self.user), ACCOUNT)
        self.assertFalse(APIKey.objects.filter(workspace__owner=user).exists(), ACCOUNT)

    def test_a_name_is_checked_before_the_cap(self):
        self.workspace.plan = "free"
        self.workspace.save()
        APIKey.create_key(self.workspace, "the one")
        self.assertEqual(account.create_key(self.user, 123).status, 400, f"{WHERE}: name, then cap")

    def test_the_plans_key_cap_is_a_402_and_makes_no_key(self):
        self.workspace.plan = "free"
        self.workspace.save()
        APIKey.create_key(self.workspace, "the one")
        answer = account.create_key(self.user, "second")
        self.assertEqual(
            answer, Answer(402, {"error": "Your workspace has reached its plan's API key limit. Revoke a key or upgrade your plan.", "code": "key_limit_reached"}), ACCOUNT
        )
        self.assertEqual(APIKey.objects.filter(workspace=self.workspace).count(), 1, ACCOUNT)
        APIKey.objects.filter(workspace=self.workspace).update(is_active=False)
        self.assertEqual(account.create_key(self.user, "after revoking").status, 201, f"{WHERE}: revoked keys do not count")

    def test_scale_has_no_cap(self):
        self.workspace.plan = "scale"
        self.workspace.save()
        for i in range(PLANS["team"]["keys"] + 2):
            self.assertEqual(account.create_key(self.user, f"k{i}").status, 201, ACCOUNT)

    def test_a_key_is_made_in_the_users_own_workspace(self):
        answer = account.create_key(self.user, "mine")
        self.assertEqual(APIKey.objects.get(pk=answer.body["id"]).workspace_id, self.workspace.pk, ACCOUNT)
        self.assertFalse(APIKey.objects.filter(workspace=self.strangers_workspace).exists(), ACCOUNT)

    def test_a_dashboard_passes_request_user_which_is_a_lazy_object(self):
        self.assertEqual(account.create_key(SimpleLazyObject(lambda: self.user), "lazy").status, 201, ACCOUNT)

    def test_the_funnel_records_one_key_issued_with_the_browsers_anonymous_id_when_a_request_is_passed(self):
        request = RequestFactory().post("/app/keys/")
        request.COOKIES["vf_funnel"] = "a" * 32 + ".l"
        account.create_key(self.user, "tracked", request=request)
        event = FunnelEvent.objects.get(stage="key_issued")
        self.assertEqual((event.workspace_id, event.anonymous_id), (self.workspace.pk, "a" * 32), f"{WHERE}: funnel.record(request, KEY_ISSUED)")
        account.create_key(self.user, "untracked")
        self.assertEqual(FunnelEvent.objects.filter(stage="key_issued").count(), 2, ACCOUNT)

    def test_a_refusal_records_nothing(self):
        account.create_key(self.user, 123)
        account.create_key(make_user("unverified", verified=False), "x")
        self.assertFalse(FunnelEvent.objects.filter(stage="key_issued").exists(), ACCOUNT)


class RevokeKeyTests(AccountCase):
    def test_a_key_is_deactivated_not_deleted_and_stops_authenticating(self):
        key, raw = APIKey.create_key(self.workspace, "agent")
        for _ in range(2):
            self.assertEqual(account.revoke_key(self.user, key.pk), Answer(204, {}), ACCOUNT)
        key.refresh_from_db()
        self.assertFalse(key.is_active, ACCOUNT)
        self.assertIsNone(APIKey.verify(raw), f"{WHERE}: a revoked key must stop working at once")

    def test_another_workspaces_key_and_a_missing_one_are_the_same_404(self):
        theirs, raw = APIKey.create_key(self.strangers_workspace, "theirs")
        expected = Answer(404, {"error": "No API key with that id exists in this workspace. Check the id.", "code": "not_found"})
        self.assertEqual(account.revoke_key(self.user, theirs.pk), expected, f"{WHERE}: a user can reach only their own keys")
        self.assertEqual(account.revoke_key(self.user, 999999), expected, ACCOUNT)
        theirs.refresh_from_db()
        self.assertTrue(theirs.is_active, ACCOUNT)

    def test_a_key_may_revoke_only_itself_and_the_404_comes_first(self):
        mine, _raw = APIKey.create_key(self.workspace, "mine")
        sibling, _raw = APIKey.create_key(self.workspace, "sibling")
        theirs, _raw = APIKey.create_key(self.strangers_workspace, "theirs")
        refused = Answer(
            403,
            {"error": "An API key can only revoke itself. Revoke other keys from the dashboard.", "code": "session_required"},
        )
        self.assertEqual(account.revoke_key(self.user, sibling.pk, only_key_id=mine.pk), refused, ACCOUNT)
        sibling.refresh_from_db()
        self.assertTrue(sibling.is_active, ACCOUNT)
        self.assertEqual(account.revoke_key(self.user, theirs.pk, only_key_id=mine.pk).status, 404, ACCOUNT)
        self.assertEqual(account.revoke_key(self.user, mine.pk, only_key_id=mine.pk).status, 204, ACCOUNT)

    def test_a_named_workspace_is_the_one_searched(self):
        theirs, _raw = APIKey.create_key(self.strangers_workspace, "theirs")
        self.assertEqual(account.revoke_key(self.user, theirs.pk, workspace=self.strangers_workspace).status, 204, ACCOUNT)

    def test_revoking_a_key_leaves_oauth_grants_alone(self):
        key, _raw = APIKey.create_key(self.workspace, "agent")
        raw_token, refresh = self.connect(self.user, self.app())
        account.revoke_key(self.user, key.pk)
        refresh.refresh_from_db()
        self.assertIsNone(refresh.revoked, f"{WHERE}: keys and connected apps are separate")
        self.assertIsNotNone(resolve_oauth_credential(raw_token), ACCOUNT)


class ListArtifactsTests(AccountCase):
    def names(self, answer):
        return [row["filename"] for row in answer.body]

    def test_every_file_newest_first_with_the_fields_of_the_endpoint(self):
        self.files(self.workspace, 3)
        self.files(self.strangers_workspace, 2)
        answer = account.list_artifacts(self.user)
        self.assertEqual(answer.status, 200, ACCOUNT)
        self.assertEqual(self.names(answer), ["f2.txt", "f1.txt", "f0.txt"], f"{WHERE}: newest first, only the user's own files")
        self.assertEqual(dict(answer.headers), {}, ACCOUNT)
        self.assertEqual(
            list(answer.body[0]),
            ["id", "filename", "mime_type", "size_bytes", "url", "expires_at", "secret_flags", "created_at"],
            ACCOUNT,
        )
        row = Artifact.objects.get(filename="f2.txt", workspace=self.workspace)
        self.assertEqual(answer.body[0]["url"], f"{settings.DROP_BASE_URL}/a/{row.token}", ACCOUNT)
        self.assertEqual(answer.body[0]["id"], str(row.id), ACCOUNT)

    def test_expired_and_held_files_are_listed_for_the_page_to_mark(self):
        self.files(self.workspace, 1)
        Artifact.objects.create(workspace=self.workspace, filename="old", size_bytes=1, expires_at=timezone.now() - timedelta(days=1))
        Artifact.objects.create(
            workspace=self.workspace, filename="held", size_bytes=1, expires_at=timezone.now() + timedelta(days=1), safety_status="held"
        )
        self.assertEqual({row["filename"] for row in account.list_artifacts(self.user).body}, {"f0.txt", "old", "held"}, ACCOUNT)

    def test_a_walk_by_cursor_returns_every_file_once_in_order_with_ties_broken_by_id(self):
        self.files(self.workspace, 7)
        # Two files share a moment, so the id has to break the tie.
        shared = Artifact.objects.filter(workspace=self.workspace).order_by("created_at")[:2]
        Artifact.objects.filter(pk=shared[1].pk).update(created_at=shared[0].created_at)
        expected = [
            row["id"]
            for row in account.list_artifacts(self.user, limit=200).body
        ]
        seen, cursor, pages = [], None, 0
        while True:
            answer = account.list_artifacts(self.user, limit=2, cursor=cursor)
            seen += [row["id"] for row in answer.body]
            pages += 1
            cursor = answer.headers.get("X-Next-Cursor")
            if not cursor:
                break
        self.assertEqual(seen, expected, f"{WHERE}: the keyset walk must see every file once, in order")
        self.assertEqual(pages, 4, ACCOUNT)
        self.assertEqual(len(set(seen)), 7, ACCOUNT)

    def test_the_next_cursor_is_in_the_headers_only_when_more_follow(self):
        self.files(self.workspace, 3)
        first = account.list_artifacts(self.user, limit=2)
        self.assertRegex(first.headers["X-Next-Cursor"], r"^\d+-[0-9a-f]{32}$", ACCOUNT)
        self.assertEqual(self.names(first), ["f2.txt", "f1.txt"], ACCOUNT)
        last = account.list_artifacts(self.user, limit=2, cursor=first.headers["X-Next-Cursor"])
        self.assertEqual((self.names(last), dict(last.headers)), (["f0.txt"], {}), ACCOUNT)
        whole = account.list_artifacts(self.user, limit=3)
        self.assertEqual(dict(whole.headers), {}, f"{WHERE}: a page that holds the rest has no next cursor")

    def test_limit_is_clamped_and_may_be_text_as_it_is_in_a_query_string(self):
        self.files(self.workspace, 3)
        self.assertEqual(len(account.list_artifacts(self.user, limit=0).body), 1, ACCOUNT)
        self.assertEqual(len(account.list_artifacts(self.user, limit="2").body), 2, ACCOUNT)
        self.assertEqual(len(account.list_artifacts(self.user, limit=100000).body), 3, ACCOUNT)

    def test_a_cursor_alone_pages_by_the_default_and_an_empty_cursor_is_the_first_page(self):
        self.files(self.workspace, 3)
        self.assertEqual(len(account.list_artifacts(self.user, cursor="").body), 3, ACCOUNT)
        cursor = account.list_artifacts(self.user, limit=1).headers["X-Next-Cursor"]
        self.assertEqual(self.names(account.list_artifacts(self.user, cursor=cursor)), ["f1.txt", "f0.txt"], ACCOUNT)

    def test_unusable_paging_is_a_400_that_changes_nothing(self):
        self.files(self.workspace, 2)
        expected = Answer(
            400, {"error": "The limit must be a whole number, and the cursor must be a value from the X-Next-Cursor header. Correct the one that is wrong, or leave both out.", "code": "invalid_request"}
        )
        for kwargs in (
            {"limit": "abc"},
            {"limit": ""},
            {"cursor": "garbage"},
            {"cursor": "123-nothex"},
            {"cursor": "99999999999999999999999-" + "0" * 32},
            {"limit": 2, "cursor": "-1"},
        ):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(account.list_artifacts(self.user, **kwargs), expected, ACCOUNT)

    def test_a_named_workspace_is_the_one_listed(self):
        self.files(self.strangers_workspace, 1)
        answer = account.list_artifacts(self.user, workspace=self.strangers_workspace)
        self.assertEqual(self.names(answer), ["f0.txt"], f"{WHERE}: workspace= is for a credential's own workspace")


class RevokeArtifactTests(AccountCase):
    def upload(self, workspace=None, name="a.txt"):
        return upload_artifact(workspace or self.workspace, None, name, b"hello", "text/plain")

    def test_the_link_the_file_and_the_row_are_gone(self):
        artifact = self.upload()
        answer = account.revoke_artifact(self.user, artifact.id)
        self.assertEqual(answer, Answer(204, {"storage_deleted": True}), ACCOUNT)
        self.assertFalse(Artifact.objects.filter(pk=artifact.pk).exists(), ACCOUNT)
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, artifact.token)), f"{WHERE}: the stored bytes must be deleted too")
        self.assertEqual(self.client.get(f"/a/{artifact.token}").status_code, 404, ACCOUNT)

    def test_when_storage_fails_the_link_is_dead_the_row_stays_and_the_answer_says_so(self):
        artifact = self.upload()
        with mock.patch("drop_core.services.get_storage", return_value=FailingDeleteStorage()):
            with self.assertLogs("drop_core.services", "ERROR"):
                answer = account.revoke_artifact(self.user, artifact.id)
        self.assertEqual(answer, Answer(204, {"storage_deleted": False}), f"{WHERE}: REVOKE_PENDING_DELETE is a 204 whose body says storage_deleted False")
        row = Artifact.objects.get(pk=artifact.pk)
        self.assertTrue(row.is_expired(), ACCOUNT)
        self.assertEqual(self.client.get(f"/a/{artifact.token}").status_code, 404, ACCOUNT)
        self.assertEqual(account.revoke_artifact(self.user, artifact.id), Answer(204, {"storage_deleted": True}), f"{WHERE}: revoking again retries the delete")

    def test_another_workspaces_file_and_a_missing_one_are_the_same_404_and_change_nothing(self):
        theirs = self.upload(self.strangers_workspace)
        expected = Answer(404, {"error": "No file with that id exists in this workspace. Check the id.", "code": "not_found"})
        self.assertEqual(account.revoke_artifact(self.user, theirs.id), expected, f"{WHERE}: a user can reach only their own files")
        self.assertEqual(account.revoke_artifact(self.user, "00000000-0000-0000-0000-000000000000"), expected, ACCOUNT)
        row = Artifact.objects.get(pk=theirs.pk)
        self.assertFalse(row.is_expired(), ACCOUNT)
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, theirs.token)), ACCOUNT)

    def test_a_file_that_has_expired_but_not_yet_been_swept_can_be_revoked(self):
        artifact = self.upload()
        Artifact.objects.filter(pk=artifact.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(account.revoke_artifact(self.user, artifact.id).status, 204, ACCOUNT)
        self.assertFalse(Artifact.objects.filter(pk=artifact.pk).exists(), ACCOUNT)

    def test_a_named_workspace_is_the_one_searched(self):
        theirs = self.upload(self.strangers_workspace)
        self.assertEqual(account.revoke_artifact(self.user, theirs.id, workspace=self.strangers_workspace).status, 204, ACCOUNT)


class GrantTests(AccountCase):
    def test_the_users_connected_apps_and_nobody_elses(self):
        mine, theirs = self.app("Claude Code"), self.app("Theirs")
        self.connect(self.user, mine)
        self.connect(self.stranger, theirs)
        answer = account.list_grants(self.user)
        self.assertEqual(answer.status, 200, ACCOUNT)
        self.assertEqual([row["name"] for row in answer.body], ["Claude Code"], ACCOUNT)
        self.assertEqual(
            list(answer.body[0]), ["id", "name", "client_id", "redirect_host", "last_authorized_at"], f"{WHERE}: the fields of GET /api/v1/oauth/grants/"
        )
        self.assertEqual((answer.body[0]["id"], answer.body[0]["redirect_host"]), (mine.pk, "127.0.0.1:9876"), ACCOUNT)

    def test_a_revoked_app_is_not_listed(self):
        app = self.app()
        _raw, refresh = self.connect(self.user, app)
        refresh.revoke()
        self.assertEqual(account.list_grants(self.user), Answer(200, []), ACCOUNT)

    def test_disconnecting_revokes_the_tokens_and_codes_of_that_app_alone_and_keeps_the_client(self):
        mine, other = self.app("Mine"), self.app("Other")
        raw_mine, refresh_mine = self.connect(self.user, mine)
        raw_other, _refresh = self.connect(self.user, other)
        raw_theirs, _refresh = self.connect(self.stranger, mine)
        get_grant_model().objects.create(
            user=self.user, application=mine, code="pending-code", expires=timezone.now() + timedelta(minutes=5),
            redirect_uri=REDIRECT, scope=OAUTH_SCOPE_MCP_USE, code_challenge="x" * 43, code_challenge_method="S256",
        )
        self.assertEqual(account.disconnect_grant(self.user, mine.pk), Answer(204, {}), ACCOUNT)
        self.assertIsNone(resolve_oauth_credential(raw_mine), f"{WHERE}: a disconnected app must lose access at once")
        refresh_mine.refresh_from_db()
        self.assertIsNotNone(refresh_mine.revoked, ACCOUNT)
        self.assertFalse(get_grant_model().objects.filter(application=mine).exists(), ACCOUNT)
        self.assertIsNotNone(resolve_oauth_credential(raw_other), f"{WHERE}: the user's other apps are untouched")
        self.assertIsNotNone(resolve_oauth_credential(raw_theirs), f"{WHERE}: other users' tokens for the same app are untouched")
        self.assertTrue(get_application_model().objects.filter(pk=mine.pk).exists(), f"{WHERE}: the client row stays, so the app can ask again")

    def test_an_app_the_user_has_nothing_under_is_a_404_and_changes_nothing(self):
        theirs = self.app("Theirs")
        raw_theirs, _refresh = self.connect(self.stranger, theirs)
        expected = Answer(404, {"error": "No connected app with that id exists for you. Check the id under Connected apps on the dashboard.", "code": "not_found"})
        self.assertEqual(account.disconnect_grant(self.user, theirs.pk), expected, f"{WHERE}: a user can reach only their own grants")
        self.assertEqual(account.disconnect_grant(self.user, 999999), expected, ACCOUNT)
        self.assertIsNotNone(resolve_oauth_credential(raw_theirs), ACCOUNT)

    def test_an_app_with_only_a_pending_code_disconnects_once(self):
        app = self.app("Half")
        get_grant_model().objects.create(
            user=self.user, application=app, code="pending-only", expires=timezone.now() + timedelta(minutes=5),
            redirect_uri=REDIRECT, scope=OAUTH_SCOPE_MCP_USE, code_challenge="x" * 43, code_challenge_method="S256",
        )
        self.assertEqual(account.disconnect_grant(self.user, app.pk).status, 204, ACCOUNT)
        self.assertEqual(account.disconnect_grant(self.user, app.pk).status, 404, ACCOUNT)


class EndpointsSendTheirAnswersTests(AccountCase):
    """The JSON endpoints add nothing to an Answer and take nothing from it."""

    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_login(self.user)
        self.key, self.raw = APIKey.create_key(self.workspace, "agent")
        self.files(self.workspace, 3)
        self.connect(self.user, self.app())

    def same(self, response, answer):
        self.assertEqual(response.status_code, answer.status, "backend/drop_core/views.py must send the status of the Answer")
        if answer.status != 204:
            self.assertEqual(response.json(), answer.body, "backend/drop_core/views.py must send the body of the Answer")
        for name, value in answer.headers.items():
            self.assertEqual(response[name], value, f"backend/drop_core/views.py must send the header {name} of the Answer")

    def test_each_endpoint_sends_what_its_function_answers(self):
        self.same(self.api.get("/api/v1/usage/"), account.usage(self.user))
        self.same(self.api.get("/api/v1/keys/"), account.list_keys(self.user))
        self.same(self.api.get("/api/v1/artifacts/"), account.list_artifacts(self.user))
        self.same(self.api.get("/api/v1/artifacts/?limit=2"), account.list_artifacts(self.user, limit=2))
        self.same(self.api.get("/api/v1/artifacts/?limit=x"), account.list_artifacts(self.user, limit="x"))
        self.same(self.api.get("/api/v1/oauth/grants/"), account.list_grants(self.user))
        self.same(self.api.delete("/api/v1/oauth/grants/999999/"), account.disconnect_grant(self.user, 999999))
        self.same(self.api.delete("/api/v1/keys/999999/"), account.revoke_key(self.user, 999999))
        self.same(
            self.api.delete("/api/v1/artifacts/00000000-0000-0000-0000-000000000000/"),
            account.revoke_artifact(self.user, "00000000-0000-0000-0000-000000000000"),
        )

    def test_the_creating_endpoint_sends_what_create_key_answers_for_a_refusal(self):
        self.workspace.plan = "free"
        self.workspace.save()
        response = self.api.post("/api/v1/keys/", {"name": "second"}, format="json")
        self.same(response, account.create_key(self.user, "second"))
        self.assertEqual(response.status_code, 402, ACCOUNT)

    def test_a_key_sees_the_same_usage_as_its_owner(self):
        keyed = APIClient()
        keyed.credentials(HTTP_AUTHORIZATION=f"Bearer {self.raw}")
        self.same(keyed.get("/api/v1/usage/"), account.usage(self.user))
        self.same(keyed.get("/api/v1/keys/"), account.list_keys(self.user))
