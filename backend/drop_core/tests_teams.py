"""Team seats: the switch, and the visibility a new file takes at upload.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it).
"""
import hashlib
import os
import re
import tempfile
import threading
import time
from datetime import timedelta
from smtplib import SMTPException
from unittest import mock

from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, connection, connections
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from config.log_redaction import redact_path

from config.settings import team_seats_from

from .models import (
    EmailVerification,
    Invitation,
    Membership,
    FILE_VISIBILITY_ALWAYS_ONLY_ME,
    FILE_VISIBILITY_ALWAYS_TEAM,
    FILE_VISIBILITY_MEMBERS_CHOOSE,
    VISIBILITY_ONLY_ME,
    VISIBILITY_TEAM,
    Workspace,
    get_or_create_workspace,
)
from .services import upload_artifact
from .teams import (
    DECLINE_WAIT,
    INVITES_PER_ADDRESS_PER_DAY,
    INVITES_PER_DAY,
    INVITES_PER_HOUR,
    MAX_WAITING,
    SITE_INVITES_PER_DAY,
    InviteRefused,
    cancel_invitation,
    find_invitation,
    invite,
    same_address,
    seat_limit,
    seats_in_use,
    team_seats_only,
)
from .tests_members import make_user, second_member


class SwitchTests(SimpleTestCase):
    longMessage = False

    def test_it_is_off_unless_set_to_on(self):
        self.assertEqual(
            [team_seats_from(value) for value in (None, "", "off", "on")],
            [False, False, False, True],
            "backend/config/settings.py: team_seats_from must be true only for the value on, and the setting must read VEILFILE_TEAM_SEATS",
        )

    @override_settings(TEAM_SEATS=False)
    def test_a_view_behind_it_is_the_shells_404_signed_out_and_signed_in(self):
        ran = []

        def view(request):
            ran.append(request)
            return HttpResponse("secret")

        def login_required_stand_in(inner):
            return inner

        wrapped = team_seats_only(login_required_stand_in(view))
        for signed_in in (False, True):
            request = RequestFactory().get("/app/team/")
            request.user = mock.Mock(is_authenticated=signed_in)
            response = wrapped(request)
            body = response.content.decode()
            self.assertEqual(response.status_code, 404, "backend/drop_core/teams.py: team_seats_only must answer 404 while the switch is off")
            self.assertIn("<title>Not found | Veilfile</title>", body, "backend/drop_core/teams.py: the answer must be the shell's 404 page")
            self.assertNotIn("secret", body)
        self.assertEqual(ran, [], "backend/drop_core/teams.py: the wrapped view must not run while the switch is off")
        self.assertEqual(wrapped.__name__, "view", "backend/drop_core/teams.py: team_seats_only must use functools.wraps")

    @override_settings(TEAM_SEATS=True)
    def test_a_view_behind_it_runs_when_the_switch_is_on(self):
        request = RequestFactory().get("/app/team/")
        response = team_seats_only(lambda request: HttpResponse("ran"))(request)
        self.assertEqual(response.content, b"ran", "backend/drop_core/teams.py: team_seats_only must call the view while the switch is on")


