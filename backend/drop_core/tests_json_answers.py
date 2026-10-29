"""The JSON endpoints for usage, keys, files and connected apps answer what they always have.

The logic behind these endpoints is drop_core/account.py, which the dashboard calls too, and drop_core/views.py sends
its answers. Published clients (the two CLIs, MCP clients, the dashboard until it is rebuilt) read the bytes: the
order of the keys in a body, the words of an error, a header, an empty 204. These tests pin them. They passed before
the logic moved into account.py, and they must keep passing.

Every class sets longMessage = False, so a failure prints its own message, which names the file and the fix.
"""

import hashlib
import json
import os
import secrets
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from oauth2_provider.models import (
    get_access_token_model,
    get_application_model,
    get_grant_model,
    get_refresh_token_model,
)
from rest_framework.test import APIClient

from drop_core.models import (
    APIKey,
    Artifact,
    EmailVerification,
    UsageCounter,
    get_or_create_workspace,
)
from drop_core.oauth import OAUTH_SCOPE_MCP_USE
from drop_core.plans import PLANS
from drop_core.services import upload_artifact
from drop_core.storage import LocalStorage

User = get_user_model()
FIX = (
    "the JSON answer changed: backend/drop_core/account.py (the logic) and backend/drop_core/views.py (how it is sent) "
    "must answer exactly what docs/API.md and the published clients read"
)


class ShortFailures:
    """Part of the convention that a failure prints the message of its test, which names the file and the fix, and
    not a diff of a whole body (``longMessage = False``). The values these tests compare are small, so this adds
    them after the message."""

    longMessage = False

    def assertEqual(self, first, second, msg=None):
        if msg is not None:
            msg = f"{msg}\n    got:      {first!r}\n    expected: {second!r}"
        super().assertEqual(first, second, msg)


def make_user(username, verified=True):
    user = User.objects.create_user(username=username, email=f"{username}@example.com")
    EmailVerification.objects.create(
        user=user,
        email=user.email,
        is_verified=verified,
        verified_at=timezone.now() if verified else None,
    )
    return user


def order_of(text):
    """The keys of a JSON object, or of the first object of a list, in the order the bytes give them."""
    data = json.loads(text, object_pairs_hook=lambda pairs: pairs)
    if data and isinstance(data[0], list) and data[0] and isinstance(data[0][0], tuple):
        data = data[0]
    return [key for key, _value in data]


class FailingDeleteStorage(LocalStorage):
    def delete(self, token):
        raise OSError("storage is down")


class JsonAnswerCase(ShortFailures, TestCase):
    longMessage = False

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-json-answers-")
        env = mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": self.tmpdir, "DROP_STORAGE": "local"})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.user = make_user("owner")
        self.workspace = get_or_create_workspace(self.user)
        self.key, self.raw_key = APIKey.create_key(self.workspace, "agent")
        self.session = APIClient()
        self.session.force_login(self.user)
        self.keyed = APIClient()
        self.keyed.credentials(HTTP_AUTHORIZATION=f"Bearer {self.raw_key}")

    def team(self):
        self.workspace.plan = "team"
        self.workspace.save()

    def exact(self, response, status, text, message=None):
        self.assertEqual(response.status_code, status, message or FIX)
        self.assertEqual(response.content.decode(), text, message or FIX)


class UsageAnswerTests(JsonAnswerCase):
    def test_usage_bytes_for_a_session_and_for_a_key(self):
        UsageCounter.objects.create(
            workspace=self.workspace, year_month=timezone.now().strftime("%Y-%m"), uploads=7
        )
        plan = PLANS["free"]
        expected = (
            '{"plan":"free","uploads_used":7,"uploads_cap":%d,"ttl_days":%d,"keys_used":1,"keys_cap":%d,'
            '"storage_used_bytes":0,"storage_cap_bytes":%d,"email":"owner@example.com","email_verified":true}'
        ) % (plan["uploads"], plan["ttl_days"], plan["keys"], plan["storage_gb"] * 1024**3)
        for client in (self.session, self.keyed):
            with self.subTest(client="key" if client is self.keyed else "session"):
                self.exact(client.get("/api/v1/usage/"), 200, expected)
                self.exact(client.get("/api/v1/usage"), 200, expected)

    def test_a_key_sees_its_workspace_owners_email_not_an_empty_one(self):
        other = make_user("other", verified=False)
        other_key, raw = APIKey.create_key(get_or_create_workspace(other), "theirs")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        body = client.get("/api/v1/usage/").json()
        self.assertEqual((body["email"], body["email_verified"]), ("other@example.com", False), FIX)

    def test_unauthenticated_is_a_401_that_advertises_bearer(self):
        response = APIClient().get("/api/v1/usage/")
        self.assertEqual(response.status_code, 401, FIX)
        self.assertEqual(response["WWW-Authenticate"], "Bearer", FIX)


