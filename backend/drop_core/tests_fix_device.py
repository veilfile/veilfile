"""Regression tests for the device-flow fixes (cluster: device).

Covers dev-1 (consent copy and confirmation), dev-2 (approve/poll races and
approval near expiry), dev-3/npm-9/ctr-6/deploy-6 (no cache handoff), dev-4/
auth-4/ctr-13 (device keys count toward the plan key cap, replace-at-cap),
dev-5 (purge and issuance ceiling), dev-8 (token endpoint body parsing) and
dev-9 (attempt limiting on user codes).
"""

import json
import os
import shutil
import tempfile
from datetime import timedelta
from unittest import mock
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from drop_core import device
from drop_core.models import (
    APIKey,
    DeviceCode,
    EmailVerification,
    get_or_create_workspace,
)

User = get_user_model()

GRANT = "urn:ietf:params:oauth:grant-type:device_code"


class _DeviceTestBase(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        # Restore (not unset) the runner's DROP_LOCAL_DIR afterwards, so later
        # tests never fall back to backend/media/artifacts.
        local_dir = mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": self.tmpdir})
        local_dir.start()
        self.addCleanup(local_dir.stop)
        cache.clear()
        self.terminal = APIClient()
        self.browser = APIClient()
        self.user = self._user("dev")
        self.workspace = get_or_create_workspace(self.user)
        self.browser.force_login(self.user)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _user(self, name):
        user = User.objects.create_user(
            username=name, email=f"{name}@example.com", password="pw12345678"
        )
        EmailVerification.objects.create(
            user=user,
            email=user.email,
            is_verified=True,
            verified_at=timezone.now(),
        )
        return user

    def _start(self, **extra):
        response = self.terminal.post("/api/v1/device/code", {}, format="json", **extra)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _row(self, grant):
        return DeviceCode.objects.get(user_code=grant["user_code"].replace("-", ""))

    def _approve(self, grant, browser=None, **fields):
        data = {"user_code": grant["user_code"], "confirm": "yes"}
        data.update(fields)
        return (browser or self.browser).post("/device/approve", data)

    def _poll(self, grant):
        return self.terminal.post(
            "/api/v1/device/token",
            {"grant_type": GRANT, "device_code": grant["device_code"]},
            format="json",
        )

    def _key(self, name, workspace=None, device_issued=False):
        key, raw = APIKey.create_key(workspace or self.workspace, name)
        if device_issued:
            key.device_issued = True
            key.save(update_fields=["device_issued"])
        return key


class DeviceKeyHandoffTests(_DeviceTestBase):
    """dev-2, dev-3, npm-9, ctr-6, deploy-6: the key is minted at poll time."""

    def test_approval_mints_nothing_and_caches_nothing(self):
        grant = self._start()
        self.assertEqual(self._approve(grant).status_code, 200)
        row = self._row(grant)
        self.assertEqual(row.status, DeviceCode.STATUS_APPROVED)
        self.assertIsNone(row.api_key)
        self.assertEqual(APIKey.objects.count(), 0)
        self.assertIsNone(cache.get(f"device-key:{row.pk}"))

    def test_key_delivered_when_poll_lands_on_another_worker(self):
        # A different gunicorn worker or instance has its own (empty)
        # LocMemCache: simulate it by clearing the cache between the
        # approval and the poll.
        grant = self._start()
        self._approve(grant)
        cache.clear()
        response = self._poll(grant)
        self.assertEqual(response.status_code, 200)
        raw_key = response.json()["api_key"]
        self.assertEqual(set(response.json()), {"api_key"})
        key = APIKey.verify(raw_key)
        self.assertIsNotNone(key)
        self.assertTrue(key.device_issued)
        self.assertEqual(self._row(grant).api_key, key)
        self.assertEqual(self._row(grant).status, DeviceCode.STATUS_CONSUMED)

    def test_approval_never_polled_creates_no_key(self):
        grant = self._start()
        self._approve(grant)
        later = timezone.now() + timedelta(minutes=20)
        with mock.patch("django.utils.timezone.now", return_value=later):
            response = self._poll(grant)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "expired_token"})
        self.assertEqual(APIKey.objects.count(), 0)
        self.assertEqual(self._row(grant).status, DeviceCode.STATUS_EXPIRED)

    def test_approval_seconds_before_expiry_is_still_delivered(self):
        grant = self._start()
        DeviceCode.objects.filter(pk=self._row(grant).pk).update(
            expires_at=timezone.now() + timedelta(seconds=2)
        )
        self.assertEqual(self._approve(grant).status_code, 200)
        later = timezone.now() + timedelta(seconds=5)
        with mock.patch("django.utils.timezone.now", return_value=later):
            response = self._poll(grant)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["api_key"].startswith("vlf_"))
        self.assertEqual(APIKey.objects.filter(is_active=True).count(), 1)

    def test_concurrent_polls_mint_exactly_one_key(self):
        grant = self._start()
        self._approve(grant)
        real = device.create_key_within_cap
        nested = {}

        def poll_meanwhile(*args, **kwargs):
            if "response" not in nested:
                nested["response"] = self._poll(grant)
            return real(*args, **kwargs)

        with mock.patch.object(device, "create_key_within_cap", side_effect=poll_meanwhile):
            first = self._poll(grant)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(nested["response"].status_code, 400)
        self.assertEqual(nested["response"].json(), {"error": "invalid_grant"})
        self.assertEqual(APIKey.objects.count(), 1)
        # Later polls: delivered exactly once.
        self.assertEqual(self._poll(grant).json(), {"error": "invalid_grant"})

    def test_deny_racing_an_approval_does_not_overwrite_it(self):
        grant = self._start()
        real_find = device._find_pending
        state = {"approved": False}

        def find_then_approve(raw):
            found = real_find(raw)
            if not state["approved"]:
                state["approved"] = True
                other_tab = APIClient()
                other_tab.force_login(self.user)
                self._approve(grant, browser=other_tab)
            return found

        with mock.patch.object(device, "_find_pending", side_effect=find_then_approve):
            response = self.browser.post("/device/deny", {"user_code": grant["user_code"]})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Already handled", status_code=400)
        self.assertEqual(self._row(grant).status, DeviceCode.STATUS_APPROVED)
        self.assertEqual(self._poll(grant).status_code, 200)

    def test_approved_grant_is_redeemed_before_slow_down(self):
        # The terminal polled a moment ago (pending); approval then lands
        # and an early poll must still collect the key, not slow_down.
        grant = self._start()
        self.assertEqual(self._poll(grant).json(), {"error": "authorization_pending"})
        self._approve(grant)
        self.assertEqual(self._poll(grant).status_code, 200)


