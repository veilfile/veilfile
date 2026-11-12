"""The seat check of billing (team seats, billing): a plan with fewer seats than the team has people is refused before Stripe is asked
to change anything, and checkout and switch read the administrator again under the workspace lock.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it).
"""
from datetime import timedelta
from unittest import mock

from django.contrib.messages import get_messages
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from dashboard import billing_page
from drop_billing import account as billing_account
from drop_billing.tests_fix_billing import FixBillingBase, make_sub, sub_list
from drop_core import teams
from drop_core.models import Invitation, Membership, Workspace
from drop_core.tests_members import second_member

ACCOUNT = "backend/drop_billing/account.py"
PAGE = "backend/templates/dashboard/billing.html"
JSON_ERROR = "This team has more people than the plan has seats. Remove people first."


def people(workspace, count):
    """Adds members until the team has `count` people, the administrator included."""
    for number in range(Membership.objects.filter(workspace=workspace, removed_at__isnull=True).count(), count):
        second_member(workspace, f"person{number}")


class SeatBase(FixBillingBase):
    longMessage = False

    def switch(self, plan, interval="month"):
        return self.api.post("/api/v1/billing/switch", {"plan": plan, "interval": interval}, format="json")

    def checkout(self, plan):
        return self.api.post("/api/v1/billing/checkout", {"plan": plan}, format="json")

    def subscribed(self, plan, price, interval="month", count=1):
        self.set_billing(plan, sub_id="sub_A")
        people(self.ws, count)
        self.listed = self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", price)))
        self.modify = self.patch_stripe("modify", return_value=make_sub("sub_A", "active", "price_team"))
        self.void = self.patch_stripe("void")


@override_settings(TEAM_SEATS=True)
class SwitchSeatTests(SeatBase):
    def test_a_switch_below_the_people_is_refused_before_stripe(self):
        self.subscribed("team_plus", "price_teamplus", count=6)
        response = self.switch("team")
        self.assertEqual(response.status_code, 409, f"{ACCOUNT}: a switch to a plan with fewer seats than people is a 409")
        self.assertEqual(response.json(), {"error": JSON_ERROR, "code": "not_enough_seats", "plan": "team", "people": 6, "seats": 5})
        self.modify.assert_not_called()
        self.void.assert_not_called()
        self.assertEqual(self.reload().plan, "team_plus", f"{ACCOUNT}: a refused switch changes no plan")

    def test_a_switch_to_enough_seats_and_an_interval_change_go_through(self):
        self.subscribed("team", "price_team", count=6)
        self.modify.return_value = make_sub("sub_A", "active", "price_teamplus")
        with mock.patch.dict("os.environ", {"STRIPE_PRICE_TEAM_ANNUAL": "price_team_year"}):
            self.assertEqual(self.switch("team_plus").json()["status"], "switched", f"{ACCOUNT}: six people fit Team Plus")
            self.set_billing("team", sub_id="sub_A")
            self.modify.return_value = make_sub("sub_A", "active", "price_team_year")
            self.assertEqual(self.switch("team", "year").status_code, 200, f"{ACCOUNT}: an interval change keeps the plan, so it is not checked")

    def test_a_stale_plan_does_not_skip_the_seat_check(self):
        self.subscribed("team", "price_scale", count=6)
        response = self.switch("team")
        self.assertEqual(response.status_code, 409, f"{ACCOUNT}: the skip reads the subscription item's plan, not the mirror's")
        self.assertEqual(response.json()["code"], "not_enough_seats")
        self.modify.assert_not_called()

    def test_waiting_invitations_do_not_count(self):
        self.subscribed("team_plus", "price_teamplus", count=5)
        for number in range(3):
            Invitation.objects.create(
                workspace=self.ws, invited_by=self.user, email=f"w{number}@example.com", token_hash=f"{number}" * 64,
                expires_at=timezone.now() + timedelta(days=7),
            )
        self.assertEqual(self.switch("team").status_code, 200, f"{ACCOUNT}: only current people count against seats")

    def test_the_count_is_read_after_the_lock(self):
        self.subscribed("team_plus", "price_teamplus", count=6)
        with CaptureQueriesContext(connection) as queries:
            self.switch("team")
        sql = [q["sql"] for q in queries.captured_queries]
        opened = next(i for i, text in enumerate(sql) if text.startswith("SAVEPOINT") or text.startswith("BEGIN"))
        locked = next(i for i, text in enumerate(sql) if i > opened and 'FROM "drop_core_workspace"' in text)
        counted = [i for i, text in enumerate(sql) if "COUNT(" in text and 'FROM "drop_core_membership"' in text]
        self.assertTrue(counted, f"{ACCOUNT}: switch counts the people")
        self.assertGreater(min(counted), locked, f"{ACCOUNT}: the people are counted after the workspace SELECT that opens the transaction")

    def test_a_former_administrator_cannot_create_the_customer_or_switch(self):
        self.subscribed("team", "price_team")
        self.set_billing("team", sub_id="sub_A")
        stale = billing_account.current_membership
        grace = second_member(self.ws, "grace")

        def demoted(user):
            found = stale(user)
            Membership.objects.filter(pk=found.pk).update(role=Membership.MEMBER)
            Membership.objects.filter(pk=grace.pk).update(role=Membership.ADMINISTRATOR)
            return found

        self.listed.reset_mock()
        with mock.patch.object(billing_account, "current_membership", demoted):
            response = self.switch("team_plus")
        self.assertEqual((response.status_code, response.json()), (403, {"error": "administrator_only"}),
                         f"{ACCOUNT}: switch reads the administrator again under the lock")
        self.listed.assert_not_called()
        self.modify.assert_not_called()


