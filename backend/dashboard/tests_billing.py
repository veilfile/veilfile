"""Billing in the dashboard (spec 4.8): the page at /app/billing/, the plan switch and its confirmation page, checkout and the portal.

Stripe is never reached. These tests patch the Stripe library the way drop_billing.tests_fix_billing does, and its base class points
the library's API address at a closed port, so a call that a test forgot to patch fails at once. They pin what a visitor reads:
the React dashboard's sentences, as the voice pass reworded them; one fixed sentence in the customer's words on the Billing page for every way Stripe or the
setup can refuse, never a 500, and never Stripe's own text, a code or the word "instance" (those, and a reference that finds them, go to
the log); and that only a POST changes anything.

Every class sets longMessage = False, so that a failure prints its own message, which names the file and the fix, and not the
page it searched.
"""
import logging
import os
import re
import time
from datetime import timedelta
from html import unescape
from pathlib import Path
from unittest import mock

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client, SimpleTestCase
from django.urls import resolve, reverse
from django.utils import timezone

from dashboard import billing_page, views
from drop_billing import account as billing_account
from drop_billing.account import ALREADY_SUBSCRIBED_MESSAGES
from drop_billing.models import Subscription
from drop_billing.tests_fix_billing import ENV, FixBillingBase, make_sub, missing_customer_error, sub_list
from drop_core.account import Answer
from drop_core.models import APIKey, Artifact, Workspace
from drop_core.plans import ANNUAL_PRICE_USD, PLANS, plan_name
from pages import registry
from pages.tests_chapters import BANNED, visible_text

TEMPLATES = Path(settings.BASE_DIR) / "templates"
# dashboard/over_limits.html is Task 3's (Overview makes it, Billing includes it); its own tests hold its words.
OWN_TEMPLATES = ("dashboard/billing.html", "dashboard/billing_switch.html")
PAGE = "backend/templates/dashboard/billing.html"
SWITCH_PAGE = "backend/templates/dashboard/billing_switch.html"
VIEWS = "backend/dashboard/views.py"
INVOICE = "https://invoice.stripe.com/i/acct_1/in_pending"
STRIPE_URL = "https://checkout.stripe.com/c/pay/cs_test_1"
PORTAL_URL = "https://billing.stripe.com/p/session/test_1"
# The variable each paid plan's Stripe price is read from, by name and not from the slug: Team Plus's is STRIPE_PRICE_TEAMPLUS (the
# founder's choice, 2026-10-16), never STRIPE_PRICE_TEAM_PLUS.
PRICE_VARIABLES = {"team": "STRIPE_PRICE_TEAM", "team_plus": "STRIPE_PRICE_TEAMPLUS", "scale": "STRIPE_PRICE_SCALE"}
# What Stripe holds for a switch that the bank has not let through.
PENDING = {"expires_at": 1790000000, "subscription_items": [{"id": "si_1", "price": "price_scale"}]}

# What a visitor reads when Billing refuses. The first two end with "Reference: " and six capitals or digits (REFERENCE below).
NOT_CONFIGURED = "Billing isn't available right now. Try again later."
FAILED = "Billing couldn't complete that just now. Try again in a moment."
PAYMENT_FAILED = "The payment for the plan change did not go through, so your plan is unchanged."
# A checkout that a live subscription blocks gets the advice that fits its status: drop_billing's own customer copy (ALREADY_SUBSCRIBED_MESSAGES,
# reused, not copied), with "from the dashboard" said as "from this page". One phrase of each, so that a wrong pick is seen.
ADVICE = {
    "active": "Switch plans from this page instead of starting a new checkout.",
    "trialing": "Switch plans from this page instead of starting a new checkout.",
    "past_due": "failed payment that Stripe is retrying",
    "unpaid": "has an unpaid invoice",
    "incomplete": "still waiting for its first payment",
    "paused": "subscription is paused",
}
# Every status Stripe can report for a subscription, in the customer's words.
STATUS_WORDS = {
    "active": "Active", "trialing": "Trial", "past_due": "Payment overdue", "unpaid": "Unpaid", "incomplete": "Payment not finished",
    "incomplete_expired": "Payment expired", "paused": "Paused", "canceled": "Canceled",
}
PAST_DUE = "Your last payment failed. Update your payment method under Manage billing before changing plans."
MULTIPLE_SUBSCRIPTIONS = "This workspace has more than one subscription. Use Manage billing to cancel the extra one, or contact support."
NO_SUCH_PLAN = "That plan isn't available. Choose one of the plans on this page."
NO_BILLING_ACCOUNT = "There is no billing account to manage yet. Upgrade to a paid plan to start one."
NOT_SUBSCRIBED = "You have no subscription to switch yet. Upgrade to a paid plan to start one."
REFERENCE = re.compile(r"Reference: ([0-9A-F]{6})$")
LOGGED = re.compile(r"^billing: dashboard refusal ref=([0-9A-F]{6}) ")
# Words of the operator, which no visitor reads on the page: Stripe's text ("Stripe: ..."), the API's codes, "instance".
OPERATOR_WORDS = ("Stripe:", "instance", "billing_not_configured", "stripe_error", "invalid_plan", "no_billing_account",
                  "payment_required", "already_subscribed", "until Stripe is connected")
# The sentences the React dashboard said, word for word. That dashboard is gone; these constants are where they are kept.
WARNINGS = {
    "past_due": "Your last payment failed. Stripe is retrying; update your payment method under Manage billing to keep your plan.",
    "unpaid": ("Your subscription has an unpaid invoice, so this workspace is on the Free plan. Pay it, or cancel the "
               "subscription, under Manage billing."),
    "incomplete": ("Your subscription's first payment hasn't gone through. Complete it under Manage billing; an unfinished "
                   "payment expires within a day."),
    "paused": "Your subscription is paused, so this workspace is on the Free plan. Resume or cancel it under Manage billing.",
}
EXPLAIN_SUBSCRIBED = ("Plan switches apply to your existing subscription with prorated charges or credits. Manage billing opens "
                      "the Stripe portal for your payment method, invoices, and cancellation.")
EXPLAIN_FREE = ("Upgrades go through Stripe Checkout. Once subscribed, you switch plans here and manage payment details and "
                "cancellation in the customer portal.")
PENDING_WITH_INVOICE = ("Your switch to Scale is waiting for payment. Pay the invoice to apply it (your bank may ask you to "
                        "confirm), or keep your current plan. Unpaid switches lapse within a day.")
PENDING_WITHOUT_INVOICE = ("Your switch to Scale is waiting for payment. Keep your current plan, or update your payment method "
                           "under Manage billing and switch again. Unpaid switches lapse within a day.")


def price(slug):
    return PLANS[slug]["price_usd"]


def name(slug):
    return plan_name(slug)


def main_of(html):
    return html[html.index("<main"):html.index("</main>")]


def attributes(tag):
    return dict(re.findall(r'([\w-]+)="([^"]*)"', tag))


def words(html):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def forms_in(html):
    """Each <form> in <main>: its attributes, its inputs' attributes and the text of its buttons."""
    return [{
        "attrs": attributes(opening),
        "inputs": [attributes(tag) for tag in re.findall(r"<input\b([^>]*)>", body)],
        "buttons": [words(text) for text in re.findall(r"<button\b[^>]*>(.*?)</button>", body, re.S)],
    } for opening, body in re.findall(r"<form\b([^>]*)>(.*?)</form>", main_of(html), re.S)]


def links_in(html):
    """Each <a> in <main> as (attributes, text)."""
    return [(attributes(opening), words(text)) for opening, text in re.findall(r"<a\b([^>]*)>(.*?)</a>", main_of(html), re.S)]


def value_of(form, input_name):
    return next((tag.get("value") for tag in form["inputs"] if tag.get("name") == input_name), None)


