"""Phase 2 tests: MCP-spec OAuth authorization server (django-oauth-toolkit).

Unit: PKCE S256 accept/reject + plain rejected, authorization-code
single-use, DCR validation, token-to-workspace resolution, dual-credential
revocation in both directions, rate limits, OAuth excluded from key caps.

Integration: full auth-code flow via the Django test client with a
DCR-registered client (register -> authorize -> code -> token with PKCE ->
/mcp tools/list -> 200), plus the unverified-user 403 and metadata
documents.
"""

import base64
import hashlib
import json
import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone
from oauth2_provider.models import get_access_token_model, get_application_model

from drop_core.models import APIKey, EmailVerification, get_or_create_workspace
from drop_core.oauth import OAUTH_SCOPE_MCP_USE, resolve_oauth_credential

User = get_user_model()
AccessToken = get_access_token_model()
Application = get_application_model()

_DCR_URL = "/o/register/"
_AUTHORIZE_URL = "/o/authorize/"
_TOKEN_URL = "/o/token/"
_REDIRECT_URI = "http://127.0.0.1:9876/callback"


def _pkce_pair():
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _authorize_params(client_id, challenge, method="S256"):
    return {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": _REDIRECT_URI,
        "scope": OAUTH_SCOPE_MCP_USE,
        "state": "test-state-123",
        "code_challenge": challenge,
        "code_challenge_method": method,
    }


class OAuthBaseTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="oauthuser", password="pw12345678")
        self.workspace = get_or_create_workspace(self.user)
        EmailVerification.objects.create(
            user=self.user,
            email="oauthuser@example.com",
            is_verified=True,
            verified_at=timezone.now(),
        )
        self.client = Client()

    def _register_client(self, name="Test MCP client", redirect_uri=_REDIRECT_URI):
        response = self.client.post(
            _DCR_URL,
            data=json.dumps(
                {
                    "client_name": name,
                    "redirect_uris": [redirect_uri],
                    "grant_types": ["authorization_code"],
                    "token_endpoint_auth_method": "none",
                }
            ),
            content_type="application/json",
        )
        return response

    def _approved_code(self, client_id, challenge, method="S256"):
        """Drive the authorize GET+POST as the verified user; return the code."""
        self.client.force_login(self.user)
        get_response = self.client.get(_AUTHORIZE_URL, _authorize_params(client_id, challenge, method))
        self.assertEqual(get_response.status_code, 200)
        post_response = self.client.post(
            _AUTHORIZE_URL,
            {
                "redirect_uri": _REDIRECT_URI,
                "scope": OAUTH_SCOPE_MCP_USE,
                "client_id": client_id,
                "state": "test-state-123",
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": method,
                "allow": "Authorize",
            },
        )
        self.assertEqual(post_response.status_code, 302)
        location = post_response["Location"]
        self.assertTrue(location.startswith(_REDIRECT_URI))
        query = parse_qs(urlparse(location).query)
        self.assertEqual(query.get("state"), ["test-state-123"])
        return query["code"][0]

    def _exchange_code(self, code, client_id, verifier):
        return self.client.post(
            _TOKEN_URL,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _REDIRECT_URI,
                "client_id": client_id,
                "code_verifier": verifier,
            },
        )


# ---------------------------------------------------------------------------
# DCR registration
# ---------------------------------------------------------------------------


class DcrTests(OAuthBaseTestCase):
    def test_register_public_client(self):
        response = self._register_client()
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIn("client_id", body)
        app = Application.objects.get(client_id=body["client_id"])
        self.assertEqual(app.name, "Test MCP client")
        self.assertEqual(app.client_type, Application.CLIENT_PUBLIC)
        self.assertIn(_REDIRECT_URI, app.redirect_uris)

    def test_bad_redirect_uri_rejected(self):
        response = self._register_client(redirect_uri="not-a-uri")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Application.objects.count(), 0)

    def test_dcr_rate_limited(self):
        for i in range(30):
            response = self._register_client(name=f"client-{i}")
            self.assertIn(response.status_code, (201, 400))
        response = self._register_client(name="client-over")
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)


