"""The pages of a member whose team is on Free, and the administrator's (team seats, billing): what Files, Keys, Apps, Overview, Billing and
Members say and hide, and the helpers behind them.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it).
"""
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from dashboard.tests_keys import connect, hidden_token, make_app
from dashboard.tests_members import live_file, visible
from drop_core import teams
from drop_core.file_access import file_details, files_visible_to
from drop_core.key_caps import create_key_within_cap
from drop_core.models import (
    VISIBILITY_ONLY_ME,
    VISIBILITY_TEAM,
    APIKey,
    Artifact,
    Membership,
    Workspace,
)
from drop_core.tests_bar import drop
from drop_core.tests_members import second_member
from drop_core.tests_teams import team_of

OVERVIEW_SENTENCE = (
    "Your team is on the Free plan, so you cannot upload or make keys until {admin} chooses a paid plan. "
    "You can still see and revoke your own files, and you can leave the team."
)
KEYS_SENTENCE = "Your team is on the Free plan, so you cannot make keys for now. You can still revoke the ones you have."
BILLING_SENTENCE = (
    "Your team is on the Free plan, so the {n} other {people} on it cannot upload or make keys. "
    "Choose a plan with enough seats for everyone, or remove people under Members."
)
MEMBERS_SENTENCE = (
    "The team is on the Free plan, so nobody but you can upload or make keys. Choose a paid plan under Billing, or remove people here."
)
OVER_SENTENCE = "Your team has {people} people, and {plan} has {seats} seats, so nobody new can join until people leave or you change plans."
ACCEPT_SENTENCE = (
    "The team is on the Free plan, so only its administrator can upload or make keys. "
    "If you accept, you can choose a paid plan under Billing."
)


@override_settings(TEAM_SEATS=True)
class BarPagesCase(TestCase):
    longMessage = False

    def setUp(self):
        self.team, self.admin = team_of("ada")
        self.grace = second_member(self.team, "grace")
        self.lin = second_member(self.team, "lin")
        drop(self.team)

    def page(self, user, url):
        client = Client()
        client.force_login(user)
        return client, client.get(url)


class FilesTests(BarPagesCase):
    def setUp(self):
        super().setUp()
        self.mine_only = live_file(self.grace, "only-me.txt")
        self.mine_shared = live_file(self.grace, "shared.txt", visibility=VISIBILITY_TEAM)
        self.lins = live_file(self.lin, "lins.txt", visibility=VISIBILITY_TEAM)

    def test_a_barred_member_sees_only_their_own_files(self):
        client, response = self.page(self.grace.user, "/app/files/")
        text = visible(response.content.decode())
        self.assertIn("only-me.txt", text)
        self.assertIn("shared.txt", text)
        self.assertNotIn("lins.txt", text, "backend/drop_core/file_access.py: files_visible_to drops the team-shared branch for a barred member")
        self.assertNotIn("Share with the team", text)
        self.assertNotIn("Make it only mine", text)
        forged = client.post(f"/app/files/{self.mine_only.id}/visibility/", {"visibility": "team"})
        self.assertEqual(forged.status_code, 404)
        self.mine_only.refresh_from_db()
        self.assertEqual(self.mine_only.visibility, VISIBILITY_ONLY_ME, "backend/drop_core/file_access.py: set_visibility filters on the bar")
        for artifact in (self.mine_only, self.mine_shared):
            self.assertEqual(client.post(f"/app/files/{artifact.id}/revoke/").status_code, 302)
            self.assertFalse(Artifact.objects.filter(pk=artifact.pk).exists())

    def test_a_member_of_a_paid_team_still_sees_the_team_shared_files(self):
        Workspace.objects.filter(pk=self.team.pk).update(plan="team")
        _, response = self.page(self.grace.user, "/app/files/")
        self.assertIn("lins.txt", visible(response.content.decode()))
        self.assertTrue(file_details(Membership.objects.get(pk=self.grace.pk), [self.mine_only.id])[self.mine_only.id]["can_change_visibility"])

    def test_the_bar_is_in_the_updates_own_filter(self):
        from drop_core.file_access import set_visibility

        stale = Membership.objects.get(pk=self.grace.pk)
        self.assertTrue(stale.workspace.plan == "free")
        self.assertFalse(set_visibility(stale, self.mine_only.id, VISIBILITY_TEAM))
        Workspace.objects.filter(pk=self.team.pk).update(plan="team")
        self.assertTrue(set_visibility(stale, self.mine_only.id, VISIBILITY_TEAM))
        drop(self.team)
        self.assertFalse(set_visibility(Membership.objects.get(pk=self.grace.pk), self.mine_shared.id, VISIBILITY_ONLY_ME))

    def test_the_administrator_still_sees_a_barred_members_team_files(self):
        client, response = self.page(self.admin.user, "/app/files/")
        text = visible(response.content.decode())
        self.assertIn("shared.txt", text)
        self.assertIn("lins.txt", text)
        self.assertNotIn("only-me.txt", text)
        self.assertEqual(client.post(f"/app/files/{self.lins.id}/revoke/").status_code, 302)
        self.assertFalse(Artifact.objects.filter(pk=self.lins.pk).exists())
        self.assertEqual(files_visible_to(Membership.objects.get(pk=self.admin.pk)).count(), 1)