class KeyAnswerTests(JsonAnswerCase):
    def test_list_fields_and_order(self):
        self.keyed.get("/api/v1/keys/")  # the key is used, so its last_used_at is set
        newer, _raw = APIKey.create_key(self.workspace, "newer")
        APIKey.objects.filter(pk=self.key.pk).update(is_active=False)
        response = self.session.get("/api/v1/keys/")
        rows = response.json()
        self.assertEqual([r["name"] for r in rows], ["newer", "agent"], FIX)
        self.assertEqual([r["is_active"] for r in rows], [True, False], FIX)
        self.assertEqual(
            order_of(response.content.decode()),
            ["id", "name", "key_prefix", "created_at", "last_used_at", "is_active"],
            FIX,
        )
        self.assertIsNone(rows[0]["last_used_at"], FIX)
        self.assertIsNotNone(rows[1]["last_used_at"], FIX)

    def test_a_key_lists_its_own_workspace_and_a_session_may_use_the_noslash_address(self):
        self.assertEqual([r["name"] for r in self.keyed.get("/api/v1/keys/").json()], ["agent"], FIX)
        self.assertEqual(self.session.get("/api/v1/keys").status_code, 200, FIX)

    def test_create_answers_the_raw_key_once_in_this_order(self):
        self.team()
        response = self.session.post("/api/v1/keys/", {"name": "  ci  "}, format="json")
        self.assertEqual(response.status_code, 201, FIX)
        self.assertEqual(order_of(response.content.decode()), ["id", "name", "key_prefix", "api_key"], FIX)
        body = response.json()
        self.assertEqual(body["name"], "ci", FIX)
        self.assertTrue(body["api_key"].startswith("vlf_"), FIX)
        self.assertEqual(body["key_prefix"], body["api_key"][:12], FIX)
        row = APIKey.objects.get(pk=body["id"])
        self.assertEqual(row.key_hash, hashlib.sha256(body["api_key"].encode()).hexdigest(), FIX)
        self.assertNotIn(body["api_key"], json.dumps(self.session.get("/api/v1/keys/").json()), FIX)

    def test_create_with_no_name_or_a_blank_one_is_untitled(self):
        self.team()
        for payload in ({}, {"name": None}, {"name": ""}, {"name": "   "}):
            with self.subTest(payload=payload):
                response = self.session.post("/api/v1/keys/", payload, format="json")
                self.assertEqual(response.status_code, 201, FIX)
                self.assertEqual(response.json()["name"], "Untitled key", FIX)

    def test_create_refusals_are_these_bytes(self):
        too_long = "n" * 256
        cases = [
            ({"name": 123}, 400, '{"error":"The name must be a string. Send name as text, or leave it out.","code":"invalid_request"}'),
            ({"name": too_long}, 400, '{"error":"The name is longer than 255 characters. Use a shorter name.","code":"invalid_request"}'),
            ({"name": "a\x00b"}, 400, '{"error":"The name contains control characters. Remove them and try again.","code":"invalid_request"}'),
            (["a"], 400, '{"error":"The request body must be a JSON object. Send the body as an object, such as one with a name field.","code":"invalid_request"}'),
        ]
        self.team()
        for payload, status, text in cases:
            with self.subTest(payload=payload):
                self.exact(self.session.post("/api/v1/keys/", payload, format="json"), status, text)
        self.assertEqual(APIKey.objects.filter(workspace=self.workspace).count(), 1, FIX)

    def test_the_cap_is_a_402_and_makes_no_key(self):
        response = self.session.post("/api/v1/keys/", {"name": "second"}, format="json")
        self.exact(
            response, 402, '{"error":"Your workspace has reached its plan\'s API key limit. Revoke a key or upgrade your plan.","code":"key_limit_reached"}'
        )
        self.assertEqual(APIKey.objects.filter(workspace=self.workspace).count(), 1, FIX)

    def test_an_unverified_account_is_told_to_verify_whatever_its_body_holds(self):
        stranger = make_user("stranger", verified=False)
        client = APIClient()
        client.force_login(stranger)
        text = (
            '{"error":"Verify your email address before creating API keys. Check your inbox for the '
            'verification link.","code":"email_verification_required"}'
        )
        self.exact(client.post("/api/v1/keys/", {"name": "x"}, format="json"), 403, text)
        self.exact(client.post("/api/v1/keys/", "{not json", content_type="application/json"), 403, text,
                   "backend/drop_core/views.py: KeyListCreateView.post asks for a verified email before it reads the body")
        self.exact(client.post("/api/v1/keys/", ["a"], format="json"), 403, text)
        self.assertFalse(APIKey.objects.filter(workspace__owner=stranger).exists(), FIX)

    def test_a_credential_cannot_mint_one(self):
        self.exact(
            self.keyed.post("/api/v1/keys/", {"name": "x"}, format="json"),
            403,
            '{"error":"API keys and connected apps cannot create API keys. Create keys from the dashboard.",'
            '"code":"session_required"}',
        )

    def test_malformed_json_from_a_verified_account_is_still_a_400(self):
        self.team()
        response = self.session.post("/api/v1/keys/", "{not json", content_type="application/json")
        self.assertEqual(response.status_code, 400, FIX)
        self.assertEqual(response.json()["code"], "invalid_request", FIX)

    def test_revoke_answers(self):
        other_key, _raw = APIKey.create_key(self.workspace, "sibling")
        # A key may revoke only itself.
        self.exact(
            self.keyed.delete(f"/api/v1/keys/{other_key.pk}/"),
            403,
            '{"error":"An API key can only revoke itself. Revoke other keys from the dashboard.",'
            '"code":"session_required"}',
        )
        # Someone else's key and a key that is not there are the same 404, with Django's words.
        stranger = APIKey.create_key(get_or_create_workspace(make_user("stranger")), "theirs")[0]
        for key_id in (stranger.pk, 999999):
            with self.subTest(key_id=key_id):
                self.exact(
                    self.session.delete(f"/api/v1/keys/{key_id}/"),
                    404,
                    '{"error":"No API key with that id exists in this workspace. Check the id.","code":"not_found"}',
                )
        # A session revokes any key of its workspace, and a second revoke is the same 204.
        for _ in range(2):
            response = self.session.delete(f"/api/v1/keys/{other_key.pk}/")
            self.assertEqual((response.status_code, response.content), (204, b""), FIX)
        other_key.refresh_from_db()
        self.assertFalse(other_key.is_active, FIX)
        # A key revokes itself.
        response = self.keyed.delete(f"/api/v1/keys/{self.key.pk}")
        self.assertEqual((response.status_code, response.content), (204, b""), FIX)
        self.assertEqual(self.keyed.get("/api/v1/keys/").status_code, 401, FIX)

    def test_oauth_tokens_cannot_touch_keys(self):
        user = self.user
        app = get_application_model().objects.create(
            name="Claude Code",
            client_type="public",
            authorization_grant_type="authorization-code",
            redirect_uris="http://127.0.0.1:9876/callback",
        )
        raw = secrets.token_urlsafe(32)
        get_access_token_model().objects.create(
            user=user, application=app, token=raw, expires=timezone.now() + timedelta(hours=1),
            scope=OAUTH_SCOPE_MCP_USE,
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        text = '{"error":"A connected app cannot manage API keys. Manage keys from the dashboard.","code":"session_required"}'
        self.exact(client.get("/api/v1/keys/"), 403, text)
        self.exact(client.delete(f"/api/v1/keys/{self.key.pk}/"), 403, text)


class ArtifactAnswerTests(JsonAnswerCase):
    def make_files(self, n):
        base = timezone.now() - timedelta(hours=1)
        expires = timezone.now() + timedelta(days=3)
        rows = []
        for i in range(n):
            row = Artifact.objects.create(
                workspace=self.workspace, filename=f"f{i}.txt", mime_type="text/plain", size_bytes=i, expires_at=expires
            )
            Artifact.objects.filter(pk=row.pk).update(created_at=base + timedelta(minutes=i))
            rows.append(row)
        return rows

    def test_list_fields_order_and_newest_first(self):
        self.make_files(3)
        response = self.session.get("/api/v1/artifacts/")
        self.assertEqual(
            order_of(response.content.decode()),
            ["id", "filename", "mime_type", "size_bytes", "url", "expires_at", "secret_flags", "created_at"],
            FIX,
        )
        self.assertEqual([r["filename"] for r in response.json()], ["f2.txt", "f1.txt", "f0.txt"], FIX)
        self.assertNotIn("X-Next-Cursor", response, FIX)
        self.assertNotIn("Link", response, FIX)
        self.assertTrue(response.json()[0]["url"].startswith("http://localhost:8000/a/"), FIX)

    def test_the_next_page_is_in_two_headers_and_the_body_stays_a_list(self):
        self.make_files(5)
        response = self.keyed.get("/api/v1/artifacts/?limit=2")
        cursor = response["X-Next-Cursor"]
        self.assertRegex(cursor, r"^\d+-[0-9a-f]{32}$", FIX)
        self.assertEqual(response["Link"], f'</api/v1/artifacts/?limit=2&cursor={cursor}>; rel="next"', FIX)
        self.assertEqual([r["filename"] for r in response.json()], ["f4.txt", "f3.txt"], FIX)
        again = self.keyed.get(f"/api/v1/artifacts?limit=2&cursor={cursor}")
        self.assertEqual([r["filename"] for r in again.json()], ["f2.txt", "f1.txt"], FIX)
        self.assertEqual(
            again["Link"], f'</api/v1/artifacts?limit=2&cursor={again["X-Next-Cursor"]}>; rel="next"', FIX
        )
        last = self.keyed.get(f"/api/v1/artifacts/?limit=2&cursor={again['X-Next-Cursor']}")
        self.assertEqual([r["filename"] for r in last.json()], ["f0.txt"], FIX)
        self.assertNotIn("X-Next-Cursor", last, FIX)
        self.assertNotIn("Link", last, FIX)

    def test_an_empty_cursor_is_the_first_page_and_an_empty_limit_is_a_400(self):
        self.make_files(3)
        response = self.session.get("/api/v1/artifacts/?cursor=")
        self.assertEqual((response.status_code, len(response.json())), (200, 3), FIX)
        self.assertEqual(self.session.get("/api/v1/artifacts/?limit=").status_code, 400, FIX)

    def test_bad_paging_is_this_400(self):
        text = '{"error":"The limit must be a whole number, and the cursor must be a value from the X-Next-Cursor header. Correct the one that is wrong, or leave both out.","code":"invalid_request"}'
        for query in ("limit=abc", "cursor=garbage", "cursor=123-nothex", "limit=2&cursor=-1"):
            with self.subTest(query=query):
                self.exact(self.session.get(f"/api/v1/artifacts/?{query}"), 400, text)

    def test_expired_and_held_files_are_listed(self):
        self.make_files(1)
        Artifact.objects.create(
            workspace=self.workspace, filename="old.txt", size_bytes=1, expires_at=timezone.now() - timedelta(days=1)
        )
        Artifact.objects.create(
            workspace=self.workspace, filename="held.txt", size_bytes=1,
            expires_at=timezone.now() + timedelta(days=1), safety_status="held",
        )
        names = {r["filename"] for r in self.session.get("/api/v1/artifacts/").json()}
        self.assertEqual(names, {"f0.txt", "old.txt", "held.txt"}, FIX)

    def test_revoke_answers(self):
        artifact = upload_artifact(self.workspace, self.key, "a.txt", b"hello", "text/plain")
        response = self.session.delete(f"/api/v1/artifacts/{artifact.id}/")
        self.assertEqual((response.status_code, response.content), (204, b""), FIX)
        self.assertFalse(Artifact.objects.filter(pk=artifact.pk).exists(), FIX)
        missing = '{"error":"No file with that id exists in this workspace. Check the id.","code":"not_found"}'
        self.exact(self.session.delete(f"/api/v1/artifacts/{artifact.id}/"), 404, missing)
        theirs = Artifact.objects.create(
            workspace=get_or_create_workspace(make_user("stranger")), filename="t.txt", size_bytes=1,
            expires_at=timezone.now() + timedelta(days=1),
        )
        self.exact(self.keyed.delete(f"/api/v1/artifacts/{theirs.id}"), 404, missing)
        self.assertTrue(Artifact.objects.filter(pk=theirs.pk).exists(), FIX)

    def test_a_failed_storage_delete_is_still_a_204_with_no_body(self):
        artifact = upload_artifact(self.workspace, self.key, "a.txt", b"hello", "text/plain")
        with mock.patch("drop_core.services.get_storage", return_value=FailingDeleteStorage()):
            with self.assertLogs("drop_core.services", "ERROR"):
                response = self.session.delete(f"/api/v1/artifacts/{artifact.id}/")
        self.assertEqual((response.status_code, response.content), (204, b""), FIX)
        row = Artifact.objects.get(pk=artifact.pk)
        self.assertTrue(row.is_expired(), "backend/drop_core/account.py: the link must be dead even when storage is down")


class GrantAnswerTests(JsonAnswerCase):
    def setUp(self):
        super().setUp()
        self.app = get_application_model().objects.create(
            name="Claude Code",
            client_type="public",
            authorization_grant_type="authorization-code",
            redirect_uris="http://127.0.0.1:9876/callback",
        )
        self.raw = secrets.token_urlsafe(32)
        token = get_access_token_model().objects.create(
            user=self.user, application=self.app, token=self.raw,
            expires=timezone.now() + timedelta(hours=1), scope=OAUTH_SCOPE_MCP_USE,
        )
        get_refresh_token_model().objects.create(
            user=self.user, application=self.app, token=secrets.token_urlsafe(32), access_token=token
        )

    def test_list_fields_and_order(self):
        response = self.session.get("/api/v1/oauth/grants/")
        self.assertEqual(
            order_of(response.content.decode()),
            ["id", "name", "client_id", "redirect_host", "last_authorized_at"],
            FIX,
        )
        self.assertEqual(response.json()[0]["name"], "Claude Code", FIX)
        self.assertEqual(response.json()[0]["redirect_host"], "127.0.0.1:9876", FIX)

    def test_only_a_session_may_ask(self):
        self.assertIn(self.keyed.get("/api/v1/oauth/grants/").status_code, (401, 403), FIX)
        self.assertEqual(APIClient().get("/api/v1/oauth/grants/").status_code, 403, FIX)

    def test_disconnect_answers(self):
        stranger = make_user("stranger")
        theirs = get_application_model().objects.create(
            name="Theirs", client_type="public", authorization_grant_type="authorization-code",
            redirect_uris="http://127.0.0.1:9876/callback",
        )
        get_access_token_model().objects.create(
            user=stranger, application=theirs, token=secrets.token_urlsafe(32),
            expires=timezone.now() + timedelta(hours=1), scope=OAUTH_SCOPE_MCP_USE,
        )
        missing = '{"error":"No connected app with that id exists for you. Check the id under Connected apps on the dashboard.","code":"not_found"}'
        self.exact(self.session.delete(f"/api/v1/oauth/grants/{theirs.pk}/"), 404, missing)
        self.exact(self.session.delete("/api/v1/oauth/grants/999999/"), 404, missing)
        response = self.session.delete(f"/api/v1/oauth/grants/{self.app.pk}/")
        self.assertEqual((response.status_code, response.content), (204, b""), FIX)
        self.exact(self.session.delete(f"/api/v1/oauth/grants/{self.app.pk}"), 404, missing)
        self.assertEqual(self.session.get("/api/v1/oauth/grants/").json(), [], FIX)
        self.assertTrue(get_application_model().objects.filter(pk=self.app.pk).exists(), FIX)

    def test_an_app_with_only_a_pending_code_disconnects(self):
        pending = get_application_model().objects.create(
            name="Half", client_type="public", authorization_grant_type="authorization-code",
            redirect_uris="http://127.0.0.1:9876/callback",
        )
        get_grant_model().objects.create(
            user=self.user, application=pending, code="pending-only",
            expires=timezone.now() + timedelta(minutes=5), redirect_uri="http://127.0.0.1:9876/callback",
            scope=OAUTH_SCOPE_MCP_USE, code_challenge="x" * 43, code_challenge_method="S256",
        )
        response = self.session.delete(f"/api/v1/oauth/grants/{pending.pk}/")
        self.assertEqual((response.status_code, response.content), (204, b""), FIX)
        self.assertFalse(get_grant_model().objects.filter(application=pending).exists(), FIX)
        self.assertEqual(self.session.delete(f"/api/v1/oauth/grants/{pending.pk}/").status_code, 404, FIX)
