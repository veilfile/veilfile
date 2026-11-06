import base64
import contextlib
import hashlib
import importlib
import json
import os
import re
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

import secrets
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.db.models import RestrictedError
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application, RefreshToken

from .key_caps import create_key_within_cap
from .oauth import OAUTH_SCOPE_MCP_USE, resolve_oauth_credential
from .services import mint_upload_url, upload_artifact
from .tests import _use_local_dir
from .tests_fix_device import _DeviceTestBase

from .models import (
    VISIBILITY_ONLY_ME,
    APIKey,
    Artifact,
    BlockedHash,
    DeviceCode,
    EmailVerification,
    Membership,
    PendingUpload,
    SafetyEvent,
    Workspace,
    administrator_of,
    current_member,
    get_or_create_workspace,
)

User = get_user_model()

backfill = importlib.import_module("drop_core.migrations.0013_backfill_members").backfill_members


def make_user(name, email=""):
    user = User.objects.create_user(username=name, email=email or f"{name}@example.com")
    EmailVerification.objects.create(
        user=user, email=user.email, is_verified=True, verified_at=timezone.now()
    )
    return user


def second_member(workspace, name, email=""):
    user = make_user(name, email)
    return Membership.objects.create(workspace=workspace, user=user, role=Membership.MEMBER)


