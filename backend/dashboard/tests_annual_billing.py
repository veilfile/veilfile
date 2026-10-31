"""Yearly billing on the dashboard's Billing page (spec 4.8): the yearly prices offered, the switch between monthly and yearly
billing and its confirmation page, and the unpaid-switch hint for a yearly price.

Stripe is never reached: BillingCase (dashboard.tests_billing) patches the library as drop_billing.tests_fix_billing does, and the
yearly prices are set as drop_billing.tests_annual sets them. Every figure is read from drop_core/plans.py. Every class sets
longMessage = False, so that a failure prints its own message, which names the file and the fix, and not the page it searched.
"""
import os
from unittest import mock

from dashboard import billing_page
from dashboard.tests_billing import INVOICE, PAGE, SWITCH_PAGE, VIEWS, BillingCase, forms_in, links_in, value_of
from drop_billing.models import Subscription
from drop_billing.tests_annual import ANNUAL_ENV, MONTHLY, PRICES, YEARLY
from drop_billing.tests_fix_billing import make_sub, sub_list
from drop_core.plans import ANNUAL_PRICE_USD, PLANS, plan_name
from pages.tests_chapters import visible_text

FILE = "backend/dashboard/billing_page.py"
TWO_MONTHS = "Yearly billing costs the price of ten months, so two months are free."


class AnnualCase(BillingCase):
    """BillingCase with the yearly prices set, and a subscription on a monthly or yearly price."""

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, ANNUAL_ENV)
        env.start()
        self.addCleanup(env.stop)

    def hold(self, plan, interval, status="active"):
        """The workspace pays for `plan` every `interval`: in the database, in the mirror the webhook writes, and in Stripe's list.
        An `interval` of "" is a mirror written before yearly billing, of a subscription on the monthly price."""
        self.set_billing(plan, customer="cus_A", sub_id="sub_A", status=status)
        Subscription.objects.filter(workspace=self.ws).update(billing_interval=interval)
        return self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", status, PRICES[interval or "month"][plan])))

    def switch_to(self, plan, interval="month"):
        url = self.switch_url(plan)
        return f"{url}?interval=year" if interval == "year" else url


class YearlyOptionsTests(AnnualCase):
    def test_a_free_workspace_is_offered_each_plan_yearly_as_a_post_to_checkout(self):
        html = self.client.get(self.billing).content.decode()
        self.assertIn(TWO_MONTHS, visible_text(html), f"{PAGE}: say what yearly billing costs, two months free, above its buttons")
        yearly = [form for form in forms_in(html) if form["attrs"].get("action") == self.checkout and value_of(form, "interval") == "year"]
        self.assertEqual([(value_of(form, "plan"), form["buttons"]) for form in yearly],
                         [(slug, [f"Upgrade to {plan_name(slug)} (${ANNUAL_PRICE_USD[slug]}/yr)"]) for slug in billing_page.paid_plans()],
                         f"{PAGE}: one POST form to checkout for each paid plan yearly, its price from plans.py")
        for form in yearly:
            self.assertTrue(value_of(form, "csrfmiddlewaretoken"), f"{PAGE}: each yearly form writes {{% csrf_token %}} itself")

    def test_a_monthly_subscriber_is_offered_every_yearly_price_its_own_plan_included(self):
        self.hold("team", "month")
        html = self.client.get(self.billing).content.decode()
        self.assertIn("Your subscription is billed monthly.", visible_text(html), f"{PAGE}: say how the subscription is billed")
        switches = [(attrs.get("href"), label) for attrs, label in links_in(html) if "/billing/switch/" in attrs.get("href", "")]
        self.assertEqual(switches,
                         [(self.switch_to(slug), f"Switch to {plan_name(slug)} (${PLANS[slug]['price_usd']}/mo)") for slug in ("team_plus", "scale")]
                         + [(self.switch_to(slug, "year"), f"Switch to {plan_name(slug)} (${ANNUAL_PRICE_USD[slug]}/yr)") for slug in billing_page.paid_plans()],
                         f"{PAGE}: Team monthly may move to any other price, Team yearly among them, each through its confirmation page")

    def test_a_yearly_subscriber_is_offered_every_monthly_price_and_the_other_yearly_ones(self):
        self.hold("team", "year")
        html = self.client.get(self.billing).content.decode()
        self.assertIn("Your subscription is billed yearly.", visible_text(html), f"{PAGE}: say how the subscription is billed")
        switches = [(attrs.get("href"), label) for attrs, label in links_in(html) if "/billing/switch/" in attrs.get("href", "")]
        self.assertEqual(switches,
                         [(self.switch_to(slug), f"Switch to {plan_name(slug)} (${PLANS[slug]['price_usd']}/mo)") for slug in billing_page.paid_plans()]
                         + [(self.switch_to(slug, "year"), f"Switch to {plan_name(slug)} (${ANNUAL_PRICE_USD[slug]}/yr)") for slug in ("team_plus", "scale")],
                         f"{PAGE}: Team yearly is not offered to itself, and Team monthly is offered")

    def test_a_subscription_whose_interval_the_mirror_has_not_recorded_reads_as_monthly_and_says_nothing(self):
        self.hold("team", "")
        html = self.client.get(self.billing).content.decode()
        self.assertNotIn("Your subscription is billed", visible_text(html), f"{PAGE}: an interval the mirror does not know is not named")
        hrefs = [attrs.get("href") for attrs, _ in links_in(html)]
        self.assertNotIn(self.switch_to("team"), hrefs, f"{FILE}: page_context() reads an unknown interval as monthly, as every older subscription is")
        self.assertIn(self.switch_to("team", "year"), hrefs, f"{FILE}: and offers the plan's yearly price")


