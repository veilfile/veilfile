"""OAuth hardening (review of 2026-10-22): the client address behind a direct-to-origin request, the revoke endpoint's
throttle, the counter that expires between get() and incr(), the DCR body cap and the redirect hosts a browser reads as an IP.

Every class sets longMessage = False; each message says what was found."""

import ast
import json
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase
from django.urls import get_resolver
from django.utils import timezone
from oauth2_provider.models import get_access_token_model, get_application_model

from drop_core import oauth
from drop_core.oauth import OAUTH_SCOPE_MCP_USE

Application = get_application_model()
AccessToken = get_access_token_model()

PEER = "8.8.4.4"  # the address Render's proxy appended: a request that skipped Cloudflare
VICTIM = "203.0.113.50"
EDGE = "172.69.4.12"  # a Cloudflare edge
INTERNAL = "10.217.0.9"
REDIRECT = "https://app.example.com/cb"
BACKEND = Path(settings.BASE_DIR)


def metadata(**changes):
    body = {
        "client_name": "Hardening test client",
        "redirect_uris": [REDIRECT],
        "grant_types": ["authorization_code"],
        "token_endpoint_auth_method": "none",
    }
    body.update(changes)
    return body


class _OAuthCase(TestCase):
    longMessage = False

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = Client(raise_request_exception=False)

    def register(self, body=None, raw=None, **extra):
        data = raw if raw is not None else json.dumps(metadata() if body is None else body)
        return self.client.post("/o/register/", data=data, content_type="application/json", **extra)

    def put(self, url, body=None, raw=None, token=None, **extra):
        data = raw if raw is not None else json.dumps(metadata() if body is None else body)
        if token:
            extra["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.put(url, data=data, content_type="application/json", **extra)


# ---------------------------------------------------------------------------
# A: the throttles' identity for a request that did not pass Cloudflare
# ---------------------------------------------------------------------------


class DirectToOriginThrottleTests(_OAuthCase):
    def spoofed(self, i):
        # Render appends the connecting address (PEER) after whatever the client typed.
        return {"REMOTE_ADDR": INTERNAL, "HTTP_X_FORWARDED_FOR": f"7.7.7.{i}, {PEER}"}

    def test_dcr_budget_holds_when_the_client_types_a_new_entry_every_time(self):
        statuses = [self.register(metadata(client_name=f"c{i}"), **self.spoofed(i)).status_code for i in range(32)]
        self.assertEqual(statuses[:30], [201] * 30, f"the first 30 registrations are allowed, got {statuses[:30]}")
        self.assertEqual(statuses[30:], [429, 429], f"registrations 31 and 32 from one connecting address must get 429, got {statuses[30:]}")

    def test_the_client_cannot_spend_someone_elses_budget(self):
        for i in range(30):
            self.register(metadata(client_name=f"p{i}"), REMOTE_ADDR=INTERNAL, HTTP_X_FORWARDED_FOR=f"{VICTIM}, {PEER}")
        self.assertEqual(
            self.register(metadata(client_name="again"), REMOTE_ADDR=INTERNAL, HTTP_X_FORWARDED_FOR=f"{VICTIM}, {PEER}").status_code,
            429,
        )
        # The victim arrives through Cloudflare: Cloudflare puts the victim's own address in CF-Connecting-IP.
        response = self.register(
            metadata(client_name="victim"),
            REMOTE_ADDR=INTERNAL,
            HTTP_X_FORWARDED_FOR=f"{VICTIM}, {EDGE}",
            HTTP_CF_CONNECTING_IP=VICTIM,
        )
        self.assertEqual(response.status_code, 201, "typing a victim's address in X-Forwarded-For must not use up the victim's budget")

    def test_through_cloudflare_the_budget_is_still_per_visitor(self):
        statuses = [
            self.register(
                metadata(client_name=f"c{i}"),
                REMOTE_ADDR=INTERNAL,
                HTTP_X_FORWARDED_FOR=f"9.9.9.{i}, {VICTIM}, {EDGE}",
                HTTP_CF_CONNECTING_IP=VICTIM,
            ).status_code
            for i in range(31)
        ]
        self.assertEqual(statuses[-1], 429)
        self.assertEqual(set(statuses[:-1]), {201})


# ---------------------------------------------------------------------------
# B: /o/revoke_token/ checks the client's secret (a PBKDF2 hash) and has the token endpoint's budget
# ---------------------------------------------------------------------------


class RevokeTokenThrottleTests(_OAuthCase):
    def revoke(self, client_id="nope", **extra):
        return self.client.post("/o/revoke_token/", {"token": "x", "client_id": client_id}, REMOTE_ADDR=extra.pop("REMOTE_ADDR", PEER), **extra)

    def test_the_301st_request_in_an_hour_from_one_address_is_refused(self):
        statuses = [self.revoke().status_code for _ in range(301)]
        self.assertNotIn(429, statuses[:300], "the first 300 requests are the budget, none of them may be refused")
        self.assertEqual(statuses[300], 429, f"request 301 must be refused with 429, got {statuses[300]}")

    def test_the_refusal_has_the_token_endpoints_shape(self):
        cache.set(f"oauth-revoke:{PEER}", 300, timeout=3600)
        response = self.revoke()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"], "rate_limited", "the body is the OAuth error JSON the token endpoint answers with")
        self.assertGreater(int(response["Retry-After"]), 0, "a 429 carries Retry-After in seconds")

    def test_an_ipv6_client_counts_by_its_slash_64(self):
        cache.set("oauth-revoke:2001:db8:1:2::/64", 300, timeout=3600)
        response = self.revoke(REMOTE_ADDR="2001:db8:1:2:aaaa:bbbb:cccc:dddd")
        self.assertEqual(response.status_code, 429, "addresses of one /64 share one budget")

    def test_it_does_not_spend_the_token_endpoints_budget(self):
        cache.set(f"oauth-revoke:{PEER}", 300, timeout=3600)
        response = self.client.post("/o/token/", {"grant_type": "authorization_code"}, REMOTE_ADDR=PEER)
        self.assertNotEqual(response.status_code, 429, "the two endpoints keep separate budgets")
        cache.set(f"oauth-token:{PEER}", 300, timeout=3600)
        cache.delete(f"oauth-revoke:{PEER}")
        self.assertNotEqual(self.revoke().status_code, 429, "the two endpoints keep separate budgets")

    def test_a_client_under_its_budget_still_revokes_its_token(self):
        app = Application.objects.create(
            name="app", client_type=Application.CLIENT_PUBLIC, authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris=REDIRECT,
        )
        token = AccessToken.objects.create(application=app, token="live-token-1", expires=timezone.now() + timedelta(hours=1), scope=OAUTH_SCOPE_MCP_USE)
        response = self.client.post("/o/revoke_token/", {"token": "live-token-1", "client_id": app.client_id}, REMOTE_ADDR=PEER)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AccessToken.objects.filter(pk=token.pk).exists(), "the token must be gone after a revoke")

    def test_a_form_post_without_a_csrf_token_still_reaches_the_view(self):
        strict = Client(enforce_csrf_checks=True)
        response = strict.post("/o/revoke_token/", {"token": "x", "client_id": "nope"}, REMOTE_ADDR=PEER)
        self.assertNotEqual(response.status_code, 403, "the endpoint is called by programs: it must stay csrf_exempt")


