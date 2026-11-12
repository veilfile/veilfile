"""The administrator offer (team seats, ways out): offering, cancelling, accepting and declining, and what the admin may no longer edit.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it).
"""
import io
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from . import teams
from .file_access import files_visible_to
from .key_caps import create_key_within_cap
from .models import (
    APIKey,
    Artifact,
    EmailVerification,
    Membership,
    NoCurrentAdministrator,
    VISIBILITY_ONLY_ME,
    Workspace,
    administrator_of,
    get_or_create_workspace,
)
from .storage import LocalStorage
from .teams import (
    OFFER_TTL,
    SeatChangeRefused,
    accept_administrator_offer,
    administrator_offer_for,
    cancel_administrator_offer,
    decline_administrator_offer,
    offer_administrator,
    waiting_administrator_offer,
)
from .tests import _use_local_dir
from .tests_members import make_user, second_member
from .tests_teams import team_of

User = get_user_model()
FILE = "backend/drop_core/teams.py"


def fresh(row):
    return Membership.objects.get(pk=row.pk)


class Base(TestCase):
    longMessage = False

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        _use_local_dir(self, self.tmpdir)
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.team, self.admin = team_of("ada")
        self.grace = second_member(self.team, "grace")

    def refused(self, code, call, *args):
        with self.assertRaises(SeatChangeRefused) as caught:
            call(*args)
        self.assertEqual(caught.exception.code, code, f"{FILE}: {call.__name__} must refuse with {code}")

    def file(self, member, name="a.txt", visibility=VISIBILITY_ONLY_ME):
        artifact = Artifact.objects.create(
            workspace=member.workspace, member=member, filename=name, size_bytes=5, visibility=visibility,
            expires_at=timezone.now() + timedelta(days=7),
        )
        LocalStorage().save(artifact.token, io.BytesIO(b"bytes"), "application/octet-stream")
        self.addCleanup(LocalStorage().delete, artifact.token)
        return artifact