class DeviceKeyCapTests(_DeviceTestBase):
    """dev-4, auth-4, ctr-13 (device part): device keys count toward the
    plan's key cap; at the cap the consent page asks for a key to replace."""

    def test_first_login_on_free_plan_needs_no_replacement(self):
        grant = self._start()
        page = self.browser.get("/device", {"user_code": grant["user_code"]})
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Choose a key to replace")
        self._approve(grant)
        self.assertEqual(self._poll(grant).status_code, 200)

    def test_consent_at_cap_lists_keys_to_replace(self):
        prod = self._key("prod-agent")
        APIKey.objects.filter(pk=prod.pk).update(last_used_at=None)
        grant = self._start()
        page = self.browser.get("/device", {"user_code": grant["user_code"]})
        self.assertContains(page, "Choose a key to replace")
        self.assertContains(page, "prod-agent")
        self.assertContains(page, prod.key_prefix)
        self.assertContains(page, f'name="replace_key" value="{prod.pk}"')
        self.assertContains(page, "last used never")

    def test_approve_at_cap_without_choosing_a_key_is_refused(self):
        self._key("prod-agent")
        grant = self._start()
        response = self._approve(grant)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Choose a key to replace", status_code=400)
        self.assertEqual(self._row(grant).status, DeviceCode.STATUS_PENDING)
        self.assertEqual(APIKey.objects.count(), 1)

    def test_replaced_key_is_revoked_when_the_new_key_is_issued(self):
        old = self._key("Terminal login", device_issued=True)
        grant = self._start()
        response = self._approve(grant, replace_key=str(old.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, old.key_prefix)
        # Not before the terminal collects its key.
        old.refresh_from_db()
        self.assertTrue(old.is_active)
        token = self._poll(grant)
        self.assertEqual(token.status_code, 200)
        old.refresh_from_db()
        self.assertFalse(old.is_active)
        new = APIKey.verify(token.json()["api_key"])
        self.assertTrue(new.is_active)
        self.assertEqual(APIKey.objects.filter(workspace=self.workspace, is_active=True).count(), 1)
        usage = self.terminal.get(
            "/api/v1/usage", HTTP_AUTHORIZATION=f"Bearer {token.json()['api_key']}"
        ).json()
        self.assertEqual((usage["keys_used"], usage["keys_cap"]), (1, 1))

    def test_cannot_replace_another_workspaces_key(self):
        self._key("mine")
        other_user = self._user("other")
        theirs = self._key("theirs", workspace=get_or_create_workspace(other_user))
        grant = self._start()
        response = self._approve(grant, replace_key=str(theirs.pk))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._row(grant).status, DeviceCode.STATUS_PENDING)
        theirs.refresh_from_db()
        self.assertTrue(theirs.is_active)

    def test_five_logins_on_free_plan_never_exceed_the_cap(self):
        # The original bypass: repeated `veilfile login` on a 1-key plan.
        previous = None
        for _ in range(5):
            grant = self._start()
            fields = {"replace_key": str(previous.pk)} if previous else {}
            self.assertEqual(self._approve(grant, **fields).status_code, 200)
            token = self._poll(grant)
            self.assertEqual(token.status_code, 200)
            previous = APIKey.verify(token.json()["api_key"])
        self.assertEqual(APIKey.objects.filter(workspace=self.workspace, is_active=True).count(), 1)
        # And the dashboard cannot add a second key either.
        response = self.browser.post("/api/v1/keys/", {"name": "dash"}, format="json")
        self.assertEqual(response.status_code, 402)

    def test_cap_shrinking_between_approval_and_poll_denies_cleanly(self):
        self.workspace.plan = "team"
        self.workspace.save(update_fields=["plan"])
        self._key("agent-1")
        grant = self._start()
        self.assertEqual(self._approve(grant).status_code, 200)
        self.workspace.plan = "free"
        self.workspace.save(update_fields=["plan"])
        response = self._poll(grant)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "access_denied")
        self.assertIn("limit", response.json()["error_description"])
        self.assertEqual(APIKey.objects.count(), 1)
        self.assertEqual(self._row(grant).status, DeviceCode.STATUS_DENIED)
        self.assertEqual(self._poll(grant).json(), {"error": "access_denied"})

    def test_key_created_after_approval_fills_cap_denies_cleanly(self):
        grant = self._start()
        self.assertEqual(self._approve(grant).status_code, 200)
        response = self.browser.post("/api/v1/keys/", {"name": "dash"}, format="json")
        self.assertEqual(response.status_code, 201)
        token = self._poll(grant)
        self.assertEqual(token.json()["error"], "access_denied")
        self.assertEqual(APIKey.objects.filter(is_active=True).count(), 1)

    def test_two_grants_replacing_the_same_key_issue_only_one(self):
        old = self._key("old")
        first, second = self._start(), self._start()
        self._approve(first, replace_key=str(old.pk))
        self._approve(second, replace_key=str(old.pk))
        self.assertEqual(self._poll(first).status_code, 200)
        self.assertEqual(self._poll(second).json()["error"], "access_denied")
        self.assertEqual(APIKey.objects.filter(is_active=True).count(), 1)

    def test_over_cap_workspace_can_rotate_but_not_grow(self):
        # Keys left over from a bigger plan: 3 active on a 1-key plan.
        keys = [self._key(f"legacy-{i}") for i in range(3)]
        grant = self._start()
        self.assertEqual(self._approve(grant).status_code, 400)
        self.assertEqual(self._approve(grant, replace_key=str(keys[0].pk)).status_code, 200)
        self.assertEqual(self._poll(grant).status_code, 200)
        self.assertEqual(APIKey.objects.filter(is_active=True).count(), 3)

    def test_unlimited_plan_needs_no_replacement(self):
        self.workspace.plan = "scale"
        self.workspace.save(update_fields=["plan"])
        for i in range(3):
            self._key(f"k{i}")
        grant = self._start()
        page = self.browser.get("/device", {"user_code": grant["user_code"]})
        self.assertNotContains(page, "Choose a key to replace")
        self._approve(grant)
        self.assertEqual(self._poll(grant).status_code, 200)
        self.assertEqual(APIKey.objects.filter(is_active=True).count(), 4)

    def test_suspended_approver_gets_no_key(self):
        grant = self._start()
        self._approve(grant)
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        response = self._poll(grant)
        self.assertEqual(response.json()["error"], "access_denied")
        self.assertEqual(APIKey.objects.count(), 0)


