"""Team Plus is bought, switched to and synced like Team and Scale.

Its Stripe price is read from STRIPE_PRICE_TEAMPLUS, the name the founder chose on 2026-10-16. The name a slug would give,
STRIPE_PRICE_TEAM_PLUS, is never read. Only Stripe-verified state puts a workspace on the plan (AGENTS.md, invariant 3).
Stripe is mocked as in tests_fix_billing: every outbound call is patched, and an unpatched one fails against a closed port.
"""

import os
from unittest import mock

from drop_billing import account
from drop_billing.models import Subscription
from drop_billing.tests_fix_billing import ENV, FixBillingBase, checkout_completed, make_event, make_sub, sub_list
from drop_core.plans import PLANS

TEAM_PLUS_PRICE = ENV["STRIPE_PRICE_TEAMPLUS"]
PRICES = {"team": ENV["STRIPE_PRICE_TEAM"], "team_plus": TEAM_PLUS_PRICE, "scale": ENV["STRIPE_PRICE_SCALE"]}
STRIPE_URL = "https://checkout.stripe.com/c/cs_1"


class TeamPlusPriceMappingTests(FixBillingBase):
    """The explicit plan-to-price mapping in drop_billing/account.py."""

    def test_team_plus_is_a_paid_plan_ranked_between_team_and_scale(self):
        self.assertEqual(account.PAID_PLANS, ("team", "team_plus", "scale"))
        self.assertLess(account.PLAN_RANK["team"], account.PLAN_RANK["team_plus"])
        self.assertLess(account.PLAN_RANK["team_plus"], account.PLAN_RANK["scale"])

    def test_the_price_is_read_from_stripe_price_teamplus_both_ways(self):
        self.assertEqual(account._price_id("team_plus"), TEAM_PLUS_PRICE)
        self.assertEqual(account._plan_for_price_id(TEAM_PLUS_PRICE), "team_plus")

    def test_each_paid_plan_keeps_its_own_price(self):
        for plan, price in PRICES.items():
            with self.subTest(plan=plan):
                self.assertEqual(account._price_id(plan), price)
                self.assertEqual(account._plan_for_price_id(price), plan)

    def test_the_slug_derived_name_is_never_read(self):
        customer = self.patch_stripe("customer", return_value={"id": "cus_NEW"})
        session = self.patch_stripe("session", return_value={"id": "cs_1", "url": STRIPE_URL})
        with mock.patch.dict(os.environ, {"STRIPE_PRICE_TEAM_PLUS": "price_wrong_name"}):
            os.environ.pop("STRIPE_PRICE_TEAMPLUS")
            self.assertIsNone(account._price_id("team_plus"))
            self.assertIsNone(account._plan_for_price_id("price_wrong_name"))
            resp = self.api.post("/api/v1/billing/checkout", {"plan": "team_plus"}, format="json")
        self.assertEqual((resp.status_code, resp.json()), (503, {"error": "billing_not_configured"}))
        customer.assert_not_called()
        session.assert_not_called()

    def test_the_unmapped_price_log_names_every_price_variable(self):
        self.set_billing("team_plus", sub_id="sub_A")
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", "price_unknown")))
        with self.assertLogs("drop_billing", level="ERROR") as logs:
            self.post_event(make_event("customer.subscription.updated", make_sub("sub_A", "active", "price_unknown")))
        self.assertEqual(self.reload().plan, "team_plus", "an unknown price leaves the plan as it was")
        line = next(m for m in logs.output if "maps to no plan" in m)
        for name in ("STRIPE_PRICE_TEAM,", "STRIPE_PRICE_TEAMPLUS", "STRIPE_PRICE_SCALE"):
            self.assertIn(name, line)
        self.assertNotIn("STRIPE_PRICE_TEAM_PLUS", line)


