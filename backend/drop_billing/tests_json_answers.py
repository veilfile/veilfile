"""The billing JSON endpoints answer what they always have.

The logic behind them is drop_billing/account.py, which the dashboard calls too, and drop_billing/views.py sends its
answers. Nothing here asks Stripe anything: the library is the mock of drop_billing.tests, patched in at
``drop_billing.views.stripe`` as every billing test does. The tests pin the bytes a client reads (the order of the
keys, the words of an error, which errors carry a ``code`` and which carry only ``error``) and the session-only rule.
They passed before the logic moved, and they must keep passing.

Every class sets longMessage = False, so a failure prints its own message, which names the file and the fix.
"""

import json
import os
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.test import APIClient

from drop_billing.models import Subscription
from drop_billing.tests import TEST_ENV, make_stripe_mock
from drop_core.models import APIKey, Artifact, get_or_create_workspace
from drop_core.plans import PLANS
from drop_core.tests_json_answers import ShortFailures

User = get_user_model()
FIX = (
    "the billing JSON answer changed: backend/drop_billing/account.py (the logic) and backend/drop_billing/views.py "
    "(how it is sent) must answer exactly what docs/API.md and the dashboard read"
)


class FakeStripeError(Exception):
    def __init__(self, message="stripe is down", code=None, param=None):
        super().__init__(message)
        self.code = code
        self.param = param


def subscription(sub_id="sub_A", status="active", price="price_team_111", pending=False, invoice=None):
    sub = {
        "id": sub_id,
        "status": status,
        "created": 1,
        "items": {"data": [{"id": "si_1", "price": {"id": price}, "current_period_end": 1900000000}]},
    }
    if pending:
        sub["pending_update"] = {"expires_at": 1}
    if invoice is not None:
        sub["latest_invoice"] = invoice
    return sub


def stripe_mock(subscriptions=(), **replace):
    """The mock of drop_billing.tests, with the subscriptions Stripe is to report and Stripe's error class.
    ``replace`` sets attributes by dotted path with ``__``, such as ``Subscription__modify=...``."""
    mocked = make_stripe_mock()
    mocked.error.StripeError = FakeStripeError
    mocked.Subscription.list.return_value = {"data": list(subscriptions)}
    for path, value in replace.items():
        target = mocked
        *parents, leaf = path.split("__")
        for name in parents:
            target = getattr(target, name)
        setattr(target, leaf, value)
    return mocked


def order_of(text):
    """The keys of a JSON object in the order the bytes give them."""
    return [key for key, _value in json.loads(text, object_pairs_hook=lambda pairs: pairs)]


class BillingJsonCase(ShortFailures, TestCase):
    longMessage = False

    def setUp(self):
        self.user = User.objects.create_user(username="payer", email="payer@example.com")
        self.workspace = get_or_create_workspace(self.user)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        env = mock.patch.dict(os.environ, TEST_ENV, clear=False)
        env.start()
        self.addCleanup(env.stop)

    def post(self, path, payload, mocked=None):
        with mock.patch("drop_billing.views.stripe", stripe_mock() if mocked is None else mocked):
            return self.client.post(f"/api/v1/billing/{path}", payload, format="json")

    def exact(self, response, status, text):
        self.assertEqual(response.status_code, status, FIX)
        self.assertEqual(response.content.decode(), text, FIX)

    def subscribe(self, plan="team", sub_id="sub_A"):
        self.workspace.plan = plan
        self.workspace.stripe_customer_id = "cus_S"
        self.workspace.stripe_subscription_id = sub_id
        self.workspace.save()


