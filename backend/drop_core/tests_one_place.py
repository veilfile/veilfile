import io
import re
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from rest_framework.test import APIClient
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone

from . import account
from .key_caps import KeyNotAllowed, create_key_within_cap
from .models import (
    APIKey,
    Artifact,
    MemberUsageCounter,
    Membership,
    NoCurrentAdministrator,
    PendingUpload,
    UsageCounter,
    Workspace,
    administrator_of,
    current_member,
    current_membership,
    get_or_create_workspace,
    has_other_members,
)
from .oauth import resolve_oauth_credential
from .plans import PLANS
from .services import mint_upload_url, upload_artifact
from .storage import LocalStorage
from .tests import _use_local_dir
from .tests_fix_device import _DeviceTestBase
from .tests_members import BACKEND, _issue_token, _make_app, make_user, second_member


class CurrentMembershipTests(TestCase):
    def test_one_person_lands_on_their_workspace(self):
        ada = make_user("ada")
        workspace = get_or_create_workspace(ada)
        self.assertEqual(current_membership(ada), administrator_of(workspace))

    def test_a_member_lands_on_the_team_and_not_their_old_workspace(self):
        team = get_or_create_workspace(make_user("ada"))
        grace = second_member(team, "grace")
        old = get_or_create_workspace(grace.user)
        Membership.objects.filter(workspace=old).update(removed_at=timezone.now())
        self.assertEqual(current_membership(grace.user), grace)
        self.assertEqual(current_membership(grace.user).workspace, team)

    def test_a_team_beats_an_own_workspace_when_both_rows_are_current(self):
        team = get_or_create_workspace(make_user("ada"))
        grace = second_member(team, "grace")
        get_or_create_workspace(grace.user)
        self.assertEqual(current_membership(grace.user), grace)

    def test_an_owner_of_two_workspaces_lands_on_the_oldest(self):
        ada = make_user("ada")
        first = get_or_create_workspace(ada)
        Workspace.objects.create(owner=ada, name="second")
        self.assertEqual(current_membership(ada).workspace, first)

    def test_a_person_with_no_workspace_gets_one(self):
        ada = make_user("ada")
        self.assertEqual(Workspace.objects.filter(owner=ada).count(), 0)
        self.assertEqual(current_membership(ada).workspace.owner_id, ada.pk)

    def test_reopening_a_row_stops_its_keys(self):
        ada = make_user("ada")
        workspace = get_or_create_workspace(ada)
        row = administrator_of(workspace)
        key, raw = create_key_within_cap(workspace, "old", member=row)
        Membership.objects.filter(pk=row.pk).update(removed_at=timezone.now())
        current_membership(ada)
        self.assertIsNone(APIKey.verify(raw))
        key.refresh_from_db()
        self.assertFalse(key.is_active)

    def test_reopening_a_row_stops_its_upload_urls(self):
        ada = make_user("ada")
        workspace = get_or_create_workspace(ada)
        row = administrator_of(workspace)
        pending = mint_upload_url(workspace, None, "a.txt", oauth_application_id=1, member=row)
        Membership.objects.filter(pk=row.pk).update(removed_at=timezone.now())
        current_membership(ada)
        pending.refresh_from_db()
        self.assertTrue(pending.used)

    def test_a_credential_lookup_does_not_reopen_a_row(self):
        ada = make_user("ada")
        workspace = get_or_create_workspace(ada)
        row = administrator_of(workspace)
        key, _ = create_key_within_cap(workspace, "old", member=row)
        Membership.objects.filter(pk=row.pk).update(removed_at=timezone.now())
        self.assertIsNone(current_membership(ada, reopen=False))
        self.assertIsNotNone(Membership.objects.get(pk=row.pk).removed_at)
        key.refresh_from_db()
        self.assertTrue(key.is_active)

    def test_a_credential_lookup_still_makes_a_first_workspace(self):
        ada = make_user("ada")
        row = current_membership(ada, reopen=False)
        self.assertIsNotNone(row)
        self.assertEqual(row.workspace.owner_id, ada.pk)

    def test_a_person_with_no_current_row_gets_their_own_workspace_back(self):
        ada = make_user("ada")
        workspace = get_or_create_workspace(ada)
        row = administrator_of(workspace)
        Membership.objects.filter(pk=row.pk).update(removed_at=timezone.now())
        again = current_membership(ada)
        self.assertEqual(again.pk, row.pk)
        self.assertIsNone(again.removed_at)
        self.assertIsNone(Membership.objects.get(pk=row.pk).removed_at)

    def test_reopening_a_workspace_does_not_revive_a_deactivated_key(self):
        ada = make_user("ada")
        workspace = get_or_create_workspace(ada)
        key, raw = APIKey.create_key(workspace, "old")
        APIKey.objects.filter(pk=key.pk).update(is_active=False)
        Membership.objects.filter(workspace=workspace).update(removed_at=timezone.now())
        current_membership(ada)
        self.assertIsNone(APIKey.verify(raw))

    def test_a_second_current_team_row_is_refused(self):
        grace = second_member(get_or_create_workspace(make_user("ada")), "grace")
        other = get_or_create_workspace(make_user("lin"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(workspace=other, user=grace.user)

    def test_a_person_can_hold_a_closed_row_and_a_current_one(self):
        grace = second_member(get_or_create_workspace(make_user("ada")), "grace")
        Membership.objects.filter(pk=grace.pk).update(removed_at=timezone.now())
        other = get_or_create_workspace(make_user("lin"))
        Membership.objects.create(workspace=other, user=grace.user)
        self.assertEqual(current_membership(grace.user).workspace, other)


class AdministratorGuardTests(TestCase):
    def test_administrator_of_refuses_a_closed_row(self):
        workspace = get_or_create_workspace(make_user("ada"))
        Membership.objects.filter(workspace=workspace).update(removed_at=timezone.now())
        with self.assertRaises(NoCurrentAdministrator):
            administrator_of(workspace)

    def test_current_member_answers_none_for_a_closed_owner_row(self):
        ada = make_user("ada")
        workspace = get_or_create_workspace(ada)
        Membership.objects.filter(workspace=workspace).update(removed_at=timezone.now())
        self.assertIsNone(current_member(workspace, ada))

    def test_a_writer_with_no_member_refuses_rather_than_writing_to_a_closed_row(self):
        workspace = get_or_create_workspace(make_user("ada"))
        Membership.objects.filter(workspace=workspace).update(removed_at=timezone.now())
        with self.assertRaises(NoCurrentAdministrator):
            create_key_within_cap(workspace, "agent")


class OtherMembersTests(TestCase):
    def test_it_counts_only_current_people(self):
        workspace = get_or_create_workspace(make_user("ada"))
        self.assertFalse(has_other_members(workspace))
        grace = second_member(workspace, "grace")
        self.assertTrue(has_other_members(workspace))
        Membership.objects.filter(pk=grace.pk).update(removed_at=timezone.now())
        self.assertFalse(has_other_members(workspace))


class LookupTests(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        _use_local_dir(self, self.tmpdir)
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.ada = make_user("ada")
        self.team = get_or_create_workspace(self.ada)
        Workspace.objects.filter(pk=self.team.pk).update(plan="team")
        self.grace = second_member(self.team, "grace")
        self.old = get_or_create_workspace(self.grace.user)
        Membership.objects.filter(workspace=self.old).update(removed_at=timezone.now())

    def _remove_grace(self):
        Membership.objects.filter(pk=self.grace.pk).update(removed_at=timezone.now())

    def test_a_members_session_reads_the_teams_usage(self):
        Workspace.objects.filter(pk=self.team.pk).update(plan="team")
        self.client.force_login(self.grace.user)
        response = self.client.get("/api/v1/usage")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["plan"], "team")

    def test_a_members_oauth_token_acts_on_the_team(self):
        raw, _, _ = _issue_token(self.grace.user, _make_app())
        credential = resolve_oauth_credential(raw)
        self.assertEqual(credential.workspace, self.team)
        self.assertEqual(credential.acting_member, self.grace)

    def test_a_key_made_in_a_members_session_lands_on_the_team(self):
        answer = account.create_key(self.grace.user, "agent")
        self.assertEqual(answer.status, 201)
        key = APIKey.objects.get(pk=answer.body["id"])
        self.assertEqual((key.workspace, key.member), (self.team, self.grace))

    def test_a_removed_persons_session_upload_lands_on_their_own_account(self):
        self._remove_grace()
        self.client.force_login(self.grace.user)
        response = self.client.post("/api/v1/artifacts/", {"file": SimpleUploadedFile("a.txt", b"hi")})
        self.assertEqual(response.status_code, 201)
        artifact = Artifact.objects.get()
        self.assertEqual(artifact.workspace, self.old)
        self.assertEqual(artifact.member.user, self.grace.user)
        self.assertFalse(Artifact.objects.filter(workspace=self.team).exists())

    def test_a_removed_persons_token_is_refused_until_they_sign_in(self):
        self._remove_grace()
        raw, _, _ = _issue_token(self.grace.user, _make_app())
        self.assertIsNone(resolve_oauth_credential(raw))
        current_membership(self.grace.user)
        self.assertEqual(resolve_oauth_credential(raw).workspace, self.old)

    def test_signup_paid_pick_reads_the_persons_place(self):
        Workspace.objects.filter(pk=self.team.pk).update(stripe_subscription_id="sub_T")
        self.client.force_login(self.grace.user)
        response = self.client.get("/accounts/signup/?plan=team")
        self.assertRedirects(response, "/app/?billing=already_subscribed", fetch_redirect_response=False)


class DeviceLookupTests(_DeviceTestBase):

    def test_reopening_a_row_stops_a_login_approved_before_it_closed(self):
        grant = self._start()
        self._approve(grant)
        Membership.objects.filter(user=self.user).update(removed_at=timezone.now())
        current_membership(self.user)
        response = self._poll(grant)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "access_denied")
        self.assertEqual(APIKey.objects.filter(member__user=self.user, device_issued=True).count(), 0)

    def test_device_approval_stores_the_team(self):
        team = get_or_create_workspace(self._user("ada"))
        Workspace.objects.filter(pk=team.pk).update(plan="team")
        grace = second_member(team, "grace")
        old = get_or_create_workspace(grace.user)
        Membership.objects.filter(workspace=old).update(removed_at=timezone.now())
        browser = APIClient()
        browser.force_login(grace.user)
        grant = self._start()
        response = self._approve(grant, browser=browser)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._row(grant).workspace, team)
        key = APIKey.verify(self._poll(grant).json()["api_key"])
        self.assertEqual((key.workspace, key.member), (team, grace))