class OAuthRoutesThrottleTests(TestCase):
    """Every OAuth route that checks or hashes a client secret has a per-IP budget; a route added to the namespace has to be
    classified here, so that a new one is a decision and not an accident."""

    longMessage = False

    THROTTLED = {
        "token": oauth.ThrottledTokenView,
        "revoke-token": oauth.ThrottledRevokeTokenView,
        "dcr-register": oauth.ThrottledDynamicClientRegistrationView,
    }
    NO_PASSWORD_HASHING = {
        "authorize": oauth.VeilfileAuthorizationView,  # needs a signed-in user; does not authenticate the client
        "dcr-register-management": oauth.ValidatedDCRManagementView,  # looks the registration token up by its SHA-256
        "oauth-server-metadata": None,
        "oauth-server-metadata-issuer": None,
        "oauth-resource-metadata": None,
        "oauth-resource-metadata-path": None,
    }

    def routes(self):
        _prefix, resolver = get_resolver().namespace_dict["oauth2_provider"]
        return {pattern.name: pattern.callback.view_class for pattern in resolver.url_patterns}

    def test_every_route_is_classified(self):
        self.assertEqual(
            sorted(self.routes()), sorted({**self.THROTTLED, **self.NO_PASSWORD_HASHING}),
            "an OAuth route was added or removed: decide whether it hashes or checks a client secret (then throttle it per IP "
            "with _ip_allowed) and list it in THROTTLED or NO_PASSWORD_HASHING in backend/drop_core/tests_oauth_hardening.py",
        )

    def test_the_routes_that_check_a_secret_are_the_throttled_views(self):
        routes = self.routes()
        for name, view in self.THROTTLED.items():
            self.assertIsNotNone(view, f"{name}: the throttled view does not exist")
            self.assertTrue(issubclass(routes[name], view), f"{name} must be served by {view.__name__}, found {routes[name].__name__}")

    def test_the_throttled_routes_refuse_when_their_budget_is_spent(self):
        for path, bucket in (("/o/token/", "oauth-token"), ("/o/revoke_token/", "oauth-revoke"), ("/o/register/", "oauth-dcr-register")):
            with self.subTest(path=path):
                cache.clear()
                cache.set(f"{bucket}:{PEER}", 10_000, timeout=3600)
                response = Client().post(path, {}, REMOTE_ADDR=PEER)
                self.assertEqual(response.status_code, 429, f"POST {path} must answer 429 once the IP's {bucket} budget is spent")


