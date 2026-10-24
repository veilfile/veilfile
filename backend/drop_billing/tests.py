"""Tests for drop_billing (checkout, portal, webhook, status).

Runnable standalone BEFORE Track A lands::

    ~/workspace/drop/venv/bin/python backend/drop_billing/tests.py

The bootstrap below stubs ``drop_core`` (models + plans) in memory only when the
real package is not importable, and configures Django settings in-process. When
the full project exists and these tests run under the project's test runner,
the stub and settings bootstrap are skipped and the real ``drop_core`` is used.

No network calls: the ``stripe`` module is mocked with unittest.mock.
"""

import datetime
import json
import os
import sys
import types
from unittest import mock

# ---------------------------------------------------------------------------
# 1. sys.path: backend dir (so drop_billing is importable)
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(HERE)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# ---------------------------------------------------------------------------
# 2. Stub drop_core when Track A has not landed yet (in-memory only)
# ---------------------------------------------------------------------------
try:
    import drop_core  # noqa: F401
    DROP_CORE_STUBBED = False
except ImportError:
    DROP_CORE_STUBBED = True

    drop_core = types.ModuleType("drop_core")
    drop_core.__path__ = [HERE]  # package marker so AppConfig can derive a path
    drop_core_models = types.ModuleType("drop_core.models")
    drop_core_plans = types.ModuleType("drop_core.plans")
    drop_core.models = drop_core_models
    drop_core.plans = drop_core_plans
    sys.modules["drop_core"] = drop_core
    sys.modules["drop_core.models"] = drop_core_models
    sys.modules["drop_core.plans"] = drop_core_plans

    # PLANS mirror the contract from the task description.
    drop_core_plans.PLANS = {
        "free": {"uploads": 100, "ttl_days": 7, "keys": 1, "price_usd": 0, "storage_gb": 1},
        "team": {"uploads": 2000, "ttl_days": 90, "keys": 5, "price_usd": 4, "storage_gb": 25},
        "scale": {
            "uploads": 10000,
            "ttl_days": 365,
            "keys": None,
            "price_usd": 12,
            "storage_gb": 100,
        },
    }

# ---------------------------------------------------------------------------
# 3. Django settings (only when not already configured)
# ---------------------------------------------------------------------------
from django.conf import settings  # noqa: E402

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY="veilfile-test-secret",
        ALLOWED_HOSTS=["*"],
        USE_TZ=True,
        TIME_ZONE="UTC",
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "rest_framework",
            "drop_core",
            "drop_billing",
        ],
        ROOT_URLCONF="drop_billing.tests",
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    import django

    django.setup()

# ---------------------------------------------------------------------------
# 4. Stub drop_core models (only when stubbed; registered post-setup)
# ---------------------------------------------------------------------------
if DROP_CORE_STUBBED:
    from django.conf import settings as dj_settings
    from django.db import models as dj_models

    class Workspace(dj_models.Model):
        owner = dj_models.OneToOneField(
            dj_settings.AUTH_USER_MODEL,
            on_delete=dj_models.CASCADE,
            related_name="workspace",
        )
        plan = dj_models.CharField(max_length=16, default="free")
        stripe_customer_id = dj_models.CharField(
            max_length=128, blank=True, default=""
        )
        stripe_subscription_id = dj_models.CharField(
            max_length=128, blank=True, default=""
        )

        class Meta:
            app_label = "drop_core"

    class APIKey(dj_models.Model):
        workspace = dj_models.ForeignKey(
            Workspace, on_delete=dj_models.CASCADE, related_name="api_keys"
        )
        name = dj_models.CharField(max_length=64, default="")

        class Meta:
            app_label = "drop_core"

    class Artifact(dj_models.Model):
        workspace = dj_models.ForeignKey(
            Workspace, on_delete=dj_models.CASCADE, related_name="artifacts"
        )
        filename = dj_models.CharField(max_length=255, default="")

        class Meta:
            app_label = "drop_core"

    def get_or_create_workspace(user):
        ws, _ = Workspace.objects.get_or_create(owner=user)
        return ws

    sys.modules["drop_core.models"].Workspace = Workspace
    sys.modules["drop_core.models"].APIKey = APIKey
    sys.modules["drop_core.models"].Artifact = Artifact
    sys.modules["drop_core.models"].get_or_create_workspace = get_or_create_workspace

# ---------------------------------------------------------------------------
# 5. URLconf for the standalone run (coordinator mounts at api/v1/billing/)
# ---------------------------------------------------------------------------
from django.urls import include, path  # noqa: E402

urlpatterns = [
    path("api/v1/billing/", include("drop_billing.urls")),
]

