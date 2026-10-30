"""Billing as plain functions: the plan and its limits, Stripe Checkout, a plan switch and the customer portal.

Each function answers what the JSON endpoint of the same name answers, as an ``Answer`` (``drop_core.account``): the
HTTP status, the body and any headers. The REST views in ``views.py`` send an ``Answer`` as a ``Response``, and the
dashboard's views call the same functions, so Stripe is spoken to by one set of code: the same locks, the same
calls, the same words. Nothing here reads a request or imports Django REST framework. A function takes the
signed-in ``user`` and works on that user's own workspace.

Plan state changes only from Stripe-verified state (AGENTS.md, invariant 3): ``checkout``, ``switch`` and ``portal``
never set a plan. They read Stripe, and write back only what Stripe reported (``_apply_subscriptions``).

Environment (read when a function runs, never at import): STRIPE_SECRET_KEY, STRIPE_PRICE_TEAM, STRIPE_PRICE_TEAMPLUS,
STRIPE_PRICE_SCALE and DROP_BASE_URL. ``views.py`` holds the webhook, which uses the helpers below.
"""

import datetime
import logging
import os
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from drop_core.account import Answer
from drop_core.key_caps import active_key_count
from drop_core.models import Artifact, Workspace, get_or_create_workspace
from drop_core.plans import PLANS
from drop_core.services import workspace_storage_bytes

from .models import Subscription

logger = logging.getLogger(__name__)

# Paid plan ids the API accepts. 'free' is never purchased through checkout.
PAID_PLANS = ("team", "team_plus", "scale")
PLAN_RANK = {"team": 1, "team_plus": 2, "scale": 3}

# Subscription statuses that keep the paid plan. past_due stays entitled
# while Stripe retries the payment; unpaid, incomplete_expired, paused and
# canceled drop the workspace to free.
ENTITLED_STATUSES = ("active", "trialing", "past_due")
# Statuses of a subscription that can still bill or be revived. A new
# Checkout next to one of these would create a second, concurrent
# subscription (double billing), so checkout refuses.
LIVE_STATUSES = ENTITLED_STATUSES + ("unpaid", "incomplete", "paused")


def _base_url():
    return os.environ.get("DROP_BASE_URL", "http://localhost:8000").rstrip("/")


def _stripe_library():
    """The ``stripe`` module. It is read from ``drop_billing.views`` each time it is needed: that module attribute
    is the one place the tests put their mock (``mock.patch("drop_billing.views.stripe", ...)``), and the webhook
    reads the same name, so every caller sees the same library."""
    from . import views

    return views.stripe


def _stripe_or_503():
    """Return (stripe_module, None) or (None, 503 Answer) when unconfigured."""
    secret = os.environ.get("STRIPE_SECRET_KEY")
    if not secret:
        return None, Answer(503, {"error": "billing_not_configured"})
    stripe_mod = _stripe_library()
    stripe_mod.api_key = secret
    # Managed Payments requires API version 2025-03-31.basil or newer; the
    # pinned stripe library defaults to an older one, so pin it explicitly.
    stripe_mod.api_version = "2025-03-31.basil"
    return stripe_mod, None


def _price_id(plan):
    if plan == "team":
        return os.environ.get("STRIPE_PRICE_TEAM")
    if plan == "team_plus":
        # The founder's name for it (2026-10-16): no underscore between TEAM and PLUS, whatever the slug suggests.
        return os.environ.get("STRIPE_PRICE_TEAMPLUS")
    if plan == "scale":
        return os.environ.get("STRIPE_PRICE_SCALE")
    return None


def _plan_for_price_id(price_id):
    """Reverse-map a Stripe price id to a Veilfile plan, or None."""
    if not price_id:
        return None
    if price_id == os.environ.get("STRIPE_PRICE_TEAM"):
        return "team"
    if price_id == os.environ.get("STRIPE_PRICE_TEAMPLUS"):
        return "team_plus"
    if price_id == os.environ.get("STRIPE_PRICE_SCALE"):
        return "scale"
    return None


def _error(code, message, http_status):
    """The API error envelope: {"error": <human message>, "code": <code>}."""
    return Answer(http_status, {"error": message, "code": code})


def _stripe_error(exc):
    """Turn a Stripe API failure into a readable 502 instead of a bare 500."""
    message = str(exc) or "Stripe request failed"
    if len(message) > 280:
        message = message[:277] + "…"
    return Answer(502, {"error": f"Stripe: {message}", "code": "stripe_error"})