# ---------------------------------------------------------------------------
# C: a counter whose hour ends between cache.get() and cache.incr()
# ---------------------------------------------------------------------------


class _EndsAfterRead:
    """A cache whose counter's hour ends right after get() read it: incr() then finds nothing, as a Django cache backend
    answers (ValueError) at the boundary of the window or when the database cache culls the row."""

    def __init__(self, real):
        self._real = real

    def get(self, key, *args, **kwargs):
        value = self._real.get(key, *args, **kwargs)
        self._real.delete(key)
        return value

    def __getattr__(self, name):
        return getattr(self._real, name)


class CounterWindowRaceTests(_OAuthCase):
    def test_a_counter_ending_between_get_and_incr_is_not_a_500(self):
        for path, bucket in (("/o/token/", "oauth-token"), ("/o/revoke_token/", "oauth-revoke"), ("/o/register/", "oauth-dcr-register")):
            with self.subTest(path=path):
                cache.set(f"{bucket}:{PEER}", 5, timeout=3600)
                with mock.patch.object(oauth, "cache", _EndsAfterRead(cache)):
                    response = self.client.post(path, {}, REMOTE_ADDR=PEER)
                self.assertNotEqual(response.status_code, 500, f"POST {path} answered 500 when its counter ended between get and incr")

    def test_the_window_starts_again_at_one(self):
        cache.set(f"oauth-token:{PEER}", 5, timeout=3600)
        request = RequestFactory().post("/o/token/", REMOTE_ADDR=PEER)
        with mock.patch.object(oauth, "cache", _EndsAfterRead(cache)):
            self.assertEqual(oauth._ip_allowed(request, "oauth-token", 300), (True, 0))
        self.assertEqual(cache.get(f"oauth-token:{PEER}"), 1, "the request that found its counter gone starts a new window at 1")

    def test_every_incr_in_the_backend_handles_a_counter_that_is_gone(self):
        """cache.incr() raises ValueError for a key that is missing or expired. Each call in the app sits in a try that
        catches it (or Exception), so a limiter never turns the hour boundary into a 500."""
        unguarded = []
        for path in sorted(BACKEND.rglob("*.py")):
            relative = path.relative_to(BACKEND)
            if relative.parts[0] in {"node_modules", "staticfiles", "media"} or "migrations" in relative.parts or relative.name.startswith("tests"):
                continue
            tree = ast.parse(path.read_text())
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "incr"):
                    continue
                if not self._caught(node, parents):
                    unguarded.append(f"{relative}:{node.lineno}")
        self.assertEqual(
            unguarded, [],
            "cache.incr() without `except ValueError` (restart the window with cache.set, as drop_core/device.py _bump does): "
            + ", ".join(unguarded),
        )

    @staticmethod
    def _caught(node, parents):
        child, parent = node, parents.get(node)
        while parent is not None:
            if isinstance(parent, ast.Try) and child in parent.body:
                for handler in parent.handlers:
                    names = [handler.type] if not isinstance(handler.type, ast.Tuple) else handler.type.elts
                    if handler.type is None or any(isinstance(n, ast.Name) and n.id in {"ValueError", "Exception"} for n in names):
                        return True
            child, parent = parent, parents.get(parent)
        return False


# ---------------------------------------------------------------------------
# D: DCR bodies are small, parse, and hold text the database can store
# ---------------------------------------------------------------------------

CAP = 16 * 1024


