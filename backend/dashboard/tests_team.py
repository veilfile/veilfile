"""A member's own team pages (spec 4.8, team seats): leaving, and what Overview says to a person who left.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it).
"""
import re
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from dashboard.tests_members import live_file, visible
from drop_core import teams
from drop_core.models import EmailVerification, Membership, Workspace
from drop_core.tests_members import second_member
from drop_core.tests_teams import team_of
from pages import tests_forms_on_the_shell as form_pages

VIEW = "backend/dashboard/team.py"
PAGE = "backend/templates/dashboard/team_leave.html"
LEAVE = "/app/team/leave/"
ACCEPT = "/app/team/administrator/"
ACCEPT_PAGE = "backend/templates/dashboard/team_administrator.html"
NOT_WAITING = "That request is not waiting any more."
OVERVIEW = "/app/"
LEFT = "You are no longer on a team, so this account is a Free account again."


@override_settings(TEAM_SEATS=True)
class TeamCase(TestCase):
    longMessage = False

    def setUp(self):
        self.workspace, self.admin = team_of("ada")
        self.member = second_member(self.workspace, "lin")
        self.client.force_login(self.member.user)

    def closed(self):
        self.member.refresh_from_db()
        return self.member.removed_at is not None


class AccessTests(TeamCase):
    def addresses(self):
        return [(LEAVE, ("get", "post")), (ACCEPT, ("get", "post"))]

    def test_every_address_is_the_shells_404_with_the_switch_off(self):
        with override_settings(TEAM_SEATS=False):
            for client in (self.client, Client()):
                for url, methods in self.addresses():
                    for method in methods:
                        with self.subTest(url=url, method=method, signed_in=client is self.client):
                            response = getattr(client, method)(url)
                            self.assertEqual(response.status_code, 404, f"{VIEW}: team_seats_only wraps each view outside dashboard_view")
                            self.assertIn("Not found", response.content.decode())
        self.assertFalse(self.closed())

    def test_signed_out_it_goes_to_sign_in(self):
        for url, methods in self.addresses():
            for method in methods:
                with self.subTest(url=url, method=method):
                    response = getattr(Client(), method)(url)
                    self.assertEqual((response.status_code, response.get("Location")), (302, f"/accounts/login/?next={url}"))

    def test_an_administrator_alone_and_a_person_with_no_team_get_the_shells_404(self):
        _, alone = team_of("kay")
        stranger = second_member(self.workspace, "mo").user
        Membership.objects.filter(user=stranger).update(removed_at=timezone.now())
        for user in (alone.user, stranger):
            client = Client()
            client.force_login(user)
            for method in ("get", "post"):
                with self.subTest(user=user.username, method=method):
                    self.assertEqual(getattr(client, method)(LEAVE).status_code, 404)