class KeysOfAPersonTests(TestCase):
    def setUp(self):
        tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        _use_local_dir(self, tmpdir)
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        self.ada = make_user("ada")
        self.team = get_or_create_workspace(self.ada)
        Workspace.objects.filter(pk=self.team.pk).update(plan="team")
        self.team.refresh_from_db()
        self.admin = administrator_of(self.team)
        self.grace = second_member(self.team, "grace")
        self.lin = second_member(self.team, "lin")
        self.admins_key, self.admins_raw = create_key_within_cap(self.team, "ada-key", member=self.admin)
        self.graces_key, self.graces_raw = create_key_within_cap(self.team, "grace-key", member=self.grace)
        self.graces_other, _ = create_key_within_cap(self.team, "grace-other", member=self.grace)
        self.lins_key, _ = create_key_within_cap(self.team, "lin-key", member=self.lin)

    def _names(self, response):
        return {row["name"] for row in response.json()}

    def _fill_the_team(self):
        for i in range(PLANS["team"]["keys"] - APIKey.objects.filter(is_active=True).count()):
            create_key_within_cap(self.team, f"filler-{i}", member=self.admin)

    def test_a_members_key_lists_only_their_own_keys(self):
        response = APIClient().get("/api/v1/keys", HTTP_AUTHORIZATION=f"Bearer {self.graces_raw}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._names(response), {"grace-key", "grace-other"})
        self.assertIn(self.graces_key.key_prefix, response.content.decode())
        self.assertNotIn("ada-key", response.content.decode())
        self.assertNotIn("lin-key", response.content.decode())

    def test_an_administrators_key_lists_only_their_own_keys(self):
        response = APIClient().get("/api/v1/keys", HTTP_AUTHORIZATION=f"Bearer {self.admins_raw}")
        self.assertEqual(self._names(response), {"ada-key"})

    def test_the_administrators_dashboard_lists_every_key(self):
        self.client.force_login(self.ada)
        body = self.client.get("/app/keys/").content.decode()
        for name in ("ada-key", "grace-key", "grace-other", "lin-key"):
            self.assertIn(name, body)

    def test_a_members_dashboard_lists_only_their_own(self):
        self.client.force_login(self.grace.user)
        body = self.client.get("/app/keys/").content.decode()
        self.assertIn("grace-key", body)
        self.assertNotIn("ada-key", body)
        self.assertNotIn("lin-key", body)

    def test_a_session_cannot_revoke_a_teammates_key(self):
        client = APIClient()
        client.force_login(self.grace.user)
        response = client.delete(f"/api/v1/keys/{self.lins_key.pk}/")
        self.assertEqual(response.status_code, 404)
        self.lins_key.refresh_from_db()
        self.assertTrue(self.lins_key.is_active)

    def test_the_administrators_session_can_revoke_any_key(self):
        client = APIClient()
        client.force_login(self.ada)
        self.assertEqual(client.delete(f"/api/v1/keys/{self.lins_key.pk}/").status_code, 204)
        self.lins_key.refresh_from_db()
        self.assertFalse(self.lins_key.is_active)

    def test_a_key_still_cannot_revoke_a_sibling(self):
        response = APIClient().delete(
            f"/api/v1/keys/{self.graces_other.pk}/", HTTP_AUTHORIZATION=f"Bearer {self.graces_raw}"
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "session_required")
        self.graces_other.refresh_from_db()
        self.assertTrue(self.graces_other.is_active)

    def test_a_members_key_probing_a_teammates_key_gets_the_missing_answer(self):
        keyed = APIClient()
        auth = {"HTTP_AUTHORIZATION": f"Bearer {self.graces_raw}"}
        theirs = keyed.delete(f"/api/v1/keys/{self.lins_key.pk}/", **auth)
        missing = keyed.delete("/api/v1/keys/999999/", **auth)
        self.assertEqual((theirs.status_code, theirs.json()), (missing.status_code, missing.json()))
        self.assertEqual(theirs.status_code, 404)

    def test_the_dashboard_revoke_page_follows_the_same_rule(self):
        url = f"/app/keys/{self.lins_key.pk}/revoke/"
        self.client.force_login(self.grace.user)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.lins_key.refresh_from_db()
        self.assertTrue(self.lins_key.is_active)
        self.client.force_login(self.ada)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.lins_key.refresh_from_db()
        self.assertFalse(self.lins_key.is_active)

    def _refused_at_the_cap(self, user):
        self._fill_the_team()
        self.client.force_login(user)
        page = self.client.get("/app/keys/").content.decode()
        token = re.search(r'name="token" value="([^"]+)"', page).group(1)
        return self.client.post("/app/keys/", {"name": "more", "token": token})

    def test_a_member_is_not_shown_the_billing_link_at_the_cap(self):
        response = self._refused_at_the_cap(self.grace.user)
        body = response.content.decode()
        self.assertEqual(response.status_code, 402)
        cap = PLANS["team"]["keys"]
        self.assertIn(
            f"Your team&#x27;s plan allows {cap} active keys, and the team has reached that limit. "
            "Ask your administrator to make room.",
            body,
        )
        self.assertNotIn('<p class="form-links"><a class="label interactive" href="/app/billing/">', body)

    def test_the_administrator_still_gets_the_plan_sentence_at_the_cap(self):
        response = self._refused_at_the_cap(self.ada)
        body = response.content.decode()
        self.assertIn("Your Team plan allows", body)
        self.assertIn('<p class="form-links"><a class="label interactive" href="/app/billing/">', body)


class _SuspensionBase(_DeviceTestBase):
    """Ada's Team workspace with Grace in it, each with a key and a live file with bytes in storage."""

    def setUp(self):
        super().setUp()
        self.team = self.workspace
        Workspace.objects.filter(pk=self.team.pk).update(plan="team")
        self.team.refresh_from_db()
        self.ada = self.user
        self.admin = administrator_of(self.team)
        self.grace = second_member(self.team, "grace")
        self.admins_key, self.admins_raw = create_key_within_cap(self.team, "ada-key", member=self.admin)
        self.graces_key, self.graces_raw = create_key_within_cap(self.team, "grace-key", member=self.grace)
        self.admins_file = self._file(self.admin, "ada.txt")
        self.graces_file = self._file(self.grace, "grace.txt")

    def _file(self, member, name, days=7):
        artifact = Artifact.objects.create(
            workspace=member.workspace, member=member, filename=name, size_bytes=5,
            expires_at=timezone.now() + timedelta(days=days),
        )
        LocalStorage().save(artifact.token, io.BytesIO(b"bytes"), "application/octet-stream")
        self.addCleanup(LocalStorage().delete, artifact.token)
        return artifact

    def _suspend(self):
        Workspace.objects.filter(pk=self.team.pk).update(suspended_at=timezone.now())
        self.team.refresh_from_db()

    def _lift(self):
        Workspace.objects.filter(pk=self.team.pk).update(suspended_at=None)
        self.team.refresh_from_db()

    def _link(self, artifact):
        return self.client.get(f"/a/{artifact.token}")


class SuspensionTests(_SuspensionBase):
    def test_a_suspended_teams_keys_are_refused(self):
        self._suspend()
        self.assertIsNone(APIKey.verify(self.admins_raw))
        self.assertIsNone(APIKey.verify(self.graces_raw))

    def test_a_suspended_teams_oauth_tokens_are_refused(self):
        raw, _, _ = _issue_token(self.grace.user, _make_app())
        self.assertIsNotNone(resolve_oauth_credential(raw))
        self._suspend()
        self.assertIsNone(resolve_oauth_credential(raw))

    def test_a_suspended_teams_upload_urls_are_dead(self):
        pending = PendingUpload.objects.create(
            workspace=self.team, api_key=self.graces_key, member=self.grace, filename="a.txt", ttl_days=1,
            token="suspended-url", expires_at=timezone.now() + timedelta(minutes=15),
        )
        self._suspend()
        response = self.client.put("/api/v1/uploads/suspended-url", b"hi", content_type="application/octet-stream")
        self.assertEqual(response.status_code, 404)
        pending.refresh_from_db()
        self.assertTrue(pending.used)
        self.assertFalse(Artifact.objects.filter(filename="a.txt").exists())

    def test_a_suspended_teams_device_login_is_refused(self):
        grant = self._start()
        browser = APIClient()
        browser.force_login(self.grace.user)
        self.assertEqual(self._approve(grant, browser=browser).status_code, 200)
        self._suspend()
        response = self._poll(grant)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "access_denied")
        self.assertEqual(
            response.json()["error_description"],
            "This account is suspended, so no key was made. Contact support@veilfile.com.",
        )
        self.assertEqual(APIKey.objects.filter(member=self.grace, is_active=True).count(), 1)

    def test_a_session_upload_is_refused_with_its_code(self):
        self._suspend()
        client = APIClient()
        client.force_login(self.grace.user)
        response = client.post("/api/v1/artifacts/", {"file": SimpleUploadedFile("a.txt", b"hi")})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "workspace_suspended")
        self.assertEqual(
            response.json()["error"],
            "This account is suspended, so nothing was stored. Contact support@veilfile.com.",
        )
        self.assertFalse(Artifact.objects.filter(filename="a.txt").exists())
        self.assertFalse(UsageCounter.objects.filter(workspace=self.team, uploads__gt=0).exists())
        self.assertFalse(MemberUsageCounter.objects.filter(member=self.grace, uploads__gt=0).exists())

    def test_a_session_key_is_refused_with_its_code(self):
        self._suspend()
        client = APIClient()
        client.force_login(self.grace.user)
        response = client.post("/api/v1/keys", {"name": "new"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "workspace_suspended")
        self.assertFalse(APIKey.objects.filter(name="new").exists())

    def test_people_can_still_sign_in(self):
        self._suspend()
        self.client.force_login(self.grace.user)
        self.assertEqual(self.client.get("/app/").status_code, 200)

    def test_a_suspended_teams_links_answer_like_missing_ones(self):
        missing = self.client.get("/a/token-that-never-existed")
        self._suspend()
        for artifact in (self.admins_file, self.graces_file):
            with self.subTest(file=artifact.filename):
                response = self._link(artifact)
                self.assertEqual(response.status_code, missing.status_code)
                self.assertEqual(response.content, missing.content)
                self.assertEqual(dict(response.headers), dict(missing.headers))
                self.assertTrue(LocalStorage().exists(artifact.token))

    def test_lifting_a_suspension_brings_back_the_links(self):
        self._suspend()
        self.assertEqual(self._link(self.graces_file).status_code, 404)
        Artifact.objects.filter(pk=self.admins_file.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self._lift()
        response = self._link(self.graces_file)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"bytes")
        self.assertEqual(self._link(self.admins_file).status_code, 404)

    def test_a_left_behind_file_stops_with_the_team_it_lives_in(self):
        own = get_or_create_workspace(self.grace.user)
        mine = self._file(administrator_of(own), "mine.txt")
        Membership.objects.filter(pk=self.grace.pk).update(removed_at=timezone.now())
        self._suspend()
        self.assertEqual(self._link(self.graces_file).status_code, 404)
        self.assertEqual(self._link(mine).status_code, 200)

    def test_suspending_a_person_leaves_their_links_serving(self):
        type(self.ada).objects.filter(pk=self.grace.user_id).update(is_active=False)
        self.assertEqual(self._link(self.graces_file).status_code, 200)

    def test_the_files_page_shows_a_suspended_teams_files_as_held(self):
        self._suspend()
        self.client.force_login(self.ada)
        body = self.client.get("/app/files/").content.decode()
        self.assertIn("ada.txt", body)
        self.assertIn("Held", body)
        self.assertNotIn(self.admins_file.token, body)
        self.assertNotIn(f"/app/files/{self.admins_file.id}/revoke/", body)

    def test_a_suspended_teams_files_cannot_be_revoked(self):
        self._suspend()
        client = APIClient()
        client.force_login(self.ada)
        response = client.delete(f"/api/v1/artifacts/{self.admins_file.id}/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "workspace_suspended")
        self.assertEqual(
            response.json()["error"],
            "This account is suspended, so the file was not revoked. Contact support@veilfile.com.",
        )
        self.client.force_login(self.ada)
        page = self.client.post(f"/app/files/{self.admins_file.id}/revoke/")
        self.assertRedirects(page, "/app/files/", fetch_redirect_response=False)
        self.assertTrue(Artifact.objects.filter(pk=self.admins_file.pk).exists())
        self.assertTrue(LocalStorage().exists(self.admins_file.token))

    def test_the_service_refuses_a_suspended_teams_file(self):
        from . import services

        self._suspend()
        before = Artifact.objects.get(pk=self.admins_file.pk).expires_at
        self.assertIs(services.revoke_artifact(self.team, self.admins_file.id), False)
        artifact = Artifact.objects.get(pk=self.admins_file.pk)
        self.assertEqual(artifact.expires_at, before)
        self.assertTrue(LocalStorage().exists(self.admins_file.token))

    def test_a_file_left_behind_in_a_suspended_team_cannot_be_revoked_from_elsewhere(self):
        own = get_or_create_workspace(self.grace.user)
        _, own_raw = create_key_within_cap(own, "own", member=administrator_of(own))
        Membership.objects.filter(pk=self.grace.pk).update(removed_at=timezone.now())
        self._suspend()
        auth = {"HTTP_AUTHORIZATION": f"Bearer {own_raw}"}
        response = APIClient().delete(f"/api/v1/artifacts/{self.graces_file.id}/", **auth)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "workspace_suspended")
        rpc = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "revoke_artifact", "arguments": {"id": str(self.graces_file.id)}},
        }
        answer = APIClient().post("/mcp", rpc, format="json", **auth)
        self.assertEqual(answer.status_code, 200)
        import json

        self.assertEqual(json.loads(answer.json()["result"]["content"][0]["text"]), {"revoked": False})
        self.assertTrue(Artifact.objects.filter(pk=self.graces_file.pk).exists())
        self.assertTrue(LocalStorage().exists(self.graces_file.token))

    def test_a_view_only_staff_user_cannot_suspend_a_team(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        staff = get_user_model().objects.create_user("viewer", "viewer@example.com", "pw12345678", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename="view_workspace"))
        self.client.force_login(staff)
        self.client.post(
            "/admin/drop_core/workspace/",
            {"action": "suspend_workspaces", "_selected_action": [str(self.team.pk)]},
        )
        self.team.refresh_from_db()
        self.assertIsNone(self.team.suspended_at)

    def test_each_suspension_is_recorded_in_the_admin_history(self):
        from django.contrib.admin.models import LogEntry
        from django.contrib.auth import get_user_model

        staff = get_user_model().objects.create_superuser("staff", "staff@example.com", "pw12345678")
        self.client.force_login(staff)
        for action in ("suspend_workspaces", "lift_suspensions"):
            self.client.post(
                "/admin/drop_core/workspace/", {"action": action, "_selected_action": [str(self.team.pk)]}
            )
        entries = LogEntry.objects.filter(object_id=str(self.team.pk)).order_by("pk")
        self.assertEqual([e.change_message for e in entries], ["Suspended.", "Lifted the suspension."])

    def test_suspending_one_team_touches_no_other(self):
        other = get_or_create_workspace(make_user("lin"))
        _, raw = APIKey.create_key(other, "lin-key")
        self._suspend()
        self.assertIsNotNone(APIKey.verify(raw))

    def test_the_admin_action_writes_only_the_suspension(self):
        from django.contrib.auth import get_user_model

        staff = get_user_model().objects.create_superuser("staff", "staff@example.com", "pw12345678")
        self.client.force_login(staff)
        self.assertEqual(self.client.get("/admin/drop_core/workspace/").status_code, 200)
        Workspace.objects.filter(pk=self.team.pk).update(plan="scale")
        response = self.client.post(
            "/admin/drop_core/workspace/",
            {"action": "suspend_workspaces", "_selected_action": [str(self.team.pk)]},
        )
        self.assertEqual(response.status_code, 302)
        self.team.refresh_from_db()
        self.assertEqual(self.team.plan, "scale")
        self.assertIsNotNone(self.team.suspended_at)
        self.client.post(
            "/admin/drop_core/workspace/",
            {"action": "lift_suspensions", "_selected_action": [str(self.team.pk)]},
        )
        self.team.refresh_from_db()
        self.assertIsNone(self.team.suspended_at)
        self.assertEqual(self.team.plan, "scale")

    def test_lifting_a_suspension_restores_access(self):
        self._suspend()
        self._lift()
        self.assertIsNotNone(APIKey.verify(self.graces_raw))

    def test_suspending_a_person_still_touches_only_that_person(self):
        type(self.ada).objects.filter(pk=self.ada.pk).update(is_active=False)
        self.assertIsNone(APIKey.verify(self.admins_raw))
        self.assertIsNotNone(APIKey.verify(self.graces_raw))


