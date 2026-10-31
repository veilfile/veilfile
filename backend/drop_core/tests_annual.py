"""Yearly billing in plans.py and on the public pages, and every cap enforced the same whichever way a plan is paid.

The founder's facts (2026-10-30): Team $40 a year, Team Plus $60 and Scale $120, tax-inclusive, ten months' price, so two months
are free. Only the price and the billing interval change: a workspace on a yearly price is on the same plan, with the same caps, and
over a cap it gets the same answers (402 key_limit_reached, 402 storage_cap_reached, 429 monthly_cap_reached, a TTL clamped to the
plan's maximum). The plan comes from Stripe-verified state only (invariant 3), so the workspaces below are put on their plan by a
signed webhook, with Stripe mocked as in drop_billing.tests_fix_billing.
"""

import os
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from drop_billing import account as billing_account
from drop_billing.tests_annual import ANNUAL_ENV, PRICES
from drop_billing.tests_fix_billing import FixBillingBase, checkout_completed, make_sub, sub_list
from drop_core.models import APIKey, Artifact, EmailVerification, UsageCounter
from drop_core.plans import ANNUAL_PRICE_USD, GB, PLANS, plan_name
from drop_core.tests import _month, _use_local_dir
from pages.tests_chapters import visible_text

PLANS_PY = "backend/drop_core/plans.py"


class AnnualPricesTests(SimpleTestCase):
    longMessage = False

    def test_the_yearly_prices_are_the_founders(self):
        self.assertEqual(ANNUAL_PRICE_USD, {"team": 40, "team_plus": 60, "scale": 120}, f"{PLANS_PY}: ANNUAL_PRICE_USD")

    def test_a_year_costs_ten_months_so_two_months_are_free(self):
        for slug, yearly in ANNUAL_PRICE_USD.items():
            with self.subTest(plan=slug):
                self.assertEqual(yearly, 10 * PLANS[slug]["price_usd"],
                                 f"{PLANS_PY}: a yearly price is ten monthly ones; change both, and the Stripe prices, together")

    def test_every_paid_plan_and_only_a_paid_plan_has_one(self):
        self.assertEqual(list(ANNUAL_PRICE_USD), list(billing_account.PAID_PLANS), f"{PLANS_PY}: one yearly price for each plan sold")

    def test_the_monthly_plans_are_unchanged(self):
        self.assertEqual(PLANS, {
            "free": {"uploads": 100, "ttl_days": 7, "keys": 1, "price_usd": 0, "storage_gb": 1},
            "team": {"uploads": 2000, "ttl_days": 90, "keys": 5, "price_usd": 4, "storage_gb": 25},
            "team_plus": {"uploads": 4000, "ttl_days": 90, "keys": 10, "price_usd": 6, "storage_gb": 40},
            "scale": {"uploads": 10000, "ttl_days": 365, "keys": None, "price_usd": 12, "storage_gb": 100},
        }, f"{PLANS_PY}: yearly billing adds prices and changes no plan, cap or monthly price")


