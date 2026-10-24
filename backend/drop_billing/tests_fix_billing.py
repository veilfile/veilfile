"""Regression tests for the billing review findings (bill-1..bill-12, web-1,
web-8, ctr-4).

Webhooks are posted as real Stripe-shaped JSON, HMAC-signed with a test
secret and verified by the real ``stripe.Webhook.construct_event``. Only
outbound Stripe API calls (Subscription.list/modify, Customer.create,
checkout/portal Session.create) are mocked, and ``stripe.api_base`` points
at a closed local port so an unmocked call fails instead of reaching Stripe.
"""

import hashlib
import hmac
import io
import json
import os
import time
from datetime import timedelta
from unittest import mock

import stripe
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import Client, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from drop_billing.models import ProcessedStripeEvent, Subscription
from drop_core.models import (
    APIKey,
    Artifact,
    EmailVerification,
    Workspace,
    get_or_create_workspace,
)

ENV = {
    "STRIPE_SECRET_KEY": "sk_test_fixbilling",
    "STRIPE_WEBHOOK_SECRET": "whsec_fixbilling",
    "STRIPE_PRICE_TEAM": "price_team",
    "STRIPE_PRICE_SCALE": "price_scale",
    "DROP_BASE_URL": "https://veilfile.test",
}

PERIOD_END = 1790000000


def make_sub(
    sub_id,
    status,
    price,
    customer="cus_A",
    created=1,
    item_id="si_1",
    pending_update=None,
    basil=True,
    metadata=None,
):
    """A subscription as Stripe renders it at API version 2025-03-31.basil
    (period end on the items), or the pre-basil shape (top level)."""
    item = {
        "id": item_id,
        "object": "subscription_item",
        "price": {"id": price, "object": "price"},
        "quantity": 1,
    }
    obj = {
        "id": sub_id,
        "object": "subscription",
        "customer": customer,
        "status": status,
        "created": created,
        "items": {"object": "list", "data": [item], "has_more": False},
        "metadata": metadata or {},
        "pending_update": pending_update,
    }
    if basil:
        item["current_period_end"] = PERIOD_END
    else:
        obj["current_period_end"] = PERIOD_END
    return obj


def sub_list(*subs):
    return {"object": "list", "data": list(subs), "has_more": False}


_event_seq = [0]


def make_event(type_, obj, event_id=None):
    _event_seq[0] += 1
    return {
        "id": event_id or f"evt_fix_{_event_seq[0]}",
        "object": "event",
        "api_version": "2025-03-31.basil",
        "created": int(time.time()),
        "type": type_,
        "data": {"object": obj},
    }


def checkout_completed(customer="cus_A", sub_id="sub_A", plan="team", **extra):
    obj = {
        "id": "cs_test_1",
        "object": "checkout.session",
        "mode": "subscription",
        "customer": customer,
        "subscription": sub_id,
        "metadata": {"plan": plan},
        "payment_status": "paid",
        "status": "complete",
    }
    obj.update(extra)
    return make_event("checkout.session.completed", obj)


def missing_customer_error(customer="cus_TESTMODE", hint="test"):
    """Stripe's resource_missing error. ``hint`` is the mode Stripe says the
    customer exists in ("test": a test-mode leftover seen with a live key;
    "live": a live customer seen with a test key; None: no such customer)."""
    message = f"No such customer: '{customer}'"
    if hint:
        other = "live" if hint == "test" else "test"
        message += (
            f"; a similar object exists in {hint} mode, but a {other} mode key "
            "was used to make this request."
        )
    return stripe.InvalidRequestError(
        message, "customer", code="resource_missing", http_status=400
    )


class FixBillingBase(TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, ENV, clear=False)
        env.start()
        self.addCleanup(env.stop)
        # Belt and braces: any Stripe call a test forgot to mock fails fast
        # against a closed local port instead of reaching api.stripe.com.
        for attr in ("api_base", "connect_api_base", "upload_api_base"):
            patcher = mock.patch.object(stripe, attr, "http://127.0.0.1:9")
            patcher.start()
            self.addCleanup(patcher.stop)
        self.user = get_user_model().objects.create_user(
            username="payer", email="payer@example.com", password="pw-123456-xyz"
        )
        self.ws = get_or_create_workspace(self.user)
        self.api = APIClient()
        self.api.force_authenticate(user=self.user)
        self.hook = Client()

    def patch_stripe(self, name, **kwargs):
        target = {
            "list": (stripe.Subscription, "list"),
            "modify": (stripe.Subscription, "modify"),
            "customer": (stripe.Customer, "create"),
            "session": (stripe.checkout.Session, "create"),
            "portal": (stripe.billing_portal.Session, "create"),
            "void": (stripe.Invoice, "void_invoice"),
            "retrieve_customer": (stripe.Customer, "retrieve"),
        }[name]
        patcher = mock.patch.object(*target, **kwargs)
        m = patcher.start()
        self.addCleanup(patcher.stop)
        return m

    def post_event(self, event, secret=None):
        secret = ENV["STRIPE_WEBHOOK_SECRET"] if secret is None else secret
        payload = json.dumps(event)
        ts = int(time.time())
        sig = hmac.new(
            secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256
        ).hexdigest()
        return self.hook.post(
            "/api/v1/billing/webhook",
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=f"t={ts},v1={sig}",
        )

    def set_billing(self, plan="free", customer="cus_A", sub_id="", status=""):
        self.ws.plan = plan
        self.ws.stripe_customer_id = customer
        self.ws.stripe_subscription_id = sub_id
        self.ws.save()
        if sub_id:
            Subscription.objects.update_or_create(
                workspace=self.ws,
                defaults={
                    "stripe_customer_id": customer,
                    "stripe_subscription_id": sub_id,
                    "status": status or "active",
                },
            )

    def reload(self):
        self.ws.refresh_from_db()
        return self.ws


