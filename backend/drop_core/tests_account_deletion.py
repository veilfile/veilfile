"""Staff deletion of an account (team seats, ways out): what is refused, what goes, what stops first, and the admin's own delete.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it). The service runs outside any transaction,
so these are TransactionTestCases.
"""
import io
import os
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from . import account_deletion
from .account_deletion import DeletionRefused, delete_account, deletion_summary, why_not_delete
from .key_caps import create_key_within_cap
from .models import (
    APIKey,
    Artifact,
    DeviceCode,
    Invitation,
    Membership,
    SafetyEvent,
    Workspace,
    get_or_create_workspace,
)
from .storage import LocalStorage
from .teams import accept_administrator_offer, offer_administrator
from .tests_members import make_user, second_member
from .tests_teams import team_of

User = get_user_model()


class Base(TransactionTestCase):
    longMessage = False

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        patcher = mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": self.tmpdir})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.team, self.admin = team_of("ada")
        self.grace = second_member(self.team, "grace")
        self.key, _ = create_key_within_cap(self.team, "grace-key", member=self.grace)

    def file(self, member, name="plans.txt", held=False, days=7):
        artifact = Artifact.objects.create(
            workspace=member.workspace, member=member, filename=name, size_bytes=5, api_key=None,
            expires_at=timezone.now() + timedelta(days=days), safety_status="held" if held else "clear",
        )
        LocalStorage().save(artifact.token, io.BytesIO(b"bytes"), "application/octet-stream")
        return artifact

    def stored(self, artifact):
        return os.path.exists(os.path.join(self.tmpdir, artifact.token))

    def gone(self, user):
        return not User.objects.filter(pk=user.pk).exists()


