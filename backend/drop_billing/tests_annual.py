"""Yearly billing: each paid plan has a yearly Stripe price that is bought, synced and switched to like the monthly one.

The yearly prices are read from STRIPE_PRICE_TEAM_ANNUAL, STRIPE_PRICE_TEAMPLUS_ANNUAL and STRIPE_PRICE_SCALE_ANNUAL, names
PROPOSED on 2026-10-30 for the founder to confirm: a rename changes account._price_id, the README and ANNUAL_ENV below together.
A yearly price maps to the same plan as its monthly one, so a yearly subscriber is on the same plan slug, with the same caps.
Only Stripe-verified state puts a workspace on a plan (AGENTS.md, invariant 3). Stripe is mocked as in tests_fix_billing: every
outbound call is patched, and an unpatched one fails against a closed port.
"""

import inspect
import os
from unittest import mock

from drop_billing import account
from drop_billing.models import Subscription
from drop_billing.tests_fix_billing import ENV, FixBillingBase, checkout_completed, make_event, make_sub, sub_list
from drop_core.plans import PLANS

ANNUAL_ENV = {
    "STRIPE_PRICE_TEAM_ANNUAL": "price_team_annual",
    "STRIPE_PRICE_TEAMPLUS_ANNUAL": "price_teamplus_annual",
    "STRIPE_PRICE_SCALE_ANNUAL": "price_scale_annual",
}
MONTHLY = {"team": ENV["STRIPE_PRICE_TEAM"], "team_plus": ENV["STRIPE_PRICE_TEAMPLUS"], "scale": ENV["STRIPE_PRICE_SCALE"]}
YEARLY = {"team": "price_team_annual", "team_plus": "price_teamplus_annual", "scale": "price_scale_annual"}
PRICES = {"month": MONTHLY, "year": YEARLY}
STRIPE_URL = "https://checkout.stripe.com/c/cs_1"
FILE = "backend/drop_billing/account.py"


class AnnualBase(FixBillingBase):
    """FixBillingBase with the three yearly prices set as well."""

    longMessage = False

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, ANNUAL_ENV)
        env.start()
        self.addCleanup(env.stop)

    def subscribe(self, plan, interval, status="active"):
        """The workspace pays for `plan` every `interval`, in the database and in Stripe's list."""
        self.set_billing(plan, sub_id="sub_A", status=status)
        Subscription.objects.filter(workspace=self.ws).update(billing_interval=interval)
        return self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", status, PRICES[interval][plan])))

    def mirror(self):
        return Subscription.objects.get(workspace=self.ws)