# ---------------------------------------------------------------------------
# DCR redirect_uri validation (researcher report 2026-10-06)
# ---------------------------------------------------------------------------


class DcrRedirectUriValidationTests(OAuthBaseTestCase):
    ACCEPTED = [
        "https://app.example.com/callback",
        "https://app.example.com:8443/cb",
        "http://127.0.0.1:9876/callback",  # RFC 8252 native-app loopback
        "http://[::1]:9876/callback",
        "https://127.0.0.1/cb",
    ]
    REJECTED = [
        "http://app.example.com/cb",  # http, not loopback
        "http://localhost:9876/callback",  # localhost name, not literal IP
        "http://localhost/cb",
        "https://192.168.1.1/cb",  # private, even over https
        "http://10.0.0.5/cb",
        "https://172.16.0.1/cb",
        "https://169.254.169.254/cb",  # link-local
        "https://[fc00::1]/cb",  # unique-local
        "https://[::ffff:192.168.1.1]/cb",  # v4-mapped private
        "https://user:pass@example.com/cb",  # userinfo
        "https://example.com/cb#frag",  # fragment
        "javascript:alert(1)",  # odd scheme
        "ftp://example.com/cb",
        "not-a-uri",
        "https://",
        "https://singlelabel/cb",  # internal name
        "//example.com/cb",  # missing scheme
    ]

    def test_accepted_uris_register(self):
        for uri in self.ACCEPTED:
            with self.subTest(uri=uri):
                response = self._register_client(
                    name=f"ok-{abs(hash(uri)) % 100000}", redirect_uri=uri
                )
                self.assertEqual(response.status_code, 201, uri)
                self.assertEqual(response.json()["redirect_uris"], [uri])

    def test_rejected_uris_fail_closed(self):
        for uri in self.REJECTED:
            with self.subTest(uri=uri):
                before = Application.objects.count()
                response = self._register_client(
                    name=f"bad-{abs(hash(uri)) % 100000}", redirect_uri=uri
                )
                self.assertEqual(response.status_code, 400, uri)
                self.assertEqual(response.json()["error"], "invalid_redirect_uri")
                self.assertEqual(Application.objects.count(), before)

    def test_management_put_cannot_swap_in_evil_uri(self):
        response = self._register_client()
        body = response.json()
        client_id = body["client_id"]
        reg_token = body["registration_access_token"]
        management_url = body["registration_client_uri"]
        evil = {
            "client_name": "Test MCP client",
            "redirect_uris": ["http://evil.example/cb"],
            "grant_types": ["authorization_code"],
            "token_endpoint_auth_method": "none",
        }
        put_response = self.client.put(
            management_url,
            data=json.dumps(evil),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {reg_token}",
        )
        self.assertEqual(put_response.status_code, 400)
        self.assertEqual(put_response.json()["error"], "invalid_redirect_uri")
        app = Application.objects.get(client_id=client_id)
        self.assertEqual(app.redirect_uris, _REDIRECT_URI)


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


class PkceTests(OAuthBaseTestCase):
    def test_s256_accepted(self):
        response = self._register_client()
        client_id = response.json()["client_id"]
        verifier, challenge = _pkce_pair()
        code = self._approved_code(client_id, challenge)
        token_response = self._exchange_code(code, client_id, verifier)
        self.assertEqual(token_response.status_code, 200)
        self.assertIn("access_token", token_response.json())

    def test_wrong_verifier_rejected(self):
        response = self._register_client()
        client_id = response.json()["client_id"]
        _, challenge = _pkce_pair()
        code = self._approved_code(client_id, challenge)
        token_response = self._exchange_code(code, client_id, "wrong-verifier")
        self.assertEqual(token_response.status_code, 400)
        self.assertEqual(token_response.json().get("error"), "invalid_grant")

    def test_plain_challenge_method_rejected(self):
        self.client.force_login(self.user)
        response = self._register_client()
        client_id = response.json()["client_id"]
        verifier, _ = _pkce_pair()
        get_response = self.client.get(
            _AUTHORIZE_URL, _authorize_params(client_id, verifier, method="plain")
        )
        self.assertEqual(get_response.status_code, 200)
        # The plain method is refused when the approval is submitted.
        post_response = self.client.post(
            _AUTHORIZE_URL,
            {
                "redirect_uri": _REDIRECT_URI,
                "scope": OAUTH_SCOPE_MCP_USE,
                "client_id": client_id,
                "state": "test-state-123",
                "response_type": "code",
                "code_challenge": verifier,
                "code_challenge_method": "plain",
                "allow": "Authorize",
            },
        )
        self.assertEqual(post_response.status_code, 302)
        location = post_response["Location"]
        # Refused: an error redirect, never a code.
        query = parse_qs(urlparse(location).query)
        self.assertEqual(query.get("error"), ["invalid_request"])
        self.assertNotIn("code", query)

    def test_authorization_code_single_use(self):
        response = self._register_client()
        client_id = response.json()["client_id"]
        verifier, challenge = _pkce_pair()
        code = self._approved_code(client_id, challenge)
        first = self._exchange_code(code, client_id, verifier)
        self.assertEqual(first.status_code, 200)
        second = self._exchange_code(code, client_id, verifier)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.json().get("error"), "invalid_grant")


