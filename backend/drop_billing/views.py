"""Billing API for Veilfile: Stripe checkout, plan switching, customer portal,
and webhooks.

Mount point (coordinator): ``api/v1/billing/``.

Billing runs in Stripe LIVE mode with Managed Payments (Stripe, via Link, is
the merchant of record) and tax-inclusive prices.

Environment:
    STRIPE_SECRET_KEY     - Stripe secret key (sk_live_... in production).
    STRIPE_WEBHOOK_SECRET - Webhook signing secret (whsec_...). While it is
                            unset the webhook refuses every event (503).
    STRIPE_PRICE_TEAM     - Price ID for the Team ($4/mo) subscription.
    STRIPE_PRICE_SCALE    - Price ID for the Scale ($12/mo) subscription.
    DROP_BASE_URL         - Public base URL used for Stripe redirect URLs.

Plan state is derived from Stripe, never from a single event: every webhook
re-reads the customer's subscriptions and maps them to a plan
(``_derive_billing_state``), so duplicated, stale or out-of-order deliveries
cannot grant or revoke a plan on their own.

The operations behind checkout, switch, portal and status are plain functions in ``account.py``; the views below
send their answers as JSON, and the dashboard calls the same functions. The webhook and the helpers it alone uses are
here.

No secret is ever committed; everything comes from the environment.
"""

import logging
import os

import stripe
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from drop_core.errors import answer_response
from drop_core.models import Workspace

from . import account

# The helpers and constants that moved to account.py and are still imported from here: by the webhook below, and by
# the sync_stripe_billing command.
from .account import (
    ENTITLED_STATUSES,
    LIVE_STATUSES,
    PAID_PLANS,
    _apply_subscriptions,
    _as_list,
    _derive_billing_state,
    _forget_missing_customer,
    _is_missing_customer,
    _list_subscriptions,
    _price_ids,
    _stripe_or_503,
    _subscription_plan,
)
from .models import ProcessedStripeEvent, Subscription

logger = logging.getLogger(__name__)

# checkout.session.completed only means "paid" for these payment statuses;
# delayed methods finish later with checkout.session.async_payment_succeeded.
PAID_CHECKOUT_STATUSES = ("paid", "no_payment_required")


def _workspace_for_customer(customer_id):
    """Best-effort workspace lookup from a Stripe customer id."""
    if not customer_id:
        return None
    sub = Subscription.objects.filter(stripe_customer_id=customer_id).first()
    if sub is not None:
        return sub.workspace
    return Workspace.objects.filter(stripe_customer_id=customer_id).first()


class CustomerNotVisible(Exception):
    """A verified webhook event names a customer that STRIPE_SECRET_KEY
    cannot see: the key and the webhook secret are from different Stripe
    modes or accounts. Nothing is changed; the webhook answers 500."""


def _customer_deleted(stripe_mod, customer_id):
    """True when Stripe reports the customer as deleted, as opposed to
    unknown to this key. Raises other StripeErrors."""
    try:
        customer = stripe_mod.Customer.retrieve(customer_id)
    except stripe_mod.error.StripeError as exc:
        if _is_missing_customer(exc):
            return False
        raise
    return bool(customer.get("deleted"))


def _sync_workspace(stripe_mod, workspace, event_customer=""):
    """Re-derive the workspace's plan from Stripe. Raises StripeError, or
    CustomerNotVisible when the event's own customer (``event_customer``)
    is the stored one and the key cannot see it.

    The row lock serializes concurrent webhooks for one workspace.
    """
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update().get(pk=workspace.pk)
        customer_id = workspace.stripe_customer_id
        subs = _list_subscriptions(stripe_mod, customer_id) if customer_id else []
        if subs is None:
            # A verified event about this very customer proves it exists in
            # the webhook's mode. Unless Stripe says it was deleted, the
            # secret key is from another mode or account: change nothing.
            if customer_id == event_customer and not _customer_deleted(
                stripe_mod, customer_id
            ):
                logger.error(
                    "billing webhook: customer %s of workspace %s is named by a "
                    "verified event but STRIPE_SECRET_KEY cannot see it; check "
                    "that STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET are from "
                    "the same Stripe account and mode. Plan left at %s.",
                    customer_id,
                    workspace.pk,
                    workspace.plan,
                )
                raise CustomerNotVisible(customer_id)
            return _forget_missing_customer(workspace, customer_id)
        _apply_subscriptions(workspace, subs, customer_id)
    return workspace