class InFlightTests(_SuspensionBase):
    def _counts(self):
        """What the counters count, ignoring empty rows an early check may have made."""
        return (
            sorted(UsageCounter.objects.filter(uploads__gt=0).values_list("workspace_id", "uploads")),
            sorted(MemberUsageCounter.objects.filter(uploads__gt=0).values_list("member_id", "uploads")),
        )

    def _stored(self):
        return sorted(path.name for path in Path(self.tmpdir).rglob("*") if path.is_file())

    def test_an_upload_is_refused_when_the_member_was_removed_after_it_began(self):
        stale = current_member(self.team, self.grace.user)
        before = self._counts()
        files_before = self._stored()
        Membership.objects.filter(pk=stale.pk).update(removed_at=timezone.now())
        with self.assertRaisesMessage(ValueError, "member_removed"):
            upload_artifact(self.team, None, "a.txt", b"hi", "text/plain", member=stale)
        self.assertFalse(Artifact.objects.filter(filename="a.txt").exists())
        self.assertEqual(self._counts(), before)
        self.assertEqual(self._stored(), files_before)

    def test_an_upload_is_refused_when_the_team_was_suspended_after_it_began(self):
        stale = current_member(self.team, self.grace.user)
        before = self._counts()
        self._suspend()
        with self.assertRaisesMessage(ValueError, "workspace_suspended"):
            upload_artifact(self.team, None, "a.txt", b"hi", "text/plain", member=stale)
        self.assertFalse(Artifact.objects.filter(filename="a.txt").exists())
        self.assertEqual(self._counts(), before)

    def test_a_key_mint_is_refused_when_the_member_was_removed_after_it_began(self):
        stale = current_member(self.team, self.grace.user)
        Membership.objects.filter(pk=stale.pk).update(removed_at=timezone.now())
        with self.assertRaises(KeyNotAllowed) as caught:
            create_key_within_cap(self.team, "late", member=stale)
        self.assertEqual(caught.exception.code, "member_removed")
        self.assertFalse(APIKey.objects.filter(name="late").exists())

    def test_a_key_mint_is_refused_when_the_team_was_suspended_after_it_began(self):
        stale = current_member(self.team, self.grace.user)
        self._suspend()
        with self.assertRaises(KeyNotAllowed) as caught:
            create_key_within_cap(self.team, "late", member=stale)
        self.assertEqual(caught.exception.code, "workspace_suspended")


