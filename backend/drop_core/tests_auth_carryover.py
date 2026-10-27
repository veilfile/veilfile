"""Regression tests for the 2026-10-13 founder carryover auth fixes.

1. ``/signup`` redirects to ``/accounts/signup/`` (hand-typed URLs).
2. The login form accepts an email address as well as a username.
3. The device flow surfaces the email-verification gate before the
   approval POST — the 403 at approval is unchanged, but the consent and
   code-entry pages now say "verify your email first" up front.

Also covers the CMO packet shipped in the same deploy:
``/.well-known/mcp.json`` serves the directory manifest verbatim as JSON.
"""

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from drop_core.models import DeviceCode, EmailVerification, get_or_create_workspace

User = get_user_model()

MCP_MANIFEST = {
    "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
    "title": "Veilfile",
    "name": "io.github.veilfile/veilfile",
    "version": "1.0.0",
    "description": "Private, expiring artifact hosting for AI agents. Upload via API or MCP; links die on their own.",
    "websiteUrl": "https://veilfile.com",
    "remotes": [
        {
            "type": "streamable-http",
            "url": "https://veilfile.com/mcp/",
            "headers": [
                {
                    "name": "Authorization",
                    "isRequired": True,
                    "isSecret": True,
                    "description": "Bearer vlf_ key, issued in the Veilfile dashboard after email verification. Shown once; store as an env var.",
                }
            ],
        }
    ],
    "repository": {
        "source": "github",
        "url": "https://github.com/veilfile/veilfile",
    },
}


class SignupRedirectTests(TestCase):
    def test_signup_without_slash_redirects(self):
        response = self.client.get("/signup")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/accounts/signup/")

    def test_signup_with_slash_redirects(self):
        response = self.client.get("/signup/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/accounts/signup/")


class WellKnownMcpJsonTests(TestCase):
    def test_serves_manifest_as_json(self):
        response = self.client.get("/.well-known/mcp.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(json.loads(response.content), MCP_MANIFEST)

    def test_manifest_matches_server_json(self):
        # Mirrors server.json everywhere the CMO packet claims to; the
        # packet's header description says "vlf_ key" where server.json
        # says "API key" (one word). The packet is served verbatim per the
        # CMO ask, so only the shared fields are compared here.
        repo_root = Path(__file__).resolve().parents[2]
        with open(repo_root / "server.json") as f:
            server = json.load(f)
        response = self.client.get("/.well-known/mcp.json")
        served = json.loads(response.content)
        for key in ("title", "name", "version", "description", "websiteUrl"):
            self.assertEqual(served[key], server[key])
        self.assertEqual(len(served["remotes"]), len(server["remotes"]))
        for served_r, server_r in zip(served["remotes"], server["remotes"]):
            self.assertEqual(served_r["type"], server_r["type"])
            self.assertEqual(served_r["url"], server_r["url"])


class EmailOrUsernameLoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="sam",
            email="sam@example.com",
            password="pw12345678",
        )

    def _login(self, identifier, password="pw12345678"):
        return self.client.post(
            "/accounts/login/", {"username": identifier, "password": password}
        )

    def test_login_with_username_still_works(self):
        response = self._login("sam")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/app/")

    def test_login_with_email_works(self):
        response = self._login("sam@example.com")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/app/")

    def test_login_with_email_is_case_insensitive(self):
        response = self._login("SAM@EXAMPLE.COM")
        self.assertEqual(response.status_code, 302)

    def test_login_with_wrong_password_fails_for_email(self):
        response = self._login("sam@example.com", password="wrong")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_unknown_email_does_not_log_in(self):
        response = self._login("nobody@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_user_cannot_log_in_by_email(self):
        self.user.is_active = False
        self.user.save()
        response = self._login("sam@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_username_containing_at_falls_back_when_no_email_matches(self):
        at_user = User.objects.create_user(
            username="sam@home", email="other@example.com", password="pw12345678"
        )
        response = self._login("sam@home")
        self.assertEqual(response.status_code, 302)

    def test_login_form_invites_email(self):
        html = self.client.get("/accounts/login/").content.decode()
        self.assertIn("Username or email", html)


class DeviceFlowEarlyVerifyNoticeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="dev", email="dev@example.com", password="pw12345678"
        )
        get_or_create_workspace(self.user)

    def _start_grant(self):
        response = self.client.post("/api/v1/device/code", {}, format="json")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_code_entry_page_warns_unverified_user(self):
        # No EmailVerification row: the gate is invisible until approval.
        self.client.force_login(self.user)
        html = self.client.get("/device").content.decode()
        self.assertIn("Verify your email first", html)

    def test_code_entry_page_quiet_for_verified_user(self):
        EmailVerification.objects.create(
            user=self.user,
            email=self.user.email,
            is_verified=True,
            verified_at=timezone.now(),
        )
        self.client.force_login(self.user)
        html = self.client.get("/device").content.decode()
        self.assertNotIn("Verify your email first", html)

    def test_consent_page_warns_unverified_user(self):
        grant = self._start_grant()
        self.client.force_login(self.user)
        html = self.client.get("/device", {"user_code": grant["user_code"]}).content.decode()
        self.assertIn("Verify your email first", html)

    def test_consent_page_quiet_for_verified_user(self):
        EmailVerification.objects.create(
            user=self.user,
            email=self.user.email,
            is_verified=True,
            verified_at=timezone.now(),
        )
        grant = self._start_grant()
        self.client.force_login(self.user)
        html = self.client.get("/device", {"user_code": grant["user_code"]}).content.decode()
        self.assertNotIn("Verify your email first", html)

    def test_approval_still_refused_for_unverified_user(self):
        # The notice is earlier, but the backend gate itself is unchanged.
        grant = self._start_grant()
        self.client.force_login(self.user)
        response = self.client.post(
            "/device/approve",
            {"user_code": grant["user_code"], "confirm": "yes"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("Verify your email first", response.content.decode())
        self.assertEqual(
            DeviceCode.objects.get(
                user_code=grant["user_code"].replace("-", "")
            ).status,
            DeviceCode.STATUS_PENDING,
        )