# ---------------------------------------------------------------------------
# Checkout, plan switch, portal and status: the JSON endpoints of account.py
# ---------------------------------------------------------------------------


class CheckoutView(APIView):
    """Create a Stripe Checkout session for a paid plan subscription.

    Only for workspaces without a subscription: existing subscribers get
    409 ``already_subscribed`` and change plans through SwitchPlanView or
    the customer portal.

    Session-only by explicit pin: this view drives Stripe customer
    operations off request.user, which has no meaning under API-key auth.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        plan = (request.data or {}).get("plan")
        return answer_response(account.checkout(request.user, plan))


def create_checkout_session_for_user(user, plan):
    """Create a Stripe Checkout session for user and plan.

    Returns (checkout_url, None) on success, or (None, error Response).
    Shared by the dashboard checkout API and the plan-aware signup flow.
    Refuses with 409 ``already_subscribed`` when Stripe already holds a live
    subscription for the workspace's customer.
    """
    answer = account.checkout(user, plan)
    if answer.status == 200:
        return answer.body["checkout_url"], None
    return None, answer_response(answer)


def paid_signup_redirect(user, plan):
    """Where the plan-aware signup flow sends a user who picked a paid plan.

    Stripe Checkout on success; the dashboard with ``billing=already_subscribed``
    for subscribers; otherwise the dashboard with ``billing=error`` (and an
    ERROR log line), where the user can retry.
    """
    url, err = create_checkout_session_for_user(user, plan)
    if url:
        return url
    data = err.data if isinstance(err.data, dict) else {}
    if data.get("code") == "already_subscribed":
        return "/app/?billing=already_subscribed"
    logger.error(
        "billing: paid signup checkout failed for user %s, plan %s: HTTP %s %s",
        user.pk,
        plan,
        err.status_code,
        data.get("error") or data,
    )
    return f"/app/?billing=error&plan={plan if plan in PAID_PLANS else ''}"


class SwitchPlanView(APIView):
    """Switch an existing subscription between Team and Scale in place.

    See ``account.switch`` for what it asks of Stripe.

    Session-only by explicit pin (see CheckoutView); DRF's
    SessionAuthentication enforces CSRF.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        plan = (request.data or {}).get("plan")
        return answer_response(account.switch(request.user, plan))


class PortalView(APIView):
    """Create a Stripe customer-portal session for managing the subscription.

    Session-only by explicit pin (see CheckoutView).
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return answer_response(account.portal(request.user))


class StatusView(APIView):
    """Current billing state for the dashboard (no Stripe call).

    Session-only by explicit pin (see CheckoutView).
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return answer_response(account.status(request.user))


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

CHECKOUT_EVENTS = (
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
)
SUBSCRIPTION_EVENTS = (
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.paused",
    "customer.subscription.resumed",
    "customer.subscription.pending_update_applied",
    "customer.subscription.pending_update_expired",
)
INVOICE_EVENTS = ("invoice.paid", "invoice.payment_failed")


def _customer_id(data):
    customer = data.get("customer")
    if isinstance(customer, dict):
        customer = customer.get("id")
    return customer or ""


def _event_subscription_id(data):
    """The subscription an event object refers to (for log lines)."""
    if data.get("object") == "subscription":
        return data.get("id") or ""
    sub = data.get("subscription")
    if not sub:
        # Invoices at 2025-03-31.basil keep it under parent.
        details = (data.get("parent") or {}).get("subscription_details") or {}
        sub = details.get("subscription")
    if isinstance(sub, dict):
        sub = sub.get("id")
    return sub or ""


def _workspace_for_event(data, customer_id):
    """The workspace an event belongs to: by customer id, else by the
    workspace reference set at checkout (client_reference_id or
    subscription metadata). A workspace found by reference adopts the
    customer when it has none."""
    workspace = _workspace_for_customer(customer_id)
    if workspace is not None:
        return workspace
    reference = data.get("client_reference_id") or (
        (data.get("metadata") or {}).get("workspace_id")
    )
    if not reference:
        return None
    try:
        workspace = Workspace.objects.filter(pk=reference).first()
    except (ValidationError, ValueError):
        return None
    if workspace is None or not customer_id:
        return None
    if workspace.stripe_customer_id and workspace.stripe_customer_id != customer_id:
        logger.error(
            "billing webhook: customer %s names workspace %s, which uses "
            "customer %s; reconcile by hand",
            customer_id,
            workspace.pk,
            workspace.stripe_customer_id,
        )
        return None
    workspace.stripe_customer_id = customer_id
    workspace.save(update_fields=["stripe_customer_id"])
    return workspace