# ---------------------------------------------------------------------------
# bill-1 / web-1: a second Checkout for an existing subscriber
# ---------------------------------------------------------------------------
class CheckoutGuardTests(FixBillingBase):
    def setUp(self):
        super().setUp()
        self.session = self.patch_stripe(
            "session",
            return_value={"id": "cs_1", "url": "https://checkout.stripe.com/c/cs_1"},
        )
        self.customer = self.patch_stripe("customer", return_value={"id": "cus_NEW"})

    def checkout(self, plan):
        return self.api.post("/api/v1/billing/checkout", {"plan": plan}, format="json")

    def test_team_subscriber_cannot_open_scale_checkout(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_team"))
        )
        resp = self.checkout("scale")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "already_subscribed")
        self.assertIn("error", resp.json())
        self.session.assert_not_called()

    def test_same_plan_checkout_refused(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_team"))
        )
        self.assertEqual(self.checkout("team").status_code, 409)
        self.session.assert_not_called()

    def test_every_live_status_blocks_checkout(self):
        for status in ("active", "trialing", "past_due", "unpaid", "incomplete", "paused"):
            with self.subTest(status=status):
                self.set_billing("free", customer="cus_A")
                self.patch_stripe(
                    "list",
                    return_value=sub_list(make_sub("sub_A", status, "price_team")),
                )
                resp = self.checkout("scale")
                self.assertEqual(resp.status_code, 409, status)
                self.assertEqual(resp.json()["code"], "already_subscribed")
        self.session.assert_not_called()

    def test_stripe_side_subscription_blocks_even_if_webhook_lagging(self):
        # Local state still says free (webhook not processed yet), but
        # Stripe already has the subscription.
        self.set_billing("free", customer="cus_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_team"))
        )
        self.assertEqual(self.checkout("team").status_code, 409)
        self.session.assert_not_called()

    def test_ended_subscriptions_do_not_block_checkout(self):
        self.set_billing("free", customer="cus_A")
        lister = self.patch_stripe(
            "list",
            return_value=sub_list(
                make_sub("sub_old", "canceled", "price_team"),
                make_sub("sub_old2", "incomplete_expired", "price_scale"),
            ),
        )
        resp = self.checkout("team")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(lister.call_args.kwargs["customer"], "cus_A")
        self.session.assert_called_once()

    def test_checkout_tags_session_with_workspace(self):
        self.patch_stripe("list", return_value=sub_list())
        resp = self.checkout("team")
        self.assertEqual(resp.status_code, 200)
        kwargs = self.session.call_args.kwargs
        self.assertEqual(kwargs["client_reference_id"], str(self.ws.id))
        self.assertEqual(
            kwargs["subscription_data"]["metadata"]["workspace_id"], str(self.ws.id)
        )
        self.assertEqual(kwargs["metadata"], {"plan": "team"})

    # -- bill-4: test-mode customer ids carried into live mode -------------
    def test_missing_test_mode_customer_is_replaced(self):
        self.set_billing("free", customer="cus_TESTMODE")
        self.patch_stripe("list", side_effect=missing_customer_error())
        resp = self.checkout("team")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.customer.assert_called_once()
        self.assertEqual(self.session.call_args.kwargs["customer"], "cus_NEW")
        self.assertEqual(self.reload().stripe_customer_id, "cus_NEW")

    def test_session_rejecting_customer_recreates_it_once(self):
        self.set_billing("free", customer="cus_TESTMODE")
        self.patch_stripe("list", return_value=sub_list())
        self.session.side_effect = [
            missing_customer_error(),
            {"id": "cs_2", "url": "https://checkout.stripe.com/c/cs_2"},
        ]
        resp = self.checkout("scale")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["checkout_url"], "https://checkout.stripe.com/c/cs_2")
        self.assertEqual(self.session.call_args.kwargs["customer"], "cus_NEW")
        self.assertEqual(self.reload().stripe_customer_id, "cus_NEW")

    def test_other_stripe_errors_still_502_json(self):
        self.patch_stripe("list", return_value=sub_list())
        self.session.side_effect = stripe.InvalidRequestError(
            "Invalid line_items[0]: the product tax code is missing.", "line_items"
        )
        resp = self.checkout("team")
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["code"], "stripe_error")
        self.assertTrue(resp.json()["error"].startswith("Stripe:"))


# ---------------------------------------------------------------------------
# Owner decision 1: in-app plan switch with proration
# ---------------------------------------------------------------------------
class SwitchPlanTests(FixBillingBase):
    def switch(self, plan, client=None):
        return (client or self.api).post(
            "/api/v1/billing/switch", {"plan": plan}, format="json"
        )

    def test_team_to_scale_swaps_price_item_with_proration(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_team"))
        )
        modify = self.patch_stripe(
            "modify", return_value=make_sub("sub_A", "active", "price_scale")
        )
        resp = self.switch("scale")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["plan"], "scale")
        args, kwargs = modify.call_args
        self.assertEqual(args, ("sub_A",))
        self.assertEqual(kwargs["items"], [{"id": "si_1", "price": "price_scale"}])
        self.assertEqual(kwargs["proration_behavior"], "always_invoice")
        self.assertEqual(kwargs["payment_behavior"], "pending_if_incomplete")
        ws = self.reload()
        self.assertEqual(ws.plan, "scale")
        self.assertEqual(ws.stripe_subscription_id, "sub_A")
        self.assertEqual(Subscription.objects.get(workspace=ws).status, "active")

    def test_scale_to_team_downgrade(self):
        self.set_billing("scale", sub_id="sub_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_scale"))
        )
        modify = self.patch_stripe(
            "modify", return_value=make_sub("sub_A", "active", "price_team")
        )
        resp = self.switch("team")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(modify.call_args.kwargs["items"], [{"id": "si_1", "price": "price_team"}])
        self.assertEqual(self.reload().plan, "team")

    def test_switch_to_current_plan_is_idempotent(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_team"))
        )
        modify = self.patch_stripe("modify")
        resp = self.switch("team")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "unchanged")
        modify.assert_not_called()

    def test_failed_upgrade_payment_leaves_plan_unchanged(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_team"))
        )
        pending = {
            "expires_at": int(time.time()) + 3600,
            "subscription_items": [{"id": "si_1", "price": {"id": "price_scale"}}],
        }
        self.patch_stripe(
            "modify",
            return_value=make_sub("sub_A", "active", "price_team", pending_update=pending),
        )
        resp = self.switch("scale")
        self.assertEqual(resp.status_code, 402)
        self.assertEqual(resp.json()["code"], "payment_required")
        self.assertEqual(self.reload().plan, "team")

    def test_free_user_has_nothing_to_switch(self):
        self.patch_stripe("list", return_value=sub_list())
        modify = self.patch_stripe("modify")
        resp = self.switch("team")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "no_subscription")
        modify.assert_not_called()

    def test_past_due_subscription_must_fix_payment_first(self):
        self.set_billing("team", sub_id="sub_A", status="past_due")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "past_due", "price_team"))
        )
        modify = self.patch_stripe("modify")
        resp = self.switch("scale")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "subscription_past_due")
        modify.assert_not_called()

    def test_stripe_error_is_json_not_500(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_team"))
        )
        self.patch_stripe(
            "modify", side_effect=stripe.APIConnectionError("connection reset")
        )
        resp = self.switch("scale")
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["code"], "stripe_error")
        self.assertEqual(self.reload().plan, "team")

    def test_missing_test_mode_customer_is_cleared(self):
        self.set_billing("team", customer="cus_TESTMODE", sub_id="sub_test")
        self.patch_stripe("list", side_effect=missing_customer_error())
        resp = self.switch("scale")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "no_subscription")
        ws = self.reload()
        self.assertEqual(ws.stripe_customer_id, "")
        self.assertEqual(ws.stripe_subscription_id, "")

    def test_invalid_plan(self):
        resp = self.switch("free")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "invalid_plan")

    def test_requires_session_and_csrf(self):
        self.assertEqual(APIClient().post("/api/v1/billing/switch", {"plan": "scale"}).status_code, 403)
        csrf_client = APIClient(enforce_csrf_checks=True)
        csrf_client.login(username="payer", password="pw-123456-xyz")
        modify = self.patch_stripe("modify")
        resp = self.switch("scale", client=csrf_client)
        self.assertEqual(resp.status_code, 403)
        modify.assert_not_called()


