"""site-tests/make_form_fixtures.py sets a known password on users, so it must refuse any database but a local or CI one; what it writes must work as the forms part of
site-tests/run_site_checks.py expects; and every state of that part must be reachable from it. Every class sets longMessage = False (READING_TEST_MODULES in pages/tests_site_rules.py holds the module to it),
and every message names the file and the fix."""
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
from django.core import mail
from django.db import connection
from django.test import Client, TestCase, override_settings

from drop_core.models import APIKey, DeviceCode, EmailVerification

ROOT = Path(settings.BASE_DIR).parent
SCRIPT, HARNESS = ROOT / "site-tests" / "make_form_fixtures.py", ROOT / "site-tests" / "run_site_checks.py"
FAST = ["django.contrib.auth.hashers.MD5PasswordHasher"]   # the script sets five passwords; a test need not wait for PBKDF2 for each
PREFIX = "site-tests/make_form_fixtures.py"


def run_script():
    """Run the script as `manage.py shell -c "exec(...)"` does, in the test's database; the fixtures it wrote."""
    with tempfile.TemporaryDirectory() as directory:
        with override_settings(DEBUG=True, PASSWORD_HASHERS=FAST), mock.patch.dict(os.environ, {"SITE_SESSION_FILE": str(Path(directory) / "session.txt")}), contextlib.redirect_stdout(io.StringIO()):
            exec(compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec"), {"__name__": "make_form_fixtures"})
        return json.loads((Path(directory) / "form-fixtures.json").read_text(encoding="utf-8"))


def harness_node(name):
    """A module-level assignment of the harness, as the syntax tree has it."""
    for each in ast.parse(HARNESS.read_text(encoding="utf-8")).body:
        if isinstance(each, ast.Assign) and getattr(each.targets[0], "id", "") == name:
            return each.value
    raise AssertionError(f"site-tests/run_site_checks.py: no module-level {name}")


def signed_in(key):
    client = Client()
    client.cookies[settings.SESSION_COOKIE_NAME] = key
    return client


class MakeFormFixturesGuardTests(TestCase):
    longMessage = False

    def assert_refused(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"SITE_SESSION_FILE": str(Path(directory) / "session.txt")}):
            with self.assertRaises(SystemExit) as refusal:
                exec(compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec"), {"__name__": "make_form_fixtures"})
            self.assertFalse((Path(directory) / "form-fixtures.json").exists(), f"{PREFIX}: it wrote a file before it refused")
        self.assertIn("unset DATABASE_URL", str(refusal.exception))
        self.assertFalse(get_user_model().objects.filter(username="site-verified").exists(), f"{PREFIX}: the guard goes before anything touches the database")

    def test_refuses_when_debug_is_off(self):
        self.assert_refused()   # tests run with DEBUG off, as production does

    @override_settings(DEBUG=True)
    def test_refuses_a_database_on_another_host(self):
        with mock.patch.object(connection, "vendor", "postgresql"), mock.patch.dict(connection.settings_dict, {"HOST": "db.example.com"}):
            self.assert_refused()


@override_settings(PASSWORD_HASHERS=FAST)
class MakeFormFixturesTests(TestCase):
    """What a run writes, and that the app answers each of it as the forms part expects."""

    longMessage = False

    @classmethod
    def setUpTestData(cls):
        cls.f = run_script()

    def test_the_sessions_the_codes_the_clients_and_the_links_work(self):
        f = self.f
        self.assertEqual(sorted(f), ["accounts", "device", "errors", "oauth", "reset", "sessions", "verify"], f"{PREFIX}: the keys run_forms reads")
        self.assertEqual(Client().post("/accounts/login/", f["accounts"]["verified"]).status_code, 302, f"{PREFIX}: the round trips sign in with this name and password")
        for key, address, status, text in (
                ("verified", f"/device?user_code={f['device']['consent']}", 200, 'name="confirm"'),
                ("verified", f"/device?user_code={f['device']['expired']}", 410, ""), ("long", f"/device?user_code={f['device']['long']}", 200, ""),
                ("verified", f["oauth"]["authorize"], 200, f["oauth"]["app"]), ("long", f["oauth"]["long"], 200, "SiteChecksLongName"), ("verified", f["oauth"]["unknown"], 400, ""),
                ("pending", "/device", 200, "Verify your email first"), ("pending", "/accounts/verify-email/?sent=1", 200, ""),
                ("verified", f"/device?user_code={f['device']['approve']}", 200, 'name="confirm"'), ("verified", f"/device?user_code={f['device']['deny']}", 200, 'name="confirm"'),
                (None, f["reset"]["link"], 200, "new_password1"), (None, f["verify"]["link"], 200, "")):
            client = signed_in(f["sessions"][key]) if key else Client()
            self.assertContains(client.get(address, follow=True), text, status_code=status, msg_prefix=f"{PREFIX}: {key or 'nobody'} at {address}")
        verified = signed_in(f["sessions"]["verified"])   # the codes the approved and denied states use up: each gets the page the state names, with the confirmation the driver ticks
        for how, code, words in (("approve", f["device"]["approve"], "Terminal approved"), ("deny", f["device"]["deny"], "Terminal denied")):
            self.assertContains(verified.post(f"/device/{how}/", {"user_code": code, "confirm": "yes"}), words, msg_prefix=f"{PREFIX}: the {how} code")
        self.assertEqual(APIKey.objects.get(workspace__owner__username__startswith="site-long-").name, "K" * 255, "the long user is at the free plan's one key, with the longest name")
        self.assertTrue(EmailVerification.objects.get(user__username="site-reset").is_verified)
        pending = signed_in(f["sessions"]["pending"])
        self.assertEqual(pending.post("/accounts/verify-email/", {"action": "resend"}).status_code, 429, f"{PREFIX}: the pending user's last link went in the future: a resend sends nothing")
        self.assertEqual(mail.outbox, [])

    def test_the_error_pages_print_nothing_of_the_request_and_a_second_run_starts_again(self):
        for name, page in self.f["errors"].items():
            self.assertNotIn("leak-check-7c1e", page["body"], f"{name}: an error page prints nothing of the address it was asked for")
            self.assertNotIn("site checks: a view that fails", page["body"], f"{name}: nor of the failure")
        self.assertEqual((self.f["errors"]["not_found"]["status"], self.f["errors"]["server_error"]["status"]), (404, 500))
        self.assertNotEqual(run_script()["sessions"], self.f["sessions"])
        self.assertEqual((get_user_model().objects.filter(username="site-verified").count(), DeviceCode.objects.filter(requester_ip="198.51.100.7").count()), (1, 5),
                         f"{PREFIX}: a run starts by deleting what an earlier one made")


@override_settings(PASSWORD_HASHERS=FAST)
class FormStatesAreReachableTests(TestCase):
    longMessage = False

    def test_the_states_opened_in_ink_are_the_ones_the_docs_say_and_each_is_also_opened_in_paper(self):
        # AGENTS.md ("Site checks") and spec section 10 say that the sign-in page with an error, the device approval page and the 404 are opened in Ink too.
        themes = {}
        for call in harness_node("FORM_STATES").elts:
            args, options = [ast.literal_eval(arg) for arg in call.args], {k.arg: ast.literal_eval(k.value) for k in call.keywords}
            themes.setdefault(options.get("theme", "light"), set()).add((args[1] if len(args) > 1 else "", options.get("how", "open"), options.get("who", "")))
        self.assertEqual(themes.get("dark", set()), {("/accounts/login/", "login_failed", ""), ("/device?user_code={device[consent]}", "open", "verified"), ("not_found", "baked", "")},
                         "site-tests/run_site_checks.py: the states with theme=\"dark\" are the sign-in page with an error, the device approval page and the 404; if that changes, change AGENTS.md "
                         "(\"Site checks\") and the spec's section 10 with it")
        self.assertLessEqual(themes["dark"], themes["light"], "site-tests/run_site_checks.py: each state opened in Ink is also opened in Paper, so a difference between them is the appearance's")

    def test_every_state_names_a_driver_a_session_and_an_address_the_fixtures_have(self):
        fixtures, names, spends, throttled, themes = run_script(), [], set(), set(), {}
        drivers, sessions = {key.value for key in harness_node("FORM_DRIVERS").keys}, set(fixtures["sessions"]) | {"", "staff"}
        for call in harness_node("FORM_STATES").elts:
            args, options = [ast.literal_eval(arg) for arg in call.args], {k.arg: ast.literal_eval(k.value) for k in call.keywords}
            name, path = args[0], args[1] if len(args) > 1 else ""
            names.append(name)
            themes.setdefault(options.get("theme", "light"), set()).add((path, options.get("how", "open")))
            (spends if options.get("spends") else set()).add(name)
            (throttled if options.get("status") == 429 else set()).add(name)
            self.assertIn(options.get("how", "open"), drivers, f"the state {name!r} names a driver that FORM_DRIVERS does not have")
            self.assertIn(options.get("who", ""), sessions, f"the state {name!r} names a session that {PREFIX} does not write")
            if options.get("how") == "baked":
                self.assertIn(path, fixtures["errors"], f"the state {name!r} serves a baked page the fixtures do not have")
            else:
                path.format(**fixtures)   # a KeyError here names a field the fixtures lack
        self.assertEqual(len(names), len(set(names)), "site-tests/run_site_checks.py: every state has a name of its own (the screenshots are named by it)")
        self.assertEqual(sorted(themes), ["dark", "light"], "site-tests/run_site_checks.py: a state's theme is light (Paper) or dark (Ink)")
        self.assertEqual(spends, throttled, "site-tests/run_site_checks.py: a state that answers 429 uses up a budget the others need, so it has spends=True, and only it does")