def _no_subscription():
    return _error(
        "no_subscription",
        "This workspace has no active subscription to change. Subscribe first "
        "with POST /api/v1/billing/checkout.",
        409,
    )


def _is_missing_customer(exc):
    """True when Stripe says the customer does not exist in this mode, e.g. a
    test-mode ``cus_`` id stored before billing went live.

    A live-mode customer looked up with a test-mode key is not "missing":
    that is a misconfigured key, and treating it as a leftover would unlink
    paying customers. Stripe names that case in the message.
    """
    if getattr(exc, "code", None) != "resource_missing":
        return False
    message = str(exc).lower()
    if "exists in live mode" in message:
        return False
    return getattr(exc, "param", None) == "customer" or (
        "no such customer" in message
    )


def _forget_missing_customer(workspace, customer_id):
    """Unlink a customer Stripe reports missing in this mode (a test-mode
    leftover, or a customer deleted in Stripe), as ``sync_stripe_billing
    --apply`` does: clear the customer and subscription ids, reset the
    Subscription mirror, and drop a paid plan to free, since no
    subscription can back it any more. Returns the locked workspace."""
    with transaction.atomic():
        locked = Workspace.objects.select_for_update().get(pk=workspace.pk)
        if locked.stripe_customer_id != customer_id:
            return locked  # replaced meanwhile, e.g. by a concurrent checkout
        logger.warning(
            "billing: clearing customer %s of workspace %s (plan %s -> free): "
            "missing in this Stripe mode (test-mode leftover or deleted)",
            customer_id,
            locked.pk,
            locked.plan,
        )
        locked.stripe_customer_id = ""
        locked.stripe_subscription_id = ""
        locked.plan = "free"
        locked.save(
            update_fields=["plan", "stripe_customer_id", "stripe_subscription_id"]
        )
        Subscription.objects.filter(workspace=locked).update(
            stripe_customer_id="",
            stripe_subscription_id="",
            status="",
            current_period_end=None,
        )
    workspace.plan = locked.plan
    workspace.stripe_customer_id = ""
    workspace.stripe_subscription_id = ""
    return locked


# ---------------------------------------------------------------------------
# Reading Stripe subscriptions
# ---------------------------------------------------------------------------


def _as_list(result):
    """The ``data`` of a Stripe list object (StripeObjects are dicts)."""
    if isinstance(result, dict):
        return list(result.get("data") or [])
    return list(getattr(result, "data", None) or [])


def _list_subscriptions(stripe_mod, customer_id):
    """Every subscription Stripe holds for the customer, in any status.

    Returns None when the customer does not exist in this Stripe mode.
    Other Stripe failures propagate.
    """
    try:
        result = stripe_mod.Subscription.list(
            customer=customer_id, status="all", limit=100
        )
    except stripe_mod.error.StripeError as exc:
        if _is_missing_customer(exc):
            return None
        raise
    return _as_list(result)


def _subscription_items(sub):
    return list(((sub.get("items") or {}).get("data")) or [])


def _item_price_id(item):
    price = item.get("price") or {}
    return price.get("id") if isinstance(price, dict) else price


def _price_ids(sub):
    return [p for p in (_item_price_id(i) for i in _subscription_items(sub)) if p]


def _subscription_plan(sub):
    for price_id in _price_ids(sub):
        plan = _plan_for_price_id(price_id)
        if plan:
            return plan
    return None


def _plan_item(sub):
    """The subscription item that carries the plan price."""
    items = _subscription_items(sub)
    for item in items:
        if _plan_for_price_id(_item_price_id(item)):
            return item
    return items[0] if items else None