# ---------------------------------------------------------------------------
# Webhooks: bill-2, bill-3, ctr-4, bill-5, bill-6, bill-7, bill-9, bill-12
# ---------------------------------------------------------------------------
class WebhookTests(FixBillingBase):
    # -- bill-7 -----------------------------------------------------------
    def test_unset_secret_fails_closed(self):
        lister = self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_X", "active", "price_scale"))
        )
        self.set_billing("free", customer="cus_A")
        with mock.patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET": ""}):
            resp = self.post_event(
                checkout_completed(sub_id="sub_X", plan="scale"), secret=""
            )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(self.reload().plan, "free")
        lister.assert_not_called()

    def test_malformed_secret_fails_closed(self):
        with mock.patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET": "not-a-secret"}):
            resp = self.post_event(checkout_completed(), secret="not-a-secret")
        self.assertEqual(resp.status_code, 503)

    def test_wrong_signature_400(self):
        resp = self.post_event(checkout_completed(), secret="whsec_other")
        self.assertEqual(resp.status_code, 400)

    # -- bill-2 -----------------------------------------------------------
    def test_old_subscription_deleted_keeps_current_plan(self):
        self.set_billing("scale", sub_id="sub_B")
        self.patch_stripe(
            "list",
            return_value=sub_list(
                make_sub("sub_A", "canceled", "price_team", created=1),
                make_sub("sub_B", "active", "price_scale", created=2, item_id="si_2"),
            ),
        )
        resp = self.post_event(
            make_event(
                "customer.subscription.deleted",
                make_sub("sub_A", "canceled", "price_team"),
            )
        )
        self.assertEqual(resp.status_code, 200)
        ws = self.reload()
        self.assertEqual(ws.plan, "scale")
        self.assertEqual(ws.stripe_subscription_id, "sub_B")
        self.assertEqual(Subscription.objects.get(workspace=ws).status, "active")

    def test_two_live_subscriptions_logged_and_highest_plan_kept(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe(
            "list",
            return_value=sub_list(
                make_sub("sub_A", "active", "price_team", created=1),
                make_sub("sub_B", "active", "price_scale", created=2, item_id="si_2"),
            ),
        )
        with self.assertLogs("drop_billing", level="ERROR") as logs:
            resp = self.post_event(
                make_event(
                    "customer.subscription.created",
                    make_sub("sub_B", "active", "price_scale"),
                )
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.reload().plan, "scale")
        self.assertTrue(any("2 live subscriptions" in m for m in logs.output), logs.output)

    # -- bill-3 / ctr-4 ---------------------------------------------------
    def test_portal_upgrade_maps_new_price(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_scale"))
        )
        resp = self.post_event(
            make_event(
                "customer.subscription.updated",
                make_sub("sub_A", "active", "price_scale"),
            )
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.reload().plan, "scale")

    def test_portal_downgrade_maps_new_price(self):
        self.set_billing("scale", sub_id="sub_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_team"))
        )
        self.post_event(
            make_event(
                "customer.subscription.updated",
                make_sub("sub_A", "active", "price_team"),
            )
        )
        self.assertEqual(self.reload().plan, "team")

    def test_unknown_price_logged_and_plan_left_alone(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_mystery"))
        )
        with self.assertLogs("drop_billing", level="ERROR") as logs:
            resp = self.post_event(
                make_event(
                    "customer.subscription.updated",
                    make_sub("sub_A", "active", "price_mystery"),
                )
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.reload().plan, "team")
        self.assertTrue(any("price_mystery" in m for m in logs.output), logs.output)

    # -- Owner decision 2 / bill-6 ----------------------------------------
    def test_dunning_statuses(self):
        expectations = {
            "active": "team",
            "trialing": "team",
            "past_due": "team",  # keeps the plan while Stripe retries
            "unpaid": "free",
            "incomplete_expired": "free",
            "paused": "free",
            "canceled": "free",
        }
        for status, plan in expectations.items():
            with self.subTest(status=status):
                self.set_billing("team", sub_id="sub_A")
                self.patch_stripe(
                    "list",
                    return_value=sub_list(make_sub("sub_A", status, "price_team")),
                )
                resp = self.post_event(
                    make_event(
                        "customer.subscription.updated",
                        make_sub("sub_A", status, "price_team"),
                    )
                )
                self.assertEqual(resp.status_code, 200)
                ws = self.reload()
                self.assertEqual(ws.plan, plan, status)
                self.assertEqual(
                    Subscription.objects.get(workspace=ws).status, status
                )

    def test_subscription_deleted_downgrades(self):
        self.set_billing("scale", sub_id="sub_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "canceled", "price_scale"))
        )
        self.post_event(
            make_event(
                "customer.subscription.deleted",
                make_sub("sub_A", "canceled", "price_scale"),
            )
        )
        ws = self.reload()
        self.assertEqual(ws.plan, "free")
        self.assertEqual(ws.stripe_subscription_id, "")
        self.assertEqual(Subscription.objects.get(workspace=ws).status, "canceled")

    def test_unpaid_checkout_does_not_grant(self):
        self.set_billing("free", customer="cus_A")
        lister = self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "incomplete", "price_team"))
        )
        resp = self.post_event(checkout_completed(payment_status="unpaid"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.reload().plan, "free")
        lister.assert_not_called()

    def test_paid_checkout_grants_plan_from_stripe_state(self):
        self.set_billing("free", customer="cus_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_team"))
        )
        resp = self.post_event(checkout_completed())
        self.assertEqual(resp.status_code, 200)
        ws = self.reload()
        self.assertEqual(ws.plan, "team")
        self.assertEqual(ws.stripe_subscription_id, "sub_A")
        sub = Subscription.objects.get(workspace=ws)
        self.assertEqual(sub.status, "active")
        self.assertEqual(sub.stripe_customer_id, "cus_A")

    # -- bill-5 -----------------------------------------------------------
    def test_redelivered_checkout_after_cancel_does_not_regrant(self):
        self.set_billing("free", customer="cus_A")
        lister = self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_scale"))
        )
        completed = checkout_completed(plan="scale")
        self.post_event(completed)
        self.assertEqual(self.reload().plan, "scale")

        lister.return_value = sub_list(make_sub("sub_A", "canceled", "price_scale"))
        self.post_event(
            make_event(
                "customer.subscription.deleted",
                make_sub("sub_A", "canceled", "price_scale"),
            )
        )
        self.assertEqual(self.reload().plan, "free")

        calls = lister.call_count
        resp = self.post_event(completed)  # same event id, redelivered
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(lister.call_count, calls)  # skipped as a duplicate
        self.assertEqual(self.reload().plan, "free")

        # A late, distinct completed event is re-derived from Stripe state.
        self.post_event(checkout_completed(plan="scale"))
        self.assertEqual(self.reload().plan, "free")
        self.assertTrue(
            ProcessedStripeEvent.objects.filter(event_id=completed["id"]).exists()
        )

    def test_out_of_order_past_due_then_completed(self):
        self.set_billing("free", customer="cus_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "past_due", "price_team"))
        )
        self.post_event(
            make_event(
                "customer.subscription.updated",
                make_sub("sub_A", "past_due", "price_team"),
            )
        )
        self.post_event(checkout_completed())
        self.assertEqual(
            Subscription.objects.get(workspace=self.reload()).status, "past_due"
        )

    def test_stripe_outage_during_sync_returns_500_and_allows_retry(self):
        self.set_billing("free", customer="cus_A")
        lister = self.patch_stripe(
            "list", side_effect=stripe.APIConnectionError("timeout")
        )
        event = checkout_completed()
        with self.assertLogs("drop_billing", level="ERROR"), self.assertLogs(
            "django.request", level="ERROR"
        ):
            resp = self.post_event(event)
        self.assertEqual(resp.status_code, 500)
        self.assertFalse(
            ProcessedStripeEvent.objects.filter(event_id=event["id"]).exists()
        )
        lister.side_effect = None
        lister.return_value = sub_list(make_sub("sub_A", "active", "price_team"))
        self.assertEqual(self.post_event(event).status_code, 200)
        self.assertEqual(self.reload().plan, "team")

    # -- bill-9 -----------------------------------------------------------
    def test_unmapped_paid_checkout_is_logged(self):
        lister = self.patch_stripe("list", return_value=sub_list())
        with self.assertLogs("drop_billing", level="ERROR") as logs:
            resp = self.post_event(checkout_completed(customer="cus_other"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any("cus_other" in m for m in logs.output), logs.output)
        lister.assert_not_called()

    def test_checkout_resolves_workspace_by_client_reference(self):
        self.set_billing("free", customer="")
        self.patch_stripe(
            "list",
            return_value=sub_list(
                make_sub("sub_A", "active", "price_team", customer="cus_fresh")
            ),
        )
        resp = self.post_event(
            checkout_completed(
                customer="cus_fresh", client_reference_id=str(self.ws.id)
            )
        )
        self.assertEqual(resp.status_code, 200)
        ws = self.reload()
        self.assertEqual(ws.stripe_customer_id, "cus_fresh")
        self.assertEqual(ws.plan, "team")

    def test_unmapped_paid_invoice_is_logged(self):
        with self.assertLogs("drop_billing", level="ERROR"):
            resp = self.post_event(
                make_event(
                    "invoice.paid",
                    {"id": "in_1", "object": "invoice", "customer": "cus_nobody"},
                )
            )
        self.assertEqual(resp.status_code, 200)

    # -- bill-12 ----------------------------------------------------------
    def test_basil_period_end_read_from_items(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_team"))
        )
        self.post_event(
            make_event(
                "customer.subscription.updated",
                make_sub("sub_A", "active", "price_team"),
            )
        )
        sub = Subscription.objects.get(workspace=self.ws)
        self.assertIsNotNone(sub.current_period_end)
        self.assertEqual(int(sub.current_period_end.timestamp()), PERIOD_END)

    def test_pre_basil_period_end_still_read(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe(
            "list",
            return_value=sub_list(make_sub("sub_A", "active", "price_team", basil=False)),
        )
        self.post_event(
            make_event(
                "customer.subscription.updated",
                make_sub("sub_A", "active", "price_team", basil=False),
            )
        )
        sub = Subscription.objects.get(workspace=self.ws)
        self.assertEqual(int(sub.current_period_end.timestamp()), PERIOD_END)

    def test_unrelated_event_acknowledged_without_stripe_call(self):
        lister = self.patch_stripe("list")
        resp = self.post_event(make_event("product.updated", {"id": "prod_1"}))
        self.assertEqual(resp.status_code, 200)
        lister.assert_not_called()


# ---------------------------------------------------------------------------
# bill-10: portal errors
# ---------------------------------------------------------------------------
class PortalTests(FixBillingBase):
    def test_portal_stripe_error_is_json_502(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe(
            "portal",
            side_effect=stripe.InvalidRequestError(
                "No configuration provided and your live mode default "
                "configuration has not been created.",
                None,
            ),
        )
        resp = self.api.post("/api/v1/billing/portal")
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["code"], "stripe_error")
        self.assertIn("configuration", resp.json()["error"])

    def test_portal_missing_test_mode_customer(self):
        self.set_billing("free", customer="cus_TESTMODE")
        self.patch_stripe("portal", side_effect=missing_customer_error())
        resp = self.api.post("/api/v1/billing/portal")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "no_billing_account")
        self.assertEqual(self.reload().stripe_customer_id, "")


# ---------------------------------------------------------------------------
# bill-8: downgrade reconciliation (surface, never auto-revoke)
# ---------------------------------------------------------------------------
class OverLimitTests(FixBillingBase):
    def setUp(self):
        super().setUp()
        for i in range(3):
            APIKey.create_key(self.ws, f"key {i}")
        Artifact.objects.create(
            workspace=self.ws,
            filename="big.bin",
            size_bytes=2 * 1024**3,
            expires_at=timezone.now() + timedelta(days=300),
        )

    def test_status_reports_over_plan_limits_after_downgrade(self):
        body = self.api.get("/api/v1/billing/status").json()
        self.assertEqual(body["plan"], "free")
        self.assertEqual(sorted(body["over_plan_limits"]), ["keys", "storage", "ttl"])
        self.assertEqual(body["active_keys"], 3)
        self.assertFalse(body["has_subscription"])
        # Nothing was revoked or deleted.
        self.assertEqual(self.ws.api_keys.filter(is_active=True).count(), 3)
        self.assertEqual(self.ws.artifacts.count(), 1)

    def test_within_limits_reports_nothing(self):
        self.set_billing("scale", sub_id="sub_A")
        body = self.api.get("/api/v1/billing/status").json()
        self.assertEqual(body["over_plan_limits"], [])
        self.assertTrue(body["has_subscription"])

    def test_new_key_blocked_while_over_cap(self):
        EmailVerification.objects.create(
            user=self.user, email=self.user.email, is_verified=True
        )
        resp = self.api.post("/api/v1/keys/", {"name": "more"}, format="json")
        self.assertEqual(resp.status_code, 402)
        self.assertEqual(resp.json()["code"], "key_limit_reached")

    def test_new_upload_ttl_capped_by_current_plan(self):
        from drop_core.services import mint_upload_url

        pending = mint_upload_url(self.ws, None, "notes.txt", ttl_days=365)
        self.assertEqual(pending.ttl_days, 7)


# ---------------------------------------------------------------------------
# bill-1 (signup path), bill-11, web-8: ?plan= signup flow
# ---------------------------------------------------------------------------
class PaidSignupTests(FixBillingBase):
    def setUp(self):
        super().setUp()
        self.web = Client()
        self.web.force_login(self.user)
        self.session = self.patch_stripe(
            "session",
            return_value={"id": "cs_1", "url": "https://checkout.stripe.com/c/cs_1"},
        )
        self.patch_stripe("customer", return_value={"id": "cus_NEW"})

    def test_signed_in_get_does_not_create_checkout(self):
        self.patch_stripe("list", return_value=sub_list())
        resp = self.web.get("/accounts/signup/?plan=team")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'method="post"')
        self.assertContains(resp, "Team")
        self.session.assert_not_called()

    def test_signed_in_post_creates_checkout(self):
        self.patch_stripe("list", return_value=sub_list())
        resp = self.web.post("/accounts/signup/?plan=team")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://checkout.stripe.com/c/cs_1")

    def test_subscriber_get_goes_to_dashboard(self):
        self.set_billing("team", sub_id="sub_A")
        resp = self.web.get("/accounts/signup/?plan=team")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/app/?billing=already_subscribed")
        self.session.assert_not_called()

    def test_subscriber_post_refused(self):
        self.set_billing("free", customer="cus_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_team"))
        )
        resp = self.web.post("/accounts/signup/?plan=scale")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/app/?billing=already_subscribed")
        self.session.assert_not_called()

    def test_signed_in_failure_logged_and_flagged(self):
        with mock.patch.dict(os.environ, {"STRIPE_PRICE_SCALE": ""}):
            with self.assertLogs("drop_billing", level="ERROR") as logs:
                resp = self.web.post("/accounts/signup/?plan=scale")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/app/?billing=error&plan=scale")
        self.assertTrue(any("scale" in m for m in logs.output))

    def test_fresh_signup_failure_logged_and_flagged(self):
        self.patch_stripe("list", return_value=sub_list())
        self.session.side_effect = stripe.InvalidRequestError(
            "No such price: 'price_team'", "line_items[0][price]"
        )
        anon = Client()
        with self.assertLogs("drop_billing", level="ERROR") as logs:
            resp = anon.post(
                "/accounts/signup/?plan=team",
                {
                    "username": "newpayer",
                    "email": "newpayer@example.com",
                    "password1": "s3cure-pass-123",
                    "password2": "s3cure-pass-123",
                },
            )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/app/?billing=error&plan=team")
        self.assertTrue(any("No such price" in m for m in logs.output), logs.output)