class BillingCase(FixBillingBase):
    """A signed-in workspace with the Stripe library patched. self.ws is the workspace; set_billing, patch_stripe and reload
    are FixBillingBase's."""
    longMessage = False

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.billing = reverse("app-billing")
        self.checkout = reverse("app-billing-checkout")
        self.portal = reverse("app-billing-portal")

    def switch_url(self, plan):
        return reverse("app-billing-switch", args=[plan])

    def page(self, url=None):
        """The text of <main> on a page, the way a visitor reads it."""
        response = self.client.get(url or self.billing)
        self.assertEqual(response.status_code, 200, f"{url or self.billing} answered {response.status_code}, not 200")
        return visible_text(response.content.decode())

    def said(self, response):
        """The lines the request left for the next page. Reading them forgets them, so that a test which sends several requests
        (a subTest for each plan) reads only the line of the one it just sent."""
        lines = [str(message) for message in get_messages(response.wsgi_request)]
        self.client.cookies.pop("messages", None)
        return lines

    def refused(self, path, data=None):
        """POST to `path`, follow the redirect to Billing, and return (response, sentence, record): the page, the one line it shows
        above the heading, and the one `billing: dashboard refusal` record the request logged through the drop_billing logger."""
        with self.assertLogs("drop_billing", level="WARNING") as captured:
            response = self.client.post(path, data or {}, follow=True)
        self.assertEqual(response.redirect_chain, [(self.billing, 302)], f"{VIEWS}: a refusal goes back to Billing, not to a 400 or a 500")
        records = [record for record in captured.records if LOGGED.match(record.getMessage())]
        self.assertEqual(len(records), 1, f"{VIEWS}: a refusal logs one 'billing: dashboard refusal ref=' line through the drop_billing logger")
        lines = self.said(response)
        self.assertEqual(len(lines), 1, f"{VIEWS}: and leaves one line for Billing")
        return response, lines[0], records[0]

    def assertFixed(self, sentence, fixed, record, level, why):
        """A refusal the page explains: the fixed sentence as it is, no reference, and a log line at `level`."""
        self.assertEqual(sentence, fixed, f"{VIEWS}: {why}")
        self.assertIsNone(REFERENCE.search(sentence), f"{VIEWS}: a sentence that says why needs no reference")
        self.assertEqual(record.levelno, level, f"backend/dashboard/billing_page.py: refusal() logs {logging.getLevelName(level)} for this")

    def assertReferenced(self, response, sentence, fixed, record, secret):
        """A refusal the page cannot explain: `fixed`, then 'Reference: XXXXXX'; the log line carries that reference and `secret` (what
        Stripe said), at ERROR; and neither the sentence nor the page holds `secret` or any word of the operator."""
        match = REFERENCE.search(sentence)
        self.assertIsNotNone(match, f"{VIEWS}: the sentence ends with 'Reference: ' and six capitals or digits, got {sentence!r}")
        self.assertEqual(sentence, f"{fixed} Reference: {match.group(1)}", f"{VIEWS}: the fixed sentence, then the reference")
        logged = record.getMessage()
        self.assertEqual(LOGGED.match(logged).group(1), match.group(1), "backend/dashboard/billing_page.py: the reference on the page is the one in the log")
        self.assertEqual(record.levelno, logging.ERROR, "backend/dashboard/billing_page.py: refusal() logs ERROR for a failure the page cannot explain")
        if secret:
            self.assertIn(secret, logged, "backend/dashboard/billing_page.py: the log line holds what Stripe said")
        html = response.content.decode()
        text = visible_text(html)
        self.assertIn(sentence, text, f"{PAGE}: the sentence is on the page")
        for word in OPERATOR_WORDS + ((secret,) if secret else ()):
            self.assertNotIn(word, text, f"{VIEWS}: the page must not show {word!r}; it goes to the log")
        if secret:
            self.assertNotIn(secret, html, f"{VIEWS}: Stripe's message is not in the page at all")

    def subscribe(self, plan="team", status="active"):
        """The workspace pays for `plan`; Stripe's list says the same."""
        self.set_billing(plan, customer="cus_A", sub_id="sub_A", status=status)
        return self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", status, f"price_{plan}")))

    def lapse(self, status):
        """A workspace that fell to free while the mirror still names the subscription's status."""
        self.set_billing("free", customer="cus_A")
        Subscription.objects.update_or_create(
            workspace=self.ws,
            defaults={"stripe_customer_id": "cus_A", "stripe_subscription_id": "sub_A", "status": status})

    def no_stripe(self):
        """Every Stripe call these pages could make, patched; assertNoStripe says none was made."""
        return {key: self.patch_stripe(key) for key in ("list", "modify", "customer", "session", "portal", "void", "retrieve_customer")}

    def assertNoStripe(self, calls, message):
        for key, patched in calls.items():
            self.assertFalse(patched.called, f"{message}: stripe {key} was called")

    def assertSentTo(self, response, location, message):
        self.assertEqual((response.status_code, response.get("Location")), (302, location), message)

    def pending(self, **changes):
        """An unpaid switch in the session, as billing_page.remember_pending writes it, with these fields changed."""
        session = self.client.session
        session[billing_page.PENDING_KEY] = {"plan": "scale", "invoice_url": INVOICE, "at": int(time.time()), **changes}
        session.save()

    def stripe_ready(self, url=STRIPE_URL):
        """Stripe, as it answers a checkout for a workspace with no customer yet: (customer, list, session)."""
        customer = self.patch_stripe("customer", return_value={"id": "cus_new"})
        lister = self.patch_stripe("list", return_value=sub_list())
        session = self.patch_stripe("session", return_value={"id": "cs_test_1", "url": url})
        return customer, lister, session

    def go_over(self, keys=0, storage=False, ttl=False):
        """The free plan's workspace over the limits asked for (the way OverLimitTests do it)."""
        caps = PLANS["free"]
        for index in range(keys):
            APIKey.create_key(self.ws, f"key {index}")
        if storage or ttl:
            Artifact.objects.create(
                workspace=self.ws, filename="big.bin",
                size_bytes=(caps["storage_gb"] + 1) * 1024**3 if storage else 1,
                expires_at=timezone.now() + timedelta(days=caps["ttl_days"] + 300 if ttl else 1))