class WaysOutTests(BarPagesCase):
    def test_a_barred_member_keeps_the_ways_out(self):
        Workspace.objects.filter(pk=self.team.pk).update(plan="team")
        key, _ = create_key_within_cap(self.team, "grace-key", member=self.grace)
        drop(self.team)
        app = make_app("Claude Code")
        connect(self.grace.user, app)
        client, response = self.page(self.grace.user, "/app/keys/")
        html = response.content.decode()
        self.assertIn("grace-key", html)
        self.assertIn(KEYS_SENTENCE, visible(html))
        self.assertNotIn("Create key", html, "backend/templates/dashboard/keys.html: no form for a barred member")
        self.assertNotIn('name="token"', html)
        self.assertEqual(client.post(f"/app/keys/{key.pk}/revoke/").status_code, 302)
        self.assertFalse(APIKey.objects.get(pk=key.pk).is_active)
        after = client.get("/app/keys/").content.decode()
        self.assertIn(KEYS_SENTENCE, visible(after))
        self.assertNotIn("Get connected", after, "backend/templates/dashboard/keys.html: no Get connected section for a barred member")
        self.assertIn("Claude Code", client.get("/app/apps/").content.decode())
        self.assertEqual(client.post(f"/app/apps/{app.pk}/disconnect/").status_code, 302)
        self.assertNotIn("<table", client.get("/app/apps/").content.decode())
        self.assertEqual(client.post("/app/team/leave/").status_code, 302)
        self.assertIsNotNone(Membership.objects.get(pk=self.grace.pk).removed_at)

    def test_a_member_of_a_paid_team_keeps_the_form(self):
        Workspace.objects.filter(pk=self.team.pk).update(plan="team")
        _, response = self.page(self.grace.user, "/app/keys/")
        html = response.content.decode()
        self.assertIn("Create key", html)
        self.assertNotIn(KEYS_SENTENCE, visible(html))


class OverviewTests(BarPagesCase):
    def test_a_barred_members_overview_says_so(self):
        _, response = self.page(self.grace.user, "/app/")
        html = response.content.decode()
        text = visible(html)
        self.assertIn(OVERVIEW_SENTENCE.format(admin=self.admin.user.email), text)
        self.assertNotIn("Get connected", text, "backend/dashboard/overview_page.py: no Get connected for a barred member")
        self.assertIn('href="/app/team/leave/"', html)

    def test_a_member_of_a_paid_team_gets_no_such_notice(self):
        Workspace.objects.filter(pk=self.team.pk).update(plan="team")
        _, response = self.page(self.grace.user, "/app/")
        self.assertNotIn("so you cannot upload or make keys", visible(response.content.decode()))