def _period_end(sub):
    """Current period end as an aware datetime, or None.

    API version 2025-03-31.basil moved current_period_end from the
    subscription onto its items; read both shapes.
    """
    value = sub.get("current_period_end")
    if not value:
        ends = [
            item.get("current_period_end")
            for item in _subscription_items(sub)
            if item.get("current_period_end")
        ]
        value = min(ends) if ends else None
    if not value:
        return None
    try:
        return datetime.datetime.fromtimestamp(int(value), tz=datetime.timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _derive_billing_state(workspace, subs, customer_id):
    """Map the customer's subscriptions, as Stripe reports them now, to
    ``(plan, subscription_id, mirror)``.

    The plan comes from the highest-ranked entitled subscription (active,
    trialing or past_due) whose price maps to a plan; the workspace's current
    subscription wins ties. Nothing entitled means free. An entitled
    subscription on an unknown price leaves the plan unchanged and logs an
    error: the customer is paying and we cannot tell for what. ``mirror`` is
    the subscription the local Subscription row should reflect.
    """
    current_id = workspace.stripe_subscription_id
    entitled = [s for s in subs if s.get("status") in ENTITLED_STATUSES]
    if len(entitled) > 1:
        logger.error(
            "billing: workspace %s has %d live subscriptions on customer %s "
            "(%s); keep one and refund the others",
            workspace.pk,
            len(entitled),
            customer_id,
            ", ".join(s.get("id") or "?" for s in entitled),
        )
    mapped = []
    for sub in entitled:
        plan = _subscription_plan(sub)
        if plan is None:
            logger.error(
                "billing: subscription %s (customer %s, workspace %s, status %s) "
                "is on price %s, which maps to no plan; check STRIPE_PRICE_TEAM, "
                "STRIPE_PRICE_TEAMPLUS and STRIPE_PRICE_SCALE. Plan left unchanged.",
                sub.get("id"),
                customer_id,
                workspace.pk,
                sub.get("status"),
                ", ".join(_price_ids(sub)) or "(none)",
            )
        else:
            mapped.append((sub, plan))
    if mapped:
        sub, plan = max(
            mapped,
            key=lambda pair: (
                PLAN_RANK[pair[1]],
                pair[0].get("id") == current_id,
                pair[0].get("created") or 0,
            ),
        )
        return plan, sub.get("id") or "", sub
    if entitled:
        sub = next((s for s in entitled if s.get("id") == current_id), entitled[0])
        return workspace.plan, sub.get("id") or "", sub
    previous = next((s for s in subs if s.get("id") == current_id), None)
    if previous is None and subs:
        previous = max(subs, key=lambda s: s.get("created") or 0)
    return "free", "", previous


def _apply_subscriptions(workspace, subs, customer_id):
    """Persist what ``_derive_billing_state`` computes. Returns the plan.

    Only the plan changes: keys and artifacts are never revoked or deleted
    on a downgrade (see StatusView's over_plan_limits).
    """
    plan, sub_id, mirror = _derive_billing_state(workspace, subs, customer_id)
    changed = []
    if workspace.plan != plan:
        logger.info(
            "billing: workspace %s plan %s -> %s (subscription %s)",
            workspace.pk,
            workspace.plan,
            plan,
            sub_id or (mirror or {}).get("id") or "-",
        )
        workspace.plan = plan
        changed.append("plan")
    if workspace.stripe_subscription_id != sub_id:
        workspace.stripe_subscription_id = sub_id
        changed.append("stripe_subscription_id")
    if changed:
        workspace.save(update_fields=changed)
    if mirror is not None:
        Subscription.objects.update_or_create(
            workspace=workspace,
            defaults={
                "stripe_customer_id": customer_id or "",
                "stripe_subscription_id": mirror.get("id") or "",
                "status": mirror.get("status") or "",
                "current_period_end": _period_end(mirror),
            },
        )
    return plan


def _sync_from_subscriptions(workspace, subs, customer_id):
    """Apply an already fetched subscription list under the row lock,
    unless the workspace has moved to another customer meanwhile."""
    with transaction.atomic():
        locked = Workspace.objects.select_for_update().get(pk=workspace.pk)
        if locked.stripe_customer_id == customer_id:
            _apply_subscriptions(locked, subs, customer_id)
    return locked


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------

# What a refused checkout tells the user, by the status of the subscription
# that blocks it. Only active and trialing subscriptions can be switched; the
# others need Manage billing (the customer portal).
ALREADY_SUBSCRIBED_MESSAGES = {
    "active": (
        "This workspace already has a subscription. Switch plans from the "
        "dashboard instead of starting a new checkout."
    ),
    "past_due": (
        "This workspace's subscription has a failed payment that Stripe is "
        "retrying. Update your payment method under Manage billing instead "
        "of starting a new checkout."
    ),
    "unpaid": (
        "This workspace's subscription has an unpaid invoice. Pay it, or "
        "cancel the subscription, under Manage billing before starting a "
        "new checkout."
    ),
    "incomplete": (
        "This workspace's subscription is still waiting for its first "
        "payment. Complete it under Manage billing, or try again after it "
        "expires (within a day)."
    ),
    "paused": (
        "This workspace's subscription is paused. Resume or cancel it under "
        "Manage billing, or contact support, before starting a new checkout."
    ),
}
ALREADY_SUBSCRIBED_MESSAGES["trialing"] = ALREADY_SUBSCRIBED_MESSAGES["active"]


def _already_subscribed(live_subs):
    """409 for a checkout blocked by ``live_subs``, worded for the most
    usable of their statuses (active before past_due before unpaid ...)."""
    statuses = {s.get("status") for s in live_subs}
    live_status = next(s for s in LIVE_STATUSES if s in statuses)
    return Answer(
        409,
        {
            "error": ALREADY_SUBSCRIBED_MESSAGES[live_status],
            "code": "already_subscribed",
            "subscription_status": live_status,
        },
    )


def _ensure_customer(stripe_mod, workspace, user, replace=""):
    """Return the workspace's Stripe customer id, creating it on first use.

    Runs under a row lock so a double click cannot create two customers:
    the second request waits, then reuses the first one's id. ``replace``
    is a stored id Stripe reported missing (a test-mode leftover); it is
    unlinked like any missing customer (``_forget_missing_customer``) and
    swapped for a fresh one.
    """
    with transaction.atomic():
        locked = Workspace.objects.select_for_update().get(pk=workspace.pk)
        if locked.stripe_customer_id and locked.stripe_customer_id != replace:
            workspace.stripe_customer_id = locked.stripe_customer_id
            return locked.stripe_customer_id
        if replace:
            locked = _forget_missing_customer(locked, replace)
        customer = stripe_mod.Customer.create(email=user.email or None)
        locked.stripe_customer_id = customer["id"]
        locked.save(update_fields=["stripe_customer_id"])
    workspace.plan = locked.plan
    workspace.stripe_customer_id = locked.stripe_customer_id
    workspace.stripe_subscription_id = locked.stripe_subscription_id
    return locked.stripe_customer_id


def _create_checkout_session(stripe_mod, workspace, customer_id, plan, price_id):
    return stripe_mod.checkout.Session.create(
        customer=customer_id,
        # Lets the webhook find the workspace even if the customer id on
        # the event is not the one stored on the workspace.
        client_reference_id=str(workspace.pk),
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        # Stripe Managed Payments: Stripe acts as the Merchant of
        # Record (handles global tax calculation/collection as the
        # legal seller). Requires product tax codes set on the Stripe
        # dashboard and tax-inclusive Prices (tax_behavior=inclusive).
        managed_payments={"enabled": True},
        metadata={"plan": plan},
        subscription_data={"metadata": {"workspace_id": str(workspace.pk)}},
        success_url=_base_url() + "/app/?billing=success",
        cancel_url=_base_url() + "/app/?billing=cancelled",
    )


def _over_plan_limits(workspace, caps, storage_used, active_keys):
    """Which limits of the current plan the workspace exceeds, typically
    after a downgrade. Nothing is revoked or deleted: new keys are refused
    while over the key cap, new uploads while over the storage cap, and new
    uploads get at most the plan's TTL. Existing links keep their expiry."""
    over = []
    if caps["keys"] is not None and active_keys > caps["keys"]:
        over.append("keys")
    if storage_used > caps["storage_gb"] * 1024**3:
        over.append("storage")
    ttl_horizon = timezone.now() + timedelta(days=caps["ttl_days"])
    if Artifact.objects.filter(workspace=workspace, expires_at__gt=ttl_horizon).exists():
        over.append("ttl")
    return over


# ---------------------------------------------------------------------------
# The four operations
# ---------------------------------------------------------------------------


def status(user):
    """GET /api/v1/billing/status: the plan, its caps, what the workspace uses of them and which limits it is over.
    Reads the local mirror of Stripe's state; it makes no Stripe call."""
    workspace = get_or_create_workspace(user)
    plan = workspace.plan if workspace.plan in PLANS else "free"
    caps = PLANS[plan]
    storage_used = workspace_storage_bytes(workspace)
    # Every active key counts toward the plan's key cap, including keys
    # minted by `veilfile login`.
    active_keys = active_key_count(workspace)
    subscription = getattr(workspace, "subscription", None)
    period_end = getattr(subscription, "current_period_end", None)
    return Answer(
        200,
        {
            "plan": plan,
            "caps": {
                "uploads_per_month": caps["uploads"],
                "ttl_days": caps["ttl_days"],
                "keys": caps["keys"],
                "storage_gb": caps["storage_gb"],
            },
            "storage_used_bytes": storage_used,
            "active_keys": active_keys,
            "over_plan_limits": _over_plan_limits(
                workspace, caps, storage_used, active_keys
            ),
            "price_usd": caps.get("price_usd", 0),
            "has_billing_account": bool(workspace.stripe_customer_id),
            "has_subscription": bool(workspace.stripe_subscription_id),
            "subscription_status": getattr(subscription, "status", "") or "",
            "current_period_end": period_end.isoformat() if period_end else None,
        },
    )


def checkout(user, plan):
    """POST /api/v1/billing/checkout: a Stripe Checkout session for a paid plan.

    The 200 body is ``{"checkout_url": ...}``. Only for workspaces without a subscription: Stripe, not the local
    mirror, decides, and an existing subscriber gets 409 ``already_subscribed`` and changes plans through ``switch``
    or the customer portal. Shared by the dashboard, the checkout API and the plan-aware signup flow.
    """
    if plan not in PAID_PLANS:
        return Answer(400, {"error": "invalid_plan"})

    stripe_mod, err = _stripe_or_503()
    if err is not None:
        return err

    price_id = _price_id(plan)
    if not price_id:
        return Answer(503, {"error": "billing_not_configured"})

    workspace = get_or_create_workspace(user)
    try:
        customer_id = _ensure_customer(stripe_mod, workspace, user)
        # Stripe, not the local mirror, decides: the webhook for a checkout
        # finished a moment ago may not have arrived yet.
        subs = _list_subscriptions(stripe_mod, customer_id)
        if subs is None:
            customer_id = _ensure_customer(
                stripe_mod, workspace, user, replace=customer_id
            )
            subs = []
        live = [s for s in subs if s.get("status") in LIVE_STATUSES]
        if live:
            # Local state may lag Stripe (webhook not in yet, or lost):
            # bring it up to date so the dashboard shows the right controls.
            _sync_from_subscriptions(workspace, subs, customer_id)
            return _already_subscribed(live)
        try:
            session = _create_checkout_session(
                stripe_mod, workspace, customer_id, plan, price_id
            )
        except stripe_mod.error.StripeError as exc:
            if not _is_missing_customer(exc):
                raise
            customer_id = _ensure_customer(
                stripe_mod, workspace, user, replace=customer_id
            )
            session = _create_checkout_session(
                stripe_mod, workspace, customer_id, plan, price_id
            )
    except stripe_mod.error.StripeError as exc:
        return _stripe_error(exc)
    return Answer(200, {"checkout_url": session["url"]})


def switch(user, plan):
    """POST /api/v1/billing/switch: switch an existing subscription between Team, Team Plus and Scale in place.

    Swaps the price on the subscription's plan item with
    ``proration_behavior=always_invoice`` (the prorated difference is
    invoiced now; a downgrade becomes a credit) and
    ``payment_behavior=pending_if_incomplete``: if that invoice cannot be
    paid, Stripe keeps the change pending and the plan stays as it is; the
    402 carries the invoice page where the customer can still pay it.
    Repeating a request for the current plan is a no-op, except that it
    cancels a change still pending payment.

    The workspace row is locked, in a transaction, for the whole of it, Stripe calls included.
    """
    if plan not in PAID_PLANS:
        return _error("invalid_plan", "Choose the team, team_plus or scale plan.", 400)

    stripe_mod, err = _stripe_or_503()
    if err is not None:
        return err
    price_id = _price_id(plan)
    if not price_id:
        return Answer(503, {"error": "billing_not_configured"})

    workspace = get_or_create_workspace(user)
    if not workspace.stripe_customer_id:
        return _no_subscription()
    try:
        with transaction.atomic():
            workspace = Workspace.objects.select_for_update().get(
                pk=workspace.pk
            )
            return _switch(stripe_mod, workspace, plan, price_id)
    except stripe_mod.error.StripeError as exc:
        logger.error(
            "billing: plan switch to %s failed for workspace %s: %s",
            plan,
            workspace.pk,
            exc,
        )
        return _stripe_error(exc)


def _switch(stripe_mod, workspace, plan, price_id):
    customer_id = workspace.stripe_customer_id
    subs = _list_subscriptions(stripe_mod, customer_id)
    if subs is None:
        _forget_missing_customer(workspace, customer_id)
        return _no_subscription()

    def refuse(answer):
        # Local state may lag Stripe (a lost webhook, or a pre-fix
        # workspace): sync it, so the dashboard offers what will work.
        _apply_subscriptions(workspace, subs, customer_id)
        return answer

    entitled = [s for s in subs if s.get("status") in ENTITLED_STATUSES]
    current = next(
        (s for s in entitled if s.get("id") == workspace.stripe_subscription_id),
        None,
    )
    if current is None and len(entitled) == 1:
        current = entitled[0]
    if current is None:
        if entitled:
            return refuse(
                _error(
                    "multiple_subscriptions",
                    "This workspace has more than one subscription. Use "
                    "Manage billing to cancel the extra one, or contact "
                    "support.",
                    409,
                )
            )
        return refuse(_no_subscription())
    if current.get("status") == "past_due":
        return refuse(
            _error(
                "subscription_past_due",
                "Your last payment failed. Update your payment method under "
                "Manage billing before changing plans.",
                409,
            )
        )
    item = _plan_item(current)
    if item is None:
        return refuse(_no_subscription())

    if _item_price_id(item) == price_id:
        _apply_subscriptions(workspace, subs, customer_id)
        if current.get("pending_update"):
            return _cancel_pending(stripe_mod, workspace, current)
        return Answer(200, {"plan": workspace.plan, "status": "unchanged"})

    updated = stripe_mod.Subscription.modify(
        current["id"],
        items=[{"id": item["id"], "price": price_id}],
        proration_behavior="always_invoice",
        payment_behavior="pending_if_incomplete",
        expand=["latest_invoice"],
    )
    subs = [updated if s.get("id") == updated.get("id") else s for s in subs]
    _apply_subscriptions(workspace, subs, customer_id)
    if updated.get("pending_update"):
        logger.warning(
            "billing: switch of %s to %s is pending payment (workspace %s)",
            current["id"],
            plan,
            workspace.pk,
        )
        invoice = updated.get("latest_invoice")
        invoice_url = (
            invoice.get("hosted_invoice_url") if isinstance(invoice, dict) else None
        )
        if invoice_url:
            # Paying this invoice applies the pending change, including
            # when the bank asks the customer to authenticate (3DS),
            # which a new card would not get around.
            message = (
                "The payment for the plan change did not go through, so "
                "your plan is unchanged. Complete the payment on the "
                "invoice page (your bank may ask you to confirm it), or "
                "update your payment method under Manage billing and "
                "switch again."
            )
        else:
            message = (
                "The payment for the plan change did not go through, so "
                "your plan is unchanged. Update your payment method under "
                "Manage billing, then switch again."
            )
        return Answer(
            402,
            {
                "error": message,
                "code": "payment_required",
                "plan": workspace.plan,
                "invoice_url": invoice_url,
            },
        )
    return Answer(
        200,
        {
            "plan": workspace.plan,
            "status": "switched",
            "subscription_status": updated.get("status") or "",
        },
    )


def _cancel_pending(stripe_mod, workspace, current):
    """Keep the current plan: void the invoice of a pending (unpaid)
    switch, which is how Stripe cancels a pending update. Otherwise
    paying that invoice later would still apply the abandoned switch."""
    invoice = current.get("latest_invoice")
    invoice_id = invoice.get("id") if isinstance(invoice, dict) else invoice
    if not invoice_id:
        logger.warning(
            "billing: subscription %s has a pending update but no latest "
            "invoice to void (workspace %s)",
            current.get("id"),
            workspace.pk,
        )
        return Answer(200, {"plan": workspace.plan, "status": "unchanged"})
    stripe_mod.Invoice.void_invoice(invoice_id)
    logger.info(
        "billing: cancelled the pending switch on %s by voiding %s "
        "(workspace %s stays on %s)",
        current.get("id"),
        invoice_id,
        workspace.pk,
        workspace.plan,
    )
    return Answer(200, {"plan": workspace.plan, "status": "pending_cancelled"})


def portal(user):
    """POST /api/v1/billing/portal: a Stripe customer-portal session for managing the subscription. The 200 body is
    ``{"portal_url": ...}``; 400 ``no_billing_account`` when the workspace has no Stripe customer."""
    stripe_mod, err = _stripe_or_503()
    if err is not None:
        return err

    workspace = get_or_create_workspace(user)
    if not workspace.stripe_customer_id:
        return Answer(400, {"error": "no_billing_account"})

    try:
        session = stripe_mod.billing_portal.Session.create(
            customer=workspace.stripe_customer_id,
            return_url=_base_url() + "/app/",
        )
    except stripe_mod.error.StripeError as exc:
        if _is_missing_customer(exc):
            _forget_missing_customer(workspace, workspace.stripe_customer_id)
            return Answer(400, {"error": "no_billing_account"})
        logger.error(
            "billing: portal session failed for workspace %s: %s",
            workspace.pk,
            exc,
        )
        return _stripe_error(exc)
    return Answer(200, {"portal_url": session["url"]})
