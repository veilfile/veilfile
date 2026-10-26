"""Overview (spec 4.8): the first page of the dashboard, at /app/.

In this order: the notice a visitor returns from Stripe with (?billing=), the prompt to verify an email, anything over a limit, the plan,
the meters (uploads this month, storage, API keys, how long links last) and the account (its email, whether it is verified, and the links
to change the email and the password). The numbers come from the account functions that the JSON endpoints answer with (drop_core.account.usage
and drop_billing.account.status, which makes no Stripe call); the words are the template's. A read that fails leaves out its own part and
nothing else.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it): a failed assertIn on a page would print the page before the
message, so each message names the file to fix and what was found.
"""
import re
import socket
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone

from drop_core.account import Answer
from drop_core.models import APIKey, Artifact, EmailVerification, UsageCounter, get_or_create_workspace
from drop_core.plans import PLANS
from drop_core.tests_reset_verify_pages import Tree, in_main, words
from pages.tests_chapters import BANNED

User = get_user_model()
PASSWORD = "pw12345678"
GB = 1024**3
VIEW = "backend/dashboard/overview_page.py"
PAGE = "backend/templates/dashboard/overview.html"
OVER = "backend/templates/dashboard/over_limits.html"

# The sentences the React dashboard said, kept word for word where they still apply (the skeleton's copy rule).
SUCCESS = "Your subscription is updated. It can take a moment for the new plan to appear below. Refresh if it doesn't."
CANCELLED = "Checkout was canceled. No charges were made."
ERROR = "We couldn't start checkout{plan}. Nothing was charged. Try again from Billing."
ALREADY = "You already have a subscription, so no new checkout was started. Switch plans or manage billing."
VERIFY = "Verify your email to create API keys."
KEYS_OVER = "You have {n} active API keys, and the plan allows {cap}. Existing keys keep working, but new keys can't be created until you revoke some or upgrade."
STORAGE_OVER = "You have {used} stored, and the plan allows {cap} GB. Existing links keep working, but new uploads are refused until you're under the cap."
TTL_OVER = "Some links expire later than the plan's {days}-day maximum. They keep their original expiry; new uploads get at most {days} days."
UPLOADS_OVER = "You have made {used} uploads this month, and the plan allows {cap}. Existing links keep working, but new uploads are refused until the next month begins (UTC)."
BILLING_DOWN = "Plan limits could not be checked just now. Reload the page to check again."
USAGE_DOWN = "Usage could not be loaded just now. Reload the page to try again."


def make_user(name, email="", verified=None, plan="free"):
    """A user with a workspace on `plan`. `verified` is None for no verification record, else whether the record is verified."""
    user = User.objects.create_user(username=name, email=email, password=PASSWORD)
    workspace = get_or_create_workspace(user)
    if plan != "free":
        workspace.plan = plan
        workspace.save(update_fields=["plan"])
    if verified is not None:
        EmailVerification.objects.create(user=user, email=email, is_verified=verified, verified_at=timezone.now() if verified else None)
    return user, workspace


def this_month():
    return timezone.now().strftime("%Y-%m")  # the upload counter's month is the UTC month


def artifact(workspace, name, size, days):
    return Artifact.objects.create(workspace=workspace, filename=name, size_bytes=size, expires_at=timezone.now() + timedelta(days=days))


class Overview:
    """A response of /app/ read as a visitor reads it."""

    def __init__(self, response):
        self.response = response
        self.html = response.content.decode()
        self.tree = Tree(self.html)
        self.main = self.tree.all("main")[0]
        self.text = words(self.main)

    def classed(self, name, tag=None):
        return [e for e in self.tree.elements if in_main(e) and name in e["attrs"].get("class", "").split() and tag in (None, e["tag"])]

    def notices(self):
        """(role, words) of each .notice in <main>, in order."""
        return [(e["attrs"].get("role"), words(e)) for e in self.classed("notice")]

    def sections(self):
        """{heading: words of its section}, in order: every <section> of <main> is named by its h2."""
        ids = self.tree.ids()
        return {words(ids[e["attrs"]["aria-labelledby"]]): words(e) for e in self.tree.all("section") if in_main(e)}

    def meters(self):
        """[(label, figures)] in order."""
        return [(words(label), words(figures)) for label, figures in zip(self.classed("meter__label"), self.classed("meter__figures"))]

    def bars(self):
        return [e["attrs"] for e in self.tree.elements if e["attrs"].get("role") == "meter"]

    def links(self):
        return [(words(e), e["attrs"].get("href")) for e in self.tree.all("a") if in_main(e)]


