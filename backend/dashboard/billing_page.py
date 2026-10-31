"""What the Billing page says and what it remembers (spec 4.8).

The page is the React dashboard's billing section as ordinary pages. This module holds what is not HTTP: the plans a visitor can
buy, the one sentence an answer that was not a success gets, and the unpaid plan switch the session keeps until something settles
it. A visitor reads only the sentences below: what Stripe or the setup said goes to the log (`refusal`), and the sentences that
cannot say why end with a reference that finds that log line. Every figure comes from drop_core/plans.py (AGENTS.md, "Pricing changes"): nothing here types a price, a cap or a plan's
figure, and the pricing scan (drop_core/tests_fix_docs.py) reads this file.
"""
import logging
import secrets
import time

from drop_billing import account as billing_account
# plan_name is how the page names a plan, the way the Plans chapter does: "team_plus" is "Team Plus".
from drop_core.plans import ANNUAL_PRICE_USD, PLANS, plan_name

# The logger the other billing code writes to (drop_billing/views.py logs under drop_billing.views, which this parent receives).
logger = logging.getLogger("drop_billing")

# The session key of a plan switch that was answered payment_required: {"plan", "interval", "invoice_url", "at"} (an entry from before
# yearly billing has no "interval" and was monthly). It is a hint for the page,
# because nothing a later page load can read says a switch is waiting. It is not a secret (invariant 1 is about API keys): a plan,
# Stripe's invoice page when Stripe gave one over https, and when it was made.
PENDING_KEY = "dashboard_pending_switch"
# Stripe lets an unpaid switch lapse within a day, as the page says; the hint lapses with it.
PENDING_SECONDS = 24 * 60 * 60

# What a visitor reads when Billing refuses, in the customer's words. Stripe's own text, the API's codes and the word "instance"
# never reach the page (the owner's rule); they go to the log with a reference, and the sentences that cannot say why end with it.
NOT_CONFIGURED = "Billing isn't available right now. Try again later."
STRIPE_FAILED = "Billing couldn't complete that just now. Try again in a moment."
# The refusals the billing code makes for a customer to act on, one fixed sentence for each (status, code). A 409 already_subscribed is
# the exception: its advice depends on the subscription's status, so it is already_subscribed_sentence().
PAYMENT_FAILED = "The payment for the plan change did not go through, so your plan is unchanged."
PAST_DUE = "Your last payment failed. Update your payment method under Manage billing before changing plans."
MULTIPLE_SUBSCRIPTIONS = "This workspace has more than one subscription. Use Manage billing to cancel the extra one, or contact support."
# The bare codes the API answers with a 400 (invalid_plan, no_billing_account) and the refusals the page itself makes, in words.
NO_SUCH_PLAN = "That plan isn't available. Choose one of the plans on this page."
NO_BILLING_ACCOUNT = "There is no billing account to manage yet. Upgrade to a paid plan to start one."
NOT_SUBSCRIBED = "You have no subscription to switch yet. Upgrade to a paid plan to start one."

FIXED = {
    (402, "payment_required"): PAYMENT_FAILED,
    (409, "subscription_past_due"): PAST_DUE,
    (409, "no_subscription"): NOT_SUBSCRIBED,
    (409, "multiple_subscriptions"): MULTIPLE_SUBSCRIPTIONS,
}


def paid_plans():
    """The plans a visitor can buy, in the order plans.py lists them: every plan that has a price."""
    return [slug for slug, caps in PLANS.items() if caps["price_usd"]]


def https_only(url):
    """`url` if it is a string that starts with https://, else None. The page sends a visitor only where Stripe's own https address
    says: the React dashboard's rule for the invoice page, kept for the checkout and the portal as well."""
    return url if isinstance(url, str) and url.startswith("https://") else None


# What follows a price's figure on the page: "/mo" for a month, "/yr" for a year.
PER = {"month": "mo", "year": "yr"}


def interval_of(value):
    """A billing interval the page can act on: "year" for "year", and "month" for anything else, an unknown interval (a mirror
    written before yearly billing) included."""
    return "year" if value == "year" else "month"


def for_sale(slug, interval):
    """True when `slug` is sold billed every `interval`: any paid plan monthly, and yearly a paid plan with a yearly price."""
    return slug in paid_plans() and (interval == "month" or (interval == "year" and slug in ANNUAL_PRICE_USD))


def price_of(slug, interval):
    """What a paid plan costs for one `interval`, from plans.py."""
    return ANNUAL_PRICE_USD[slug] if interval == "year" else PLANS[slug]["price_usd"]


def billed_name(slug, interval):
    """A plan named with how it is paid when that is yearly: "Team" monthly, "Team yearly" yearly."""
    return f"{plan_name(slug)} yearly" if interval == "year" else plan_name(slug)


def plan_options(current, interval="month"):
    """The plans the page offers monthly: each paid plan that is not the plan held, if it is held monthly, as {slug, name, price}."""
    return [{"slug": slug, "name": plan_name(slug), "price": PLANS[slug]["price_usd"]}
            for slug in paid_plans() if (slug, "month") != (current, interval)]


