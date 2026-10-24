"""Regression tests for the auth-cluster fixes (review of 2026-10-07).

- auth-1 / deploy-2 / gap-3: only the OAuth routes Veilfile uses are
  mounted, DCR accepts only authorization_code (+ refresh_token), the
  password and implicit grants are refused, and tokens of unverified or
  inactive users never authorize.
- auth-3 / ctr-10: deactivating a user stops their API keys and tokens.
- auth-6 / deploy-3 / dev-5 (XFF): client_ip ignores client-supplied
  X-Forwarded-For entries and never returns a non-IP string; REST upload
  safety events record the client, not the proxy.
- auth-5 / auth-9: password reset and login (including /admin/login/)
  are throttled with a friendly 429 page.
- auth-8: email change needs the current password, notifies the old
  address, and only repoints user.email once the new one is verified.
- auth-14 / ctr-14 / auth-7: /api/v1/keys validation and credential limits.
- ctr-8 / web-9: revoking an API key leaves OAuth grants alone; the
  dashboard lists and revokes OAuth grants per app.
- gap-1: whitespace-smuggled DCR redirect URIs are rejected (POST and PUT).
- gap-2: password reset and password change revoke OAuth grants.
- gap-4: verification links work for addresses containing '/'.
- web-13: signup/login keep typed values and carry autocomplete tokens.

Review follow-ups (bottom of the file): look-alike usernames share the
login lock, throttle windows survive Django's stock DatabaseCache, a
password reset clears the lock, the password-change form shares the
login budget, IPv6 clients are limited per /64, NUL/control characters
are 400s, locks are logged, verification links carry a keyed email
digest, disconnecting an app with only a pending code is a 204, a
password reset signs the owner in past a lock, and (auth-7) a credential
can revoke only itself: an API key its own key, an OAuth token its own app.
"""

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail, signing
from django.core.cache import cache
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from oauth2_provider.models import (
    get_access_token_model,
    get_application_model,
    get_grant_model,
    get_refresh_token_model,
)

from drop_core.email_verification import (
    client_ip,
    make_verification_token,
    throttle_ip,
)
from drop_core.models import (
    APIKey,
    BlockedHash,
    EmailSendLog,
    EmailVerification,
    SafetyEvent,
    get_or_create_workspace,
)
from drop_core.oauth import OAUTH_SCOPE_MCP_USE, resolve_oauth_credential

User = get_user_model()
AccessToken = get_access_token_model()
RefreshToken = get_refresh_token_model()
Application = get_application_model()
Grant = get_grant_model()

_REDIRECT_URI = "http://127.0.0.1:9876/callback"
_PASSWORD = "pw12345678"


def _make_user(username, verified=True, email=None, **extra):
    email = email or f"{username}@example.com"
    user = User.objects.create_user(
        username=username, email=email, password=_PASSWORD, **extra
    )
    if verified is not None:
        EmailVerification.objects.create(
            user=user,
            email=email,
            is_verified=verified,
            verified_at=timezone.now() if verified else None,
        )
    return user


def _make_app(name="Test app", grant=None, client_type=None, secret=None):
    app = Application(
        name=name,
        client_type=client_type or Application.CLIENT_PUBLIC,
        authorization_grant_type=grant or Application.GRANT_AUTHORIZATION_CODE,
        redirect_uris=_REDIRECT_URI,
    )
    if secret is not None:
        app.client_secret = secret
    app.save()
    return app


def _issue_token(user, app, scope=OAUTH_SCOPE_MCP_USE, refresh=True):
    raw = secrets.token_urlsafe(32)
    token = AccessToken.objects.create(
        user=user,
        application=app,
        token=raw,
        expires=timezone.now() + timedelta(hours=1),
        scope=scope,
    )
    refresh_row = None
    if refresh:
        refresh_row = RefreshToken.objects.create(
            user=user,
            application=app,
            token=secrets.token_urlsafe(32),
            access_token=token,
        )
    return raw, token, refresh_row


def _mcp_tools_list(client, credential):
    return client.post(
        "/mcp",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {credential}",
    )