class TeamPlusCheckoutTests(FixBillingBase):
    """POST /api/v1/billing/checkout with plan team_plus, and the webhook that grants it."""

    def setUp(self):
        super().setUp()
        self.session = self.patch_stripe("session", return_value={"id": "cs_1", "url": STRIPE_URL})
        self.customer = self.patch_stripe("customer", return_value={"id": "cus_NEW"})

    def checkout(self):
        return self.api.post("/api/v1/billing/checkout", {"plan": "team_plus"}, format="json")

    def test_checkout_opens_a_stripe_session_on_the_team_plus_price(self):
        self.patch_stripe("list", return_value=sub_list())
        resp = self.checkout()
        self.assertEqual((resp.status_code, resp.json()), (200, {"checkout_url": STRIPE_URL}), resp.content)
        kwargs = self.session.call_args.kwargs
        self.assertEqual(kwargs["line_items"], [{"price": TEAM_PLUS_PRICE, "quantity": 1}])
        self.assertEqual(kwargs["metadata"], {"plan": "team_plus"})
        self.assertEqual(kwargs["mode"], "subscription")
        self.assertEqual(kwargs["managed_payments"], {"enabled": True})
        self.assertEqual(kwargs["client_reference_id"], str(self.ws.id))
        self.assertEqual(self.reload().plan, "free", "opening checkout never sets a plan (invariant 3)")

    def test_the_plan_aware_signup_sends_a_signed_in_user_to_stripe_on_the_team_plus_price(self):
        self.patch_stripe("list", return_value=sub_list())
        self.hook.force_login(self.user)
        response = self.hook.post("/accounts/signup/?plan=team_plus")
        self.assertEqual((response.status_code, response["Location"]), (302, STRIPE_URL))
        self.assertEqual(self.session.call_args.kwargs["line_items"], [{"price": TEAM_PLUS_PRICE, "quantity": 1}])

    def test_a_live_subscription_still_blocks_a_team_plus_checkout(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", PRICES["team"])))
        resp = self.checkout()
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "already_subscribed")
        self.session.assert_not_called()

    def test_a_paid_checkout_puts_the_workspace_on_team_plus_from_stripe_state(self):
        self.set_billing("free", customer="cus_A")
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", TEAM_PLUS_PRICE)))
        resp = self.post_event(checkout_completed(plan="team_plus"))
        self.assertEqual(resp.status_code, 200)
        ws = self.reload()
        self.assertEqual((ws.plan, ws.stripe_subscription_id), ("team_plus", "sub_A"))
        self.assertEqual(Subscription.objects.get(workspace=ws).status, "active")

    def test_the_checkout_metadata_alone_grants_nothing(self):
        # The session says team_plus, but Stripe's subscription is on Team's price: Stripe's subscription decides.
        self.set_billing("free", customer="cus_A")
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", PRICES["team"])))
        self.post_event(checkout_completed(plan="team_plus"))
        self.assertEqual(self.reload().plan, "team")

    def test_dunning_on_team_plus_follows_the_other_plans(self):
        for status, plan in (("past_due", "team_plus"), ("unpaid", "free"), ("canceled", "free"), ("paused", "free")):
            with self.subTest(status=status):
                self.set_billing("team_plus", sub_id="sub_A")
                self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", status, TEAM_PLUS_PRICE)))
                self.post_event(make_event("customer.subscription.updated", make_sub("sub_A", status, TEAM_PLUS_PRICE)))
                self.assertEqual(self.reload().plan, plan)

    def test_status_reports_the_team_plus_caps_and_price(self):
        self.set_billing("team_plus", sub_id="sub_A")
        body = self.api.get("/api/v1/billing/status").json()
        self.assertEqual(body["plan"], "team_plus")
        self.assertEqual(body["caps"], {"uploads_per_month": 4000, "ttl_days": 90, "keys": 10, "storage_gb": 40})
        self.assertEqual(body["price_usd"], 6)
        self.assertEqual(body["over_plan_limits"], [])


class TeamPlusSwitchTests(FixBillingBase):
    """POST /api/v1/billing/switch to and from Team Plus swaps the price item in place, with proration."""

    def test_switches_to_and_from_team_plus(self):
        for current, target in (("team", "team_plus"), ("team_plus", "scale"), ("scale", "team_plus"), ("team_plus", "team")):
            with self.subTest(current=current, target=target):
                self.set_billing(current, sub_id="sub_A")
                self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", PRICES[current])))
                modify = self.patch_stripe("modify", return_value=make_sub("sub_A", "active", PRICES[target]))
                resp = self.api.post("/api/v1/billing/switch", {"plan": target}, format="json")
                self.assertEqual(resp.status_code, 200, resp.content)
                self.assertEqual(resp.json()["plan"], target)
                kwargs = modify.call_args.kwargs
                self.assertEqual(kwargs["items"], [{"id": "si_1", "price": PRICES[target]}])
                self.assertEqual(kwargs["proration_behavior"], "always_invoice")
                self.assertEqual(kwargs["payment_behavior"], "pending_if_incomplete")
                self.assertEqual(self.reload().plan, target)

    def test_an_unpaid_upgrade_to_team_plus_is_a_402_and_the_plan_stays(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", PRICES["team"])))
        pending = make_sub("sub_A", "active", PRICES["team"], pending_update={"subscription_items": [{"price": TEAM_PLUS_PRICE}]})
        pending["latest_invoice"] = {"id": "in_1", "hosted_invoice_url": "https://invoice.stripe.com/i/in_1"}
        self.patch_stripe("modify", return_value=pending)
        resp = self.api.post("/api/v1/billing/switch", {"plan": "team_plus"}, format="json")
        self.assertEqual(resp.status_code, 402)
        self.assertEqual((resp.json()["code"], resp.json()["plan"]), ("payment_required", "team"))
        self.assertEqual(self.reload().plan, "team")

    def test_with_two_live_subscriptions_the_higher_plan_wins(self):
        for low, high in (("team", "team_plus"), ("team_plus", "scale")):
            with self.subTest(low=low, high=high):
                self.set_billing(low, sub_id="sub_A")
                self.patch_stripe("list", return_value=sub_list(
                    make_sub("sub_A", "active", PRICES[low], created=1),
                    make_sub("sub_B", "active", PRICES[high], created=2, item_id="si_2"),
                ))
                with self.assertLogs("drop_billing", level="ERROR"):
                    self.post_event(make_event("customer.subscription.created", make_sub("sub_B", "active", PRICES[high])))
                self.assertEqual(self.reload().plan, high)

    def test_a_plan_that_is_not_sold_names_team_plus_among_the_choices(self):
        resp = self.api.post("/api/v1/billing/switch", {"plan": "enterprise"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "Choose the team, team_plus or scale plan.", "code": "invalid_plan"})

    def test_every_paid_plan_in_plans_py_can_be_bought(self):
        self.assertEqual(set(account.PAID_PLANS), {slug for slug, caps in PLANS.items() if caps["price_usd"]})