# ---------------------------------------------------------------------------
# bill-4: one-off reconciliation command for test-mode leftovers
# ---------------------------------------------------------------------------
class SyncCommandTests(FixBillingBase):
    def test_dry_run_reports_without_writing(self):
        self.set_billing("team", customer="cus_TESTMODE", sub_id="sub_test")
        self.patch_stripe("list", side_effect=missing_customer_error())
        out = io.StringIO()
        call_command("sync_stripe_billing", stdout=out)
        self.assertIn("cus_TESTMODE", out.getvalue())
        self.assertIn("dry run", out.getvalue().lower())
        ws = self.reload()
        self.assertEqual(ws.plan, "team")
        self.assertEqual(ws.stripe_customer_id, "cus_TESTMODE")

    def test_apply_clears_test_mode_leftovers_and_syncs_live(self):
        self.set_billing("team", customer="cus_TESTMODE", sub_id="sub_test")
        other_user = get_user_model().objects.create_user(username="live", password="x")
        live_ws = get_or_create_workspace(other_user)
        live_ws.stripe_customer_id = "cus_LIVE"
        live_ws.save()

        def fake_list(customer=None, **kwargs):
            if customer == "cus_TESTMODE":
                raise missing_customer_error()
            return sub_list(make_sub("sub_L", "active", "price_scale", customer="cus_LIVE"))

        self.patch_stripe("list", side_effect=fake_list)
        # In two pieces so that secret scanners don't take it for a live key.
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_live_" + "fixbilling"}):
            call_command("sync_stripe_billing", "--apply", stdout=io.StringIO())
        ws = self.reload()
        self.assertEqual(ws.plan, "free")
        self.assertEqual(ws.stripe_customer_id, "")
        self.assertEqual(ws.stripe_subscription_id, "")
        live_ws = Workspace.objects.get(pk=live_ws.pk)
        self.assertEqual(live_ws.plan, "scale")
        self.assertEqual(live_ws.stripe_subscription_id, "sub_L")


