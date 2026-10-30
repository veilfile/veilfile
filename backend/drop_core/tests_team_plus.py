"""Team Plus in plans.py, and its caps enforced on the same paths and with the same answers as every other plan.

The spec is the founder's: $6 a month, 4,000 uploads a month, links up to 90 days, 40 GB of storage and 10 API keys. Over a
cap, Team Plus answers as Team and Scale do: 402 key_limit_reached, 402 storage_cap_reached and 429 monthly_cap_reached.
"""

import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from drop_billing import account as billing_account
from drop_core.models import APIKey, Artifact, EmailVerification, UsageCounter, Workspace, get_or_create_workspace
from drop_core.plans import GB, PLANS, plan_name
from drop_core.tests import _month, _use_local_dir

TEAM_PLUS = {"uploads": 4000, "ttl_days": 90, "keys": 10, "price_usd": 6, "storage_gb": 40}


class TeamPlusPlanTests(SimpleTestCase):
    def test_the_entry_is_the_founders_spec(self):
        self.assertEqual(PLANS["team_plus"], TEAM_PLUS)

    def test_the_entry_has_the_shape_of_the_other_plans(self):
        for slug, caps in PLANS.items():
            with self.subTest(plan=slug):
                self.assertEqual(set(caps), set(TEAM_PLUS))

    def test_the_plans_are_listed_cheapest_first(self):
        self.assertEqual(list(PLANS), ["free", "team", "team_plus", "scale"])

    def test_the_other_plans_are_unchanged(self):
        self.assertEqual(PLANS["free"], {"uploads": 100, "ttl_days": 7, "keys": 1, "price_usd": 0, "storage_gb": 1})
        self.assertEqual(PLANS["team"], {"uploads": 2000, "ttl_days": 90, "keys": 5, "price_usd": 4, "storage_gb": 25})
        self.assertEqual(PLANS["scale"], {"uploads": 10000, "ttl_days": 365, "keys": None, "price_usd": 12, "storage_gb": 100})

    def test_its_name_is_team_plus(self):
        self.assertEqual([plan_name(slug) for slug in PLANS], ["Free", "Team", "Team Plus", "Scale"])

    def test_a_workspace_can_hold_it(self):
        self.assertEqual(Workspace._meta.get_field("plan").choices,
                         [("free", "Free"), ("team", "Team"), ("team_plus", "Team Plus"), ("scale", "Scale")])


class TeamPlusOnThePagesTests(TestCase):
    """The words a visitor reads, written out rather than computed with plan_name, so a wrong name cannot pass both sides."""

    def test_the_plans_chapter_has_a_team_plus_card(self):
        html = self.client.get("/plans/").content.decode()
        card = html[html.index('<h2 class="plan__name">Team Plus</h2>'):]
        card = card[:card.index("</article>")]
        for words in ("$6 a month", "4,000 uploads a month", "Links last up to 90 days", "40 GB of storage", "10 keys",
                      'href="/accounts/signup/?plan=team_plus"', "Choose Team Plus"):
            self.assertIn(words, card)
        self.assertIn("switch between Team, Team Plus and Scale", html)
        self.assertIn('"name": "Team Plus"', html, "the Plans chapter's structured data offers Team Plus")

    def test_the_checkout_step_names_team_plus_and_its_price(self):
        user = get_user_model().objects.create_user(username="buyer", password="pw12345678")
        self.client.force_login(user)
        html = self.client.get("/accounts/signup/?plan=team_plus").content.decode()
        self.assertIn("<h1>Subscribe to the Team Plus plan ($6/mo, tax included)</h1>", html)

    def test_billing_offers_a_free_workspace_team_plus(self):
        user = get_user_model().objects.create_user(username="buyer", password="pw12345678")
        self.client.force_login(user)
        html = self.client.get("/app/billing/").content.decode()
        self.assertIn("Upgrade to Team Plus ($6/mo)", html)
        self.assertIn('<input type="hidden" name="plan" value="team_plus">', html)
        self.assertNotIn("Team_Plus", html)