class DeviceConsentTests(_DeviceTestBase):
    """dev-1: the consent page resists phishing and describes the key."""

    def test_unknown_code_from_a_link_says_so_instead_of_asking_to_enter_it(self):
        # A link from 'veilfile login' carries the code; landing on the entry
        # form means it is unknown, expired or used, not that it must be typed.
        response = self.browser.get("/device?user_code=ABCD-EFGH")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "does not match a pending login")
        self.assertNotContains(response, "Enter the code shown in your terminal.")

    def test_issuance_records_requester_ip_and_user_agent(self):
        grant = self._start(
            REMOTE_ADDR="198.51.100.7", HTTP_USER_AGENT="veilfile-cli/0.3.0 (test)"
        )
        row = self._row(grant)
        self.assertEqual(row.requester_ip, "198.51.100.7")
        self.assertEqual(row.requester_user_agent, "veilfile-cli/0.3.0 (test)")

    def test_consent_page_warns_and_shows_who_asked(self):
        grant = self._start(
            REMOTE_ADDR="198.51.100.7", HTTP_USER_AGENT="veilfile-cli/0.3.0 (test)"
        )
        page = self.browser.get("/device", {"user_code": grant["user_code"]})
        self.assertEqual(page.status_code, 200)
        for needle in (
            "Approve only if you just ran",
            "veilfile login",
            "matches the one in your terminal",
            "choose Deny",
            "198.51.100.7",
            "veilfile-cli/0.3.0 (test)",
            "dev@example.com",
            grant["user_code"],
        ):
            self.assertContains(page, needle)

    def test_grant_summary_states_the_keys_real_power(self):
        grant = self._start()
        page = self.browser.get("/device", {"user_code": grant["user_code"]})
        for needle in (
            "Upload files",
            "List every artifact in the workspace and download",
            "Revoke artifacts",
            "List the workspace's API keys and revoke only the key in use",
            "does not expire",
            "limit of 1 API key",
        ):
            self.assertContains(page, needle)

    def test_requester_fields_are_escaped(self):
        grant = self._start(HTTP_USER_AGENT="<script>alert(1)</script>")
        page = self.browser.get("/device", {"user_code": grant["user_code"]})
        self.assertNotContains(page, "<script>alert(1)</script>")
        self.assertContains(page, "&lt;script&gt;")

    def test_one_click_approval_is_refused(self):
        grant = self._start()
        response = self.browser.post("/device/approve", {"user_code": grant["user_code"]})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Confirm that you ran veilfile login", status_code=400)
        self.assertEqual(self._row(grant).status, DeviceCode.STATUS_PENDING)

    def test_consent_form_requires_the_confirmation_checkbox(self):
        grant = self._start()
        page = self.browser.get("/device", {"user_code": grant["user_code"]})
        self.assertContains(page, 'type="checkbox" name="confirm" value="yes" required')
        # Deny skips the browser's required-field check.
        self.assertContains(page, "formnovalidate")

    def test_deny_from_consent_form_needs_no_confirmation(self):
        grant = self._start()
        response = self.browser.post("/device/deny", {"user_code": grant["user_code"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._poll(grant).json(), {"error": "access_denied"})


class DeviceTokenParsingTests(_DeviceTestBase):
    """dev-8: malformed token requests get RFC 8628 errors, never a 500."""

    def _raw(self, body, content_type="application/json"):
        return self.terminal.post("/api/v1/device/token", data=body, content_type=content_type)

    def test_json_array_body(self):
        response = self._raw("[1]")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "invalid_request"})

    def test_json_scalar_bodies(self):
        for body in ('"x"', "1", "null", "true"):
            response = self._raw(body)
            self.assertEqual(response.status_code, 400, body)
            self.assertEqual(response.json(), {"error": "invalid_request"}, body)

    def test_malformed_json(self):
        response = self._raw("{not json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "invalid_request"})

    def test_unsupported_content_type(self):
        response = self._raw("grant_type=x", content_type="text/plain")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "invalid_request"})

    def test_missing_or_non_string_fields(self):
        cases = [
            ({}, "invalid_request"),
            ({"device_code": "abc"}, "invalid_request"),
            ({"grant_type": GRANT}, "invalid_request"),
            ({"grant_type": GRANT, "device_code": ""}, "invalid_request"),
            ({"grant_type": GRANT, "device_code": ["abc"]}, "invalid_request"),
            ({"grant_type": GRANT, "device_code": 12}, "invalid_request"),
            ({"grant_type": ["x"], "device_code": "abc"}, "invalid_request"),
            ({"grant_type": "password", "device_code": "abc"}, "unsupported_grant_type"),
            ({"grant_type": GRANT, "device_code": "unknown"}, "invalid_grant"),
        ]
        for body, error in cases:
            response = self.terminal.post("/api/v1/device/token", body, format="json")
            self.assertEqual(response.status_code, 400, body)
            self.assertEqual(response.json(), {"error": error}, body)

    def test_form_encoded_poll_redeems_the_key(self):
        grant = self._start()
        self._approve(grant)
        response = self._raw(
            urlencode({"grant_type": GRANT, "device_code": grant["device_code"]}),
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["api_key"].startswith("vlf_"))

    def test_lone_surrogate_device_code_is_an_unknown_code(self):
        # JSON may carry "\ud800", which str.encode("utf-8") rejects.
        response = self._raw(json.dumps({"grant_type": GRANT, "device_code": "\ud800"}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "invalid_grant"})

    def test_form_body_over_djangos_field_limit(self):
        # Django refuses it (TooManyFieldsSent) with an HTML 400 and an
        # ERROR log line unless the view answers first.
        body = "&".join(f"f{i}=1" for i in range(1500))
        with self.assertNoLogs("django.security", level="ERROR"):
            response = self._raw(body, content_type="application/x-www-form-urlencoded")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "invalid_request"})


