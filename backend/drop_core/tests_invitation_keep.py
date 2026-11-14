"""The sweep deletes an invitation 30 days after it ends (team seats F2b; the owner's F1 decision 1, 2026-11-14).

An invitation ends at the first of accepted, declined, revoked and expired. Every class sets longMessage = False.
"""
import hashlib
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from . import sweep, teams
from .models import Artifact, Invitation
from .teams import DECLINE_WAIT, InviteRefused
from .tests_invitations import Base

KINDS = ("accepted", "declined", "revoked", "expired")


@override_settings(TEAM_SEATS=True)
class InvitationKeepTests(Base):
    longMessage = False

    def row(self, email, *, ended_days, how):
        now = timezone.now()
        ended = now - timedelta(days=ended_days)
        if how == "expired":
            expires, created, stamps = ended, ended - Invitation.TTL, {}
        else:
            created = ended - timedelta(days=1)
            expires, stamps = created + Invitation.TTL, {f"{how}_at": ended}
        invitation = Invitation.objects.create(
            workspace=self.team, email=email, token_hash=hashlib.sha256(email.encode()).hexdigest(), invited_by=None,
            expires_at=expires, **stamps,
        )
        Invitation.objects.filter(pk=invitation.pk).update(created_at=created)
        return invitation

    def rows_of_each_kind(self, ended_days):
        return [self.row(f"{how}@example.com", ended_days=ended_days, how=how) for how in KINDS]

    def test_each_ending_is_deleted_after_30_days(self):
        self.rows_of_each_kind(31)
        self.assertEqual(teams.purge_ended_invitations(), 4,
                         "backend/drop_core/teams.py: purge_ended_invitations deletes each kind of ending 30 days on")
        self.assertFalse(Invitation.objects.exists())

    def test_each_ending_is_kept_before_30_days(self):
        self.rows_of_each_kind(29)
        self.send("waiting@example.com")
        self.assertEqual(teams.purge_ended_invitations(), 0,
                         "backend/drop_core/teams.py: an invitation that ended 29 days ago is kept")
        self.assertEqual(Invitation.objects.count(), 5)

    def test_a_late_close_counts_from_the_expiry(self):
        late = self.row("late@example.com", ended_days=31, how="expired")
        Invitation.objects.filter(pk=late.pk).update(revoked_at=timezone.now() - timedelta(days=2))
        teams.purge_ended_invitations()
        self.assertFalse(Invitation.objects.filter(pk=late.pk).exists(),
                         "backend/drop_core/teams.py: an invitation closed long after it expired ended at its expiry")

    def test_the_decline_block_ends_before_the_row_goes(self):
        declined = self.row("grace@example.com", ended_days=29, how="declined")
        teams.purge_ended_invitations()
        with self.assertRaises(InviteRefused) as caught:
            self.send("grace@example.com")
        self.assertEqual(caught.exception.code, "declined_recently",
                         "backend/drop_core/teams.py: the decline block still sees a decline 29 days old")
        Invitation.objects.filter(pk=declined.pk).update(declined_at=timezone.now() - timedelta(days=31))
        self.assertEqual(teams.purge_ended_invitations(), 1)
        row, _ = self.send("grace@example.com")
        self.assertIsNone(row.declined_at)

    def test_the_keep_covers_the_limits(self):
        self.assertEqual(teams.INVITATION_KEEP, timedelta(days=30))
        self.assertGreaterEqual(teams.INVITATION_KEEP, DECLINE_WAIT,
                                "backend/drop_core/teams.py: INVITATION_KEEP never ends before the decline block")
        self.assertGreater(teams.INVITATION_KEEP, timedelta(days=1),
                           "backend/drop_core/teams.py: INVITATION_KEEP outlasts the per-day limits")

    def test_the_sweep_deletes_them(self):
        old = self.row("old@example.com", ended_days=31, how="accepted")
        sweep.sweep_expired()
        self.assertFalse(Invitation.objects.filter(pk=old.pk).exists(),
                         "backend/drop_core/sweep.py: sweep_expired runs purge_ended_invitations")

    def test_a_failed_purge_does_not_fail_the_sweep(self):
        Artifact.objects.create(workspace=self.team, member=self.admin, filename="old.txt", size_bytes=1,
                                expires_at=timezone.now() - timedelta(days=2))
        with mock.patch("drop_core.teams.purge_ended_invitations", side_effect=RuntimeError("boom")):
            with self.assertLogs("drop_core.sweep", "ERROR") as logs:
                result = sweep.sweep_expired()
        self.assertEqual(result.swept, 1, "backend/drop_core/sweep.py: a failed purge does not stop the file deletes")
        self.assertTrue(any("sweep: deleting ended invitations failed" in line for line in logs.output))