class StatusAnswerTests(BillingJsonCase):
    def test_a_free_workspace(self):
        caps = PLANS["free"]
        expected = (
            '{"plan":"free","caps":{"uploads_per_month":%d,"ttl_days":%d,"keys":%d,"storage_gb":%d},'
            '"storage_used_bytes":0,"active_keys":0,"over_plan_limits":[],"price_usd":0,'
            '"has_billing_account":false,"has_subscription":false,"subscription_status":"",'
            '"current_period_end":null,"billing_interval":""}'
        ) % (caps["uploads"], caps["ttl_days"], caps["keys"], caps["storage_gb"])
        self.exact(self.client.get("/api/v1/billing/status"), 200, expected)

    def test_over_the_limits_after_a_downgrade_and_the_subscription_mirror(self):
        for i in range(2):
            APIKey.create_key(self.workspace, f"k{i}")
        Artifact.objects.create(
            workspace=self.workspace, filename="big.bin", size_bytes=2 * 1024**3,
            expires_at=timezone.now() + timedelta(days=300),
        )
        Subscription.objects.create(
            workspace=self.workspace, stripe_customer_id="cus_S", stripe_subscription_id="sub_A",
            status="unpaid", current_period_end=timezone.now() + timedelta(days=3),
        )
        response = self.client.get("/api/v1/billing/status")
        body = response.json()
        self.assertEqual(body["over_plan_limits"], ["keys", "storage", "ttl"], FIX)
        self.assertEqual((body["active_keys"], body["subscription_status"]), (2, "unpaid"), FIX)
        self.assertIsNotNone(body["current_period_end"], FIX)
        self.assertEqual(
            order_of(response.content.decode()),
            ["plan", "caps", "storage_used_bytes", "active_keys", "over_plan_limits", "price_usd",
             "has_billing_account", "has_subscription", "subscription_status", "current_period_end",
             "billing_interval"],
            FIX,
        )

    def test_an_unknown_plan_reads_as_free(self):
        self.workspace.plan = "legacy"
        self.workspace.save()
        self.assertEqual(self.client.get("/api/v1/billing/status").json()["plan"], "free", FIX)

    def test_status_makes_no_stripe_call(self):
        mocked = stripe_mock()
        with mock.patch("drop_billing.views.stripe", mocked):
            self.client.get("/api/v1/billing/status")
        self.assertEqual(mocked.mock_calls, [], "backend/drop_billing/account.py: status() reads the local mirror and asks Stripe nothing")


class SessionOnlyTests(BillingJsonCase):
    def test_a_key_and_a_stranger_are_refused_everywhere(self):
        key, raw = APIKey.create_key(self.workspace, "agent")
        keyed = APIClient()
        keyed.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        for client in (keyed, APIClient()):
            for method, path in (
                ("get", "status"), ("post", "checkout"), ("post", "switch"), ("post", "portal"),
            ):
                with self.subTest(path=path):
                    response = getattr(client, method)(f"/api/v1/billing/{path}", {"plan": "team"}, format="json")
                    self.assertEqual(response.status_code, 403, FIX)
                    self.assertNotIn("WWW-Authenticate", response, FIX)

    def test_a_session_post_needs_its_csrf_token(self):
        strict = APIClient(enforce_csrf_checks=True)
        strict.force_login(self.user)
        for path in ("checkout", "switch", "portal"):
            with self.subTest(path=path), mock.patch("drop_billing.views.stripe", stripe_mock()) as mocked:
                response = strict.post(f"/api/v1/billing/{path}", {"plan": "team"}, format="json")
                self.assertEqual(response.status_code, 403, FIX)
                self.assertEqual(mocked.mock_calls, [], FIX)


class CheckoutAnswerTests(BillingJsonCase):
    def test_a_plan_that_cannot_be_bought_is_a_bare_400(self):
        for payload in ({"plan": "enterprise"}, {"plan": "free"}, {}):
            with self.subTest(payload=payload):
                self.exact(self.post("checkout", payload), 400, '{"error":"invalid_plan"}')

    def test_not_configured_is_a_bare_503(self):
        not_configured = '{"error":"billing_not_configured"}'
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": ""}):
            self.exact(self.post("checkout", {"plan": "team"}), 503, not_configured)
        with mock.patch.dict(os.environ, {"STRIPE_PRICE_TEAM": ""}):
            self.exact(self.post("checkout", {"plan": "team"}), 503, not_configured)

    def test_the_session_url_and_the_stripe_settings(self):
        mocked = stripe_mock()
        response = self.post("checkout", {"plan": "scale"}, mocked)
        self.exact(response, 200, '{"checkout_url":"https://checkout.stripe.com/pay/cs_test_123"}')
        self.assertEqual((mocked.api_key, mocked.api_version), ("sk_test_111", "2025-03-31.basil"), FIX)
        mocked.Customer.create.assert_called_once_with(email="payer@example.com")
        kwargs = mocked.checkout.Session.create.call_args.kwargs
        self.assertEqual(kwargs["metadata"], {"plan": "scale"}, FIX)
        self.assertEqual(kwargs["success_url"], "https://veilfile.test/app/?billing=success", FIX)
        self.assertEqual(kwargs["cancel_url"], "https://veilfile.test/app/?billing=cancelled", FIX)

    def test_a_live_subscription_is_a_409_worded_for_its_status(self):
        self.exact(
            self.post("checkout", {"plan": "scale"}, stripe_mock([subscription(status="active")])),
            409,
            '{"error":"This workspace already has a subscription. Switch plans from the dashboard instead of '
            'starting a new checkout.","code":"already_subscribed","subscription_status":"active"}',
        )
        body = self.post("checkout", {"plan": "scale"}, stripe_mock([subscription(status="past_due")])).json()
        self.assertIn("Manage billing", body["error"], FIX)
        self.assertEqual(body["subscription_status"], "past_due", FIX)

    def test_a_stripe_error_is_a_502_that_says_so(self):
        failing = stripe_mock(checkout__Session__create=mock.Mock(side_effect=FakeStripeError("tax code missing")))
        self.exact(
            self.post("checkout", {"plan": "team"}, failing),
            502,
            '{"error":"Stripe: tax code missing","code":"stripe_error"}',
        )

    def test_a_long_stripe_message_is_cut_at_280_characters(self):
        failing = stripe_mock(checkout__Session__create=mock.Mock(side_effect=FakeStripeError("x" * 400)))
        error = self.post("checkout", {"plan": "team"}, failing).json()["error"]
        self.assertEqual(error, "Stripe: " + "x" * 277 + "\u2026", FIX)

    def test_checkout_session_for_user_keeps_its_contract(self):
        from drop_billing.views import create_checkout_session_for_user

        with mock.patch("drop_billing.views.stripe", stripe_mock()):
            url, err = create_checkout_session_for_user(self.user, "team")
        self.assertEqual((url, err), ("https://checkout.stripe.com/pay/cs_test_123", None), FIX)
        with mock.patch("drop_billing.views.stripe", stripe_mock([subscription()])):
            url, err = create_checkout_session_for_user(self.user, "scale")
        self.assertIsNone(url, FIX)
        self.assertIsInstance(err, Response, FIX)
        self.assertEqual((err.status_code, err.data["code"]), (409, "already_subscribed"), FIX)
        url, err = create_checkout_session_for_user(self.user, "free")
        self.assertEqual((url, err.status_code, err.data), (None, 400, {"error": "invalid_plan"}), FIX)