class AnnualPriceMappingTests(AnnualBase):
    """The explicit plan-and-interval-to-price mapping in drop_billing/account.py."""

    def test_each_plan_and_interval_reads_its_own_variable_both_ways(self):
        self.assertEqual(account.INTERVALS, ("month", "year"), f"{FILE}: the intervals are Stripe's month and year")
        for plan in account.PAID_PLANS:
            with self.subTest(plan=plan):
                self.assertEqual(account._price_id(plan), MONTHLY[plan], f"{FILE}: _price_id() with no interval is the monthly price")
                self.assertEqual(account._price_id(plan, "month"), MONTHLY[plan], f"{FILE}: _price_id(plan, 'month')")
                self.assertEqual(account._price_id(plan, "year"), YEARLY[plan], f"{FILE}: _price_id(plan, 'year') reads the *_ANNUAL variable")
                self.assertEqual(account._plan_and_interval(MONTHLY[plan]), (plan, "month"), f"{FILE}: _plan_and_interval() of a monthly price")
                self.assertEqual(account._plan_and_interval(YEARLY[plan]), (plan, "year"), f"{FILE}: _plan_and_interval() of a yearly price")
                self.assertEqual(account._plan_for_price_id(YEARLY[plan]), plan, f"{FILE}: a yearly price maps to the same plan as the monthly one")
        self.assertIsNone(account._plan_and_interval("price_unknown"), f"{FILE}: a price of no plan maps to nothing")
        self.assertIsNone(account._plan_and_interval(""), f"{FILE}: no price maps to nothing")
        self.assertIsNone(account._price_id("team", "week"), f"{FILE}: an interval that is not sold has no price")
        self.assertIsNone(account._price_id("free", "year"), f"{FILE}: Free has no yearly price")

    def test_each_name_is_written_out_and_none_is_derived_from_a_slug(self):
        source = inspect.getsource(account._price_id)
        for name in ANNUAL_ENV:
            self.assertIn(f'os.environ.get("{name}")', source, f"{FILE}: _price_id() names {name} in full")
        self.assertNotIn("upper()", source, f"{FILE}: _price_id() maps by name, never from the slug")
        with mock.patch.dict(os.environ, {"STRIPE_PRICE_TEAM_PLUS_ANNUAL": "price_wrong_name"}):
            os.environ.pop("STRIPE_PRICE_TEAMPLUS_ANNUAL")
            self.assertIsNone(account._price_id("team_plus", "year"), f"{FILE}: the slug's STRIPE_PRICE_TEAM_PLUS_ANNUAL is never read")
            self.assertIsNone(account._plan_for_price_id("price_wrong_name"), f"{FILE}: nor reverse-mapped")

    def test_an_unset_yearly_variable_refuses_a_yearly_checkout_and_leaves_monthly_selling(self):
        customer = self.patch_stripe("customer", return_value={"id": "cus_NEW"})
        session = self.patch_stripe("session", return_value={"id": "cs_1", "url": STRIPE_URL})
        self.patch_stripe("list", return_value=sub_list())
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("STRIPE_PRICE_TEAM_ANNUAL")
            resp = self.api.post("/api/v1/billing/checkout", {"plan": "team", "interval": "year"}, format="json")
            self.assertEqual((resp.status_code, resp.json()), (503, {"error": "billing_not_configured"}),
                             f"{FILE}: a yearly price that is not set is the 503 of any unset price, before Stripe is asked")
            customer.assert_not_called()
            session.assert_not_called()
            resp = self.api.post("/api/v1/billing/checkout", {"plan": "team"}, format="json")
        self.assertEqual(resp.status_code, 200, f"{FILE}: monthly Team sells without the yearly variable")
        self.assertEqual(session.call_args.kwargs["line_items"], [{"price": MONTHLY["team"], "quantity": 1}])

    def test_an_interval_that_is_neither_is_a_400_with_a_code_and_no_stripe_call(self):
        calls = {name: self.patch_stripe(name) for name in ("list", "modify", "customer", "session")}
        self.set_billing("team", sub_id="sub_A")
        for interval in ("week", "Year", "yearly", 12, ["year"]):
            for endpoint in ("checkout", "switch"):
                with self.subTest(interval=interval, endpoint=endpoint):
                    resp = self.api.post(f"/api/v1/billing/{endpoint}", {"plan": "team", "interval": interval}, format="json")
                    self.assertEqual((resp.status_code, resp.json()),
                                     (400, {"error": "Choose the month or year billing interval.", "code": "invalid_interval"}),
                                     f"{FILE}: an interval that is not month or year is refused with the standard envelope")
        for name, patched in calls.items():
            self.assertFalse(patched.called, f"{FILE}: a refused interval reached stripe {name}")

    def test_the_unmapped_price_log_names_every_price_variable(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", "price_unknown")))
        with self.assertLogs("drop_billing", level="ERROR") as logs:
            self.post_event(make_event("customer.subscription.updated", make_sub("sub_A", "active", "price_unknown")))
        line = next(m for m in logs.output if "maps to no plan" in m)
        for name in ("STRIPE_PRICE_TEAM,", "STRIPE_PRICE_TEAMPLUS,", "STRIPE_PRICE_SCALE,", *ANNUAL_ENV):
            self.assertIn(name, line, f"{FILE}: the log line for an unknown price names {name}")
        self.assertNotIn("STRIPE_PRICE_TEAM_PLUS", line, f"{FILE}: and never the slug's name")


class AnnualCheckoutTests(AnnualBase):
    """POST /api/v1/billing/checkout with interval year, the other two ways in, and the webhook that grants the plan."""

    def setUp(self):
        super().setUp()
        self.session = self.patch_stripe("session", return_value={"id": "cs_1", "url": STRIPE_URL})
        self.customer = self.patch_stripe("customer", return_value={"id": "cus_NEW"})

    def test_checkout_opens_a_stripe_session_on_the_yearly_price_of_each_plan(self):
        self.patch_stripe("list", return_value=sub_list())
        for plan in account.PAID_PLANS:
            with self.subTest(plan=plan):
                resp = self.api.post("/api/v1/billing/checkout", {"plan": plan, "interval": "year"}, format="json")
                self.assertEqual((resp.status_code, resp.json()), (200, {"checkout_url": STRIPE_URL}), resp.content)
                kwargs = self.session.call_args.kwargs
                self.assertEqual(kwargs["line_items"], [{"price": YEARLY[plan], "quantity": 1}],
                                 f"{FILE}: a yearly checkout is a subscription on the yearly price, whose interval Stripe bills")
                self.assertEqual((kwargs["mode"], kwargs["managed_payments"], kwargs["metadata"]),
                                 ("subscription", {"enabled": True}, {"plan": plan}), f"{FILE}: the session is the monthly one's but for its price")
                self.assertEqual(self.reload().plan, "free", f"{FILE}: opening checkout never sets a plan (invariant 3)")

    def test_a_body_without_an_interval_is_monthly_as_before(self):
        self.patch_stripe("list", return_value=sub_list())
        for body in ({"plan": "scale"}, {"plan": "scale", "interval": None}, {"plan": "scale", "interval": ""}, {"plan": "scale", "interval": "month"}):
            with self.subTest(body=body):
                self.assertEqual(self.api.post("/api/v1/billing/checkout", body, format="json").status_code, 200)
                self.assertEqual(self.session.call_args.kwargs["line_items"], [{"price": MONTHLY["scale"], "quantity": 1}],
                                 "backend/drop_billing/views.py: a published client sends no interval and must still buy monthly")

    def test_the_dashboard_checkout_sends_the_yearly_price(self):
        self.patch_stripe("list", return_value=sub_list())
        self.client.force_login(self.user)
        response = self.client.post("/app/billing/checkout/", {"plan": "team_plus", "interval": "year"})
        self.assertEqual((response.status_code, response["Location"]), (303, STRIPE_URL), "backend/dashboard/views.py: a yearly upgrade goes to Stripe")
        self.assertEqual(self.session.call_args.kwargs["line_items"], [{"price": YEARLY["team_plus"], "quantity": 1}],
                         "backend/dashboard/views.py: billing_checkout passes the form's interval")

    def test_the_plan_aware_signup_sends_a_signed_in_user_to_stripe_on_the_yearly_price(self):
        self.patch_stripe("list", return_value=sub_list())
        self.hook.force_login(self.user)
        response = self.hook.post("/accounts/signup/?plan=scale&interval=year")
        self.assertEqual((response.status_code, response["Location"]), (302, STRIPE_URL), "backend/drop_core/views.py: signup with ?interval=year")
        self.assertEqual(self.session.call_args.kwargs["line_items"], [{"price": YEARLY["scale"], "quantity": 1}],
                         "backend/drop_core/views.py: ?interval=year rides along to checkout")
        self.hook.post("/accounts/signup/?plan=scale&interval=weekly")
        self.assertEqual(self.session.call_args.kwargs["line_items"], [{"price": MONTHLY["scale"], "quantity": 1}],
                         "backend/drop_core/views.py: an interval that is not year is monthly, as a plan that is not sold is free")

    def test_a_live_subscription_still_blocks_a_yearly_checkout(self):
        self.subscribe("team", "month")
        resp = self.api.post("/api/v1/billing/checkout", {"plan": "team", "interval": "year"}, format="json")
        self.assertEqual((resp.status_code, resp.json()["code"]), (409, "already_subscribed"),
                         f"{FILE}: a subscriber moves to yearly through switch, never a second checkout")
        self.session.assert_not_called()

    def test_a_paid_yearly_checkout_puts_the_workspace_on_the_plan_from_stripe_state(self):
        for plan in account.PAID_PLANS:
            with self.subTest(plan=plan):
                self.set_billing("free", customer="cus_A")
                Subscription.objects.filter(workspace=self.ws).delete()
                self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", YEARLY[plan])))
                self.assertEqual(self.post_event(checkout_completed(plan=plan)).status_code, 200)
                ws = self.reload()
                self.assertEqual((ws.plan, ws.stripe_subscription_id), (plan, "sub_A"), f"{FILE}: the yearly price grants its plan")
                self.assertEqual((self.mirror().status, self.mirror().billing_interval), ("active", "year"),
                                 f"{FILE}: _apply_subscriptions records the interval of the price Stripe reports")
                body = self.api.get("/api/v1/billing/status").json()
                caps = PLANS[plan]
                self.assertEqual(
                    (body["plan"], body["billing_interval"], body["caps"], body["price_usd"]),
                    (plan, "year", {"uploads_per_month": caps["uploads"], "ttl_days": caps["ttl_days"], "keys": caps["keys"],
                                    "storage_gb": caps["storage_gb"]}, caps["price_usd"]),
                    f"{FILE}: status() of a yearly plan has the monthly plan's caps; price_usd stays the monthly price")

    def test_a_monthly_subscription_records_month(self):
        self.set_billing("free", customer="cus_A")
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", MONTHLY["team"])))
        self.post_event(checkout_completed(plan="team"))
        self.assertEqual((self.reload().plan, self.mirror().billing_interval), ("team", "month"))

    def test_the_checkout_metadata_alone_grants_nothing(self):
        # The session says yearly Scale, but Stripe's subscription is on Team's monthly price: Stripe's subscription decides.
        self.set_billing("free", customer="cus_A")
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", MONTHLY["team"])))
        self.post_event(checkout_completed(plan="scale"))
        self.assertEqual((self.reload().plan, self.mirror().billing_interval), ("team", "month"))

    def test_dunning_on_a_yearly_plan_follows_the_monthly_one(self):
        for status, plan in (("past_due", "scale"), ("unpaid", "free"), ("canceled", "free"), ("paused", "free"), ("incomplete_expired", "free")):
            with self.subTest(status=status):
                self.set_billing("scale", sub_id="sub_A")
                self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", status, YEARLY["scale"])))
                self.post_event(make_event("customer.subscription.updated", make_sub("sub_A", status, YEARLY["scale"])))
                self.assertEqual(self.reload().plan, plan, f"{FILE}: {status} on a yearly price is what it is on a monthly one")

    def test_status_names_the_interval_only_while_there_is_a_subscription(self):
        self.assertEqual(self.api.get("/api/v1/billing/status").json()["billing_interval"], "", f"{FILE}: no subscription, no interval")
        self.subscribe("team", "year")
        self.assertEqual(self.api.get("/api/v1/billing/status").json()["billing_interval"], "year")
        self.set_billing("free", customer="cus_A")  # lapsed: the mirror still names the old subscription's interval
        self.assertEqual(self.api.get("/api/v1/billing/status").json()["billing_interval"], "", f"{FILE}: a lapsed subscription names none")

    def test_a_missing_customer_forgets_the_interval(self):
        self.subscribe("team", "year")
        account._forget_missing_customer(self.ws, "cus_A")
        self.assertEqual(self.mirror().billing_interval, "", f"{FILE}: _forget_missing_customer resets the interval with the rest of the mirror")


class AnnualSwitchTests(AnnualBase):
    """POST /api/v1/billing/switch between monthly and yearly swaps the price item in place, with proration."""

    def switch(self, plan, interval=None):
        body = {"plan": plan} if interval is None else {"plan": plan, "interval": interval}
        return self.api.post("/api/v1/billing/switch", body, format="json")

    def test_between_monthly_and_yearly_the_price_item_is_swapped_in_place(self):
        moves = (("team", "month", "team", "year"), ("team", "year", "team", "month"), ("team", "month", "scale", "year"),
                 ("scale", "year", "team_plus", "month"), ("team_plus", "year", "scale", "year"), ("scale", "year", "team", "year"))
        for plan, interval, target, target_interval in moves:
            with self.subTest(source=(plan, interval), target=(target, target_interval)):
                self.subscribe(plan, interval)
                modify = self.patch_stripe("modify", return_value=make_sub("sub_A", "active", PRICES[target_interval][target]))
                resp = self.switch(target, target_interval)
                self.assertEqual((resp.status_code, resp.json()), (200, {"plan": target, "status": "switched", "subscription_status": "active"}),
                                 f"{FILE}: a switch between intervals answers as any switch does")
                kwargs = modify.call_args.kwargs
                self.assertEqual(
                    (kwargs["items"], kwargs["proration_behavior"], kwargs["payment_behavior"]),
                    ([{"id": "si_1", "price": PRICES[target_interval][target]}], "always_invoice", "pending_if_incomplete"),
                    f"{FILE}: the same in-place swap as between monthly plans")
                self.assertEqual((self.reload().plan, self.mirror().billing_interval), (target, target_interval),
                                 f"{FILE}: the plan and interval follow what Stripe returned")

    def test_a_switch_without_an_interval_is_monthly_as_before(self):
        self.subscribe("team", "year")
        modify = self.patch_stripe("modify", return_value=make_sub("sub_A", "active", MONTHLY["scale"]))
        self.assertEqual(self.switch("scale").status_code, 200)
        self.assertEqual(modify.call_args.kwargs["items"], [{"id": "si_1", "price": MONTHLY["scale"]}],
                         "backend/drop_billing/views.py: a body with no interval asks for the monthly price")

    def test_the_price_held_is_unchanged_and_asks_stripe_to_change_nothing(self):
        self.subscribe("team", "year")
        modify = self.patch_stripe("modify")
        resp = self.switch("team", "year")
        self.assertEqual((resp.status_code, resp.json()), (200, {"plan": "team", "status": "unchanged"}), f"{FILE}: the plan and interval held")
        modify.assert_not_called()

    def test_an_unpaid_switch_to_yearly_is_a_402_and_the_plan_and_interval_stay(self):
        self.subscribe("team", "month")
        pending = make_sub("sub_A", "active", MONTHLY["team"], pending_update={"subscription_items": [{"price": YEARLY["team"]}]})
        pending["latest_invoice"] = {"id": "in_1", "hosted_invoice_url": "https://invoice.stripe.com/i/in_1"}
        self.patch_stripe("modify", return_value=pending)
        resp = self.switch("team", "year")
        self.assertEqual((resp.status_code, resp.json()["code"], resp.json()["plan"], resp.json()["invoice_url"]),
                         (402, "payment_required", "team", "https://invoice.stripe.com/i/in_1"), f"{FILE}: a yearly invoice that is not paid is a 402")
        self.assertEqual((self.reload().plan, self.mirror().billing_interval), ("team", "month"), f"{FILE}: and nothing changes")

    def test_asking_for_the_price_held_cancels_a_switch_still_waiting_for_payment(self):
        for held, waiting in (("month", "year"), ("year", "month")):
            with self.subTest(held=held, waiting=waiting):
                self.set_billing("team", sub_id="sub_A")
                current = make_sub("sub_A", "active", PRICES[held]["team"], pending_update={"subscription_items": [{"price": PRICES[waiting]["team"]}]})
                current["latest_invoice"] = {"id": "in_1"}
                self.patch_stripe("list", return_value=sub_list(current))
                void = self.patch_stripe("void")
                modify = self.patch_stripe("modify")
                resp = self.switch("team", held)
                self.assertEqual((resp.status_code, resp.json()), (200, {"plan": "team", "status": "pending_cancelled"}),
                                 f"{FILE}: asking for the price held, {held}ly, voids the invoice of the switch to {waiting}ly")
                void.assert_called_once_with("in_1")
                modify.assert_not_called()

    def test_with_a_yearly_and_a_monthly_subscription_the_higher_plan_wins(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe("list", return_value=sub_list(
            make_sub("sub_A", "active", YEARLY["team"], created=1),
            make_sub("sub_B", "active", MONTHLY["scale"], created=2, item_id="si_2"),
        ))
        with self.assertLogs("drop_billing", level="ERROR"):
            self.post_event(make_event("customer.subscription.created", make_sub("sub_B", "active", MONTHLY["scale"])))
        self.assertEqual((self.reload().plan, self.mirror().billing_interval), ("scale", "month"),
                         f"{FILE}: the rank is the plan's, whatever the interval")
