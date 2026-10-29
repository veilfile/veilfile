"""drop_billing.account: the plan and its limits, Stripe Checkout, a plan switch and the portal as plain functions.

The JSON endpoints and the dashboard call these functions, so each test here calls one directly and reads its Answer.
tests_json_answers.py holds the endpoints to the bytes clients read; these tests hold the functions to what the
dashboard leans on: which Stripe calls they make and in which transaction, what they refuse and before asking Stripe
what, and that Stripe is the one library that ``drop_billing.views.stripe`` names. Nothing here asks Stripe anything:
the library is the mock of drop_billing.tests.

Every class sets longMessage = False, so a failure prints its own message, which names the file and the fix.
"""

import ast
import os
import re
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from django.utils.functional import SimpleLazyObject
from rest_framework.test import APIClient

from drop_billing import account
from drop_billing.models import Subscription
from drop_billing.tests import TEST_ENV
from drop_billing.tests_json_answers import FakeStripeError, stripe_mock, subscription
from drop_core.account import Answer
from drop_core.models import APIKey, Artifact, Workspace, get_or_create_workspace
from drop_core.plans import PLANS
from drop_core.tests_json_answers import ShortFailures

User = get_user_model()
WHERE = "backend/drop_billing/account.py"
ACCOUNT = (
    f"{WHERE}: the JSON endpoints and the dashboard share this function. Fix the function; an answer that clients "
    "read changes only with their release order planned (AGENTS.md, invariant 4)"
)
NOT_CONFIGURED = Answer(503, {"error": "billing_not_configured"})
NO_SUBSCRIPTION = Answer(
    409,
    {
        "error": "This workspace has no active subscription to change. Subscribe first with POST /api/v1/billing/checkout.",
        "code": "no_subscription",
    },
)
MISSING = dict(code="resource_missing", param="customer")


class AccountCase(ShortFailures, TestCase):
    longMessage = False

    def setUp(self):
        self.user = User.objects.create_user(username="payer", email="payer@example.com")
        self.workspace = get_or_create_workspace(self.user)
        env = mock.patch.dict(os.environ, TEST_ENV, clear=False)
        env.start()
        self.addCleanup(env.stop)

    def call(self, function, *args, mocked=None):
        with mock.patch("drop_billing.views.stripe", stripe_mock() if mocked is None else mocked):
            return function(*args)

    def subscribe(self, plan="team", sub_id="sub_A", customer="cus_S"):
        self.workspace.plan = plan
        self.workspace.stripe_customer_id = customer
        self.workspace.stripe_subscription_id = sub_id
        self.workspace.save()

    def reload(self):
        self.workspace.refresh_from_db()
        return self.workspace