class SwitchAnswerTests(BillingJsonCase):
    def test_a_plan_that_cannot_be_switched_to_is_a_400_with_a_code(self):
        self.exact(
            self.post("switch", {"plan": "free"}),
            400,
            '{"error":"Choose the team, team_plus or scale plan.","code":"invalid_plan"}',
        )

    def test_not_configured_is_a_bare_503(self):
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": ""}):
            self.exact(self.post("switch", {"plan": "scale"}), 503, '{"error":"billing_not_configured"}')
        with mock.patch.dict(os.environ, {"STRIPE_PRICE_SCALE": ""}):
            self.exact(self.post("switch", {"plan": "scale"}), 503, '{"error":"billing_not_configured"}')

    def test_no_customer_and_no_live_subscription_are_the_same_409(self):
        text = (
            '{"error":"This workspace has no active subscription to change. Subscribe first with POST /api/v1/billing/checkout.",'
            '"code":"no_subscription"}'
        )
        self.exact(self.post("switch", {"plan": "scale"}), 409, text)
        self.subscribe()
        self.exact(self.post("switch", {"plan": "scale"}, stripe_mock([subscription(status="canceled")])), 409, text)

    def test_a_switch_asks_stripe_for_a_prorated_change_and_follows_what_it_reports(self):
        self.subscribe()
        modify = mock.Mock(return_value=subscription(price="price_scale_111"))
        mocked = stripe_mock([subscription()], Subscription__modify=modify)
        self.exact(
            self.post("switch", {"plan": "scale"}, mocked),
            200,
            '{"plan":"scale","status":"switched","subscription_status":"active"}',
        )
        modify.assert_called_once_with(
            "sub_A",
            items=[{"id": "si_1", "price": "price_scale_111"}],
            proration_behavior="always_invoice",
            payment_behavior="pending_if_incomplete",
            expand=["latest_invoice"],
        )
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.plan, "scale", FIX)

    def test_the_current_plan_is_unchanged_and_asks_stripe_to_change_nothing(self):
        self.subscribe()
        mocked = stripe_mock([subscription()])
        self.exact(self.post("switch", {"plan": "team"}, mocked), 200, '{"plan":"team","status":"unchanged"}')
        mocked.Subscription.modify.assert_not_called()

    def test_switching_back_voids_the_invoice_of_a_pending_switch(self):
        self.subscribe()
        mocked = stripe_mock([subscription(pending=True, invoice="in_pending")])
        self.exact(self.post("switch", {"plan": "team"}, mocked), 200, '{"plan":"team","status":"pending_cancelled"}')
        mocked.Invoice.void_invoice.assert_called_once_with("in_pending")

    def test_a_payment_that_fails_is_a_402_that_leaves_the_plan_and_carries_the_invoice_page(self):
        self.subscribe()
        invoice = {"id": "in_x", "hosted_invoice_url": "https://invoice.stripe.com/i/x"}
        pending = mock.Mock(return_value=subscription(pending=True, invoice=invoice))
        response = self.post("switch", {"plan": "scale"}, stripe_mock([subscription()], Subscription__modify=pending))
        self.assertEqual(response.status_code, 402, FIX)
        self.assertEqual(order_of(response.content.decode()), ["error", "code", "plan", "invoice_url"], FIX)
        body = response.json()
        self.assertEqual((body["code"], body["plan"], body["invoice_url"]), ("payment_required", "team", "https://invoice.stripe.com/i/x"), FIX)
        self.assertIn("invoice page", body["error"], FIX)
        none = mock.Mock(return_value=subscription(pending=True))
        again = self.post("switch", {"plan": "scale"}, stripe_mock([subscription()], Subscription__modify=none)).json()
        self.assertIsNone(again["invoice_url"], FIX)
        self.assertNotIn("invoice page", again["error"], FIX)
        self.assertIn("Manage billing", again["error"], FIX)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.plan, "team", FIX)

    def test_past_due_and_more_than_one_subscription_are_409s(self):
        self.subscribe()
        self.exact(
            self.post("switch", {"plan": "scale"}, stripe_mock([subscription(status="past_due")])),
            409,
            '{"error":"Your last payment failed. Update your payment method under Manage billing before '
            'changing plans.","code":"subscription_past_due"}',
        )
        self.subscribe(sub_id="sub_Z")
        two = stripe_mock([subscription("sub_A"), subscription("sub_B")])
        self.exact(
            self.post("switch", {"plan": "scale"}, two),
            409,
            '{"error":"This workspace has more than one subscription. Use Manage billing to cancel the extra '
            'one, or contact support.","code":"multiple_subscriptions"}',
        )

    def test_a_stripe_error_is_a_502_and_the_plan_is_not_touched(self):
        self.subscribe()
        failing = stripe_mock([subscription()], Subscription__modify=mock.Mock(side_effect=FakeStripeError("api down")))
        self.exact(
            self.post("switch", {"plan": "scale"}, failing), 502, '{"error":"Stripe: api down","code":"stripe_error"}'
        )
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.plan, "team", FIX)

    def test_a_customer_stripe_does_not_know_is_forgotten(self):
        self.subscribe()
        missing = FakeStripeError("No such customer: 'cus_S'", code="resource_missing", param="customer")
        response = self.post("switch", {"plan": "scale"}, stripe_mock(Subscription__list=mock.Mock(side_effect=missing)))
        self.assertEqual((response.status_code, response.json()["code"]), (409, "no_subscription"), FIX)
        self.workspace.refresh_from_db()
        self.assertEqual((self.workspace.plan, self.workspace.stripe_customer_id), ("free", ""), FIX)