class BillingPageModuleTests(SimpleTestCase):
    """backend/dashboard/billing_page.py: what is not HTTP."""
    longMessage = False
    FILE = "backend/dashboard/billing_page.py"

    def test_the_plans_for_sale_are_the_plans_that_have_a_price(self):
        self.assertEqual(billing_page.paid_plans(), ["team", "team_plus", "scale"], f"{self.FILE}: paid_plans() lists the plans of plans.py that have a price")
        with mock.patch.dict(PLANS, {"pro": {**PLANS["scale"], "price_usd": 30}}):
            self.assertEqual(billing_page.paid_plans(), ["team", "team_plus", "scale", "pro"], f"{self.FILE}: a plan added to plans.py is for sale at once")

    def test_a_plan_is_named_the_way_the_plans_chapter_names_it(self):
        self.assertEqual([billing_page.plan_name(slug) for slug in PLANS], ["Free", "Team", "Team Plus", "Scale"],
                         f"{self.FILE}: plan_name() capitalises the slug and reads an underscore as a space")

    def test_only_an_https_address_is_followed(self):
        good = "https://checkout.stripe.com/c/pay/cs_1"
        self.assertEqual(billing_page.https_only(good), good, f"{self.FILE}: https_only() keeps an https address")
        for bad in ("http://checkout.stripe.com/x", "javascript:alert(1)", "//evil.example/x", "HTTPS://x", "", None, 7, ["https://x"]):
            with self.subTest(url=bad):
                self.assertIsNone(billing_page.https_only(bad), f"{self.FILE}: https_only() gives None for anything that does not start with https://")

    def test_the_other_paid_plans_are_offered_with_their_prices_from_plans_py(self):
        self.assertEqual(billing_page.plan_options("free"),
                         [{"slug": slug, "name": name(slug), "price": price(slug)} for slug in ("team", "team_plus", "scale")], f"{self.FILE}: plan_options('free')")
        self.assertEqual([option["slug"] for option in billing_page.plan_options("team")], ["team_plus", "scale"], f"{self.FILE}: the current plan is not offered")
        self.assertEqual([option["slug"] for option in billing_page.plan_options("team_plus")], ["team", "scale"], f"{self.FILE}: the current plan is not offered")
        self.assertEqual([option["slug"] for option in billing_page.plan_options("scale")], ["team", "team_plus"], f"{self.FILE}: the current plan is not offered")
        self.assertEqual([option["slug"] for option in billing_page.plan_options("team", "year")], ["team", "team_plus", "scale"],
                         f"{self.FILE}: a plan held yearly is still offered monthly")

    def test_a_switch_costs_more_when_the_target_has_the_higher_price(self):
        for current, target, expected in (("team", "scale", True), ("scale", "team", False), ("free", "team", False), ("", "scale", False),
                                          ("team", "team_plus", True), ("team_plus", "scale", True), ("team_plus", "team", False), ("scale", "team_plus", False)):
            with self.subTest(current=current, target=target):
                self.assertIs(billing_page.is_upgrade(current, target), expected,
                              f"{self.FILE}: is_upgrade() compares the two prices in plans.py; a current plan that is not for sale reads as a downgrade (React's switchConfirmText)")
        with mock.patch.dict(PLANS, {"team": {**PLANS["team"], "price_usd": price("scale") + 1}}):
            self.assertIs(billing_page.is_upgrade("scale", "team"), True, f"{self.FILE}: is_upgrade() must compare prices, not plan names")

    def test_the_switch_page_is_given_both_plans_and_its_price(self):
        context = billing_page.switch_context("team", "scale")
        self.assertEqual(context, {"target": {"slug": "scale", "name": "Scale", "label": "Scale", "price": price("scale"), "per": "mo", "interval": "month"},
                                   "current": {"slug": "team", "name": "Team"}, "change": "upgrade", "lower": False}, f"{self.FILE}: switch_context()")

    def test_a_switch_that_stripe_accepted_is_a_line_in_the_react_dashboards_words(self):
        for outcome, sentence in (("switched", "You're now on the Scale plan."), ("unchanged", "You're already on the Scale plan."),
                                  ("pending_cancelled", "The pending switch was canceled. You stay on the Scale plan."), (None, "You're now on the Scale plan.")):
            with self.subTest(outcome=outcome):
                self.assertEqual(billing_page.switch_result("scale", outcome), sentence, f"{self.FILE}: switch_result()")

    def refuse(self, answer, where="checkout"):
        """billing_page.refusal() with the reference fixed: (sentence, the one record it logged)."""
        with mock.patch.object(billing_page.secrets, "token_hex", return_value="4f9a2c"), self.assertLogs("drop_billing", level="WARNING") as captured:
            sentence = billing_page.refusal(answer, "In words.", where)
        self.assertEqual(len(captured.records), 1, f"{self.FILE}: refusal() logs exactly one record")
        return sentence, captured.records[0]

    def test_a_refusal_the_page_can_explain_is_a_fixed_sentence_and_a_warning(self):
        cases = (
            (Answer(402, {"error": "The payment for the plan change did not go through, so your plan is unchanged. Complete the payment on the invoice page.", "code": "payment_required", "invoice_url": INVOICE}),
             PAYMENT_FAILED, "a 402 payment_required"),
            (Answer(409, {"error": "Your last payment failed.", "code": "subscription_past_due"}), PAST_DUE, "a 409 subscription_past_due"),
            (Answer(409, {"error": "This workspace has no active subscription to change. Subscribe first with POST /api/v1/billing/checkout.", "code": "no_subscription"}),
             NOT_SUBSCRIBED, "a 409 no_subscription says to upgrade, not to use checkout"),
            (Answer(409, {"error": "more than one", "code": "multiple_subscriptions"}), MULTIPLE_SUBSCRIPTIONS, "a 409 multiple_subscriptions"),
            (Answer(400, {"error": "invalid_plan"}), "In words.", "a 400 is a bare code, so the caller says it in words"),
        )
        for answer, sentence, why in cases:
            with self.subTest(status=answer.status, body=answer.body):
                said, record = self.refuse(answer)
                self.assertEqual(said, sentence, f"{self.FILE}: refusal(): {why}")
                self.assertEqual(record.levelno, logging.WARNING, f"{self.FILE}: refusal() logs WARNING for a refusal it explains")
                self.assertTrue(record.getMessage().startswith("billing: dashboard refusal ref=4F9A2C where=checkout status="), f"{self.FILE}: the line begins 'billing: dashboard refusal ref=<REF>'")

    def test_a_checkout_that_a_subscription_blocks_gets_the_advice_for_its_status(self):
        self.assertEqual(set(ADVICE), set(ALREADY_SUBSCRIBED_MESSAGES), f"{self.FILE}: a status was added to or removed from drop_billing's ALREADY_SUBSCRIBED_MESSAGES; give it a phrase in ADVICE")
        seen = {}
        for status, phrase in ADVICE.items():
            with self.subTest(status=status):
                answer = Answer(409, {"error": "Stripe: not for a customer", "code": "already_subscribed", "subscription_status": status})
                said, record = self.refuse(answer)
                self.assertEqual(said, ALREADY_SUBSCRIBED_MESSAGES[status].replace("from the dashboard", "from this page"), f"{self.FILE}: the advice for {status} is drop_billing's, reused")
                self.assertIn(phrase, said, f"{self.FILE}: the advice for {status}")
                self.assertNotIn("from the dashboard", said, f"{self.FILE}: the page it sends the visitor to is this one")
                self.assertNotIn("Stripe:", said, f"{self.FILE}: never the API's own error text")
                self.assertIsNone(REFERENCE.search(said), f"{self.FILE}: advice that says why needs no reference")
                self.assertEqual(record.levelno, logging.WARNING, f"{self.FILE}: refusal() logs WARNING for it")
                seen[status] = said
        self.assertEqual(len({seen[status] for status in ("active", "past_due", "unpaid", "incomplete", "paused")}), 5, f"{self.FILE}: each status has its own advice")
        self.assertEqual(seen["trialing"], seen["active"], f"{self.FILE}: a trial is advised like an active subscription")
        for unknown in (None, "canceled", 7):
            with self.subTest(unknown=unknown):
                answer = Answer(409, {"error": "x", "code": "already_subscribed", "subscription_status": unknown})
                self.assertEqual(self.refuse(answer)[0], seen["active"], f"{self.FILE}: a status it has no advice for gets the active advice")

    def test_the_words_of_the_advice_are_mapped_not_copied(self):
        with mock.patch.dict(ALREADY_SUBSCRIBED_MESSAGES, {"active": "Pick again from the dashboard, or from the dashboard's menu."}):
            self.assertEqual(billing_page.already_subscribed_sentence("active"), "Pick again from this page, or from this page's menu.",
                             f"{self.FILE}: already_subscribed_sentence() reads drop_billing's constant at call time and says 'from this page'")

    def test_a_failure_the_page_cannot_explain_is_a_sentence_with_a_reference_and_an_error(self):
        stripe_text = "Stripe: Invalid line_items[0]: the product tax code is missing."
        cases = (
            (Answer(502, {"error": stripe_text, "code": "stripe_error"}), FAILED, stripe_text, "a 502 keeps Stripe's words in the log and out of the page"),
            (Answer(503, {"error": "billing_not_configured"}), NOT_CONFIGURED, "billing_not_configured", "a 503 is the not-available sentence"),
            (Answer(500, {"error": "boom"}), FAILED, "boom", "a status the page has never seen"),
            (Answer(429, "slow down"), FAILED, "", "a body that is not a dict"),
            (Answer(200, {"checkout_url": "http://x"}), FAILED, "no https address to follow", "a 200 with nothing to follow"),
            (Answer(502, {"error": ""}), FAILED, "", "a 502 with no words"),
            (Answer(409, {"error": ["not", "a", "string"], "code": "other"}), FAILED, "", "a 409 the page has no sentence for"),
            (Answer(402, {"error": "x", "code": "other"}), FAILED, "", "a 402 the page has no sentence for"),
        )
        for answer, fixed, in_log, why in cases:
            with self.subTest(status=answer.status, body=answer.body):
                said, record = self.refuse(answer, "portal")
                self.assertEqual(said, f"{fixed} Reference: 4F9A2C", f"{self.FILE}: refusal(): {why}")
                self.assertEqual(record.levelno, logging.ERROR, f"{self.FILE}: refusal() logs ERROR for a failure it cannot explain")
                logged = record.getMessage()
                self.assertTrue(logged.startswith(f"billing: dashboard refusal ref=4F9A2C where=portal status={answer.status} code="), f"{self.FILE}: {logged}")
                self.assertIn(in_log, logged, f"{self.FILE}: the log line holds the detail")
                for word in OPERATOR_WORDS:
                    self.assertNotIn(word, said, f"{self.FILE}: the sentence must not hold {word!r}")

    def test_a_reference_is_six_capitals_or_digits_from_the_secrets_module(self):
        self.assertRegex(billing_page.reference(), r"^[0-9A-F]{6}$", f"{self.FILE}: reference() is secrets.token_hex(3), upper case")
        with mock.patch.object(billing_page.secrets, "token_hex", return_value="ab12cd") as token:
            self.assertEqual(billing_page.reference(), "AB12CD", f"{self.FILE}: reference() upper-cases")
        token.assert_called_once_with(3)

    def test_body_of_is_a_dict_whatever_the_answer_carries(self):
        self.assertEqual(billing_page.body_of(Answer(200, {"a": 1})), {"a": 1}, f"{self.FILE}: body_of() gives the body")
        self.assertEqual(billing_page.body_of(Answer(502, "text")), {}, f"{self.FILE}: body_of() gives an empty dict for a body that is not one")

    def test_an_unpaid_switch_is_remembered_and_forgotten(self):
        session = {}
        billing_page.remember_pending(session, "scale", INVOICE, now=1000)
        self.assertEqual(session, {billing_page.PENDING_KEY: {"plan": "scale", "interval": "month", "invoice_url": INVOICE, "at": 1000}},
                         f"{self.FILE}: remember_pending()")
        billing_page.remember_pending(session, "scale", "http://invoice.stripe.test/x", now=1000)
        self.assertIsNone(session[billing_page.PENDING_KEY]["invoice_url"], f"{self.FILE}: an invoice page that is not https is not kept")
        billing_page.forget_pending(session)
        billing_page.forget_pending(session)
        self.assertEqual(session, {}, f"{self.FILE}: forget_pending() removes the entry, and is no error when there is none")

    def test_the_hint_holds_only_while_it_is_true(self):
        entry = {"plan": "scale", "invoice_url": INVOICE, "at": 1000}
        shown = {"plan": "scale", "name": "Scale", "invoice_url": INVOICE}
        day = billing_page.PENDING_SECONDS
        cases = (
            ("a switch waiting for payment", dict(entry), "team", 1000 + 60, shown, False),
            ("the page after a day less a second", dict(entry), "team", 1000 + day - 1, shown, False),
            ("a day old", dict(entry), "team", 1000 + day, None, True),
            ("from the future", dict(entry, at=5000), "team", 1000, None, True),
            ("already on that plan", dict(entry), "scale", 1000 + 60, None, True),
            ("the workspace is on the free plan", dict(entry), "free", 1000 + 60, None, True),
            ("a plan that is not sold", dict(entry, plan="enterprise"), "team", 1000 + 60, None, True),
            ("no time on it", dict(entry, at=None), "team", 1000 + 60, None, True),
            ("an entry of another shape", "scale", "team", 1000 + 60, None, True),
            ("an invoice page that is not https", dict(entry, invoice_url="http://x"), "team", 1000 + 60, dict(shown, invoice_url=None), False),
        )
        for why, stored, current, now, expected, forgotten in cases:
            with self.subTest(why):
                session = {billing_page.PENDING_KEY: stored}
                self.assertEqual(billing_page.pending_for(session, current, now=now), expected, f"backend/dashboard/billing_page.py: pending_for(): {why}")
                self.assertEqual(billing_page.PENDING_KEY not in session, forgotten,
                                 f"backend/dashboard/billing_page.py: pending_for() forgets an entry that no longer holds, and only that: {why}")
        self.assertIsNone(billing_page.pending_for({}, "team"), "backend/dashboard/billing_page.py: pending_for() of a session with none is None")