class SwitchConfirmationTests(AnnualCase):
    def confirm(self, plan, interval="month"):
        response = self.client.get(self.switch_to(plan, interval))
        self.assertEqual(response.status_code, 200, f"{VIEWS}: the confirmation page answers 200")
        html = response.content.decode()
        return visible_text(html), [button for form in forms_in(html) for button in form["buttons"]]

    def test_monthly_to_yearly_says_the_year_starts_today_and_is_charged_now(self):
        self.hold("team", "month")
        calls = self.no_stripe()
        text, buttons = self.confirm("team", "year")
        self.assertIn("Do you want to switch to Team ($40/yr)?", text, f"{SWITCH_PAGE}: the heading names the yearly price")
        self.assertIn("Your billing becomes yearly from today. You'll be charged $40 now, less a credit for the unused part of your current "
                      "month, then $40/yr at each renewal. If that payment doesn't go through, you stay on Team, billed monthly.", text,
                      f"{SWITCH_PAGE}: Stripe starts a new period on the day of an interval change and invoices it at once")
        self.assertNotIn("for the rest of this billing period", text, f"{SWITCH_PAGE}: React's upgrade sentence is wrong for a change of interval")
        self.assertNotIn("limits apply", text, f"{SWITCH_PAGE}: the same plan keeps its limits, so none are named")
        self.assertEqual(buttons, ["Switch to Team yearly"], f"{FILE}: switch_context() labels the button with the interval")
        self.assertNoStripe(calls, "a GET of the confirmation page reached Stripe")

    def test_monthly_to_a_lower_plan_yearly_names_its_limits(self):
        self.hold("scale", "month")
        text, _ = self.confirm("team", "year")
        self.assertIn("Once it is paid, Team limits apply. Existing API keys and links are not removed.", text,
                      f"{SWITCH_PAGE}: a lower plan's limits are named, as React's downgrade sentence names them")

    def test_yearly_to_monthly_says_the_year_left_is_credited(self):
        self.hold("team", "year")
        text, buttons = self.confirm("team")
        self.assertIn("Do you want to switch to Team ($4/mo)?", text, f"{SWITCH_PAGE}: the heading names the monthly price")
        self.assertIn("Your billing becomes monthly from today. The unused part of your current year is credited against your first month "
                      "at $4/mo, and any credit left over goes toward your next invoices rather than back to your card. If a payment is "
                      "due and doesn't go through, you stay on Team yearly.", text, f"{SWITCH_PAGE}: the sentence for a move back to monthly")
        self.assertNotIn("limits apply", text, f"{SWITCH_PAGE}: the same plan keeps its limits, so none are named")
        self.assertEqual(buttons, ["Switch to Team monthly"], f"{FILE}: switch_context() labels the button with the interval")

    def test_yearly_to_a_lower_plan_monthly_names_its_limits(self):
        self.hold("scale", "year")
        text, buttons = self.confirm("team")
        self.assertIn("Team limits apply as soon as the switch is made. Existing API keys and links are not removed. If a payment is due and "
                      "doesn't go through, you stay on Scale yearly.", text,
                      f"{SWITCH_PAGE}: a lower plan's limits are named, and only once the switch is made, since a payment may be due")
        self.assertEqual(buttons, ["Switch to Team monthly"], f"{FILE}: switch_context()")

    def test_between_two_yearly_prices_the_react_sentences_name_the_yearly_price(self):
        self.hold("team", "year")
        text, buttons = self.confirm("scale", "year")
        self.assertIn("Do you want to switch to Scale ($120/yr)?", text, f"{SWITCH_PAGE}: the heading")
        self.assertIn("You'll be charged now for the prorated difference for the rest of this billing period, then $120/yr from your "
                      "next renewal. If that payment doesn't go through, you stay on Team yearly.", text,
                      f"{SWITCH_PAGE}: within one interval React's upgrade sentence holds, with the yearly price")
        self.assertEqual(buttons, ["Switch to Scale yearly"], f"{FILE}: switch_context()")

    def test_monthly_switches_read_as_before(self):
        self.hold("team", "month")
        text, buttons = self.confirm("scale")
        self.assertIn("Do you want to switch to Scale ($12/mo)?", text, f"{SWITCH_PAGE}: a monthly switch's heading is unchanged")
        self.assertEqual(buttons, ["Switch to Scale"], f"{SWITCH_PAGE}: and so is its button")

    def test_the_price_held_has_nothing_to_confirm(self):
        self.hold("team", "year")
        response = self.client.get(self.switch_to("team", "year"))
        self.assertSentTo(response, self.billing, f"{VIEWS}: the plan and interval held go back to Billing")
        self.assertEqual(self.said(response), ["You're already on the Team plan, billed yearly."], f"{FILE}: switch_result() names the interval")

    def test_an_interval_that_is_neither_is_the_shells_404(self):
        self.hold("team", "month")
        calls = self.no_stripe()
        for method in ("get", "post"):
            for query in ("?interval=week", "?interval=Year", "?interval="):
                with self.subTest(method=method, query=query):
                    response = getattr(self.client, method)(self.switch_url("scale") + query)
                    self.assertEqual(response.status_code, 404, f"{VIEWS}: billing_switch() answers an interval it does not sell with the shell's 404")
        self.assertNoStripe(calls, "a refused interval reached Stripe")