class VisibilityAtUploadTests(TestCase):
    longMessage = False

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": tempfile.mkdtemp(prefix="veilfile-test-")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.workspace = get_or_create_workspace(make_user("ada"))

    def upload(self, workspace=None):
        return upload_artifact(workspace or self.workspace, None, "a.txt", b"hello", None)

    def set_setting(self, value):
        Workspace.objects.filter(pk=self.workspace.pk).update(file_visibility=value)

    def test_the_setting_starts_as_members_choose(self):
        self.assertEqual(Workspace.objects.get(pk=self.workspace.pk).file_visibility, FILE_VISIBILITY_MEMBERS_CHOOSE,
                         "backend/drop_core/models.py: file_visibility must default to members_choose")

    def test_a_new_file_starts_as_only_me_when_members_choose(self):
        self.assertEqual(self.upload().visibility, VISIBILITY_ONLY_ME, "backend/drop_core/services.py: members_choose must start a file as only me")

    def test_a_new_file_is_shared_when_the_team_always_shares(self):
        self.set_setting(FILE_VISIBILITY_ALWAYS_TEAM)
        self.assertEqual(self.upload().visibility, VISIBILITY_TEAM, "backend/drop_core/services.py: always_team must start a file as team")

    def test_a_new_file_is_only_me_when_the_team_never_shares(self):
        self.set_setting(FILE_VISIBILITY_ALWAYS_ONLY_ME)
        self.assertEqual(self.upload().visibility, VISIBILITY_ONLY_ME, "backend/drop_core/services.py: always_only_me must start a file as only me")

    def test_changing_the_setting_relabels_nothing(self):
        self.set_setting(FILE_VISIBILITY_ALWAYS_TEAM)
        shared = self.upload()
        self.set_setting(FILE_VISIBILITY_ALWAYS_ONLY_ME)
        private = self.upload()
        self.set_setting(FILE_VISIBILITY_ALWAYS_TEAM)
        shared.refresh_from_db()
        private.refresh_from_db()
        self.assertEqual((shared.visibility, private.visibility), (VISIBILITY_TEAM, VISIBILITY_ONLY_ME),
                         "backend/drop_core/services.py: a stored file's visibility must not follow the setting")

    def test_the_setting_is_read_under_the_lock(self):
        stale = Workspace.objects.get(pk=self.workspace.pk)
        self.set_setting(FILE_VISIBILITY_ALWAYS_TEAM)
        self.assertEqual(stale.file_visibility, FILE_VISIBILITY_MEMBERS_CHOOSE)
        self.assertEqual(self.upload(stale).visibility, VISIBILITY_TEAM,
                         "backend/drop_core/services.py: upload_artifact must read file_visibility from the locked row")


def build_url(raw):
    return f"https://veilfile.test/accounts/invitations/{raw}/"


def team_of(name, plan=Workspace.PLAN_TEAM):
    """A workspace on `plan` and its administrator's membership."""
    workspace = get_or_create_workspace(make_user(name))
    Workspace.objects.filter(pk=workspace.pk).update(plan=plan)
    workspace.refresh_from_db()
    return workspace, Membership.objects.get(workspace=workspace, role=Membership.ADMINISTRATOR)


def refusal(test, code, *args, **kwargs):
    with test.assertRaises(InviteRefused) as caught:
        invite(*args, build_url=build_url, **kwargs)
    test.assertEqual(caught.exception.code, code, f"backend/drop_core/teams.py: invite must refuse with {code}")