class StatusTests(AccountCase):
    def test_a_free_workspace(self):
        caps = PLANS["free"]
        self.assertEqual(
            account.status(self.user),
            Answer(
                200,
                {
                    "plan": "free",
                    "caps": {
                        "uploads_per_month": caps["uploads"],
                        "ttl_days": caps["ttl_days"],
                        "keys": caps["keys"],
                        "storage_gb": caps["storage_gb"],
                    },
                    "storage_used_bytes": 0,
                    "active_keys": 0,
                    "over_plan_limits": [],
                    "price_usd": 0,
                    "has_billing_account": False,
                    "has_subscription": False,
                    "subscription_status": "",
                    "current_period_end": None,
                },
            ),
            f"{WHERE}: status() is the body of GET /api/v1/billing/status",
        )

    def test_a_subscriber_with_its_mirror(self):
        self.subscribe("scale")
        end = timezone.now() + timedelta(days=20)
        Subscription.objects.create(
            workspace=self.workspace, stripe_customer_id="cus_S", stripe_subscription_id="sub_A",
            status="active", current_period_end=end,
        )
        body = account.status(self.user).body
        caps = PLANS["scale"]
        self.assertEqual((body["plan"], body["price_usd"], body["caps"]["keys"], body["caps"]["storage_gb"]),
                         ("scale", caps["price_usd"], None, caps["storage_gb"]), ACCOUNT)
        self.assertEqual((body["has_billing_account"], body["has_subscription"], body["subscription_status"]), (True, True, "active"), ACCOUNT)
        self.assertEqual(body["current_period_end"], end.isoformat(), ACCOUNT)

    def test_each_limit_is_named_when_the_workspace_is_over_it(self):
        cap = PLANS["free"]
        for i in range(cap["keys"] + 1):
            APIKey.create_key(self.workspace, f"k{i}")
        self.assertEqual(account.status(self.user).body["over_plan_limits"], ["keys"], ACCOUNT)
        Artifact.objects.create(
            workspace=self.workspace, filename="big", size_bytes=cap["storage_gb"] * 1024**3 + 1,
            expires_at=timezone.now() + timedelta(days=1),
        )
        self.assertEqual(account.status(self.user).body["over_plan_limits"], ["keys", "storage"], ACCOUNT)
        Artifact.objects.create(
            workspace=self.workspace, filename="long", size_bytes=1,
            expires_at=timezone.now() + timedelta(days=cap["ttl_days"] + 5),
        )
        body = account.status(self.user).body
        self.assertEqual(body["over_plan_limits"], ["keys", "storage", "ttl"], f"{WHERE}: keys, storage, ttl, in that order")
        self.assertEqual(body["active_keys"], cap["keys"] + 1, ACCOUNT)

    def test_nothing_is_over_within_the_limits_and_a_scale_workspace_has_no_key_limit(self):
        self.workspace.plan = "scale"
        self.workspace.save()
        for i in range(8):
            APIKey.create_key(self.workspace, f"k{i}")
        self.assertEqual(account.status(self.user).body["over_plan_limits"], [], ACCOUNT)

    def test_an_unknown_plan_reads_as_free(self):
        self.workspace.plan = "legacy"
        self.workspace.save()
        body = account.status(self.user).body
        self.assertEqual((body["plan"], body["caps"]["keys"]), ("free", PLANS["free"]["keys"]), ACCOUNT)

    def test_it_asks_stripe_nothing_and_accepts_request_user_as_the_dashboard_passes_it(self):
        mocked = stripe_mock()
        with mock.patch("drop_billing.views.stripe", mocked):
            answer = account.status(SimpleLazyObject(lambda: self.user))
        self.assertEqual((answer.status, mocked.mock_calls), (200, []), f"{WHERE}: status() reads the local mirror only")