class PortalAnswerTests(BillingJsonCase):
    def test_no_billing_account_is_a_bare_400(self):
        self.exact(self.post("portal", {}), 400, '{"error":"no_billing_account"}')

    def test_not_configured_is_a_bare_503(self):
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": ""}):
            self.exact(self.post("portal", {}), 503, '{"error":"billing_not_configured"}')

    def test_the_portal_url_and_where_it_returns(self):
        self.subscribe()
        mocked = stripe_mock()
        self.exact(self.post("portal", {}, mocked), 200, '{"portal_url":"https://billing.stripe.com/session/bps_test_123"}')
        mocked.billing_portal.Session.create.assert_called_once_with(
            customer="cus_S", return_url="https://veilfile.test/app/"
        )

    def test_a_stripe_error_is_a_502(self):
        self.subscribe()
        failing = stripe_mock(billing_portal__Session__create=mock.Mock(side_effect=FakeStripeError("configuration missing")))
        self.exact(self.post("portal", {}, failing), 502, '{"error":"Stripe: configuration missing","code":"stripe_error"}')

    def test_a_customer_stripe_does_not_know_is_forgotten_and_answers_no_billing_account(self):
        self.subscribe()
        missing = FakeStripeError("No such customer: 'cus_S'", code="resource_missing", param="customer")
        failing = stripe_mock(billing_portal__Session__create=mock.Mock(side_effect=missing))
        self.exact(self.post("portal", {}, failing), 400, '{"error":"no_billing_account"}')
        self.workspace.refresh_from_db()
        self.assertEqual((self.workspace.plan, self.workspace.stripe_customer_id), ("free", ""), FIX)
