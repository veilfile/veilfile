from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from .key_caps import create_key_within_cap
from .models import (
    APIKey,
    Membership,
    NoCurrentAdministrator,
    Workspace,
    administrator_of,
    current_member,
    current_membership,
    get_or_create_workspace,
    has_other_members,
)
from .tests_members import make_user, second_member


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