class DeviceAbuseLimitTests(_DeviceTestBase):
    """dev-9 (attempt limiting) and dev-5 (purge, issuance ceiling, slow_down)."""

    def test_wrong_code_lookups_are_limited_per_user(self):
        grant = self._start()
        for i in range(10):
            response = self.browser.get("/device", {"user_code": f"AAAA-AA{i:02d}"})
            self.assertEqual(response.status_code, 200)
        # Blocked now, even for a real code: a blocked caller learns nothing.
        response = self.browser.get("/device", {"user_code": grant["user_code"]})
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many attempts", status_code=429)
        self.assertEqual(self._approve(grant).status_code, 429)
        self.assertEqual(self._row(grant).status, DeviceCode.STATUS_PENDING)

    def test_wrong_code_approvals_are_limited(self):
        for i in range(10):
            response = self.browser.post(
                "/device/approve", {"user_code": f"BBBB-BB{i:02d}", "confirm": "yes"}
            )
            self.assertEqual(response.status_code, 400)
        response = self.browser.post(
            "/device/approve", {"user_code": "BBBB-BBZZ", "confirm": "yes"}
        )
        self.assertEqual(response.status_code, 429)

    def test_wrong_code_lookups_are_limited_per_ip_across_accounts(self):
        for n in range(3):
            browser = APIClient()
            browser.force_login(self._user(f"guesser{n}"))
            for i in range(10):
                browser.get(
                    "/device", {"user_code": f"CCCC-C{n}{i:02d}"}, REMOTE_ADDR="203.0.113.9"
                )
        fresh = APIClient()
        fresh.force_login(self._user("guesser-new"))
        response = fresh.get("/device", {"user_code": "DDDD-DDDD"}, REMOTE_ADDR="203.0.113.9")
        self.assertEqual(response.status_code, 429)
        # Another IP is unaffected.
        response = fresh.get("/device", {"user_code": "DDDD-DDDD"}, REMOTE_ADDR="203.0.113.10")
        self.assertEqual(response.status_code, 200)

    def test_valid_lookups_do_not_count(self):
        grant = self._start()
        for _ in range(25):
            response = self.browser.get("/device", {"user_code": grant["user_code"]})
            self.assertEqual(response.status_code, 200)

    def test_purge_removes_rows_a_day_after_expiry_but_keeps_keys(self):
        old_pending, _, _ = device._issue_device_code()
        old_consumed, _, _ = device._issue_device_code()
        fresh, _, _ = device._issue_device_code()
        issued = self._key("Terminal login", device_issued=True)
        DeviceCode.objects.filter(pk=old_consumed.pk).update(
            status=DeviceCode.STATUS_CONSUMED, api_key=issued
        )
        two_days_ago = timezone.now() - timedelta(days=2)
        DeviceCode.objects.filter(pk__in=[old_pending.pk, old_consumed.pk]).update(
            expires_at=two_days_ago
        )
        self.assertEqual(device.purge_stale_device_codes(), 2)
        self.assertEqual(list(DeviceCode.objects.values_list("pk", flat=True)), [fresh.pk])
        issued.refresh_from_db()
        self.assertTrue(issued.is_active)

    def test_issuance_purges_stale_rows_opportunistically(self):
        stale, _, _ = device._issue_device_code()
        DeviceCode.objects.filter(pk=stale.pk).update(
            expires_at=timezone.now() - timedelta(days=2)
        )
        self._start()
        self.assertFalse(DeviceCode.objects.filter(pk=stale.pk).exists())

    def test_live_pending_ceiling(self):
        with mock.patch.object(device, "_MAX_LIVE_PENDING_CODES", 2):
            self._start(REMOTE_ADDR="198.51.100.1")
            self._start(REMOTE_ADDR="198.51.100.2")
            response = self.terminal.post(
                "/api/v1/device/code", {}, format="json", REMOTE_ADDR="198.51.100.3"
            )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "60")

    def test_slow_down_counts_as_a_poll_and_grows_the_interval(self):
        grant = self._start()
        self.assertEqual(self._poll(grant).json(), {"error": "authorization_pending"})
        first_poll = self._row(grant).last_polled_at
        self.assertEqual(self._poll(grant).json(), {"error": "slow_down"})
        row = self._row(grant)
        self.assertEqual(row.interval_seconds, 10)
        self.assertGreaterEqual(row.last_polled_at, first_poll)