# ---------------------------------------------------------------------------
# Token -> workspace resolution + scope enforcement
# ---------------------------------------------------------------------------


class TokenResolutionTests(OAuthBaseTestCase):
    def test_resolve_valid_token(self):
        app = Application.objects.create(
            name="t",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris=_REDIRECT_URI,
            user=self.user,
        )
        token = AccessToken.objects.create(
            user=self.user,
            application=app,
            token=secrets.token_urlsafe(32),
            expires=timezone.now() + timedelta(hours=1),
            scope=OAUTH_SCOPE_MCP_USE,
        )
        adapter = resolve_oauth_credential(token.token)
        self.assertIsNotNone(adapter)
        self.assertTrue(adapter.is_oauth)
        self.assertEqual(adapter.workspace, self.workspace)

    def test_unknown_token_rejected(self):
        self.assertIsNone(resolve_oauth_credential("nope-not-a-token"))

    def test_expired_token_rejected(self):
        app = Application.objects.create(
            name="t",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris=_REDIRECT_URI,
            user=self.user,
        )
        token = AccessToken.objects.create(
            user=self.user,
            application=app,
            token=secrets.token_urlsafe(32),
            expires=timezone.now() - timedelta(seconds=1),
            scope=OAUTH_SCOPE_MCP_USE,
        )
        self.assertIsNone(resolve_oauth_credential(token.token))

    def test_wrong_scope_rejected(self):
        app = Application.objects.create(
            name="t",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris=_REDIRECT_URI,
            user=self.user,
        )
        token = AccessToken.objects.create(
            user=self.user,
            application=app,
            token=secrets.token_urlsafe(32),
            expires=timezone.now() + timedelta(hours=1),
            scope="read",
        )
        self.assertIsNone(resolve_oauth_credential(token.token))


# ---------------------------------------------------------------------------
# Email verification gate
# ---------------------------------------------------------------------------


class VerificationGateTests(OAuthBaseTestCase):
    def test_unverified_user_gets_403_at_authorize(self):
        EmailVerification.objects.filter(user=self.user).update(is_verified=False)
        response = self._register_client()
        client_id = response.json()["client_id"]
        self.client.force_login(self.user)
        verifier, challenge = _pkce_pair()
        get_response = self.client.get(
            _AUTHORIZE_URL, _authorize_params(client_id, challenge)
        )
        self.assertEqual(get_response.status_code, 403)
        # No grant was created.
        from oauth2_provider.models import get_grant_model

        self.assertEqual(get_grant_model().objects.count(), 0)


# ---------------------------------------------------------------------------
# Full integration: DCR -> authorize -> token -> /mcp
# ---------------------------------------------------------------------------


