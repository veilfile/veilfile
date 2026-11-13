"""What a team member's agent reads at a plan's cap and when the team is on Free (team seats PR E1)."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from drop_core.account import create_key as account_create_key
from drop_core.auth import barred_credential
from drop_core.key_caps import active_key_count, create_key_within_cap
from drop_core.models import APIKey, EmailVerification, Membership, UsageCounter, administrator_of
from drop_core.plans import PLANS
from drop_core.services import _current_month
from drop_core.tests_allowances import live_file, second_member
from drop_core.tests_bar import Base as BarBase
from drop_core.tests_bar import bearer, drop, restore, rpc
from drop_core.tests_members import _issue_token, _make_app
from drop_mcp.tests_fix_mcp import McpFixTestBase, _b64

WORDS = "backend/drop_core/cap_words.py"
AUTH = "backend/drop_core/auth.py"
MCP = "backend/drop_mcp/views.py"

# The sentences main answers for everyone, copied from main at 5b41aa2.
MAIN_MONTHLY = (
    "Your workspace has reached its plan's monthly upload cap. Uploads work again next month, or you can upgrade your plan."
)
MAIN_STORAGE = "Your workspace has reached its plan's storage cap. Revoke files you no longer need, or upgrade your plan."
MAIN_KEYS = "Your workspace has reached its plan's API key limit. Revoke a key or upgrade your plan."

MEMBER_MONTHLY = (
    "Your team has reached its plan's monthly upload cap. Uploads work again next month, or you can ask your "
    "administrator to choose a larger plan under Billing."
)
MEMBER_STORAGE = (
    "Your team has reached its plan's storage cap. Revoke files you no longer need, or ask your administrator to "
    "choose a larger plan under Billing."
)
MEMBER_KEYS = (
    "Your team has reached its plan's API key limit. Revoke a key, or ask your administrator to choose a larger plan "
    "under Billing."
)


TODAY_401 = "The API key is not valid, or the connected app's access has ended. Send a current key, or reconnect the app."
BARRED_401 = (
    "Your team is on the Free plan, so its members' API keys and connected apps are paused. Ask your administrator "
    "to choose a paid plan under Billing."
)


@override_settings(TEAM_SEATS=True)
class MemberCapTests(McpFixTestBase):
    """A team on Team with its administrator, whose key is `raw_key`, and Grace, a member with her own key."""

    longMessage = False

    def setUp(self):
        super().setUp()
        self.workspace.plan = "team"
        self.workspace.save(update_fields=["plan"])
        self.admin = administrator_of(self.workspace)
        self.grace = second_member(self.workspace, "grace")
        _, self.grace_raw = APIKey.create_key(self.workspace, "grace-key", member=self.grace)
        self.user.email = "owner@example.com"
        self.user.save(update_fields=["email"])
        for user in (self.user, self.grace.user):
            EmailVerification.objects.update_or_create(
                user=user, defaults={"email": user.email, "is_verified": True, "verified_at": timezone.now()}
            )

    def at_monthly_cap(self):
        UsageCounter.objects.create(workspace=self.workspace, year_month=_current_month(), uploads=PLANS["team"]["uploads"])

    def at_storage_cap(self):
        live_file(self.workspace, self.admin, PLANS["team"]["storage_gb"] * 1024**3)

    def at_key_limit(self):
        while active_key_count(self.workspace) < PLANS["team"]["keys"]:
            APIKey.create_key(self.workspace, "filler")

    def answers(self, raw):
        """The REST answer and the MCP tool error for one upload with the key `raw`."""
        response = APIClient().post(
            "/api/v1/artifacts/", {"file": SimpleUploadedFile("a.txt", b"hi")}, HTTP_AUTHORIZATION=f"Bearer {raw}"
        )
        tool = self.tool_error(self.call("upload_artifact", {"filename": "a.txt", "content_base64": _b64(b"hi")}, raw_key=raw))
        return response.status_code, response.json(), (tool["code"], tool["error"])

    def key_answer(self, user):
        answer = account_create_key(user, "one more")
        return answer.status, answer.body

    def test_a_member_at_the_monthly_cap_reads_the_member_sentence_on_rest_and_mcp(self):
        self.at_monthly_cap()
        status, body, tool = self.answers(self.grace_raw)
        self.assertEqual((status, body), (429, {"error": MEMBER_MONTHLY, "code": "monthly_cap_reached"}), f"{WORDS}: REST")
        self.assertEqual(tool, ("monthly_cap_reached", MEMBER_MONTHLY), f"{WORDS}: MCP")

    def test_a_member_at_the_storage_cap_reads_the_member_sentence_on_rest_and_mcp(self):
        self.at_storage_cap()
        status, body, tool = self.answers(self.grace_raw)
        self.assertEqual((status, body), (402, {"error": MEMBER_STORAGE, "code": "storage_cap_reached"}), f"{WORDS}: REST")
        self.assertEqual(tool, ("storage_cap_reached", MEMBER_STORAGE), f"{WORDS}: MCP")

    def test_a_member_at_the_key_limit_reads_the_member_sentence(self):
        self.at_key_limit()
        self.assertEqual(
            self.key_answer(self.grace.user), (402, {"error": MEMBER_KEYS, "code": "key_limit_reached"}), f"{WORDS}: keys"
        )

    def test_the_administrator_reads_todays_sentences(self):
        self.at_monthly_cap()
        self.assertEqual(self.answers(self.raw_key)[1:], ({"error": MAIN_MONTHLY, "code": "monthly_cap_reached"},
                                                          ("monthly_cap_reached", MAIN_MONTHLY)), f"{WORDS}: monthly")
        UsageCounter.objects.all().delete()
        self.at_storage_cap()
        self.assertEqual(self.answers(self.raw_key)[1:], ({"error": MAIN_STORAGE, "code": "storage_cap_reached"},
                                                          ("storage_cap_reached", MAIN_STORAGE)), f"{WORDS}: storage")
        self.at_key_limit()
        keys = self.key_answer(self.user)
        self.assertEqual(keys, (402, {"error": MAIN_KEYS, "code": "key_limit_reached"}), f"{WORDS}: keys, not {keys}")

    def test_with_the_switch_off_the_sentences_are_mains(self):
        with override_settings(TEAM_SEATS=False):
            for who, raw in (("the administrator", self.raw_key), ("a member row", self.grace_raw)):
                with self.subTest(who=who):
                    UsageCounter.objects.all().delete()
                    self.at_monthly_cap()
                    tool = self.answers(raw)[2]
                    self.assertEqual(tool, ("monthly_cap_reached", MAIN_MONTHLY), f"{WORDS}: {who}, not {tool}")
            self.at_key_limit()
            keys = self.key_answer(self.user)
            self.assertEqual(keys, (402, {"error": MAIN_KEYS, "code": "key_limit_reached"}), f"{WORDS}: keys, not {keys}")


class BarredCredentialTests(BarBase):
    """Grace, a member of Ada's team, holds a key and an OAuth token, and the team then sits on Free."""

    def setUp(self):
        super().setUp()
        self.raw_token, _, _ = _issue_token(self.grace.user, _make_app())

    def rest_401(self, raw):
        response = self.post_file(**bearer(raw))
        self.assertEqual(response.status_code, 401, f"{AUTH}: a refused credential is a 401")
        return response.json()["code"], response.json()["error"], response["WWW-Authenticate"]

    def test_a_barred_members_key_gets_the_barred_sentence_on_rest(self):
        drop(self.team)
        for name, raw in (("key", self.raw), ("token", self.raw_token)):
            with self.subTest(credential=name):
                self.assertEqual(self.rest_401(raw), ("unauthorized", BARRED_401, "Bearer"), f"{AUTH}: the {name}")

    def test_a_barred_members_token_gets_the_barred_sentence_on_mcp(self):
        drop(self.team)
        for name, raw in (("token", self.raw_token), ("key", self.raw)):
            with self.subTest(credential=name):
                response = rpc(self.client, "list_artifacts", {}, raw)
                self.assertEqual(response.status_code, 401, f"{MCP}: the {name} is refused")
                error = response.json()["error"]
                self.assertEqual((error["code"], error["message"]), (-32001, BARRED_401), f"{MCP}: the {name}")
                self.assertIn("resource_metadata=", response["WWW-Authenticate"], f"{MCP}: the challenge is unchanged")

    def test_other_refused_credentials_keep_todays_sentence(self):
        lin = second_member(self.team, "lin")
        _, lins_raw = create_key_within_cap(self.team, "lin-key", member=lin)
        Membership.objects.filter(pk=lin.pk).update(removed_at=timezone.now())
        APIKey.objects.filter(pk=self.key.pk).update(is_active=False)
        drop(self.team)
        cases = (("a removed member's key", lins_raw), ("a revoked key", self.raw), ("an unknown key", "vlf_" + "0" * 40))
        for name, raw in cases:
            with self.subTest(credential=name):
                self.assertEqual(self.rest_401(raw)[1], TODAY_401, f"{AUTH}: {name} is not barred")

    def test_the_check_leaves_the_key_untouched_and_it_works_once_the_team_pays(self):
        drop(self.team)
        before = APIKey.objects.filter(pk=self.key.pk).values_list("last_used_at", "is_active").get()
        self.assertEqual(self.rest_401(self.raw)[1], BARRED_401)
        after = APIKey.objects.filter(pk=self.key.pk).values_list("last_used_at", "is_active").get()
        self.assertEqual(after, before, f"{AUTH}: the check writes nothing")
        restore(self.team)
        self.assertEqual(self.post_file(**bearer(self.raw)).status_code, 201, f"{AUTH}: the same key works once the team pays")

    def test_with_the_switch_off_the_check_asks_nothing(self):
        drop(self.team)
        with override_settings(TEAM_SEATS=False):
            with self.assertNumQueries(0):
                self.assertFalse(barred_credential(self.raw), f"{AUTH}: the key")
                self.assertFalse(barred_credential(self.raw_token), f"{AUTH}: the token")
            self.assertEqual(self.rest_401(self.raw)[1], TODAY_401, f"{AUTH}: the switch-off answer is main's")
