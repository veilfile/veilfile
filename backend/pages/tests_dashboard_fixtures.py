"""site-tests/make_dashboard_fixtures.py plants users and stores a billing state, so it must refuse any database but a local or CI one, any server that has a Stripe variable and any
storage but the local files; what it writes must work as the dashboard part of site-tests/run_site_checks.py expects; and every name that part reads from it must be there. Every class
sets longMessage = False (READING_TEST_MODULES in pages/tests_site_rules.py holds the module to it), and every message names the file and the fix."""
import ast
import contextlib
import io
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings

from drop_billing import account as billing_account
from drop_core import account
from drop_core.key_caps import key_cap
from drop_core.models import APIKey, Artifact, Invitation, Membership, get_or_create_workspace
from drop_core.teams import find_invitation, member_rows

ROOT = Path(settings.BASE_DIR).parent
SCRIPT, HARNESS = ROOT / "site-tests" / "make_dashboard_fixtures.py", ROOT / "site-tests" / "run_site_checks.py"
PREFIX = "site-tests/make_dashboard_fixtures.py"
URL_MEMBERS = "/app/members/"
SECTIONS = ("/app/", "/app/files/", "/app/keys/", "/app/apps/", "/app/billing/")


def run_script():
    """Run the script as `manage.py shell -c "exec(...)"` does, in the test's database; the fixtures it wrote."""
    with tempfile.TemporaryDirectory() as directory:
        with override_settings(DEBUG=True), mock.patch.dict(os.environ, {"SITE_SESSION_FILE": str(Path(directory) / "session.txt")}), contextlib.redirect_stdout(io.StringIO()):
            exec(compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec"), {"__name__": "make_dashboard_fixtures"})
        return json.loads((Path(directory) / "dashboard-fixtures.json").read_text(encoding="utf-8"))


def signed_in(key):
    client = Client()
    client.cookies[settings.SESSION_COOKIE_NAME] = key
    return client


def fixture_paths():
    """Every path into the fixtures that the dashboard part's functions (dashboard_*, dash_*) read, as a tuple of the keys: fixtures["owner"]["keys"] is ("owner", "keys")."""
    found = set()
    for function in ast.parse(HARNESS.read_text(encoding="utf-8")).body:
        if not (isinstance(function, ast.FunctionDef) and function.name.startswith(("dashboard_", "dash_"))):
            continue
        inner = {id(node.value) for node in ast.walk(function) if isinstance(node, ast.Subscript)}   # a subscript that is itself subscripted again is not the end of its chain
        for node in ast.walk(function):
            if not isinstance(node, ast.Subscript) or id(node) in inner:
                continue
            chain = []
            while isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                chain.append(node.slice.value)
                node = node.value
            if isinstance(node, ast.Name) and node.id == "fixtures" and chain:
                found.add(tuple(reversed(chain)))
    return found


class MakeDashboardFixturesGuardTests(TestCase):
    longMessage = False

    def assert_refused(self, words):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"SITE_SESSION_FILE": str(Path(directory) / "session.txt")}):
            with self.assertRaises(SystemExit) as refusal:
                exec(compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec"), {"__name__": "make_dashboard_fixtures"})
            self.assertFalse((Path(directory) / "dashboard-fixtures.json").exists(), f"{PREFIX}: it wrote a file before it refused")
        self.assertIn(words, str(refusal.exception), f"{PREFIX}: the refusal should say how to go on ({words!r})")
        self.assertFalse(get_user_model().objects.filter(username__startswith="site-dash-").exists(), f"{PREFIX}: the guards go before anything touches the database")

    def test_refuses_when_debug_is_off(self):
        self.assert_refused("unset DATABASE_URL")   # tests run with DEBUG off, as production does

    @override_settings(DEBUG=True)
    def test_refuses_a_database_on_another_host(self):
        with mock.patch.object(connection, "vendor", "postgresql"), mock.patch.dict(connection.settings_dict, {"HOST": "db.example.com"}):
            self.assert_refused("unset DATABASE_URL")

    @override_settings(DEBUG=True)
    def test_refuses_while_a_stripe_variable_is_set(self):
        # the checks press Upgrade and Manage billing: a server with a key would ask Stripe
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_not_a_key"}):
            self.assert_refused("Unset every STRIPE_ variable")

    @override_settings(DEBUG=True)
    def test_refuses_a_storage_that_is_not_the_local_files(self):
        # the checks revoke a file: with R2 configured that is a delete in a bucket
        with mock.patch.dict(os.environ, {"DROP_STORAGE": "s3"}):
            self.assert_refused("Unset DROP_STORAGE")


class MakeDashboardFixturesTests(TestCase):
    """What a run writes, and that the app answers each of it as the dashboard part expects."""

    longMessage = False

    @classmethod
    def setUpTestData(cls):
        cls.f = run_script()

    def test_it_writes_the_sessions_and_the_names_the_harness_reads_and_each_session_opens_every_section(self):
        f = self.f
        self.assertEqual(sorted(f), ["over", "owner", "sessions", "sub", "team"], f"{PREFIX}: the keys the dashboard part reads")
        self.assertEqual(sorted(f["sessions"]), ["admin", "member", "over", "owner", "sign_out", "sub"], f"{PREFIX}: the sessions the dashboard part reads")
        self.assertEqual(len(f["sessions"]["sign_out"]), 14, f"{PREFIX}: the Sign out checks end 14 sessions of the owner (dashboard_sign_out_problems and dashboard_slow_frames_problems), so there are 14")
        missing = []
        for path in sorted(fixture_paths() - {("run",)}):
            here = f
            for step in path:
                if not (isinstance(here, dict) and step in here) and not (isinstance(here, list) and isinstance(step, int) and step < len(here)):
                    missing.append("/".join(map(str, path)))
                    break
                here = here[step]
        self.assertEqual(missing, [], f"{PREFIX}: site-tests/run_site_checks.py reads fixtures it does not write: add them to the fixtures dict, or take the read out")
        self.assertTrue(fixture_paths(), "pages/tests_dashboard_fixtures.py: fixture_paths() found no read of fixtures[...] in the dashboard part's functions, so nothing was compared")
        for name, key in (("owner", f["sessions"]["owner"]), ("over", f["sessions"]["over"]), ("sub", f["sessions"]["sub"]), ("the team's administrator", f["sessions"]["admin"]), ("the team's member", f["sessions"]["member"]), ("a spare", f["sessions"]["sign_out"][0]), ("the last spare", f["sessions"]["sign_out"][13])):
            client = signed_in(key)
            for path in SECTIONS:
                self.assertEqual(client.get(path).status_code, 200, f"{PREFIX}: the {name} session should open {path}")

    def test_the_owner_has_the_keys_the_files_and_the_apps_the_checks_look_for_and_room_for_two_new_keys(self):
        owner = self.f["owner"]
        workspace = get_or_create_workspace(get_user_model().objects.get(username="site-dash-owner"))
        keys = list(APIKey.objects.filter(workspace=workspace))
        self.assertEqual((workspace.plan, len(keys), sum(key.is_active for key in keys)), ("team", owner["keys"], 3), f"{PREFIX}: a Team workspace with four keys, three of them active")
        self.assertEqual(sorted(len(key.name) for key in keys)[-1], 255, f"{PREFIX}: one key has the longest name a key takes")
        self.assertEqual([key.name for key in keys if not key.is_active], [owner["retired_key"]], f"{PREFIX}: one key is revoked")
        self.assertGreaterEqual(key_cap(workspace) - 3, 2, f"{PREFIX}: dashboard_key_problems makes two keys, so the plan's cap leaves room for two")
        first = account.list_artifacts(get_user_model().objects.get(username="site-dash-owner"), limit=50)
        self.assertEqual(len(first.body), 50, f"{PREFIX}: the Files list's first page has 50 files")
        later = account.list_artifacts(get_user_model().objects.get(username="site-dash-owner"), cursor=first.headers["X-Next-Cursor"])
        self.assertEqual((Artifact.objects.filter(workspace=workspace).count(), len(later.body)), (owner["files"], owner["files"] - 50), f"{PREFIX}: 53 files, so a second page of 3")
        names = set(Artifact.objects.filter(workspace=workspace).values_list("filename", flat=True))
        for wanted in ("long_file", "revoke_file", "flags_file", "expired_file", "held_file"):
            self.assertIn(owner[wanted], names, f"{PREFIX}: the file {wanted}")
        self.assertEqual(len(owner["long_file"]), 512, f"{PREFIX}: the longest name a file takes")
        self.assertEqual(Artifact.objects.get(workspace=workspace, filename=owner["held_file"]).safety_status, "held")
        grants = account.list_grants(get_user_model().objects.get(username="site-dash-owner")).body
        self.assertEqual(sorted(row["name"] for row in grants), sorted(owner["apps"].values()), f"{PREFIX}: three connected apps, one with the longest name")
        self.assertEqual(max(len(row["name"]) for row in grants), 255)

    def test_the_downgraded_user_is_over_four_limits_with_a_stored_unpaid_subscription(self):
        user = get_user_model().objects.get(username="site-dash-over")
        status, usage = billing_account.status(user).body, account.usage(user).body
        self.assertEqual((status["plan"], sorted(status["over_plan_limits"]), status["subscription_status"], status["has_subscription"], status["has_billing_account"]),
                         ("free", ["keys", "storage", "ttl"], "unpaid", False, True), f"{PREFIX}: a Free workspace over keys, storage and link lifetime with a stored unpaid status and a customer id")
        self.assertGreater(usage["uploads_used"], usage["uploads_cap"], f"{PREFIX}: more uploads this month than the plan allows")

    def test_the_subscriber_has_a_failed_payment_and_a_switch_waiting_for_payment(self):
        user = get_user_model().objects.get(username="site-dash-sub")
        status = billing_account.status(user).body
        self.assertEqual((status["plan"], status["subscription_status"], status["has_subscription"]), ("team", "past_due", True), f"{PREFIX}: a Team subscriber whose payment failed")
        pending = signed_in(self.f["sessions"]["sub"]).session["dashboard_pending_switch"]
        self.assertEqual((pending["plan"], pending["invoice_url"].startswith("https://")), ("scale", True), f"{PREFIX}: a switch to Scale waits for payment (dashboard/billing_page.py remember_pending)")

    @override_settings(TEAM_SEATS=True)
    def test_the_team_has_an_administrator_a_member_with_allowances_and_a_waiting_invitation(self):
        team, User = self.f["team"], get_user_model()
        admin = User.objects.get(username="site-dash-admin")
        workspace = get_or_create_workspace(admin)
        self.assertEqual(workspace.plan, "team", f"{PREFIX}: the team is on the Team plan, which allows five people")
        rows = member_rows(workspace)
        self.assertEqual([row["role"] for row in rows], ["administrator", "member"], f"{PREFIX}: an administrator and one member")
        member = rows[1]
        self.assertEqual(member["person"].pk, team["member_id"], f"{PREFIX}: the allowance page is opened for this member")
        self.assertEqual((member["upload_allowance"], member["storage_allowance"], member["key_allowance"]), (50, 2 * 1024**3, 9),
                         f"{PREFIX}: the member has all three allowances, the keys one above the plan's five, so that a cell reads 'capped at'")
        self.assertGreater(member["uploads_used"], 0, f"{PREFIX}: the member has used some of the month's uploads")
        self.assertGreater(member["storage_used"], 0, f"{PREFIX}: the member has files, so the Storage cell has a figure")
        invitation = find_invitation(team["token"])
        self.assertEqual((invitation.workspace_id, invitation.email), (workspace.pk, team["invitee"]), f"{PREFIX}: the raw token opens the waiting invitation, which was made directly")
        self.assertEqual(Invitation.objects.filter(workspace=workspace).count(), 1, f"{PREFIX}: one invitation waits")
        self.assertEqual(Membership.objects.filter(user__username__in=["site-dash-admin", "site-dash-member"], removed_at__isnull=True).count(), 2,
                         f"{PREFIX}: the new users are the team's two people and nobody else's")
        admin_client, member_client = signed_in(self.f["sessions"]["admin"]), signed_in(self.f["sessions"]["member"])
        for path, words in ((URL_MEMBERS, "Waiting invitations"), (f"{URL_MEMBERS}{team['member_id']}/allowance/", "Set an allowance")):
            self.assertContains(admin_client.get(path), words, msg_prefix=f"{PREFIX}: the administrator at {path}")
        self.assertContains(member_client.get("/app/"), "Your use", msg_prefix=f"{PREFIX}: the member's Overview")
        self.assertContains(member_client.get("/app/"), "Review the request", msg_prefix=f"{PREFIX}: the member has a request to become the administrator waiting, so Overview says so")
        for path, words in ((f"{URL_MEMBERS}{team['member_id']}/remove/", "Do you want to remove"), (f"{URL_MEMBERS}{team['member_id']}/administrator/", "Do you want to make"),
                            (f"{URL_MEMBERS}administrator/cancel/", "Do you want to cancel the request?")):
            self.assertContains(admin_client.get(path), words, msg_prefix=f"{PREFIX}: the administrator at {path}")
        for path, words in (("/app/team/leave/", "Do you want to leave this team?"), ("/app/team/administrator/", "Do you want to become the administrator of this team?")):
            self.assertContains(member_client.get(path), words, msg_prefix=f"{PREFIX}: the member at {path}")
        self.assertEqual(member_client.get(URL_MEMBERS).status_code, 404, f"{PREFIX}: a member has no Members page")

    def test_a_second_run_starts_again_and_touches_nobody_else(self):
        bystander = get_user_model().objects.create(username="site-dash-bystander")
        APIKey.create_key(get_or_create_workspace(bystander), "not-theirs")
        again = run_script()
        self.assertNotEqual(again["sessions"], self.f["sessions"], f"{PREFIX}: a run makes new sessions")
        admin = get_user_model().objects.get(username="site-dash-admin")
        self.assertEqual((Membership.objects.filter(workspace__owner=admin, removed_at__isnull=True).count(), Invitation.objects.filter(workspace__owner=admin).count()), (2, 1),
                         f"{PREFIX}: a run starts by deleting the team's invitations and members, so a second leaves two people and one invitation")
        self.assertNotEqual(again["team"]["token"], self.f["team"]["token"], f"{PREFIX}: a run makes a new invitation, with a token of its own")
        owner = get_user_model().objects.get(username="site-dash-owner")
        self.assertEqual((Artifact.objects.filter(workspace__owner=owner).count(), APIKey.objects.filter(workspace__owner=owner).count()), (53, 4),
                         f"{PREFIX}: a run starts by deleting what an earlier one made, so a second leaves 53 files and 4 keys and not twice as many")
        self.assertEqual(len(account.list_grants(owner).body), 3, f"{PREFIX}: nor twice the connected apps")
        self.assertEqual(APIKey.objects.filter(workspace__owner=bystander).count(), 1, f"{PREFIX}: it deletes only its own users' things")

    def test_git_ignores_the_fixtures_beside_the_session_file(self):
        self.assertIn("site-tests/dashboard-fixtures.json", (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines(),
                      ".gitignore: add site-tests/dashboard-fixtures.json beside site-tests/form-fixtures.json: it holds the keys of signed-in sessions")