class OAuthMcpIntegrationTests(OAuthBaseTestCase):
    def _mcp_token(self):
        response = self._register_client()
        client_id = response.json()["client_id"]
        verifier, challenge = _pkce_pair()
        code = self._approved_code(client_id, challenge)
        token_response = self._exchange_code(code, client_id, verifier)
        self.assertEqual(token_response.status_code, 200)
        return token_response.json()["access_token"]

    def test_full_flow_calls_mcp(self):
        access_token = self._mcp_token()
        response = self.client.post(
            "/mcp",
            data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("result", body)
        names = [t["name"] for t in body["result"]["tools"]]
        self.assertIn("upload_artifact", names)

    def test_oauth_token_hits_same_rest_api(self):
        access_token = self._mcp_token()
        response = self.client.get(
            "/api/v1/usage/",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["plan"], self.workspace.plan)

    def test_unauthenticated_mcp_has_www_authenticate(self):
        response = self.client.post(
            "/mcp",
            data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        challenge = response["WWW-Authenticate"]
        self.assertIn("Bearer", challenge)
        self.assertIn("/.well-known/oauth-protected-resource", challenge)

    def test_oauth_excluded_from_key_caps(self):
        before = self.workspace.api_keys.filter(
            is_active=True, device_issued=False
        ).count()
        self._mcp_token()
        after = self.workspace.api_keys.filter(
            is_active=True, device_issued=False
        ).count()
        self.assertEqual(before, after)

    def test_metadata_documents(self):
        auth_server = self.client.get("/.well-known/oauth-authorization-server")
        self.assertEqual(auth_server.status_code, 200)
        auth_doc = auth_server.json()
        self.assertIn(OAUTH_SCOPE_MCP_USE, auth_doc["scopes_supported"])
        self.assertIn("S256", auth_doc["code_challenge_methods_supported"])
        self.assertTrue(
            auth_doc["authorization_endpoint"].endswith("/o/authorize/")
        )

        protected = self.client.get("/.well-known/oauth-protected-resource")
        self.assertEqual(protected.status_code, 200)
        resource_doc = protected.json()
        self.assertEqual(resource_doc["resource"], "http://localhost:8000/mcp")
        self.assertEqual(resource_doc["scopes_supported"], [OAUTH_SCOPE_MCP_USE])
        self.assertIn("header", resource_doc["bearer_methods_supported"])


# ---------------------------------------------------------------------------
# Dual-credential revocation
# ---------------------------------------------------------------------------


class RevocationTests(OAuthBaseTestCase):
    def _mcp_token(self):
        response = self._register_client()
        client_id = response.json()["client_id"]
        verifier, challenge = _pkce_pair()
        code = self._approved_code(client_id, challenge)
        token_response = self._exchange_code(code, client_id, verifier)
        self.assertEqual(token_response.status_code, 200)
        return token_response.json()["access_token"]

    def _mcp_call(self, credential):
        return self.client.post(
            "/mcp",
            data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {credential}",
        )

    def test_key_revocation_leaves_oauth_tokens(self):
        # ctr-8: revoking an API key (e.g. `veilfile logout`) revokes only
        # that key; OAuth apps are disconnected from Connected apps.
        access_token = self._mcp_token()
        key, raw = APIKey.create_key(self.workspace, "agent")
        self.client.force_login(self.user)
        response = self.client.delete(f"/api/v1/keys/{key.pk}/")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self._mcp_call(raw).status_code, 401)
        self.assertEqual(self._mcp_call(access_token).status_code, 200)

    def test_grant_revocation_rejects_subsequent_use(self):
        response = self._register_client()
        client_id = response.json()["client_id"]
        verifier, challenge = _pkce_pair()
        code = self._approved_code(client_id, challenge)
        token_response = self._exchange_code(code, client_id, verifier)
        access_token = token_response.json()["access_token"]
        self.assertEqual(self._mcp_call(access_token).status_code, 200)
        self.client.force_login(self.user)
        response = self.client.post("/api/v1/oauth/revoke-grants/")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.json()["revoked_tokens"], 1)
        self.assertEqual(self._mcp_call(access_token).status_code, 401)
        # DCR client rows are kept: the client can re-authorize.
        self.assertTrue(Application.objects.filter(client_id=client_id).exists())

    def test_grant_revocation_via_oauth_credential_revokes_itself(self):
        access_token = self._mcp_token()
        response = self.client.post(
            "/api/v1/oauth/revoke-grants/",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._mcp_call(access_token).status_code, 401)