# ---------------------------------------------------------------------------
# 6. The tests
# ---------------------------------------------------------------------------
from django.contrib.auth import get_user_model  # noqa: E402
from django.test import TestCase  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from drop_billing.models import Subscription  # noqa: E402
from drop_core.models import get_or_create_workspace  # noqa: E402

TEST_ENV = {
    "STRIPE_SECRET_KEY": "sk_test_111",
    "STRIPE_WEBHOOK_SECRET": "whsec_111",
    "STRIPE_PRICE_TEAM": "price_team_111",
    "STRIPE_PRICE_SCALE": "price_scale_111",
    "DROP_BASE_URL": "https://veilfile.test",
}


def make_stripe_mock():
    stripe_mock = mock.MagicMock(name="stripe")
    stripe_mock.Customer.create.return_value = {"id": "cus_test_123"}
    stripe_mock.checkout.Session.create.return_value = {
        "id": "cs_test_123",
        "url": "https://checkout.stripe.com/pay/cs_test_123",
    }
    stripe_mock.billing_portal.Session.create.return_value = {
        "url": "https://billing.stripe.com/session/bps_test_123"
    }
    return stripe_mock


class BillingApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="dev", email="dev@example.com", password="x"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.env = mock.patch.dict(os.environ, TEST_ENV, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    # -- checkout ---------------------------------------------------------
    def test_checkout_creates_customer_and_session(self):
        stripe_mock = make_stripe_mock()
        with mock.patch("drop_billing.views.stripe", stripe_mock):
            resp = self.client.post(
                "/api/v1/billing/checkout", {"plan": "team"}, format="json"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json()["checkout_url"],
            "https://checkout.stripe.com/pay/cs_test_123",
        )
        stripe_mock.Customer.create.assert_called_once_with(
            email="dev@example.com"
        )
        _, kwargs = stripe_mock.checkout.Session.create.call_args
        self.assertEqual(kwargs["customer"], "cus_test_123")
        self.assertEqual(kwargs["mode"], "subscription")
        self.assertEqual(
            kwargs["line_items"], [{"price": "price_team_111", "quantity": 1}]
        )
        self.assertEqual(kwargs["metadata"], {"plan": "team"})
        self.assertEqual(
            kwargs["success_url"], "https://veilfile.test/app/?billing=success"
        )
        self.assertEqual(
            kwargs["cancel_url"], "https://veilfile.test/app/?billing=cancelled"
        )
        ws = get_or_create_workspace(self.user)
        self.assertEqual(ws.stripe_customer_id, "cus_test_123")

    def test_checkout_reuses_existing_customer(self):
        ws = get_or_create_workspace(self.user)
        ws.stripe_customer_id = "cus_existing"
        ws.save()
        stripe_mock = make_stripe_mock()
        with mock.patch("drop_billing.views.stripe", stripe_mock):
            resp = self.client.post(
                "/api/v1/billing/checkout", {"plan": "scale"}, format="json"
            )
        self.assertEqual(resp.status_code, 200)
        stripe_mock.Customer.create.assert_not_called()
        _, kwargs = stripe_mock.checkout.Session.create.call_args
        self.assertEqual(kwargs["customer"], "cus_existing")
        self.assertEqual(
            kwargs["line_items"], [{"price": "price_scale_111", "quantity": 1}]
        )
        self.assertEqual(kwargs["metadata"], {"plan": "scale"})

    def test_checkout_enables_managed_payments(self):
        stripe_mock = make_stripe_mock()
        with mock.patch("drop_billing.views.stripe", stripe_mock):
            resp = self.client.post(
                "/api/v1/billing/checkout", {"plan": "team"}, format="json"
            )
        self.assertEqual(resp.status_code, 200)
        _, kwargs = stripe_mock.checkout.Session.create.call_args
        self.assertEqual(kwargs["managed_payments"], {"enabled": True})

    def test_checkout_pins_api_version_for_managed_payments(self):
        stripe_mock = make_stripe_mock()
        with mock.patch("drop_billing.views.stripe", stripe_mock):
            resp = self.client.post(
                "/api/v1/billing/checkout", {"plan": "team"}, format="json"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(stripe_mock.api_version, "2025-03-31.basil")

    def test_checkout_stripe_error_returns_readable_502(self):
        class FakeStripeError(Exception):
            pass

        stripe_mock = make_stripe_mock()
        stripe_mock.error.StripeError = FakeStripeError
        stripe_mock.checkout.Session.create.side_effect = FakeStripeError(
            "Invalid line_items[0]: the product tax code is missing."
        )
        with mock.patch("drop_billing.views.stripe", stripe_mock):
            resp = self.client.post(
                "/api/v1/billing/checkout", {"plan": "team"}, format="json"
            )
        self.assertEqual(resp.status_code, 502)
        body = resp.json()
        self.assertIn("tax code is missing", body["error"])
        self.assertTrue(body["error"].startswith("Stripe:"))

    def test_checkout_invalid_plan(self):
        with mock.patch("drop_billing.views.stripe", make_stripe_mock()):
            resp = self.client.post(
                "/api/v1/billing/checkout", {"plan": "enterprise"}, format="json"
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "invalid_plan")

    def test_checkout_requires_auth(self):
        anon = APIClient()
        resp = anon.post("/api/v1/billing/checkout", {"plan": "team"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_checkout_missing_secret_key_503(self):
        with mock.patch.dict(os.environ, TEST_ENV):
            os.environ.pop("STRIPE_SECRET_KEY", None)
            with mock.patch("drop_billing.views.stripe", make_stripe_mock()):
                resp = self.client.post(
                    "/api/v1/billing/checkout", {"plan": "team"}, format="json"
                )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error"], "billing_not_configured")

    def test_checkout_missing_price_id_503(self):
        with mock.patch.dict(os.environ, TEST_ENV):
            os.environ.pop("STRIPE_PRICE_TEAM", None)
            with mock.patch("drop_billing.views.stripe", make_stripe_mock()):
                resp = self.client.post(
                    "/api/v1/billing/checkout", {"plan": "team"}, format="json"
                )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error"], "billing_not_configured")

    # -- portal -----------------------------------------------------------
    def test_portal_returns_url(self):
        ws = get_or_create_workspace(self.user)
        ws.stripe_customer_id = "cus_test_123"
        ws.save()
        stripe_mock = make_stripe_mock()
        with mock.patch("drop_billing.views.stripe", stripe_mock):
            resp = self.client.post("/api/v1/billing/portal")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json()["portal_url"],
            "https://billing.stripe.com/session/bps_test_123",
        )
        _, kwargs = stripe_mock.billing_portal.Session.create.call_args
        self.assertEqual(kwargs["customer"], "cus_test_123")
        self.assertEqual(kwargs["return_url"], "https://veilfile.test/app/")

    def test_portal_no_billing_account_400(self):
        with mock.patch("drop_billing.views.stripe", make_stripe_mock()):
            resp = self.client.post("/api/v1/billing/portal")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "no_billing_account")

    def test_portal_missing_secret_key_503(self):
        with mock.patch.dict(os.environ, TEST_ENV):
            os.environ.pop("STRIPE_SECRET_KEY", None)
            with mock.patch("drop_billing.views.stripe", make_stripe_mock()):
                resp = self.client.post("/api/v1/billing/portal")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error"], "billing_not_configured")

    # -- status -----------------------------------------------------------
    def test_status_free_plan(self):
        resp = self.client.get("/api/v1/billing/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["plan"], "free")
        self.assertEqual(body["caps"]["uploads_per_month"], 100)
        self.assertEqual(body["caps"]["ttl_days"], 7)
        self.assertEqual(body["caps"]["keys"], 1)
        self.assertFalse(body["has_billing_account"])

    def test_status_reflects_team_plan(self):
        ws = get_or_create_workspace(self.user)
        ws.plan = "team"
        ws.stripe_customer_id = "cus_test_123"
        ws.save()
        Subscription.objects.create(
            workspace=ws,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_123",
            status="active",
        )
        resp = self.client.get("/api/v1/billing/status")
        body = resp.json()
        self.assertEqual(body["plan"], "team")
        self.assertEqual(body["caps"]["uploads_per_month"], 2000)
        self.assertEqual(body["caps"]["storage_gb"], 25)
        self.assertEqual(body["storage_used_bytes"], 0)
        self.assertEqual(body["price_usd"], 4)
        self.assertTrue(body["has_billing_account"])
        self.assertEqual(body["subscription_status"], "active")

    # -- webhook ----------------------------------------------------------
    # The webhook re-reads the customer's subscriptions from Stripe on every
    # event, so each test sets what Stripe.Subscription.list reports. Fully
    # signed, Stripe-shaped payloads live in tests_fix_billing.py.
    def _post_webhook(self, event, subscriptions=()):
        with mock.patch(
            "drop_billing.views.stripe"
        ) as stripe_mock:
            stripe_mock.Webhook.construct_event.return_value = event
            stripe_mock.Subscription.list.return_value = {
                "object": "list",
                "data": list(subscriptions),
            }
            resp = self.client.post(
                "/api/v1/billing/webhook",
                data=json.dumps({"id": "evt_1"}),
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=abc",
            )
        return resp, stripe_mock

    @staticmethod
    def _subscription(sub_id, status, price, period_end=None):
        item = {"id": "si_1", "price": {"id": price}}
        if period_end:
            item["current_period_end"] = period_end
        return {
            "id": sub_id,
            "object": "subscription",
            "customer": "cus_test_123",
            "status": status,
            "items": {"object": "list", "data": [item]},
        }

    def test_webhook_checkout_completed_maps_metadata_plan(self):
        ws = get_or_create_workspace(self.user)
        ws.stripe_customer_id = "cus_test_123"
        ws.save()
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "object": "checkout.session",
                    "mode": "subscription",
                    "payment_status": "paid",
                    "customer": "cus_test_123",
                    "subscription": "sub_test_123",
                    "metadata": {"plan": "team"},
                }
            },
        }
        resp, stripe_mock = self._post_webhook(
            event,
            [self._subscription("sub_test_123", "active", "price_team_111")],
        )
        self.assertEqual(resp.status_code, 200)
        stripe_mock.Webhook.construct_event.assert_called_once()
        ws.refresh_from_db()
        self.assertEqual(ws.plan, "team")
        self.assertEqual(ws.stripe_subscription_id, "sub_test_123")
        sub = Subscription.objects.get(workspace=ws)
        self.assertEqual(sub.status, "active")
        self.assertEqual(sub.stripe_subscription_id, "sub_test_123")
        self.assertEqual(sub.stripe_customer_id, "cus_test_123")

    def test_webhook_checkout_completed_maps_subscription_price(self):
        # The plan comes from the subscription's price as Stripe reports
        # it, not from session metadata (line_items are never in webhooks).
        ws = get_or_create_workspace(self.user)
        ws.stripe_customer_id = "cus_test_123"
        ws.save()
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "object": "checkout.session",
                    "mode": "subscription",
                    "payment_status": "paid",
                    "customer": "cus_test_123",
                    "subscription": "sub_test_456",
                    "metadata": {},
                }
            },
        }
        resp, _ = self._post_webhook(
            event,
            [self._subscription("sub_test_456", "active", "price_scale_111")],
        )
        self.assertEqual(resp.status_code, 200)
        ws.refresh_from_db()
        self.assertEqual(ws.plan, "scale")

    def test_webhook_subscription_updated(self):
        ws = get_or_create_workspace(self.user)
        ws.plan = "team"
        ws.stripe_customer_id = "cus_test_123"
        ws.stripe_subscription_id = "sub_test_123"
        ws.save()
        Subscription.objects.create(
            workspace=ws,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
        )
        past_due = self._subscription(
            "sub_test_123", "past_due", "price_team_111", period_end=1790000000
        )
        event = {
            "type": "customer.subscription.updated",
            "data": {"object": past_due},
        }
        resp, _ = self._post_webhook(event, [past_due])
        self.assertEqual(resp.status_code, 200)
        sub = Subscription.objects.get(workspace=ws)
        self.assertEqual(sub.status, "past_due")
        self.assertIsNotNone(sub.current_period_end)
        ws.refresh_from_db()
        self.assertEqual(ws.plan, "team")  # past_due keeps the plan while Stripe retries

    def test_webhook_subscription_deleted_downgrades_to_free(self):
        ws = get_or_create_workspace(self.user)
        ws.plan = "team"
        ws.stripe_customer_id = "cus_test_123"
        ws.stripe_subscription_id = "sub_test_123"
        ws.save()
        Subscription.objects.create(
            workspace=ws,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
            status="active",
        )
        canceled = self._subscription("sub_test_123", "canceled", "price_team_111")
        event = {
            "type": "customer.subscription.deleted",
            "data": {"object": canceled},
        }
        resp, _ = self._post_webhook(event, [canceled])
        self.assertEqual(resp.status_code, 200)
        ws.refresh_from_db()
        self.assertEqual(ws.plan, "free")
        sub = Subscription.objects.get(workspace=ws)
        self.assertEqual(sub.status, "canceled")

    def test_webhook_unrelated_event_acknowledged(self):
        resp, _ = self._post_webhook(
            {"type": "invoice.paid", "data": {"object": {}}}
        )
        self.assertEqual(resp.status_code, 200)

    def test_webhook_bad_signature_400(self):
        import stripe as real_stripe

        with mock.patch(
            "drop_billing.views.stripe"
        ) as stripe_mock:
            stripe_mock.Webhook.construct_event.side_effect = (
                real_stripe.SignatureVerificationError("bad sig", "sig")
            )
            resp = self.client.post(
                "/api/v1/billing/webhook",
                data="{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=bogus",
            )
        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# 7. Standalone runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from django.test.runner import DiscoverRunner

    runner = DiscoverRunner(verbosity=2, interactive=False)
    failures = runner.run_tests(["drop_billing"])
    print(
        "drop_core stubbed:", DROP_CORE_STUBBED,
        "| failures:", failures,
    )
    sys.exit(bool(failures))