@override_settings(TEAM_SEATS=True)
class CheckoutSeatTests(SeatBase):
    def setUp(self):
        super().setUp()
        self.session = self.patch_stripe("session", return_value={"id": "cs_1", "url": "https://checkout.stripe.com/c/cs_1"})
        self.customer = self.patch_stripe("customer", return_value={"id": "cus_NEW"})
        self.listed = self.patch_stripe("list", return_value=sub_list())

    def test_a_checkout_below_the_people_is_refused(self):
        people(self.ws, 7)
        for customer in ("", "cus_A"):
            with self.subTest(customer=customer):
                Workspace.objects.filter(pk=self.ws.pk).update(stripe_customer_id=customer)
                self.ws.refresh_from_db()
                response = self.checkout("team")
                self.assertEqual(response.status_code, 409, f"{ACCOUNT}: checkout of a plan with too few seats is a 409")
                self.assertEqual(response.json(), {"error": JSON_ERROR, "code": "not_enough_seats", "plan": "team", "people": 7, "seats": 5})
                self.session.assert_not_called()
        self.customer.assert_not_called()

    def test_a_checkout_that_fits_goes_through(self):
        people(self.ws, 5)
        self.assertEqual(self.checkout("team").status_code, 200, f"{ACCOUNT}: five people fit Team")

    def test_a_former_administrator_cannot_create_the_customer(self):
        grace = second_member(self.ws, "grace")
        stale = billing_account.current_membership

        def demoted(user):
            found = stale(user)
            Membership.objects.filter(pk=found.pk).update(role=Membership.MEMBER)
            Membership.objects.filter(pk=grace.pk).update(role=Membership.ADMINISTRATOR)
            return found

        with mock.patch.object(billing_account, "current_membership", demoted):
            response = self.checkout("team")
        self.assertEqual((response.status_code, response.json()), (403, {"error": "administrator_only"}),
                         f"{ACCOUNT}: _ensure_customer reads the administrator again under the lock")
        self.customer.assert_not_called()
        self.listed.assert_not_called()

    def test_the_new_customer_gets_the_current_administrators_address(self):
        people(self.ws, 3)
        self.checkout("team")
        self.customer.assert_called_once_with(email="payer@example.com")

    def test_a_one_person_account_creates_its_customer_with_its_own_address(self):
        self.user.email = ""
        self.user.save()
        self.checkout("team")
        self.customer.assert_called_once_with(email=None)