@override_settings(TEAM_SEATS=True)
class InviteTests(TestCase):
    longMessage = False

    def setUp(self):
        self.workspace, self.admin = team_of("ada")

    def invite(self, email="grace@example.com", admin=None):
        return invite(admin or self.admin, email, build_url=build_url)

    def test_an_invitation_is_mailed_and_only_its_hash_is_stored(self):
        row = self.invite()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual((message.to, message.subject), (["grace@example.com"], "You are invited to a team on Veilfile"))
        token = re.search(r"/accounts/invitations/([^/\s]+)/", message.body).group(1)
        self.assertEqual(row.token_hash, hashlib.sha256(token.encode()).hexdigest(),
                         "backend/drop_core/teams.py: token_hash must be the sha256 of the raw token")
        stored = " ".join(str(value) for value in Invitation.objects.values_list().get())
        self.assertNotIn(token, stored, "backend/drop_core/teams.py: the raw token must not be stored")

    def test_the_email_names_only_the_inviters_address(self):
        Workspace.objects.filter(pk=self.workspace.pk).update(name="Secret Lab Name")
        self.invite()
        body = mail.outbox[0].body
        self.assertIn("ada@example.com invited you to join their team on Veilfile", body)
        self.assertNotIn("Secret Lab Name", body, "backend/templates/registration/invitation_email.txt: must not name the workspace")

    def test_it_expires_in_seven_days(self):
        before = timezone.now()
        row = self.invite()
        self.assertEqual(Invitation.TTL, timedelta(days=7))
        self.assertAlmostEqual((row.expires_at - row.created_at).total_seconds(), Invitation.TTL.total_seconds(), delta=1)
        self.assertGreaterEqual(row.created_at, before)

    def test_a_waiting_invitation_holds_a_seat(self):
        for n in range(4):
            self.invite(f"p{n}@example.com")
        self.assertEqual((seat_limit(self.workspace), seats_in_use(self.workspace)), (5, 5))
        refusal(self, "no_free_seat", self.admin, "late@example.com")

    def test_an_expired_invitation_frees_its_seat(self):
        for n in range(4):
            self.invite(f"p{n}@example.com")
        old = Invitation.objects.first()
        Invitation.objects.filter(pk=old.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(seats_in_use(self.workspace), 4)
        self.invite("late@example.com")
        old.refresh_from_db()
        self.assertIsNotNone(old.revoked_at, "backend/drop_core/teams.py: invite must close expired rows")

    def test_cancelling_frees_its_seat(self):
        row = self.invite()
        self.assertEqual(seats_in_use(self.workspace), 2)
        self.assertTrue(cancel_invitation(self.admin, row.pk))
        self.assertEqual(seats_in_use(self.workspace), 1)
        self.assertIsNotNone(Invitation.objects.get(pk=row.pk).revoked_at)

    def test_scale_has_no_seat_limit_but_a_waiting_limit(self):
        Workspace.objects.filter(pk=self.workspace.pk).update(plan=Workspace.PLAN_SCALE)
        self.workspace.refresh_from_db()
        self.assertIsNone(seat_limit(self.workspace))
        old = timezone.now() - timedelta(hours=2)
        for n in range(MAX_WAITING):
            row = Invitation.objects.create(workspace=self.workspace, email=f"w{n}@example.com", token_hash=f"h{n}",
                                            invited_by=self.admin.user, expires_at=timezone.now() + timedelta(days=1))
            Invitation.objects.filter(pk=row.pk).update(created_at=old)
        refusal(self, "too_many_waiting", self.admin, "next@example.com")

    def test_the_site_wide_limit_refuses_and_logs(self):
        other, other_admin = team_of("bob")
        for n in range(SITE_INVITES_PER_DAY):
            Invitation.objects.create(workspace=other, email=f"s{n}@example.com", token_hash=f"s{n}",
                                      invited_by=other_admin.user, expires_at=timezone.now() + timedelta(days=1))
        with self.assertLogs("drop_core.teams", level="WARNING"):
            refusal(self, "site_rate_limited", self.admin, "grace@example.com")

    def test_a_decline_stops_this_team_for_thirty_days(self):
        self.assertEqual(DECLINE_WAIT, timedelta(days=30))
        row = self.invite()
        Invitation.objects.filter(pk=row.pk).update(declined_at=timezone.now() - timedelta(days=2))
        refusal(self, "declined_recently", self.admin, "grace@example.com")
        other, other_admin = team_of("bob")
        self.assertIsNotNone(self.invite("grace@example.com", other_admin))

    def test_a_bad_address_is_refused_before_any_row(self):
        for bad in ("nope", "a@b", "a\n@example.com", "a@example.com\nBcc: x@example.com"):
            with self.assertRaises(ValidationError):
                self.invite(bad)
        self.assertEqual((Invitation.objects.count(), len(mail.outbox)), (0, 0))

    def test_the_team_limits_count_every_row(self):
        workspace, admin = team_of("bob", Workspace.PLAN_SCALE)
        for n in range(INVITES_PER_HOUR):
            row = invite(admin, f"p{n}@example.com", build_url=build_url)
            self.assertTrue(cancel_invitation(admin, row.pk))
        refusal(self, "team_rate_limited", admin, "extra@example.com")
        Invitation.objects.filter(workspace=workspace).update(created_at=timezone.now() - timedelta(hours=2))
        for n in range(INVITES_PER_DAY - INVITES_PER_HOUR):
            row = invite(admin, f"q{n}@example.com", build_url=build_url)
            Invitation.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(hours=2), revoked_at=timezone.now())
        refusal(self, "team_rate_limited", admin, "extra@example.com")

    def test_the_address_limit_spans_teams(self):
        for n in range(INVITES_PER_ADDRESS_PER_DAY):
            _, admin = team_of(f"t{n}")
            invite(admin, "grace@example.com", build_url=build_url)
        refusal(self, "address_rate_limited", self.admin, "grace@example.com")
        self.assertIsNotNone(self.invite("other@example.com"))

    def test_an_address_is_matched_without_regard_to_case(self):
        row = self.invite("Grace@Example.com")
        self.assertEqual(row.email, "grace@example.com")
        refusal(self, "already_waiting", self.admin, "grace@example.com")
        for n in range(INVITES_PER_ADDRESS_PER_DAY - 1):
            _, admin = team_of(f"t{n}")
            invite(admin, "GRACE@example.com" if n else "grace@example.com", build_url=build_url)
        refusal(self, "address_rate_limited", team_of("last")[1], "grace@EXAMPLE.com")

    def test_a_compatibility_form_of_an_address_is_the_same_address(self):
        full = "grace@\uff25\uff38\uff21\uff2d\uff30\uff2c\uff25.com"
        row = self.invite(full)
        self.assertEqual(row.email, "grace@example.com", "backend/drop_core/teams.py: fold the address before storing it")
        refusal(self, "already_waiting", self.admin, "grace@example.com")
        refusal(self, "already_waiting", self.admin, full)
        Invitation.objects.update(declined_at=timezone.now())
        refusal(self, "declined_recently", self.admin, full)
        for n in range(INVITES_PER_ADDRESS_PER_DAY - 1):
            _, admin = team_of(f"t{n}")
            invite(admin, full if n else "grace@example.com", build_url=build_url)
        refusal(self, "address_rate_limited", team_of("last")[1], full)

    def test_a_member_stored_in_another_form_cannot_be_invited(self):
        second_member(self.workspace, "grace", "grace@\uff25\uff38\uff21\uff2d\uff30\uff2c\uff25.com")
        refusal(self, "already_on_team", self.admin, "grace@example.com")

    def test_someone_on_the_team_cannot_be_invited(self):
        member = second_member(self.workspace, "grace", "grace@example.com")
        refusal(self, "already_on_team", self.admin, "ada@example.com")
        refusal(self, "already_on_team", self.admin, member.user.email.upper())

    def test_a_second_invitation_to_one_address_is_refused(self):
        self.invite()
        refusal(self, "already_waiting", self.admin, "grace@example.com")

    def test_only_the_administrator_can_invite(self):
        member = second_member(self.workspace, "grace")
        with self.assertRaises(PermissionDenied):
            self.invite("x@example.com", member)
        self.assertEqual(Invitation.objects.count(), 0)

    def test_an_unverified_inviter_is_refused(self):
        EmailVerification.objects.filter(user=self.admin.user).update(is_verified=False)
        refusal(self, "email_verification_required", self.admin, "grace@example.com")
        self.assertEqual(len(mail.outbox), 0)

    def test_a_free_plan_has_no_seat_to_offer(self):
        _, admin = team_of("bob", Workspace.PLAN_FREE)
        refusal(self, "no_free_seat", admin, "grace@example.com")
        self.assertEqual(len(mail.outbox), 0)

    def test_a_suspended_team_cannot_invite(self):
        Workspace.objects.filter(pk=self.workspace.pk).update(suspended_at=timezone.now())
        refusal(self, "workspace_suspended", self.admin, "grace@example.com")

    def test_a_failed_send_frees_the_seat_and_spends_no_limit(self):
        for error in (SMTPException("down"), ValueError("bad")):
            with mock.patch("drop_core.teams.send_mail", side_effect=error):
                refusal(self, "send_failed", self.admin, "grace@example.com")
            self.assertEqual(Invitation.objects.count(), 0, "backend/drop_core/teams.py: a failed send must delete the row")
            self.assertEqual(seats_in_use(self.workspace), 1)
        self.assertIsNotNone(self.invite())

    def test_an_apostrophe_in_an_address_survives_the_email(self):
        self.invite("o'brien@example.com")
        self.assertIn("o'brien@example.com", mail.outbox[0].to)
        self.admin.user.email = "o'neil@example.com"
        self.admin.user.save(update_fields=["email"])
        self.invite("second@example.com")
        self.assertIn("o'neil@example.com invited you", mail.outbox[1].body,
                      "backend/templates/registration/invitation_email.txt: must use autoescape off")

    def test_a_token_in_a_path_is_hidden_from_logs(self):
        self.assertNotIn("abc", redact_path("/accounts/invitations/abc"))

    def test_find_invitation_answers_only_for_an_open_unexpired_token(self):
        self.invite()
        token = re.search(r"/accounts/invitations/([^/\s]+)/", mail.outbox[0].body).group(1)
        row = find_invitation(token)
        self.assertEqual(row.email, "grace@example.com")
        self.assertIsNone(find_invitation("wrong"))
        Invitation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(find_invitation(token))

    def test_same_address_ignores_case_and_compatibility_forms(self):
        self.assertTrue(same_address("Grace@Example.com", "grace@example.com"))
        self.assertTrue(same_address("grace@example.com", "grace@example.com"))
        self.assertFalse(same_address("grace@example.com", "ada@example.com"))

    def test_with_the_switch_off_the_helpers_answer_empty_before_any_query(self):
        row = self.invite()
        with override_settings(TEAM_SEATS=False), self.assertNumQueries(0):
            self.assertEqual((seat_limit(self.workspace), seats_in_use(self.workspace), find_invitation("x")), (None, 0, None))
            self.assertFalse(cancel_invitation(self.admin, row.pk))