class CheckoutTests(AccountCase):
    def test_a_plan_that_cannot_be_bought_is_refused_before_stripe_is_touched(self):
        for plan in ("enterprise", "free", "", None, ["team"]):
            with self.subTest(plan=plan):
                mocked = stripe_mock()
                self.assertEqual(self.call(account.checkout, self.user, plan, mocked=mocked), Answer(400, {"error": "invalid_plan"}), ACCOUNT)
                self.assertEqual(mocked.mock_calls, [], ACCOUNT)

    def test_no_secret_key_is_a_503_and_no_price_is_a_503_that_creates_no_customer(self):
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": ""}):
            self.assertEqual(self.call(account.checkout, self.user, "team"), NOT_CONFIGURED, ACCOUNT)
        mocked = stripe_mock()
        with mock.patch.dict(os.environ, {"STRIPE_PRICE_SCALE": ""}):
            self.assertEqual(self.call(account.checkout, self.user, "scale", mocked=mocked), NOT_CONFIGURED, ACCOUNT)
        mocked.Customer.create.assert_not_called()
        self.assertEqual(self.reload().stripe_customer_id, "", ACCOUNT)

    def test_a_session_for_the_plan_with_everything_stripe_is_asked_for(self):
        mocked = stripe_mock()
        answer = self.call(account.checkout, self.user, "scale", mocked=mocked)
        self.assertEqual(answer, Answer(200, {"checkout_url": "https://checkout.stripe.com/pay/cs_test_123"}), ACCOUNT)
        self.assertEqual((mocked.api_key, mocked.api_version), ("sk_test_111", "2025-03-31.basil"), f"{WHERE}: _stripe_or_503 sets both on the library")
        mocked.Customer.create.assert_called_once_with(email="payer@example.com")
        mocked.checkout.Session.create.assert_called_once_with(
            customer="cus_test_123",
            client_reference_id=str(self.workspace.pk),
            mode="subscription",
            line_items=[{"price": "price_scale_111", "quantity": 1}],
            managed_payments={"enabled": True},
            metadata={"plan": "scale"},
            subscription_data={"metadata": {"workspace_id": str(self.workspace.pk)}},
            success_url="https://veilfile.test/app/?billing=success",
            cancel_url="https://veilfile.test/app/?billing=cancelled",
        )
        self.assertEqual(self.reload().stripe_customer_id, "cus_test_123", ACCOUNT)

    def test_the_customer_is_made_once_and_reused(self):
        mocked = stripe_mock()
        self.call(account.checkout, self.user, "team", mocked=mocked)
        self.call(account.checkout, self.user, "scale", mocked=mocked)
        self.assertEqual(mocked.Customer.create.call_count, 1, f"{WHERE}: _ensure_customer reuses the stored customer")
        self.assertEqual(mocked.checkout.Session.create.call_args.kwargs["customer"], "cus_test_123", ACCOUNT)

    def test_the_customer_is_made_inside_the_row_lock_and_the_session_outside_it(self):
        depths = {}
        baseline = len(connection.savepoint_ids)

        def make_customer(**kwargs):
            depths["customer"] = len(connection.savepoint_ids)
            return {"id": "cus_1"}

        def make_session(**kwargs):
            depths["session"] = len(connection.savepoint_ids)
            return {"id": "cs_1", "url": "https://checkout.stripe.com/c/cs_1"}

        mocked = stripe_mock(Customer__create=make_customer, checkout__Session__create=make_session)
        self.call(account.checkout, self.user, "team", mocked=mocked)
        self.assertEqual(depths["customer"], baseline + 1, f"{WHERE}: Customer.create runs inside _ensure_customer's transaction, so a double click makes one customer")
        self.assertEqual(depths["session"], baseline, f"{WHERE}: the Checkout session is made outside the row lock")

    def test_a_live_subscription_is_a_409_worded_for_each_status_and_nothing_is_created(self):
        for status, phrase in (
            ("active", "Switch plans"),
            ("trialing", "Switch plans"),
            ("past_due", "failed payment"),
            ("unpaid", "unpaid invoice"),
            ("incomplete", "first payment"),
            ("paused", "is paused"),
        ):
            with self.subTest(status=status):
                mocked = stripe_mock([subscription(status=status)])
                answer = self.call(account.checkout, self.user, "scale", mocked=mocked)
                self.assertEqual((answer.status, answer.body["code"], answer.body["subscription_status"]), (409, "already_subscribed", status), ACCOUNT)
                self.assertEqual(list(answer.body), ["error", "code", "subscription_status"], ACCOUNT)
                self.assertIn(phrase, answer.body["error"], ACCOUNT)
                mocked.checkout.Session.create.assert_not_called()

    def test_the_status_that_words_a_409_is_the_most_usable_of_several(self):
        mocked = stripe_mock([subscription("sub_P", status="past_due"), subscription("sub_A", status="active")])
        self.assertEqual(self.call(account.checkout, self.user, "scale", mocked=mocked).body["subscription_status"], "active", ACCOUNT)

    def test_a_409_brings_the_local_state_up_to_what_stripe_reports(self):
        self.assertEqual(self.workspace.plan, "free")
        self.call(account.checkout, self.user, "scale", mocked=stripe_mock([subscription(status="active")]))
        workspace = self.reload()
        self.assertEqual((workspace.plan, workspace.stripe_subscription_id), ("team", "sub_A"), f"{WHERE}: plan state changes only from Stripe-verified state")

    def test_ended_subscriptions_do_not_block_a_new_checkout(self):
        for status in ("canceled", "incomplete_expired"):
            with self.subTest(status=status):
                answer = self.call(account.checkout, self.user, "team", mocked=stripe_mock([subscription(status=status)]))
                self.assertEqual(answer.status, 200, ACCOUNT)

    def test_a_customer_stripe_does_not_know_is_replaced(self):
        self.subscribe("team", customer="cus_old")
        missing = FakeStripeError("No such customer: 'cus_old'", **MISSING)
        mocked = stripe_mock(Subscription__list=mock.Mock(side_effect=missing))
        answer = self.call(account.checkout, self.user, "scale", mocked=mocked)
        self.assertEqual(answer.status, 200, ACCOUNT)
        workspace = self.reload()
        self.assertEqual((workspace.stripe_customer_id, workspace.plan), ("cus_test_123", "free"), f"{WHERE}: the leftover is unlinked and a paid plan it carried drops to free")

    def test_a_session_that_rejects_the_customer_is_retried_once_with_a_new_one(self):
        self.subscribe("free", sub_id="", customer="cus_stale")
        missing = FakeStripeError("No such customer: 'cus_stale'", **MISSING)
        calls = []

        def create(**kwargs):
            calls.append(kwargs["customer"])
            if len(calls) == 1:
                raise missing
            return {"id": "cs_2", "url": "https://checkout.stripe.com/c/cs_2"}

        mocked = stripe_mock(checkout__Session__create=mock.Mock(side_effect=create))
        answer = self.call(account.checkout, self.user, "team", mocked=mocked)
        self.assertEqual(answer, Answer(200, {"checkout_url": "https://checkout.stripe.com/c/cs_2"}), ACCOUNT)
        self.assertEqual(calls, ["cus_stale", "cus_test_123"], ACCOUNT)

    def test_another_stripe_error_is_a_502_and_a_long_message_is_cut(self):
        failing = stripe_mock(checkout__Session__create=mock.Mock(side_effect=FakeStripeError("tax code missing")))
        self.assertEqual(
            self.call(account.checkout, self.user, "team", mocked=failing),
            Answer(502, {"error": "Stripe: tax code missing", "code": "stripe_error"}),
            ACCOUNT,
        )
        long = stripe_mock(checkout__Session__create=mock.Mock(side_effect=FakeStripeError("y" * 400)))
        error = self.call(account.checkout, self.user, "team", mocked=long).body["error"]
        self.assertEqual((len(error), error[-1]), (len("Stripe: ") + 278, "\u2026"), f"{WHERE}: _stripe_error cuts at 280 characters")

    def test_the_secret_key_and_the_price_are_read_when_the_function_runs(self):
        mocked = stripe_mock()
        self.call(account.checkout, self.user, "team", mocked=mocked)
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_222", "STRIPE_PRICE_TEAM": "price_other"}):
            self.call(account.checkout, self.user, "team", mocked=mocked)
        self.assertEqual((mocked.api_key, mocked.checkout.Session.create.call_args.kwargs["line_items"][0]["price"]), ("sk_test_222", "price_other"), ACCOUNT)