class TeamPlusEnforcementTests(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        _use_local_dir(self, self.tmpdir)
        self.user = get_user_model().objects.create_user(username="plus", password="pw12345678")
        EmailVerification.objects.create(user=self.user, email="plus@example.com", is_verified=True, verified_at=timezone.now())
        self.workspace = get_or_create_workspace(self.user)
        self.workspace.plan = "team_plus"
        self.workspace.save()
        self.key, raw_key = APIKey.create_key(self.workspace, "agent-key")
        self.client = APIClient()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {raw_key}"}

    def upload(self, content=b"hello veilfile", **data):
        file = SimpleUploadedFile("note.txt", content, content_type="text/plain")
        return self.client.post("/api/v1/artifacts/", {"file": file, **data}, **self.auth)

    def seed(self, size_bytes):
        Artifact.objects.create(workspace=self.workspace, api_key=self.key, filename="seed.txt", mime_type="text/plain",
                                size_bytes=size_bytes, expires_at=timezone.now() + timedelta(days=30))

    def test_ten_keys_and_the_eleventh_is_a_402(self):
        self.client.force_login(self.user)
        for n in range(2, 11):
            response = self.client.post("/api/v1/keys/", {"name": f"key-{n}"}, format="json")
            self.assertEqual(response.status_code, 201, f"key {n} of 10")
        self.assertEqual(APIKey.objects.filter(workspace=self.workspace, is_active=True).count(), 10)
        response = self.client.post("/api/v1/keys/", {"name": "key-11"}, format="json")
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()["code"], "key_limit_reached")
        self.assertEqual(APIKey.objects.filter(workspace=self.workspace, is_active=True).count(), 10)

    def test_storage_is_capped_hard_at_40_gb(self):
        content = b"hello veilfile"
        self.seed(40 * GB - len(content))
        self.assertEqual(self.upload(content).status_code, 201, "an upload that lands exactly on the cap is stored")
        response = self.upload(b"x")
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()["code"], "storage_cap_reached")

    def test_uploads_are_capped_at_4000_a_month(self):
        UsageCounter.objects.create(workspace=self.workspace, year_month=_month(), uploads=3999)
        self.assertEqual(self.upload().status_code, 201, "upload 4,000 of the month is stored")
        response = self.upload()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["code"], "monthly_cap_reached")

    def test_links_last_at_most_90_days(self):
        response = self.upload(ttl_days=91)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["ttl_adjusted"])
        expires = Artifact.objects.get(id=response.json()["id"]).expires_at
        self.assertLessEqual(expires, timezone.now() + timedelta(days=90, seconds=60))
        self.assertGreater(expires, timezone.now() + timedelta(days=89))
        response = self.upload(ttl_days=90)
        self.assertNotIn("ttl_adjusted", response.json(), "90 days is within the plan")

    def test_usage_reports_the_team_plus_caps(self):
        body = self.client.get("/api/v1/usage/", **self.auth).json()
        self.assertEqual(
            {k: body[k] for k in ("plan", "uploads_cap", "ttl_days", "keys_cap", "storage_cap_bytes")},
            {"plan": "team_plus", "uploads_cap": 4000, "ttl_days": 90, "keys_cap": 10, "storage_cap_bytes": 40 * GB},
        )

    def test_a_downgrade_to_team_is_over_its_limits_and_nothing_is_revoked(self):
        for n in range(2, 11):
            APIKey.create_key(self.workspace, f"key-{n}")
        self.seed(30 * GB)
        self.workspace.plan = "team"
        self.workspace.save()
        status = billing_account.status(self.user).body
        self.assertEqual(status["over_plan_limits"], ["keys", "storage"])
        self.assertEqual(APIKey.objects.filter(workspace=self.workspace, is_active=True).count(), 10)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post("/api/v1/keys/", {"name": "more"}, format="json").status_code, 402)