# ---------------------------------------------------------------------------
# Review follow-ups: local state heals on refusals, pending switches,
# wrong-mode keys, missing-customer cleanup, reconciliation safety.
# ---------------------------------------------------------------------------
class StaleStateTests(FixBillingBase):
    """A refused switch or checkout brings local state up to Stripe's, so
    the dashboard offers the control that will work (no 409 ping-pong)."""

    def setUp(self):
        super().setUp()
        self.session = self.patch_stripe(
            "session", return_value={"id": "cs_1", "url": "https://checkout.stripe.com/c/cs_1"}
        )
        self.modify = self.patch_stripe("modify")

    def status(self):
        return self.api.get("/api/v1/billing/status").json()

    def test_switch_on_lapsed_subscription_syncs_local_state(self):
        # Local state says team (a lost webhook); Stripe says unpaid.
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "unpaid", "price_team"))
        )
        resp = self.api.post("/api/v1/billing/switch", {"plan": "scale"}, format="json")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "no_subscription")
        self.modify.assert_not_called()
        body = self.status()
        self.assertEqual(body["plan"], "free")
        self.assertFalse(body["has_subscription"])
        self.assertEqual(body["subscription_status"], "unpaid")

        # The dashboard now offers checkout, which explains the unpaid
        # invoice instead of sending the user back to "switch plans".
        resp = self.api.post("/api/v1/billing/checkout", {"plan": "scale"}, format="json")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "already_subscribed")
        self.assertEqual(resp.json()["subscription_status"], "unpaid")
        self.assertIn("unpaid invoice", resp.json()["error"])
        self.assertIn("Manage billing", resp.json()["error"])
        self.session.assert_not_called()

    def test_checkout_409_syncs_lagging_local_state(self):
        # Back from Checkout before the webhook landed.
        self.set_billing("free", customer="cus_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_team"))
        )
        resp = self.api.post("/api/v1/billing/checkout", {"plan": "team"}, format="json")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["subscription_status"], "active")
        self.assertIn("Switch plans", resp.json()["error"])
        body = self.status()
        self.assertEqual(body["plan"], "team")
        self.assertTrue(body["has_subscription"])
        self.assertEqual(body["subscription_status"], "active")

    def test_checkout_message_follows_most_usable_status(self):
        self.set_billing("free", customer="cus_A")
        self.patch_stripe(
            "list",
            return_value=sub_list(
                make_sub("sub_old", "unpaid", "price_team", created=1),
                make_sub("sub_new", "past_due", "price_team", created=2, item_id="si_2"),
            ),
        )
        resp = self.api.post("/api/v1/billing/checkout", {"plan": "scale"}, format="json")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["subscription_status"], "past_due")
        self.assertIn("payment method", resp.json()["error"])

    def test_multiple_subscriptions_refusal_syncs_local_state(self):
        self.set_billing("free", customer="cus_A")
        self.patch_stripe(
            "list",
            return_value=sub_list(
                make_sub("sub_A", "active", "price_team", created=1),
                make_sub("sub_B", "active", "price_scale", created=2, item_id="si_2"),
            ),
        )
        with self.assertLogs("drop_billing", level="ERROR"):
            resp = self.api.post(
                "/api/v1/billing/switch", {"plan": "team"}, format="json"
            )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "multiple_subscriptions")
        ws = self.reload()
        self.assertEqual(ws.plan, "scale")
        self.assertEqual(ws.stripe_subscription_id, "sub_B")