class SwitchTests(AccountCase):
    def test_a_plan_that_cannot_be_switched_to_is_refused_before_stripe_is_touched(self):
        for plan in ("free", "enterprise", None):
            with self.subTest(plan=plan):
                mocked = stripe_mock()
                answer = self.call(account.switch, self.user, plan, mocked=mocked)
                self.assertEqual(answer, Answer(400, {"error": "Choose the team or scale plan.", "code": "invalid_plan"}), ACCOUNT)
                self.assertEqual(mocked.mock_calls, [], ACCOUNT)

    def test_not_configured_is_a_503_and_a_workspace_with_no_customer_has_nothing_to_switch(self):
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": ""}):
            self.assertEqual(self.call(account.switch, self.user, "team"), NOT_CONFIGURED, ACCOUNT)
        with mock.patch.dict(os.environ, {"STRIPE_PRICE_SCALE": ""}):
            self.assertEqual(self.call(account.switch, self.user, "scale"), NOT_CONFIGURED, ACCOUNT)
        mocked = stripe_mock()
        self.assertEqual(self.call(account.switch, self.user, "scale", mocked=mocked), NO_SUBSCRIPTION, ACCOUNT)
        mocked.Subscription.list.assert_not_called()

    def test_a_prorated_change_asked_of_stripe_and_the_plan_follows_what_it_reports(self):
        self.subscribe()
        modify = mock.Mock(return_value=subscription(price="price_scale_111"))
        answer = self.call(account.switch, self.user, "scale", mocked=stripe_mock([subscription()], Subscription__modify=modify))
        self.assertEqual(answer, Answer(200, {"plan": "scale", "status": "switched", "subscription_status": "active"}), ACCOUNT)
        modify.assert_called_once_with(
            "sub_A",
            items=[{"id": "si_1", "price": "price_scale_111"}],
            proration_behavior="always_invoice",
            payment_behavior="pending_if_incomplete",
            expand=["latest_invoice"],
        )
        workspace = self.reload()
        self.assertEqual(workspace.plan, "scale", f"{WHERE}: the plan changes from Stripe's reply (invariant 3)")
        self.assertEqual(Subscription.objects.get(workspace=workspace).status, "active", ACCOUNT)

    def test_a_downgrade_is_the_same_call_with_the_other_price(self):
        self.subscribe("scale")
        modify = mock.Mock(return_value=subscription(price="price_team_111"))
        mocked = stripe_mock([subscription(price="price_scale_111")], Subscription__modify=modify)
        self.assertEqual(self.call(account.switch, self.user, "team", mocked=mocked).body["plan"], "team", ACCOUNT)
        self.assertEqual(modify.call_args.kwargs["items"], [{"id": "si_1", "price": "price_team_111"}], ACCOUNT)

    def test_the_stripe_call_is_made_inside_the_transaction_that_holds_the_row_lock(self):
        self.subscribe()
        baseline = len(connection.savepoint_ids)
        depths = []

        def modify(*args, **kwargs):
            depths.append(len(connection.savepoint_ids))
            return subscription(price="price_scale_111")

        locks = []
        real = Workspace.objects.select_for_update

        def spy(*args, **kwargs):
            locks.append(len(connection.savepoint_ids))
            return real(*args, **kwargs)

        with mock.patch.object(Workspace.objects, "select_for_update", spy):
            self.call(account.switch, self.user, "scale", mocked=stripe_mock([subscription()], Subscription__modify=modify))
        self.assertEqual(depths, [baseline + 1], f"{WHERE}: Subscription.modify runs in the transaction of the switch")
        self.assertEqual(locks[0], baseline + 1, f"{WHERE}: the workspace row is locked inside that transaction")

    def test_the_current_plan_changes_nothing_at_stripe(self):
        self.subscribe()
        mocked = stripe_mock([subscription()])
        self.assertEqual(self.call(account.switch, self.user, "team", mocked=mocked), Answer(200, {"plan": "team", "status": "unchanged"}), ACCOUNT)
        mocked.Subscription.modify.assert_not_called()
        mocked.Invoice.void_invoice.assert_not_called()

    def test_a_pending_switch_is_cancelled_by_voiding_its_invoice(self):
        self.subscribe()
        for invoice in ("in_pending", {"id": "in_pending"}):
            with self.subTest(invoice=invoice):
                mocked = stripe_mock([subscription(pending=True, invoice=invoice)])
                answer = self.call(account.switch, self.user, "team", mocked=mocked)
                self.assertEqual(answer, Answer(200, {"plan": "team", "status": "pending_cancelled"}), ACCOUNT)
                mocked.Invoice.void_invoice.assert_called_once_with("in_pending")

    def test_a_pending_switch_with_no_invoice_to_void_is_unchanged(self):
        self.subscribe()
        mocked = stripe_mock([subscription(pending=True)])
        with self.assertLogs("drop_billing", "WARNING"):
            answer = self.call(account.switch, self.user, "team", mocked=mocked)
        self.assertEqual(answer, Answer(200, {"plan": "team", "status": "unchanged"}), ACCOUNT)
        mocked.Invoice.void_invoice.assert_not_called()

    def test_a_payment_that_fails_leaves_the_plan_and_names_the_invoice_page_only_when_there_is_one(self):
        self.subscribe()
        invoice = {"id": "in_x", "hosted_invoice_url": "https://invoice.stripe.com/i/x"}
        with_page = mock.Mock(return_value=subscription(pending=True, invoice=invoice))
        without = mock.Mock(return_value=subscription(pending=True, invoice="in_str"))
        with self.assertLogs("drop_billing", "WARNING"):
            first = self.call(account.switch, self.user, "scale", mocked=stripe_mock([subscription()], Subscription__modify=with_page))
            second = self.call(account.switch, self.user, "scale", mocked=stripe_mock([subscription()], Subscription__modify=without))
        self.assertEqual((first.status, list(first.body)), (402, ["error", "code", "plan", "invoice_url"]), ACCOUNT)
        self.assertEqual((first.body["code"], first.body["plan"], first.body["invoice_url"]), ("payment_required", "team", "https://invoice.stripe.com/i/x"), ACCOUNT)
        self.assertIn("invoice page", first.body["error"], ACCOUNT)
        self.assertEqual((second.status, second.body["invoice_url"]), (402, None), ACCOUNT)
        self.assertNotIn("invoice page", second.body["error"], ACCOUNT)
        self.assertEqual(self.reload().plan, "team", ACCOUNT)

    def test_past_due_and_more_than_one_subscription_are_409s_that_sync_the_local_state(self):
        self.subscribe("free", sub_id="")
        answer = self.call(account.switch, self.user, "scale", mocked=stripe_mock([subscription(status="past_due")]))
        self.assertEqual((answer.status, answer.body["code"]), (409, "subscription_past_due"), ACCOUNT)
        self.assertEqual(self.reload().plan, "team", f"{WHERE}: a refusal brings the local plan up to Stripe's")
        self.subscribe(sub_id="sub_Z")
        with self.assertLogs("drop_billing", "ERROR"):
            two = self.call(account.switch, self.user, "scale", mocked=stripe_mock([subscription("sub_A"), subscription("sub_B")]))
        self.assertEqual((two.status, two.body["code"]), (409, "multiple_subscriptions"), ACCOUNT)

    def test_no_live_subscription_is_a_409_and_so_is_one_with_no_plan_item(self):
        self.subscribe()
        self.assertEqual(self.call(account.switch, self.user, "scale", mocked=stripe_mock([subscription(status="canceled")])), NO_SUBSCRIPTION, ACCOUNT)
        bare = {"id": "sub_A", "status": "active", "items": {"data": []}}
        self.subscribe()
        with self.assertLogs("drop_billing", "ERROR"):
            self.assertEqual(self.call(account.switch, self.user, "scale", mocked=stripe_mock([bare])), NO_SUBSCRIPTION, ACCOUNT)

    def test_a_customer_stripe_does_not_know_is_unlinked_and_the_answer_is_no_subscription(self):
        self.subscribe()
        missing = FakeStripeError("No such customer", **MISSING)
        answer = self.call(account.switch, self.user, "scale", mocked=stripe_mock(Subscription__list=mock.Mock(side_effect=missing)))
        self.assertEqual(answer, NO_SUBSCRIPTION, ACCOUNT)
        workspace = self.reload()
        self.assertEqual((workspace.plan, workspace.stripe_customer_id, workspace.stripe_subscription_id), ("free", "", ""), ACCOUNT)

    def test_a_stripe_error_is_a_502_and_the_whole_switch_is_undone(self):
        # The local plan lags Stripe's: the switch would sync it before voiding the invoice, and the void fails.
        self.subscribe("free", sub_id="")
        failing = stripe_mock(
            [subscription(pending=True, invoice="in_pending")],
            Invoice__void_invoice=mock.Mock(side_effect=FakeStripeError("void failed")),
        )
        with self.assertLogs("drop_billing", "ERROR"):
            answer = self.call(account.switch, self.user, "team", mocked=failing)
        self.assertEqual(answer, Answer(502, {"error": "Stripe: void failed", "code": "stripe_error"}), ACCOUNT)
        self.assertEqual(self.reload().plan, "free", f"{WHERE}: a switch is one transaction; a Stripe error rolls it back")

    def test_a_stripe_error_while_changing_the_price_is_a_502_and_the_plan_stays(self):
        self.subscribe()
        failing = stripe_mock([subscription()], Subscription__modify=mock.Mock(side_effect=FakeStripeError("api down")))
        with self.assertLogs("drop_billing", "ERROR"):
            answer = self.call(account.switch, self.user, "scale", mocked=failing)
        self.assertEqual(answer, Answer(502, {"error": "Stripe: api down", "code": "stripe_error"}), ACCOUNT)
        self.assertEqual(self.reload().plan, "team", ACCOUNT)


