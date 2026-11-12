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
    MemberUsageCounter,
    Membership,
    SafetyEvent,
    UsageCounter,
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
            with self.assertRaises(RuntimeError):
                delete_account(self.grace.user)
        self.assertFalse(self.gone(self.grace.user))

    def test_the_guard_is_an_explicit_raise_that_python_dash_o_keeps(self):
        import inspect

        self.assertNotIn("assert ", inspect.getsource(delete_account))

    def test_the_account_is_inactive_and_its_offer_cleared_before_any_other_step(self):
        Membership.objects.filter(pk=self.grace.pk).update(offered_administrator_at=timezone.now())
        seen = []

        def look(row, now):
            seen.append(
                (
                    User.objects.get(pk=self.grace.user_id).is_active,
                    Membership.objects.get(pk=self.grace.pk).offered_administrator_at,
                )
            )

        with mock.patch("drop_core.account_deletion._close_member_row", look):
            delete_account(self.grace.user)
        self.assertEqual(seen, [(False, None)])

    def test_an_administrator_role_taken_between_the_check_and_the_delete_is_refused_with_the_team_intact(self):
        theirs = self.file(self.admin, "admins.txt")
        real = account_deletion.file_access.delete_files_of

        def accept_late(row, batch=500):
            # What an accepted offer does, as the step in the middle of the deletion would see it.
            Membership.objects.filter(pk=self.admin.pk).update(role=Membership.MEMBER)
            Membership.objects.filter(pk=self.grace.pk).update(role=Membership.ADMINISTRATOR)
            Workspace.objects.filter(pk=self.team.pk).update(owner_id=self.grace.user_id)
            return real(row, batch)

        with mock.patch("drop_core.account_deletion.file_access.delete_files_of", accept_late):
            with self.assertRaises(DeletionRefused) as caught:
                delete_account(self.grace.user)
        self.assertEqual(caught.exception.code, "changed")
        self.assertTrue(Workspace.objects.filter(pk=self.team.pk).exists())
        self.assertTrue(Membership.objects.filter(pk=self.admin.pk).exists())
        self.assertTrue(Artifact.objects.filter(pk=theirs.pk).exists())
        self.assertTrue(self.stored(theirs))
        self.assertFalse(User.objects.get(pk=self.grace.user_id).is_active)

    def test_a_held_file_that_lands_late_is_refused(self):
        alone = get_or_create_workspace(make_user("lone"))
        row = Membership.objects.get(workspace=alone)
        late = self.file(row, "late.txt")
        real = account_deletion.Invitation

        class Late:
            class objects:
                @staticmethod
                def filter(*args, **kwargs):
                    Artifact.objects.filter(pk=late.pk).update(safety_status="held")
                    return real.objects.filter(*args, **kwargs)

        with mock.patch("drop_core.account_deletion.Invitation", Late):
            with self.assertRaises(DeletionRefused) as caught:
                delete_account(alone.owner)
        self.assertEqual(caught.exception.code, "changed")
        self.assertTrue(Artifact.objects.filter(pk=late.pk).exists())
        self.assertTrue(self.stored(late))
        self.assertFalse(User.objects.get(pk=alone.owner_id).is_active)

    def test_a_hold_that_lands_after_the_first_check_keeps_the_file_on_a_team_they_joined(self):
        held_late = self.file(self.grace, "late.txt")
        gone_anyway = self.file(self.grace, "plain.txt")

        def hold(user):
            Artifact.objects.filter(pk=held_late.pk).update(safety_status="held")

        with mock.patch("drop_core.account_deletion.revoke_user_oauth_grants", hold):
            with self.assertRaises(DeletionRefused) as caught:
                delete_account(self.grace.user)
        self.assertEqual(caught.exception.code, "changed")
        self.assertTrue(Artifact.objects.filter(pk=held_late.pk).exists())
        self.assertTrue(self.stored(held_late))
        self.assertFalse(Artifact.objects.filter(pk=gone_anyway.pk).exists())
        self.assertFalse(self.stored(gone_anyway))
        self.assertFalse(User.objects.get(pk=self.grace.user_id).is_active)

    def test_a_file_uploaded_late_on_a_team_they_joined_is_refused_without_a_restricted_error(self):
        real = account_deletion.file_access.delete_files_of
        late = []

        def upload_late(row, batch=500):
            done = real(row, batch)
            late.append(self.file(Membership.objects.get(pk=row.pk), "late.txt"))
            return done

        with mock.patch("drop_core.account_deletion.file_access.delete_files_of", upload_late):
            with self.assertRaises(DeletionRefused) as caught:
                delete_account(self.grace.user)
        self.assertEqual(caught.exception.code, "changed")
        self.assertTrue(Artifact.objects.filter(pk=late[0].pk).exists())
        self.assertFalse(self.gone(self.grace.user))
        self.assertFalse(User.objects.get(pk=self.grace.user_id).is_active)

    def test_the_summary_counts_and_names_nothing(self):
        self.file(self.grace, "secret-name.txt")
        self.file(self.grace, "another.txt")
        summary = deletion_summary(self.grace.user)
        self.assertEqual(summary, {"teams": 1, "files": 2, "keys": 1})

    def test_the_summary_does_not_count_the_persons_own_workspace_as_a_team(self):
        alone = get_or_create_workspace(make_user("lone"))
        self.assertEqual(deletion_summary(alone.owner)["teams"], 0)

    def test_the_files_go_in_batches(self):
        for n in range(5):
            self.file(self.grace, f"f{n}.txt")
        from . import file_access

        self.assertEqual(file_access.delete_files_of(Membership.objects.get(pk=self.grace.pk), batch=2), 5)
        self.assertFalse(Artifact.objects.filter(member=self.grace).exists())

    def test_a_held_file_survives_delete_files_of_and_the_call_counts_what_it_deleted(self):
        from . import file_access

        row = Membership.objects.get(pk=self.grace.pk)
        a, b = self.file(row, "a.txt"), self.file(row, "b.txt")
        held = self.file(row, "held.txt", held=True)
        self.assertEqual(file_access.delete_files_of(row, batch=1), 2)
        self.assertEqual(list(Artifact.objects.filter(member=row).values_list("pk", flat=True)), [held.pk])
        self.assertTrue(self.stored(held))
        self.assertFalse(self.stored(a) or self.stored(b))

    def test_the_last_transaction_locks_the_counters_before_the_workspace_rows(self):
        from django.test.utils import CaptureQueriesContext

        alone = get_or_create_workspace(make_user("lone"))
        row = Membership.objects.get(workspace=alone)
        month = timezone.now().strftime("%Y-%m")
        UsageCounter.objects.create(workspace=alone, year_month=month, uploads=1)
        MemberUsageCounter.objects.create(member=row, year_month=month, uploads=1)
        with CaptureQueriesContext(connection) as captured:
            delete_account(alone.owner)
        sql = [q["sql"] for q in captured.captured_queries]
        delete_at = next(i for i, q in enumerate(sql) if q.startswith('DELETE FROM "auth_user"') or q.startswith('DELETE FROM "' + User._meta.db_table + '"'))
        before = sql[:delete_at]

        def last_select(table):
            hits = [i for i, q in enumerate(before) if q.startswith("SELECT") and f'FROM "{table}"' in q]
            self.assertTrue(hits, table)
            return hits[-1]

        counters = last_select("drop_core_usagecounter")
        members = last_select("drop_core_memberusagecounter")
        workspaces = last_select("drop_core_workspace")
        self.assertTrue(counters < members < workspaces, (counters, members, workspaces))
        counter_selects = [
            q for q in before if q.startswith("SELECT") and 'FROM "drop_core_memberusagecounter"' in q
        ]
        self.assertTrue(counter_selects)
        # A join would make Postgres lock the membership rows too, ahead of the workspace rows.
        self.assertFalse(any("JOIN" in q for q in counter_selects), counter_selects)


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

    def test_a_late_refusal_shows_the_changed_sentence(self):
        real = account_deletion.file_access.delete_files_of

        def upload_late(row, batch=500):
            done = real(row, batch)
            self.file(Membership.objects.get(pk=row.pk), "late.txt")
            return done

        with mock.patch("drop_core.account_deletion.file_access.delete_files_of", upload_late):
            response = self.client.post(self.url, {"post": "yes"})
        self.assertEqual(response.status_code, 302)
        followed = self.client.get(response["Location"])
        self.assertContains(
            followed, "Something changed while the account was being deleted, so it was not deleted. It is inactive now."
        )
        self.assertFalse(self.gone(self.grace.user))

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

    def test_staff_cannot_delete_the_account_they_are_signed_in_with(self):
        from django.contrib.admin.models import LogEntry

        url = f"/admin/auth/user/{self.staff.pk}/delete/"
        change = f"/admin/auth/user/{self.staff.pk}/change/"
        for method in (self.client.get, lambda u: self.client.post(u, {"post": "yes"})):
            response = method(url)
            self.assertEqual((response.status_code, response["Location"]), (302, change))
            self.assertContains(
                self.client.get(change),
                "You cannot delete the account you are signed in with. Ask another member of staff to do it.",
            )
        self.assertTrue(User.objects.get(pk=self.staff.pk).is_active)
        self.assertFalse(LogEntry.objects.filter(action_flag=3).exists())