class PendingSwitchTests(FixBillingBase):
    PENDING = {
        "expires_at": 1790000000,
        "subscription_items": [{"id": "si_1", "price": "price_scale"}],
    }

    def switch(self, plan):
        return self.api.post("/api/v1/billing/switch", {"plan": plan}, format="json")

    def test_402_links_the_invoice_page(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_team"))
        )
        updated = make_sub("sub_A", "active", "price_team", pending_update=self.PENDING)
        updated["latest_invoice"] = {
            "id": "in_pending",
            "object": "invoice",
            "status": "open",
            "hosted_invoice_url": "https://invoice.stripe.com/i/acct_1/in_pending",
        }
        modify = self.patch_stripe("modify", return_value=updated)
        resp = self.switch("scale")
        self.assertEqual(resp.status_code, 402)
        body = resp.json()
        self.assertEqual(body["code"], "payment_required")
        self.assertEqual(
            body["invoice_url"], "https://invoice.stripe.com/i/acct_1/in_pending"
        )
        self.assertIn("invoice page", body["error"])
        self.assertEqual(modify.call_args.kwargs["expand"], ["latest_invoice"])
        self.assertEqual(self.reload().plan, "team")

    def test_402_without_invoice_page_falls_back_to_portal_advice(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe(
            "list", return_value=sub_list(make_sub("sub_A", "active", "price_team"))
        )
        updated = make_sub("sub_A", "active", "price_team", pending_update=self.PENDING)
        updated["latest_invoice"] = "in_pending"  # not expanded
        self.patch_stripe("modify", return_value=updated)
        resp = self.switch("scale")
        self.assertEqual(resp.status_code, 402)
        self.assertIsNone(resp.json()["invoice_url"])
        self.assertIn("Manage billing", resp.json()["error"])

    def test_switching_back_cancels_the_pending_switch(self):
        self.set_billing("team", sub_id="sub_A")
        current = make_sub("sub_A", "active", "price_team", pending_update=self.PENDING)
        current["latest_invoice"] = "in_pending"
        self.patch_stripe("list", return_value=sub_list(current))
        modify = self.patch_stripe("modify")
        void = self.patch_stripe("void", return_value={"id": "in_pending", "status": "void"})
        resp = self.switch("team")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["status"], "pending_cancelled")
        self.assertEqual(resp.json()["plan"], "team")
        void.assert_called_once_with("in_pending")
        modify.assert_not_called()
        self.assertEqual(self.reload().plan, "team")

    def test_void_failure_is_json_502(self):
        self.set_billing("team", sub_id="sub_A")
        current = make_sub("sub_A", "active", "price_team", pending_update=self.PENDING)
        current["latest_invoice"] = "in_pending"
        self.patch_stripe("list", return_value=sub_list(current))
        self.patch_stripe(
            "void",
            side_effect=stripe.InvalidRequestError(
                "You can only pass in open invoices.", None
            ),
        )
        with self.assertLogs("drop_billing", level="ERROR"):
            resp = self.switch("team")
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["code"], "stripe_error")

    def test_no_pending_update_stays_a_plain_no_op(self):
        self.set_billing("team", sub_id="sub_A")
        current = make_sub("sub_A", "active", "price_team")
        current["latest_invoice"] = "in_paid"
        self.patch_stripe("list", return_value=sub_list(current))
        void = self.patch_stripe("void")
        resp = self.switch("team")
        self.assertEqual(resp.json()["status"], "unchanged")
        void.assert_not_called()


