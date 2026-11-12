"""The Members page (spec 4.8, team seats): who is on the team, what each person uses, the invitations, allowances and the file setting.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it).
"""
import re
from datetime import timedelta
from html import unescape
from unittest import mock

from django.core import mail
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from dashboard import members
from dashboard.tests_keys import TableRows, hidden_token  # noqa: F401
from drop_core import plans, teams
from drop_core.models import (
    FILE_VISIBILITY_ALWAYS_ONLY_ME,
    FILE_VISIBILITY_ALWAYS_TEAM,
    FILE_VISIBILITY_MEMBERS_CHOOSE,
    VISIBILITY_ONLY_ME,
    VISIBILITY_TEAM,
    APIKey,
    Artifact,
    EmailVerification,
    Invitation,
    Membership,
    MemberUsageCounter,
    Workspace,
)
from drop_core.plans import PLANS
from drop_core.tests_members import second_member
from drop_core.tests_teams import team_of
from pages import tests_forms_on_the_shell as form_pages
from pages.tests_forms import tags

VIEW = "backend/dashboard/members.py"
PAGE = "backend/templates/dashboard/members.html"
ALLOWANCE_PAGE = "backend/templates/dashboard/member_allowance.html"
CANCEL_PAGE = "backend/templates/dashboard/invitation_cancel.html"
REMOVE_PAGE = "backend/templates/dashboard/member_remove.html"
URL = "/app/members/"
SETTING_URL = "/app/members/visibility/"
MAX_INT = 2_147_483_647
SENTENCES = {
    "email_verification_required": "Verify your email before you invite anyone.",
    "no_free_seat": "Every seat is in use, so no invitation was sent. Cancel a waiting invitation, or change plans.",
    "already_on_team": "That person is already on this team.",
    "already_waiting": "An invitation to that address is already waiting. Cancel it to send a new one.",
    "team_rate_limited": "This team has sent as many invitations as it can for now. Try again later.",
    "address_rate_limited": "That address has had too many invitations today. Try again tomorrow.",
    "too_many_waiting": "This team has too many invitations waiting. Cancel some to send more.",
    "declined_recently": "That address declined an invitation from this team recently, so no invitation was sent.",
    "site_rate_limited": "Invitations are paused for now. Try again tomorrow.",
    "send_failed": "The invitation could not be sent. Try again in a moment.",
    "workspace_suspended": "This account is suspended, so no invitation was sent. Contact support@veilfile.com.",
}


def visible(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html[html.index("<main"):html.index("</main>")]).split())


def live_file(row, name, size=10, days=3, visibility=VISIBILITY_ONLY_ME):
    return Artifact.objects.create(
        workspace=row.workspace, member=row, filename=name, mime_type="text/plain", size_bytes=size,
        expires_at=timezone.now() + timedelta(days=days), content_sha256="0" * 64, visibility=visibility,
    )


@override_settings(TEAM_SEATS=True)
class MembersCase(TestCase):
    longMessage = False

    def setUp(self):
        self.workspace, self.admin = team_of("ada")
        self.client.force_login(self.admin.user)

    def get(self, url=URL):
        return self.client.get(url)

    def rows(self):
        return TableRows(self.get().content.decode()).rows