class _CacheIsolatedTestCase(TestCase):
    """Throttle counters live in the cache; keep them per-test. The
    throttle's lockout warnings are expected here, so keep them out of the
    test output (assertLogs still sees them)."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        throttle_logger = logging.getLogger("drop_core.throttle")
        self.addCleanup(throttle_logger.setLevel, throttle_logger.level)
        throttle_logger.setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# auth-1 / deploy-2 / gap-3: OAuth surface
# ---------------------------------------------------------------------------


class OAuthSurfaceTests(_CacheIsolatedTestCase):
    def _register(self, **overrides):
        body = {
            "client_name": "Test MCP client",
            "redirect_uris": [_REDIRECT_URI],
            "grant_types": ["authorization_code"],
            "token_endpoint_auth_method": "none",
        }
        body.update(overrides)
        body = {k: v for k, v in body.items() if v is not None}
        return self.client.post(
            "/o/register/", data=json.dumps(body), content_type="application/json"
        )

    def test_dcr_rejects_non_authorization_code_grants(self):
        for grants in (
            ["password"],
            ["implicit"],
            ["client_credentials"],
            ["urn:ietf:params:oauth:grant-type:device_code"],
            ["authorization_code", "password"],
            ["refresh_token", "client_credentials"],
        ):
            with self.subTest(grants=grants):
                response = self._register(grant_types=grants)
                self.assertEqual(response.status_code, 400, grants)
                self.assertEqual(response.json()["error"], "invalid_client_metadata")
        self.assertEqual(Application.objects.count(), 0)

    def test_dcr_accepts_authorization_code_with_refresh(self):
        response = self._register(grant_types=["authorization_code", "refresh_token"])
        self.assertEqual(response.status_code, 201)
        # Omitting grant_types still defaults to authorization_code.
        response = self._register(name="b", grant_types=None)
        self.assertEqual(response.status_code, 201)

    def test_dcr_management_put_rejects_password_grant(self):
        body = self._register().json()
        response = self.client.put(
            body["registration_client_uri"],
            data=json.dumps(
                {
                    "client_name": "Test MCP client",
                    "redirect_uris": [_REDIRECT_URI],
                    "grant_types": ["password"],
                    "token_endpoint_auth_method": "none",
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {body['registration_access_token']}",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_client_metadata")
        app = Application.objects.get(client_id=body["client_id"])
        self.assertEqual(app.authorization_grant_type, Application.GRANT_AUTHORIZATION_CODE)

    def test_password_grant_refused_for_preexisting_password_client(self):
        # A client registered before the fix (or by an operator) with the
        # password grant must not mint tokens, verified or not.
        user = _make_user("pwgrant", verified=False)
        app = _make_app(grant=Application.GRANT_PASSWORD)
        response = self.client.post(
            "/o/token/",
            {
                "grant_type": "password",
                "username": user.username,
                "password": _PASSWORD,
                "client_id": app.client_id,
                "scope": OAUTH_SCOPE_MCP_USE,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access_token", response.json())
        self.assertFalse(AccessToken.objects.filter(user=user).exists())

    def test_client_credentials_grant_refused(self):
        app = _make_app(
            grant=Application.GRANT_CLIENT_CREDENTIALS,
            client_type=Application.CLIENT_CONFIDENTIAL,
            secret="s3cret-value",
        )
        basic = base64.b64encode(f"{app.client_id}:s3cret-value".encode()).decode()
        response = self.client.post(
            "/o/token/",
            {"grant_type": "client_credentials"},
            HTTP_AUTHORIZATION=f"Basic {basic}",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(AccessToken.objects.count(), 0)

    def test_refresh_grant_still_works(self):
        user = _make_user("refresher")
        app = _make_app()
        _, _, refresh = _issue_token(user, app)
        response = self.client.post(
            "/o/token/",
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh.token,
                "client_id": app.client_id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(resolve_oauth_credential(response.json()["access_token"]))

    def test_unused_dot_routes_are_not_mounted(self):
        for path in (
            "/o/introspect/",
            "/o/device-authorization/",
            "/o/device/",
            "/o/applications/",
            "/o/applications/register/",
            "/o/authorized_tokens/",
            "/o/userinfo/",
            "/o/.well-known/openid-configuration",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404, path)
                self.assertEqual(self.client.post(path).status_code, 404, path)

    def test_used_routes_still_resolve(self):
        self.assertEqual(reverse("oauth2_provider:authorize"), "/o/authorize/")
        self.assertEqual(reverse("oauth2_provider:token"), "/o/token/")
        self.assertEqual(reverse("oauth2_provider:revoke-token"), "/o/revoke_token/")
        self.assertEqual(reverse("oauth2_provider:dcr-register"), "/o/register/")
        self.assertEqual(
            reverse("oauth2_provider:dcr-register-management", args=["abc"]),
            "/o/register/abc/",
        )

    def test_metadata_matches_what_the_server_accepts(self):
        doc = self.client.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(doc["grant_types_supported"], ["authorization_code", "refresh_token"])
        self.assertEqual(doc["response_types_supported"], ["code"])
        self.assertNotIn("introspection_endpoint", doc)
        self.assertTrue(doc["token_endpoint"].endswith("/o/token/"))
        self.assertTrue(doc["revocation_endpoint"].endswith("/o/revoke_token/"))
        self.assertTrue(doc["registration_endpoint"].endswith("/o/register/"))

    def test_revoke_token_endpoint_still_works(self):
        user = _make_user("revoker")
        app = _make_app()
        raw, token, _ = _issue_token(user, app, refresh=False)
        response = self.client.post(
            "/o/revoke_token/", {"token": raw, "client_id": app.client_id}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AccessToken.objects.filter(pk=token.pk).exists())

    def test_unverified_users_token_does_not_authorize(self):
        user = _make_user("unverified-oauth", verified=False)
        raw, _, _ = _issue_token(user, _make_app())
        self.assertIsNone(resolve_oauth_credential(raw))
        self.assertEqual(
            self.client.get("/api/v1/usage/", HTTP_AUTHORIZATION=f"Bearer {raw}").status_code,
            401,
        )
        self.assertEqual(_mcp_tools_list(self.client, raw).status_code, 401)

    def test_user_without_verification_row_token_does_not_authorize(self):
        user = _make_user("legacy-oauth", verified=None)
        raw, _, _ = _issue_token(user, _make_app())
        self.assertIsNone(resolve_oauth_credential(raw))

    def test_verified_users_token_still_authorizes(self):
        user = _make_user("verified-oauth")
        raw, _, _ = _issue_token(user, _make_app())
        self.assertIsNotNone(resolve_oauth_credential(raw))
        self.assertEqual(_mcp_tools_list(self.client, raw).status_code, 200)


# ---------------------------------------------------------------------------
# auth-3 / ctr-10: deactivated users
# ---------------------------------------------------------------------------


class InactiveUserCredentialTests(_CacheIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.user = _make_user("suspended")
        self.workspace = get_or_create_workspace(self.user)
        self.key, self.raw_key = APIKey.create_key(self.workspace, "agent")
        self.oauth_raw, _, _ = _issue_token(self.user, _make_app())
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

    def test_api_key_stops_working(self):
        self.assertIsNone(APIKey.verify(self.raw_key))
        response = self.client.get(
            "/api/v1/usage/", HTTP_AUTHORIZATION=f"Bearer {self.raw_key}"
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(_mcp_tools_list(self.client, self.raw_key).status_code, 401)

    def test_oauth_token_stops_working(self):
        self.assertIsNone(resolve_oauth_credential(self.oauth_raw))
        response = self.client.get(
            "/api/v1/artifacts/", HTTP_AUTHORIZATION=f"Bearer {self.oauth_raw}"
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(_mcp_tools_list(self.client, self.oauth_raw).status_code, 401)

    def test_reactivation_restores_access(self):
        self.user.is_active = True
        self.user.save(update_fields=["is_active"])
        self.assertIsNotNone(APIKey.verify(self.raw_key))
        self.assertIsNotNone(resolve_oauth_credential(self.oauth_raw))


# ---------------------------------------------------------------------------
# auth-6 / deploy-3 / dev-5: client_ip
# ---------------------------------------------------------------------------


# Real-world-shaped public addresses (documentation ranges are "private"
# to Python's ipaddress module, so they are avoided here).
_CLIENT = "81.2.69.160"
_CF_EDGE = "172.69.4.12"  # Cloudflare edge (public)
_RENDER_INTERNAL = "10.217.3.4"


class ClientIpTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _ip(self, xff=None, remote="10.217.0.9"):
        extra = {"REMOTE_ADDR": remote}
        if xff is not None:
            extra["HTTP_X_FORWARDED_FOR"] = xff
        return client_ip(self.factory.get("/", **extra))

    def test_no_forwarded_header_uses_remote_addr(self):
        self.assertEqual(self._ip(remote="8.8.8.8"), "8.8.8.8")

    def test_render_chain_resolves_the_visitor(self):
        # Cloudflare appends the visitor, Render's proxy appends the edge.
        self.assertEqual(self._ip(f"{_CLIENT}, {_CF_EDGE}"), _CLIENT)

    def test_spoofed_leftmost_entries_are_ignored(self):
        self.assertEqual(self._ip(f"1.2.3.4, {_CLIENT}, {_CF_EDGE}"), _CLIENT)
        self.assertEqual(
            self._ip(f"9.9.9.9, 8.8.4.4, 1.1.1.1, {_CLIENT}, {_CF_EDGE}"), _CLIENT
        )

    def test_trailing_render_internal_hop_is_skipped(self):
        self.assertEqual(
            self._ip(f"6.6.6.6, {_CLIENT}, {_CF_EDGE}, {_RENDER_INTERNAL}"), _CLIENT
        )

    def test_short_chain_uses_leftmost(self):
        # Fewer entries than trusted hops: nothing client-supplied present.
        self.assertEqual(self._ip(_CLIENT), _CLIENT)

    def test_non_ip_entry_falls_back_to_remote_addr(self):
        self.assertEqual(self._ip(f"unknown, {_CF_EDGE}", remote="10.0.0.7"), "10.0.0.7")
        self.assertEqual(self._ip(f"{_CLIENT}:5678, {_CF_EDGE}", remote="10.0.0.7"), "10.0.0.7")
        self.assertEqual(self._ip("", remote="10.0.0.7"), "10.0.0.7")

    def test_never_returns_a_non_ip_string(self):
        self.assertIsNone(self._ip("garbage, more-garbage", remote="not-an-ip"))
        self.assertIsNone(self._ip(None, remote=""))

    @override_settings(VEILFILE_TRUSTED_PROXY_HOPS=1)
    def test_hop_count_is_configurable(self):
        self.assertEqual(self._ip(f"1.2.3.4, {_CLIENT}"), _CLIENT)

    @override_settings(VEILFILE_TRUSTED_PROXY_HOPS=0)
    def test_zero_hops_ignores_forwarded_header(self):
        self.assertEqual(self._ip(f"{_CLIENT}, {_CF_EDGE}", remote="8.8.8.8"), "8.8.8.8")

    def test_ipv6_is_normalized(self):
        self.assertEqual(
            self._ip(f"2001:0db8:0000:0000:0000:0000:0000:0001, {_CF_EDGE}"),
            "2001:db8::1",
        )


class ClientIpThrottleIntegrationTests(_CacheIsolatedTestCase):
    """Rotating the client-controlled prefix no longer escapes per-IP limits."""

    def _xff(self, i):
        return {
            "REMOTE_ADDR": _RENDER_INTERNAL,
            "HTTP_X_FORWARDED_FOR": f"10.0.{i // 250}.{i % 250}, {_CLIENT}, {_CF_EDGE}",
        }

    def test_signup_limit_holds_with_spoofed_prefix(self):
        statuses = []
        for i in range(12):
            response = self.client.post(
                "/accounts/signup/",
                {
                    "username": f"spoof{i}",
                    "password1": "s3cur3pass!!",
                    "password2": "s3cur3pass!!",
                    "email": f"spoof{i}@example.com",
                },
                **self._xff(i),
            )
            statuses.append(response.status_code)
        self.assertEqual(statuses[:10], [302] * 10)
        self.assertEqual(statuses[10:], [429, 429])
        self.assertEqual(
            set(
                EmailSendLog.objects.filter(
                    purpose=EmailSendLog.PURPOSE_SIGNUP_ATTEMPT
                ).values_list("ip_address", flat=True)
            ),
            {_CLIENT},
        )

    def test_signup_with_non_ip_forwarded_value_records_valid_ip(self):
        response = self.client.post(
            "/accounts/signup/",
            {
                "username": "weirdxff",
                "password1": "s3cur3pass!!",
                "password2": "s3cur3pass!!",
                "email": "weirdxff@example.com",
            },
            REMOTE_ADDR="10.9.8.7",
            HTTP_X_FORWARDED_FOR="unknown, also-unknown",
        )
        self.assertEqual(response.status_code, 302)
        ips = set(EmailSendLog.objects.values_list("ip_address", flat=True))
        self.assertEqual(ips, {"10.9.8.7"})

    def test_safety_event_records_client_not_proxy(self):
        user = _make_user("uploader")
        _, raw_key = APIKey.create_key(get_or_create_workspace(user), "agent")
        bad = b"\x89PNG\r\n\x1a\n" + b"known-bad-bytes"
        BlockedHash.objects.create(sha256=hashlib.sha256(bad).hexdigest())
        env = {"VEILFILE_SAFETY_MODE": "log", "DROP_LOCAL_DIR": tempfile.mkdtemp()}
        self.addCleanup(shutil.rmtree, env["DROP_LOCAL_DIR"], True)
        with mock.patch.dict(os.environ, env):
            response = self.client.post(
                "/api/v1/artifacts/",
                {"file": SimpleUploadedFile("shot.png", bad, content_type="image/png")},
                HTTP_AUTHORIZATION=f"Bearer {raw_key}",
                **self._xff(1),
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(SafetyEvent.objects.get().uploader_ip, _CLIENT)

    def test_device_code_limit_holds_with_spoofed_prefix(self):
        statuses = [
            self.client.post(
                "/api/v1/device/code", {}, content_type="application/json", **self._xff(i)
            ).status_code
            for i in range(31)
        ]
        self.assertEqual(statuses[-1], 429)

    def test_dcr_limit_holds_with_spoofed_prefix(self):
        statuses = []
        for i in range(31):
            statuses.append(
                self.client.post(
                    "/o/register/",
                    data=json.dumps(
                        {
                            "client_name": f"c{i}",
                            "redirect_uris": [_REDIRECT_URI],
                            "grant_types": ["authorization_code"],
                            "token_endpoint_auth_method": "none",
                        }
                    ),
                    content_type="application/json",
                    **self._xff(i),
                ).status_code
            )
        self.assertEqual(statuses[-1], 429)


# ---------------------------------------------------------------------------
# auth-9: login throttling (site and admin)
# ---------------------------------------------------------------------------


class LoginThrottleTests(_CacheIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.user = _make_user("victim")

    def _login(self, username, password, path="/accounts/login/", **extra):
        data = {"username": username, "password": password}
        if path.startswith("/admin/"):
            data["next"] = "/admin/"
        return self.client.post(path, data, **extra)

    def test_username_locked_after_repeated_failures(self):
        for _ in range(10):
            self.assertEqual(self._login("victim", "wrong-guess").status_code, 200)
        response = self._login("victim", _PASSWORD)
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)
        self.assertContains(response, "Too many sign-in attempts", status_code=429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_lock_message_does_not_reveal_whether_account_exists(self):
        for _ in range(10):
            self._login("victim", "nope")
            self._login("no-such-user", "nope")
        real = self._login("victim", "nope")
        fake = self._login("no-such-user", "nope")
        self.assertEqual(real.status_code, 429)
        self.assertEqual(fake.status_code, 429)
        self.assertEqual(real.content, fake.content)

    def test_ip_locked_across_usernames_even_with_spoofed_prefix(self):
        statuses = []
        for i in range(31):
            statuses.append(
                self._login(
                    f"user{i}",
                    "wrong",
                    REMOTE_ADDR=_RENDER_INTERNAL,
                    HTTP_X_FORWARDED_FOR=f"10.1.0.{i}, {_CLIENT}, {_CF_EDGE}",
                ).status_code
            )
        self.assertEqual(statuses[:30], [200] * 30)
        self.assertEqual(statuses[30], 429)

    def test_success_clears_the_username_counter(self):
        for _ in range(9):
            self._login("victim", "wrong")
        self.assertEqual(self._login("victim", _PASSWORD).status_code, 302)
        self.client.logout()
        for _ in range(9):
            self.assertEqual(self._login("victim", "wrong").status_code, 200)
        self.assertEqual(self._login("victim", _PASSWORD).status_code, 302)

    def test_admin_login_is_throttled(self):
        User.objects.create_superuser(
            username="boss", email="boss@example.com", password=_PASSWORD
        )
        for _ in range(10):
            self.assertEqual(
                self._login("boss", "wrong", path="/admin/login/").status_code, 200
            )
        response = self._login("boss", _PASSWORD, path="/admin/login/")
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_admin_login_still_works(self):
        User.objects.create_superuser(
            username="boss", email="boss@example.com", password=_PASSWORD
        )
        response = self._login("boss", _PASSWORD, path="/admin/login/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/admin/")

    def test_login_page_get_never_throttled(self):
        for _ in range(12):
            self._login("victim", "wrong")
        self.assertEqual(self.client.get("/accounts/login/").status_code, 200)


# ---------------------------------------------------------------------------
# auth-5: password reset throttling
# ---------------------------------------------------------------------------


class PasswordResetThrottleTests(_CacheIsolatedTestCase):
    def setUp(self):
        super().setUp()
        _make_user("resetme", email="resetme@example.com")

    def _reset(self, email, **extra):
        return self.client.post("/accounts/password_reset/", {"email": email}, **extra)

    def test_per_address_limit(self):
        for _ in range(3):
            self.assertEqual(self._reset("resetme@example.com").status_code, 302)
        self.assertEqual(len(mail.outbox), 3)
        response = self._reset("resetme@example.com")
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many password reset requests", status_code=429)
        self.assertEqual(len(mail.outbox), 3)

    def test_per_address_limit_does_not_enumerate(self):
        for _ in range(3):
            self.assertEqual(self._reset("nobody@example.com").status_code, 302)
        self.assertEqual(self._reset("nobody@example.com").status_code, 429)

    def test_per_ip_limit(self):
        for i in range(10):
            self.assertEqual(self._reset(f"someone{i}@example.com").status_code, 302)
        response = self._reset("resetme@example.com")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(mail.outbox), 0)

    def test_per_ip_limit_holds_with_spoofed_prefix(self):
        for i in range(10):
            self._reset(
                f"someone{i}@example.com",
                REMOTE_ADDR=_RENDER_INTERNAL,
                HTTP_X_FORWARDED_FOR=f"10.2.0.{i}, {_CLIENT}, {_CF_EDGE}",
            )
        response = self._reset(
            "resetme@example.com",
            REMOTE_ADDR=_RENDER_INTERNAL,
            HTTP_X_FORWARDED_FOR=f"10.2.0.99, {_CLIENT}, {_CF_EDGE}",
        )
        self.assertEqual(response.status_code, 429)


# ---------------------------------------------------------------------------
# auth-8: email change
# ---------------------------------------------------------------------------


class EmailChangeTests(_CacheIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.user = _make_user("changer", email="old@example.com")
        self.client.force_login(self.user)

    def _change(self, email, password=_PASSWORD):
        data = {"action": "update_email", "email": email}
        if password is not None:
            data["password"] = password
        return self.client.post("/accounts/verify-email/", data)

    def test_change_requires_current_password(self):
        for password in (None, "", "wrong-password"):
            with self.subTest(password=password):
                response = self._change("attacker@evil.example", password=password)
                self.assertEqual(response.status_code, 200)
                self.user.refresh_from_db()
                self.assertEqual(self.user.email, "old@example.com")
                record = EmailVerification.objects.get(user=self.user)
                self.assertEqual(record.email, "old@example.com")
                self.assertTrue(record.is_verified)
        self.assertEqual(len(mail.outbox), 0)

    def test_change_keeps_account_email_until_verified_and_notifies_old(self):
        response = self._change("new@example.com")
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        # Password reset keeps going to the old, verified address.
        self.assertEqual(self.user.email, "old@example.com")
        record = EmailVerification.objects.get(user=self.user)
        self.assertEqual(record.email, "new@example.com")
        self.assertFalse(record.is_verified)
        by_recipient = {tuple(m.to): m for m in mail.outbox}
        self.assertEqual(set(by_recipient), {("new@example.com",), ("old@example.com",)})
        self.assertIn("/accounts/verify-email/", by_recipient[("new@example.com",)].body)
        notice = by_recipient[("old@example.com",)]
        self.assertNotIn("/accounts/verify-email/", notice.body)
        self.assertIn("password", notice.body.lower())

        mail.outbox.clear()
        self.client.logout()
        self.client.post("/accounts/password_reset/", {"email": "new@example.com"})
        self.assertEqual(len(mail.outbox), 0)
        self.client.post("/accounts/password_reset/", {"email": "old@example.com"})
        self.assertEqual([m.to for m in mail.outbox], [["old@example.com"]])

    def test_verification_link_repoints_account_email(self):
        self._change("new@example.com")
        verify_mail = next(m for m in mail.outbox if m.to == ["new@example.com"])
        path = re.search(r"https?://\S*(/accounts/verify-email/\S+)", verify_mail.body).group(1)
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")
        self.assertTrue(EmailVerification.objects.get(user=self.user).is_verified)

    def test_verification_refused_if_address_taken_meanwhile(self):
        self._change("new@example.com")
        verify_mail = next(m for m in mail.outbox if m.to == ["new@example.com"])
        path = re.search(r"https?://\S*(/accounts/verify-email/\S+)", verify_mail.body).group(1)
        User.objects.create_user(username="squatter", email="NEW@example.com", password=_PASSWORD)
        response = self.client.get(path)
        self.assertContains(response, "invalid or has expired")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old@example.com")
        self.assertFalse(EmailVerification.objects.get(user=self.user).is_verified)

    def test_unverified_typo_fix_mails_the_new_address_only(self):
        typo = _make_user("typo", verified=False, email="tpyo@example.com")
        self.client.force_login(typo)
        response = self.client.post(
            "/accounts/verify-email/",
            {"action": "update_email", "email": "typo@example.com", "password": _PASSWORD},
        )
        self.assertEqual(response.status_code, 302)
        # No notice to an address that was never verified.
        self.assertEqual([m.to for m in mail.outbox], [["typo@example.com"]])

    def test_repeated_wrong_passwords_lock_the_form(self):
        for _ in range(10):
            self._change("x@example.com", password="wrong")
        response = self._change("x@example.com")
        self.assertEqual(response.status_code, 429)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old@example.com")

    def test_form_asks_for_password(self):
        unverified = _make_user("formuser", verified=False)
        self.client.force_login(unverified)
        response = self.client.get("/accounts/verify-email/")
        self.assertContains(response, 'name="password"')
        self.assertContains(response, 'autocomplete="current-password"')


# ---------------------------------------------------------------------------
# gap-4: verification tokens for addresses containing '/'
# ---------------------------------------------------------------------------


class VerificationTokenTests(_CacheIsolatedTestCase):
    def test_signup_with_slash_in_email(self):
        response = self.client.post(
            "/accounts/signup/",
            {
                "username": "roleaddr",
                "password1": "s3cur3pass!!",
                "password2": "s3cur3pass!!",
                "email": "ops/billing@example.com",
            },
        )
        self.assertEqual(response.status_code, 302)
        path = re.search(
            r"https?://\S*(/accounts/verify-email/\S+)", mail.outbox[0].body
        ).group(1)
        self.assertEqual(self.client.get(path).status_code, 200)
        user = User.objects.get(username="roleaddr")
        self.assertTrue(EmailVerification.objects.get(user=user).is_verified)

    def test_token_is_url_safe_and_opaque(self):
        user = _make_user("opaque", verified=False, email="a/b+c@example.com")
        token = make_verification_token(user, user.email)
        self.assertNotIn("/", token)
        self.assertNotIn("example.com", token)

    def test_legacy_format_links_still_verify(self):
        # Links mailed before the format change (valid for 3 days).
        user = _make_user("legacylink", verified=False)
        legacy = signing.TimestampSigner(salt="veilfile-email-verification").sign(
            f"{user.pk}:{user.email}"
        )
        response = self.client.get(f"/accounts/verify-email/{legacy}/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(EmailVerification.objects.get(user=user).is_verified)

    def test_tampered_token_rejected(self):
        user = _make_user("tamper", verified=False)
        token = make_verification_token(user, user.email)
        response = self.client.get(f"/accounts/verify-email/{token[:-2]}xx/")
        self.assertContains(response, "invalid or has expired")
        self.assertFalse(EmailVerification.objects.get(user=user).is_verified)


# ---------------------------------------------------------------------------
# auth-14 / ctr-14 / auth-7 / ctr-8: key endpoints
# ---------------------------------------------------------------------------


class KeyEndpointTests(_CacheIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.user = _make_user("keyowner")
        self.workspace = get_or_create_workspace(self.user)
        self.workspace.plan = "scale"
        self.workspace.save()
        self.key, self.raw_key = APIKey.create_key(self.workspace, "ci")
        self.oauth_raw, _, _ = _issue_token(self.user, _make_app("Claude Code"))

    def _post_key(self, body, **extra):
        return self.client.post(
            "/api/v1/keys/", data=json.dumps(body), content_type="application/json", **extra
        )

    def test_invalid_names_are_400_not_500(self):
        self.client.force_login(self.user)
        for name in (123, ["a"], {"x": 1}, True, "x" * 256):
            with self.subTest(name=name):
                response = self._post_key({"name": name})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "invalid_request")
        response = self.client.post(
            "/api/v1/keys/", data=json.dumps(["not", "an", "object"]),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_valid_names_still_work(self):
        self.client.force_login(self.user)
        self.assertEqual(self._post_key({"name": "  ok  "}).json()["name"], "ok")
        self.assertEqual(self._post_key({}).json()["name"], "Untitled key")
        self.assertEqual(self._post_key({"name": None}).json()["name"], "Untitled key")
        self.assertEqual(self._post_key({"name": "x" * 255}).status_code, 201)

    def test_credential_callers_get_session_required(self):
        for credential in (self.raw_key, self.oauth_raw):
            with self.subTest(credential=credential[:4]):
                response = self._post_key(
                    {"name": "sneaky"}, HTTP_AUTHORIZATION=f"Bearer {credential}"
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["code"], "session_required")

    def test_oauth_token_cannot_list_or_revoke_keys(self):
        auth = {"HTTP_AUTHORIZATION": f"Bearer {self.oauth_raw}"}
        listed = self.client.get("/api/v1/keys/", **auth)
        self.assertEqual(listed.status_code, 403)
        self.assertEqual(listed.json()["code"], "session_required")
        deleted = self.client.delete(f"/api/v1/keys/{self.key.pk}/", **auth)
        self.assertEqual(deleted.status_code, 403)
        self.key.refresh_from_db()
        self.assertTrue(self.key.is_active)

    def test_api_key_logout_flow_still_works(self):
        auth = {"HTTP_AUTHORIZATION": f"Bearer {self.raw_key}"}
        listed = self.client.get("/api/v1/keys/", **auth).json()
        match = next(k for k in listed if k["key_prefix"] == self.raw_key[:12])
        self.assertEqual(
            self.client.delete(f"/api/v1/keys/{match['id']}/", **auth).status_code, 204
        )

    def test_key_revocation_leaves_oauth_grants_alone(self):
        # CLI logout (key auth) and dashboard revoke (session) both.
        self.assertEqual(
            self.client.delete(
                f"/api/v1/keys/{self.key.pk}/",
                HTTP_AUTHORIZATION=f"Bearer {self.raw_key}",
            ).status_code,
            204,
        )
        self.assertEqual(_mcp_tools_list(self.client, self.oauth_raw).status_code, 200)
        other_key, _ = APIKey.create_key(self.workspace, "laptop")
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.delete(f"/api/v1/keys/{other_key.pk}/").status_code, 204
        )
        self.assertEqual(_mcp_tools_list(self.client, self.oauth_raw).status_code, 200)
        self.assertTrue(RefreshToken.objects.filter(user=self.user, revoked__isnull=True).exists())


# ---------------------------------------------------------------------------
# web-9 / ctr-8: per-app OAuth grant listing and revocation
# ---------------------------------------------------------------------------


class OAuthGrantsApiTests(_CacheIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.user = _make_user("connected")
        self.app_a = _make_app("Claude Code")
        self.app_b = _make_app("Other client")
        self.raw_a, self.token_a, self.refresh_a = _issue_token(self.user, self.app_a)
        self.raw_b, self.token_b, self.refresh_b = _issue_token(self.user, self.app_b)
        self.stranger = _make_user("stranger")
        self.raw_s, _, _ = _issue_token(self.stranger, _make_app("Stranger app"))

    def test_lists_only_own_connected_apps(self):
        self.client.force_login(self.user)
        response = self.client.get("/api/v1/oauth/grants/")
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertEqual({r["name"] for r in rows}, {"Claude Code", "Other client"})
        row = next(r for r in rows if r["name"] == "Claude Code")
        self.assertEqual(row["id"], self.app_a.pk)
        self.assertEqual(row["client_id"], self.app_a.client_id)
        self.assertEqual(row["redirect_host"], "127.0.0.1:9876")
        self.assertIsNotNone(row["last_authorized_at"])

    def test_revoked_and_dead_grants_not_listed(self):
        self.refresh_b.revoke()
        self.client.force_login(self.user)
        names = {r["name"] for r in self.client.get("/api/v1/oauth/grants").json()}
        self.assertEqual(names, {"Claude Code"})

    def test_registration_tokens_not_listed(self):
        AccessToken.objects.create(
            user=self.user,
            application=_make_app("Registered only"),
            token=secrets.token_urlsafe(32),
            expires=timezone.now() + timedelta(days=365),
            scope="oauth2_provider:registration",
        )
        self.client.force_login(self.user)
        names = {r["name"] for r in self.client.get("/api/v1/oauth/grants").json()}
        self.assertNotIn("Registered only", names)

    def test_revoke_one_app(self):
        Grant.objects.create(
            user=self.user,
            application=self.app_a,
            code="pending-code",
            expires=timezone.now() + timedelta(minutes=5),
            redirect_uri=_REDIRECT_URI,
            scope=OAUTH_SCOPE_MCP_USE,
            code_challenge="x" * 43,
            code_challenge_method="S256",
        )
        self.client.force_login(self.user)
        response = self.client.delete(f"/api/v1/oauth/grants/{self.app_a.pk}/")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(_mcp_tools_list(self.client, self.raw_a).status_code, 401)
        self.refresh_a.refresh_from_db()
        self.assertIsNotNone(self.refresh_a.revoked)
        self.assertFalse(Grant.objects.filter(application=self.app_a).exists())
        # Other apps and other users are untouched.
        self.assertEqual(_mcp_tools_list(self.client, self.raw_b).status_code, 200)
        self.assertEqual(_mcp_tools_list(self.client, self.raw_s).status_code, 200)
        # DCR client row kept so the app can ask again.
        self.assertTrue(Application.objects.filter(pk=self.app_a.pk).exists())
        # Second revoke: nothing left.
        self.assertEqual(
            self.client.delete(f"/api/v1/oauth/grants/{self.app_a.pk}").status_code, 404
        )

    def test_cannot_revoke_another_users_app(self):
        stranger_app = AccessToken.objects.get(user=self.stranger).application
        self.client.force_login(self.user)
        response = self.client.delete(f"/api/v1/oauth/grants/{stranger_app.pk}/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(_mcp_tools_list(self.client, self.raw_s).status_code, 200)

    def test_session_only(self):
        key, raw_key = APIKey.create_key(get_or_create_workspace(self.user), "k")
        for credential in (raw_key, self.raw_a):
            auth = {"HTTP_AUTHORIZATION": f"Bearer {credential}"}
            self.assertIn(self.client.get("/api/v1/oauth/grants/", **auth).status_code, (401, 403))
            self.assertIn(
                self.client.delete(f"/api/v1/oauth/grants/{self.app_b.pk}/", **auth).status_code,
                (401, 403),
            )
        self.assertEqual(_mcp_tools_list(self.client, self.raw_b).status_code, 200)

    def test_delete_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.delete(f"/api/v1/oauth/grants/{self.app_a.pk}/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(_mcp_tools_list(self.client, self.raw_a).status_code, 200)


# ---------------------------------------------------------------------------
# auth-7 / web-9: consent copy
# ---------------------------------------------------------------------------


class ConsentCopyTests(_CacheIsolatedTestCase):
    def test_consent_page_describes_real_power_and_revocation(self):
        user = _make_user("consenter")
        client_id = self.client.post(
            "/o/register/",
            data=json.dumps(
                {
                    "client_name": "Some MCP app",
                    "redirect_uris": [_REDIRECT_URI],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "token_endpoint_auth_method": "none",
                }
            ),
            content_type="application/json",
        ).json()["client_id"]
        self.client.force_login(user)
        response = self.client.get(
            "/o/authorize/",
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": _REDIRECT_URI,
                "scope": OAUTH_SCOPE_MCP_USE,
                "state": "s",
                "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
                "code_challenge_method": "S256",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Connected apps")
        self.assertContains(response, "account email")
        self.assertContains(response, "cannot create, list, or revoke API keys")
        self.assertNotContains(response, "expires automatically")


# ---------------------------------------------------------------------------
# gap-2: password reset / change revoke OAuth grants
# ---------------------------------------------------------------------------


class PasswordChangeRevokesOAuthTests(_CacheIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.user = _make_user("rotator", email="rotator@example.com")
        self.workspace = get_or_create_workspace(self.user)
        self.key, self.raw_key = APIKey.create_key(self.workspace, "ci")
        self.oauth_raw, _, self.refresh = _issue_token(self.user, _make_app())

    def _assert_oauth_dead_key_alive(self):
        self.assertEqual(_mcp_tools_list(self.client, self.oauth_raw).status_code, 401)
        self.refresh.refresh_from_db()
        self.assertIsNotNone(self.refresh.revoked)
        # API keys are deliberately left alone (documented choice).
        self.assertEqual(
            self.client.get(
                "/api/v1/usage/", HTTP_AUTHORIZATION=f"Bearer {self.raw_key}"
            ).status_code,
            200,
        )

    def test_password_reset_revokes_oauth(self):
        self.client.post("/accounts/password_reset/", {"email": "rotator@example.com"})
        url = re.search(r"https?://\S*(/accounts/reset/\S+)", mail.outbox[0].body).group(1)
        response = self.client.get(url, follow=True)
        response = self.client.post(
            response.redirect_chain[-1][0],
            {"new_password1": "newpass12345678", "new_password2": "newpass12345678"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].endswith("/accounts/reset/done/"))
        self._assert_oauth_dead_key_alive()

    def test_password_change_revokes_oauth(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/accounts/password_change/",
            {
                "old_password": _PASSWORD,
                "new_password1": "newpass12345678",
                "new_password2": "newpass12345678",
            },
        )
        self.assertEqual(response.status_code, 302)
        self._assert_oauth_dead_key_alive()

    def test_failed_password_change_revokes_nothing(self):
        self.client.force_login(self.user)
        self.client.post(
            "/accounts/password_change/",
            {
                "old_password": "wrong",
                "new_password1": "newpass12345678",
                "new_password2": "newpass12345678",
            },
        )
        self.assertEqual(_mcp_tools_list(self.client, self.oauth_raw).status_code, 200)


# ---------------------------------------------------------------------------
# gap-1: whitespace-smuggled DCR redirect URIs
# ---------------------------------------------------------------------------


class DcrWhitespaceRedirectTests(_CacheIsolatedTestCase):
    SMUGGLED = [
        "https://ok.example.com/cb http://192.168.1.1/cb",
        "https://ok.example.com/cb http://localhost/cb",
        "https://ok.example.com/cb http://evil.example.com/cb",
        "https://ok.example.com/cb ",
        " https://ok.example.com/cb",
        "https://ok.example.com/c b",
        "https://ok.example.com/cb\thttp://evil.example.com/cb",
        "https://ok.example.com/cb\nhttp://evil.example.com/cb",
        "https://ok.example.com/cb http://evil.example.com/cb",
    ]

    def _body(self, uri):
        return json.dumps(
            {
                "client_name": "smuggler",
                "redirect_uris": [uri],
                "grant_types": ["authorization_code"],
                "token_endpoint_auth_method": "none",
            }
        )

    def test_register_rejects_whitespace(self):
        for uri in self.SMUGGLED:
            with self.subTest(uri=uri):
                response = self.client.post(
                    "/o/register/", data=self._body(uri), content_type="application/json"
                )
                self.assertEqual(response.status_code, 400, repr(uri))
                self.assertEqual(response.json()["error"], "invalid_redirect_uri")
        self.assertEqual(Application.objects.count(), 0)

    def test_management_put_rejects_whitespace(self):
        body = self.client.post(
            "/o/register/",
            data=self._body("https://ok.example.com/cb"),
            content_type="application/json",
        ).json()
        response = self.client.put(
            body["registration_client_uri"],
            data=self._body(self.SMUGGLED[0]),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {body['registration_access_token']}",
        )
        self.assertEqual(response.status_code, 400)
        app = Application.objects.get(client_id=body["client_id"])
        self.assertEqual(app.redirect_uris, "https://ok.example.com/cb")


# ---------------------------------------------------------------------------
# web-13: signup/login keep values and carry autocomplete tokens
# ---------------------------------------------------------------------------


class AccountFormAccessibilityTests(_CacheIsolatedTestCase):
    def test_signup_keeps_values_on_error(self):
        User.objects.create_user(username="taken", password=_PASSWORD)
        response = self.client.post(
            "/accounts/signup/",
            {
                "username": "taken",
                "password1": "s3cur3pass!!",
                "password2": "s3cur3pass!!",
                "email": "kept@example.com",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="taken"')
        self.assertContains(response, 'value="kept@example.com"')

    def test_signup_autocomplete_tokens(self):
        response = self.client.get("/accounts/signup/")
        self.assertContains(response, 'autocomplete="username"')
        self.assertContains(response, 'autocomplete="email"')
        self.assertContains(response, 'autocomplete="new-password"', count=2)

    def test_login_keeps_username_and_autocomplete(self):
        response = self.client.post(
            "/accounts/login/", {"username": "someone", "password": "wrong"}
        )
        self.assertContains(response, 'value="someone"')
        self.assertContains(response, 'autocomplete="username"')
        self.assertContains(response, 'autocomplete="current-password"')

    def test_reset_pages_autocomplete_tokens(self):
        self.assertContains(
            self.client.get("/accounts/password_reset/"), 'autocomplete="email"'
        )
        _make_user("acuser", email="acuser@example.com")
        self.client.post("/accounts/password_reset/", {"email": "acuser@example.com"})
        url = re.search(r"https?://\S*(/accounts/reset/\S+)", mail.outbox[0].body).group(1)
        response = self.client.get(url, follow=True)
        self.assertContains(response, 'autocomplete="new-password"', count=2)


# ---------------------------------------------------------------------------
# Review follow-ups
# ---------------------------------------------------------------------------


def _fullwidth(text):
    return "".join(chr(ord(ch) + 0xFEE0) for ch in text)


class LoginLockFollowUpTests(_CacheIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.user = _make_user("victim")

    def _login(self, username, password, **extra):
        return self.client.post(
            "/accounts/login/", {"username": username, "password": password}, **extra
        )

    def test_lookalike_username_cannot_dodge_the_lock(self):
        # Django's login form NFKC-folds the username: the fullwidth
        # spelling logs in to the same account.
        self.assertEqual(self._login(_fullwidth("victim"), _PASSWORD).status_code, 302)
        self.client.logout()
        for i in range(10):
            self.assertEqual(
                self._login("victim", "wrong", REMOTE_ADDR=f"198.51.100.{i}").status_code,
                200,
            )
        for name in (_fullwidth("victim"), "\U0001d42f\U0001d422ctim", " VICTIM "):
            with self.subTest(name=name):
                self.assertEqual(
                    self._login(name, _PASSWORD, REMOTE_ADDR="192.0.2.77").status_code,
                    429,
                )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_failures_spread_over_lookalikes_count_once(self):
        variants = ["victim", _fullwidth("victim"), "v" + _fullwidth("ictim")]
        for i in range(10):
            self._login(variants[i % 3], "wrong", REMOTE_ADDR=f"203.0.113.{i}")
        self.assertEqual(self._login("victim", _PASSWORD).status_code, 429)

    def test_password_reset_clears_the_username_lock(self):
        for i in range(10):
            self._login("victim", "wrong", REMOTE_ADDR=f"198.51.100.{i}")
        self.assertEqual(self._login("victim", _PASSWORD).status_code, 429)
        self.client.post("/accounts/password_reset/", {"email": "victim@example.com"})
        url = re.search(r"https?://\S*(/accounts/reset/\S+)", mail.outbox[0].body).group(1)
        response = self.client.get(url, follow=True)
        response = self.client.post(
            response.redirect_chain[-1][0],
            {"new_password1": "newpass12345678", "new_password2": "newpass12345678"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._login("victim", "newpass12345678").status_code, 302)

    def test_lock_is_logged_once_without_username_or_password(self):
        with self.assertNoLogs("drop_core.throttle", "WARNING"):
            for _ in range(9):
                self._login("victim", "guess-xyz")
        with self.assertLogs("drop_core.throttle", "WARNING") as logs:
            self._login("victim", "guess-xyz")
        self.assertEqual(len(logs.output), 1)
        self.assertIn("login-fail:user:", logs.output[0])
        self.assertNotIn("victim", logs.output[0])
        self.assertNotIn("guess-xyz", logs.output[0])


class PasswordChangeThrottleTests(_CacheIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.user = _make_user("pwchanger")
        self.client.force_login(self.user)

    def _change(self, old, new2="newpass12345678"):
        return self.client.post(
            "/accounts/password_change/",
            {
                "old_password": old,
                "new_password1": "newpass12345678",
                "new_password2": new2,
            },
        )

    def test_wrong_old_passwords_share_the_login_lock(self):
        for _ in range(10):
            self.assertEqual(self._change("wrong").status_code, 200)
        self.assertEqual(self._change(_PASSWORD).status_code, 429)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(_PASSWORD))
        login = Client().post(
            "/accounts/login/", {"username": "pwchanger", "password": _PASSWORD}
        )
        self.assertEqual(login.status_code, 429)

    def test_new_password_mistakes_do_not_count(self):
        for _ in range(12):
            self.assertEqual(self._change(_PASSWORD, new2="mismatch-123456").status_code, 200)
        self.assertEqual(self._change(_PASSWORD).status_code, 302)


_STOCK_DB_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "veilfile_throttle_test_cache",
    }
}


@override_settings(CACHES=_STOCK_DB_CACHE)
class StockDatabaseCacheWindowTests(_CacheIsolatedTestCase):
    """Django's stock DatabaseCache resets a key's expiry to 300 s on every
    incr(); the throttles must keep their full windows anyway."""

    def setUp(self):
        call_command("createcachetable", verbosity=0)
        super().setUp()
        _make_user("dbcache", email="dbcache@example.com")

    def _later(self, minutes):
        return mock.patch(
            "django.core.cache.backends.db.tz_now",
            return_value=timezone.now() + timedelta(minutes=minutes),
        )

    def _login(self, password):
        return self.client.post(
            "/accounts/login/", {"username": "dbcache", "password": password}
        )

    def test_login_lock_lasts_the_whole_window(self):
        for _ in range(10):
            self.assertEqual(self._login("wrong").status_code, 200)
        with self._later(6):
            self.assertEqual(self._login(_PASSWORD).status_code, 429)
        with self._later(16):
            self.assertEqual(self._login(_PASSWORD).status_code, 302)

    def test_password_reset_limit_lasts_the_hour(self):
        def reset():
            return self.client.post(
                "/accounts/password_reset/", {"email": "dbcache@example.com"}
            )

        for _ in range(3):
            self.assertEqual(reset().status_code, 302)
        with self._later(10):
            self.assertEqual(reset().status_code, 429)
        with self._later(61):
            self.assertEqual(reset().status_code, 302)


class Ipv6ThrottleTests(_CacheIsolatedTestCase):
    def _v6(self, host, prefix="2001:db8:1:2"):
        return {
            "REMOTE_ADDR": _RENDER_INTERNAL,
            "HTTP_X_FORWARDED_FOR": f"{prefix}::{host:x}, {_CF_EDGE}",
        }

    def test_throttle_ip_groups_ipv6_by_64(self):
        self.assertEqual(throttle_ip("2001:db8:1:2::5"), "2001:db8:1:2::/64")
        self.assertEqual(
            throttle_ip("2001:db8:1:2:aaaa:bbbb:cccc:dddd"), "2001:db8:1:2::/64"
        )
        self.assertEqual(throttle_ip(_CLIENT), _CLIENT)
        self.assertEqual(throttle_ip(f"::ffff:{_CLIENT}"), _CLIENT)
        self.assertIsNone(throttle_ip(None))
        self.assertIsNone(throttle_ip("not-an-ip"))

    def test_login_ip_limit_covers_the_64(self):
        statuses = [
            self.client.post(
                "/accounts/login/",
                {"username": f"user{i}", "password": "wrong"},
                **self._v6(i + 1),
            ).status_code
            for i in range(31)
        ]
        self.assertEqual(statuses[:30], [200] * 30)
        self.assertEqual(statuses[30], 429)
        other_net = self.client.post(
            "/accounts/login/",
            {"username": "user99", "password": "wrong"},
            **self._v6(1, prefix="2001:db8:1:3"),
        )
        self.assertEqual(other_net.status_code, 200)

    def test_signup_limit_covers_the_64(self):
        statuses = []
        for i in range(12):
            response = self.client.post(
                "/accounts/signup/",
                {
                    "username": f"six{i}",
                    "password1": "s3cur3pass!!",
                    "password2": "s3cur3pass!!",
                    "email": f"six{i}@example.com",
                },
                **self._v6(i + 1),
            )
            statuses.append(response.status_code)
        self.assertEqual(statuses[:10], [302] * 10)
        self.assertEqual(statuses[10:], [429, 429])
        # Audit rows keep the exact address.
        self.assertEqual(
            EmailSendLog.objects.filter(
                purpose=EmailSendLog.PURPOSE_SIGNUP_ATTEMPT
            ).values("ip_address").distinct().count(),
            10,
        )

    def test_dcr_limit_covers_the_64(self):
        statuses = [
            self.client.post(
                "/o/register/",
                data=json.dumps(
                    {
                        "client_name": f"c{i}",
                        "redirect_uris": [_REDIRECT_URI],
                        "grant_types": ["authorization_code"],
                        "token_endpoint_auth_method": "none",
                    }
                ),
                content_type="application/json",
                **self._v6(i + 1),
            ).status_code
            for i in range(31)
        ]
        self.assertEqual(statuses[-1], 429)

    def test_password_reset_ip_limit_covers_the_64(self):
        for i in range(10):
            self.client.post(
                "/accounts/password_reset/",
                {"email": f"someone{i}@example.com"},
                **self._v6(i + 1),
            )
        response = self.client.post(
            "/accounts/password_reset/", {"email": "x@example.com"}, **self._v6(99)
        )
        self.assertEqual(response.status_code, 429)


class NulAndControlCharacterTests(_CacheIsolatedTestCase):
    """Postgres text columns cannot hold NUL: these would be 500s in prod
    (SQLite stores them, so assert the 400 explicitly)."""

    def test_key_name_with_control_characters_is_400(self):
        user = _make_user("nulkeys")
        self.client.force_login(user)
        for name in ("a\x00b", "line\nbreak", "tab\there", "\x7f"):
            with self.subTest(name=name):
                response = self.client.post(
                    "/api/v1/keys/",
                    data=json.dumps({"name": name}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "invalid_request")
        self.assertFalse(APIKey.objects.filter(workspace__owner=user).exists())
        # Surrounding whitespace is still just stripped.
        response = self.client.post(
            "/api/v1/keys/",
            data=json.dumps({"name": " laptop\n"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "laptop")

    def test_dcr_metadata_with_nul_is_400(self):
        for body in (
            {"client_name": "evil\x00app"},
            {"client_name": "ok", "contacts": ["a\x00@example.com"]},
        ):
            with self.subTest(body=body):
                body.update(
                    {
                        "redirect_uris": [_REDIRECT_URI],
                        "grant_types": ["authorization_code"],
                        "token_endpoint_auth_method": "none",
                    }
                )
                response = self.client.post(
                    "/o/register/", data=json.dumps(body), content_type="application/json"
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], "invalid_client_metadata")
        self.assertFalse(Application.objects.exists())


class FollowUpMiscTests(_CacheIsolatedTestCase):
    def test_token_email_digest_is_keyed(self):
        user = _make_user("keyed", verified=False, email="guessable@example.com")
        token = make_verification_token(user, user.email)
        payload = signing.loads(token, salt="veilfile-email-verification")
        self.assertNotEqual(
            payload["e"], hashlib.sha256(b"guessable@example.com").hexdigest()[:32]
        )
        self.assertEqual(self.client.get(f"/accounts/verify-email/{token}/").status_code, 200)
        self.assertTrue(EmailVerification.objects.get(user=user).is_verified)

    def test_disconnect_app_with_only_a_pending_code(self):
        user = _make_user("halfway")
        app = _make_app("Half connected")
        Grant.objects.create(
            user=user,
            application=app,
            code="pending-only",
            expires=timezone.now() + timedelta(minutes=5),
            redirect_uri=_REDIRECT_URI,
            scope=OAUTH_SCOPE_MCP_USE,
            code_challenge="x" * 43,
            code_challenge_method="S256",
        )
        self.client.force_login(user)
        self.assertEqual(self.client.delete(f"/api/v1/oauth/grants/{app.pk}/").status_code, 204)
        self.assertFalse(Grant.objects.filter(application=app).exists())
        self.assertEqual(self.client.delete(f"/api/v1/oauth/grants/{app.pk}/").status_code, 404)


class ResetSignsInPastLockTests(_CacheIsolatedTestCase):
    """An attacker can lock a known username at will; the reset link is
    the owner's way back in even while the attacker keeps re-locking it."""

    def setUp(self):
        super().setUp()
        self.user = _make_user("relocked")

    def _lock(self):
        for i in range(10):
            Client().post(
                "/accounts/login/",
                {"username": "relocked", "password": "wrong"},
                REMOTE_ADDR=f"198.51.100.{i}",
            )

    def test_reset_signs_the_owner_in_while_the_attacker_relocks(self):
        self._lock()
        self.client.post("/accounts/password_reset/", {"email": "relocked@example.com"})
        url = re.search(r"https?://\S*(/accounts/reset/\S+)", mail.outbox[0].body).group(1)
        response = self.client.get(url, follow=True)
        response = self.client.post(
            response.redirect_chain[-1][0],
            {"new_password1": "newpass12345678", "new_password2": "newpass12345678"},
            follow=True,
        )
        self.assertTrue(response.redirect_chain[-1][0].endswith("/accounts/reset/done/"))
        self.assertContains(response, "you are signed in")
        self._lock()  # the attacker locks the username again at once
        self.assertEqual(self.client.get("/api/v1/usage/").status_code, 200)

    def test_complete_page_without_a_session_still_says_log_in(self):
        response = self.client.get("/accounts/reset/done/")
        self.assertContains(response, "Log in with the new one")


