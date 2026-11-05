from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from .models import (
    EmailVerification,
    Membership,
    Workspace,
    administrator_of,
    current_member,
    get_or_create_workspace,
)

User = get_user_model()


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
