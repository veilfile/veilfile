"""The invitation page, joining and declining.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it).
"""
import os
import re
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from . import account, teams
from .file_access import file_to_revoke
from .models import (
    APIKey,
    EmailVerification,
    Invitation,
    Membership,
    PendingUpload,
    Workspace,
    current_membership,
    get_or_create_workspace,
)
from .services import mint_upload_url, upload_artifact, workspace_storage_bytes
from .oauth import resolve_oauth_credential
from .teams import JoinRefused, accept_invitation, decline_invitation, invite, seats_in_use, why_not_join
from .tests import _use_local_dir
from .tests_fix_device import _DeviceTestBase
from .tests_members import _issue_token, _make_app, make_user, second_member
from .tests_teams import team_of

HERE = "backend/drop_core/teams.py"
VIEW = "backend/drop_core/invitation_views.py"
PAGE = "backend/templates/registration/invitation.html"

CAN_JOIN = [
    "What joining means",
    "You share the team's limits with everyone on it.",
    "Your API keys stop working, and your connected apps are disconnected. Connect your agents again with veilfile login.",
    "Files you already have keep working until they expire. Only you can see them, and they do not count toward the team's limits.",
    "The administrator chooses whether your new files are shared with the team or stay only yours.",
    "The administrator can see how much you use, but not the names of files that only you can see.",
    "If you leave the team or are removed, this account is a Free account again.",
]
INVITED = "ada@example.com invited grace@example.com to join their team on Veilfile."
REFUSALS = {
    "wrong_person": "You are signed in as lin@example.com. This invitation is for grace@example.com, so sign out and sign in with that address to accept it.",
    "email_verification_required": "Verify grace@example.com first, then open this link again.",
    "on_this_team": "You are already on this team.",
    "on_another_team": "You are already on a team. Leave it before you join another.",
    "administers_people": "You administer a team with other people on it, so you cannot join another one until you hand the role to someone else.",
    "account_suspended": "This account is suspended, so it cannot join a team. Contact support@veilfile.com.",
    "has_paid_plan": "This account has its own paid plan, so it cannot join a team yet.",
    "no_free_seat": "This team has no free seat. Ask ada@example.com to make room, then open this link again.",
}
NOT_VALID = "This invitation is no longer valid"
JOINED = "You have joined the team. Connect your agents again with veilfile login."
TOKEN = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')


def url(raw):
    return f"/accounts/invitations/{raw}/"


class Base(TestCase):
    longMessage = False

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        _use_local_dir(self, self.tmpdir)
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.team, self.admin = team_of("ada")
        self.client = Client()

    def send(self, email="grace@example.com", admin=None, team_admin=None):
        sent = []
        row = invite(team_admin or admin or self.admin, email, build_url=lambda raw: sent.append(raw) or url(raw))
        return row, sent[0]

    def sign_in(self, user):
        self.client.force_login(user)
        return user

    def page(self, raw, **extra):
        return self.client.get(url(raw), **extra).content.decode()

    def post(self, raw, action, client=None, **extra):
        return (client or self.client).post(url(raw), {"action": action}, **extra)