class LeaveTests(TeamCase):
    def test_leaving_asks_first_and_changes_nothing_on_a_get(self):
        live_file(self.member, "mine.txt")
        response = self.client.get(LEAVE)
        self.assertEqual(response.status_code, 200)
        text = visible(response.content.decode())
        self.assertIn("Do you want to leave this team?", text)
        self.assertIn("You leave the team that ada@example.com administers, and this account becomes a Free account again.", text)
        self.assertIn("Your API keys and connected apps stop working at once. Files you uploaded here keep working until they expire, "
                      "and you can still see and revoke them on Files. They count toward the team's storage until then.", text)
        self.assertIn("Leave the team", text)
        self.assertIn("Stay on the team", text)
        self.assertNotIn("mine.txt", text)
        self.assertFalse(self.closed(), f"{VIEW}: a GET changes nothing")

    def test_leaving_lands_on_overview_with_the_notice(self):
        response = self.client.post(LEAVE, follow=True)
        self.assertEqual(response.redirect_chain, [(OVERVIEW, 302)])
        self.assertIn("You have left the team. Connect your agents again with veilfile login.", visible(response.content.decode()))
        self.assertTrue(self.closed())

    def test_an_administrator_with_people_gets_the_sentence(self):
        client = Client()
        client.force_login(self.admin.user)
        for method in ("get", "post"):
            response = getattr(client, method)(LEAVE, follow=True)
            self.assertEqual(response.redirect_chain, [("/app/members/", 302)])
            self.assertIn(
                "You administer this team, so you cannot leave it while other people are on it. Make someone else the administrator first.",
                visible(response.content.decode()),
            )
        self.admin.refresh_from_db()
        self.assertIsNone(self.admin.removed_at)

    def test_a_suspended_team_gets_the_sentence(self):
        Workspace.objects.filter(pk=self.workspace.pk).update(suspended_at=timezone.now())
        response = self.client.post(LEAVE, follow=True)
        self.assertEqual(response.redirect_chain, [(OVERVIEW, 302)])
        self.assertIn("This team is suspended, so nobody can be removed or leave it until the suspension ends.", visible(response.content.decode()))
        self.assertFalse(self.closed())

    def test_a_post_without_its_token_is_form_not_sent(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.member.user)
        with self.assertLogs("django.security.csrf", level="WARNING"):
            response = client.post(LEAVE)
        self.assertEqual(response.status_code, 403)
        self.assertIn("Form not sent", response.content.decode())
        self.assertFalse(self.closed())

    def test_leaving_works_with_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.member.user)
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', client.get(LEAVE).content.decode()).group(1)
        response = client.post(LEAVE, {"csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 302, f"{PAGE}: the form carries {{% csrf_token %}}")
        self.assertTrue(self.closed())

    def test_the_page_is_a_form_page_on_the_shell(self):
        form_pages.FormPagesAreOnTheShellTests.check(self, LEAVE)


class OverviewTests(TeamCase):
    def page(self, user):
        client = Client()
        client.force_login(user)
        return visible(client.get(OVERVIEW).content.decode()), client.get(OVERVIEW).content.decode()

    def test_the_member_overview_links_to_leave(self):
        text, html = self.page(self.member.user)
        self.assertIn(f'href="{LEAVE}"', html)
        self.assertIn("Leave the team", text)
        self.assertLess(text.index("which has 2 people."), text.index("Leave the team"))

    def test_the_administrators_overview_has_no_leave_link(self):
        text, html = self.page(self.admin.user)
        self.assertNotIn(LEAVE, html)

    def test_a_member_overview_never_contains_app_members(self):
        self.assertNotIn("/app/members/", self.page(self.member.user)[1])

    def test_a_person_who_left_reads_the_notice_on_overview(self):
        teams.leave_team(self.member)
        text, _ = self.page(self.member.user)
        self.assertIn(LEFT, text)
        self.assertNotIn(self.admin.user.email, text)
        Membership.objects.filter(pk=self.member.pk).update(removed_at=timezone.now() - timedelta(days=8))
        self.assertNotIn(LEFT, self.page(self.member.user)[0])

    def test_nobody_else_reads_the_notice(self):
        self.assertNotIn(LEFT, self.page(self.member.user)[0])
        self.assertNotIn(LEFT, self.page(self.admin.user)[0])

    def test_with_the_switch_off_the_notice_is_not_drawn(self):
        teams.leave_team(self.member)
        with override_settings(TEAM_SEATS=False):
            self.assertNotIn(LEFT, self.page(self.member.user)[0])


class AdministratorRequestTests(TeamCase):
    def setUp(self):
        super().setUp()
        teams.offer_administrator(self.admin, self.member.pk)

    def fresh(self, row):
        return Membership.objects.get(pk=row.pk)

    def roles(self):
        return (self.fresh(self.admin).role, self.fresh(self.member).role)

    def overview(self, user):
        client = Client()
        client.force_login(user)
        response = client.get(OVERVIEW)
        return visible(response.content.decode()), response.content.decode()

    def test_the_chosen_member_sees_the_notice_on_overview_and_no_one_else_does(self):
        text, html = self.overview(self.member.user)
        self.assertIn("ada@example.com asked you to become the administrator of this team. The request ends on", text)
        self.assertIn("Review the request", text)
        self.assertIn(f'href="{ACCEPT}"', html)
        self.assertNotIn("/app/members/", html)
        other = second_member(self.workspace, "kay")
        for user in (self.admin.user, other.user):
            with self.subTest(user=user.username):
                self.assertNotIn("asked you to become the administrator", self.overview(user)[0])
        teams.cancel_administrator_offer(self.admin)
        self.assertNotIn("asked you to become the administrator", self.overview(self.member.user)[0])

    def test_with_the_switch_off_the_notice_is_not_drawn(self):
        with override_settings(TEAM_SEATS=False):
            self.assertNotIn("asked you to become", self.overview(self.member.user)[0])

    def test_the_accept_page_says_what_the_role_means(self):
        response = self.client.get(ACCEPT)
        self.assertEqual(response.status_code, 200)
        text = visible(response.content.decode())
        self.assertIn("Do you want to become the administrator of this team?", text)
        self.assertIn("ada@example.com asked you to take over the team. If you accept:", text)
        for line in (
            "You manage who is on the team, what each person can use and who can see new files.",
            "You pay for the team's plan, and Billing is yours to manage.",
            "ada@example.com becomes a member, and the files that only they can see stay private.",
            "You cannot leave the team while other people are on it, until you hand the role on.",
        ):
            self.assertIn(line, text)
        self.assertIn("Become the administrator", text)
        self.assertIn("Decline", text)
        self.assertEqual(self.roles(), (Membership.ADMINISTRATOR, Membership.MEMBER), f"{VIEW}: a GET changes nothing")

    def test_an_unverified_chosen_member_is_asked_to_verify(self):
        EmailVerification.objects.filter(user=self.member.user).update(is_verified=False)
        response = self.client.get(ACCEPT)
        text, html = visible(response.content.decode()), response.content.decode()
        self.assertIn("Verify your email first, then open this page again.", text)
        self.assertIn("Verify email", text)
        self.assertIn('href="/accounts/verify-email/"', html)
        self.assertNotIn("Become the administrator", text)
        response = self.client.post(ACCEPT, {"action": "accept"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.roles(), (Membership.ADMINISTRATOR, Membership.MEMBER))

    def test_accepting_lands_on_members_with_the_notice(self):
        response = self.client.post(ACCEPT, {"action": "accept"}, follow=True)
        self.assertEqual(response.redirect_chain, [("/app/members/", 302)])
        self.assertIn("You are now the administrator of this team.", visible(response.content.decode()))
        self.assertEqual(self.roles(), (Membership.MEMBER, Membership.ADMINISTRATOR))

    def test_accepting_twice_is_harmless(self):
        self.client.post(ACCEPT, {"action": "accept"})
        for method in ("get", "post"):
            response = getattr(self.client, method)(ACCEPT, {"action": "accept"}, follow=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn(NOT_WAITING, visible(response.content.decode()))
        self.assertEqual(self.roles(), (Membership.MEMBER, Membership.ADMINISTRATOR))

    def test_declining_lands_on_overview_with_the_notice(self):
        response = self.client.post(ACCEPT, {"action": "decline"}, follow=True)
        self.assertEqual(response.redirect_chain, [(OVERVIEW, 302)])
        self.assertIn("You declined the request.", visible(response.content.decode()))
        self.assertIsNone(teams.administrator_offer_for(self.fresh(self.member)))
        self.assertEqual(self.roles(), (Membership.ADMINISTRATOR, Membership.MEMBER))

    def test_an_unknown_action_changes_nothing(self):
        for value in (None, "", "ACCEPT", "accept ", "yes", "accept,decline"):
            with self.subTest(value=value):
                response = self.client.post(ACCEPT, {} if value is None else {"action": value}, follow=True)
                self.assertEqual(response.redirect_chain, [(OVERVIEW, 302)])
                self.assertIn(NOT_WAITING, visible(response.content.decode()))
                self.assertEqual(self.roles(), (Membership.ADMINISTRATOR, Membership.MEMBER))
                self.assertIsNotNone(teams.administrator_offer_for(self.fresh(self.member)))

    def test_a_page_for_a_request_that_is_not_waiting_gives_the_sentence(self):
        teams.cancel_administrator_offer(self.admin)
        for method in ("get", "post"):
            response = getattr(self.client, method)(ACCEPT, {"action": "accept"}, follow=True)
            self.assertEqual(response.redirect_chain, [(OVERVIEW, 302)])
            self.assertIn(NOT_WAITING, visible(response.content.decode()))
        self.assertEqual(self.roles(), (Membership.ADMINISTRATOR, Membership.MEMBER))

    def test_a_suspended_team_gives_the_sentence_and_changes_nothing(self):
        Workspace.objects.filter(pk=self.workspace.pk).update(suspended_at=timezone.now())
        response = self.client.post(ACCEPT, {"action": "accept"}, follow=True)
        self.assertIn("This team is suspended, so nobody can be removed or leave it until the suspension ends.", visible(response.content.decode()))
        self.assertEqual(self.roles(), (Membership.ADMINISTRATOR, Membership.MEMBER))

    def test_the_administrator_with_people_and_a_person_with_no_team_are_not_waiting(self):
        client = Client()
        client.force_login(self.admin.user)
        for method in ("get", "post"):
            response = getattr(client, method)(ACCEPT, {"action": "accept"}, follow=True)
            self.assertEqual(response.redirect_chain, [("/app/members/", 302)])
            self.assertIn(NOT_WAITING, visible(response.content.decode()))
        _, alone = team_of("kay")
        client = Client()
        client.force_login(alone.user)
        self.assertEqual(client.get(ACCEPT).status_code, 404)

    def test_both_forms_work_with_the_token_the_page_carries(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.member.user)
        with self.assertLogs("django.security.csrf", level="WARNING"):
            self.assertEqual(client.post(ACCEPT, {"action": "accept"}).status_code, 403)
        html = client.get(ACCEPT).content.decode()
        forms = re.findall(r"<form.*?</form>", html[html.index("<main"):html.index("</main>")], re.S)
        self.assertEqual(len(forms), 2, f"{ACCEPT_PAGE}: one form to accept and one to decline")
        tokens = [re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', form).group(1) for form in forms]
        self.assertEqual(client.post(ACCEPT, {"action": "decline", "csrfmiddlewaretoken": tokens[1]}).status_code, 302)
        self.assertEqual(self.roles(), (Membership.ADMINISTRATOR, Membership.MEMBER))
        teams.offer_administrator(self.admin, self.member.pk)
        self.assertEqual(client.post(ACCEPT, {"action": "accept", "csrfmiddlewaretoken": tokens[0]}).status_code, 302)
        self.assertEqual(self.roles(), (Membership.MEMBER, Membership.ADMINISTRATOR))

    def test_the_page_is_a_form_page_on_the_shell(self):
        form_pages.FormPagesAreOnTheShellTests.check(self, ACCEPT)