class PortalTests(AccountCase):
    def test_not_configured_is_a_503_before_the_workspace_is_read(self):
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": ""}):
            self.assertEqual(self.call(account.portal, self.user), NOT_CONFIGURED, ACCOUNT)

    def test_no_customer_is_a_400_that_asks_stripe_nothing(self):
        mocked = stripe_mock()
        self.assertEqual(self.call(account.portal, self.user, mocked=mocked), Answer(400, {"error": "no_billing_account"}), ACCOUNT)
        mocked.billing_portal.Session.create.assert_not_called()

    def test_the_portal_url_and_the_page_it_returns_to(self):
        self.subscribe()
        mocked = stripe_mock()
        answer = self.call(account.portal, self.user, mocked=mocked)
        self.assertEqual(answer, Answer(200, {"portal_url": "https://billing.stripe.com/session/bps_test_123"}), ACCOUNT)
        mocked.billing_portal.Session.create.assert_called_once_with(customer="cus_S", return_url="https://veilfile.test/app/")

    def test_a_stripe_error_is_a_502(self):
        self.subscribe()
        failing = stripe_mock(billing_portal__Session__create=mock.Mock(side_effect=FakeStripeError("configuration missing")))
        with self.assertLogs("drop_billing", "ERROR"):
            answer = self.call(account.portal, self.user, mocked=failing)
        self.assertEqual(answer, Answer(502, {"error": "Stripe: configuration missing", "code": "stripe_error"}), ACCOUNT)
        self.assertEqual(self.reload().stripe_customer_id, "cus_S", ACCOUNT)

    def test_a_customer_stripe_does_not_know_is_unlinked_and_answers_no_billing_account(self):
        self.subscribe()
        missing = FakeStripeError("No such customer: 'cus_S'", **MISSING)
        failing = stripe_mock(billing_portal__Session__create=mock.Mock(side_effect=missing))
        self.assertEqual(self.call(account.portal, self.user, mocked=failing), Answer(400, {"error": "no_billing_account"}), ACCOUNT)
        workspace = self.reload()
        self.assertEqual((workspace.plan, workspace.stripe_customer_id), ("free", ""), ACCOUNT)

    def test_a_customer_of_the_other_stripe_mode_is_a_502_and_is_not_unlinked(self):
        self.subscribe()
        live = FakeStripeError("No such customer: 'cus_S'; a similar object exists in live mode", **MISSING)
        failing = stripe_mock(billing_portal__Session__create=mock.Mock(side_effect=live))
        with self.assertLogs("drop_billing", "ERROR"):
            answer = self.call(account.portal, self.user, mocked=failing)
        self.assertEqual(answer.status, 502, ACCOUNT)
        self.assertEqual(self.reload().stripe_customer_id, "cus_S", f"{WHERE}: a misconfigured key must not unlink paying customers")