class AnnualOnThePagesTests(TestCase):
    """The words a visitor reads, written out rather than computed, so a wrong figure cannot pass both sides."""
    longMessage = False

    def test_each_paid_card_on_the_plans_chapter_has_its_yearly_price_and_choice(self):
        html = self.client.get("/plans/").content.decode()
        for name, slug, words in (("Team", "team", "$40 a year, two months free"), ("Team Plus", "team_plus", "$60 a year, two months free"),
                                  ("Scale", "scale", "$120 a year, two months free")):
            with self.subTest(plan=name):
                card = html[html.index(f'<h2 class="plan__name">{name}</h2>'):]
                card = card[:card.index("</article>")]
                self.assertIn(f'<p class="plan__price label">{words}</p>', card, "backend/pages/plan_cards.py: the card's yearly price")
                self.assertIn(f'href="/accounts/signup/?plan={slug}&amp;interval=year"', card,
                              "backend/templates/components/plan.html: a link into sign-up with the yearly price chosen")
                self.assertIn(f'Choose yearly<span class="visually-hidden"> for {name}</span></a>', card,
                              "backend/templates/components/plan.html: the link names its plan to a screen reader")
                self.assertIn(f"Choose {name}</a>", card, "backend/pages/plan_cards.py: the monthly choice stays")
        free = html[html.index('<h2 class="plan__name">Free</h2>'):]
        free = free[:free.index("</article>")]
        self.assertNotIn("a year", free, "backend/pages/plan_cards.py: Free has no yearly price")
        self.assertNotIn("interval=year", free, "backend/pages/plan_cards.py: nor a yearly choice")
        self.assertIn("Team, Team Plus and Scale can also be paid yearly at the price of ten months, so two months are free.",
                      visible_text(html), "backend/templates/chapters/plans.html: say what yearly billing is")

    def test_the_checkout_step_names_the_yearly_price(self):
        user = get_user_model().objects.create_user(username="buyer", password="pw12345678")
        self.client.force_login(user)
        for query, heading in (("?plan=team_plus&interval=year", "Subscribe to the Team Plus plan ($60/yr, tax included)"),
                               ("?plan=team_plus", "Subscribe to the Team Plus plan ($6/mo, tax included)"),
                               ("?plan=team_plus&interval=weekly", "Subscribe to the Team Plus plan ($6/mo, tax included)")):
            with self.subTest(query=query):
                html = self.client.get(f"/accounts/signup/{query}").content.decode()
                self.assertIn(f"<h1>{heading}</h1>", html, "backend/templates/registration/checkout_confirm.html: the price the view passes")

    def test_the_sign_up_page_names_the_yearly_price(self):
        for query, sentence in (
            ("?plan=team&interval=year", "You're signing up for the Team plan ($40/yr, tax included), billed yearly with two months free."),
            ("?plan=team_plus&interval=year", "You're signing up for the Team Plus plan ($60/yr, tax included), billed yearly with two months free."),
            ("?plan=scale&interval=year", "You're signing up for the Scale plan ($120/yr, tax included), billed yearly with two months free."),
            ("?plan=scale", "You're signing up for the Scale plan ($12/mo, tax included)."),
        ):
            with self.subTest(query=query):
                text = visible_text(self.client.get(f"/accounts/signup/{query}").content.decode())
                self.assertIn(sentence, text, "backend/drop_core/views.py: _plan_blurb()")
        text = visible_text(self.client.get("/accounts/signup/?plan=free&interval=year").content.decode())
        self.assertNotIn("signing up for", text, "backend/drop_core/views.py: Free has no yearly price, so ?interval=year changes nothing")