@override_settings(TEAM_SEATS=True)
class DeleteAccountTests(Base):
    def test_a_member_with_keys_and_files_is_deleted_with_them(self):
        live = self.file(self.grace, "live.txt")
        other = self.file(self.admin, "admins.txt")
        delete_account(self.grace.user)
        self.assertTrue(self.gone(self.grace.user))
        self.assertFalse(Artifact.objects.filter(pk=live.pk).exists())
        self.assertFalse(self.stored(live))
        self.assertFalse(APIKey.objects.filter(pk=self.key.pk).exists())
        self.assertTrue(Artifact.objects.filter(pk=other.pk).exists())
        self.assertTrue(self.stored(other))
        self.assertTrue(Workspace.objects.filter(pk=self.team.pk).exists())

    def test_a_member_with_expired_unswept_files_is_deleted(self):
        old = self.file(self.grace, "old.txt", days=-2)
        delete_account(self.grace.user)
        self.assertTrue(self.gone(self.grace.user))
        self.assertFalse(Artifact.objects.filter(pk=old.pk).exists())
        self.assertFalse(self.stored(old))

    def test_a_held_file_of_theirs_refuses_and_changes_nothing(self):
        held = self.file(self.grace, "held.txt", held=True)
        live = self.file(self.grace, "live.txt")
        self.assertEqual(why_not_delete(self.grace.user), "has_held_files")
        with self.assertRaises(DeletionRefused) as caught:
            delete_account(self.grace.user)
        self.assertEqual(caught.exception.code, "has_held_files")
        self.assertFalse(self.gone(self.grace.user))
        self.assertTrue(Artifact.objects.filter(pk__in=[held.pk, live.pk]).count() == 2)
        self.key.refresh_from_db()
        self.assertTrue(self.key.is_active)
        self.assertIsNone(Membership.objects.get(pk=self.grace.pk).removed_at)

    def test_a_held_file_of_a_removed_person_in_their_workspace_refuses(self):
        Membership.objects.filter(pk=self.grace.pk).update(removed_at=timezone.now())
        held = self.file(self.grace, "held.txt", held=True)
        Membership.objects.filter(pk=self.grace.pk).update(removed_at=timezone.now())
        self.assertEqual(why_not_delete(self.admin.user), "has_held_files")
        with self.assertRaises(DeletionRefused):
            delete_account(self.admin.user)
        self.assertTrue(Artifact.objects.filter(pk=held.pk).exists())
        self.assertFalse(self.gone(self.admin.user))

    def test_an_administrator_of_people_is_refused_and_changes_nothing(self):
        mine = self.file(self.admin, "mine.txt")
        self.assertEqual(why_not_delete(self.admin.user), "administers_people")
        with self.assertRaises(DeletionRefused) as caught:
            delete_account(self.admin.user)
        self.assertEqual(caught.exception.code, "administers_people")
        self.assertFalse(self.gone(self.admin.user))
        self.assertTrue(Artifact.objects.filter(pk=mine.pk).exists())
        self.assertTrue(self.stored(mine))

    def test_a_paying_account_is_refused(self):
        alone = get_or_create_workspace(make_user("lone"))
        Workspace.objects.filter(pk=alone.pk).update(plan=Workspace.PLAN_TEAM, stripe_subscription_id="sub_123")
        self.assertEqual(why_not_delete(alone.owner), "pays_through_stripe")
        with self.assertRaises(DeletionRefused) as caught:
            delete_account(alone.owner)
        self.assertEqual(caught.exception.code, "pays_through_stripe")
        self.assertFalse(self.gone(alone.owner))

    def test_an_administrator_alone_is_deleted_with_the_workspace(self):
        alone = get_or_create_workspace(make_user("lone"))
        row = Membership.objects.get(workspace=alone)
        mine = self.file(row, "mine.txt")
        APIKey.create_key(alone, "k", member=row)
        delete_account(alone.owner)
        self.assertFalse(Workspace.objects.filter(pk=alone.pk).exists())
        self.assertFalse(Artifact.objects.filter(pk=mine.pk).exists())
        self.assertFalse(self.stored(mine))
        self.assertFalse(APIKey.objects.filter(workspace_id=alone.pk).exists())

    def test_after_a_transfer_the_former_administrator_can_be_deleted_and_the_team_stays(self):
        offer_administrator(self.admin, self.grace.pk)
        accept_administrator_offer(Membership.objects.get(pk=self.grace.pk))
        former = self.admin.user
        theirs = self.file(Membership.objects.get(pk=self.admin.pk), "theirs.txt")
        self.assertIsNone(why_not_delete(former))
        delete_account(former)
        self.assertTrue(self.gone(former))
        self.assertTrue(Workspace.objects.filter(pk=self.team.pk).exists())
        self.assertFalse(Artifact.objects.filter(pk=theirs.pk).exists())
        self.assertFalse(self.stored(theirs))
        self.assertEqual(Workspace.objects.get(pk=self.team.pk).owner_id, self.grace.user_id)

    def test_a_removed_persons_files_left_on_the_team_go_with_their_account(self):
        left = self.file(self.grace, "left.txt")
        Membership.objects.filter(pk=self.grace.pk).update(removed_at=timezone.now())
        delete_account(self.grace.user)
        self.assertFalse(Artifact.objects.filter(pk=left.pk).exists())
        self.assertFalse(self.stored(left))
        self.assertTrue(self.gone(self.grace.user))

    def test_the_invitations_they_sent_are_revoked(self):
        offer_administrator(self.admin, self.grace.pk)
        accept_administrator_offer(Membership.objects.get(pk=self.grace.pk))
        sent = Invitation.objects.create(
            workspace=self.team, email="new@example.com", token_hash="h" * 64, invited_by=self.admin.user,
            expires_at=timezone.now() + timedelta(days=3),
        )
        delete_account(self.admin.user)
        sent.refresh_from_db()
        self.assertIsNotNone(sent.revoked_at)
        self.assertIsNone(sent.invited_by_id)

    def test_a_safety_event_keeps_its_row_and_loses_the_person(self):
        event = SafetyEvent.objects.create(
            workspace=self.team, member=self.grace, signal="blocked_hash_match", action_taken="blocked"
        )
        delete_account(self.grace.user)
        event.refresh_from_db()
        self.assertIsNone(event.member_id)

    def test_credentials_stop_before_any_row_is_deleted(self):
        self.file(self.grace, "live.txt")
        login = DeviceCode.objects.create(
            user_code="UG1", device_code_hash="d" * 64, status=DeviceCode.STATUS_APPROVED, workspace=self.team,
            approved_by=self.grace.user, expires_at=timezone.now() + timedelta(minutes=10),
        )
        with mock.patch("drop_core.file_access.delete_files_of", side_effect=RuntimeError("storage down")):
            with self.assertRaises(RuntimeError):
                delete_account(self.grace.user)
        self.assertFalse(self.gone(self.grace.user))
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)
        self.assertIsNotNone(Membership.objects.get(pk=self.grace.pk).removed_at)
        login.refresh_from_db()
        self.assertEqual(login.status, DeviceCode.STATUS_DENIED)

    def test_delete_account_refuses_to_run_inside_a_transaction(self):
        from django.db import transaction

        with transaction.atomic():
            with self.assertRaises(AssertionError):
                delete_account(self.grace.user)
        self.assertFalse(self.gone(self.grace.user))

    def test_the_summary_counts_and_names_nothing(self):
        self.file(self.grace, "secret-name.txt")
        self.file(self.grace, "another.txt")
        summary = deletion_summary(self.grace.user)
        self.assertEqual(summary, {"teams": 1, "files": 2, "keys": 1})

    def test_the_files_go_in_batches(self):
        for n in range(5):
            self.file(self.grace, f"f{n}.txt")
        from . import file_access

        self.assertEqual(file_access.delete_files_of(Membership.objects.get(pk=self.grace.pk), batch=2), 5)
        self.assertFalse(Artifact.objects.filter(member=self.grace).exists())