class AdministratorTests(BarPagesCase):
    def test_the_administrator_of_a_free_team_is_told_on_billing_and_members(self):
        client, billing = self.page(self.admin.user, "/app/billing/")
        self.assertIn(BILLING_SENTENCE.format(n=2, people="people"), visible(billing.content.decode()))
        self.assertIn(MEMBERS_SENTENCE, visible(client.get("/app/members/").content.decode()))
        Membership.objects.filter(pk=self.lin.pk).update(removed_at=timezone.now())
        self.assertIn(BILLING_SENTENCE.format(n=1, people="person"), visible(client.get("/app/billing/").content.decode()))

    def test_an_over_seats_paid_team_is_told_on_billing_and_members(self):
        Workspace.objects.filter(pk=self.team.pk).update(plan="team")
        for name in ("a", "b", "c"):
            second_member(self.team, name)
        client, billing = self.page(self.admin.user, "/app/billing/")
        sentence = OVER_SENTENCE.format(people=6, plan="Team", seats=5)
        self.assertIn(sentence, visible(billing.content.decode()))
        self.assertIn(sentence, visible(client.get("/app/members/").content.decode()))
        self.assertNotIn("on it cannot upload or make keys", visible(billing.content.decode()))

    def test_a_paid_team_that_fits_is_told_nothing(self):
        Workspace.objects.filter(pk=self.team.pk).update(plan="team")
        client, billing = self.page(self.admin.user, "/app/billing/")
        self.assertNotIn("nobody new can join", visible(billing.content.decode()))
        self.assertNotIn("cannot upload or make keys", visible(client.get("/app/members/").content.decode()))

    def test_the_accept_page_says_the_team_is_on_free(self):
        teams.offer_administrator(Membership.objects.get(pk=self.admin.pk), self.grace.pk)
        _, response = self.page(self.grace.user, "/app/team/administrator/")
        html = response.content.decode()
        self.assertIn(ACCEPT_SENTENCE, visible(html))
        self.assertLess(html.index("asked you to take over the team"), html.index(ACCEPT_SENTENCE), "backend/templates/dashboard/team_administrator.html: after the first paragraph")
        self.assertLess(html.index(ACCEPT_SENTENCE), html.index("<ul>"))
        Workspace.objects.filter(pk=self.team.pk).update(plan="team")
        self.assertNotIn(ACCEPT_SENTENCE, visible(self.page(self.grace.user, "/app/team/administrator/")[1].content.decode()))


class HelperTests(BarPagesCase):
    def test_too_few_seats_gives_people_and_seats_when_the_team_does_not_fit(self):
        self.assertEqual(teams.too_few_seats(self.team), (3, 1))
        self.assertIsNone(teams.too_few_seats(self.team, "team"))
        self.assertEqual(teams.too_few_seats(self.team, "free"), (3, 1))

    def test_too_few_seats_is_none_for_unlimited_seats(self):
        from drop_core.plans import PLANS

        unlimited = [slug for slug, plan in PLANS.items() if plan["seats"] is None]
        for slug in unlimited:
            self.assertIsNone(teams.too_few_seats(self.team, slug))

    def test_the_new_helpers_read_nothing_with_the_switch_off(self):
        member = Membership.objects.get(pk=self.admin.pk)
        member.workspace
        with override_settings(TEAM_SEATS=False):
            with self.assertNumQueries(0):
                self.assertIsNone(teams.too_few_seats(self.team))
                self.assertIsNone(teams.too_few_seats(self.team, "free"))
                self.assertIsNone(teams.team_summary(member))

    def test_the_summary_carries_the_bar_and_the_counts(self):
        admin = Membership.objects.get(pk=self.admin.pk)
        member = Membership.objects.get(pk=self.grace.pk)
        self.assertEqual(
            (teams.team_summary(member)["barred"], teams.team_summary(admin)["barred"]), (True, False)
        )
        summary = teams.team_summary(admin)
        self.assertEqual((summary["others_barred"], summary["over_seats"]), (2, None))
        Workspace.objects.filter(pk=self.team.pk).update(plan="team")
        for name in ("a", "b", "c"):
            second_member(self.team, name)
        admin = Membership.objects.get(pk=self.admin.pk)
        summary = teams.team_summary(admin)
        self.assertEqual((summary["others_barred"], summary["over_seats"]), (0, {"people": 6, "seats": 5, "plan": "Team"}))
        self.assertFalse(teams.team_summary(Membership.objects.get(pk=self.grace.pk))["barred"])