class YearlySwitchTests(AnnualCase):
    def test_the_post_switches_to_the_yearly_price_and_says_so(self):
        self.hold("team", "month")
        modify = self.patch_stripe("modify", return_value=make_sub("sub_A", "active", YEARLY["team"]))
        response = self.client.post(self.switch_to("team", "year"))
        self.assertSentTo(response, self.billing, f"{VIEWS}: a switch goes back to Billing")
        self.assertEqual(modify.call_args.kwargs["items"], [{"id": "si_1", "price": YEARLY["team"]}],
                         f"{VIEWS}: the POST to ?interval=year asks billing.switch for the yearly price")
        self.assertEqual(self.said(response), ["You're now on the Team plan, billed yearly."], f"{FILE}: switch_result() for a yearly switch")
        self.assertIn("Your subscription is billed yearly.", self.page(), f"{PAGE}: Billing then shows the yearly billing Stripe reported")

    def test_the_post_back_to_monthly(self):
        self.hold("team", "year")
        modify = self.patch_stripe("modify", return_value=make_sub("sub_A", "active", MONTHLY["team"]))
        response = self.client.post(self.switch_to("team"))
        self.assertEqual(modify.call_args.kwargs["items"], [{"id": "si_1", "price": MONTHLY["team"]}], f"{VIEWS}: no interval is monthly")
        self.assertEqual(self.said(response), ["You're now on the Team plan."], f"{FILE}: the monthly sentence is React's, as before")
        self.assertIn("Your subscription is billed monthly.", self.page(), f"{PAGE}: Billing shows the monthly billing")

    def test_an_unpaid_switch_to_yearly_is_remembered_with_its_interval(self):
        self.hold("team", "month")
        pending = make_sub("sub_A", "active", MONTHLY["team"], pending_update={"subscription_items": [{"price": YEARLY["team"]}]})
        pending["latest_invoice"] = {"id": "in_1", "hosted_invoice_url": INVOICE}
        self.patch_stripe("modify", return_value=pending)
        self.client.post(self.switch_to("team", "year"))
        entry = self.client.session[billing_page.PENDING_KEY]
        self.assertEqual((entry["plan"], entry["interval"], entry["invoice_url"]), ("team", "year", INVOICE),
                         f"{VIEWS}: remember_pending() keeps the interval the switch named")
        html = self.client.get(self.billing).content.decode()
        self.assertIn("Your switch to Team yearly is waiting for payment.", visible_text(html), f"{FILE}: pending_for() names the yearly switch")
        keep = [form for form in forms_in(html) if form["buttons"] == ["Keep Team"]]
        self.assertEqual([form["attrs"].get("action") for form in keep], [self.switch_to("team")],
                         f"{PAGE}: Keep asks for the price held, Team monthly, which voids the yearly invoice")

    def test_keep_on_a_yearly_plan_voids_the_waiting_switch_and_changes_no_price(self):
        self.set_billing("team", customer="cus_A", sub_id="sub_A")
        Subscription.objects.filter(workspace=self.ws).update(billing_interval="year")
        current = make_sub("sub_A", "active", YEARLY["team"], pending_update={"subscription_items": [{"price": MONTHLY["scale"]}]})
        current["latest_invoice"] = {"id": "in_1"}
        self.patch_stripe("list", return_value=sub_list(current))
        void = self.patch_stripe("void")
        modify = self.patch_stripe("modify")
        self.pending(plan="scale", interval="month")
        response = self.client.post(self.switch_to("team", "year"))
        self.assertSentTo(response, self.billing, f"{VIEWS}: Keep goes back to Billing")
        void.assert_called_once_with("in_1")
        modify.assert_not_called()
        self.assertEqual(self.said(response), ["The pending switch was canceled. You stay on the Team plan, billed yearly."],
                         f"{FILE}: switch_result() for Keep on a yearly plan")
        self.assertNotIn(billing_page.PENDING_KEY, self.client.session, f"{VIEWS}: Keep forgets the hint")

    def test_a_waiting_switch_back_to_monthly_says_monthly(self):
        self.hold("team", "year")
        self.pending(plan="team", interval="month")
        self.assertIn("Your switch to Team monthly is waiting for payment.", self.page(),
                      f"{FILE}: pending_for() says monthly beside Keep Team yearly")

    def test_keep_on_a_yearly_plan_asks_for_the_yearly_price(self):
        self.hold("team", "year")
        self.pending(plan="scale", interval="month")
        html = self.client.get(self.billing).content.decode()
        self.assertIn("Your switch to Scale monthly is waiting for payment.", visible_text(html),
                      f"{FILE}: pending_for() of a switch from yearly to monthly names the interval")
        keep = [form for form in forms_in(html) if form["buttons"] == ["Keep Team yearly"]]
        self.assertEqual([form["attrs"].get("action") for form in keep], [self.switch_to("team", "year")],
                         f"{PAGE}: Keep on a yearly plan posts ?interval=year; without it, Keep would move the subscription to monthly")

    def test_an_entry_from_before_yearly_billing_is_a_monthly_one(self):
        self.hold("team", "month")
        self.pending()  # {"plan": "scale", "invoice_url", "at"}, with no interval
        self.assertIn("Your switch to Scale is waiting for payment.", self.page(), f"{FILE}: pending_for() reads an entry with no interval as monthly")
        self.hold("scale", "month")
        self.pending()
        self.assertNotIn("is waiting for payment", self.page(), f"{FILE}: and forgets it once the workspace is on Scale monthly")

    def test_a_paid_plan_with_no_yearly_price_is_offered_monthly_only(self):
        with mock.patch.dict(PLANS, {"pro": {**PLANS["scale"], "price_usd": 30}}):
            self.assertEqual([option["slug"] for option in billing_page.yearly_options("free")], ["team", "team_plus", "scale"],
                             f"{FILE}: yearly_options() offers only a plan with a yearly price in plans.py")
            self.assertFalse(billing_page.for_sale("pro", "year"), f"{FILE}: for_sale()")
            self.assertTrue(billing_page.for_sale("pro", "month"), f"{FILE}: for_sale()")
            self.assertEqual(self.client.get(self.billing).status_code, 200, f"{PAGE}: the page still draws")
            self.hold("team", "month")
            self.assertEqual(self.client.get(self.switch_to("pro", "year")).status_code, 404, f"{VIEWS}: a yearly price that does not exist is the 404")

    def test_the_hint_holds_only_while_it_is_true_for_plan_and_interval(self):
        entry = {"plan": "team", "interval": "year", "invoice_url": INVOICE, "at": 1000}
        for why, stored, plan, interval, holds in (
            ("Team monthly waiting for Team yearly", entry, "team", "month", True),
            ("already on Team yearly", entry, "team", "year", False),
            ("an interval that is not sold", dict(entry, interval="week"), "team", "month", False),
            ("an interval that is not a word", dict(entry, interval=["year"]), "team", "month", False),
        ):
            with self.subTest(why):
                session = {billing_page.PENDING_KEY: dict(stored)}
                found = billing_page.pending_for(session, plan, now=1060, current_interval=interval)
                self.assertEqual(found is not None, holds, f"{FILE}: pending_for(): {why}")