class LookupOutsideTransactionTests(TransactionTestCase):
    def test_the_lookup_runs_before_any_transaction(self):
        tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        _use_local_dir(self, tmpdir)
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        user = make_user("ada")
        seen = []

        def wrapped(person):
            seen.append(connection.in_atomic_block)
            return current_membership(person)

        with mock.patch("drop_core.views.current_membership", wrapped), mock.patch(
            "drop_core.account.current_membership", wrapped
        ):
            self.client.force_login(user)
            self.client.post("/api/v1/artifacts/", {"file": SimpleUploadedFile("a.txt", b"hi")})
            account.create_key(user, "agent")
        self.assertTrue(seen)
        self.assertEqual(set(seen), {False})


class OnlySignupAndTheLookupTests(SimpleTestCase):
    ALLOWED_CALLS = {"drop_core/models.py": 2, "drop_core/views.py": 1}

    def test_only_signup_and_the_lookup_call_get_or_create_workspace(self):
        pattern = re.compile(r"\bget_or_create_workspace\(")
        found = {}
        for path in BACKEND.rglob("*.py"):
            rel = path.relative_to(BACKEND).as_posix()
            if "/migrations/" in rel or path.name.startswith("tests"):
                continue
            count = len(pattern.findall(path.read_text()))
            if count:
                found[rel] = count
        self.assertEqual(found, self.ALLOWED_CALLS, "Use current_membership(user): AGENTS.md, People inside an account.")