@override_settings(TEAM_SEATS=True)
class AdminDeleteTests(Base):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_superuser("root", "root@example.com", "pw12345678")
        self.client = Client()
        self.client.force_login(self.staff)
        self.url = f"/admin/auth/user/{self.grace.user_id}/delete/"

    def test_the_admin_deletes_a_member_with_keys_and_files(self):
        live = self.file(self.grace, "live.txt")
        seen = []
        real = account_deletion.delete_account

        def spy(user):
            seen.append(connection.in_atomic_block)
            return real(user)

        with mock.patch("drop_core.admin.delete_account", spy):
            response = self.client.post(self.url, {"post": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(seen, [False])
        self.assertTrue(self.gone(self.grace.user))
        self.assertFalse(self.stored(live))

    def test_the_admin_deletes_a_lone_administrator(self):
        alone = get_or_create_workspace(make_user("lone"))
        mine = self.file(Membership.objects.get(workspace=alone), "mine.txt")
        response = self.client.post(f"/admin/auth/user/{alone.owner_id}/delete/", {"post": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(username="lone").exists())
        self.assertFalse(self.stored(mine))

    def test_the_admin_delete_button_refuses_with_the_sentence(self):
        url = f"/admin/auth/user/{self.admin.user_id}/delete/"
        for method in (self.client.get, lambda u: self.client.post(u, {"post": "yes"})):
            response = method(url)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response["Location"], f"/admin/auth/user/{self.admin.user_id}/change/")
            followed = self.client.get(response["Location"])
            self.assertContains(
                followed, "This account administers a team with other people on it. Make someone else the administrator first."
            )
        self.assertFalse(self.gone(self.admin.user))

    def test_the_confirmation_shows_counts_and_no_file_name(self):
        self.file(self.grace, "very-private-name.txt")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This deletes the account, its own workspace, and its files and keys on 1 team: 1 file and 1 key in all. It cannot be undone.")
        self.assertContains(response, "Delete the account")
        self.assertContains(response, "Go back")
        self.assertNotContains(response, "very-private-name")
        self.assertTrue(User.objects.filter(pk=self.grace.user_id).exists())

    def test_the_confirmation_form_carries_a_csrf_token(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.staff)
        response = strict.get(self.url)
        token = response.cookies["csrftoken"].value
        refused = strict.post(self.url, {"post": "yes"})
        self.assertEqual(refused.status_code, 403)
        accepted = strict.post(self.url, {"post": "yes", "csrfmiddlewaretoken": token})
        self.assertEqual(accepted.status_code, 302)
        self.assertTrue(self.gone(self.grace.user))

    def test_there_is_no_bulk_delete_of_accounts(self):
        response = self.client.get("/admin/auth/user/")
        self.assertContains(response, "grace@example.com")
        self.assertNotContains(response, 'value="delete_selected"')
        response = self.client.post(
            "/admin/auth/user/", {"action": "delete_selected", "_selected_action": [self.grace.user_id], "post": "yes"}
        )
        self.assertTrue(User.objects.filter(pk=self.grace.user_id).exists())
        Group.objects.create(name="staff-readers")
        self.assertContains(self.client.get("/admin/auth/group/"), 'value="delete_selected"')

    def test_staff_cannot_delete_a_membership_or_a_workspace(self):
        self.assertEqual(self.client.get(f"/admin/drop_core/membership/{self.grace.pk}/delete/").status_code, 403)
        self.assertEqual(self.client.get(f"/admin/drop_core/workspace/{self.team.pk}/delete/").status_code, 403)
        self.assertEqual(self.client.post(f"/admin/drop_core/workspace/{self.team.pk}/delete/", {"post": "yes"}).status_code, 403)