class SeamTests(AccountCase):
    def test_the_library_is_read_from_drop_billing_views_each_time(self):
        first, second = stripe_mock(), stripe_mock()
        self.call(account.checkout, self.user, "team", mocked=first)
        self.call(account.checkout, self.user, "team", mocked=second)
        self.assertEqual(
            (first.checkout.Session.create.call_count, second.checkout.Session.create.call_count),
            (1, 1),
            "backend/drop_billing/account.py reads the stripe library from backend/drop_billing/views.py (_stripe_library) at "
            "call time: the tests, and the dashboard's, patch drop_billing.views.stripe",
        )

    def test_views_still_names_what_other_code_imports(self):
        from drop_billing import views

        # sync_stripe_billing and drop_core.views import these from drop_billing.views; the tests patch stripe there.
        for name in (
            "stripe", "paid_signup_redirect", "create_checkout_session_for_user", "LIVE_STATUSES",
            "_apply_subscriptions", "_as_list", "_derive_billing_state", "_forget_missing_customer",
            "_list_subscriptions", "_price_ids", "_stripe_or_503", "_subscription_plan", "webhook",
        ):
            self.assertTrue(
                hasattr(views, name),
                f"backend/drop_billing/views.py must keep the name {name}: the sync_stripe_billing command, "
                "drop_core.views or the tests import or patch it from there",
            )

    def test_the_sync_command_gets_the_same_functions(self):
        from drop_billing import views

        self.assertIs(views._list_subscriptions, account._list_subscriptions, "backend/drop_billing/views.py re-exports the functions of account.py")
        self.assertIs(views._stripe_or_503, account._stripe_or_503, "backend/drop_billing/views.py re-exports the functions of account.py")