@override_settings(TEAM_SEATS=True)
class SeatPageTests(SeatBase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(self.user)

    def said(self, response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_the_billing_page_shows_the_seat_sentence_instead_of_the_button(self):
        self.subscribed("team_plus", "price_teamplus", count=6)
        page = self.client.get("/app/billing/").content.decode()
        self.assertIn("Team has 5 seats, and your team has 6 people.", page, f"{PAGE}: a plan with too few seats shows a sentence")
        self.assertNotIn("Switch to Team ($", page, f"{PAGE}: and no button for it")
        self.assertIn("Switch to Scale ($", page, f"{PAGE}: a plan that fits keeps its button")
        confirm = self.client.get("/app/billing/switch/team/").content.decode()
        self.assertIn("Team has 5 seats, and your team has 6 people.", confirm)
        self.assertNotIn("Switch to Team</button>", confirm, f"{PAGE}: the confirmation has no button either")

    def test_a_team_over_its_own_plan_can_still_go_from_monthly_to_yearly(self):
        self.subscribed("team", "price_team", count=6)
        page = self.client.get("/app/billing/").content.decode()
        self.assertIn("Switch to Team ($", page, f"{PAGE}: the plan the team holds keeps its yearly button")
        self.assertNotIn("Team has 5 seats, and your team has 6 people.", page, f"{PAGE}: no seat sentence for the held plan")
        confirm = self.client.get("/app/billing/switch/team/?interval=year").content.decode()
        self.assertIn("Switch to Team yearly</button>", confirm, "backend/templates/dashboard/billing_switch.html: the form stays")
        self.assertNotIn("Team has 5 seats, and your team has 6 people.", confirm)

    def test_the_refusal_sentence_has_no_reference(self):
        self.subscribed("team_plus", "price_teamplus", count=6)
        with self.assertLogs("drop_billing", level="WARNING") as logs:
            response = self.client.post("/app/billing/switch/team/")
        sentence = self.said(response)
        self.assertEqual(sentence, ["Your team has 6 people, and Team has 5 seats. Remove 1 person under Members before you change to Team."])
        self.assertEqual([record.levelname for record in logs.records], ["WARNING"])
        self.modify.assert_not_called()

    def test_the_sentence_says_people_for_more_than_one(self):
        self.assertIn("Remove 2 people under", billing_page.not_enough_seats_sentence({"plan": "team", "people": 7, "seats": 5}))

    def test_a_refused_checkout_says_the_same(self):
        people(self.ws, 7)
        self.patch_stripe("customer", return_value={"id": "cus_NEW"})
        response = self.client.post("/app/billing/checkout/", {"plan": "team"})
        self.assertEqual(self.said(response), ["Your team has 7 people, and Team has 5 seats. Remove 2 people under Members before you change to Team."])


class SwitchOnOneAccountTests(SeatBase):
    def test_one_person_billing_answers_do_not_change_with_the_switch_on(self):
        self.patch_stripe("session", return_value={"id": "cs_1", "url": "https://checkout.stripe.com/c/cs_1"})
        self.patch_stripe("customer", return_value={"id": "cus_NEW"})
        self.patch_stripe("list", return_value=sub_list())
        for plan in ("team", "free"):
            with self.subTest(plan=plan):
                answers = []
                for on in (False, True):
                    Workspace.objects.filter(pk=self.ws.pk).update(stripe_customer_id="")
                    with override_settings(TEAM_SEATS=on):
                        answers.append([
                            self.api.get("/api/v1/billing/status").json(),
                            self.checkout(plan).json(),
                            self.switch("team").json(),
                        ])
                self.assertEqual(answers[0], answers[1], f"{ACCOUNT}: an account alone gets the same answers with the switch on")

    def test_an_account_alone_on_team_plus_switches_to_team_the_same_with_the_switch_on(self):
        answers = []
        for on in (False, True):
            self.subscribed("team_plus", "price_teamplus")
            with override_settings(TEAM_SEATS=on):
                answers.append(self.switch("team"))
            self.modify.assert_called_once()
            self.modify.reset_mock()
        self.assertEqual(
            [(answer.status_code, answer.json()) for answer in answers[:1]],
            [(answer.status_code, answer.json()) for answer in answers[1:]],
            f"{ACCOUNT}: a one-person account's switch answers the same with the switch on",
        )

    def test_too_few_seats_reads_nothing_with_the_switch_off(self):
        people(self.ws, 8)
        with self.assertNumQueries(0):
            self.assertIsNone(teams.too_few_seats(self.ws, "team"))