class AnnualEnforcementTests(FixBillingBase):
    """A workspace put on each plan by Stripe, on its monthly and on its yearly price: the same caps, the same answers over them."""
    longMessage = False

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, ANNUAL_ENV)
        env.start()
        self.addCleanup(env.stop)
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        _use_local_dir(self, self.tmpdir)
        EmailVerification.objects.create(user=self.user, email=self.user.email, is_verified=True, verified_at=timezone.now())

    def put_on(self, plan, interval):
        """The workspace, emptied, on `plan` billed every `interval`, from a paid checkout's webhook and Stripe's read-back."""
        APIKey.objects.filter(workspace=self.ws).delete()
        Artifact.objects.filter(workspace=self.ws).delete()
        UsageCounter.objects.filter(workspace=self.ws).delete()
        self.set_billing("free", customer="cus_A")
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", PRICES[interval][plan])))
        self.assertEqual(self.post_event(checkout_completed(plan=plan)).status_code, 200)
        self.assertEqual(self.reload().plan, plan, f"backend/drop_billing/account.py: the {interval}ly price puts the workspace on {plan}")
        self.key, raw = APIKey.create_key(self.ws, "agent-key")
        self.agent = APIClient()
        self.agent.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")

    def upload(self, content=b"hello veilfile", **data):
        return self.agent.post("/api/v1/artifacts/", {"file": SimpleUploadedFile("note.txt", content, content_type="text/plain"), **data})

    def answers_over_each_cap(self, plan):
        """What the workspace is told at and over each cap of `plan`, as (what, status, code or detail)."""
        caps = PLANS[plan]
        found = []
        usage = self.agent.get("/api/v1/usage/").json()
        found.append(("usage", {k: usage[k] for k in ("plan", "uploads_cap", "ttl_days", "keys_cap", "storage_cap_bytes")}))
        # Keys: the cap's last key is made, the next is refused (Scale has no cap).
        made = [self.api.post("/api/v1/keys/", {"name": f"key-{n}"}, format="json").status_code for n in range(2, (caps["keys"] or 12) + 1)]
        over = self.api.post("/api/v1/keys/", {"name": "one-more"}, format="json")
        found.append(("keys", set(made), over.status_code, over.json().get("code") if over.status_code != 201 else None))
        # Storage: an upload that lands on the cap is stored, the next byte is refused.
        content = b"hello veilfile"
        Artifact.objects.create(workspace=self.ws, api_key=self.key, filename="seed.txt", mime_type="text/plain",
                                size_bytes=caps["storage_gb"] * GB - len(content), expires_at=timezone.now() + timedelta(days=30))
        at_cap = self.upload(content).status_code
        over = self.upload(b"x")
        found.append(("storage", at_cap, over.status_code, over.json()["code"]))
        Artifact.objects.filter(workspace=self.ws).delete()
        # Uploads: the month's last upload is stored, the next is refused.
        UsageCounter.objects.update_or_create(workspace=self.ws, year_month=_month(), defaults={"uploads": caps["uploads"] - 1})
        last = self.upload().status_code
        over = self.upload()
        found.append(("uploads", last, over.status_code, over.json()["code"]))
        UsageCounter.objects.filter(workspace=self.ws).delete()
        # TTL: a link asked for past the plan's maximum is clamped to it.
        response = self.upload(ttl_days=caps["ttl_days"] + 1)
        expires = Artifact.objects.get(id=response.json()["id"]).expires_at
        found.append(("ttl", response.status_code, response.json().get("ttl_adjusted"),
                      timezone.now() + timedelta(days=caps["ttl_days"] - 1) < expires <= timezone.now() + timedelta(days=caps["ttl_days"], seconds=60)))
        return found

    def test_each_plan_enforces_the_same_caps_with_the_same_answers_monthly_and_yearly(self):
        for plan in billing_account.PAID_PLANS:
            caps = PLANS[plan]
            expected = [
                ("usage", {"plan": plan, "uploads_cap": caps["uploads"], "ttl_days": caps["ttl_days"], "keys_cap": caps["keys"],
                           "storage_cap_bytes": caps["storage_gb"] * GB}),
                ("keys", {201}, 201, None) if caps["keys"] is None else ("keys", {201}, 402, "key_limit_reached"),
                ("storage", 201, 402, "storage_cap_reached"),
                ("uploads", 201, 429, "monthly_cap_reached"),
                ("ttl", 201, True, True),
            ]
            answers = {}
            for interval in ("month", "year"):
                with self.subTest(plan=plan, interval=interval):
                    self.put_on(plan, interval)
                    answers[interval] = self.answers_over_each_cap(plan)
                    self.assertEqual(answers[interval], expected, f"{PLANS_PY}: {plan} billed {interval}ly enforces its caps from PLANS")
            self.assertEqual(answers["year"], answers["month"], f"{plan}: a yearly subscriber is told exactly what a monthly one is")

    def test_a_downgrade_from_yearly_scale_to_yearly_team_is_over_its_limits_and_nothing_is_revoked(self):
        self.put_on("scale", "year")
        for n in range(6):
            APIKey.create_key(self.ws, f"key-{n}")
        Artifact.objects.create(workspace=self.ws, api_key=self.key, filename="big.bin", mime_type="text/plain",
                                size_bytes=30 * GB, expires_at=timezone.now() + timedelta(days=200))
        modify = self.patch_stripe("modify", return_value=make_sub("sub_A", "active", PRICES["year"]["team"]))
        self.assertEqual(self.api.post("/api/v1/billing/switch", {"plan": "team", "interval": "year"}, format="json").status_code, 200)
        self.assertEqual(modify.call_args.kwargs["items"], [{"id": "si_1", "price": PRICES["year"]["team"]}],
                         "backend/drop_billing/account.py: the switch swaps Scale's yearly price for Team's")
        status = self.api.get("/api/v1/billing/status").json()
        self.assertEqual((status["plan"], status["billing_interval"], status["over_plan_limits"]), ("team", "year", ["keys", "storage", "ttl"]),
                         "backend/drop_billing/account.py: over Team's limits as a monthly downgrade is")
        self.assertEqual(APIKey.objects.filter(workspace=self.ws, is_active=True).count(), 7, "nothing is revoked on a downgrade")
        self.assertEqual(self.api.post("/api/v1/keys/", {"name": "more"}, format="json").status_code, 402)