class OverviewTestCase(TestCase):
    longMessage = False

    def open(self, user, query=""):
        client = Client()
        client.force_login(user)
        response = client.get("/app/" + query)
        self.assertEqual(response.status_code, 200, f"{PAGE}: /app/ answers 200 for a signed-in visitor, found {response.status_code}")
        return Overview(response)


class OverviewPageTests(OverviewTestCase):
    def test_one_h1_overview_then_the_plan_the_usage_and_the_account(self):
        user, _ = make_user("ov-shape", "shape@example.com", verified=True)
        page = self.open(user)
        h1s = page.tree.all("h1")
        self.assertEqual([words(h) for h in h1s], ["Overview"], f"{PAGE}: one <h1>, Overview, inside <main>")
        self.assertTrue(in_main(h1s[0]))
        self.assertEqual(list(page.sections()), ["Plan", "Usage", "Account"], f"{PAGE}: three sections, each a <section aria-labelledby> named by its <h2>, in this order")
        for element in page.tree.elements:
            if element["tag"] in ("header", "footer"):
                self.assertFalse(in_main(element), f"{PAGE}: no <{element['tag']}> inside <main> (spec 9)")

    def test_the_page_is_kept_out_of_search(self):
        user, _ = make_user("ov-robots", "robots@example.com", verified=True)
        page = self.open(user)
        self.assertIn('<meta name="robots" content="noindex, nofollow">', page.html, "backend/templates/layouts/app.html: every dashboard page carries the noindex tag, which the layout puts in the head's meta block (Task 2)")
        self.assertEqual(page.response["X-Robots-Tag"], "noindex, nofollow", "backend/dashboard/views.py: overview is wrapped in dashboard_view")

    def test_a_fresh_free_account_shows_its_four_figures(self):
        user, _ = make_user("ov-fresh", "fresh@example.com", verified=True)
        page = self.open(user)
        self.assertEqual(page.meters(), [("Successful uploads this month", "0 of 100"), ("Storage", "0 B of 1.0 GB"), ("API keys", "0 of 1"), ("Links last up to", "7 days")],
                         f"{PAGE}: the four figures, from usage (drop_core.account.usage)")
        self.assertEqual(len(page.bars()), 3, f"{PAGE}: uploads, storage and keys have a bar; how long links last is a value with no limit")
        for bar in page.bars():
            self.assertIn(bar["aria-labelledby"], page.tree.ids(), f"{PAGE}: each bar is named by its label")
        self.assertIn("Uploads are counted by calendar month, in UTC.", page.sections()["Usage"], f"{PAGE}: the uploads figure says that its month is the UTC month")
        self.assertIn("You are on the Free plan.", page.sections()["Plan"])
        self.assertIn(("Billing", "/app/billing/"), page.links(), f"{PAGE}: the plan section links to Billing")

    def test_a_team_account_with_some_use(self):
        user, workspace = make_user("ov-team", "team@example.com", verified=True, plan="team")
        UsageCounter.objects.create(workspace=workspace, year_month=this_month(), uploads=1234)
        UsageCounter.objects.create(workspace=workspace, year_month="1999-01", uploads=99)  # another month is not this month's figure
        APIKey.create_key(workspace, "ci")
        APIKey.create_key(workspace, "laptop")
        artifact(workspace, "report.pdf", GB * 3 // 2, 30)
        page = self.open(user)
        self.assertEqual(page.meters(), [("Successful uploads this month", "1,234 of 2,000"), ("Storage", "1.5 GB of 25.0 GB"), ("API keys", "2 of 5"), ("Links last up to", "90 days")])
        self.assertIn("You are on the Team plan.", page.sections()["Plan"])
        self.assertNotIn("Your workspace is over", page.text, f"{VIEW}: nothing is over, so there are no limit lines")
        self.assertEqual(page.notices(), [], f"{PAGE}: a clean account has no notice")

    def test_a_scale_account_has_unlimited_keys_and_no_bar_for_them(self):
        user, workspace = make_user("ov-scale", "scale@example.com", verified=True, plan="scale")
        APIKey.create_key(workspace, "one")
        APIKey.create_key(workspace, "two")
        page = self.open(user)
        self.assertEqual(page.meters()[2:], [("API keys", "2 of unlimited"), ("Links last up to", "365 days")])
        self.assertEqual(page.meters()[0], ("Successful uploads this month", "0 of 10,000"))
        self.assertEqual(len(page.bars()), 2, f"{PAGE}: with no key limit there is no bar for keys: pass unlimited=True to the meter")

    def test_the_figures_are_the_visitors_own(self):
        _, other = make_user("ov-other", "other@example.com", verified=True)
        for i in range(3):
            APIKey.create_key(other, f"key {i}")
        user, _ = make_user("ov-mine", "mine@example.com", verified=True)
        page = self.open(user)
        self.assertEqual(page.meters()[2], ("API keys", "0 of 1"), f"{VIEW}: the figures are the signed-in user's workspace, nobody else's")

    def test_everything_comes_in_the_order_the_spec_gives(self):
        user, workspace = make_user("ov-order", "order@example.com", verified=False)
        for i in range(2):
            APIKey.create_key(workspace, f"key {i}")
        page = self.open(user, "?billing=error&plan=scale")
        marks = ["We couldn't start checkout for the Scale plan.", VERIFY, "Your workspace is over the Free plan's limits.", "You are on the Free plan.",
                 "Successful uploads this month", "not verified."]
        found = [page.text.find(mark) for mark in marks]
        self.assertTrue(all(i >= 0 for i in found), f"{PAGE}: all of {marks} are on this page; found at {found}")
        self.assertEqual(found, sorted(found), f"{PAGE}: the return notice, the verify prompt, what is over a limit, the plan, the meters and the account, in that order; found at {found}")
        self.assertLess(page.text.index("Overview"), found[0], f"{PAGE}: the h1 comes first")


class OverLimitTests(OverviewTestCase):
    def over_text(self, page):
        found = [words(e) for e in page.classed("notice") if "Your workspace is over" in words(e)]
        self.assertEqual(len(found), 1, f"{PAGE}: one block says the workspace is over, found {len(found)}: {found}")
        return found[0]

    def test_a_workspace_over_nothing_says_nothing(self):
        user, workspace = make_user("ov-within", "within@example.com", verified=True)
        APIKey.create_key(workspace, "only")
        page = self.open(user)
        self.assertNotIn("Your workspace is over", page.text, f"{OVER}: the lines are drawn only when something is over")

    def test_after_a_downgrade_every_limit_is_named_first_in_the_old_words(self):
        user, workspace = make_user("ov-down", "down@example.com", verified=True)
        for i in range(3):
            APIKey.create_key(workspace, f"key {i}")
        artifact(workspace, "big.bin", 2 * GB, 300)
        UsageCounter.objects.create(workspace=workspace, year_month=this_month(), uploads=150)
        page = self.open(user)
        block = page.classed("notice")[0]
        self.assertEqual(block["attrs"].get("role"), "status", f'{OVER}: the lines are a .notice with role="status", the first notice of the page')
        said = words(block)
        self.assertTrue(said.startswith("Your workspace is over the Free plan's limits."), f"{OVER}: it opens with the old sentence, naming the plan; found {said!r}")
        for sentence in (KEYS_OVER.format(n=3, cap=1), STORAGE_OVER.format(used="2.0 GB", cap=1), TTL_OVER.format(days=7), UPLOADS_OVER.format(used=150, cap=100)):
            self.assertIn(sentence, said, f"{OVER}: keep the sentence {sentence!r}")
        self.assertLess(page.text.index("Your workspace is over"), page.text.index("You are on the Free plan."), f"{PAGE}: anything over a limit comes before the plan (spec 4.8)")
        self.assertNotIn("section", [p["tag"] for p in block["parents"]], f"{PAGE}: the lines are not inside a section: they come first")
        self.assertEqual([label for label, figures in page.meters() if "Over the limit" in figures], ["Successful uploads this month", "Storage", "API keys"],
                         f"{PAGE}: the meters that are over say so in words")
        self.assertEqual((workspace.api_keys.filter(is_active=True).count(), workspace.artifacts.count()), (3, 1), "nothing is revoked or deleted for being over")

    def test_each_limit_has_a_line_of_its_own(self):
        cases = {}
        user, workspace = make_user("ov-keys", "keys@example.com", verified=True)
        APIKey.create_key(workspace, "a")
        APIKey.create_key(workspace, "b")
        cases["keys"] = (user, [KEYS_OVER.format(n=2, cap=1)])
        user, workspace = make_user("ov-storage", "storage@example.com", verified=True)
        artifact(workspace, "big.bin", 2 * GB, 3)
        cases["storage"] = (user, [STORAGE_OVER.format(used="2.0 GB", cap=1)])
        user, workspace = make_user("ov-ttl", "ttl@example.com", verified=True)
        artifact(workspace, "slow.bin", 10, 30)
        cases["ttl"] = (user, [TTL_OVER.format(days=7)])
        user, workspace = make_user("ov-uploads", "uploads@example.com", verified=True)
        UsageCounter.objects.create(workspace=workspace, year_month=this_month(), uploads=101)
        cases["uploads"] = (user, [UPLOADS_OVER.format(used=101, cap=100)])
        for name, (user, sentences) in cases.items():
            with self.subTest(over=name):
                said = self.over_text(self.open(user))
                for sentence in sentences:
                    self.assertIn(sentence, said, f"{OVER}: the {name} line is {sentence!r}")
                self.assertEqual(said.count(", and the plan allows") + said.count("Some links expire"), 1, f"{OVER}: only the {name} line, found {said!r}")

    def test_the_heading_names_the_plan_the_workspace_is_on(self):
        user, workspace = make_user("ov-teamover", "teamover@example.com", verified=True, plan="team")
        for i in range(6):
            APIKey.create_key(workspace, f"key {i}")
        said = self.over_text(self.open(user))
        self.assertTrue(said.startswith("Your workspace is over the Team plan's limits."), f"{OVER}: the plan is named as the old dashboard named it; found {said!r}")
        self.assertIn(KEYS_OVER.format(n=6, cap=5), said)

    def test_the_over_limit_heading_names_the_plan_with_a_capital(self):
        user, workspace = make_user("ov-capital", "capital@example.com", verified=True, plan="team")
        for i in range(6):
            APIKey.create_key(workspace, f"key {i}")
        said = self.over_text(self.open(user))
        self.assertIn("Team plan's limits", said, f"{OVER}: the plan name goes through |title; found {said!r}")
        self.assertNotIn("team plan's limits", said, f"{OVER}: the plan name is never lowercase in the heading")

    def test_one_upload_is_singular(self):
        for used, phrase, wrong in ((1, "1 upload this month", "1 uploads"), (2, "2 uploads this month", None)):
            with self.subTest(used=used):
                html = render_to_string("dashboard/over_limits.html", {"usage": {"plan": "free", "uploads_used": used, "uploads_cap": 0}, "status": None})
                said = " ".join(html.split())
                self.assertIn(phrase, said, f"{OVER}: {used} upload(s) uses the right number")
                if wrong:
                    self.assertNotIn(wrong, said, f"{OVER}: one upload is singular")


class BillingReturnTests(OverviewTestCase):
    """?billing= is the address Stripe and sign up send people back with (drop_billing/views.py): four values, four notices."""

    def setUp(self):
        super().setUp()
        self.user, _ = make_user("ov-return", "return@example.com", verified=True)

    def test_each_of_the_four_says_what_the_old_dashboard_said(self):
        cases = {
            "success": ("status", SUCCESS),
            "cancelled": ("status", CANCELLED),
            "error": ("alert", ERROR.format(plan="")),
            "already_subscribed": ("status", ALREADY),
        }
        for value, (role, sentence) in cases.items():
            with self.subTest(billing=value):
                page = self.open(self.user, f"?billing={value}")
                self.assertEqual(page.notices(), [(role, sentence)], f'{PAGE}: ?billing={value} shows one .notice, role="{role}": {sentence!r}')

    def test_the_sentences_that_pointed_below_point_at_billing(self):
        page = self.open(self.user, "?billing=error")
        self.assertIn(("Billing", "/app/billing/"), page.links(), f"{PAGE}: Billing is another page now, so the error notice links to it instead of saying below")
        page = self.open(self.user, "?billing=already_subscribed")
        self.assertIn(("Switch plans or manage billing", "/app/billing/"), page.links())

    def test_any_other_value_shows_nothing(self):
        for value in ("", "nope", "SUCCESS", "success%20", "error%00", "<script>", "success&billing=x"):
            with self.subTest(billing=value):
                page = self.open(self.user, f"?billing={value}")
                self.assertEqual(page.notices(), [], f"{VIEW}: only success, cancelled, error and already_subscribed are notices")

    def test_the_error_names_the_plan_only_when_it_is_a_paid_plan_of_plans_py(self):
        for plan, name in (("team", " for the Team plan"), ("scale", " for the Scale plan")):
            with self.subTest(plan=plan):
                self.assertEqual(self.open(self.user, f"?billing=error&plan={plan}").notices(), [("alert", ERROR.format(plan=name))])
        for plan in ("free", "", "TEAM", "team%20", "enterprise", "%3Cscript%3Ealert(1)%3C/script%3E", "..%2Fx", "team%00", "constructor"):
            with self.subTest(plan=plan):
                page = self.open(self.user, f"?billing=error&plan={plan}")
                self.assertEqual(page.notices(), [("alert", ERROR.format(plan=""))], f"{VIEW}: a plan that is not a paid plan of drop_core/plans.py is not named")
                for raw in ("enterprise", "alert(1)", "constructor", "../x"):
                    self.assertNotIn(raw, page.html, f"{VIEW}: the value of ?plan= is never printed; the name comes from plans.py")

    def test_the_paid_plans_are_the_ones_plans_py_has(self):
        pro = {**PLANS["team"], "price_usd": 9}
        with mock.patch.dict(PLANS, {"pro": pro}):
            page = self.open(self.user, "?billing=error&plan=pro")
        self.assertEqual(page.notices(), [("alert", ERROR.format(plan=" for the Pro plan"))],
                         f"{VIEW}: check ?plan= against plans.PLANS (a plan with a price), not against a list written in the view")

    def test_the_other_notices_ignore_the_plan(self):
        self.assertEqual(self.open(self.user, "?billing=success&plan=team").notices(), [("status", SUCCESS)])

    def test_a_notice_is_not_shown_on_a_page_without_the_query(self):
        self.assertEqual(self.open(self.user).notices(), [])


class VerifyPromptTests(OverviewTestCase):
    def prompts(self, page):
        return [(role, said) for role, said in page.notices() if said.startswith(VERIFY)]

    def test_no_address_at_all_asks_for_one(self):
        user, _ = make_user("ov-noaddr")
        page = self.open(user)
        self.assertEqual(self.prompts(page), [(None, f"{VERIFY} Add an email address to your account. Verify email")], f"{PAGE}: the old sentence for an account with no address")
        self.assertIn(("Verify email", "/accounts/verify-email/"), page.links())

    def test_an_unverified_address_says_where_the_link_went(self):
        user, _ = make_user("ov-unver", "unver@example.com", verified=False)
        page = self.open(user)
        self.assertEqual(self.prompts(page), [(None, f"{VERIFY} We sent a link to unver@example.com. Verify email")])

    def test_a_changed_address_names_the_one_the_link_went_to(self):
        user, _ = make_user("ov-moved", "old@example.com", verified=True)
        EmailVerification.objects.filter(user=user).update(email="new@example.com", is_verified=False, verified_at=None)  # as get_or_create_verification leaves it
        page = self.open(user)
        self.assertEqual(self.prompts(page), [(None, f"{VERIFY} We sent a link to new@example.com. Verify email")],
                         f"{VIEW}: the link went to the address on the verification record, which is not user.email until it is verified")

    def test_a_verified_address_has_no_prompt(self):
        user, _ = make_user("ov-ver", "ver@example.com", verified=True)
        self.assertEqual(self.prompts(self.open(user)), [])

    def test_the_prompt_is_page_content_and_not_a_live_message(self):
        user, _ = make_user("ov-quiet", "quiet@example.com", verified=False)
        element = self.open(user).classed("notice")[0]
        self.assertNotIn("role", element["attrs"], f'{PAGE}: the prompt is not a result that just happened, so it has no role="status"')

    def test_an_address_is_escaped(self):
        user, _ = make_user("ov-esc2", '"><b>x</b>@example.com', verified=False)
        page = self.open(user)
        self.assertNotIn("<b>x</b>", page.html, f"{PAGE}: print the address escaped")
        self.assertIn("&lt;b&gt;x&lt;/b&gt;@example.com", page.html)


class AccountLineTests(OverviewTestCase):
    def account(self, page):
        return page.sections()["Account"]

    def test_a_verified_address(self):
        user, _ = make_user("ov-acc1", "acc1@example.com", verified=True)
        self.assertIn("acc1@example.com is verified.", self.account(self.open(user)))

    def test_an_unverified_address(self):
        user, _ = make_user("ov-acc2", "acc2@example.com", verified=False)
        self.assertIn("acc2@example.com is not verified.", self.account(self.open(user)))

    def test_an_account_with_no_address_says_so_and_offers_to_add_one(self):
        user, _ = make_user("ov-acc3")
        page = self.open(user)
        self.assertIn("No email address is set.", self.account(page))
        self.assertIn(("Add email", "/accounts/verify-email/"), page.links(), f"{PAGE}: with no address the link says Add email")
        self.assertNotIn("Change email", page.text)

    def test_change_email_and_change_password_are_quiet_links_to_their_pages(self):
        user, _ = make_user("ov-acc4", "acc4@example.com", verified=True)
        page = self.open(user)
        self.assertIn(("Change email", "/accounts/verify-email/"), page.links())
        self.assertIn(("Change password", "/accounts/password_change/"), page.links())
        quiet = [e for e in page.tree.all("a") if in_main(e) and words(e) in ("Change email", "Change password")]
        self.assertEqual([e["attrs"]["class"] for e in quiet], ["label interactive"] * 2, f'{PAGE}: secondary links are class="label interactive" in a .form-links line (spec 4.7)')

    def test_the_longest_address_is_printed_whole_once_in_the_account_line(self):
        address = "a" * 242 + "@example.com"
        self.assertEqual(len(address), 254)
        user, _ = make_user("ov-acc5", address, verified=True)
        page = self.open(user)
        self.assertEqual(page.html.count(address), 1, f"{PAGE}: the address is printed once, whole (the column wraps it: the layout's overflow-wrap)")

    def test_the_account_line_needs_no_usage(self):
        user, _ = make_user("ov-acc6", "acc6@example.com", verified=True)
        with mock.patch("drop_core.account.usage", side_effect=RuntimeError("boom")), self.assertLogs("dashboard.overview_page", "ERROR"):
            page = self.open(user)
        self.assertIn("acc6@example.com is verified.", self.account(page), f"{VIEW}: the address and its state come from the account, not from the usage read")


class FailureTests(OverviewTestCase):
    """The two reads are separate. One that fails leaves out its own part and puts a sentence there; nothing else goes, and no detail of the failure is printed."""

    def setUp(self):
        super().setUp()
        self.user, self.workspace = make_user("ov-fail", "fail@example.com", verified=True)
        for i in range(3):
            APIKey.create_key(self.workspace, f"key {i}")

    def test_a_billing_status_that_raises_does_not_hide_the_usage(self):
        with mock.patch("drop_billing.account.status", side_effect=RuntimeError("boom: secret detail")), self.assertLogs("dashboard.overview_page", "ERROR") as logs:
            page = self.open(self.user)
        self.assertEqual(page.meters()[2], ("API keys", "3 of 1 Over the limit"), f"{VIEW}: the meters come from usage alone")
        self.assertEqual(len(page.bars()), 3)
        self.assertIn((("status"), BILLING_DOWN), page.notices(), f'{PAGE}: say that the limits could not be checked, as a role="status" notice, where the lines would be')
        self.assertNotIn("Your workspace is over", page.text, f"{PAGE}: with no status there are no limit lines")
        self.assertIn("You are on the Free plan.", page.sections()["Plan"], f"{VIEW}: the plan comes from usage when the status is down")
        for detail in ("boom", "secret detail", "RuntimeError", "Traceback"):
            self.assertNotIn(detail, page.html, f"{VIEW}: never print a failure's detail")
        self.assertTrue(any("boom" in line for line in logs.output), f"{VIEW}: log the failure (logger.exception) so it is found")

    def test_a_billing_status_that_answers_an_error_is_the_same(self):
        with mock.patch("drop_billing.account.status", return_value=Answer(502, {"error": "Stripe: down", "code": "stripe_error"})), self.assertLogs("dashboard.overview_page", "WARNING"):
            page = self.open(self.user)
        self.assertIn(("status", BILLING_DOWN), page.notices())
        self.assertNotIn("Stripe: down", page.html, f"{VIEW}: an error body is not shown")
        self.assertEqual(len(page.meters()), 4)

    def test_no_state_of_the_page_shows_the_operators_words(self):
        # Ruling R1: the dashboard speaks to customers. Stripe's name, its message, a raw error code or "instance" never reach the page, in any state of it.
        banned = ("stripe", "instance", "configured", "traceback", "boom", "card_declined", "acct_", "stripe_error", "billing_not_configured")
        failing = {
            "a Stripe error": mock.patch("drop_billing.account.status", return_value=Answer(502, {"error": "Stripe: card_declined for acct_123", "code": "stripe_error"})),
            "billing not configured": mock.patch("drop_billing.account.status", return_value=Answer(503, {"error": "Billing is not configured on this Veilfile instance.", "code": "billing_not_configured"})),
            "a read that raises": mock.patch("drop_billing.account.status", side_effect=RuntimeError("Stripe: boom on this Veilfile instance")),
        }
        for query in ("", "?billing=success", "?billing=cancelled", "?billing=error", "?billing=error&plan=team", "?billing=already_subscribed"):
            page = self.open(self.user, query)
            for word in banned:
                self.assertNotIn(word, page.text.lower(), f"{PAGE}: {word!r} is operator wording and never shows on Overview (query {query!r})")
            for label, patch in failing.items():
                with patch, self.assertLogs("dashboard.overview_page", "WARNING"):
                    page = self.open(self.user, query)
                self.assertIn(BILLING_DOWN, page.text, f"{PAGE}: {label} ends in the customer sentence {BILLING_DOWN!r} (query {query!r})")
                for word in banned:
                    self.assertNotIn(word, page.text.lower(), f"{PAGE}: {word!r} is operator wording and never shows on Overview, after {label} (query {query!r})")

    def test_the_uploads_line_needs_only_usage(self):
        UsageCounter.objects.create(workspace=self.workspace, year_month=this_month(), uploads=150)
        with mock.patch("drop_billing.account.status", side_effect=RuntimeError("boom")), self.assertLogs("dashboard.overview_page", "ERROR"):
            page = self.open(self.user)
        said = [words(e) for e in page.classed("notice") if "Your workspace is over" in words(e)]
        self.assertEqual(len(said), 1, f"{OVER}: with no status the uploads line still shows, from usage")
        self.assertTrue(said[0].startswith("Your workspace is over the Free plan's limits."), f"{OVER}: the plan is named from usage when the status is down; found {said[0]!r}")
        self.assertIn(UPLOADS_OVER.format(used=150, cap=100), said[0])
        self.assertNotIn("active API keys, and the plan allows", said[0], f"{OVER}: the keys line needs the status, which is down")
        self.assertIn(("status", BILLING_DOWN), page.notices(), f"{PAGE}: and the page still says that the other limits could not be checked")

    def test_a_usage_that_raises_does_not_hide_the_limits_or_the_account(self):
        with mock.patch("drop_core.account.usage", side_effect=RuntimeError("boom")), self.assertLogs("dashboard.overview_page", "ERROR"):
            page = self.open(self.user)
        self.assertEqual(page.meters(), [], f"{PAGE}: with no usage there are no meters")
        self.assertIn(USAGE_DOWN, page.sections()["Usage"], f"{PAGE}: say so where the meters would be")
        self.assertIn(KEYS_OVER.format(n=3, cap=1), page.text, f"{VIEW}: the limit lines come from the billing status alone")
        self.assertIn("fail@example.com is verified.", page.sections()["Account"])
        self.assertIn("You are on the Free plan.", page.sections()["Plan"], f"{VIEW}: the plan comes from the status when usage is down")

    def test_both_reads_failing_still_draws_the_page(self):
        with mock.patch("drop_core.account.usage", side_effect=RuntimeError("one")), mock.patch("drop_billing.account.status", side_effect=RuntimeError("two")), \
                self.assertLogs("dashboard.overview_page", "ERROR"):
            page = self.open(self.user)
        self.assertEqual([words(h) for h in page.tree.all("h1")], ["Overview"])
        self.assertIn(USAGE_DOWN, page.text)
        self.assertIn(BILLING_DOWN, page.text)
        self.assertNotIn("You are on", page.text, f"{PAGE}: with neither read the plan is not known, so the Plan section is left out")
        self.assertIn("fail@example.com is verified.", page.text)

    def test_the_overview_opens_no_network_connection(self):
        # The billing status is read from our own tables. A call to Stripe here would make the first page of the dashboard wait on a third party and fail with it.
        refuse = AssertionError(f"{VIEW}: the Overview opened a network connection; it may read only our own tables")
        with mock.patch.object(socket.socket, "connect", side_effect=refuse), mock.patch.object(socket.socket, "connect_ex", side_effect=refuse):
            page = self.open(self.user, "?billing=success")
        self.assertEqual(len(page.meters()), 4)


class VoiceTests(SimpleTestCase):
    """The words of the page and its include keep the site's voice (spec 5): no em dash, no exclamation mark, none of the banned words. Voice is the owner's, and PR 7 reads these words again."""
    longMessage = False

    def test_the_templates_keep_the_voice(self):
        for name in ("dashboard/overview.html", "dashboard/over_limits.html"):
            text = (Path(settings.BASE_DIR) / "templates" / name).read_text(encoding="utf-8")
            words_only = re.sub(r"{%.*?%}|{#.*?#}|{{.*?}}", "", text)  # a template tag may say != ; the words may not shout
            where = f"backend/templates/{name}"
            with self.subTest(template=name):
                self.assertNotIn("\N{EM DASH}", text, f"{where}: no em dashes; use a colon, a comma or two sentences")
                self.assertNotIn("!", words_only, f"{where}: no exclamation marks")
                for word in BANNED:
                    self.assertNotIn(word, text.lower(), f"{where}: avoid {word.strip()!r} (AGENTS.md, voice)")
