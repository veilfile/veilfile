"""Holding a file (team seats, ways out): what a hold stops, who is told and what the notice leaves out.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it).
"""
from datetime import timedelta
from unittest import mock

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from . import holds
from .admin import ArtifactAdmin
from .key_caps import create_key_within_cap
from .models import Artifact, Membership
from .teams import accept_administrator_offer, offer_administrator
from .tests_members import second_member
from .tests_teams import team_of

User = get_user_model()
SUBJECT = "A file on your Veilfile team was held"


@override_settings(TEAM_SEATS=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class HoldTests(TestCase):
    longMessage = False

    def setUp(self):
        self.team, self.admin = team_of("ada")
        self.grace = second_member(self.team, "grace")
        self.key, self.raw = create_key_within_cap(self.team, "graces-secret-key-name", member=self.grace)

    def file(self, member=None, name="graces-plans.txt", key="own", workspace=None):
        member = member or self.grace
        return Artifact.objects.create(
            workspace=workspace or member.workspace, member=member, filename=name, size_bytes=4321,
            api_key=self.key if key == "own" else None, expires_at=timezone.now() + timedelta(days=3),
        )

    def held(self, artifact):
        return Artifact.objects.get(pk=artifact.pk).safety_status == "held"

    def test_holding_sets_the_file_and_stops_its_key(self):
        artifact = self.file()
        result = holds.hold_files([artifact.pk])
        self.assertEqual((result.held, result.told, result.not_told), (1, 1, 0))
        self.assertTrue(self.held(artifact))
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)
        self.assertEqual(self.client.get(f"/a/{artifact.token}").status_code, 404)

    def test_holding_a_file_with_no_key_holds_it_and_the_email_makes_no_claim_about_a_key(self):
        artifact = self.file(key=None)
        holds.hold_files([artifact.pk])
        self.assertTrue(self.held(artifact))
        self.key.refresh_from_db()
        self.assertTrue(self.key.is_active)
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn("API", mail.outbox[0].body)
        self.assertNotIn("stopped working", mail.outbox[0].body)

    def test_holding_an_already_held_file_does_nothing_and_sends_nothing(self):
        artifact = self.file()
        holds.hold_files([artifact.pk])
        mail.outbox.clear()
        result = holds.hold_files([artifact.pk])
        self.assertEqual((result.held, result.told, result.not_told), (0, 0, 0))
        self.assertEqual(mail.outbox, [])

    def test_one_email_per_team_whatever_the_count(self):
        ids = [self.file(name=f"f{i}.txt").pk for i in range(3)]
        other, _ = team_of("kay")
        other_file = self.file(member=second_member(other, "lin"), key=None)
        result = holds.hold_files(ids + [other_file.pk])
        self.assertEqual((result.held, result.told), (4, 2))
        self.assertEqual(len(mail.outbox), 2)
        by_team = {message.to[0]: message for message in mail.outbox}
        self.assertIn("Veilfile held 3 files that someone on your team uploaded", by_team["ada@example.com"].body)
        self.assertIn("their links no longer open", by_team["ada@example.com"].body)
        self.assertIn("Veilfile held 1 file that someone on your team uploaded", by_team["kay@example.com"].body)

    def test_the_email_has_the_words(self):
        holds.hold_files([self.file().pk])
        message = mail.outbox[0]
        self.assertEqual(message.subject, SUBJECT)
        self.assertEqual(message.to, ["ada@example.com"])
        self.assertEqual(
            message.body,
            "Veilfile held 1 file that someone on your team uploaded, because it may break our Terms of Service, and its link no longer "
            "opens. The API key that uploaded it has stopped working.\n\n"
            "This email does not say which file was held or who uploaded it, to keep files private between the people on a team. If you "
            "have a question, write to support@veilfile.com.\n\nveilfile\n\n",
        )

    def test_the_email_names_no_file_and_no_person(self):
        artifact = self.file()
        with self.assertNoLogs(level="DEBUG"):
            holds.hold_files([artifact.pk])
        text = "\n".join([mail.outbox[0].subject, mail.outbox[0].body])
        for secret in ("graces-plans", artifact.token, "4321", "grace@example.com", "graces-secret-key-name", str(artifact.pk)):
            self.assertNotIn(secret, text, f"backend/drop_core/holds.py: the notice must not hold {secret}")

    def test_the_email_goes_to_the_current_administrator(self):
        offer_administrator(self.admin, self.grace.pk)
        accept_administrator_offer(Membership.objects.get(pk=self.grace.pk))
        artifact = self.file(member=Membership.objects.get(pk=self.admin.pk), key=None)
        holds.hold_files([artifact.pk])
        self.assertEqual([message.to for message in mail.outbox], [["grace@example.com"]])

    def test_no_email_when_the_uploader_is_the_administrator(self):
        artifact = self.file(member=self.admin, key=None)
        result = holds.hold_files([artifact.pk])
        self.assertEqual((result.held, result.told, result.not_told), (1, 0, 0))
        self.assertEqual(mail.outbox, [])

    def test_a_file_in_a_workspace_with_no_current_administrator_is_held_without_an_email(self):
        _, closed_admin = team_of("kay")
        # A person who joined a team keeps a closed administrator row in the place they left behind.
        Membership.objects.filter(pk=closed_admin.pk).update(removed_at=timezone.now())
        artifact = self.file(member=closed_admin, key=None)
        result = holds.hold_files([artifact.pk])
        self.assertEqual((result.held, result.told, result.not_told), (1, 0, 0))
        self.assertTrue(self.held(artifact))
        self.assertEqual(mail.outbox, [])

    def test_a_failed_email_keeps_the_hold_and_logs_the_class_only(self):
        artifact = self.file()
        with mock.patch.object(holds, "send_mail", side_effect=ConnectionRefusedError("smtp password hunter2 ada@example.com")):
            with self.assertLogs("drop_core.holds", level="ERROR") as logged:
                result = holds.hold_files([artifact.pk])
        self.assertEqual((result.held, result.told, result.not_told), (1, 0, 1))
        self.assertTrue(self.held(artifact))
        self.assertEqual(logged.output, ["ERROR:drop_core.holds:Hold notice failed: ConnectionRefusedError"])

    def test_releasing_clears_the_hold_and_keeps_the_key_stopped(self):
        artifact = self.file()
        holds.hold_files([artifact.pk])
        self.assertEqual(holds.release_files([artifact.pk]), 1)
        self.assertFalse(self.held(artifact))
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)
        self.assertEqual(holds.release_files([artifact.pk]), 0)

    def test_holding_works_with_the_switch_off(self):
        artifact = self.file()
        with override_settings(TEAM_SEATS=False):
            result = holds.hold_files([artifact.pk])
        self.assertEqual((result.held, result.told), (1, 1))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AdminActionTests(TestCase):
    longMessage = False

    def setUp(self):
        self.team, self.admin = team_of("ada")
        self.grace = second_member(self.team, "grace")
        self.artifact = Artifact.objects.create(
            workspace=self.team, member=self.grace, filename="a.txt", size_bytes=1, expires_at=timezone.now() + timedelta(days=3)
        )
        self.staff = User.objects.create_superuser("root", "root@example.com", "pw12345678")
        self.client.force_login(self.staff)

    def act(self, action):
        return self.client.post(
            "/admin/drop_core/artifact/", {"action": action, "_selected_action": [str(self.artifact.pk)]}, follow=True
        )

    def state(self):
        return Artifact.objects.get(pk=self.artifact.pk).safety_status

    def test_the_admin_form_cannot_edit_safety_status(self):
        self.assertIn("safety_status", ArtifactAdmin.readonly_fields)
        response = self.client.post(
            f"/admin/drop_core/artifact/{self.artifact.pk}/change/",
            {"filename": "a.txt", "mime_type": "text/plain", "size_bytes": 1, "safety_status": "held", "member": self.grace.pk,
             "workspace": self.team.pk, "visibility": "only_me", "expires_at_0": "2030-01-01", "expires_at_1": "00:00:00",
             "content_sha256": "", "secret_flags": "[]"},
        )
        self.assertEqual(self.state(), "clear", f"status {response.status_code}")

    def test_the_actions_hold_and_release_and_log_each_change(self):
        response = self.act("hold_selected_files")
        self.assertEqual(self.state(), "held")
        text = " ".join(str(message) for message in response.context["messages"])
        self.assertIn("1 file held. 1 administrator was told.", text)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(LogEntry.objects.filter(object_id=str(self.artifact.pk), change_message__contains="Held").count(), 1)
        self.act("hold_selected_files")
        self.assertEqual(LogEntry.objects.filter(object_id=str(self.artifact.pk), change_message__contains="Held").count(), 1)
        response = self.act("release_selected_files")
        self.assertEqual(self.state(), "clear")
        text = " ".join(str(message) for message in response.context["messages"])
        self.assertIn("1 file released. The keys that uploaded them stay stopped.", text)
        self.assertEqual(LogEntry.objects.filter(object_id=str(self.artifact.pk), change_message__contains="Released").count(), 1)

    def test_a_failed_notice_warns_staff(self):
        with mock.patch.object(holds, "send_mail", side_effect=OSError):
            with self.assertLogs("drop_core.holds", level="ERROR"):
                response = self.act("hold_selected_files")
        self.assertEqual(self.state(), "held")
        text = " ".join(str(message) for message in response.context["messages"])
        self.assertIn("The notice to 1 administrator could not be sent, so tell them another way.", text)