class ModuleRulesTests(AccountCase):
    def source(self):
        return (Path(settings.BASE_DIR) / "drop_billing" / "account.py").read_text()

    def test_it_imports_no_rest_framework_and_no_http_layer(self):
        found = []
        for node in ast.walk(ast.parse(self.source())):
            if isinstance(node, ast.Import):
                found += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                found.append(node.module or "")
        banned = [name for name in found if name.split(".")[0] == "rest_framework" or name in ("django.http", "django.shortcuts")]
        self.assertEqual(banned, [],
                         f"{WHERE} is plain functions that the JSON endpoints and the dashboard share: it must not import "
                         "Django REST framework or Django's HTTP layer. Take what it needs as arguments, and send the Answer "
                         "from the view (backend/drop_core/errors.py: answer_response)")

    def test_it_does_not_import_stripe_itself(self):
        found = [n.names[0].name for n in ast.walk(ast.parse(self.source())) if isinstance(n, ast.Import)]
        self.assertNotIn("stripe", found,
                         f"{WHERE} must get the library from backend/drop_billing/views.py (_stripe_library), where the tests patch it; "
                         "an import here would make every mocked test call the real Stripe")

    def test_it_types_no_price(self):
        self.assertEqual(re.findall(r"\$\s?\d", self.source()), [],
                         f"{WHERE} must not type a price: plans and prices come from backend/drop_core/plans.py (AGENTS.md, Pricing changes)")

    def test_it_reads_the_environment_only_when_a_function_runs(self):
        top_level = [n for n in ast.parse(self.source()).body if isinstance(n, ast.Assign)]
        reads = [ast.unparse(n) for n in top_level if "environ" in ast.unparse(n)]
        self.assertEqual(reads, [], f"{WHERE} reads STRIPE_* and DROP_BASE_URL when a function runs, never at import: the tests set them per test")