class BillingPageTests(BillingCase):
    """The page at /app/billing/: the plan, the warnings, what is over a limit, and the ways to change plan."""

    def test_signed_out_visitors_are_sent_to_sign_in_from_every_billing_address(self):
        calls = self.no_stripe()
        visitor = Client()
        for method, path in (("get", self.billing), ("get", self.switch_url("scale")), ("post", self.switch_url("scale")),
                             ("post", self.checkout), ("post", self.portal)):
            with self.subTest(method=method, path=path):
                response = getattr(visitor, method)(path)
                self.assertEqual((response.status_code, response.get("Location")), (302, f"/accounts/login/?next={path}"),
                                 f"{VIEWS}: wrap the view of {path} in dashboard_view, so that a signed-out {method.upper()} goes to sign in")
        self.assertNoStripe(calls, "a signed-out visitor reached Stripe")

    def test_the_page_names_itself_and_is_kept_out_of_search(self):
        response = self.client.get(self.billing)
        html = response.content.decode()
        self.assertEqual(response.status_code, 200, f"{VIEWS}: billing answers a signed-in GET with 200")
        self.assertIn("<title>Billing | Veilfile</title>", html, "backend/pages/context_processors.py: the Billing section's entry gives the title")
        self.assertEqual(re.findall(r"<h1[ >][^<]*", main_of(html)), ["<h1>Billing"], f"{PAGE}: one <h1>Billing</h1>, inside <main>")
        for element in ("<header", "<footer"):
            self.assertNotIn(element, main_of(html), f"{PAGE}: no {element}> inside <main>; use a div")
        self.assertIn('<meta name="robots" content="noindex, nofollow">', html, "backend/templates/layouts/app.html: the layout's meta block keeps the page out of search, as well as the header")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow", f"{VIEWS}: billing is wrapped in dashboard_view")

    def test_a_free_workspace_is_offered_each_paid_plan_as_a_post_to_checkout(self):
        html = self.client.get(self.billing).content.decode()
        text = visible_text(html)
        self.assertIn("Current plan: Free", text, f"{PAGE}: say 'Current plan:' and the plan's name")
        upgrades = [form for form in forms_in(html) if form["attrs"].get("action") == self.checkout]
        self.assertEqual(
            [(form["attrs"].get("method"), value_of(form, "plan"), form["buttons"]) for form in upgrades],
            [("post", slug, [f"Upgrade to {name(slug)} (${price(slug)}/mo)"]) for slug in billing_page.paid_plans()]
            + [("post", slug, [f"Upgrade to {name(slug)} (${ANNUAL_PRICE_USD[slug]}/yr)"]) for slug in billing_page.paid_plans()],
            f"{PAGE}: one POST form to checkout for each paid plan monthly, then yearly, the plan in a hidden field and the price from plans.py")
        self.assertEqual([value_of(form, "interval") for form in upgrades], [None] * 3 + ["year"] * 3,
                         f"{PAGE}: a yearly form names the interval in a hidden field; a monthly one names none, as before")
        for form in upgrades:
            self.assertTrue(value_of(form, "csrfmiddlewaretoken"), f"{PAGE}: each form writes {{% csrf_token %}} itself")
        self.assertNotIn("Switch to", text, f"{PAGE}: a workspace with no subscription is offered an upgrade, not a switch")
        self.assertEqual([form for form in forms_in(html) if form["attrs"].get("action") == self.portal], [],
                         f"{PAGE}: no Manage billing where there is no billing account")
        self.assertIn(EXPLAIN_FREE, text, f"{PAGE}: keep React's sentence under the buttons")

    def test_a_subscriber_is_offered_the_other_plan_as_a_link_to_its_confirmation_page(self):
        self.subscribe("team")
        html = self.client.get(self.billing).content.decode()
        text = visible_text(html)
        switches = [(attrs.get("href"), label) for attrs, label in links_in(html) if "/billing/switch/" in attrs.get("href", "")]
        self.assertEqual(switches, [(self.switch_url(slug), f"Switch to {name(slug)} (${price(slug)}/mo)") for slug in billing_page.paid_plans() if slug != "team"]
                         + [(f"{self.switch_url(slug)}?interval=year", f"Switch to {name(slug)} (${ANNUAL_PRICE_USD[slug]}/yr)") for slug in billing_page.paid_plans()],
                         f"{PAGE}: a subscriber gets a link to the confirmation page of each other paid plan and price; a switch is never one click")
        self.assertEqual([form for form in forms_in(html) if form["attrs"].get("action") == self.checkout], [],
                         f"{PAGE}: a subscriber cannot start another checkout")
        portal = [form for form in forms_in(html) if form["attrs"].get("action") == self.portal]
        self.assertEqual([(form["attrs"].get("method"), form["buttons"], bool(value_of(form, "csrfmiddlewaretoken"))) for form in portal],
                         [("post", ["Manage billing"], True)], f"{PAGE}: Manage billing is a POST form with its own token")
        self.assertIn(EXPLAIN_SUBSCRIBED, text, f"{PAGE}: keep React's sentence under the buttons")
        self.assertNotIn("Upgrade to", text, f"{PAGE}: a subscriber is offered a switch, not an upgrade")

    def test_manage_billing_shows_only_where_a_billing_account_exists(self):
        for customer, shown in (("", False), ("cus_A", True)):
            with self.subTest(customer=customer):
                self.set_billing("free", customer=customer)
                html = self.client.get(self.billing).content.decode()
                self.assertEqual(any(form["attrs"].get("action") == self.portal for form in forms_in(html)), shown,
                                 f"{PAGE}: Manage billing is shown when the workspace has a Stripe customer (has_billing_account) and not otherwise")

    def test_the_status_is_named_beside_the_plan_in_the_customers_words(self):
        self.subscribe("team")
        self.assertIn("Current plan: Team (Active)", self.page(), f"{PAGE}: name the plan, then the subscription's status in plain words")
        self.set_billing("scale", customer="cus_A")
        Subscription.objects.filter(workspace=self.ws).delete()
        self.assertIn("Current plan: Scale", self.page(), f"{PAGE}: with no status there is no parenthesis")
        self.assertNotIn("Current plan: Scale (", self.page(), f"{PAGE}: no status, no parenthesis")

    def test_a_canceled_subscription_reads_canceled(self):
        self.assertEqual(billing_page.status_words("canceled"), "Canceled", "backend/dashboard/billing_page.py: the label for canceled is American")
        self.lapse("canceled")
        text = self.page()
        self.assertIn("Current plan: Free (Canceled)", text, f"{PAGE}: the status in plain words")
        self.assertNotIn("Cancelled", text, f"{PAGE}: American spelling, so Cancelled appears nowhere")

    def test_every_status_stripe_reports_has_its_own_plain_words(self):
        for status, words_ in STATUS_WORDS.items():
            with self.subTest(status=status):
                self.lapse(status)
                text = self.page()
                self.assertIn(f"Current plan: Free ({words_})", text, f"backend/dashboard/billing_page.py: status_words({status!r}) must be {words_!r}")
                self.assertNotIn(f"({status})", text, f"{PAGE}: the raw status {status!r} must not reach a customer")
                if "_" in status:
                    self.assertNotIn(status, text, f"{PAGE}: the raw code {status!r} must not appear anywhere on the page")
                self.assertNotIn("Stripe status", text, f"{PAGE}: no operator wording")

    def test_a_status_it_does_not_know_shows_nothing_extra(self):
        self.lapse("some_new_status")
        text = self.page()
        self.assertIn("Current plan: Free", text, f"{PAGE}: the plan is still named")
        self.assertNotIn("some_new_status", text, f"{PAGE}: an unknown status is not printed")
        self.assertNotIn("Current plan: Free (", text, f"{PAGE}: and leaves no empty parenthesis")

    def test_each_status_warning_shows_for_its_status_only(self):
        def warnings_shown():
            text = self.page()
            return sorted(key for key, sentence in WARNINGS.items() if sentence in text)

        self.subscribe("team", status="past_due")
        self.assertEqual(warnings_shown(), ["past_due"], f"{PAGE}: a failed payment that Stripe is retrying says so, and keeps the plan")
        for status in ("unpaid", "incomplete", "paused"):
            with self.subTest(status=status):
                self.lapse(status)
                self.assertEqual(warnings_shown(), [status], f"{PAGE}: a lapsed subscription named {status} gets its own sentence, and no other")
        self.subscribe("team", status="unpaid")
        self.assertEqual(warnings_shown(), [], f"{PAGE}: the three lapsed-subscription sentences say the workspace is on the Free plan, so they are for a workspace with no subscription (React's rule)")
        self.subscribe("team", status="active")
        self.assertEqual(warnings_shown(), [], f"{PAGE}: an active subscription has no warning")

    def test_every_limit_the_workspace_is_over_is_listed_in_the_react_dashboards_words(self):
        caps = PLANS["free"]
        self.go_over(keys=caps["keys"] + 2, storage=True, ttl=True)
        text = self.page()
        for sentence in (
            "Your workspace is over the Free plan's limits.",
            f"You have {caps['keys'] + 2} active API keys, and the plan allows {caps['keys']}. Existing keys keep working, but new keys can't be created until you revoke some or upgrade.",
            f"You have {caps['storage_gb'] + 1}.0 GB stored, and the plan allows {caps['storage_gb']} GB. Existing links keep working, but new uploads are refused until you're under the cap.",
            f"Some links expire later than the plan's {caps['ttl_days']}-day maximum. They keep their original expiry; new uploads get at most {caps['ttl_days']} days.",
        ):
            self.assertIn(sentence, text, "backend/templates/dashboard/over_limits.html: keep React's sentence")

    def test_only_the_limits_it_is_over_are_listed(self):
        self.go_over(keys=PLANS["free"]["keys"] + 1)
        text = self.page()
        self.assertIn("active API keys, and the plan allows", text, "backend/templates/dashboard/over_limits.html: the keys line shows when keys is over")
        for absent in ("stored, and the plan allows", "Some links expire later"):
            self.assertNotIn(absent, text, f"backend/templates/dashboard/over_limits.html: {absent!r} shows only when its own limit is over")

    def test_nothing_is_said_of_limits_within_them(self):
        self.subscribe("scale")
        self.assertNotIn("over the", self.page(), "backend/templates/dashboard/over_limits.html: draw nothing when over_plan_limits is empty")

    def test_the_page_shows_this_workspace_and_nobody_elses(self):
        other = get_user_model().objects.create_user(username="other", email="other@example.com", password="pw-123456-xyz")
        Workspace.objects.create(name="other", owner=other, plan="scale", stripe_customer_id="cus_B", stripe_subscription_id="sub_B")
        html = self.client.get(self.billing).content.decode()
        self.assertIn("Current plan: Free", visible_text(html), f"{VIEWS}: billing shows the signed-in user's workspace")
        self.assertNotIn("cus_B", html, f"{VIEWS}: another workspace's Stripe ids are never on the page")

    def test_a_visit_never_calls_stripe(self):
        self.subscribe("team")
        calls = self.no_stripe()
        self.client.get(self.billing)
        self.client.get(self.switch_url("scale"))
        self.client.get(self.checkout)
        self.client.get(self.portal)
        self.assertNoStripe(calls, "a GET of a billing address reached Stripe (a GET changes nothing and never creates a session)")