class SeatSentenceTests(BarPagesCase):
    """Billing names a plan with too few seats once, in a paragraph after the monthly row, and leaves its buttons out of both rows."""

    SENTENCE = "Team has 5 seats, and your team has 6 people."

    def setUp(self):
        super().setUp()
        for name in ("a", "b", "c"):
            second_member(self.team, name)

    def html(self):
        return self.page(self.admin.user, "/app/billing/")[1].content.decode()

    @staticmethod
    def rows(html):
        import re

        return re.findall(r'<div class="form-actions">(.*?)</div>', html, re.S)

    def test_the_sentence_appears_once_outside_any_row_of_actions(self):
        html = self.html()
        self.assertEqual(html.count(self.SENTENCE), 1, "backend/templates/dashboard/billing.html: a plan with too few seats is named once")
        self.assertFalse([row for row in self.rows(html) if "has 5 seats" in row], "backend/templates/dashboard/billing.html: not in a row of actions")

    def test_the_sentence_follows_the_monthly_row_and_comes_before_the_yearly_sentence(self):
        html = self.html()
        monthly_end = html.index("</div>", html.index('<div class="form-actions">'))
        self.assertLess(monthly_end, html.index(self.SENTENCE))
        self.assertLess(html.index(self.SENTENCE), html.index("Yearly billing costs"))
        self.assertIn("<p>" + self.SENTENCE + "</p>", html)

    def test_no_button_for_that_plan_appears_in_either_row(self):
        rows = " ".join(self.rows(self.html()))
        self.assertNotIn("Upgrade to Team (", rows)
        self.assertNotIn("Upgrade to Team yearly", rows)
        self.assertNotIn('name="plan" value="team"', rows)

    def test_a_plan_that_fits_keeps_its_buttons(self):
        rows = self.rows(self.html())
        self.assertEqual(len(rows), 2)
        for row, per in zip(rows, ("mo", "yr")):
            self.assertIn(f"Upgrade to Team Plus ($", row)
            self.assertIn(f"/{per})", row)


class BarredOverviewTests(BarPagesCase):
    def test_a_barred_member_has_their_own_use_and_not_the_teams(self):
        html = self.page(self.grace.user, "/app/")[1].content.decode()
        self.assertNotIn('id="overview-usage"', html, "backend/templates/dashboard/overview.html: no team use for a barred member")
        self.assertNotIn("The team's use", html)
        self.assertIn('id="overview-own"', html)

    def test_an_unbarred_member_still_has_the_teams_use(self):
        Workspace.objects.filter(pk=self.team.pk).update(plan="team")
        html = self.page(self.grace.user, "/app/")[1].content.decode()
        self.assertIn('id="overview-usage"', html)
        self.assertIn("The team's use", html)
        self.assertIn('id="overview-own"', html)


class BarredFilesCaptionTests(BarPagesCase):
    def test_a_barred_members_caption_names_only_their_files_and_keeps_the_columns(self):
        live_file(self.grace, "mine.txt")
        html = self.page(self.grace.user, "/app/files/")[1].content.decode()
        self.assertIn("<caption>Your files, newest first</caption>", html)
        for label in ("Visible to", "Uploaded by"):
            self.assertIn(f'<th scope="col">{label}</th>', html)

    def test_an_unbarred_members_caption_names_the_teams_too(self):
        Workspace.objects.filter(pk=self.team.pk).update(plan="team")
        live_file(self.grace, "mine.txt")
        html = self.page(self.grace.user, "/app/files/")[1].content.decode()
        self.assertIn("<caption>Your files and your team's, newest first</caption>", html)


class OnePersonTests(TestCase):
    longMessage = False

    def test_a_free_one_person_account_sees_no_change_with_the_switch_on(self):
        workspace, admin = team_of("kay", plan="free")
        client = Client()
        client.force_login(admin.user)
        for url in ("/app/", "/app/keys/", "/app/billing/", "/app/files/"):
            off = client.get(url).content.decode()
            with override_settings(TEAM_SEATS=True):
                on = client.get(url).content.decode()
            self.assertEqual(self._strip(off), self._strip(on), url)

    @staticmethod
    def _strip(html):
        import re

        return re.sub(r'(name="(csrfmiddlewaretoken|token)" value=")[^"]*', r"\1", html)