@override_settings(TEAM_SEATS=True)
class OfferTests(Base):
    def test_an_offer_waits_for_seven_days(self):
        row = offer_administrator(self.admin, self.grace.pk)
        self.assertEqual(OFFER_TTL, timedelta(days=7))
        self.assertIsNotNone(fresh(self.grace).offered_administrator_at)
        self.assertEqual(row.pk, self.grace.pk)
        waiting = waiting_administrator_offer(self.team)
        self.assertEqual(waiting["email"], "grace@example.com")
        self.assertEqual(waiting["expires"], fresh(self.grace).offered_administrator_at + OFFER_TTL)
        mine = administrator_offer_for(fresh(self.grace))
        self.assertEqual(mine["admin_email"], "ada@example.com")
        self.assertEqual(mine["expires"], waiting["expires"])
        self.assertIsNone(administrator_offer_for(self.admin))
        Membership.objects.filter(pk=self.grace.pk).update(
            offered_administrator_at=timezone.now() - OFFER_TTL - timedelta(seconds=1)
        )
        self.assertIsNone(waiting_administrator_offer(self.team))
        self.assertIsNone(administrator_offer_for(fresh(self.grace)))

    def test_a_new_offer_replaces_the_waiting_one(self):
        hank = second_member(self.team, "hank")
        offer_administrator(self.admin, self.grace.pk)
        offer_administrator(self.admin, hank.pk)
        self.assertIsNone(fresh(self.grace).offered_administrator_at)
        self.assertIsNotNone(fresh(hank).offered_administrator_at)
        self.assertEqual(waiting_administrator_offer(self.team)["email"], "hank@example.com")

    def test_the_database_allows_one_offer_a_team(self):
        hank = second_member(self.team, "hank")
        offer_administrator(self.admin, self.grace.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.filter(pk=hank.pk).update(offered_administrator_at=timezone.now())

    def test_an_offer_to_the_administrator_a_closed_row_or_another_team_is_refused(self):
        gone = second_member(self.team, "gone")
        Membership.objects.filter(pk=gone.pk).update(removed_at=timezone.now())
        other, _ = team_of("lin")
        stranger = second_member(other, "stranger")
        for pk in (self.admin.pk, gone.pk, stranger.pk):
            self.refused("person_gone", offer_administrator, self.admin, pk)
        self.assertFalse(Membership.objects.filter(offered_administrator_at__isnull=False).exists())

    def test_an_offer_to_a_suspended_or_unverified_person_is_refused(self):
        User.objects.filter(pk=self.grace.user_id).update(is_active=False)
        self.refused("chosen_suspended", offer_administrator, self.admin, self.grace.pk)
        User.objects.filter(pk=self.grace.user_id).update(is_active=True)
        EmailVerification.objects.filter(user=self.grace.user).update(is_verified=False)
        self.refused("chosen_not_verified", offer_administrator, self.admin, self.grace.pk)
        self.assertIsNone(fresh(self.grace).offered_administrator_at)

    def test_only_the_administrator_may_offer(self):
        with self.assertRaises(PermissionDenied):
            offer_administrator(self.grace, self.grace.pk)

    def test_a_suspended_team_refuses_offers_and_acceptance(self):
        Workspace.objects.filter(pk=self.team.pk).update(suspended_at=timezone.now())
        self.refused("team_suspended", offer_administrator, self.admin, self.grace.pk)
        Membership.objects.filter(pk=self.grace.pk).update(offered_administrator_at=timezone.now())
        self.refused("team_suspended", accept_administrator_offer, fresh(self.grace))
        self.assertEqual(fresh(self.admin).role, Membership.ADMINISTRATOR)

    def test_cancelling_clears_the_offer(self):
        self.assertFalse(cancel_administrator_offer(self.admin))
        offer_administrator(self.admin, self.grace.pk)
        self.assertFalse(cancel_administrator_offer(self.grace))
        self.assertTrue(cancel_administrator_offer(self.admin))
        self.assertIsNone(fresh(self.grace).offered_administrator_at)

    def test_with_the_switch_off_the_helpers_ask_nothing(self):
        Membership.objects.filter(pk=self.grace.pk).update(offered_administrator_at=timezone.now())
        grace = fresh(self.grace)
        with override_settings(TEAM_SEATS=False), self.assertNumQueries(0):
            self.assertIsNone(waiting_administrator_offer(self.team))
            self.assertIsNone(administrator_offer_for(grace))
            self.assertFalse(cancel_administrator_offer(self.admin))
            self.assertFalse(decline_administrator_offer(grace))
            self.refused("offer_not_waiting", accept_administrator_offer, grace)
            with self.assertRaises(PermissionDenied):
                offer_administrator(self.admin, grace.pk)
        self.assertEqual(fresh(self.admin).role, Membership.ADMINISTRATOR)


@override_settings(TEAM_SEATS=True)
class AcceptTests(Base):
    def setUp(self):
        super().setUp()
        offer_administrator(self.admin, self.grace.pk)

    def test_accepting_flips_the_roles_moves_the_owner_and_keeps_the_rows(self):
        Membership.objects.filter(pk=self.grace.pk).update(upload_allowance=3, storage_allowance_bytes=10, key_allowance=1)
        ada_key, _ = create_key_within_cap(self.team, "ada-key", member=self.admin)
        grace_key, _ = create_key_within_cap(self.team, "grace-key", member=self.grace)
        ada_file, grace_file = self.file(self.admin), self.file(self.grace)
        accept_administrator_offer(fresh(self.grace))
        ada, grace = fresh(self.admin), fresh(self.grace)
        self.assertEqual((ada.pk, grace.pk), (self.admin.pk, self.grace.pk))
        self.assertEqual((ada.role, grace.role), (Membership.MEMBER, Membership.ADMINISTRATOR))
        self.assertEqual(Workspace.objects.get(pk=self.team.pk).owner_id, self.grace.user_id)
        self.assertIsNone(grace.offered_administrator_at)
        self.assertEqual((grace.upload_allowance, grace.storage_allowance_bytes, grace.key_allowance), (None, None, None))
        self.assertEqual(APIKey.objects.get(pk=ada_key.pk).member_id, ada.pk)
        self.assertEqual(APIKey.objects.get(pk=grace_key.pk).member_id, grace.pk)
        self.assertEqual(Artifact.objects.get(pk=ada_file.pk).member_id, ada.pk)
        self.assertEqual(Artifact.objects.get(pk=grace_file.pk).member_id, grace.pk)
        self.assertIsNone(waiting_administrator_offer(self.team))

    def test_accepting_after_the_offer_expired_or_was_cancelled_is_refused(self):
        Membership.objects.filter(pk=self.grace.pk).update(
            offered_administrator_at=timezone.now() - OFFER_TTL - timedelta(seconds=1)
        )
        self.refused("offer_not_waiting", accept_administrator_offer, fresh(self.grace))
        offer_administrator(self.admin, self.grace.pk)
        cancel_administrator_offer(self.admin)
        self.refused("offer_not_waiting", accept_administrator_offer, fresh(self.grace))
        self.assertEqual(fresh(self.admin).role, Membership.ADMINISTRATOR)

    def test_accepting_after_the_administrator_changed_is_refused(self):
        hank = second_member(self.team, "hank")
        Membership.objects.filter(pk=self.admin.pk).update(role=Membership.MEMBER)
        Membership.objects.filter(pk=hank.pk).update(role=Membership.ADMINISTRATOR)
        self.refused("offer_not_waiting", accept_administrator_offer, fresh(self.grace))
        self.assertEqual(fresh(self.grace).role, Membership.MEMBER)
        self.assertEqual(fresh(hank).role, Membership.ADMINISTRATOR)

    def test_accepting_with_no_administrator_row_is_refused(self):
        Membership.objects.filter(pk=self.admin.pk).update(removed_at=timezone.now())
        self.refused("offer_not_waiting", accept_administrator_offer, fresh(self.grace))

    def test_accepting_twice_is_harmless(self):
        stale = fresh(self.grace)
        accept_administrator_offer(stale)
        self.refused("offer_not_waiting", accept_administrator_offer, stale)
        self.assertEqual(fresh(self.grace).role, Membership.ADMINISTRATOR)
        self.assertEqual(fresh(self.admin).role, Membership.MEMBER)

    def test_a_person_who_is_suspended_or_unverified_by_then_cannot_accept(self):
        User.objects.filter(pk=self.grace.user_id).update(is_active=False)
        self.refused("chosen_suspended", accept_administrator_offer, fresh(self.grace))
        User.objects.filter(pk=self.grace.user_id).update(is_active=True)
        EmailVerification.objects.filter(user=self.grace.user).update(is_verified=False)
        self.refused("chosen_not_verified", accept_administrator_offer, fresh(self.grace))
        self.assertEqual(fresh(self.admin).role, Membership.ADMINISTRATOR)

    def test_declining_clears_the_offer(self):
        self.assertFalse(decline_administrator_offer(fresh(self.admin)))
        self.assertTrue(decline_administrator_offer(fresh(self.grace)))
        self.assertIsNone(fresh(self.grace).offered_administrator_at)
        self.assertFalse(decline_administrator_offer(fresh(self.grace)))
        self.assertEqual(fresh(self.admin).role, Membership.ADMINISTRATOR)

    def test_the_former_administrators_only_me_files_stay_out_of_the_new_administrators_list(self):
        mine = self.file(self.admin, "ada-private.txt")
        accept_administrator_offer(fresh(self.grace))
        self.assertNotIn(mine.pk, files_visible_to(fresh(self.grace)).values_list("pk", flat=True))
        self.assertIn(mine.pk, files_visible_to(fresh(self.admin)).values_list("pk", flat=True))

    def test_administrator_of_refuses_a_stale_workspace(self):
        stale = Workspace.objects.get(pk=self.team.pk)
        accept_administrator_offer(fresh(self.grace))
        with self.assertRaises(NoCurrentAdministrator):
            administrator_of(stale)
        self.assertEqual(administrator_of(Workspace.objects.get(pk=self.team.pk)).pk, self.grace.pk)

    def test_a_new_workspace_still_gets_its_administrator_row(self):
        workspace = get_or_create_workspace(make_user("lin"))
        row = administrator_of(workspace)
        self.assertEqual((row.role, row.user_id), (Membership.ADMINISTRATOR, workspace.owner_id))

    def test_an_integrity_error_becomes_offer_not_waiting(self):
        other, _ = team_of("lin")
        Membership.objects.create(workspace=other, user=self.admin.user, role=Membership.MEMBER)
        self.refused("offer_not_waiting", accept_administrator_offer, fresh(self.grace))
        self.assertEqual(fresh(self.admin).role, Membership.ADMINISTRATOR)
        self.assertEqual(fresh(self.grace).role, Membership.MEMBER)
        self.assertEqual(Workspace.objects.get(pk=self.team.pk).owner_id, self.admin.user_id)


@override_settings(TEAM_SEATS=True)
class AdminTests(TestCase):
    longMessage = False

    def setUp(self):
        self.team, self.admin = team_of("ada")
        self.grace = second_member(self.team, "grace")
        self.staff = User.objects.create_superuser("root", "root@example.com", "pw12345678")
        self.client = Client()
        self.client.force_login(self.staff)

    def test_staff_cannot_edit_the_owner_or_a_memberships_fields(self):
        page = self.client.get(f"/admin/drop_core/workspace/{self.team.pk}/change/")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("owner", page.context["adminform"].form.fields)
        self.client.post(
            f"/admin/drop_core/workspace/{self.team.pk}/change/",
            {"owner": self.grace.user_id, "name": "Renamed", "plan": self.team.plan},
        )
        self.assertEqual(Workspace.objects.get(pk=self.team.pk).owner_id, self.admin.user_id)
        page = self.client.get(f"/admin/drop_core/membership/{self.grace.pk}/change/")
        self.assertEqual(page.status_code, 200)
        shown = page.context["adminform"].form.fields
        for name in ("workspace", "user", "role", "removed_at", "offered_administrator_at"):
            self.assertNotIn(name, shown, f"backend/drop_core/admin.py: {name} must be read-only")
        self.client.post(
            f"/admin/drop_core/membership/{self.grace.pk}/change/",
            {"role": Membership.ADMINISTRATOR, "workspace": self.team.pk, "user": self.grace.user_id, "upload_allowance": 4},
        )
        row = Membership.objects.get(pk=self.grace.pk)
        self.assertEqual((row.role, row.upload_allowance), (Membership.MEMBER, 4))

    def test_staff_cannot_add_a_membership_or_a_workspace(self):
        for name in ("membership", "workspace"):
            self.assertEqual(self.client.get(f"/admin/drop_core/{name}/add/").status_code, 403)
            self.assertEqual(self.client.post(f"/admin/drop_core/{name}/add/", {}).status_code, 403)