class CredentialManagesOnlyItselfTests(_CacheIsolatedTestCase):
    """auth-7: an API key may revoke itself but not its siblings; an OAuth
    token may disconnect its own app but not the user's other apps."""

    def setUp(self):
        super().setUp()
        self.user = _make_user("selfonly")
        self.workspace = get_or_create_workspace(self.user)
        self.workspace.plan = "scale"
        self.workspace.save()
        self.key, self.raw_key = APIKey.create_key(self.workspace, "ci")
        self.sibling, self.raw_sibling = APIKey.create_key(self.workspace, "laptop")
        self.raw_a, _, _ = _issue_token(self.user, _make_app("App A"))
        self.raw_b, _, _ = _issue_token(self.user, _make_app("App B"))

    def _bearer(self, raw):
        return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}

    def test_api_key_cannot_revoke_a_sibling_key(self):
        response = self.client.delete(
            f"/api/v1/keys/{self.sibling.pk}/", **self._bearer(self.raw_key)
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "session_required")
        self.sibling.refresh_from_db()
        self.assertTrue(self.sibling.is_active)
        self.assertEqual(
            self.client.get("/api/v1/keys/", **self._bearer(self.raw_sibling)).status_code,
            200,
        )

    def test_old_key_revokes_itself_after_relogin(self):
        # Both CLIs drop the key a re-login replaced by authenticating as
        # that old key (list, match own prefix, delete).
        auth = self._bearer(self.raw_sibling)
        listed = self.client.get("/api/v1/keys/", **auth).json()
        match = next(k for k in listed if k["key_prefix"] == self.raw_sibling[:12])
        self.assertEqual(
            self.client.delete(f"/api/v1/keys/{match['id']}/", **auth).status_code, 204
        )
        self.sibling.refresh_from_db()
        self.assertFalse(self.sibling.is_active)
        self.key.refresh_from_db()
        self.assertTrue(self.key.is_active)

    def test_dashboard_session_can_revoke_any_key(self):
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.delete(f"/api/v1/keys/{self.sibling.pk}/").status_code, 204
        )

    def test_oauth_logout_disconnects_only_its_own_app(self):
        response = self.client.post(
            "/api/v1/oauth/revoke-grants/", **self._bearer(self.raw_a)
        )
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.json()["revoked_tokens"], 1)
        self.assertEqual(_mcp_tools_list(self.client, self.raw_a).status_code, 401)
        self.assertEqual(_mcp_tools_list(self.client, self.raw_b).status_code, 200)

    def test_api_key_cannot_disconnect_oauth_apps(self):
        response = self.client.post(
            "/api/v1/oauth/revoke-grants/", **self._bearer(self.raw_key)
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "session_required")
        self.assertEqual(_mcp_tools_list(self.client, self.raw_a).status_code, 200)
        self.assertEqual(_mcp_tools_list(self.client, self.raw_b).status_code, 200)

    def test_session_revoke_grants_still_disconnects_every_app(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.post("/api/v1/oauth/revoke-grants/").status_code, 200)
        self.client.logout()
        self.assertEqual(_mcp_tools_list(self.client, self.raw_a).status_code, 401)
        self.assertEqual(_mcp_tools_list(self.client, self.raw_b).status_code, 401)

    def test_consent_copy_matches(self):
        from django.template.loader import render_to_string

        html = render_to_string("oauth/includes/grant_summary_oauth.html")
        self.assertIn("disconnect your other apps", html)
        self.assertNotIn("including itself", html)