def yearly_options(current, interval="month"):
    """The plans the page offers yearly: each paid plan that is not the plan held, if it is held yearly, as {slug, name, price}."""
    return [{"slug": slug, "name": plan_name(slug), "price": ANNUAL_PRICE_USD[slug]}
            for slug in paid_plans() if for_sale(slug, "year") and (slug, "year") != (current, interval)]


def is_upgrade(current, target):
    """True when moving from `current` to `target` costs more: both are for sale and the target's price is the higher. A switch
    from anything that is not a paid plan reads as a downgrade, as the React dashboard's switchConfirmText had it."""
    return current in paid_plans() and PLANS[target]["price_usd"] > PLANS[current]["price_usd"]


def switch_change(current, target, current_interval, interval):
    """Which sentence billing_switch.html prints. Between two prices of one interval it is React's pair, "upgrade" or "downgrade".
    A move to the other interval is "to_yearly" or "to_monthly": Stripe then starts a new billing period on the day of the switch and
    invoices it at once, less a credit for the unused part of the old one, which neither of React's sentences says."""
    if current_interval == interval:
        return "upgrade" if is_upgrade(current, target) else "downgrade"
    return "to_yearly" if interval == "year" else "to_monthly"


def switch_context(current, target, current_interval="month", interval="month"):
    """What billing_switch.html prints: the plan and interval to move to, the plan moved from, which sentence applies, and whether
    the target's limits are lower (a plan the page lists before the current one), which the interval sentences then add."""
    paid = paid_plans()
    change = switch_change(current, target, current_interval, interval)
    # The button names the interval whenever the switch is to a yearly price or back to a monthly one: "Switch to Team" alone
    # would not say which of Team's two prices it means.
    label = f"{plan_name(target)} monthly" if change == "to_monthly" else billed_name(target, interval)
    return {
        "target": {"slug": target, "name": plan_name(target), "label": label, "price": price_of(target, interval), "per": PER[interval],
                   "interval": interval},
        "current": {"slug": current, "name": billed_name(current, current_interval)},
        "change": change,
        "lower": current in paid and paid.index(target) < paid.index(current),
    }


# Every status Stripe reports for a subscription, in the customer's words. A status not listed shows nothing: the page never prints a code.
STATUS_WORDS = {
    "active": "Active",
    "trialing": "Trial",
    "past_due": "Payment overdue",
    "unpaid": "Unpaid",
    "incomplete": "Payment not finished",
    "incomplete_expired": "Payment expired",
    "paused": "Paused",
    "canceled": "Canceled",
}


def status_words(subscription_status):
    """The subscription's status as the customer reads it, or "" for a status this page has no words for."""
    return STATUS_WORDS.get(subscription_status, "") if isinstance(subscription_status, str) else ""


def page_context(status, pending):
    """What billing.html prints. `status` is the body billing.status answers; `pending` is pending_for()'s answer."""
    plan = status["plan"]
    subscribed = bool(status["has_subscription"])
    # The interval the subscription is billed at, as the mirror knows it ("" before a webhook has written it, which is monthly:
    # every subscription before yearly billing was).
    known = status.get("billing_interval") or ""
    interval = interval_of(known) if subscribed else ""
    return {
        "status": status,
        "plan": plan,
        "plan_name": plan_name(plan),
        "subscribed": subscribed,
        "interval": interval,
        "billed_words": {"month": "monthly", "year": "yearly"}.get(known, "") if subscribed else "",
        "subscription_status": status["subscription_status"],
        "status_words": status_words(status["subscription_status"]),
        "can_manage": bool(status["has_billing_account"]),
        "options": plan_options(plan, interval or "month"),
        "yearly_options": yearly_options(plan, interval or "month"),
        "pending": pending,
    }


def switch_result(plan, outcome, interval="month"):
    """The line after a switch that Stripe accepted. `plan` is the plan the workspace is on now, `outcome` the answer's status
    (switched, unchanged or pending_cancelled) and `interval` the one the switch named, which a 200 means the subscription is on.
    The sentences are the React dashboard's; a yearly plan adds "billed yearly"."""
    name = plan_name(plan)
    billed = ", billed yearly" if interval == "year" else ""
    if outcome == "pending_cancelled":
        return f"The pending switch was canceled. You stay on the {name} plan{billed}."
    if outcome == "unchanged":
        return f"You're already on the {name} plan{billed}."
    return f"You're now on the {name} plan{billed}."


def body_of(answer):
    """The answer's body if it is a dict, else an empty one: the page reads fields, and an answer it has never seen must not
    be a 500."""
    return answer.body if isinstance(answer.body, dict) else {}