class PendingSwitchPanelTests(BillingCase):
    """A switch whose payment did not go through: what the page offers, and when the hint goes."""

    def setUp(self):
        super().setUp()
        self.subscribe("team")

    def test_an_unpaid_switch_with_an_invoice_page_offers_to_pay_it_or_keep_the_plan(self):
        self.pending()
        html = self.client.get(self.billing).content.decode()
        self.assertIn(PENDING_WITH_INVOICE, visible_text(html), f"{PAGE}: keep React's sentence for a switch waiting for payment")
        links = [(attrs, label) for attrs, label in links_in(html) if label.startswith("Complete payment")]
        self.assertEqual([(attrs.get("href"), attrs.get("target"), attrs.get("rel")) for attrs, _ in links], [(INVOICE, "_blank", "noopener noreferrer")],
                         f"{PAGE}: Complete payment opens the invoice page in a new tab, with rel='noopener noreferrer'")
        keep = [form for form in forms_in(html) if form["attrs"].get("action") == self.switch_url("team")]
        self.assertEqual([(form["attrs"].get("method"), form["buttons"], bool(value_of(form, "csrfmiddlewaretoken"))) for form in keep],
                         [("post", ["Keep Team"], True)], f"{PAGE}: Keep {{current plan}} is a POST of a switch to the current plan, with a token")

    def test_without_an_invoice_page_it_says_to_update_the_payment_method(self):
        self.pending(invoice_url=None)
        html = self.client.get(self.billing).content.decode()
        self.assertIn(PENDING_WITHOUT_INVOICE, visible_text(html), f"{PAGE}: React's other sentence, for a switch with no invoice page")
        self.assertEqual([label for _, label in links_in(html) if label.startswith("Complete payment")], [], f"{PAGE}: no Complete payment without an invoice page")
        self.assertIn("Keep Team", [button for form in forms_in(html) for button in form["buttons"]], f"{PAGE}: Keep stays")

    def test_an_invoice_page_that_is_not_https_is_never_linked(self):
        for url in ("http://invoice.stripe.test/x", "javascript:alert(1)", "//invoice.stripe.test/x"):
            with self.subTest(url=url):
                self.pending(invoice_url=url)
                html = self.client.get(self.billing).content.decode()
                self.assertNotIn(url, html, f"{PAGE}: only an https invoice page is linked; billing_page.https_only holds the rule")
                self.assertIn(PENDING_WITHOUT_INVOICE, visible_text(html), f"{PAGE}: with no usable address the sentence is the one without an invoice")

    def test_the_hint_goes_when_it_no_longer_holds(self):
        stale = {
            "the switch was applied (the workspace is on that plan)": {"plan": "team"},
            "a day has passed": {"at": int(time.time()) - billing_page.PENDING_SECONDS - 1},
            "the plan is not one that is sold": {"plan": "enterprise"},
            "the plan is not a plan name": {"plan": ["scale"]},
            "it has no time": {"at": None},
        }
        for why, changes in stale.items():
            with self.subTest(why):
                self.pending(**changes)
                self.assertNotIn("is waiting for payment", self.page(), f"{PAGE}: no panel once {why}")
                self.assertNotIn(billing_page.PENDING_KEY, self.client.session, f"backend/dashboard/billing_page.py: pending_for() forgets the entry once {why}")
        session = self.client.session
        session[billing_page.PENDING_KEY] = "scale"
        session.save()
        self.assertNotIn("is waiting for payment", self.page(), "an entry of another shape draws nothing, and is no error")
        self.assertNotIn(billing_page.PENDING_KEY, self.client.session, "backend/dashboard/billing_page.py: an entry of another shape is forgotten")

    def test_a_workspace_that_is_no_longer_on_a_paid_plan_has_no_panel(self):
        self.pending()
        self.set_billing("free", customer="cus_A")
        self.assertNotIn("is waiting for payment", self.page(), f"{PAGE}: a lapsed subscription has no switch waiting")
        self.assertNotIn(billing_page.PENDING_KEY, self.client.session, "backend/dashboard/billing_page.py: pending_for() forgets it")


class CheckoutTests(BillingCase):
    """POST /app/billing/checkout/: Stripe Checkout for a workspace with no subscription."""

    def test_a_get_never_starts_checkout(self):
        calls = self.no_stripe()
        response = self.client.get(self.checkout + "?plan=team")
        self.assertSentTo(response, self.billing, f"{VIEWS}: a GET of the checkout address does nothing and goes to Billing")
        self.assertNoStripe(calls, "a GET started a checkout")
        self.assertEqual(self.said(response), [], f"{VIEWS}: and says nothing")

    def test_a_post_creates_the_session_and_the_browser_goes_to_stripe_with_a_303(self):
        for slug in billing_page.paid_plans():
            with self.subTest(plan=slug):
                customer, lister, session = self.stripe_ready()
                response = self.client.post(self.checkout, {"plan": slug})
                self.assertEqual((response.status_code, response.get("Location")), (303, STRIPE_URL),
                                 f"{VIEWS}: billing_checkout answers 303 to the address Stripe returned")
                kwargs = session.call_args.kwargs
                self.assertEqual((kwargs["line_items"], kwargs["metadata"]), ([{"price": ENV[PRICE_VARIABLES[slug]], "quantity": 1}], {"plan": slug}),
                                 f"{VIEWS}: the plan the form named is the plan checked out")
                self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow", f"{VIEWS}: every dashboard response, a redirect too")

    def test_an_address_that_is_not_https_is_not_followed(self):
        customer, lister, session = self.stripe_ready()
        for url in ("http://checkout.stripe.test/x", "javascript:alert(1)"):
            with self.subTest(url=url):
                session.return_value = {"id": "cs_test_1", "url": url}
                response, sentence, record = self.refused(self.checkout, {"plan": "team"})
                self.assertReferenced(response, sentence, FAILED, record, "no https address to follow")
                self.assertNotIn(url, response.content.decode(), f"{VIEWS}: billing_checkout follows Stripe's address only if it starts with https://, and never prints it")

    def test_a_plan_that_is_not_sold_here_is_a_sentence_on_billing(self):
        calls = self.no_stripe()
        for data in ({"plan": "enterprise"}, {"plan": "free"}, {"plan": ""}, {"plan": "TEAM"}, {}):
            with self.subTest(data=data):
                response, sentence, record = self.refused(self.checkout, data)
                self.assertFixed(sentence, NO_SUCH_PLAN, record, logging.WARNING, "an unknown plan goes back to Billing with the sentence for a plan that is not sold")
        self.assertNoStripe(calls, "an unknown plan reached Stripe")

    def test_a_subscription_already_there_sends_the_visitor_back_with_the_advice_and_the_right_controls(self):
        self.set_billing("free", customer="cus_A")
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", "price_team")))
        session = self.patch_stripe("session")
        response, sentence, record = self.refused(self.checkout, {"plan": "scale"})
        self.assertFixed(sentence, ALREADY_SUBSCRIBED_MESSAGES["active"].replace("from the dashboard", "from this page"), record, logging.WARNING,
                         "a 409 already_subscribed goes back to Billing with the advice for the subscription's status")
        session.assert_not_called()
        text = self.page()
        self.assertIn("Current plan: Team", text, "the refusal brought the workspace up to date with Stripe, so Billing shows the subscription")
        self.assertIn("Switch to Scale", text, f"{PAGE}: and Billing now offers a switch, not an upgrade")

    def test_each_live_status_of_the_blocking_subscription_gets_its_own_advice(self):
        for status, phrase in ADVICE.items():
            with self.subTest(status=status):
                self.set_billing("free", customer="cus_A")
                self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", status, "price_team")))
                self.patch_stripe("session")
                response, sentence, record = self.refused(self.checkout, {"plan": "scale"})
                self.assertFixed(sentence, ALREADY_SUBSCRIBED_MESSAGES[status].replace("from the dashboard", "from this page"), record, logging.WARNING,
                                 f"the advice for a {status} subscription, not another status's")
                self.assertIn(phrase, sentence, f"{VIEWS}: the advice for {status}")
                for word in OPERATOR_WORDS:
                    self.assertNotIn(word, visible_text(response.content.decode()), f"{VIEWS}: the page must not show {word!r}")

    def test_billing_not_connected_is_a_sentence_with_a_reference(self):
        for missing in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_TEAM"):
            with self.subTest(missing=missing), mock.patch.dict(os.environ):
                os.environ.pop(missing)
                response, sentence, record = self.refused(self.checkout, {"plan": "team"})
                self.assertReferenced(response, sentence, NOT_CONFIGURED, record, "billing_not_configured")

    def test_a_stripe_error_is_a_sentence_with_a_reference_and_stripes_words_are_in_the_log(self):
        customer, lister, session = self.stripe_ready()
        session.side_effect = stripe.InvalidRequestError("Invalid line_items[0]: the product tax code is missing.", "line_items")
        response, sentence, record = self.refused(self.checkout, {"plan": "team"})
        self.assertReferenced(response, sentence, FAILED, record, "the product tax code is missing")
        self.assertIn("status=502 code=stripe_error", record.getMessage(), "backend/dashboard/billing_page.py: the log line names the status and the code")