class DeviceReviewFollowupTests(_DeviceTestBase):
    """Follow-ups from the fix review: odd inputs that used to 500, the
    deploy overlap with the previous release, IPv6 rate-limit buckets, the
    pending ceiling and retention, and the grant summary's last gap."""

    def test_odd_replace_key_values_are_refused_not_a_500(self):
        self._key("prod-agent")
        grant = self._start()
        # "²" passes str.isdigit() but int() rejects it.
        for value in ("²", "٣", "9" * 30, "-1", "1.0", "1\x00"):
            response = self._approve(grant, replace_key=value)
            self.assertEqual(response.status_code, 400, repr(value))
            self.assertContains(response, "no longer active", status_code=400)
        self.assertEqual(self._row(grant).status, DeviceCode.STATUS_PENDING)

    def test_malformed_user_codes_never_reach_the_database(self):
        # A NUL byte makes Postgres raise (500) before the attempt counts.
        for raw in ("AB\x00CD", "AB\x00CDEFG", "ABCD-EFG", "ABCD-EFGHJ", "ABCD-EF0O"):
            with self.assertNumQueries(0):
                self.assertEqual(device._find_pending(raw), (None, "invalid"), repr(raw))
        for i in range(10):
            response = self.browser.get("/device", {"user_code": f"AAAA-AA\x00{i}"})
            self.assertEqual(response.status_code, 200)
        response = self.browser.get("/device", {"user_code": "AAAA-AAAA"})
        self.assertEqual(response.status_code, 429)

    def _legacy_approval(self, grant):
        """What the previous release's POST /device/approve leaves behind
        during the deploy overlap: an approved grant whose key was minted
        at approval, its raw value held only in that process's cache."""
        legacy = self._key("Terminal login", device_issued=True)
        DeviceCode.objects.filter(pk=self._row(grant).pk).update(
            status=DeviceCode.STATUS_APPROVED,
            workspace=self.workspace,
            approved_by=self.user,
            api_key=legacy,
        )
        return legacy

    def test_legacy_approval_revokes_the_undeliverable_key(self):
        grant = self._start()
        legacy = self._legacy_approval(grant)
        response = self._poll(grant)
        self.assertEqual(response.status_code, 200)
        new = APIKey.verify(response.json()["api_key"])
        self.assertNotEqual(new.pk, legacy.pk)
        legacy.refresh_from_db()
        self.assertFalse(legacy.is_active)
        self.assertEqual(APIKey.objects.filter(is_active=True).count(), 1)
        self.assertEqual(self._row(grant).api_key, new)

    def test_legacy_approval_refused_by_the_cap_still_revokes_the_orphan(self):
        self._key("prod-agent")
        grant = self._start()
        legacy = self._legacy_approval(grant)
        response = self._poll(grant)
        self.assertEqual(response.json()["error"], "access_denied")
        legacy.refresh_from_db()
        self.assertFalse(legacy.is_active)
        self.assertEqual(APIKey.objects.filter(is_active=True).count(), 1)

    def test_previous_release_insert_survives_the_migration(self):
        # Render runs migrate while the previous release still serves; its
        # INSERT names only the 0006 columns.
        now = timezone.now()
        table = connection.ops.quote_name(DeviceCode._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {table} (user_code, device_code_hash, status, "
                "expires_at, interval_seconds, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    "LEGACY22",
                    "0" * 64,
                    DeviceCode.STATUS_PENDING,
                    connection.ops.adapt_datetimefield_value(now + timedelta(minutes=10)),
                    5,
                    connection.ops.adapt_datetimefield_value(now),
                ],
            )
        row = DeviceCode.objects.get(user_code="LEGACY22")
        self.assertEqual((row.requester_ip, row.requester_user_agent), ("", ""))
        self.assertIsNone(row.replace_key)

    def test_ipv6_issuance_is_limited_per_slash_64(self):
        for i in range(device._CODE_ISSUE_LIMIT_PER_HOUR):
            self._start(REMOTE_ADDR=f"2001:db8:0:1::{i + 1:x}")
        response = self.terminal.post(
            "/api/v1/device/code", {}, format="json", REMOTE_ADDR="2001:db8:0:1:ffff::9"
        )
        self.assertEqual(response.status_code, 429)
        # A different /64, and IPv4, are separate buckets.
        self._start(REMOTE_ADDR="2001:db8:0:2::1")
        self._start(REMOTE_ADDR="198.51.100.4")

    def test_ipv6_wrong_code_lookups_are_limited_per_slash_64(self):
        for n in range(3):
            browser = APIClient()
            browser.force_login(self._user(f"v6guesser{n}"))
            for i in range(10):
                browser.get(
                    "/device",
                    {"user_code": f"CCCC-C{n}{i:02d}"},
                    REMOTE_ADDR=f"2001:db8:0:7::{n:x}{i:x}",
                )
        fresh = APIClient()
        fresh.force_login(self._user("v6guesser-new"))
        response = fresh.get("/device", {"user_code": "DDDD-DDDD"}, REMOTE_ADDR="2001:db8:0:7::abcd")
        self.assertEqual(response.status_code, 429)
        response = fresh.get("/device", {"user_code": "DDDD-DDDD"}, REMOTE_ADDR="2001:db8:0:8::1")
        self.assertEqual(response.status_code, 200)

    def test_a_thousand_pending_grants_do_not_lock_everyone_out(self):
        # The ceiling is a storage backstop, not a login switch someone
        # with a modest pool of addresses can flip for everybody.
        expires = timezone.now() + timedelta(minutes=10)
        DeviceCode.objects.bulk_create(
            DeviceCode(
                user_code=f"Z{i:07d}",
                device_code_hash=f"{i:064d}",
                expires_at=expires,
            )
            for i in range(1000)
        )
        self._start(REMOTE_ADDR="198.51.100.5")

    def test_grants_that_issued_no_key_are_purged_an_hour_after_expiry(self):
        issued = self._key("Terminal login", device_issued=True)
        rows = {}
        for name in ("pending_2h", "denied_2h", "consumed_2h", "pending_30m", "consumed_2d"):
            rows[name], _, _ = device._issue_device_code()
        now = timezone.now()
        updates = {
            "pending_2h": dict(expires_at=now - timedelta(hours=2)),
            "denied_2h": dict(
                expires_at=now - timedelta(hours=2), status=DeviceCode.STATUS_DENIED
            ),
            "consumed_2h": dict(
                expires_at=now - timedelta(hours=2),
                status=DeviceCode.STATUS_CONSUMED,
                api_key=issued,
            ),
            "pending_30m": dict(expires_at=now - timedelta(minutes=30)),
            "consumed_2d": dict(
                expires_at=now - timedelta(days=2),
                status=DeviceCode.STATUS_CONSUMED,
                api_key=issued,
            ),
        }
        for name, fields in updates.items():
            DeviceCode.objects.filter(pk=rows[name].pk).update(**fields)
        self.assertEqual(device.purge_stale_device_codes(), 3)
        self.assertEqual(
            set(DeviceCode.objects.values_list("pk", flat=True)),
            {rows["consumed_2h"].pk, rows["pending_30m"].pk},
        )
        issued.refresh_from_db()
        self.assertTrue(issued.is_active)

    def test_grant_summary_mentions_disconnecting_connected_apps(self):
        grant = self._start()
        page = self.browser.get("/device", {"user_code": grant["user_code"]})
        self.assertContains(page, "disconnect your apps")
        self.assertContains(page, "It cannot create other API keys, disconnect your apps, or change your billing or account settings.")
