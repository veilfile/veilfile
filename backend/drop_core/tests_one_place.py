import re
import shutil
import tempfile
from unittest import mock

from rest_framework.test import APIClient
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone

from . import account
from .key_caps import create_key_within_cap
from .models import (
    APIKey,
    Artifact,
    Membership,
    NoCurrentAdministrator,
    Workspace,
    administrator_of,
    current_member,
    current_membership,
    get_or_create_workspace,
    has_other_members,
)
from .oauth import resolve_oauth_credential
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

    def test_a_removed_persons_token_acts_on_their_own_account(self):
        self._remove_grace()
        raw, _, _ = _issue_token(self.grace.user, _make_app())
        credential = resolve_oauth_credential(raw)
        self.assertEqual(credential.workspace, self.old)

    def test_signup_paid_pick_reads_the_persons_place(self):
        Workspace.objects.filter(pk=self.team.pk).update(stripe_subscription_id="sub_T")
        self.client.force_login(self.grace.user)
        response = self.client.get("/accounts/signup/?plan=team")
        self.assertRedirects(response, "/app/?billing=already_subscribed", fetch_redirect_response=False)


class DeviceLookupTests(_DeviceTestBase):
    def test_device_approval_stores_the_team(self):
        team = get_or_create_workspace(self._user("ada"))
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
