"""The Overview page (spec 4.8): what the account has used and what it is over, from the account functions that the JSON endpoints answer with.

Two reads feed it: drop_core.account.usage and drop_billing.account.status (our own tables; it makes no Stripe call). Either may fail, and a read that fails
leaves out its own part of the page, puts a sentence there and nothing else goes: the meters never wait on the billing status, nor the limit lines on usage, nor the
account on either. The failure is logged; its detail is never shown. Which limits the workspace is over is the include's to say (dashboard/over_limits.html), from the two bodies.
"""
import logging

from drop_billing import account as billing_account
from drop_core import account as core_account
from drop_core.email_verification import is_email_verified
from drop_core.models import EmailVerification
from drop_core.plans import PLANS, plan_name

logger = logging.getLogger(__name__)

# The ?billing= values that Stripe, sign up and checkout send a visitor back with (drop_billing/views.py), and how a screen reader is told of each.
BILLING_NOTICES = {"success": "status", "cancelled": "status", "error": "alert", "already_subscribed": "status"}


def read(function, user, what):
    """The body of an account function's answer, or None when it fails: it raised, or it answered something other than 200. The failure is logged."""
    try:
        answer = function(user)
    except Exception:
        logger.exception("dashboard: the %s could not be read for user %s", what, user.pk)
        return None
    if answer.status != 200:
        logger.warning("dashboard: the %s answered %s for user %s", what, answer.status, user.pk)
        return None
    return answer.body


def billing_notice(query):
    """The notice for ?billing= (and ?plan= for an error), or None for a value that is not one of the four.

    The plan is named only when ?plan= is a plan of drop_core/plans.py with a price, and the name is the plan's id written as the plan cards write it, never the
    text of the query: whatever else is in ?plan= is not printed.
    """
    kind = query.get("billing")
    if kind not in BILLING_NOTICES:
        return None
    plan = query.get("plan") if kind == "error" else ""
    return {"kind": kind, "role": BILLING_NOTICES[kind], "plan": plan_name(plan) if plan in PLANS and PLANS[plan]["price_usd"] > 0 else ""}


def overview_context(request):
    """What templates/dashboard/overview.html is drawn from."""
    user = request.user
    usage = read(core_account.usage, user, "usage")
    status = read(billing_account.status, user, "billing status")
    verified = is_email_verified(user)
    record = EmailVerification.objects.filter(user=user).first()
    return {
        "billing_notice": billing_notice(request.GET),
        # The link went to the address on the verification record, which is not user.email until it is verified (drop_core.views.email_verification).
        "verify_prompt": None if verified else {"sent_to": record.email if record is not None else ""},
        "usage": usage,
        "status": status,
        "plan": (usage or status or {}).get("plan", ""),
        "email": user.email or "",
        "email_verified": verified,
    }