@override_settings(TEAM_SEATS=True)
class PageTests(Base):
    def setUp(self):
        super().setUp()
        self.invitation, self.raw = self.send()
        self.grace = make_user("grace")

    @override_settings(TEAM_SEATS=False)
    def test_it_is_the_shells_404_with_the_switch_off(self):
        response = self.client.get(url(self.raw))
        self.assertEqual(response.status_code, 404, f"{VIEW}: the page must be behind team_seats_only")
        self.assertIn("<title>Not found | Veilfile</title>", response.content.decode(), f"{VIEW}: the answer must be the shell's 404")

    def test_a_suspended_teams_link_gets_the_same_answer_as_a_bad_one(self):
        Workspace.objects.filter(pk=self.team.pk).update(suspended_at=timezone.now())
        for signed_in in (False, True):
            if signed_in:
                self.sign_in(self.grace)
            bad = self.client.get(url("not-a-token"))
            response = self.client.get(url(self.raw))
            self.assertEqual(response.status_code, 404, f"{VIEW}: a suspended team's link must answer 404 on a GET")
            self.assertEqual(response.content, bad.content, f"{VIEW}: a suspended team's link must read like a bad one")
            self.assertNotIn("What joining means", response.content.decode())

    def test_a_bad_expired_used_or_cancelled_link_gets_one_answer(self):
        expired, expired_raw = self.send("expired@example.com")
        Invitation.objects.filter(pk=expired.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        used, used_raw = self.send("used@example.com")
        Invitation.objects.filter(pk=used.pk).update(accepted_at=timezone.now())
        gone, gone_raw = self.send("gone@example.com")
        Invitation.objects.filter(pk=gone.pk).update(revoked_at=timezone.now())
        for signed_in in (False, True):
            if signed_in:
                self.sign_in(self.grace)
            answers = set()
            for raw in ("not-a-token", expired_raw, used_raw, gone_raw):
                response = self.client.get(url(raw))
                answers.add((response.status_code, response.content))
            self.assertEqual({status for status, _ in answers}, {404}, f"{VIEW}: every link that is not valid must answer 404")
            self.assertEqual(len(answers), 1, f"{VIEW}: every link that is not valid must answer with the same body")
            body = next(iter(answers))[1].decode()
            self.assertIn(NOT_VALID, body)
            self.assertIn("Ask the person who invited you for a new one.", body)

    def test_signed_out_it_offers_sign_in_sign_up_and_decline(self):
        html = self.page(self.raw)
        self.assertIn(INVITED, html)
        self.assertIn("Sign in with that address, or sign up with it, to accept.", html)
        self.assertIn("A new account must verify its address first, then open this link again.", html)
        self.assertIn(f'href="/accounts/login/?next={url(self.raw)}"', html, f"{PAGE}: Sign in must carry next=")
        self.assertIn(f'href="/accounts/signup/?next={url(self.raw)}"', html, f"{PAGE}: Sign up must carry next=")
        self.assertIn(">Decline</button>", html)
        self.assertNotIn("Join the team", html)

    def test_the_page_does_not_say_whether_an_account_exists(self):
        _, other_raw = self.send("nobody@example.com")

        def plain(raw, address):
            html = self.page(raw).replace(raw, "TOKEN").replace(address, "ADDRESS")
            return TOKEN.sub("CSRF", html)

        self.assertEqual(plain(self.raw, "grace@example.com"), plain(other_raw, "nobody@example.com"),
                         f"{VIEW}: the signed-out page must be the same for an address with an account and one without")

    def test_signed_in_as_someone_else_it_says_so_and_offers_no_join(self):
        self.sign_in(make_user("lin"))
        html = self.page(self.raw)
        self.assertIn(REFUSALS["wrong_person"], html)
        self.assertNotIn("Join the team", html)
        self.assertIn(">Sign out</button>", html)

    def test_an_unverified_address_is_asked_to_verify_and_offers_no_join(self):
        EmailVerification.objects.filter(user=self.grace).update(is_verified=False, verified_at=None)
        self.sign_in(self.grace)
        html = self.page(self.raw)
        self.assertIn(REFUSALS["email_verification_required"], html)
        self.assertIn('href="/accounts/verify-email/">Verify email</a>', html)
        self.assertNotIn("Join the team", html)

    def test_a_pending_change_of_address_does_not_count(self):
        EmailVerification.objects.filter(user=self.grace).update(email="grace@example.com", is_verified=False, verified_at=None)
        type(self.grace).objects.filter(pk=self.grace.pk).update(email="elsewhere@example.com")
        self.grace.refresh_from_db()
        self.sign_in(self.grace)
        self.assertEqual(why_not_join(self.grace, self.invitation), "wrong_person", f"{HERE}: user.email must match the invitation")
        self.assertNotIn("Join the team", self.page(self.raw))

    def test_sign_out_returns_here(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(make_user("lin"))
        html = client.get(url(self.raw)).content.decode()
        token = TOKEN.search(html).group(1)
        self.assertIn(f'<input type="hidden" name="next" value="{url(self.raw)}">', html)
        response = client.post("/accounts/logout/", {"csrfmiddlewaretoken": token, "next": url(self.raw)})
        self.assertEqual((response.status_code, response["Location"]), (302, url(self.raw)), f"{PAGE}: Sign out must come back to the page")
        self.assertIn("Sign in with that address", client.get(url(self.raw)).content.decode(), "the person is signed out")

    def test_every_refusal_offers_decline(self):
        EmailVerification.objects.filter(user=self.grace).update(is_verified=False)
        for user in (None, make_user("lin"), self.grace):
            with self.subTest(user=user and user.username):
                if user:
                    self.sign_in(user)
                self.assertIn(">Decline</button>", self.page(self.raw))

    def test_a_person_who_can_join_reads_what_it_means(self):
        self.sign_in(self.grace)
        html = self.page(self.raw)
        for sentence in CAN_JOIN:
            self.assertIn(sentence, html, f"{PAGE}: the Can join row of the Words table must be on the page")
        self.assertIn(">Join the team</button>", html)
        self.assertIn(">Decline</button>", html)
        self.assertIn(INVITED, html)

    def refused(self, code):
        self.grace = type(self.grace).objects.get(pk=self.grace.pk)  # a fresh read, as a request has
        self.sign_in(self.grace)
        html = self.page(self.raw)
        self.assertEqual(why_not_join(self.grace, self.invitation), code, f"{HERE}: why_not_join must give {code}")
        self.assertIn(REFUSALS[code], html, f"{PAGE}: the sentence for {code}")
        self.assertNotIn("Join the team", html)
        for other, sentence in REFUSALS.items():
            if other != code:
                self.assertNotIn(sentence, html)

    def test_wrong_person(self):
        self.sign_in(make_user("lin"))
        self.assertEqual(why_not_join(make_user("lin2"), self.invitation), "wrong_person")
        self.assertIn(REFUSALS["wrong_person"], self.page(self.raw))

    def test_email_verification_required(self):
        EmailVerification.objects.filter(user=self.grace).update(is_verified=False)
        self.refused("email_verification_required")

    def test_on_this_team(self):
        Membership.objects.create(workspace=self.team, user=self.grace, role=Membership.MEMBER)
        self.refused("on_this_team")

    def test_on_another_team(self):
        other, _ = team_of("lin")
        Membership.objects.create(workspace=other, user=self.grace, role=Membership.MEMBER)
        self.refused("on_another_team")

    def test_administers_people(self):
        own = get_or_create_workspace(self.grace)
        second_member(own, "friend")
        self.refused("administers_people")

    def test_account_suspended(self):
        own = get_or_create_workspace(self.grace)
        Workspace.objects.filter(pk=own.pk).update(suspended_at=timezone.now())
        self.refused("account_suspended")

    def test_has_paid_plan(self):
        own = get_or_create_workspace(self.grace)
        Workspace.objects.filter(pk=own.pk).update(plan=Workspace.PLAN_TEAM)
        self.refused("has_paid_plan")

    def test_a_subscription_alone_counts_as_a_paid_plan(self):
        own = get_or_create_workspace(self.grace)
        Workspace.objects.filter(pk=own.pk).update(stripe_subscription_id="sub_123")
        self.refused("has_paid_plan")

    def test_no_free_seat(self):
        for name in ("a", "b", "c", "d"):
            second_member(self.team, name)
        self.refused("no_free_seat")

    def test_a_get_changes_nothing(self):
        own = get_or_create_workspace(self.grace)
        before = (Membership.objects.count(), Workspace.objects.count(), Invitation.objects.values_list("accepted_at", "declined_at"))
        for user in (None, self.grace):
            if user:
                self.sign_in(user)
            self.client.get(url(self.raw))
        after = (Membership.objects.count(), Workspace.objects.count(), Invitation.objects.values_list("accepted_at", "declined_at"))
        self.assertEqual(list(before[2]), list(after[2]))
        self.assertEqual(before[:2], after[:2], f"{VIEW}: a GET must write nothing")
        self.assertTrue(Membership.objects.get(workspace=own, user=self.grace).removed_at is None)

    def test_it_is_not_indexed_or_cached(self):
        for raw in (self.raw, "not-a-token"):
            response = self.client.get(url(raw))
            self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow", f"{VIEW}: the page must send X-Robots-Tag")
            self.assertEqual(response["Cache-Control"], "no-store", f"{VIEW}: the page must send Cache-Control: no-store")

    def test_a_post_without_its_token_is_form_not_sent(self):
        client = Client(enforce_csrf_checks=True)
        with self.assertLogs("django.security.csrf", level="WARNING"):
            response = client.post(url(self.raw), {"action": "decline"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("Form not sent", response.content.decode())
        self.assertIsNone(Invitation.objects.get(pk=self.invitation.pk).declined_at)


@override_settings(TEAM_SEATS=True)
class JoinTests(_DeviceTestBase):
    """The person is `dev`, who owns a Free workspace holding one key, one file, one unused upload URL, one approved device
    login and one OAuth token."""

    longMessage = False

    def setUp(self):
        super().setUp()
        self.team, self.admin = team_of("ada")
        self.person = self.user
        self.old = self.workspace
        self.old_member = current_membership(self.person)
        # The login is approved before the key is made, because a Free account holds one key and approving needs room for another.
        self.grant = self._start()
        self.assertEqual(self._approve(self.grant).status_code, 200)
        self.old_key, self.old_raw = APIKey.create_key(self.old, "old", member=self.old_member)
        self.artifact = upload_artifact(self.old, self.old_key, "a.txt", b"hello", None, member=self.old_member)
        self.pending = mint_upload_url(self.old, self.old_key, "b.txt", member=self.old_member)
        self.token_raw, _, _ = _issue_token(self.person, _make_app())
        self.sent = []
        self.invitation = invite(self.admin, self.person.email, build_url=lambda raw: self.sent.append(raw) or url(raw))
        self.raw = self.sent[0]
        self.web = Client()
        self.web.force_login(self.person)

    def join(self, client=None):
        return (client or self.web).post(url(self.raw), {"action": "accept"})

    def test_joining_moves_the_person_to_the_team(self):
        response = self.join()
        self.assertEqual((response.status_code, response["Location"]), (302, "/app/"), f"{VIEW}: a join must go to Overview")
        row = current_membership(self.person)
        self.assertEqual((row.workspace_id, row.role), (self.team.pk, Membership.MEMBER))
        self.assertEqual((row.upload_allowance, row.storage_allowance_bytes, row.key_allowance), (None, None, None))
        self.assertIsNotNone(Invitation.objects.get(pk=self.invitation.pk).accepted_at)
        self.assertEqual(Membership.objects.filter(user=self.person, removed_at__isnull=True).count(), 1)

    def test_joining_stops_everything_the_old_account_had(self):
        self.join()
        self.assertIsNone(APIKey.verify(self.old_raw), f"{HERE}: joining must stop the old keys")
        self.assertFalse(APIKey.objects.get(pk=self.old_key.pk).is_active)
        self.assertTrue(PendingUpload.objects.get(pk=self.pending.pk).used, f"{HERE}: joining must use up the old upload URLs")
        poll = self._poll(self.grant)
        self.assertEqual((poll.status_code, poll.json()["error"]), (400, "access_denied"), "an approved device login must be refused")
        self.assertIsNone(resolve_oauth_credential(self.token_raw), f"{HERE}: joining must revoke the OAuth grants")

    def test_an_upload_in_flight_is_refused_after_joining(self):
        self.join()
        with self.assertRaises(ValueError) as caught:
            upload_artifact(self.old, None, "c.txt", b"late", None, member=self.old_member)
        self.assertEqual(str(caught.exception), "member_removed", f"{HERE}: the old administrator row must be closed")

    def test_old_files_keep_working_and_stay_the_persons(self):
        self.join()
        self.assertEqual(Client().get(f"/a/{self.artifact.token}").status_code, 200)
        row = current_membership(self.person)
        self.assertEqual(file_to_revoke(row, self.artifact.id), self.artifact, "the person must still revoke the file")
        self.assertIsNone(file_to_revoke(self.admin, self.artifact.id), "the administrator must not see the file")
        self.assertEqual(workspace_storage_bytes(self.team), 0, "the team must not count the file")

    def test_a_new_key_lands_on_the_team(self):
        self.join()
        answer = account.create_key(self.person, "agent")
        self.assertEqual(answer.status, 201)
        key = APIKey.verify(answer.body["api_key"])
        self.assertEqual((key.workspace_id, key.member.user_id, key.member.workspace_id), (self.team.pk, self.person.pk, self.team.pk))

    def test_joining_cancels_invitations_the_old_workspace_sent(self):
        waiting = Invitation.objects.create(
            workspace=self.old, email="x@example.com", token_hash="h" * 64, invited_by=self.person,
            expires_at=timezone.now() + timedelta(days=1))
        self.join()
        self.assertIsNotNone(Invitation.objects.get(pk=waiting.pk).revoked_at, f"{HERE}: joining must cancel the old workspace's invitations")

    def test_a_forwarded_link_cannot_join_as_someone_else(self):
        lin = make_user("lin")
        client = Client()
        client.force_login(lin)
        response = self.join(client)
        self.assertEqual(response.status_code, 400)
        self.assertIn(REFUSALS["wrong_person"].replace("grace@", "dev@"), response.content.decode())
        self.assertIsNone(Invitation.objects.get(pk=self.invitation.pk).accepted_at)
        self.assertFalse(Membership.objects.filter(user=lin, workspace=self.team).exists())

    def test_an_unverified_account_cannot_join(self):
        EmailVerification.objects.filter(user=self.person).update(is_verified=False)
        response = self.join()
        self.assertEqual(response.status_code, 400)
        self.assertIn("Verify dev@example.com first", response.content.decode())
        self.assertTrue(APIKey.objects.get(pk=self.old_key.pk).is_active)
        self.assertIsNone(Invitation.objects.get(pk=self.invitation.pk).accepted_at)

    def test_accepting_twice_is_harmless(self):
        self.join()
        second = self.join()
        self.assertEqual((second.status_code, second["Location"]), (302, "/app/"))
        self.assertEqual(Membership.objects.filter(user=self.person, removed_at__isnull=True).count(), 1)

    def test_a_double_click_lands_on_overview(self):
        self.join()
        second = self.join()
        self.assertEqual((second.status_code, second["Location"]), (302, "/app/"), f"{VIEW}: a second accept must redirect")
        page = self.web.get("/app/").content.decode()
        self.assertEqual(page.count(JOINED), 1, f"{VIEW}: the notice must show once")

    def test_someone_else_with_a_used_link_still_gets_not_valid(self):
        self.join()
        lin = make_user("lin")
        client = Client()
        client.force_login(lin)
        response = self.join(client)
        self.assertEqual(response.status_code, 404)
        self.assertIn(NOT_VALID, response.content.decode())

    def test_a_person_ends_on_one_team_after_two_accepts(self):
        other, other_admin = team_of("lin")
        sent = []
        invite(other_admin, self.person.email, build_url=lambda raw: sent.append(raw) or url(raw))
        self.join()
        response = self.web.post(url(sent[0]), {"action": "accept"})
        self.assertEqual(response.status_code, 400)
        self.assertIn(REFUSALS["on_another_team"], response.content.decode())
        self.assertEqual(list(Membership.objects.filter(user=self.person, removed_at__isnull=True).values_list("workspace_id", flat=True)), [self.team.pk])
        self.assertFalse(Membership.objects.filter(user=self.person, workspace=other).exists())

    def test_a_person_with_a_closed_row_on_this_team_comes_back(self):
        row = Membership.objects.create(
            workspace=self.team, user=self.person, role=Membership.MEMBER, removed_at=timezone.now(),
            upload_allowance=3, storage_allowance_bytes=5, key_allowance=2)
        old, _ = APIKey.create_key(self.team, "before", member=row)
        APIKey.objects.filter(pk=old.pk).update(is_active=False)
        self.join()
        rows = Membership.objects.filter(user=self.person, workspace=self.team)
        self.assertEqual(rows.count(), 1, f"{HERE}: a closed row must be reopened, not duplicated")
        row = rows.get()
        self.assertEqual((row.removed_at, row.role, row.upload_allowance, row.storage_allowance_bytes, row.key_allowance),
                         (None, Membership.MEMBER, None, None, None))
        self.assertFalse(APIKey.objects.get(pk=old.pk).is_active, f"{HERE}: the old keys of a reopened row stay inactive")

    def test_an_expired_or_cancelled_invitation_cannot_be_accepted_at_the_last_moment(self):
        for change in ({"expires_at": timezone.now() - timedelta(seconds=1)}, {"revoked_at": timezone.now()}):
            with self.subTest(change=list(change)):
                Invitation.objects.filter(pk=self.invitation.pk).update(**change)
                with self.assertRaises(JoinRefused) as caught:
                    accept_invitation(self.person, self.raw)
                self.assertEqual(caught.exception.code, "not_valid", f"{HERE}: accept_invitation must re-read the invitation under the lock")
                self.assertTrue(APIKey.objects.get(pk=self.old_key.pk).is_active)
                Invitation.objects.filter(pk=self.invitation.pk).update(expires_at=timezone.now() + timedelta(days=1), revoked_at=None)

    def test_a_suspended_team_cannot_be_joined(self):
        Workspace.objects.filter(pk=self.team.pk).update(suspended_at=timezone.now())
        response = self.join()
        self.assertEqual(response.status_code, 404)
        self.assertIn(NOT_VALID, response.content.decode())
        self.assertTrue(APIKey.objects.get(pk=self.old_key.pk).is_active)

    def test_a_suspended_person_cannot_join(self):
        Workspace.objects.filter(pk=self.old.pk).update(suspended_at=timezone.now())
        response = self.join()
        self.assertEqual(response.status_code, 400)
        self.assertIn(REFUSALS["account_suspended"], response.content.decode())
        self.assertIsNone(Invitation.objects.get(pk=self.invitation.pk).accepted_at)
        self.assertTrue(APIKey.objects.get(pk=self.old_key.pk).is_active)
        self.assertFalse(Membership.objects.filter(user=self.person, workspace=self.team).exists())

    def test_a_forced_integrity_error_is_refused_not_a_500(self):
        with mock.patch.object(Membership.objects, "create", side_effect=IntegrityError("membership_one_team")):
            response = self.join()
        self.assertEqual(response.status_code, 400)
        self.assertIn(REFUSALS["on_another_team"], response.content.decode(), f"{HERE}: an IntegrityError must answer on_another_team")
        self.assertTrue(APIKey.objects.get(pk=self.old_key.pk).is_active, "the failed join must roll back")
        self.assertEqual(Membership.objects.filter(user=self.person, removed_at__isnull=True).count(), 1)
        self.assertEqual(self.join().status_code, 302, "the transaction must not be poisoned")

    def test_the_lock_set_is_rechecked(self):
        real = teams._current_workspace_ids
        calls = []

        def stale_first(user):
            calls.append(1)
            return [] if len(calls) == 1 else real(user)

        with mock.patch.object(teams, "_current_workspace_ids", side_effect=stale_first):
            row = accept_invitation(self.person, self.raw)
        self.assertEqual(row.workspace_id, self.team.pk)
        self.assertEqual(len(calls), 4, f"{HERE}: a current row that appears after the first read must cause a retry under the new lock set")
        self.assertIsNone(APIKey.verify(self.old_raw))

    def test_the_person_lands_on_overview_with_the_notice(self):
        response = self.web.post(url(self.raw), {"action": "accept"}, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], "/app/")
        self.assertIn(JOINED, response.content.decode(), f"{VIEW}: the notice of the Words table")


@override_settings(TEAM_SEATS=True)
class DeclineTests(Base):
    def setUp(self):
        super().setUp()
        self.invitation, self.raw = self.send()

    def test_declining_needs_only_the_link(self):
        response = self.post(self.raw, "decline")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("You declined the invitation", html)
        self.assertIn("Nothing has changed on your account.", html)
        self.assertIsNotNone(Invitation.objects.get(pk=self.invitation.pk).declined_at, f"{HERE}: decline_invitation must set declined_at")
        self.assertEqual(seats_in_use(self.team), 1, "a declined invitation must free its seat")
        self.assertEqual(self.client.get(url(self.raw)).status_code, 404)

    def test_declining_touches_nothing_else(self):
        other, _ = self.send("lin@example.com")
        grace = make_user("grace")
        key, _ = APIKey.create_key(get_or_create_workspace(grace), "k")
        before = list(Membership.objects.values_list("pk", "removed_at"))
        self.post(self.raw, "decline")
        self.assertIsNone(Invitation.objects.get(pk=other.pk).declined_at)
        self.assertTrue(APIKey.objects.get(pk=key.pk).is_active)
        self.assertEqual(list(Membership.objects.values_list("pk", "removed_at")), before)

    def test_declining_works_in_a_real_browser_session(self):
        client = Client(enforce_csrf_checks=True)
        token = TOKEN.search(client.get(url(self.raw)).content.decode()).group(1)
        response = client.post(url(self.raw), {"action": "decline", "csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 200, f"{PAGE}: the decline form must carry the CSRF token")
        self.assertIsNotNone(Invitation.objects.get(pk=self.invitation.pk).declined_at)

    def test_declining_cannot_undo_an_acceptance(self):
        grace = make_user("grace")
        self.sign_in(grace)
        self.assertEqual(self.post(self.raw, "accept").status_code, 302)
        response = self.post(self.raw, "decline")
        self.assertEqual(response.status_code, 404)
        row = Invitation.objects.get(pk=self.invitation.pk)
        self.assertEqual((row.declined_at, row.accepted_at is None), (None, False), f"{HERE}: decline_invitation must touch only a waiting row")
        self.assertFalse(decline_invitation(self.raw))