def padded(body, size):
    """The JSON of ``body`` followed by spaces, ``size`` bytes in all (trailing whitespace is valid JSON)."""
    raw = json.dumps(body).encode()
    assert len(raw) <= size
    return raw + b" " * (size - len(raw))


class DcrBodyTests(_OAuthCase):
    def registered(self):
        response = self.register()
        self.assertEqual(response.status_code, 201, response.content[:200])
        return response.json()

    def assertBadMetadata(self, response, what):
        self.assertEqual(response.status_code, 400, f"{what}: expected 400, got {response.status_code}")
        self.assertEqual(response.json()["error"], "invalid_client_metadata", f"{what}: {response.content[:200]!r}")

    # -- size ---------------------------------------------------------------

    def test_a_body_over_16_kib_is_refused_on_post(self):
        self.assertBadMetadata(self.register(raw=padded(metadata(), CAP + 1)), "POST of 16 KiB + 1 byte")
        self.assertEqual(Application.objects.count(), 0, "nothing is registered from a refused body")

    def test_a_body_of_exactly_16_kib_is_accepted(self):
        response = self.register(raw=padded(metadata(), CAP))
        self.assertEqual(response.status_code, 201, f"a 16 KiB body is within the cap: {response.content[:200]!r}")

    def test_a_large_list_of_redirect_uris_is_refused_before_it_is_validated(self):
        uris = [f"https://a{i}.example.com/" for i in range(5000)]
        self.assertGreater(len(json.dumps(metadata(redirect_uris=uris))), CAP)
        with mock.patch.object(oauth, "_validate_dcr_redirect_uri", side_effect=AssertionError("validated")):
            self.assertBadMetadata(self.register(metadata(redirect_uris=uris)), "POST with 5000 redirect URIs")

    def test_the_cap_is_checked_before_the_body_is_parsed(self):
        with mock.patch.object(oauth.json, "loads", side_effect=AssertionError("parsed")):
            post = self.register(raw=padded(metadata(), CAP + 1))
            put = self.put("/o/register/anything/", raw=padded(metadata(), CAP + 1))
        self.assertBadMetadata(post, "POST over the cap")
        self.assertBadMetadata(put, "PUT over the cap")

    def test_a_declared_length_over_the_cap_is_refused_without_reading_the_body(self):
        response = self.client.post(
            "/o/register/", data=json.dumps(metadata()), content_type="application/json", CONTENT_LENGTH=str(CAP * 10)
        )
        self.assertBadMetadata(response, "a Content-Length ten times the cap")

    def test_put_over_the_cap_is_refused_before_the_registration_token_is_checked(self):
        for token in (None, "not-a-registration-token"):
            with self.subTest(token=token):
                response = self.put("/o/register/anything/", raw=padded(metadata(), CAP + 1), token=token)
                self.assertBadMetadata(response, f"PUT over the cap with token {token!r} (401 would mean the token was checked first)")

    def test_put_over_the_cap_changes_nothing_and_keeps_the_registration_token(self):
        client = self.registered()
        url, token = client["registration_client_uri"], client["registration_access_token"]
        response = self.put(url, raw=padded(metadata(client_name="renamed"), CAP + 1), token=token)
        self.assertBadMetadata(response, "PUT over the cap with the right token")
        self.assertEqual(Application.objects.get(client_id=client["client_id"]).name, "Hardening test client")
        self.assertEqual(self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {token}").status_code, 200, "the token was not rotated")

    def test_put_within_the_cap_still_works(self):
        client = self.registered()
        response = self.put(client["registration_client_uri"], metadata(client_name="renamed"), token=client["registration_access_token"])
        self.assertEqual(response.status_code, 200, response.content[:200])
        self.assertEqual(Application.objects.get(client_id=client["client_id"]).name, "renamed")

    # -- nesting ------------------------------------------------------------

    def test_deeply_nested_json_is_a_400_on_post_and_put(self):
        for label, raw in (("arrays", b"[" * 12000), ("objects in arrays", b'{"a":' + b"[" * 12000)):
            self.assertLess(len(raw), CAP, "the body must be under the cap, so that it is the nesting that is refused")
            with self.subTest(body=label):
                self.assertBadMetadata(self.register(raw=raw), f"POST of {label} nested 12000 deep")
                self.assertBadMetadata(self.put("/o/register/anything/", raw=raw), f"PUT of {label} nested 12000 deep")

    # -- text the database cannot store -------------------------------------

    def test_lone_surrogates_are_a_400_on_post_and_put(self):
        # json.loads turns \ud800 into a str that cannot be encoded to UTF-8: the insert would raise (a 500).
        bodies = {
            "client_name": '{"client_name": "bad\\ud800", "redirect_uris": ["https://app.example.com/cb"], "token_endpoint_auth_method": "none"}',
            "key": '{"bad\\udfff": 1, "redirect_uris": ["https://app.example.com/cb"], "token_endpoint_auth_method": "none"}',
            "nested": '{"contacts": [["\\ud83d"]], "redirect_uris": ["https://app.example.com/cb"], "token_endpoint_auth_method": "none"}',
        }
        for label, raw in bodies.items():
            with self.subTest(where=label):
                self.assertBadMetadata(self.register(raw=raw), f"POST with a lone surrogate in {label}")
                self.assertBadMetadata(self.put("/o/register/anything/", raw=raw), f"PUT with a lone surrogate in {label}")
        self.assertEqual(Application.objects.count(), 0)

    def test_a_valid_surrogate_pair_and_other_unicode_are_kept(self):
        response = self.register(raw='{"client_name": "Caf\\u00e9 \\ud83d\\ude80", "redirect_uris": ["https://app.example.com/cb"], "token_endpoint_auth_method": "none"}')
        self.assertEqual(response.status_code, 201, f"an escaped pair is one character, not a lone surrogate: {response.content[:200]!r}")
        self.assertEqual(Application.objects.get().name, "Café 🚀")

    def test_nul_is_still_refused(self):
        self.assertBadMetadata(self.register(metadata(client_name="a\x00b")), "NUL in client_name")


# ---------------------------------------------------------------------------
# E: a host whose last label is a number is an IP address to a browser
# ---------------------------------------------------------------------------


class RedirectHostTests(_OAuthCase):
    # A browser (the URL standard's host parser) reads a host whose last label is all digits, or 0x and hex digits, as an IPv4
    # address in any of its shorthand spellings. ipaddress.ip_address() does not, so these passed as "DNS names".
    NUMERIC = (
        "127.1", "127.0.1", "10.1", "192.168.1", "169.254.1", "0.1", "0x7f.1", "0x7f.0.0.1", "0177.0.0.1",
        "127.000.000.001", "1.2.3.4.5", "example.0x", "example.0x7f", "example.0X7F", "127.1.", "127.1..",
    )
    ACCEPTED = (
        "https://app.example.com/cb", "https://app.example.com:8443/cb", "https://a.co/cb", "https://xn--e1afmkfd.xn--p1ai/cb",
        "https://app1.example2.com/cb", "https://example.0xg/cb", "https://example.co1/cb", "https://127.0.0.1/cb",
        "http://127.0.0.1:9876/callback", "http://[::1]:9876/callback", "https://[2606:4700:4700::1111]/cb",
    )

    def test_a_host_that_ends_in_a_number_is_refused(self):
        for host in self.NUMERIC:
            with self.subTest(host=host):
                reason = oauth._validate_dcr_redirect_uri(f"https://{host}/cb")
                self.assertIsNotNone(reason, f"https://{host}/cb: a browser reads this host as an IP address; it must be refused")

    def test_ordinary_hosts_and_ip_literals_are_still_accepted(self):
        for uri in self.ACCEPTED:
            with self.subTest(uri=uri):
                reason = oauth._validate_dcr_redirect_uri(uri)
                self.assertIsNone(reason, f"{uri}: refused with {reason!r}")

    def test_registration_refuses_it_as_invalid_redirect_uri(self):
        for host in ("127.1", "10.1", "0x7f.1", "0177.0.0.1"):
            with self.subTest(host=host):
                response = self.register(metadata(redirect_uris=[f"https://{host}/cb"]))
                self.assertEqual(response.status_code, 400, f"https://{host}/cb: expected 400, got {response.status_code}")
                self.assertEqual(response.json()["error"], "invalid_redirect_uri")
        self.assertEqual(Application.objects.count(), 0)

    def test_put_refuses_it_and_keeps_the_registered_uri(self):
        client = self.register().json()
        response = self.put(
            client["registration_client_uri"], metadata(redirect_uris=["https://192.168.1/cb"]), token=client["registration_access_token"]
        )
        self.assertEqual(response.status_code, 400, "PUT with https://192.168.1/cb: expected 400")
        self.assertEqual(response.json()["error"], "invalid_redirect_uri")
        self.assertEqual(Application.objects.get(client_id=client["client_id"]).redirect_uris, REDIRECT)