class SuspendedUserTokenGrantTests(_CacheIsolatedTestCase):
    """ctr-10: suspending a user (is_active=False) also stops the token
    endpoint from minting for them, and reactivation undoes it."""

    def setUp(self):
        super().setUp()
        self.user = _make_user("frozen")
        self.app = _make_app()
        _, _, self.refresh = _issue_token(self.user, self.app)

    def _suspend(self, active=False):
        self.user.is_active = active
        self.user.save(update_fields=["is_active"])

    def _refresh(self):
        return self.client.post(
            "/o/token/",
            {
                "grant_type": "refresh_token",
                "refresh_token": self.refresh.token,
                "client_id": self.app.client_id,
            },
        )

    def test_refresh_refused_while_suspended_and_works_after(self):
        self._suspend()
        before = AccessToken.objects.count()
        response = self._refresh()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_grant")
        self.assertEqual(AccessToken.objects.count(), before)
        self._suspend(active=True)
        response = self._refresh()
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(resolve_oauth_credential(response.json()["access_token"]))

    def test_code_exchange_refused_while_suspended(self):
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        Grant.objects.create(
            user=self.user,
            application=self.app,
            code="issued-before-suspension",
            expires=timezone.now() + timedelta(minutes=5),
            redirect_uri=_REDIRECT_URI,
            scope=OAUTH_SCOPE_MCP_USE,
            code_challenge=challenge,
            code_challenge_method="S256",
        )
        self._suspend()
        before = AccessToken.objects.count()
        response = self.client.post(
            "/o/token/",
            {
                "grant_type": "authorization_code",
                "code": "issued-before-suspension",
                "redirect_uri": _REDIRECT_URI,
                "client_id": self.app.client_id,
                "code_verifier": verifier,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_grant")
        self.assertEqual(AccessToken.objects.count(), before)