def already_subscribed_sentence(subscription_status):
    """The advice for a checkout that a live subscription blocks, by that subscription's status (active or trialing, past_due, unpaid,
    incomplete, paused). The words are Veilfile's own customer copy, ALREADY_SUBSCRIBED_MESSAGES of drop_billing/account.py, reused and
    not copied; the one change is that the page it sends the visitor to is this one. An unknown status gets the active advice."""
    messages = billing_account.ALREADY_SUBSCRIBED_MESSAGES
    return messages.get(subscription_status, messages["active"]).replace("from the dashboard", "from this page")


def reference():
    """A short reference for one refusal: six hexadecimal digits in capitals, such as 4F9A2C. The visitor can read it out, and it finds
    the log line that holds what Stripe said."""
    return secrets.token_hex(3).upper()


def refusal(answer, bad_request, where):
    """The one sentence the Billing page shows for an answer that was not a success, and the one log line that holds the detail.

    A visitor never reads Stripe's words, an API code or "instance". Each refusal the page can explain is a fixed sentence: a 402
    payment_required and the 409s the billing code writes for a customer (FIXED; for already_subscribed the advice that fits the
    subscription's status), and a 400, which is a bare code (invalid_plan,
    no_billing_account) that the caller says in words (`bad_request`). A 503 (Stripe is not connected) is NOT_CONFIGURED, and a 502
    (Stripe failed), any status the page has never seen and an address it must not follow are STRIPE_FAILED: those end with
    "Reference: 4F9A2C", and the log line `billing: dashboard refusal ref=4F9A2C ...` holds the status, the code and Stripe's message
    (the launch runbook sends the owner there). A refusal the page explained logs at WARNING; the others at ERROR. `where` names the
    action (checkout, portal or switch).
    """
    body = body_of(answer)
    code = body.get("code")
    text = body.get("error")
    message = text if isinstance(text, str) else ("no https address to follow" if answer.status == 200 else "")
    sentence = FIXED.get((answer.status, code))
    if (answer.status, code) == (409, "already_subscribed"):
        sentence = already_subscribed_sentence(body.get("subscription_status"))
    if sentence is None and answer.status == 400:
        sentence = bad_request
    ref = reference()
    logger.log(
        logging.WARNING if sentence is not None else logging.ERROR,
        "billing: dashboard refusal ref=%s where=%s status=%s code=%s message=%r", ref, where, answer.status, code or "-", message,
    )
    if sentence is not None:
        return sentence
    return f"{NOT_CONFIGURED if answer.status == 503 else STRIPE_FAILED} Reference: {ref}"


def unexpected(where):
    """The sentence for an exception the billing code did not mean to raise (not a Stripe error, which it turns into an answer). Call it
    from inside the `except Exception` block: the traceback goes to the log under a reference, at ERROR, and the visitor reads
    STRIPE_FAILED and the reference, never the exception's text."""
    ref = reference()
    logger.exception("billing: dashboard refusal ref=%s where=%s status=- code=unexpected_exception message=%r", ref, where, "exception")
    return f"{STRIPE_FAILED} Reference: {ref}"


def remember_pending(session, plan, invoice_url, now=None, interval="month"):
    """Keep an unpaid switch to `plan` in the session: the plan and the interval the visitor asked for, Stripe's invoice page if it
    is an https address (else None), and the time."""
    session[PENDING_KEY] = {
        "plan": plan,
        "interval": interval,
        "invoice_url": https_only(invoice_url),
        "at": int(time.time() if now is None else now),
    }


def forget_pending(session):
    session.pop(PENDING_KEY, None)


def pending_for(session, current_plan, now=None, current_interval="month"):
    """The unpaid switch the Billing page should offer to settle, as {plan, name, invoice_url}, or None.

    An entry holds while the workspace is on a paid plan and interval that is not the one the switch named, the named plan is for
    sale, the entry is under PENDING_SECONDS old, and it is the shape remember_pending writes (an entry with no interval is a monthly
    one, written before yearly billing). An entry that no longer holds is forgotten here, so a stale hint does not wait for the next
    switch to go. The name says "yearly" for a yearly switch.
    """
    entry = session.get(PENDING_KEY)
    if entry is None:
        return None
    now = time.time() if now is None else now
    plan = entry.get("plan") if isinstance(entry, dict) else None
    at = entry.get("at") if isinstance(entry, dict) else None
    interval = entry.get("interval", "month") if isinstance(entry, dict) else None
    paid = paid_plans()
    holds = (
        plan in paid
        and interval in ("month", "year")
        and for_sale(plan, interval)
        and current_plan in paid
        and (plan, interval) != (current_plan, current_interval)
        and isinstance(at, (int, float))
        and 0 <= now - at < PENDING_SECONDS
    )
    if not holds:
        forget_pending(session)
        return None
    # A move back to monthly names "monthly": beside "Keep Team yearly", "Your switch to Team" would not say which Team it is.
    name = f"{plan_name(plan)} monthly" if (interval, current_interval) == ("month", "year") else billed_name(plan, interval)
    return {"plan": plan, "name": name, "invoice_url": https_only(entry.get("invoice_url"))}