class EndpointsSendTheirAnswersTests(AccountCase):
    """The JSON endpoints add nothing to an Answer and take nothing from it."""

    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(user=self.user)

    def same(self, response, answer):
        self.assertEqual(response.status_code, answer.status, "backend/drop_billing/views.py must send the status of the Answer")
        self.assertEqual(response.json(), answer.body, "backend/drop_billing/views.py must send the body of the Answer")

    def post(self, path, payload, mocked):
        with mock.patch("drop_billing.views.stripe", mocked):
            return self.api.post(f"/api/v1/billing/{path}", payload, format="json")

    def test_each_endpoint_sends_what_its_function_answers(self):
        self.same(self.api.get("/api/v1/billing/status"), account.status(self.user))
        self.same(self.post("checkout", {"plan": "enterprise"}, stripe_mock()), account.checkout(self.user, "enterprise"))
        self.same(self.post("switch", {"plan": "free"}, stripe_mock()), account.switch(self.user, "free"))
        self.same(self.post("portal", {}, stripe_mock()), self.call(account.portal, self.user))
        self.subscribe()
        mocked = stripe_mock([subscription(status="past_due")])
        self.same(self.post("checkout", {"plan": "scale"}, mocked), self.call(account.checkout, self.user, "scale", mocked=mocked))
        self.subscribe()
        self.same(self.post("switch", {"plan": "scale"}, mocked), self.call(account.switch, self.user, "scale", mocked=mocked))
        self.same(self.post("portal", {}, mocked), self.call(account.portal, self.user, mocked=mocked))

    def test_the_sign_up_flows_helper_sends_a_response_from_an_answer(self):
        from drop_billing.views import create_checkout_session_for_user

        with mock.patch("drop_billing.views.stripe", stripe_mock()):
            url, err = create_checkout_session_for_user(self.user, "team")
        self.assertEqual((url, err), ("https://checkout.stripe.com/pay/cs_test_123", None), "backend/drop_billing/views.py: (url, None) on success")
        url, err = create_checkout_session_for_user(self.user, "enterprise")
        self.assertEqual((url, err.status_code, err.data), (None, 400, {"error": "invalid_plan"}), "backend/drop_billing/views.py: (None, Response) on a refusal, which paid_signup_redirect reads")