class CsrfTests(BillingCase):
    """Every state change is a POST from the page's own form, with its token."""

    def test_a_post_without_the_token_is_the_form_not_sent_page_and_changes_nothing(self):
        self.subscribe("team")
        calls = self.no_stripe()
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        for path, data in ((self.checkout, {"plan": "scale"}), (self.portal, {}), (self.switch_url("scale"), {})):
            with self.subTest(path=path):
                response = strict.post(path, data)
                self.assertEqual(response.status_code, 403, f"{VIEWS}: {path} must be refused without a CSRF token")
                self.assertIn("Form not sent", response.content.decode(), "config/settings.py: CSRF_FAILURE_VIEW draws the form-not-sent page")
        self.assertNoStripe(calls, "a POST without a token reached Stripe")

    def test_the_forms_on_the_pages_carry_a_token_that_works(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        form = next(form for form in forms_in(strict.get(self.billing).content.decode()) if form["attrs"].get("action") == self.checkout)
        self.stripe_ready()
        response = strict.post(self.checkout, {"plan": value_of(form, "plan"), "csrfmiddlewaretoken": value_of(form, "csrfmiddlewaretoken")})
        self.assertEqual((response.status_code, response.get("Location")), (303, STRIPE_URL),
                         f"{PAGE}: the form's own csrfmiddlewaretoken must pass the check; write {{% csrf_token %}} inside the <form>")
        self.subscribe("team")
        confirm = forms_in(strict.get(self.switch_url("scale")).content.decode())
        self.assertEqual([bool(value_of(form, "csrfmiddlewaretoken")) for form in confirm], [True], f"{SWITCH_PAGE}: the confirmation form writes {{% csrf_token %}}")


class SwitchConfirmationTests(BillingCase):
    """GET /app/billing/switch/<plan>/: the sentences of React's switchConfirmText, on a page of their own."""

    def test_an_upgrade_says_what_is_charged_now_and_what_follows(self):
        self.subscribe("team")
        text = self.page(self.switch_url("scale"))
        for sentence in (
            f"Do you want to switch to Scale (${price('scale')}/mo)?",
            f"You'll be charged now for the prorated difference for the rest of this billing period, then ${price('scale')}/mo from your next "
            "renewal. If that payment doesn't go through, you stay on Team.",
        ):
            self.assertIn(sentence, text, f"{SWITCH_PAGE}: keep React's upgrade sentence, with the price from the view")

    def test_a_downgrade_says_the_unused_part_becomes_a_credit(self):
        self.subscribe("scale")
        text = self.page(self.switch_url("team"))
        for sentence in (
            f"Do you want to switch to Team (${price('team')}/mo)?",
            "The unused part of your current period becomes a credit on your next invoices, and Team limits apply right away. "
            "Existing API keys and links are not removed.",
        ):
            self.assertIn(sentence, text, f"{SWITCH_PAGE}: keep React's downgrade sentence")
        self.assertNotIn("charged now", text, f"{SWITCH_PAGE}: a downgrade is not charged")

    def test_the_figures_follow_plans_py(self):
        self.subscribe("team")
        with mock.patch.dict(PLANS, {"scale": {**PLANS["scale"], "price_usd": 99}}):
            text = self.page(self.switch_url("scale"))
        self.assertIn("Do you want to switch to Scale ($99/mo)?", text, f"{SWITCH_PAGE}: the heading's price comes from plans.py, never from the template")
        self.assertIn("then $99/mo from your next renewal", text, f"{SWITCH_PAGE}: and so does the sentence")

    def test_a_dearer_target_is_an_upgrade_whatever_it_is_called(self):
        self.subscribe("scale")
        with mock.patch.dict(PLANS, {"team": {**PLANS["team"], "price_usd": price("scale") + 1}}):
            text = self.page(self.switch_url("team"))
        self.assertIn("You'll be charged now for the prorated difference", text, "backend/dashboard/billing_page.py: is_upgrade() compares prices")
        self.assertIn("you stay on Scale.", text, f"{SWITCH_PAGE}: and names the plan the visitor is on")

    def test_the_page_is_a_form_page_on_the_shell_that_loads_as_an_ordinary_page(self):
        self.subscribe("team")
        response = self.client.get(self.switch_url("scale"))
        html = response.content.decode()
        self.assertTrue(html.lstrip().lower().startswith("<!doctype html>"), f"{SWITCH_PAGE}: the page must start with <!DOCTYPE html>, with nothing (a code fence) before it; got {html[:20]!r}")
        self.assertNotIn("```", html, f"{SWITCH_PAGE}: no markdown fence in the page")
        self.assertRegex(html, r'<body class="[^"]*\blayout-form\b', f"{SWITCH_PAGE}: extend layouts/form.html")
        self.assertNotIn("data-layout", html, f"{SWITCH_PAGE}: a confirmation page has no layout family: it loads as an ordinary page")
        self.assertIn("<title>Switch plan | Veilfile</title>", html, f"{SWITCH_PAGE}: fill {{% block title %}} with the page's name and | Veilfile")
        self.assertEqual(re.findall(r"<h1[ >]", main_of(html)).__len__(), 1, f"{SWITCH_PAGE}: one <h1>, inside <main>")
        self.assertIn('<meta name="robots" content="noindex, nofollow">', html, f"{SWITCH_PAGE}: noindex in the meta block")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow", f"{VIEWS}: billing_switch is wrapped in dashboard_view")
        forms = forms_in(html)
        self.assertEqual([(form["attrs"].get("method"), form["attrs"].get("action"), form["buttons"], bool(value_of(form, "csrfmiddlewaretoken"))) for form in forms],
                         [("post", None, ["Switch to Scale"], True)],
                         f"{SWITCH_PAGE}: one form that posts to its own address, with its token and one button")
        self.assertIn('class="action interactive"', html, f"{SWITCH_PAGE}: the main action is class='action interactive'")
        self.assertEqual([(attrs.get("href"), label) for attrs, label in links_in(html) if label == "Back to billing"], [(self.billing, "Back to billing")],
                         f"{SWITCH_PAGE}: a way back that changes nothing")

    def test_a_get_changes_nothing(self):
        self.subscribe("team")
        calls = self.no_stripe()
        self.pending()
        before = self.client.session[billing_page.PENDING_KEY]
        response = self.client.get(self.switch_url("scale"))
        self.assertEqual(response.status_code, 200, "the confirmation page opens for a subscriber")
        self.assertNoStripe(calls, "opening the confirmation page reached Stripe")
        self.assertEqual(self.reload().plan, "team", "opening the confirmation page changed the plan")
        self.assertEqual(self.client.session[billing_page.PENDING_KEY], before, "opening the confirmation page changed the session")

    def test_a_plan_that_is_not_sold_is_the_shells_404_for_a_get_and_a_post(self):
        self.subscribe("team")
        calls = self.no_stripe()
        for plan in ("free", "enterprise", "Team", "SCALE", "team-2", "0"):
            for method in ("get", "post"):
                with self.subTest(plan=plan, method=method):
                    response = getattr(self.client, method)(self.switch_url(plan))
                    self.assertEqual(response.status_code, 404, f"{VIEWS}: billing_switch gives the shell's 404 for the plan {plan!r}")
                    self.assertIn("404.html", [template.name for template in response.templates], f"{VIEWS}: the 404 is the shell's page (pages.errors.not_found)")
                    self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow", f"{VIEWS}: the 404 inside /app/ carries the header too")
        self.assertNoStripe(calls, "an unknown plan reached Stripe")

    def test_a_workspace_with_no_subscription_is_sent_back_with_a_sentence(self):
        calls = self.no_stripe()
        for plan in billing_page.paid_plans():
            with self.subTest(plan=plan):
                response = self.client.get(self.switch_url(plan))
                self.assertSentTo(response, self.billing, f"{VIEWS}: nothing to confirm without a subscription: back to Billing")
                self.assertEqual(self.said(response), [billing_page.NOT_SUBSCRIBED], f"{VIEWS}: with the sentence that says so")
        self.assertNoStripe(calls, "the confirmation page reached Stripe")

    def test_the_plan_the_workspace_is_on_is_sent_back_with_a_sentence(self):
        self.subscribe("team")
        response = self.client.get(self.switch_url("team"))
        self.assertSentTo(response, self.billing, f"{VIEWS}: nothing to confirm for the plan already held: back to Billing")
        self.assertEqual(self.said(response), ["You're already on the Team plan."], f"{VIEWS}: with the sentence for an unchanged plan")


class SwitchPostTests(BillingCase):
    """POST /app/billing/switch/<plan>/: the switch, and what each answer of the billing code becomes."""

    def post(self, plan):
        return self.client.post(self.switch_url(plan))

    def test_an_upgrade_goes_to_stripe_and_the_result_is_a_line_on_billing(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", "price_team")))
        modify = self.patch_stripe("modify", return_value=make_sub("sub_A", "active", "price_scale"))
        self.pending()
        response = self.post("scale")
        self.assertSentTo(response, self.billing, f"{VIEWS}: a switch POST goes back to Billing")
        self.assertEqual(self.said(response), ["You're now on the Scale plan."], f"{VIEWS}: with React's sentence for a switch")
        self.assertEqual(modify.call_args.kwargs["items"], [{"id": "si_1", "price": "price_scale"}], "the price swapped is the plan's")
        self.assertEqual(self.reload().plan, "scale", "the plan changes through the billing code's read of Stripe, as the JSON endpoint's does")
        self.assertNotIn(billing_page.PENDING_KEY, self.client.session, f"{VIEWS}: a switch Stripe accepted settles any unpaid one")

    def test_the_line_is_on_the_billing_page_after_the_redirect(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", "price_team")))
        self.patch_stripe("modify", return_value=make_sub("sub_A", "active", "price_scale"))
        response = self.client.post(self.switch_url("scale"), follow=True)
        self.assertEqual(response.redirect_chain, [(self.billing, 302)], f"{VIEWS}: POST, redirect, GET")
        self.assertIn("You're now on the Scale plan.", visible_text(response.content.decode()), "backend/templates/layouts/app.html: shows the messages above the page")
        self.assertIn("Current plan: Scale", visible_text(response.content.decode()), "and the page shows the new plan")

    def test_the_plan_already_held_is_unchanged(self):
        self.subscribe("team")
        modify = self.patch_stripe("modify")
        response = self.post("team")
        self.assertEqual(self.said(response), ["You're already on the Team plan."], f"{VIEWS}: React's sentence for an unchanged plan")
        modify.assert_not_called()

    def test_keeping_the_plan_cancels_a_pending_switch(self):
        self.set_billing("team", sub_id="sub_A")
        current = make_sub("sub_A", "active", "price_team", pending_update=PENDING)
        current["latest_invoice"] = "in_pending"
        self.patch_stripe("list", return_value=sub_list(current))
        void = self.patch_stripe("void", return_value={"id": "in_pending", "status": "void"})
        self.pending()
        response = self.post("team")
        self.assertEqual(self.said(response), ["The pending switch was canceled. You stay on the Team plan."], f"{VIEWS}: React's sentence")
        void.assert_called_once_with("in_pending")
        self.assertNotIn(billing_page.PENDING_KEY, self.client.session, f"{VIEWS}: the hint goes with the switch")

    def test_a_payment_that_did_not_go_through_keeps_the_invoice_page_for_billing(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", "price_team")))
        updated = make_sub("sub_A", "active", "price_team", pending_update=PENDING)
        updated["latest_invoice"] = {"id": "in_pending", "object": "invoice", "status": "open", "hosted_invoice_url": INVOICE}
        self.patch_stripe("modify", return_value=updated)
        response, sentence, record = self.refused(self.switch_url("scale"))
        self.assertFixed(sentence, PAYMENT_FAILED, record, logging.WARNING, "a 402 goes back to Billing with one fixed sentence")
        self.assertNotIn(INVOICE, sentence, f"{VIEWS}: the sentence is not the API's, which points at the invoice page")
        entry = self.client.session[billing_page.PENDING_KEY]
        self.assertEqual((entry["plan"], entry["invoice_url"]), ("scale", INVOICE),
                         f"{VIEWS}: the hint holds the plan the visitor asked for (the 402 body's plan is the current one) and the invoice page")
        self.assertAlmostEqual(entry["at"], time.time(), delta=60, msg=f"{VIEWS}: and when it was made")
        self.assertEqual(self.reload().plan, "team", "the plan is unchanged")
        self.assertIn(PENDING_WITH_INVOICE, visible_text(response.content.decode()), "and Billing offers to pay the invoice or keep the plan")

    def test_a_payment_that_did_not_go_through_with_no_invoice_page(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", "price_team")))
        updated = make_sub("sub_A", "active", "price_team", pending_update=PENDING)
        updated["latest_invoice"] = "in_pending"
        self.patch_stripe("modify", return_value=updated)
        response, sentence, record = self.refused(self.switch_url("scale"))
        self.assertFixed(sentence, PAYMENT_FAILED, record, logging.WARNING, "the same sentence with no invoice page")
        self.assertIsNone(self.client.session[billing_page.PENDING_KEY]["invoice_url"], f"{VIEWS}: no invoice page, none kept")
        self.assertIn(PENDING_WITHOUT_INVOICE, visible_text(response.content.decode()), "and Billing says to update the payment method")

    def test_an_invoice_page_that_is_not_https_is_not_kept(self):
        self.set_billing("team", sub_id="sub_A")
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", "price_team")))
        updated = make_sub("sub_A", "active", "price_team", pending_update=PENDING)
        updated["latest_invoice"] = {"id": "in_pending", "object": "invoice", "status": "open", "hosted_invoice_url": "http://invoice.stripe.test/x"}
        self.patch_stripe("modify", return_value=updated)
        self.refused(self.switch_url("scale"))
        self.assertIsNone(self.client.session[billing_page.PENDING_KEY]["invoice_url"], f"{VIEWS}: only an https invoice page is kept")

    def test_a_refusal_keeps_the_hint(self):
        self.set_billing("team", sub_id="sub_A", status="past_due")
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "past_due", "price_team")))
        modify = self.patch_stripe("modify")
        self.pending()
        response, sentence, record = self.refused(self.switch_url("scale"))
        self.assertFixed(sentence, PAST_DUE, record, logging.WARNING, "a 409 goes back to Billing with the sentence for a failed payment")
        modify.assert_not_called()
        self.assertIn(billing_page.PENDING_KEY, self.client.session,
                      f"{VIEWS}: a refusal keeps the hint: clearing it would take away Keep exactly when the visitor must try again")

    def test_no_subscription_is_a_sentence_and_a_missing_customer_is_forgotten(self):
        self.set_billing("team", customer="cus_TESTMODE", sub_id="sub_test")
        self.patch_stripe("list", side_effect=missing_customer_error())
        response, sentence, record = self.refused(self.switch_url("scale"))
        self.assertFixed(sentence, NOT_SUBSCRIBED, record, logging.WARNING, "a customer Stripe cannot find is the sentence that says to upgrade, not to use checkout")
        self.assertEqual(self.reload().stripe_customer_id, "", "the billing code forgot the customer, as it does for the JSON endpoint")
        self.assertNotIn("Manage billing", [button for form in forms_in(self.client.get(self.billing).content.decode()) for button in form["buttons"]],
                         f"{PAGE}: and the next page has no Manage billing")

    def test_a_stripe_error_is_a_sentence_with_a_reference_and_changes_nothing(self):
        self.subscribe("team")
        self.patch_stripe("modify", side_effect=stripe.APIConnectionError("connection reset"))
        response, sentence, record = self.refused(self.switch_url("scale"))
        self.assertReferenced(response, sentence, FAILED, record, "connection reset")
        self.assertIn("where=switch", record.getMessage(), "backend/dashboard/billing_page.py: the log line names the action")
        self.assertEqual(self.reload().plan, "team", "the plan is unchanged")

    def test_billing_not_connected_is_a_sentence_with_a_reference(self):
        self.subscribe("team")
        with mock.patch.dict(os.environ):
            os.environ.pop("STRIPE_SECRET_KEY")
            response, sentence, record = self.refused(self.switch_url("scale"))
        self.assertReferenced(response, sentence, NOT_CONFIGURED, record, "billing_not_configured")


class PortalTests(BillingCase):
    """POST /app/billing/portal/: the Stripe customer portal."""

    def test_a_get_never_opens_the_portal(self):
        self.subscribe("team")
        calls = self.no_stripe()
        response = self.client.get(self.portal)
        self.assertSentTo(response, self.billing, f"{VIEWS}: a GET of the portal address does nothing and goes to Billing")
        self.assertNoStripe(calls, "a GET opened the portal")

    def test_a_post_sends_the_browser_to_the_portal_with_a_303(self):
        self.subscribe("team")
        portal = self.patch_stripe("portal", return_value={"url": PORTAL_URL})
        response = self.client.post(self.portal)
        self.assertEqual((response.status_code, response.get("Location")), (303, PORTAL_URL), f"{VIEWS}: billing_portal answers 303 to the address Stripe returned")
        self.assertEqual(portal.call_args.kwargs, {"customer": "cus_A", "return_url": "https://veilfile.test/app/"},
                         "the portal is opened for this workspace's customer, and returns to /app/ as the JSON endpoint says")

    def test_an_address_that_is_not_https_is_not_followed(self):
        self.subscribe("team")
        portal = self.patch_stripe("portal")
        for url in ("http://billing.stripe.test/x", "javascript:alert(1)"):
            with self.subTest(url=url):
                portal.return_value = {"url": url}
                response, sentence, record = self.refused(self.portal)
                self.assertReferenced(response, sentence, FAILED, record, "no https address to follow")
                self.assertNotIn(url, response.content.decode(), f"{VIEWS}: billing_portal follows Stripe's address only if it starts with https://, and never prints it")

    def test_without_a_billing_account_the_visitor_is_told(self):
        self.set_billing("free", customer="")
        portal = self.patch_stripe("portal")
        response, sentence, record = self.refused(self.portal)
        self.assertFixed(sentence, NO_BILLING_ACCOUNT, record, logging.WARNING, "a 400 no_billing_account goes back to Billing with a sentence, not the bare code")
        portal.assert_not_called()

    def test_a_customer_stripe_cannot_find_is_forgotten_and_the_button_goes(self):
        self.set_billing("team", customer="cus_TESTMODE", sub_id="sub_test")
        self.patch_stripe("portal", side_effect=missing_customer_error())
        response, sentence, record = self.refused(self.portal)
        self.assertFixed(sentence, NO_BILLING_ACCOUNT, record, logging.WARNING, "the billing code answers 400 no_billing_account")
        self.assertEqual(self.reload().stripe_customer_id, "", "and forgets the customer")
        html = self.client.get(self.billing).content.decode()
        self.assertEqual([form for form in forms_in(html) if form["attrs"].get("action") == self.portal], [], f"{PAGE}: the next page has no Manage billing")

    def test_billing_not_connected_is_a_sentence_with_a_reference(self):
        self.subscribe("team")
        with mock.patch.dict(os.environ):
            os.environ.pop("STRIPE_SECRET_KEY")
            response, sentence, record = self.refused(self.portal)
        self.assertReferenced(response, sentence, NOT_CONFIGURED, record, "billing_not_configured")

    def test_a_stripe_error_is_a_sentence_with_a_reference(self):
        self.subscribe("team")
        self.patch_stripe("portal", side_effect=stripe.APIConnectionError("connection reset"))
        response, sentence, record = self.refused(self.portal)
        self.assertReferenced(response, sentence, FAILED, record, "connection reset")
        self.assertIn("where=portal", record.getMessage(), "backend/dashboard/billing_page.py: the log line names the action")


class UnexpectedAnswerTests(BillingCase):
    """An answer the page has never seen is a sentence with a reference on Billing, never a 500."""

    def test_an_answer_the_page_does_not_know_is_still_a_sentence(self):
        cases = (
            ("checkout", self.checkout, {"plan": "team"}, (Answer(500, {"error": "boom"}), Answer(429, "slow down"), Answer(200, {}))),
            ("portal", self.portal, {}, (Answer(500, {"error": "boom"}), Answer(429, "slow down"), Answer(200, {}))),
            ("switch", self.switch_url("scale"), {}, (Answer(500, {"error": "boom"}), Answer(429, "slow down"))),
        )
        for function, path, data, answers in cases:
            for answer in answers:
                with self.subTest(function=function, status=answer.status, body=answer.body), mock.patch.object(billing_account, function, return_value=answer):
                    response, sentence, record = self.refused(path, data)
                    self.assertReferenced(response, sentence, FAILED, record, "")
                    self.assertIn(f"status={answer.status} ", record.getMessage(), f"{VIEWS}: billing_{function} logs the status it did not know")


    def test_an_exception_that_is_not_stripes_is_still_a_sentence_with_a_reference(self):
        cases = (
            ("checkout", self.checkout, {"plan": "team"}),
            ("portal", self.portal, {}),
            ("switch", self.switch_url("scale"), {}),
        )
        for function, path, data in cases:
            with self.subTest(function=function), mock.patch.object(billing_account, function, side_effect=RuntimeError("secret boom text")):
                response, sentence, record = self.refused(path, data)
                self.assertReferenced(response, sentence, FAILED, record, "")
                self.assertIsNotNone(record.exc_info, f"{VIEWS}: billing_{function} logs the traceback of an exception it did not expect")
                self.assertEqual(record.exc_info[0], RuntimeError, f"{VIEWS}: the traceback is the exception's own")
                self.assertNotIn("secret boom text", response.content.decode(), f"{VIEWS}: the exception's text never reaches the page")
                self.assertNotEqual(response.status_code, 500, f"{VIEWS}: never a 500")

    def test_a_failure_inside_the_stripe_calls_is_the_same_sentence(self):
        self.set_billing("free", customer="")
        self.patch_stripe("list", return_value=sub_list())
        self.patch_stripe("customer", side_effect=RuntimeError("secret boom text"))
        response, sentence, record = self.refused(self.checkout, {"plan": "team"})
        self.assertReferenced(response, sentence, FAILED, record, "")
        self.assertIsNotNone(record.exc_info, f"{VIEWS}: the traceback is logged")


class NothingIsCachedTests(BillingCase):
    """Every /app/ answer is kept out of the browser's cache, so that Back after Sign out shows nothing of the account."""

    def test_every_section_and_every_confirmation_page_sends_no_store(self):
        from dashboard.tests_keys import connect, make_app
        self.subscribe("team")
        key, _ = APIKey.create_key(self.ws, "agent")
        artifact = Artifact.objects.create(workspace=self.ws, filename="a.txt", size_bytes=1, expires_at=timezone.now() + timedelta(days=3))
        app = make_app("Claude Code")
        connect(self.user, app)
        pages = [reverse(name) for name in ("app", "app-files", "app-keys", "app-apps", "app-billing")] + [
            reverse("app-file-revoke", args=[artifact.id]), reverse("app-key-revoke", args=[key.id]),
            reverse("app-app-disconnect", args=[app.pk]), self.switch_url("scale"),
        ]
        for path in pages:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, f"{path} answered {response.status_code}, not 200")
                control = response.get("Cache-Control", "")
                self.assertIn("no-store", control, f"{VIEWS}: dashboard_view must send Cache-Control: no-store on {path}")
                self.assertIn("private", control, f"{VIEWS}: and private")


class AddressTests(SimpleTestCase):
    longMessage = False

    def test_the_four_addresses_and_the_views_behind_them(self):
        for url_name, args, path, view in (
            ("app-billing", [], "/app/billing/", views.billing),
            ("app-billing-switch", ["team"], "/app/billing/switch/team/", views.billing_switch),
            ("app-billing-checkout", [], "/app/billing/checkout/", views.billing_checkout),
            ("app-billing-portal", [], "/app/billing/portal/", views.billing_portal),
        ):
            with self.subTest(url_name=url_name):
                self.assertEqual(reverse(url_name, args=args), path, f"backend/dashboard/urls.py: {url_name} is {path}")
                self.assertIs(resolve(path).func, view, f"backend/dashboard/urls.py: {path} is served by dashboard.views.{view.__name__}")

    def test_every_billing_address_marks_billing_in_the_dashboards_menu(self):
        for url_name in ("app-billing", "app-billing-switch", "app-billing-checkout", "app-billing-portal"):
            with self.subTest(url_name=url_name):
                self.assertEqual(registry.DASHBOARD_SECTIONS.get(url_name), "app-billing",
                                 f"backend/pages/registry.py: DASHBOARD_SECTIONS maps {url_name} to app-billing, so the confirmation page draws the dashboard's menu with Billing marked")


class WordsTests(SimpleTestCase):
    """The voice, ASCII and no typed figures, for the files this task wrote."""
    longMessage = False

    def sources(self):
        found = {f"backend/templates/{name}": (TEMPLATES / name).read_text(encoding="utf-8") for name in OWN_TEMPLATES}
        found["backend/dashboard/billing_page.py"] = (Path(settings.BASE_DIR) / "dashboard" / "billing_page.py").read_text(encoding="utf-8")
        return found

    def test_the_sentences_keep_the_voice(self):
        sentences = " ".join(value for key, value in vars(billing_page).items() if key.isupper() and isinstance(value, str))
        texts = dict(self.sources(), **{"the constants of backend/dashboard/billing_page.py": sentences})
        for where, text in texts.items():
            with self.subTest(file=where):
                self.assertNotIn("\u2014", text, f"{where}: no em dashes; use a colon, a comma or two sentences")
                if where.endswith(".html") or where.startswith("the constants"):
                    self.assertNotIn("!", text, f"{where}: no exclamation marks")
                for word in BANNED:
                    self.assertNotIn(word, text.lower(), f"{where}: avoid {word.strip()!r} (AGENTS.md, voice)")

    def test_no_sentence_speaks_for_the_operator(self):
        """Stripe's own text, an API code and the word 'instance' go to the log, never to a visitor (the owner's rule)."""
        sentences = {f"billing_page.{key}": value for key, value in vars(billing_page).items()
                     if key.isupper() and isinstance(value, str) and key != "PENDING_KEY"}
        advice = {f"advice for {status}": billing_page.already_subscribed_sentence(status) for status in ALREADY_SUBSCRIBED_MESSAGES}
        templates = {where: text for where, text in self.sources().items() if where.endswith(".html")}
        for where, text in dict(sentences, **advice, **templates).items():
            for word in OPERATOR_WORDS:
                with self.subTest(file=where, word=word):
                    self.assertNotIn(word, text, f"{where}: {word!r} is the operator's word; say it in the customer's, and log the detail (billing_page.refusal)")

    def test_the_templates_are_ascii(self):
        for where, text in self.sources().items():
            with self.subTest(file=where):
                self.assertTrue(text.isascii(), f"{where}: ASCII only. A character outside it must be in all three fonts (pages/tests_content.py); the middle dot of React's badge is not needed")

    def test_no_price_or_cap_is_typed(self):
        typed = re.compile(r"\$\s?\d|\b\d[\d,]*[\s-]*(?:GB|uploads?|days?|keys?)\b")
        for where, text in self.sources().items():
            stripped = re.sub(r"{#.*?#}", "", text) if where.endswith(".html") else text
            with self.subTest(file=where):
                self.assertIsNone(typed.search(stripped), f"{where}: a price or a cap is typed here. Take it from drop_core/plans.py (AGENTS.md, Pricing changes)")