class MembershipTests(TestCase):
    def test_a_new_workspace_gets_its_owner_as_administrator(self):
        owner = make_user("ada")
        workspace = get_or_create_workspace(owner)
        member = Membership.objects.get(workspace=workspace)
        self.assertEqual((member.user, member.role, member.removed_at), (owner, Membership.ADMINISTRATOR, None))

    def test_a_workspace_made_directly_gets_an_administrator_too(self):
        owner = make_user("ada")
        workspace = Workspace.objects.create(owner=owner, name="direct")
        self.assertEqual(administrator_of(workspace).user, owner)
        self.assertEqual(Membership.objects.filter(workspace=workspace).count(), 1)

    def test_a_workspace_has_one_current_administrator(self):
        workspace = get_or_create_workspace(make_user("ada"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(workspace=workspace, user=make_user("grace"), role=Membership.ADMINISTRATOR)

    def test_a_person_belongs_to_a_workspace_once(self):
        workspace = get_or_create_workspace(make_user("ada"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(workspace=workspace, user=workspace.owner)

    def test_administrator_of_heals_a_workspace_with_no_membership(self):
        workspace = get_or_create_workspace(make_user("ada"))
        Membership.objects.filter(workspace=workspace).delete()
        self.assertEqual(administrator_of(workspace).role, Membership.ADMINISTRATOR)

    def test_current_member_finds_the_person_and_ignores_removed_ones(self):
        workspace = get_or_create_workspace(make_user("ada"))
        grace = second_member(workspace, "grace")
        self.assertEqual(current_member(workspace, workspace.owner), administrator_of(workspace))
        self.assertEqual(current_member(workspace, grace.user), grace)
        Membership.objects.filter(pk=grace.pk).update(removed_at=timezone.now())
        self.assertIsNone(current_member(workspace, grace.user))
        self.assertIsNone(current_member(workspace, make_user("stranger")))
        self.assertIsNone(current_member(workspace, AnonymousUser()))


def legacy_rows(workspace):
    """One key, file, upload URL and safety event of the workspace, all naming no member."""
    key, _ = APIKey.create_key(workspace, "agent")
    expires = timezone.now() + timedelta(days=1)
    Artifact.objects.create(workspace=workspace, filename="a.txt", size_bytes=1, expires_at=expires)
    PendingUpload.objects.create(
        workspace=workspace, api_key=key, filename="a.txt", ttl_days=1, token="legacy-token", expires_at=expires
    )
    SafetyEvent.objects.create(workspace=workspace, api_key=key, signal="blocked_hash_match", action_taken="logged")
    for model in (APIKey, Artifact, PendingUpload, SafetyEvent):
        model.objects.filter(workspace=workspace).update(member=None)


class BackfillTests(TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": tempfile.mkdtemp(prefix="veilfile-test-")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rows_with_no_member_go_to_the_administrator(self):
        workspace = get_or_create_workspace(make_user("ada"))
        legacy_rows(workspace)
        backfill(apps, None)
        admin = administrator_of(workspace)
        for model in (APIKey, Artifact, PendingUpload, SafetyEvent):
            self.assertEqual(set(model.objects.values_list("member", flat=True)), {admin.pk}, model.__name__)

    def test_a_workspace_with_no_membership_gets_one(self):
        workspace = get_or_create_workspace(make_user("ada"))
        legacy_rows(workspace)
        Membership.objects.filter(workspace=workspace).delete()  # allowed: nothing points at it after legacy_rows
        backfill(apps, None)
        self.assertEqual(Membership.objects.get(workspace=workspace).role, Membership.ADMINISTRATOR)
        self.assertFalse(APIKey.objects.filter(member__isnull=True).exists())

    def test_the_backfill_can_run_twice(self):
        workspace = get_or_create_workspace(make_user("ada"))
        legacy_rows(workspace)
        backfill(apps, None)
        backfill(apps, None)
        self.assertEqual(Membership.objects.filter(workspace=workspace).count(), 1)

    def test_an_owner_of_two_workspaces_gets_an_administrator_in_each(self):
        ada = make_user("ada")
        first = get_or_create_workspace(ada)
        Workspace.objects.create(owner=ada, name="second")
        Membership.objects.all().delete()
        backfill(apps, None)
        self.assertEqual(Membership.objects.filter(user=ada, role=Membership.ADMINISTRATOR).count(), 2)
        self.assertEqual(get_or_create_workspace(ada), first)

    def test_every_row_is_filled_when_there_are_more_rows_than_one_batch(self):
        workspace = get_or_create_workspace(make_user("ada"))
        legacy_rows(workspace)
        expires = timezone.now() + timedelta(days=1)
        for number in range(6):
            Artifact.objects.create(workspace=workspace, filename=f"f{number}.txt", size_bytes=1, expires_at=expires)
            APIKey.create_key(workspace, f"key{number}")
        for model in (APIKey, Artifact):
            model.objects.filter(workspace=workspace).update(member=None)
        self.assertGreater(Artifact.objects.count(), 5)
        module = importlib.import_module("drop_core.migrations.0013_backfill_members")
        with mock.patch.object(module, "BATCH_SIZE", 2, create=True):
            backfill(apps, None)
        admin = administrator_of(workspace)
        for model in (APIKey, Artifact, PendingUpload, SafetyEvent):
            self.assertFalse(model.objects.filter(member__isnull=True).exists(), model.__name__)
            self.assertEqual(model.objects.filter(member=admin).count(), model.objects.count(), model.__name__)

    def test_the_backfill_ends_and_leaves_a_row_null_when_no_administrator_is_current(self):
        workspace = get_or_create_workspace(make_user("ada"))
        legacy_rows(workspace)
        other = get_or_create_workspace(make_user("grace"))
        APIKey.create_key(other, "other")
        APIKey.objects.filter(workspace=other).update(member=None)
        Membership.objects.filter(workspace=workspace).update(removed_at=timezone.now())
        module = importlib.import_module("drop_core.migrations.0013_backfill_members")
        with mock.patch.object(module, "BATCH_SIZE", 1, create=True):
            backfill(apps, None)
        for model in (APIKey, Artifact, PendingUpload, SafetyEvent):
            self.assertFalse(model.objects.filter(workspace=workspace, member__isnull=False).exists(), model.__name__)
        self.assertFalse(APIKey.objects.filter(workspace=other, member__isnull=True).exists())

    def test_the_migration_is_not_one_transaction(self):
        module = importlib.import_module("drop_core.migrations.0013_backfill_members")
        self.assertIs(module.Migration.atomic, False)

    def test_new_files_are_only_me(self):
        workspace = get_or_create_workspace(make_user("ada"))
        artifact = Artifact.objects.create(
            workspace=workspace, filename="a.txt", size_bytes=1, expires_at=timezone.now() + timedelta(days=1)
        )
        self.assertEqual(artifact.visibility, VISIBILITY_ONLY_ME)

    def test_deleting_a_user_still_deletes_their_account(self):
        ada = make_user("ada")
        workspace = get_or_create_workspace(ada)
        APIKey.create_key(workspace, "agent")
        Artifact.objects.create(
            workspace=workspace, member=administrator_of(workspace), filename="a.txt", size_bytes=1,
            expires_at=timezone.now() + timedelta(days=1),
        )
        ada.delete()
        for model in (Workspace, Membership, APIKey, Artifact):
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_a_membership_with_files_cannot_be_deleted(self):
        workspace = get_or_create_workspace(make_user("ada"))
        Artifact.objects.create(
            workspace=workspace, member=administrator_of(workspace), filename="a.txt", size_bytes=1,
            expires_at=timezone.now() + timedelta(days=1),
        )
        with self.assertRaises(RestrictedError):
            administrator_of(workspace).delete()

    def every_kind_of_row(self):
        """An owner's workspace with a key, file, upload URL and safety event of the administrator, and a key and
        file of a second member. Returns the owner, the second member's user and the workspace."""
        owner = make_user("ada")
        workspace = get_or_create_workspace(owner)
        admin = administrator_of(workspace)
        grace = second_member(workspace, "grace")
        expires = timezone.now() + timedelta(days=1)
        key, _ = APIKey.create_key(workspace, "agent")
        APIKey.objects.filter(pk=key.pk).update(member=admin)
        Artifact.objects.create(workspace=workspace, member=admin, filename="a.txt", size_bytes=1, expires_at=expires)
        PendingUpload.objects.create(
            workspace=workspace, member=admin, api_key=key, filename="a.txt", ttl_days=1, token="t1", expires_at=expires
        )
        SafetyEvent.objects.create(
            workspace=workspace, member=admin, api_key=key, signal="blocked_hash_match", action_taken="logged"
        )
        other_key, _ = APIKey.create_key(workspace, "other")
        APIKey.objects.filter(pk=other_key.pk).update(member=grace)
        Artifact.objects.create(workspace=workspace, member=grace, filename="b.txt", size_bytes=1, expires_at=expires)
        for model in (APIKey, Artifact, PendingUpload, SafetyEvent):
            self.assertFalse(model.objects.filter(member__isnull=True).exists(), model.__name__)
            self.assertIn(admin.pk, set(model.objects.values_list("member", flat=True)), model.__name__)
        for model in (APIKey, Artifact):
            self.assertIn(grace.pk, set(model.objects.values_list("member", flat=True)), model.__name__)
        return owner, grace.user, workspace

    def assertNoRowsOfAnyKind(self):
        for model in (Workspace, Membership, APIKey, Artifact, PendingUpload, SafetyEvent):
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_deleting_a_user_with_every_kind_of_row_still_deletes_their_account(self):
        owner, grace_user, _ = self.every_kind_of_row()
        owner.delete()
        self.assertNoRowsOfAnyKind()
        self.assertTrue(User.objects.filter(pk=grace_user.pk).exists())

    def test_deleting_a_workspace_with_every_kind_of_row_deletes_its_rows(self):
        owner, grace_user, workspace = self.every_kind_of_row()
        workspace.delete()
        self.assertNoRowsOfAnyKind()
        self.assertTrue(User.objects.filter(pk=owner.pk).exists())
        self.assertTrue(User.objects.filter(pk=grace_user.pk).exists())


def _make_app(name="Test app"):
    return Application.objects.create(
        name=name,
        client_type=Application.CLIENT_PUBLIC,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
        redirect_uris="http://127.0.0.1:9876/callback",
    )


def _issue_token(user, app, scope=OAUTH_SCOPE_MCP_USE, refresh=True):
    raw = secrets.token_urlsafe(32)
    token = AccessToken.objects.create(
        user=user,
        application=app,
        token=raw,
        expires=timezone.now() + timedelta(hours=1),
        scope=scope,
    )
    refresh_row = None
    if refresh:
        refresh_row = RefreshToken.objects.create(
            user=user,
            application=app,
            token=secrets.token_urlsafe(32),
            access_token=token,
        )
    return raw, token, refresh_row


class CredentialPersonTests(TestCase):
    def test_a_new_key_belongs_to_the_person_who_made_it(self):
        workspace = get_or_create_workspace(make_user("ada"))
        grace = second_member(workspace, "grace")
        key, _ = APIKey.create_key(workspace, "agent", member=grace)
        self.assertEqual((key.member, key.person), (grace, grace.user))
        self.assertEqual(APIKey.create_key(workspace, "other")[0].member, administrator_of(workspace))

    def test_a_key_with_no_member_is_refused(self):
        workspace = get_or_create_workspace(make_user("ada"))
        key, raw = APIKey.create_key(workspace, "agent")
        APIKey.objects.filter(pk=key.pk).update(member=None)
        self.assertTrue(workspace.owner.is_active)
        self.assertIsNone(APIKey.verify(raw))

    def test_a_suspended_owner_loses_their_own_keys(self):
        workspace = get_or_create_workspace(make_user("ada"))
        _, raw = APIKey.create_key(workspace, "agent")
        User.objects.filter(pk=workspace.owner.pk).update(is_active=False)
        self.assertIsNone(APIKey.verify(raw))

    def test_a_members_key_follows_that_member_not_the_owner(self):
        workspace = get_or_create_workspace(make_user("ada"))
        grace = second_member(workspace, "grace")
        _, raw = APIKey.create_key(workspace, "agent", member=grace)
        User.objects.filter(pk=workspace.owner.pk).update(is_active=False)
        self.assertIsNotNone(APIKey.verify(raw))
        User.objects.filter(pk=grace.user.pk).update(is_active=False)
        self.assertIsNone(APIKey.verify(raw))

    def test_a_removed_members_key_stops(self):
        workspace = get_or_create_workspace(make_user("ada"))
        grace = second_member(workspace, "grace")
        _, raw = APIKey.create_key(workspace, "agent", member=grace)
        Membership.objects.filter(pk=grace.pk).update(removed_at=timezone.now())
        self.assertIsNone(APIKey.verify(raw))

    def test_an_oauth_token_acts_as_the_person_who_approved_it(self):
        ada = make_user("ada")
        raw, _, _ = _issue_token(ada, _make_app())
        credential = resolve_oauth_credential(raw)
        self.assertEqual(
            (credential.acting_member, credential.person),
            (administrator_of(get_or_create_workspace(ada)), ada),
        )

    def test_an_oauth_token_of_a_removed_member_is_refused(self):
        ada = make_user("ada")
        get_or_create_workspace(ada)
        raw, _, _ = _issue_token(ada, _make_app())
        Membership.objects.filter(user=ada).update(removed_at=timezone.now())
        self.assertIsNone(resolve_oauth_credential(raw))
        self.assertTrue(Membership.objects.filter(user=ada, removed_at__isnull=False).exists())
        self.assertFalse(Membership.objects.filter(user=ada, removed_at__isnull=True).exists())


class DeviceCredentialPersonTests(_DeviceTestBase):
    def test_a_device_login_key_belongs_to_the_approver(self):
        grant = self._start()
        self._approve(grant)
        response = self._poll(grant)
        self.assertEqual(response.status_code, 200)
        key = APIKey.verify(response.json()["api_key"])
        self.assertEqual(key.member, current_member(self.workspace, self.user))

    def test_a_device_login_approved_by_a_removed_member_is_refused(self):
        grant = self._start()
        self._approve(grant)
        Membership.objects.filter(user=self.user).update(removed_at=timezone.now())
        response = self._poll(grant)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "access_denied")
        self.assertEqual(
            response.json()["error_description"],
            "The account that approved this login has since moved to or left a team. "
            "Run veilfile login again.",
        )
        self.assertEqual(self._row(grant).status, DeviceCode.STATUS_DENIED)
        self.assertEqual(APIKey.objects.count(), 0)


class UploaderTests(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        _use_local_dir(self, self.tmpdir)
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.ada = make_user("ada")
        self.workspace = get_or_create_workspace(self.ada)
        self.grace = second_member(self.workspace, "grace")

    def _post_file(self, **headers):
        return self.client.post(
            "/api/v1/artifacts/", {"file": SimpleUploadedFile("a.txt", b"hi")}, **headers
        )

    def _mcp(self, name, arguments, raw):
        response = self.client.post(
            "/mcp",
            data=json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw}",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertNotIn("error", data, data)
        self.assertFalse(data["result"].get("isError"), data)
        return json.loads(data["result"]["content"][0]["text"])

    def _put(self, token):
        return self.client.put(
            f"/api/v1/uploads/{token}", b"hi", content_type="application/octet-stream"
        )

    def test_a_rest_upload_records_the_keys_member(self):
        _, raw = APIKey.create_key(self.workspace, "agent", member=self.grace)
        response = self._post_file(HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(response.status_code, 201)
        artifact = Artifact.objects.get()
        self.assertEqual((artifact.member, artifact.visibility), (self.grace, VISIBILITY_ONLY_ME))

    def test_a_dashboard_upload_records_the_signed_in_person(self):
        self.client.force_login(self.ada)
        self.assertEqual(self._post_file().status_code, 201)
        self.assertEqual(Artifact.objects.get().member, administrator_of(self.workspace))

    def test_an_oauth_upload_records_the_token_person(self):
        raw, _, _ = _issue_token(self.ada, _make_app())
        self.assertEqual(self._post_file(HTTP_AUTHORIZATION=f"Bearer {raw}").status_code, 201)
        self.assertEqual(Artifact.objects.get().member, current_member(self.workspace, self.ada))

    def test_an_mcp_upload_records_the_keys_member(self):
        _, raw = APIKey.create_key(self.workspace, "agent", member=self.grace)
        self._mcp("upload_artifact", {
            "filename": "a.txt", "content_base64": base64.b64encode(b"hi").decode(),
        }, raw)
        self.assertEqual(Artifact.objects.get().member, self.grace)

    def test_an_upload_url_carries_its_minter_to_the_file(self):
        _, raw = APIKey.create_key(self.workspace, "agent", member=self.grace)
        result = self._mcp("create_upload_url", {"filename": "a.txt"}, raw)
        pending = PendingUpload.objects.get()
        self.assertEqual(pending.member, self.grace)
        self.assertEqual(self._put(result["upload_url"].rsplit("/", 1)[1]).status_code, 201)
        self.assertEqual(Artifact.objects.get().member, self.grace)

    def test_a_blocked_upload_records_its_member(self):
        BlockedHash.objects.create(sha256=hashlib.sha256(b"hi").hexdigest(), reason="test seed")
        _, raw = APIKey.create_key(self.workspace, "agent", member=self.grace)
        with mock.patch.dict(os.environ, {"VEILFILE_SAFETY_MODE": "log"}):
            response = self._post_file(HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(SafetyEvent.objects.get().member, self.grace)

    def test_an_upload_url_with_no_member_is_refused(self):
        key, _ = APIKey.create_key(self.workspace, "agent")
        pending = mint_upload_url(self.workspace, key, "a.txt")
        PendingUpload.objects.filter(pk=pending.pk).update(member=None)
        unknown = self._put("no-such-token")
        response = self._put(pending.token)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), unknown.json())
        self.assertFalse(Artifact.objects.exists())

    def test_an_upload_url_follows_its_members_grant_not_the_owners(self):
        app = _make_app()
        pending = mint_upload_url(
            self.workspace, None, "a.txt", oauth_application_id=app.pk, member=self.grace
        )
        _issue_token(self.ada, app)
        self.assertEqual(self._put(pending.token).status_code, 404)
        pending = mint_upload_url(
            self.workspace, None, "a.txt", oauth_application_id=app.pk, member=self.grace
        )
        _issue_token(self.grace.user, app)
        self.assertEqual(self._put(pending.token).status_code, 201)

    def test_a_removed_members_upload_url_stops(self):
        _, _raw = APIKey.create_key(self.workspace, "agent", member=self.grace)
        key = APIKey.objects.get()
        pending = mint_upload_url(self.workspace, key, "a.txt", member=self.grace)
        unknown = self._put("no-such-token")
        Membership.objects.filter(pk=self.grace.pk).update(removed_at=timezone.now())
        response = self._put(pending.token)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), unknown.json())


BACKEND = Path(__file__).resolve().parent.parent
ALLOWED = {}


class PersonReadsTests(TestCase):
    def test_usage_reports_the_keys_person(self):
        workspace = get_or_create_workspace(make_user("ada", email="ada@example.com"))
        grace = second_member(workspace, "grace", email="grace@example.com")
        _, raw = APIKey.create_key(workspace, "agent", member=grace)
        body = self.client.get("/api/v1/usage", HTTP_AUTHORIZATION=f"Bearer {raw}").json()
        self.assertEqual(body["email"], "grace@example.com")

    def test_an_oauth_logout_revokes_only_the_callers_grants(self):
        ada = make_user("ada")
        workspace = get_or_create_workspace(ada)
        grace = second_member(workspace, "grace")
        app = _make_app()
        _issue_token(ada, app)
        raw = _issue_token(grace.user, app)[0]
        response = self.client.post(
            "/api/v1/oauth/revoke-grants", HTTP_AUTHORIZATION=f"Bearer {raw}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AccessToken.objects.filter(user=grace.user).exists())
        self.assertTrue(AccessToken.objects.filter(user=ada).exists())


class OwnerReadsTests(SimpleTestCase):
    def test_only_the_fallbacks_read_the_workspace_owner(self):
        pattern = re.compile(r"workspace__owner|\bworkspace\.owner\b")
        found = {}
        for path in BACKEND.rglob("*.py"):
            rel = path.relative_to(BACKEND).as_posix()
            if "/migrations/" in rel or path.name.startswith("tests"):
                continue
            count = len(pattern.findall(path.read_text()))
            if count:
                found[rel] = count
        self.assertEqual(
            found,
            ALLOWED,
            "A credential or file check reads workspace.owner. Use .acting_member or .person "
            "(AGENTS.md, People inside an account), or, for a new NULL-member fallback, add it to ALLOWED.",
        )

    def test_no_code_treats_a_missing_member_specially(self):
        found = []
        for path in BACKEND.rglob("*.py"):
            rel = path.relative_to(BACKEND).as_posix()
            if "/migrations/" in rel or path.name.startswith("tests"):
                continue
            if "member__isnull" in path.read_text():
                found.append(rel)
        self.assertEqual(found, [])


class ResolveMemberBeforeLockTests(TransactionTestCase):
    """administrator_of creates a row, so it must never run while the workspace row is locked."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": tempfile.mkdtemp(prefix="veilfile-test-")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.workspace = get_or_create_workspace(make_user("ada"))
        self.calls = []

    def _recording(self, *modules):
        def wrapper(workspace):
            self.calls.append(connection.in_atomic_block)
            return administrator_of(workspace)

        stack = contextlib.ExitStack()
        for module in modules:
            stack.enter_context(mock.patch(f"drop_core.{module}.administrator_of", wrapper, create=True))
        return stack

    def test_upload_artifact_resolves_the_administrator_outside_a_transaction(self):
        Membership.objects.filter(workspace=self.workspace).delete()
        with self._recording("services"):
            upload_artifact(self.workspace, None, "a.txt", b"hello", "text/plain")
        self.assertTrue(self.calls)
        self.assertEqual(set(self.calls), {False})

    def test_create_key_within_cap_resolves_the_administrator_outside_a_transaction(self):
        Membership.objects.filter(workspace=self.workspace).delete()
        with self._recording("models", "key_caps"):
            create_key_within_cap(self.workspace, "agent")
        self.assertTrue(self.calls)
        self.assertEqual(set(self.calls), {False})