class AccessTests(MembersCase):
    def setUp(self):
        super().setUp()
        self.member = second_member(self.workspace, "lin")
        self.invitation = teams.invite(self.admin, "grace@example.com", build_url=lambda raw: f"https://veilfile.test/{raw}/")

    def addresses(self):
        return [
            (URL, ("get", "post")),
            (f"{URL}{self.member.pk}/allowance/", ("get", "post")),
            (f"{URL}invitations/{self.invitation.pk}/cancel/", ("get", "post")),
            (f"{URL}{self.member.pk}/remove/", ("get", "post")),
            (f"{URL}{self.member.pk}/administrator/", ("get", "post")),
            (f"{URL}administrator/cancel/", ("get", "post")),
            (SETTING_URL, ("post",)),
        ]

    def test_every_address_is_the_shells_404_with_the_switch_off(self):
        addresses = self.addresses()
        with override_settings(TEAM_SEATS=False):
            for client in (self.client, Client()):
                for url, methods in addresses:
                    for method in methods:
                        with self.subTest(url=url, method=method, signed_in=client is self.client):
                            response = getattr(client, method)(url)
                            self.assertEqual(response.status_code, 404, f"{VIEW}: team_seats_only wraps each view outside dashboard_view")
                            self.assertIn("Not found", response.content.decode())

    def test_signed_out_it_goes_to_sign_in(self):
        for url, methods in self.addresses():
            for method in methods:
                with self.subTest(url=url, method=method):
                    response = getattr(Client(), method)(url)
                    self.assertEqual((response.status_code, response.get("Location")), (302, f"/accounts/login/?next={url}"))

    def test_a_member_and_a_free_administrator_get_the_shells_404(self):
        free, free_admin = team_of("kay", plan=Workspace.PLAN_FREE)
        for user in (self.member.user, free_admin.user):
            client = Client()
            client.force_login(user)
            for url, methods in self.addresses():
                for method in methods:
                    with self.subTest(user=user.username, url=url, method=method):
                        self.assertEqual(getattr(client, method)(url).status_code, 404, f"{VIEW}: only an administrator of a team with these pages")

    def test_another_teams_member_or_invitation_is_the_shells_404(self):
        other, other_admin = team_of("kay")
        stranger = second_member(other, "mo")
        invitation = teams.invite(other_admin, "zed@example.com", build_url=lambda raw: f"https://veilfile.test/{raw}/")
        for url in (f"{URL}{stranger.pk}/allowance/", f"{URL}invitations/{invitation.pk}/cancel/"):
            for method in ("get", "post"):
                with self.subTest(url=url, method=method):
                    self.assertEqual(getattr(self.client, method)(url, {"uploads": "1", "storage_gb": "", "keys": ""}).status_code, 404)
        stranger.refresh_from_db()
        invitation.refresh_from_db()
        self.assertEqual((stranger.upload_allowance, invitation.revoked_at), (None, None), f"{VIEW}: another team's rows are never changed")

    def test_a_post_without_its_token_is_form_not_sent(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin.user)
        for url in (URL, f"{URL}{self.member.pk}/allowance/", SETTING_URL):
            with self.subTest(url=url), self.assertLogs("django.security.csrf", level="WARNING"):
                response = client.post(url, {"email": "grace@example.com", "uploads": "1", "file_visibility": FILE_VISIBILITY_ALWAYS_TEAM})
            self.assertEqual(response.status_code, 403)
            self.assertIn("Form not sent", response.content.decode())
        self.assertEqual(Invitation.objects.count(), 1, "only the invitation made in setUp")

    def test_the_invite_form_works_with_the_token_the_page_carries(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin.user)
        html = client.get(URL).content.decode()
        start = html.index(f'action="{URL}"')
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html[start:html.index("</form>", start)]).group(1)
        response = client.post(URL, {"email": "zed@example.com", "csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 302, f"{PAGE}: the invite form carries {{% csrf_token %}}")
        self.assertEqual(Invitation.objects.count(), 2)


class ListTests(MembersCase):
    def test_it_lists_each_person_with_their_use_and_allowance(self):
        member = second_member(self.workspace, "lin")
        Membership.objects.filter(pk=member.pk).update(upload_allowance=10, storage_allowance_bytes=2 * plans.GB, key_allowance=0)
        MemberUsageCounter.objects.create(member=member, year_month=timezone.now().strftime("%Y-%m"), uploads=4)
        live_file(member, "a.txt", size=2048)
        APIKey.create_key(self.workspace, "k", member=member)
        rows = self.rows()
        self.assertEqual([row["Person"] for row in rows], ["ada@example.com", "lin@example.com"], f"{PAGE}: one row per person, the administrator first")
        self.assertEqual(rows[0]["Role"], "Administrator")
        lin = rows[1]
        self.assertEqual(
            (lin["Role"], lin["Uploads this month"], lin["Storage"], lin["API keys"]),
            ("Member", "4 of 10", "2.0 KB of 2.0 GB", "1 of 0"),
        )
        self.assertEqual(rows[0]["Uploads this month"], "0")

    def test_it_counts_only_files_still_live(self):
        member = second_member(self.workspace, "lin")
        live_file(member, "live.txt", size=100)
        live_file(member, "gone.txt", size=5000, days=-1)
        self.assertEqual(self.rows()[1]["Storage"], "100 B")

    def test_no_file_name_appears_on_the_page(self):
        member = second_member(self.workspace, "lin")
        artifact = live_file(member, "secret-plans.txt")
        self.assertTrue(Artifact.objects.filter(filename="secret-plans.txt", visibility=VISIBILITY_ONLY_ME).exists(), "the fixture holds an only-me file")
        html = self.get().content.decode()
        self.assertNotIn("secret-plans", html, f"{VIEW}: the Members page reads counts and sums, never a file name")
        self.assertNotIn(artifact.token, html, f"{VIEW}: nor a file's link")

    def test_the_held_count_shows_without_a_name(self):
        member = second_member(self.workspace, "lin")
        removed = second_member(self.workspace, "gone")
        Membership.objects.filter(pk=removed.pk).update(removed_at=timezone.now())
        for owner, name in ((member, "secret-plans.txt"), (removed, "other-secret.txt")):
            Artifact.objects.filter(pk=live_file(owner, name).pk).update(safety_status="held")
        text = visible(self.get().content.decode())
        self.assertIn("Veilfile has held 2 files that people on this team uploaded. This page does not say which ones, or whose.", text)
        for hidden in ("secret-plans", "other-secret", "lin@example.com held"):
            self.assertNotIn(hidden, text, f"{PAGE}: a hold notice names no file and no person")
        Artifact.objects.filter(filename="other-secret.txt").delete()
        self.assertIn("Veilfile has held 1 file that people on this team uploaded. This page does not say which one, or whose.", visible(self.get().content.decode()))

    def test_no_held_line_when_nothing_is_held(self):
        member = second_member(self.workspace, "lin")
        live_file(member, "plain.txt")
        Artifact.objects.filter(pk=live_file(member, "old.txt", days=-1).pk).update(safety_status="held")
        self.assertNotIn("Veilfile has held", self.get().content.decode())

    def test_it_runs_one_query_per_figure(self):
        second_member(self.workspace, "lin")
        self.get()  # the first request warms caches that later ones do not pay for
        two = self._count()
        for name in ("a", "b", "c", "d"):
            member = second_member(self.workspace, name)
            live_file(member, f"{name}.txt")
            APIKey.create_key(self.workspace, name, member=member)
        self.assertEqual(self._count(), two, f"{VIEW}: member_rows asks once for each figure across the team, never once per person")

    def _count(self):
        with CaptureQueriesContext(connection) as queries:
            self.get()
        return len(queries)

    def test_it_states_seats_from_the_plan(self):
        for plan in ("team", "team_plus"):
            Workspace.objects.filter(pk=self.workspace.pk).update(plan=plan)
            text = visible(self.get().content.decode())
            seats = PLANS[plan]["seats"]
            self.assertIn(f"1 of {seats} seats is in use, counting invitations that are still waiting.", text)
        second_member(self.workspace, "lin")
        self.assertIn("2 of 10 seats are in use, counting invitations that are still waiting.", visible(self.get().content.decode()))
        Workspace.objects.filter(pk=self.workspace.pk).update(plan="scale")
        self.assertIn("2 seats are in use, and this plan has no limit on seats.", visible(self.get().content.decode()))

    def test_an_allowance_above_the_plans_limit_shows_as_capped(self):
        member = second_member(self.workspace, "lin")
        limit = PLANS["team"]["uploads"]
        Membership.objects.filter(pk=member.pk).update(upload_allowance=limit + 5)
        cell = self.rows()[1]["Uploads this month"]
        self.assertEqual(cell, f"0 of {limit + 5}, capped at {limit} by the team's limit", f"{PAGE}: an allowance above the plan's limit says so")

    def test_zero_is_a_real_allowance(self):
        member = second_member(self.workspace, "lin")
        Membership.objects.filter(pk=member.pk).update(upload_allowance=0, storage_allowance_bytes=0, key_allowance=0)
        row = self.rows()[1]
        self.assertEqual((row["Uploads this month"], row["Storage"], row["API keys"]), ("0 of 0", "0 B of 0 B", "0 of 0"))

    def test_it_states_the_allowance_note_and_the_setting(self):
        text = visible(self.get().content.decode())
        for words in (
            "An allowance is the most one person can use. The team's limits still apply to everyone together.",
            "Invite someone", "They get an email with a link that works for 7 days.", "Who can see new files",
            "Each person decides", "New files start as only them.", "Everyone on the team", "Only the person who uploaded them",
            "This applies to new files. Files that already exist keep the visibility they have.",
        ):
            self.assertIn(words, text, f"{PAGE}: the owner's words, verbatim")

    def test_every_table_has_a_caption_and_data_labels(self):
        teams.invite(self.admin, "grace@example.com", build_url=lambda raw: f"https://veilfile.test/{raw}/")
        second_member(self.workspace, "lin")
        html = self.get().content.decode()
        self.assertEqual(html.count("<table"), 2)
        self.assertEqual(html.count("<caption"), 2, f"{PAGE}: each table has a caption")
        for cell in re.findall(r"<td([^>]*)>", html):
            self.assertIn("data-label=", cell, f"{PAGE}: every cell has a data-label")

    def test_waiting_invitations_list_with_a_cancel_link(self):
        invitation = teams.invite(self.admin, "grace@example.com", build_url=lambda raw: f"https://veilfile.test/{raw}/")
        html = self.get().content.decode()
        self.assertIn("Waiting invitations", html)
        waiting = TableRows(html).rows[-1]
        self.assertEqual(waiting["Address"], "grace@example.com")
        self.assertEqual(waiting["links"], [f"{URL}invitations/{invitation.pk}/cancel/"])


class InviteTests(MembersCase):
    def test_inviting_sends_and_says_so(self):
        response = self.client.post(URL, {"email": "grace@example.com"}, follow=True)
        self.assertEqual(response.redirect_chain, [(URL, 302)])
        self.assertIn("The invitation is on its way to grace@example.com.", visible(response.content.decode()))
        self.assertEqual((Invitation.objects.count(), len(mail.outbox)), (1, 1))
        self.assertIn("/accounts/invitations/", mail.outbox[0].body)

    def test_each_refusal_shows_its_sentence_with_a_400(self):
        for code, sentence in SENTENCES.items():
            with self.subTest(code=code):
                with mock.patch.object(members, "invite", side_effect=teams.InviteRefused(code)):
                    response = self.client.post(URL, {"email": "grace@example.com"})
                self.assertEqual(response.status_code, 400)
                html = response.content.decode()
                self.assertIn(f"<p>{sentence}</p>", html[html.index('class="form-errors"'):], f"{VIEW}: REFUSALS says {code}")

    def test_every_refusal_code_has_a_sentence(self):
        self.assertEqual(set(members.REFUSALS), set(SENTENCES), f"{VIEW}: REFUSALS has a sentence for each code invite can raise")

    def test_a_bad_address_is_a_field_error(self):
        for bad in ("nobody", "", "a@b\nc@d.com"):
            with self.subTest(bad=bad):
                response = self.client.post(URL, {"email": bad})
                html = response.content.decode()
                self.assertEqual(response.status_code, 400)
                self.assertIn('class="field__error"', html, f"{PAGE}: a bad address is the field's error")
                self.assertNotIn('class="form-errors"', html)
        self.assertEqual(Invitation.objects.count(), 0)

    def test_an_address_too_long_after_folding_is_a_field_error(self):
        response = self.client.post(URL, {"email": "\u3389" * 64 + "@example.com"})
        self.assertEqual(response.status_code, 400)
        self.assertIn('class="field__error"', response.content.decode())
        self.assertEqual(Invitation.objects.count(), 0)

    def test_a_resent_form_does_not_invite_twice(self):
        self.client.post(URL, {"email": "grace@example.com"})
        response = self.client.post(URL, {"email": "grace@example.com"})
        self.assertEqual(response.status_code, 400)
        self.assertIn(SENTENCES["already_waiting"], response.content.decode())
        self.assertEqual((Invitation.objects.count(), len(mail.outbox)), (1, 1))

    def test_a_real_refusal_reaches_the_page(self):
        Workspace.objects.filter(pk=self.workspace.pk).update(suspended_at=timezone.now())
        response = self.client.post(URL, {"email": "grace@example.com"})
        self.assertEqual(response.status_code, 400)
        self.assertIn(SENTENCES["workspace_suspended"], response.content.decode())


class CancelTests(MembersCase):
    def setUp(self):
        super().setUp()
        self.invitation = teams.invite(self.admin, "grace@example.com", build_url=lambda raw: f"https://veilfile.test/{raw}/")
        self.url = f"{URL}invitations/{self.invitation.pk}/cancel/"

    def test_cancelling_asks_first_and_frees_the_seat(self):
        before = teams.seats_in_use(self.workspace)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("grace@example.com", response.content.decode())
        self.invitation.refresh_from_db()
        self.assertIsNone(self.invitation.revoked_at, f"{VIEW}: a GET changes nothing")
        response = self.client.post(self.url, follow=True)
        self.assertEqual(response.redirect_chain, [(URL, 302)])
        self.assertIn("The invitation is canceled.", visible(response.content.decode()))
        self.assertEqual(teams.seats_in_use(self.workspace), before - 1)

    def test_cancelling_works_with_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin.user)
        page = client.get(self.url).content.decode()
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page).group(1)
        response = client.post(self.url, {"csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 302)
        self.invitation.refresh_from_db()
        self.assertIsNotNone(self.invitation.revoked_at)

    def test_the_page_is_a_form_page_on_the_shell(self):
        form_pages.FormPagesAreOnTheShellTests.check(self, self.url)

    def test_a_closed_invitation_is_a_sentence_and_a_redirect(self):
        self.client.post(self.url)
        for method in ("get", "post"):
            response = getattr(self.client, method)(self.url, follow=True)
            self.assertEqual(response.redirect_chain, [(URL, 302)])
            self.assertIn("That invitation is not waiting any more.", visible(response.content.decode()))


class AllowanceTests(MembersCase):
    def setUp(self):
        super().setUp()
        self.member = second_member(self.workspace, "lin")
        self.url = f"{URL}{self.member.pk}/allowance/"

    def save(self, uploads="", storage_gb="", keys=""):
        return self.client.post(self.url, {"uploads": uploads, "storage_gb": storage_gb, "keys": keys})

    def allowances(self):
        self.member.refresh_from_db()
        return (self.member.upload_allowance, self.member.storage_allowance_bytes, self.member.key_allowance)

    def test_the_page_is_a_form_page_on_the_shell(self):
        form_pages.FormPagesAreOnTheShellTests.check(self, self.url)

    def test_the_page_names_the_person_and_prefills_zero(self):
        Membership.objects.filter(pk=self.member.pk).update(upload_allowance=0, key_allowance=3, storage_allowance_bytes=plans.GB)
        html = self.client.get(self.url).content.decode()
        self.assertIn("You are setting the allowance for lin@example.com.", visible(html))
        values = {attrs["name"]: attrs.get("value") for attrs in tags(html, "input") if attrs.get("name") in ("uploads", "storage_gb", "keys")}
        self.assertEqual(values, {"uploads": "0", "storage_gb": "1", "keys": "3"}, f"{ALLOWANCE_PAGE}: fields are prefilled with strings, because a zero is not truthy")

    def test_an_allowance_saves_and_an_empty_field_clears_it(self):
        response = self.save("10", "2", "3")
        self.assertEqual((response.status_code, response["Location"]), (302, URL))
        self.assertEqual(self.allowances(), (10, 2 * plans.GB, 3))
        followed = self.client.get(URL)
        self.save()
        self.assertEqual(self.allowances(), (None, None, None))
        self.assertIn("The allowance is saved.", visible(self.client.get(URL).content.decode()) + visible(followed.content.decode()))

    def test_gb_become_bytes(self):
        self.save(storage_gb="0.5")
        self.assertEqual(self.allowances()[1], plans.GB // 2)

    def test_zero_is_a_real_allowance(self):
        self.save("0", "0", "0")
        self.assertEqual(self.allowances(), (0, 0, 0))

    def test_bad_allowances_are_field_errors(self):
        cases = [
            {"uploads": "-1"}, {"uploads": "many"}, {"uploads": "1.5"}, {"uploads": str(MAX_INT + 1)},
            {"keys": "-1"}, {"keys": "x"}, {"keys": str(MAX_INT + 1)},
            {"storage_gb": "-1"}, {"storage_gb": "lots"}, {"storage_gb": "nan"}, {"storage_gb": "inf"}, {"storage_gb": "1e3"},
            {"storage_gb": str(2**33)}, {"storage_gb": "9" * 30},
        ]
        for case in cases:
            with self.subTest(case=case):
                response = self.save(**case)
                self.assertEqual(response.status_code, 400, f"{VIEW}: {case} is a field error, not a 500 on Postgres")
                self.assertIn('class="field__error"', response.content.decode())
        self.assertEqual(self.allowances(), (None, None, None))

    def test_the_field_errors_use_the_approved_words(self):
        # The owner approved these on 2026-11-11: the database's own limits are not numbers a person needs to read.
        for case, words in (
            ({"uploads": "many"}, "Enter a whole number, or leave this empty."),
            ({"keys": str(MAX_INT + 1)}, "Enter a whole number, or leave this empty."),
            ({"storage_gb": "lots"}, "Enter a number of GB, such as 5 or 0.5, or leave this empty."),
            ({"storage_gb": "9" * 30}, "Enter a number of GB, such as 5 or 0.5, or leave this empty."),
        ):
            with self.subTest(case=case):
                html = self.save(**case).content.decode()
                self.assertIn(words, html, f"{VIEW}: the field error for {case} is the approved sentence")
                self.assertNotIn("2,147,483,647", html)

    def test_the_largest_values_are_accepted(self):
        response = self.save(str(MAX_INT), "8589934591.99", str(MAX_INT))
        self.assertEqual(response.status_code, 302)
        self.assertLess(self.allowances()[1], 2**63)

    def test_a_removed_person_is_the_shells_404(self):
        Membership.objects.filter(pk=self.member.pk).update(removed_at=timezone.now())
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.save("1").status_code, 404)


class SettingTests(MembersCase):
    def test_the_setting_saves_and_relabels_nothing(self):
        member = second_member(self.workspace, "lin")
        artifact = live_file(member, "a.txt")
        response = self.client.post(SETTING_URL, {"file_visibility": FILE_VISIBILITY_ALWAYS_TEAM}, follow=True)
        self.assertEqual(response.redirect_chain, [(URL, 302)])
        self.assertIn("The setting is saved.", visible(response.content.decode()))
        self.workspace.refresh_from_db()
        artifact.refresh_from_db()
        self.assertEqual((self.workspace.file_visibility, artifact.visibility), (FILE_VISIBILITY_ALWAYS_TEAM, VISIBILITY_ONLY_ME))

    def test_the_page_checks_the_current_choice(self):
        Workspace.objects.filter(pk=self.workspace.pk).update(file_visibility=FILE_VISIBILITY_ALWAYS_ONLY_ME)
        html = self.get().content.decode()
        checked = [attrs["value"] for attrs in tags(html, "input") if attrs.get("name") == "file_visibility" and "checked" in attrs]
        self.assertEqual(checked, [FILE_VISIBILITY_ALWAYS_ONLY_ME])

    def test_a_bad_setting_value_is_refused(self):
        for value in ("", "everyone", "TEAM"):
            with self.subTest(value=value):
                response = self.client.post(SETTING_URL, {"file_visibility": value})
                self.assertEqual(response.status_code, 400)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.file_visibility, FILE_VISIBILITY_MEMBERS_CHOOSE)

    def test_a_get_goes_to_the_members_page(self):
        response = self.client.get(SETTING_URL)
        self.assertEqual((response.status_code, response["Location"]), (302, URL))


class HelperTests(MembersCase):
    def test_has_team_features_follows_the_one_rule(self):
        free, free_admin = team_of("kay", plan=Workspace.PLAN_FREE)
        self.assertTrue(teams.has_team_features(self.workspace), "a Team plan has more than one seat")
        self.assertFalse(teams.has_team_features(free), "a Free account alone has none")
        second_member(free, "mo")
        self.assertTrue(teams.has_team_features(free), "someone else is on it")
        with override_settings(TEAM_SEATS=False):
            self.assertFalse(teams.has_team_features(self.workspace))

    def test_with_the_switch_off_the_helpers_ask_nothing(self):
        with override_settings(TEAM_SEATS=False), self.assertNumQueries(0):
            self.assertFalse(teams.has_team_features(self.workspace))
            self.assertEqual(teams.member_rows(self.workspace), [])
            self.assertEqual(teams.waiting_invitations(self.workspace), [])
            self.assertFalse(teams.set_allowances(self.admin, self.admin.pk, uploads=1, storage_bytes=None, keys=None))
            self.assertFalse(teams.set_file_visibility_setting(self.admin, FILE_VISIBILITY_ALWAYS_TEAM))

    def test_a_member_cannot_write_allowances_or_the_setting(self):
        member = second_member(self.workspace, "lin")
        self.assertFalse(teams.set_allowances(member, member.pk, uploads=1, storage_bytes=None, keys=None))
        self.assertFalse(teams.set_file_visibility_setting(member, FILE_VISIBILITY_ALWAYS_TEAM))
        member.refresh_from_db()
        self.assertIsNone(member.upload_allowance)

    def test_an_administrator_whose_row_closed_writes_nothing(self):
        member = second_member(self.workspace, "lin")
        Membership.objects.filter(pk=self.admin.pk).update(removed_at=timezone.now())
        self.assertFalse(teams.set_allowances(self.admin, member.pk, uploads=1, storage_bytes=None, keys=None))
        self.assertFalse(teams.set_file_visibility_setting(self.admin, FILE_VISIBILITY_ALWAYS_TEAM))


class RemoveTests(MembersCase):
    def setUp(self):
        super().setUp()
        self.member = second_member(self.workspace, "lin")
        self.file = live_file(self.member, "secret-plans.txt", size=2048)
        live_file(self.member, "shared.txt", size=1024, visibility=VISIBILITY_TEAM)
        self.url = f"{URL}{self.member.pk}/remove/"

    def post(self, **fields):
        return self.client.post(self.url, fields, follow=True)

    def closed(self):
        self.member.refresh_from_db()
        return self.member.removed_at is not None

    def expiries(self):
        return [a.expires_at > timezone.now() for a in Artifact.objects.filter(member=self.member)]

    def test_removing_asks_first_and_changes_nothing_on_a_get(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        text = visible(response.content.decode())
        self.assertIn("Do you want to remove lin@example.com from the team?", text)
        self.assertFalse(self.closed(), f"{VIEW}: a GET changes nothing")
        self.assertEqual(self.expiries(), [True, True])

    def test_removing_with_files_kept_and_with_files_revoked(self):
        response = self.post(files="keep")
        self.assertEqual(response.redirect_chain, [(URL, 302)])
        self.assertIn("lin@example.com is no longer on the team.", visible(response.content.decode()))
        self.assertNotIn("their files are revoked", visible(response.content.decode()))
        self.assertTrue(self.closed())
        self.assertEqual(self.expiries(), [True, True])
        other = second_member(self.workspace, "kay")
        live_file(other, "k.txt")
        response = self.client.post(f"{URL}{other.pk}/remove/", {"files": "revoke"}, follow=True)
        self.assertIn("kay@example.com is no longer on the team, and their files are revoked.", visible(response.content.decode()))
        self.assertEqual([a.expires_at > timezone.now() for a in Artifact.objects.filter(member=other)], [False])

    def test_the_remove_page_starts_on_keeping_the_files(self):
        html = self.client.get(self.url).content.decode()
        radios = re.findall(r"<input[^>]*type=\"radio\"[^>]*>", html)
        self.assertEqual([("checked" in tag, re.search(r'value="([^"]*)"', tag).group(1)) for tag in radios], [(True, "keep"), (False, "revoke")])
        text = visible(html)
        self.assertIn("Let them keep working until they expire", text)
        self.assertIn("Revoke all of them now", text)
        self.assertIn("Remove from the team", text)
        self.assertIn("Keep them on the team", text)

    def test_an_unknown_choice_keeps_the_files(self):
        for value in ("", "REVOKE", "revoke ", "yes"):
            with self.subTest(value=value):
                self.post(files=value)
                self.assertTrue(self.closed())
                self.assertEqual(self.expiries(), [True, True], f"{VIEW}: only the exact value revoke revokes")
                Membership.objects.filter(pk=self.member.pk).update(removed_at=None)

    def test_no_file_name_appears_on_the_remove_page(self):
        self.assertTrue(Artifact.objects.filter(filename="secret-plans.txt", visibility=VISIBILITY_ONLY_ME, member=self.member).exists())
        self.assertTrue(self.file.token)
        html = self.client.get(self.url).content.decode()
        self.assertNotIn("secret-plans", html, f"{VIEW}: the page states a count and a size only")
        self.assertNotIn("shared.txt", html)
        self.assertNotIn(self.file.token, html)

    def test_the_remove_page_states_count_and_size_only(self):
        self.assertIn("They have 2 live files on this team, using 3.0 KB.", visible(self.client.get(self.url).content.decode()))

    def test_a_person_with_no_live_files_gets_no_choice(self):
        Artifact.objects.filter(member=self.member).delete()
        html = self.client.get(self.url).content.decode()
        self.assertIn("They have no live files on this team.", visible(html))
        self.assertNotIn('type="radio"', html)
        self.assertNotIn("What happens to their files", html)
        self.assertEqual(self.post().redirect_chain, [(URL, 302)])
        self.assertTrue(self.closed())

    def test_removing_twice_is_harmless(self):
        self.post()
        response = self.post()
        self.assertEqual(response.redirect_chain, [(URL, 302)])
        self.assertIn("That person is not on the team any more.", visible(response.content.decode()))
        response = self.client.get(self.url, follow=True)
        self.assertEqual(response.redirect_chain, [(URL, 302)])
        self.assertIn("That person is not on the team any more.", visible(response.content.decode()))

    def test_the_administrators_own_row_is_a_sentence_and_a_redirect(self):
        for method in ("get", "post"):
            response = getattr(self.client, method)(f"{URL}{self.admin.pk}/remove/", follow=True)
            self.assertEqual(response.redirect_chain, [(URL, 302)])
            self.assertIn("That person is not on the team any more.", visible(response.content.decode()))
        self.admin.refresh_from_db()
        self.assertIsNone(self.admin.removed_at)

    def test_another_teams_member_is_the_shells_404(self):
        other, _ = team_of("kay")
        stranger = second_member(other, "mo")
        for method in ("get", "post"):
            self.assertEqual(getattr(self.client, method)(f"{URL}{stranger.pk}/remove/").status_code, 404)
        self.assertEqual(self.client.get(f"{URL}999999/remove/").status_code, 404)
        stranger.refresh_from_db()
        self.assertIsNone(stranger.removed_at)

    def test_the_administrators_own_row_has_no_remove_link(self):
        html = self.get().content.decode()
        self.assertIn(self.url, html)
        self.assertNotIn(f"{URL}{self.admin.pk}/remove/", html)
        self.assertIn("Remove<span class=\"visually-hidden\"> for lin@example.com</span>", html)

    def test_a_suspended_team_gets_the_sentence(self):
        Workspace.objects.filter(pk=self.workspace.pk).update(suspended_at=timezone.now())
        response = self.post(files="revoke")
        self.assertEqual(response.redirect_chain, [(URL, 302)])
        self.assertIn(
            "This team is suspended, so nobody can be removed or leave it until the suspension ends. Contact support@veilfile.com.",
            visible(response.content.decode()),
        )
        self.assertFalse(self.closed())

    def test_removing_the_last_member_of_a_free_team_goes_to_overview(self):
        Workspace.objects.filter(pk=self.workspace.pk).update(plan=Workspace.PLAN_FREE)
        response = self.post()
        self.assertEqual(response.redirect_chain, [("/app/", 302)])
        self.assertTrue(self.closed())
        self.assertIn("lin@example.com is no longer on the team.", visible(response.content.decode()))

    def test_a_post_without_its_token_is_form_not_sent(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin.user)
        with self.assertLogs("django.security.csrf", level="WARNING"):
            response = client.post(self.url, {"files": "revoke"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("Form not sent", response.content.decode())
        self.assertFalse(self.closed())

    def test_removing_works_with_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin.user)
        page = client.get(self.url).content.decode()
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page).group(1)
        response = client.post(self.url, {"files": "keep", "csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 302, f"{REMOVE_PAGE}: the form carries {{% csrf_token %}}")
        self.assertTrue(self.closed())

    def test_the_page_is_a_form_page_on_the_shell(self):
        form_pages.FormPagesAreOnTheShellTests.check(self, self.url)


ASK_PAGE = "backend/templates/dashboard/member_administrator.html"
CANCEL_REQUEST_PAGE = "backend/templates/dashboard/administrator_offer_cancel.html"
NOT_WAITING = "That request is not waiting any more."


class AdministratorRequestTests(MembersCase):
    def setUp(self):
        super().setUp()
        self.member = second_member(self.workspace, "lin")
        self.url = f"{URL}{self.member.pk}/administrator/"
        self.cancel_url = f"{URL}administrator/cancel/"

    def offered(self):
        return teams.waiting_administrator_offer(self.workspace)

    def said(self, response):
        return unescape(visible(response.content.decode()))

    def test_asking_asks_first_and_says_so(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        text = self.said(response)
        self.assertIn("Do you want to make lin@example.com the administrator?", text)
        self.assertIn("They become the administrator when they accept, and you become a member of the team. The administrator manages who is "
                      "on the team and pays for its plan. Files that only you can see stay that way.", text)
        self.assertIn("They have 7 days to accept, on their Overview page.", text)
        self.assertIn("Ask them", text)
        self.assertIn("Go back", text)
        self.assertIsNone(self.offered(), f"{VIEW}: a GET changes nothing")
        response = self.client.post(self.url, follow=True)
        self.assertEqual(response.redirect_chain, [(URL, 302)])
        self.assertIn("lin@example.com can now accept the request on their Overview page.", self.said(response))
        self.assertEqual(self.offered()["email"], "lin@example.com")

    def test_the_waiting_request_shows_on_members_with_a_cancel_link(self):
        self.assertNotIn("You asked", self.said(self.get()))
        teams.offer_administrator(self.admin, self.member.pk)
        response = self.get()
        text, html = self.said(response), response.content.decode()
        expires = self.offered()["expires"]
        self.assertIn("You asked lin@example.com to become the administrator. The request ends on", text)
        self.assertIn("Cancel the request", text)
        self.assertIn(f'href="{self.cancel_url}"', html)
        self.assertLess(html.index("You asked"), html.index("<table"), f"{PAGE}: the request is a paragraph above the table")
        self.assertIn(expires.strftime("%Y-%m-%dT%H:%MZ"), html)

    def test_the_row_has_make_administrator_between_the_allowance_and_remove(self):
        html = self.get().content.decode()
        # The links sit in one row of quiet links, so that the three read as three and not as one run of capitals.
        cell = re.search(rf'<td data-label="Actions"><p class="form-links"><a[^>]*href="{URL}{self.member.pk}/allowance/".*?</td>', html, re.S).group(0)
        self.assertLess(cell.index("Set allowance"), cell.index("Make administrator"))
        self.assertLess(cell.index("Make administrator"), cell.index("Remove"))
        self.assertIn("Make administrator<span class=\"visually-hidden\"> for lin@example.com</span>", cell)
        self.assertNotIn(f"{URL}{self.admin.pk}/administrator/", html)

    def test_cancelling_the_request_asks_first(self):
        teams.offer_administrator(self.admin, self.member.pk)
        response = self.client.get(self.cancel_url)
        self.assertEqual(response.status_code, 200)
        text = self.said(response)
        self.assertIn("Do you want to cancel the request?", text)
        self.assertIn("lin@example.com can no longer accept the administrator role.", text)
        self.assertIn("Keep the request", text)
        self.assertIsNotNone(self.offered(), f"{VIEW}: a GET changes nothing")
        response = self.client.post(self.cancel_url, follow=True)
        self.assertEqual(response.redirect_chain, [(URL, 302)])
        self.assertIn("The request is canceled.", self.said(response))
        self.assertIsNone(self.offered())

    def test_cancelling_with_no_request_waiting_gives_the_sentence(self):
        for method in ("get", "post"):
            response = getattr(self.client, method)(self.cancel_url, follow=True)
            self.assertEqual(response.redirect_chain, [(URL, 302)])
            self.assertIn(NOT_WAITING, self.said(response))

    def test_each_refusal_gives_its_sentence_and_changes_nothing(self):
        def suspend_team():
            Workspace.objects.filter(pk=self.workspace.pk).update(suspended_at=timezone.now())

        def suspend_person():
            self.member.user.is_active = False
            self.member.user.save(update_fields=["is_active"])

        def unverify():
            EmailVerification.objects.filter(user=self.member.user).update(is_verified=False)

        def remove():
            Membership.objects.filter(pk=self.member.pk).update(removed_at=timezone.now())

        cases = (
            (suspend_team, members.TEAM_SUSPENDED),
            (suspend_person, "That person's account is suspended, so they cannot become the administrator."),
            (unverify, "That person has not verified their email address, so they cannot become the administrator yet."),
            (remove, "That person is not on the team any more."),
        )
        for setup, sentence in cases:
            with self.subTest(sentence=sentence):
                setup()
                response = self.client.post(self.url, follow=True)
                self.assertEqual(response.redirect_chain, [(URL, 302)])
                self.assertIn(sentence, self.said(response))
                self.assertIsNone(self.offered())
                Workspace.objects.filter(pk=self.workspace.pk).update(suspended_at=None)
                Membership.objects.filter(pk=self.member.pk).update(removed_at=None)
                EmailVerification.objects.filter(user=self.member.user).update(is_verified=True)
                self.member.user.is_active = True
                self.member.user.save(update_fields=["is_active"])

    def test_the_administrators_own_row_and_other_teams_people_are_refused(self):
        response = self.client.post(f"{URL}{self.admin.pk}/administrator/", follow=True)
        self.assertIn("That person is not on the team any more.", self.said(response))
        other, _ = team_of("kay")
        stranger = second_member(other, "mo")
        self.assertEqual(self.client.post(f"{URL}{stranger.pk}/administrator/").status_code, 404)
        self.assertEqual(self.client.get(f"{URL}999999/administrator/").status_code, 404)
        self.assertIsNone(Membership.objects.get(pk=stranger.pk).offered_administrator_at)

    def test_both_forms_work_with_the_token_the_page_carries(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin.user)
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', client.get(self.url).content.decode()).group(1)
        self.assertEqual(client.post(self.url, {"csrfmiddlewaretoken": token}).status_code, 302, f"{ASK_PAGE}: the form carries {{% csrf_token %}}")
        self.assertIsNotNone(self.offered())
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', client.get(self.cancel_url).content.decode()).group(1)
        self.assertEqual(client.post(self.cancel_url, {"csrfmiddlewaretoken": token}).status_code, 302, f"{CANCEL_REQUEST_PAGE}: the form carries {{% csrf_token %}}")
        self.assertIsNone(self.offered())

    def test_the_pages_are_form_pages_on_the_shell(self):
        form_pages.FormPagesAreOnTheShellTests.check(self, self.url)
        teams.offer_administrator(self.admin, self.member.pk)
        form_pages.FormPagesAreOnTheShellTests.check(self, self.cancel_url)


@override_settings(TEAM_SEATS=True)
class OverviewTests(TestCase):
    """Overview for a person on a team (spec 4.8, team seats). The switch-off bytes are SwitchOffTests'."""

    longMessage = False

    def setUp(self):
        self.workspace, self.admin = team_of("ada")
        self.member = second_member(self.workspace, "lin")

    def page(self, user, **query):
        client = Client()
        client.force_login(user)
        response = client.get("/app/", query)
        self.assertEqual(response.status_code, 200)
        return re.sub(r'(name="csrfmiddlewaretoken" value=")[^"]*', r"\1CSRF", response.content.decode())

    def as_member(self):
        return self.page(self.member.user)

    def test_a_free_one_person_account_sees_no_change_with_the_switch_on(self):
        _, free = team_of("kay", plan=Workspace.PLAN_FREE)
        on = self.page(free.user)
        with override_settings(TEAM_SEATS=False):
            off = self.page(free.user)
        self.assertEqual(on, off, "backend/dashboard/overview_page.py: a Free one-person account sees the page it saw before")

    def test_a_members_team_sentence_comes_before_their_meters(self):
        body = self.as_member()
        self.assertIn('id="overview-team"', body, "backend/templates/dashboard/overview.html: a member's Overview has the Team section")
        self.assertLess(body.index('id="overview-team"'), body.index('id="overview-own"'),
                        "backend/templates/dashboard/overview.html: a member reads whose team it is before their own use (spec decision 14)")
        self.assertEqual(body.count('id="overview-team"'), 1, "backend/templates/dashboard/overview.html: one Team section, not two")

    def test_an_administrators_team_section_comes_after_the_account(self):
        body = self.page(self.admin.user)
        self.assertLess(body.index('id="overview-account"'), body.index('id="overview-team"'),
                        "backend/templates/dashboard/overview.html: an administrator's Team section stays last, where it moves nothing under the open sheet")
        self.assertEqual(body.count('id="overview-team"'), 1, "backend/templates/dashboard/overview.html: one Team section, not two")

    def test_a_paid_administrator_gets_the_team_section_and_a_link(self):
        _, alone = team_of("sam", plan=Workspace.PLAN_TEAM)
        html = self.page(alone.user)
        self.assertIn("Team You are the administrator. 1 of 5 seats is in use. Members", visible(html))
        self.assertIn('href="/app/members/"', html)
        _, scale = team_of("sky", plan="scale")
        html = self.page(scale.user)
        self.assertIn("Team You are the administrator. 1 seat is in use. Members", visible(html))
        self.assertIn('href="/app/members/"', html)
        self.assertIn("2 of 5 seats are in use.", visible(self.page(self.admin.user)))

    def test_a_member_with_no_row_in_the_summary_gets_no_own_block(self):
        with mock.patch("drop_core.teams.member_rows", return_value=[]):
            html = self.as_member()
        self.assertIn("You are on the team that ada@example.com administers", visible(html))

    def test_a_member_sees_the_team_their_allowances_and_the_totals(self):
        Membership.objects.filter(pk=self.member.pk).update(upload_allowance=7, storage_allowance_bytes=2 * 1024**3, key_allowance=3)
        html = self.as_member()
        text = visible(html)
        self.assertIn("You are on the team that ada@example.com administers, which has 2 people.", text)
        self.assertIn("Your use", text)
        self.assertIn("The team's use", text)
        for meter, figure in (("overview-own-uploads", "0 of 7"), ("overview-own-storage", "0 B of 2.0 GB"), ("overview-own-keys", "0 of 3")):
            with self.subTest(meter=meter):
                self.assertIn(f'id="{meter}_figures"', html)
        self.assertIn('id="overview-own-uploads_figures">0 of 7', html)
        self.assertIn('id="overview-own-keys_figures">0 of 3', html)
        self.assertIn('id="overview-uploads_figures"', html, "the team's totals are still drawn")

    def test_a_member_with_no_allowance_sees_their_use_without_a_cap(self):
        live_file(self.member, "mine.txt", size=2048)
        APIKey.create_key(self.workspace, "agent", member=self.member)
        html = self.as_member()
        self.assertIn('id="overview-own-keys_figures">1</p>', html)
        self.assertIn('id="overview-own-uploads_figures">0</p>', html)
        self.assertNotIn("overview-own-keys_figures\">1 of", html)

    def test_a_zero_allowance_shows_as_a_limit_of_zero(self):
        Membership.objects.filter(pk=self.member.pk).update(upload_allowance=0, key_allowance=0)
        html = self.as_member()
        self.assertIn('id="overview-own-uploads_figures">0 of 0', html)
        self.assertIn('id="overview-own-keys_figures">0 of 0', html)

    def test_a_member_sees_no_list_of_people(self):
        second_member(self.workspace, "gus", "gus@example.com")
        text = visible(self.as_member())
        self.assertNotIn("gus@example.com", text)
        self.assertNotIn("/app/members/", self.as_member())

    def test_a_member_gets_no_billing_link_or_notice(self):
        Workspace.objects.filter(pk=self.workspace.pk).update(plan=Workspace.PLAN_FREE)
        for number in range(PLANS["free"]["keys"] + 1):
            APIKey.create_key(self.workspace, f"k{number}")
        html = self.as_member()
        text = visible(html)
        self.assertIn("Your team is on the Free plan.", text)
        self.assertNotIn("/app/billing/", html[html.index("<main"):html.index("</main>")])
        self.assertNotIn("is over the", text)
        self.assertNotIn("Plan limits could not be checked", text)

    def test_get_connected_follows_a_members_own_keys(self):
        APIKey.create_key(self.workspace, "agent", member=self.admin)
        self.assertIn("Get connected", self.as_member())
        APIKey.create_key(self.workspace, "mine", member=self.member)
        self.assertNotIn("Get connected", self.as_member())

    def test_a_waiting_invitation_shows_a_notice_only_when_verified(self):
        tokens = []
        teams.invite(self.admin, "grace@example.com", build_url=lambda raw: tokens.append(raw) or f"https://veilfile.test/{raw}/")
        from django.contrib.auth import get_user_model

        grace = get_user_model().objects.create_user(username="grace", email="grace@example.com")
        sentence = "An invitation to a team is waiting for this address. Open the link in the email to accept or decline it."
        html = self.page(grace)
        self.assertNotIn(sentence, visible(html), "an unverified address is told nothing")
        from drop_core.models import EmailVerification

        EmailVerification.objects.create(user=grace, email=grace.email, is_verified=True, verified_at=timezone.now())
        html = self.page(grace)
        self.assertIn(sentence, visible(html))
        self.assertNotIn(tokens[0], html, "the page holds no token")
        with override_settings(TEAM_SEATS=False):
            self.assertNotIn(sentence, visible(self.page(grace)))