@override_settings(TEAM_SEATS=True)
class InviteLockTests(TransactionTestCase):
    longMessage = False

    def test_the_last_seat_goes_to_one_invitation(self):
        workspace, admin = team_of("ada", Workspace.PLAN_TEAM)
        for n in range(3):
            invite(admin, f"p{n}@example.com", build_url=build_url)
        results = []

        def attempt(address):
            # SQLite's shared in-memory database answers "table is locked" to a second writer; Postgres waits on the row lock.
            try:
                for _ in range(100):
                    try:
                        invite(admin, address, build_url=build_url)
                        results.append("ok")
                    except OperationalError:
                        time.sleep(0.01)
                        continue
                    except InviteRefused as refused:
                        results.append(refused.code)
                    break
            finally:
                connections.close_all()

        threads = [threading.Thread(target=attempt, args=(f"x{n}@example.com",)) for n in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), ["no_free_seat", "ok"])
        self.assertEqual(seats_in_use(workspace), 5)

    def test_mail_is_sent_after_commit_and_outside_the_lock(self):
        _, admin = team_of("ada")
        seen = []
        with mock.patch("drop_core.teams.send_mail", side_effect=lambda *a, **k: seen.append(connection.in_atomic_block)):
            invite(admin, "grace@example.com", build_url=build_url)
        self.assertEqual(seen, [False], "backend/drop_core/teams.py: send_mail must run after the transaction ends")


@override_settings(TEAM_SEATS=True)
class CancelTests(TestCase):
    longMessage = False

    def test_another_teams_administrator_and_a_member_change_nothing(self):
        workspace, admin = team_of("ada")
        row = invite(admin, "grace@example.com", build_url=build_url)
        _, stranger = team_of("bob")
        member = second_member(workspace, "mo")
        for who in (stranger, member):
            self.assertFalse(cancel_invitation(who, row.pk))
        self.assertIsNone(Invitation.objects.get(pk=row.pk).revoked_at)
        self.assertFalse(cancel_invitation(admin, row.pk + 1000))
        self.assertTrue(cancel_invitation(admin, row.pk))
        self.assertFalse(cancel_invitation(admin, row.pk), "backend/drop_core/teams.py: a closed invitation must not be canceled again")
