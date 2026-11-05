import importlib
import os
import tempfile
from datetime import timedelta
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.db.models import RestrictedError
from django.test import TestCase
from django.utils import timezone

from .models import (
    VISIBILITY_ONLY_ME,
    APIKey,
    Artifact,
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