class StripeModeTests(FixBillingBase):
    """A wrong-mode secret key must never unlink or downgrade live customers."""

    def invoice_paid(self, customer="cus_LIVE"):
        return make_event(
            "invoice.paid", {"id": "in_1", "object": "invoice", "customer": customer}
        )

    def test_webhook_with_key_that_cannot_see_customer_changes_nothing(self):
        self.set_billing("scale", customer="cus_LIVE", sub_id="sub_L")
        self.patch_stripe("list", side_effect=missing_customer_error("cus_LIVE", hint=None))
        self.patch_stripe(
            "retrieve_customer", side_effect=missing_customer_error("cus_LIVE", hint=None)
        )
        event = self.invoice_paid()
        with self.assertLogs("drop_billing", level="ERROR") as logs, self.assertLogs(
            "django.request", level="ERROR"
        ):
            resp = self.post_event(event)
        self.assertEqual(resp.status_code, 500)
        self.assertTrue(any("cannot see it" in m for m in logs.output), logs.output)
        ws = self.reload()
        self.assertEqual(ws.plan, "scale")
        self.assertEqual(ws.stripe_customer_id, "cus_LIVE")
        self.assertEqual(ws.stripe_subscription_id, "sub_L")
        self.assertFalse(ProcessedStripeEvent.objects.filter(event_id=event["id"]).exists())

    def test_webhook_live_customer_seen_with_test_key_changes_nothing(self):
        self.set_billing("scale", customer="cus_LIVE", sub_id="sub_L")
        self.patch_stripe("list", side_effect=missing_customer_error("cus_LIVE", hint="live"))
        with self.assertLogs("drop_billing", level="ERROR"), self.assertLogs(
            "django.request", level="ERROR"
        ):
            resp = self.post_event(self.invoice_paid())
        self.assertEqual(resp.status_code, 500)
        ws = self.reload()
        self.assertEqual((ws.plan, ws.stripe_customer_id), ("scale", "cus_LIVE"))

    def test_webhook_for_deleted_customer_drops_to_free(self):
        self.set_billing("scale", customer="cus_GONE", sub_id="sub_G")
        self.patch_stripe("list", side_effect=missing_customer_error("cus_GONE", hint=None))
        self.patch_stripe(
            "retrieve_customer",
            return_value={"id": "cus_GONE", "object": "customer", "deleted": True},
        )
        resp = self.post_event(
            make_event(
                "customer.subscription.deleted",
                make_sub("sub_G", "canceled", "price_scale", customer="cus_GONE"),
            )
        )
        self.assertEqual(resp.status_code, 200)
        ws = self.reload()
        self.assertEqual(ws.plan, "free")
        self.assertEqual(ws.stripe_customer_id, "")
        mirror = Subscription.objects.get(workspace=ws)
        self.assertEqual((mirror.status, mirror.stripe_subscription_id), ("", ""))

    def test_portal_with_test_key_on_live_customer_keeps_link(self):
        self.set_billing("scale", customer="cus_LIVE", sub_id="sub_L")
        self.patch_stripe("portal", side_effect=missing_customer_error("cus_LIVE", hint="live"))
        with self.assertLogs("drop_billing", level="ERROR"):
            resp = self.api.post("/api/v1/billing/portal")
        self.assertEqual(resp.status_code, 502)
        ws = self.reload()
        self.assertEqual((ws.plan, ws.stripe_customer_id), ("scale", "cus_LIVE"))

    def test_checkout_with_test_key_on_live_customer_keeps_link(self):
        self.set_billing("free", customer="cus_LIVE")
        self.patch_stripe("list", side_effect=missing_customer_error("cus_LIVE", hint="live"))
        customer = self.patch_stripe("customer", return_value={"id": "cus_NEW"})
        resp = self.api.post("/api/v1/billing/checkout", {"plan": "team"}, format="json")
        self.assertEqual(resp.status_code, 502)
        customer.assert_not_called()
        self.assertEqual(self.reload().stripe_customer_id, "cus_LIVE")


