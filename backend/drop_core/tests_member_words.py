"""What a team member's agent reads at a plan's cap and when the team is on Free (team seats PR E1)."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from drop_core.account import create_key as account_create_key
from drop_core.key_caps import active_key_count
from drop_core.models import APIKey, EmailVerification, UsageCounter, administrator_of
from drop_core.plans import PLANS
from drop_core.services import _current_month
from drop_core.tests_allowances import live_file, second_member
from drop_mcp.tests_fix_mcp import McpFixTestBase, _b64

WORDS = "backend/drop_core/cap_words.py"

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