def _handle_event(stripe_mod, event_type, data, event_id):
    """Re-sync the workspace an event concerns. Raises StripeError or
    CustomerNotVisible."""
    if event_type in CHECKOUT_EVENTS:
        if data.get("mode") not in (None, "", "subscription"):
            return
        if data.get("payment_status") not in PAID_CHECKOUT_STATUSES:
            logger.info(
                "billing webhook: %s %s for customer %s is not paid yet "
                "(payment_status=%s); the plan follows the subscription",
                event_type,
                event_id,
                _customer_id(data),
                data.get("payment_status"),
            )
            return
        paid = True
    elif event_type in SUBSCRIPTION_EVENTS:
        paid = data.get("status") in ENTITLED_STATUSES
    elif event_type in INVOICE_EVENTS:
        paid = event_type == "invoice.paid"
        if not paid:
            logger.warning(
                "billing webhook: payment failed for customer %s (invoice %s); "
                "Stripe will retry",
                _customer_id(data),
                data.get("id"),
            )
    else:
        return  # acknowledged, ignored

    customer_id = _customer_id(data)
    workspace = _workspace_for_event(data, customer_id)
    if workspace is None:
        log = logger.error if paid else logger.warning
        log(
            "billing webhook: %s %s for customer %s (subscription %s) maps to "
            "no workspace%s",
            event_type,
            event_id,
            customer_id or "?",
            _event_subscription_id(data) or "?",
            "; the customer may be paying without a plan" if paid else "",
        )
        return
    _sync_workspace(stripe_mod, workspace, event_customer=customer_id)


@csrf_exempt
def webhook(request):
    """Stripe webhook receiver.

    Fails closed: 503 while STRIPE_WEBHOOK_SECRET or STRIPE_SECRET_KEY is
    missing, 400 for an unverified payload. Verified events answer 200
    (repeats of an already processed event id are skipped), or 500 when
    Stripe could not be read back, or the secret key cannot see the event's
    customer, so that Stripe retries the delivery.
    """
    if request.method != "POST":
        return HttpResponse(status=405)

    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret.startswith("whsec_"):
        # An empty secret would verify events HMAC-signed with an empty key.
        logger.error(
            "billing webhook: STRIPE_WEBHOOK_SECRET is unset or not a whsec_ "
            "secret; refusing all events"
        )
        return HttpResponse("billing webhook not configured", status=503)

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as exc:  # signature mismatch, bad payload
        logger.warning("billing webhook: unverified event (%s)", exc)
        return HttpResponseBadRequest("invalid signature")

    event_id = event.get("id") or ""
    event_type = event.get("type", "")
    if event_id and ProcessedStripeEvent.objects.filter(event_id=event_id).exists():
        return HttpResponse(status=200)

    stripe_mod, err = _stripe_or_503()
    if err is not None:
        logger.error(
            "billing webhook: STRIPE_SECRET_KEY is unset; cannot process %s %s",
            event_type,
            event_id,
        )
        return HttpResponse("billing not configured", status=503)

    data = (event.get("data") or {}).get("object") or {}
    try:
        _handle_event(stripe_mod, event_type, data, event_id)
    except stripe_mod.error.StripeError as exc:
        logger.error(
            "billing webhook: Stripe error while handling %s %s (%s); answering "
            "500 so Stripe retries",
            event_type,
            event_id,
            exc,
        )
        return HttpResponse(status=500)
    except CustomerNotVisible:
        # Logged in _sync_workspace. Answer 500 so Stripe retries once the
        # keys are fixed; the plan is left as it was.
        return HttpResponse(status=500)

    if event_id:
        ProcessedStripeEvent.objects.get_or_create(
            event_id=event_id, defaults={"event_type": event_type[:128]}
        )
    return HttpResponse(status=200)