class MissingCustomerCleanupTests(FixBillingBase):
    """Healing a test-mode leftover also drops its plan and resets the mirror,
    as sync_stripe_billing --apply does."""

    def assert_unlinked(self):
        ws = self.reload()
        self.assertEqual(ws.plan, "free")
        self.assertEqual((ws.stripe_customer_id, ws.stripe_subscription_id), ("", ""))
        mirror = Subscription.objects.get(workspace=ws)
        self.assertEqual(
            (mirror.status, mirror.stripe_customer_id, mirror.stripe_subscription_id),
            ("", "", ""),
        )
        self.assertIsNone(mirror.current_period_end)
        body = self.api.get("/api/v1/billing/status").json()
        self.assertEqual((body["plan"], body["subscription_status"]), ("free", ""))
        self.assertFalse(body["has_subscription"])

    def test_portal_heal(self):
        self.set_billing("scale", customer="cus_TESTMODE", sub_id="sub_t")
        self.patch_stripe("portal", side_effect=missing_customer_error())
        resp = self.api.post("/api/v1/billing/portal")
        self.assertEqual(resp.status_code, 400)
        self.assert_unlinked()

    def test_switch_heal(self):
        self.set_billing("team", customer="cus_TESTMODE", sub_id="sub_t")
        self.patch_stripe("list", side_effect=missing_customer_error())
        resp = self.api.post("/api/v1/billing/switch", {"plan": "scale"}, format="json")
        self.assertEqual(resp.status_code, 409)
        self.assert_unlinked()

    def test_checkout_replacement_drops_test_granted_plan(self):
        self.set_billing("team", customer="cus_TESTMODE", sub_id="sub_t")
        self.patch_stripe("list", side_effect=missing_customer_error())
        self.patch_stripe("customer", return_value={"id": "cus_NEW"})
        self.patch_stripe(
            "session", return_value={"id": "cs_1", "url": "https://checkout.stripe.com/c/cs_1"}
        )
        resp = self.api.post("/api/v1/billing/checkout", {"plan": "scale"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        ws = self.reload()
        self.assertEqual((ws.plan, ws.stripe_customer_id, ws.stripe_subscription_id), ("free", "cus_NEW", ""))
        self.assertEqual(Subscription.objects.get(workspace=ws).status, "")


class SyncCommandSafetyTests(FixBillingBase):
    def test_apply_refused_with_test_key(self):
        self.set_billing("team", customer="cus_LIVE", sub_id="sub_L")
        lister = self.patch_stripe("list", side_effect=missing_customer_error("cus_LIVE"))
        with self.assertRaisesMessage(CommandError, "test-mode STRIPE_SECRET_KEY"):
            call_command("sync_stripe_billing", "--apply", stdout=io.StringIO())
        lister.assert_not_called()
        ws = self.reload()
        self.assertEqual((ws.plan, ws.stripe_customer_id), ("team", "cus_LIVE"))

    def test_allow_test_key_overrides(self):
        self.set_billing("team", customer="cus_GONE", sub_id="sub_t")
        self.patch_stripe("list", side_effect=missing_customer_error("cus_GONE"))
        call_command(
            "sync_stripe_billing", "--apply", "--allow-test-key", stdout=io.StringIO()
        )
        self.assertEqual(self.reload().stripe_customer_id, "")

    def test_dry_run_prints_key_mode(self):
        self.patch_stripe("list", return_value=sub_list())
        out = io.StringIO()
        call_command("sync_stripe_billing", stdout=out)
        self.assertIn("Stripe key mode: test", out.getvalue())

    def test_live_customer_seen_with_test_key_is_not_cleared(self):
        self.set_billing("team", customer="cus_LIVE", sub_id="sub_L")
        self.patch_stripe("list", side_effect=missing_customer_error("cus_LIVE", hint="live"))
        out, err = io.StringIO(), io.StringIO()
        call_command(
            "sync_stripe_billing", "--apply", "--allow-test-key", stdout=out, stderr=err
        )
        self.assertIn("cus_LIVE", err.getvalue())
        ws = self.reload()
        self.assertEqual((ws.plan, ws.stripe_customer_id), ("team", "cus_LIVE"))

    def test_duplicates_and_orphans_listed(self):
        self.set_billing("team", customer="cus_A", sub_id="sub_A")
        orphan = make_sub(
            "sub_O",
            "active",
            "price_scale",
            customer="cus_ORPHAN",
            metadata={"workspace_id": "ws-hint"},
        )

        def fake_list(customer=None, **kwargs):
            if customer == "cus_A":
                return sub_list(
                    make_sub("sub_A", "active", "price_team", created=1),
                    make_sub("sub_A2", "active", "price_team", created=2, item_id="si_2"),
                )
            self.assertIsNone(customer)
            return stripe.ListObject.construct_from(
                {
                    "object": "list",
                    "url": "/v1/subscriptions",
                    "has_more": False,
                    "data": [
                        make_sub("sub_A", "active", "price_team"),
                        orphan,
                        make_sub("sub_X", "incomplete_expired", "price_team", customer="cus_X"),
                    ],
                },
                "sk_test_fixbilling",
            )

        self.patch_stripe("list", side_effect=fake_list)
        out = io.StringIO()
        with self.assertLogs("drop_billing", level="ERROR"):
            call_command("sync_stripe_billing", "--list-orphans", stdout=out)
        text = out.getvalue()
        self.assertIn("2 live subscriptions", text)
        self.assertIn("sub_O", text)
        self.assertIn("cus_ORPHAN", text)
        self.assertIn("ws-hint", text)
        self.assertNotIn("cus_X", text)
        self.assertIn("1 orphan subscription(s)", text)
