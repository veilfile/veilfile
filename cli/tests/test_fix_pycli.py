"""Regression tests for the pycli review fixes. Stdlib unittest only.

Covers: hostile pull filenames, including Windows stream/device names and
malformed RFC 2231 parameters (pycli-1), overwrite refusal (pycli-2),
logout DELETE failures (pycli-3), malformed device-code fields (pycli-4),
redirect pinning to the same origin (host and port) and unparseable
redirects (pycli-5), atomic credential writes (pycli-6), real push/pull
round-trips, including names at the filesystem's length limit (pycli-7),
transient login polling errors and non-ASCII Retry-After (dev-7), 429
guidance (ctr-16), inherited VEILFILE_BASE_URL (ops-9), browser-open origin
checks, including URLs browsers parse differently from urllib (G5),
server text that would print as several lines, and revoking the previous
key on re-login, only on the server that issued it, with a warning when
VEILFILE_API_KEY holds that key (owner decision 3). Both suites refuse
every non-loopback connection (tests/offline.py).
"""

import email.parser
import email.policy
import errno
import http.server
import importlib
import io
import json
import os
import shutil
import socket
import stat
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from email.message import Message
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import veilfile  # noqa: E402
from tests.offline import NetworkUsedInTest, refuse_outbound_connections  # noqa: E402

OLD_KEY = "vlf_oldkey0123456789abcdef"
NEW_KEY = "vlf_newkey9876543210fedcba"
ENV_VARS = (
    "VEILFILE_API_KEY",
    "VEILFILE_BASE_URL",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)


# ---------------------------------------------------------------------------
# Scripted HTTP (no sockets)
# ---------------------------------------------------------------------------


def _message(headers):
    msg = Message()
    for name, value in (headers or {}).items():
        msg[name] = value
    return msg


class _Response:
    def __init__(self, status, raw, headers):
        self.status = status
        self._raw = raw
        self._headers = headers or {}

    def read(self, n=-1):
        return self._raw

    def getheaders(self):
        return list(self._headers.items())

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Scripted:
    """Replacement for veilfile._urlopen serving scripted responses.

    Items are exceptions (raised) or (status, body[, headers]); a bytes
    body is sent raw, anything else as JSON.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []  # (method, url, headers, raw body)

    def __call__(self, request, timeout=None):
        self.requests.append(
            (
                request.get_method(),
                request.full_url,
                dict(request.header_items()),
                request.data,
            )
        )
        if not self._responses:
            raise AssertionError(f"unexpected extra request: {request.full_url}")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item[0], item[1]
        headers = item[2] if len(item) > 2 else {}
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        if status >= 400:
            raise HTTPError(
                request.full_url, status, "error", _message(headers), io.BytesIO(raw)
            )
        return _Response(status, raw, headers)

    def calls(self, method, suffix):
        return [r for r in self.requests if r[0] == method and r[1].endswith(suffix)]


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def device_code(**overrides):
    payload = {
        "device_code": "raw-device-code-123",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://veilfile.com/device",
        "verification_uri_complete": "https://veilfile.com/device?user_code=ABCD-EFGH",
        "expires_in": 600,
        "interval": 5,
    }
    payload.update(overrides)
    return (200, payload)


def pending():
    return (400, {"error": "authorization_pending"})


def token(key=NEW_KEY):
    return (200, {"api_key": key})


def usage():
    return (200, {"plan": "free", "email": "lead@example.com", "email_verified": True})


def key_list(*keys):
    return (
        200,
        [
            {"id": index, "key_prefix": key[:12], "is_active": True}
            for index, key in enumerate(keys, start=3)
        ],
    )


class FixTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config_dir = self.root / "config"
        env = mock.patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"})
        env.start()
        self.addCleanup(env.stop)
        for name in ENV_VARS:
            os.environ.pop(name, None)
        # The default base URL is production: only the local test servers
        # may be reached.
        refuse_outbound_connections(self)
        self.browser = mock.Mock(return_value=True)
        for patcher in (
            mock.patch.object(veilfile, "config_dir", return_value=self.config_dir),
            mock.patch("webbrowser.open", self.browser),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def use_fake_clock(self):
        self.clock = FakeClock()
        for patcher in (
            mock.patch("time.sleep", self.clock.sleep),
            mock.patch("time.monotonic", self.clock.monotonic),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, argv, http=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                if http is None:
                    code = veilfile.main(argv)
                else:
                    with mock.patch.object(veilfile, "_urlopen", http):
                        code = veilfile.main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def stored_key(self):
        path = self.config_dir / "credentials"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))["api_key"]


# ---------------------------------------------------------------------------
# login: transient errors (dev-7), malformed fields (pycli-4)
# ---------------------------------------------------------------------------


class LoginPollingTests(FixTestCase):
    def setUp(self):
        super().setUp()
        self.use_fake_clock()

    def test_one_502_html_page_does_not_abort_login(self):
        http = Scripted(
            [
                device_code(),
                (502, b"<html>Bad gateway</html>", {"Content-Type": "text/html"}),
                token(),
                usage(),
            ]
        )
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.stored_key(), NEW_KEY)
        self.assertIn("retrying", err)
        self.assertNotIn("unexpected response", err)

    def test_429_retry_after_is_honoured(self):
        http = Scripted(
            [
                device_code(),
                (429, {"error": "Throttled.", "code": "rate_limited"}, {"Retry-After": "30"}),
                token(),
                usage(),
            ]
        )
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.clock.sleeps[0], 5)
        self.assertGreaterEqual(self.clock.sleeps[1], 30)

    def test_non_json_200_is_transient(self):
        http = Scripted(
            [device_code(), (200, b"<html>captive portal</html>"), token(), usage()]
        )
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.stored_key(), NEW_KEY)

    def test_backoff_grows_and_is_capped(self):
        http = Scripted(
            [device_code(expires_in=3600)]
            + [(503, b"unavailable")] * 6
            + [token(), usage()]
        )
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        waits = self.clock.sleeps[1:]
        self.assertEqual(waits, sorted(waits))
        self.assertGreater(waits[1], waits[0])
        self.assertLessEqual(max(waits), 60)
        self.assertEqual(waits[-1], 60)

    def test_persistent_5xx_ends_at_expiry_not_on_first_error(self):
        http = Scripted([device_code(expires_in=40)] + [(503, b"down")] * 20)
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        self.assertNotIn("unexpected response", err)
        self.assertGreater(len(http.calls("POST", "/device/token")), 1)
        self.assertIsNone(self.stored_key())

    def test_invalid_grant_is_terminal(self):
        http = Scripted([device_code(), (400, {"error": "invalid_grant"})])
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertEqual(len(http.calls("POST", "/device/token")), 1)
        self.assertIn("veilfile login", err)

    def test_unknown_4xx_error_is_terminal(self):
        http = Scripted([device_code(), (400, {"error": "unsupported_grant_type"})])
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertIn("Login failed", err)

    def test_non_integer_interval_and_expiry_fall_back_to_defaults(self):
        http = Scripted(
            [
                device_code(interval="5s", expires_in="ten minutes"),
                pending(),
                token(),
                usage(),
            ]
        )
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.clock.sleeps[:2], [5, 5])

    def test_interval_is_clamped(self):
        for raw, expected in ((10_000, 60), ("7", 7), (2.5, 2), (-3, 5), (None, 5)):
            with self.subTest(interval=raw):
                self.clock.sleeps.clear()
                http = Scripted([device_code(interval=raw), token(), usage()])
                code, _, err = self.run_cli(["login"], http)
                self.assertEqual(code, 0, err)
                self.assertEqual(self.clock.sleeps[0], expected)

    def test_json_4xx_without_rfc8628_error_fails_fast(self):
        http = Scripted([device_code(), (404, {"detail": "Not found."})])
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertEqual(len(http.calls("POST", "/device/token")), 1)
        self.assertIn("Login failed", err)
        self.assertIn("HTTP 404", err)

    def test_server_refusal_reason_is_shown(self):
        reason = (
            "Your plan's API key limit has been reached. Run veilfile login "
            "again and choose a key to replace, or revoke one in the dashboard."
        )
        http = Scripted(
            [
                device_code(),
                (400, {"error": "access_denied", "error_description": reason + "\x1b[2J"}),
            ]
        )
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertIn(reason, err)
        self.assertNotIn("\x1b", err)
        self.assertNotIn("in the browser", err)

    def test_plain_denial_still_says_browser(self):
        http = Scripted([device_code(), (400, {"error": "access_denied"})])
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertIn("denied in the browser", err)

    def test_expiry_after_errors_names_the_last_error(self):
        http = Scripted([device_code(expires_in=20)] + [(502, b"<html>")] * 10)
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        self.assertIn("Last error: HTTP 502, not a JSON response", err)

    def test_device_code_429_reports_retry_after(self):
        http = Scripted(
            [(429, {"error": "Too many.", "code": "rate_limited"}, {"Retry-After": "60"})]
        )
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertIn("Too many login attempts", err)
        self.assertIn("60 seconds", err)

    def test_non_ascii_retry_after_does_not_stop_polling(self):
        # http.client decodes headers as latin-1; '²' passes str.isdigit()
        # but not int().
        http = Scripted(
            [
                device_code(),
                (429, {"error": "Throttled.", "code": "rate_limited"}, {"Retry-After": "\xb2"}),
                (503, b"down", {"Retry-After": "٣"}),
                token(),
                usage(),
            ]
        )
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.stored_key(), NEW_KEY)
        self.assertLessEqual(max(self.clock.sleeps), 60)

    def test_device_code_429_with_non_ascii_retry_after(self):
        http = Scripted([(429, {"error": "Too many."}, {"Retry-After": "\xb2"})])
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertIn("Wait a while and retry", err)

    def test_hostile_uri_and_code_are_printed_without_escapes(self):
        http = Scripted(
            [
                device_code(
                    verification_uri_complete="https://veilfile.com/device\x1b]0;pwned\x07",
                    user_code="AB\x1b[31mCD",
                ),
                token(),
                usage(),
            ]
        )
        code, out, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        self.assertNotIn("\x1b", out)
        self.assertIn("Code: AB[31mCD", out)

    def test_line_separators_and_surrogates_in_server_text_are_dropped(self):
        # str.splitlines() (how agents read CLI output) breaks on U+2028 and
        # U+2029; a lone surrogate cannot be encoded for a UTF-8 terminal.
        http = Scripted(
            [
                device_code(user_code="AB CD\ud800"),
                token(),
                (200, {"plan": "free", "email": "lead@example.com rm -rf ~"}),
            ]
        )
        code, out, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertIn("  Code: ABCD", lines)
        self.assertIn("Logged in as lead@example.comrm -rf ~.", lines)
        for char in (" ", " ", "\ud800"):
            self.assertNotIn(char, out)

    def test_huge_expiry_is_clamped(self):
        http = Scripted([device_code(expires_in=10**9)] + [pending()] * 1000)
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        self.assertLessEqual(sum(self.clock.sleeps), 3600)


# ---------------------------------------------------------------------------
# login: browser open (G5)
# ---------------------------------------------------------------------------


class BrowserOpenTests(FixTestCase):
    def setUp(self):
        super().setUp()
        self.use_fake_clock()

    def login(self, uri, base=None):
        http = Scripted(
            [device_code(verification_uri_complete=uri), token(), usage()]
        )
        argv = (["--base-url", base] if base else []) + ["login"]
        return self.run_cli(argv, http)

    def test_same_origin_https_is_opened(self):
        code, _, _ = self.login("https://veilfile.com/device?user_code=ABCD-EFGH")
        self.assertEqual(code, 0)
        self.browser.assert_called_once_with(
            "https://veilfile.com/device?user_code=ABCD-EFGH"
        )

    def test_foreign_or_unsafe_urls_are_printed_not_opened(self):
        cases = [
            ("https://evil.example/device?user_code=X", None),
            ("https://veilfile.com.evil.example/device", None),
            ("http://veilfile.com/device", None),
            ("https://veilfile.com:8443/device", None),
            ("file:///etc/passwd", None),
            ("javascript:alert(1)", None),
            ("http://dev.example.test/device", "http://dev.example.test"),
            # urllib sees host veilfile.com; browsers read '\' as '/' and
            # open evil.example.
            ("https://evil.example\\@veilfile.com/device", None),
            ("https://evil.example\\\\@veilfile.com/device", None),
            ("https://user@veilfile.com/device", None),
            ("https://evil.example:x@veilfile.com/device", None),
            ("https://veilfile.com\\device", None),
            ("https://veilfile.com /device", None),
            # Port 0 is not the default port.
            ("https://veilfile.com:0/device", None),
        ]
        for uri, base in cases:
            with self.subTest(uri=uri):
                self.browser.reset_mock()
                code, out, _ = self.login(uri, base)
                self.assertEqual(code, 0)
                self.browser.assert_not_called()
                self.assertIn(uri, out)

    def test_urls_browsers_would_reparse_are_not_opened(self):
        for uri in (
            "https://veilfile.com\t/device",
            "https://evil.example\t\\@veilfile.com/device",
            "https://veil\nfile.com/device",
            "\x01https://veilfile.com/device",
        ):
            with self.subTest(uri=uri):
                self.assertFalse(
                    veilfile._browser_url_allowed(uri, "https://veilfile.com")
                )
        self.assertTrue(
            veilfile._browser_url_allowed(
                "https://veilfile.com/device?user_code=AB%5CCD", "https://veilfile.com"
            )
        )

    def test_unparseable_url_is_printed_not_a_crash(self):
        for uri in ("https://[veilfile.com/device", "https://veilfile.com／@x/device"):
            with self.subTest(uri=uri):
                self.browser.reset_mock()
                code, out, err = self.login(uri)
                self.assertEqual(code, 0, err)
                self.assertEqual(self.stored_key(), NEW_KEY)
                self.browser.assert_not_called()
                self.assertIn("Not opened automatically", out)

    def test_loopback_http_dev_server_is_opened(self):
        code, _, _ = self.login(
            "http://127.0.0.1:8000/device?user_code=X", "http://127.0.0.1:8000"
        )
        self.assertEqual(code, 0)
        self.browser.assert_called_once()
        self.browser.reset_mock()
        code, _, _ = self.login(
            "http://localhost:8000/device?user_code=X", "http://localhost:8000/"
        )
        self.assertEqual(code, 0)
        self.browser.assert_called_once()


# ---------------------------------------------------------------------------
# login: revoke the previously stored key (owner decision 3)
# ---------------------------------------------------------------------------


class ReloginRevocationTests(FixTestCase):
    def setUp(self):
        super().setUp()
        self.use_fake_clock()

    def test_relogin_revokes_previous_stored_key_with_that_key(self):
        veilfile.write_stored_key(OLD_KEY)
        http = Scripted(
            [
                device_code(),
                token(NEW_KEY),
                usage(),
                key_list(NEW_KEY, OLD_KEY),  # NEW is id 3, OLD is id 4
                (204, b""),
            ]
        )
        code, out, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.stored_key(), NEW_KEY)
        deletes = http.calls("DELETE", "/api/v1/keys/4")
        self.assertEqual(len(deletes), 1)
        self.assertEqual(deletes[0][2].get("Authorization"), f"Bearer {OLD_KEY}")
        self.assertEqual(http.calls("DELETE", "/api/v1/keys/3"), [])
        listing = http.calls("GET", "/api/v1/keys")
        self.assertEqual(listing[0][2].get("Authorization"), f"Bearer {OLD_KEY}")
        self.assertIn("previous", out.lower())
        self.assertNotIn(OLD_KEY, out + err)
        self.assertNotIn(NEW_KEY, out + err)

    def test_previous_key_already_invalid_is_quiet_success(self):
        veilfile.write_stored_key(OLD_KEY)
        http = Scripted(
            [device_code(), token(NEW_KEY), usage(), (401, {"code": "unauthorized"})]
        )
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.stored_key(), NEW_KEY)
        self.assertEqual([r for r in http.requests if r[0] == "DELETE"], [])

    def test_revocation_failure_is_a_note_not_a_failed_login(self):
        veilfile.write_stored_key(OLD_KEY)
        http = Scripted(
            [
                device_code(),
                token(NEW_KEY),
                usage(),
                key_list(OLD_KEY),
                URLError("connection reset"),
            ]
        )
        code, out, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0)
        self.assertEqual(self.stored_key(), NEW_KEY)
        self.assertIn("dashboard", (out + err).lower())
        self.assertNotIn(OLD_KEY, out + err)

    def test_env_key_is_never_revoked(self):
        os.environ["VEILFILE_API_KEY"] = OLD_KEY
        http = Scripted([device_code(), token(NEW_KEY), usage()])
        code, out, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        self.assertEqual(http.calls("GET", "/api/v1/keys"), [])
        self.assertIn("takes precedence", out)

    def test_env_key_equal_to_revoked_stored_key_is_called_out(self):
        # `login --print-key` output copied into a shell profile or MCP
        # config: the stored key is revoked, so the env copy stops working.
        veilfile.write_stored_key(OLD_KEY)
        os.environ["VEILFILE_API_KEY"] = OLD_KEY
        http = Scripted(
            [device_code(), token(NEW_KEY), usage(), key_list(NEW_KEY, OLD_KEY), (204, b"")]
        )
        code, out, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(http.calls("DELETE", "/api/v1/keys/4")), 1)
        self.assertIn("VEILFILE_API_KEY is set in this shell to the previously stored key", err)
        self.assertIn("now revoked", err)
        self.assertNotIn("takes precedence", out)
        self.assertNotIn(OLD_KEY, out + err)

    def test_env_key_equal_to_unrevoked_stored_key_gets_the_plain_note(self):
        veilfile.write_stored_key(OLD_KEY)
        os.environ["VEILFILE_API_KEY"] = OLD_KEY
        http = Scripted(
            [device_code(), token(NEW_KEY), usage(), key_list(OLD_KEY), URLError("reset")]
        )
        code, out, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        self.assertIn("takes precedence", out)
        self.assertNotIn("now revoked", err)

    def test_env_key_different_from_stored_key(self):
        veilfile.write_stored_key(OLD_KEY)
        os.environ["VEILFILE_API_KEY"] = "vlf_somethingelse0123456"
        http = Scripted(
            [device_code(), token(NEW_KEY), usage(), key_list(OLD_KEY), (204, b"")]
        )
        code, out, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(http.calls("DELETE", "/api/v1/keys/3")), 1)
        self.assertIn("takes precedence", out)
        self.assertNotIn("now revoked", err)

    def test_failed_login_keeps_and_does_not_revoke_previous_key(self):
        veilfile.write_stored_key(OLD_KEY)
        http = Scripted([device_code(), (400, {"error": "access_denied"})])
        code, _, _ = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertEqual(self.stored_key(), OLD_KEY)
        self.assertEqual(http.calls("GET", "/api/v1/keys"), [])

    def test_credentials_record_the_issuing_server(self):
        http = Scripted([device_code(), token(NEW_KEY), usage()])
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        data = json.loads((self.config_dir / "credentials").read_text())
        self.assertEqual(data, {"api_key": NEW_KEY, "base_url": "https://veilfile.com"})

    def test_previous_key_is_never_sent_to_another_server(self):
        veilfile.write_stored_key(OLD_KEY, "https://veilfile.com")
        http = Scripted([device_code(), token(NEW_KEY), usage()])
        code, out, err = self.run_cli(
            ["--base-url", "https://evil.example", "login"], http
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(self.stored_key(), NEW_KEY)
        sent = [r for r in http.requests if r[2].get("Authorization") == f"Bearer {OLD_KEY}"]
        self.assertEqual(sent, [])
        self.assertIn("not revoked", out)
        self.assertIn("was issued by https://veilfile.com", out)
        self.assertNotIn(OLD_KEY, out + err)

    def test_legacy_file_key_is_taken_to_be_for_the_default_server(self):
        # 0.3.0 wrote {"api_key": ...} only.
        self.config_dir.mkdir(parents=True)
        (self.config_dir / "credentials").write_text(json.dumps({"api_key": OLD_KEY}))
        http = Scripted([device_code(), token(NEW_KEY), usage()])
        code, out, err = self.run_cli(
            ["--base-url", "http://127.0.0.1:8000", "login"], http
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(http.calls("GET", "/api/v1/keys"), [])
        self.assertEqual(veilfile.read_stored_key(), (NEW_KEY, "file"))
        # Its issuer is assumed, not known: say so rather than assert it.
        self.assertIn("older veilfile version", out)
        self.assertIn("assumed https://veilfile.com", out)
        self.assertNotIn("was issued by", out)
        self.assertIn("not revoked", out)

    def test_legacy_file_key_is_revoked_on_the_default_server(self):
        self.config_dir.mkdir(parents=True)
        (self.config_dir / "credentials").write_text(json.dumps({"api_key": OLD_KEY}))
        http = Scripted(
            [device_code(), token(NEW_KEY), usage(), key_list(NEW_KEY, OLD_KEY), (204, b"")]
        )
        code, out, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(http.calls("DELETE", "/api/v1/keys/4")), 1)
        self.assertIn("Revoked the previously stored key.", out)

    def test_unstorable_key_is_revoked_and_previous_file_kept(self):
        veilfile.write_stored_key(OLD_KEY)
        http = Scripted(
            [device_code(), token(NEW_KEY), key_list(NEW_KEY, OLD_KEY), (204, b"")]
        )
        with mock.patch.object(
            veilfile, "write_stored_key", side_effect=OSError(28, "No space left on device")
        ):
            code, out, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertIn("could not be stored", err)
        self.assertIn("revoked again", err)
        deletes = http.calls("DELETE", "/api/v1/keys/3")
        self.assertEqual(len(deletes), 1)
        self.assertEqual(deletes[0][2].get("Authorization"), f"Bearer {NEW_KEY}")
        self.assertEqual(self.stored_key(), OLD_KEY)
        self.assertNotIn(NEW_KEY, out + err)
        # At the key cap the user may have replaced this CLI's own key on
        # the approval page: the server already revoked it, so the CLI must
        # not claim the previous login still works.
        self.assertNotIn("unchanged", err)
        self.assertIn("If you chose a key to replace on the approval page, it is already revoked", err)
        self.assertIn("run `veilfile login` again", err)

    def test_unstorable_unrevokable_key_points_to_the_dashboard(self):
        http = Scripted([device_code(), token(NEW_KEY), URLError("connection reset")])
        with mock.patch.object(veilfile, "write_stored_key", side_effect=OSError("read-only")):
            code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertIn(f"The new key ({NEW_KEY[:12]}…) could not be revoked", err)
        self.assertIn("already revoked", err)
        self.assertNotIn(NEW_KEY, err)

    def test_unstorable_key_with_print_key_is_shown_not_revoked(self):
        http = Scripted([device_code(), token(NEW_KEY)])
        with mock.patch.object(veilfile, "write_stored_key", side_effect=OSError("read-only")):
            code, out, err = self.run_cli(["login", "--print-key"], http)
        self.assertEqual(code, 1)
        self.assertIn(NEW_KEY, out)
        self.assertIn("not saved", err)
        self.assertEqual([r for r in http.requests if r[0] in ("GET", "DELETE")], [])

    def test_same_key_returned_again_is_not_revoked(self):
        veilfile.write_stored_key(NEW_KEY)
        http = Scripted([device_code(), token(NEW_KEY), usage()])
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.stored_key(), NEW_KEY)
        self.assertEqual(http.calls("GET", "/api/v1/keys"), [])


# ---------------------------------------------------------------------------
# logout (pycli-3)
# ---------------------------------------------------------------------------


class LogoutTests(FixTestCase):
    def test_delete_network_failure_is_a_clean_failure(self):
        veilfile.write_stored_key(OLD_KEY)
        http = Scripted([key_list(OLD_KEY), URLError("connection reset")])
        code, _, err = self.run_cli(["logout"], http)
        self.assertEqual(code, 1)
        self.assertIn("Could not reach", err)
        self.assertIn("left in place", err)
        self.assertEqual(self.stored_key(), OLD_KEY)


# ---------------------------------------------------------------------------
# credentials file (pycli-6)
# ---------------------------------------------------------------------------


@unittest.skipIf(os.name == "nt", "POSIX permission bits")
class CredentialWriteTests(FixTestCase):
    def _seed_loose_file(self):
        self.config_dir.mkdir(parents=True)
        os.chmod(self.config_dir, 0o755)
        path = self.config_dir / "credentials"
        path.write_text(json.dumps({"api_key": OLD_KEY}), encoding="utf-8")
        os.chmod(path, 0o644)
        return path

    def test_key_is_never_written_into_a_loose_file(self):
        path = self._seed_loose_file()
        modes = []
        real_dump = json.dump

        def spy(obj, handle, *args, **kwargs):
            modes.append(stat.S_IMODE(os.fstat(handle.fileno()).st_mode))
            # The live credentials file is untouched until the swap.
            self.assertEqual(json.loads(path.read_text())["api_key"], OLD_KEY)
            return real_dump(obj, handle, *args, **kwargs)

        with mock.patch.object(veilfile.json, "dump", spy):
            veilfile.write_stored_key(NEW_KEY)
        self.assertEqual(modes, [0o600])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(self.stored_key(), NEW_KEY)

    def test_failed_write_keeps_previous_credentials(self):
        self._seed_loose_file()

        def failing_dump(obj, handle, *args, **kwargs):
            raise OSError(28, "No space left on device")

        with mock.patch.object(veilfile.json, "dump", failing_dump):
            with self.assertRaises(OSError):
                veilfile.write_stored_key(NEW_KEY)
        self.assertEqual(self.stored_key(), OLD_KEY)
        self.assertEqual(sorted(p.name for p in self.config_dir.iterdir()), ["credentials"])

    def test_existing_config_dir_is_tightened(self):
        self._seed_loose_file()
        veilfile.write_stored_key(NEW_KEY)
        self.assertEqual(stat.S_IMODE(self.config_dir.stat().st_mode), 0o700)


# ---------------------------------------------------------------------------
# push: 429 guidance (ctr-16), multipart fields
# ---------------------------------------------------------------------------


def _parse_multipart(content_type, body):
    parser = email.parser.BytesParser(policy=email.policy.HTTP)
    message = parser.parsebytes(
        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8") + body
    )
    fields, files = {}, {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        if filename is None:
            fields[name] = payload.decode("utf-8")
        else:
            files[name] = (filename, payload, part.get_content_type())
    return fields, files


class PushTests(FixTestCase):
    def setUp(self):
        super().setUp()
        veilfile.write_stored_key(OLD_KEY)
        self.upload = self.root / "notes.txt"
        self.upload.write_bytes(b"hello")

    def test_monthly_cap_points_to_upgrade_not_retry(self):
        http = Scripted(
            [
                (
                    429,
                    {
                        "error": "Monthly upload cap for your plan has been reached.",
                        "code": "monthly_cap_reached",
                    },
                )
            ]
        )
        code, _, err = self.run_cli(["push", str(self.upload)], http)
        self.assertEqual(code, 1)
        self.assertIn("Monthly upload cap", err)
        self.assertIn("https://veilfile.com/plans/", err)
        self.assertNotIn("Wait a while and retry", err)

    def test_monthly_cap_sent_as_402_is_not_called_storage_cap(self):
        http = Scripted([(402, {"error": "cap", "code": "monthly_cap_reached"})])
        code, _, err = self.run_cli(["push", str(self.upload)], http)
        self.assertEqual(code, 1)
        self.assertIn("Monthly upload cap", err)
        self.assertNotIn("Storage cap", err)

    def test_throttle_reports_retry_after(self):
        http = Scripted(
            [(429, {"error": "Throttled.", "code": "rate_limited"}, {"Retry-After": "120"})]
        )
        code, _, err = self.run_cli(["push", str(self.upload)], http)
        self.assertEqual(code, 1)
        self.assertIn("Rate limited", err)
        self.assertIn("120 seconds", err)

    def test_non_ascii_retry_after_is_ignored_not_a_crash(self):
        http = Scripted([(429, {"error": "Throttled.", "code": "rate_limited"}, {"Retry-After": "\xb2"})])
        code, _, err = self.run_cli(["push", str(self.upload)], http)
        self.assertEqual(code, 1)
        self.assertIn("Throttled.", err)
        self.assertNotIn("Retry in", err)

    def test_unlabelled_429_passes_on_the_server_message(self):
        http = Scripted([(429, {"error": "Monthly upload cap for your plan has been reached."})])
        code, _, err = self.run_cli(["push", str(self.upload)], http)
        self.assertEqual(code, 1)
        self.assertIn("Monthly upload cap for your plan has been reached.", err)
        self.assertNotIn("Wait a while and retry", err)

    def test_ttl_days_is_sent_as_a_form_field_value(self):
        http = Scripted([(201, {"url": "https://veilfile.com/a/x", "secret_flags": []})])
        code, _, _ = self.run_cli(["push", str(self.upload), "--ttl-days", "30"], http)
        self.assertEqual(code, 0)
        _, _, headers, body = http.requests[0]
        fields, files = _parse_multipart(headers["Content-type"], body)
        self.assertEqual(fields, {"ttl_days": "30"})
        self.assertEqual(files["file"][:2], ("notes.txt", b"hello"))

    def test_percent_in_local_name_is_uploaded_literally(self):
        upload = self.root / "100%25 done.txt"
        upload.write_bytes(b"x")
        http = Scripted([(201, {"url": "https://veilfile.com/a/x", "secret_flags": []})])
        code, _, _ = self.run_cli(["push", str(upload)], http)
        self.assertEqual(code, 0)
        _, _, headers, body = http.requests[0]
        _, files = _parse_multipart(headers["Content-type"], body)
        self.assertEqual(files["file"][0], "100%25 done.txt")


# ---------------------------------------------------------------------------
# A real local server: push/pull round-trips, hostile names, redirects
# ---------------------------------------------------------------------------

_INLINE_PREFIXES = ("image/", "text/")  # backend views.INLINE_PREFIXES
_INLINE_TYPES = {"application/pdf"}  # backend views.INLINE_TYPES


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body=b"", headers=None):
        headers = dict(headers or {})
        # A route may declare its own (wrong) Content-Length.
        headers.setdefault("Content-Length", str(len(body)))
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        state = self.server.state
        state["hits"].append((self.headers.get("Host"), self.path, self.headers.get("Authorization")))
        route = state["routes"].get(self.path)
        if route is not None:
            self._send(*route)
            return
        artifact = state["artifacts"].get(self.path)
        if artifact is None:
            self._send(404, b'{"error": "Not found."}')
            return
        filename, data, mime = artifact
        # Mirror backend serve_artifact: inline quote()s the name, attachment
        # uses Django's raw quoted form (or filename* for non-ASCII).
        if mime.startswith(_INLINE_PREFIXES) or mime in _INLINE_TYPES:
            disposition = f'inline; filename="{quote(filename)}"'
        elif filename.isascii():
            escaped = filename.replace("\\", "\\\\").replace('"', '\\"')
            disposition = f'attachment; filename="{escaped}"'
        else:
            disposition = f"attachment; filename*=utf-8''{quote(filename)}"
        self._send(200, data, {"Content-Type": mime, "Content-Disposition": disposition})

    def do_POST(self):
        state = self.server.state
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        route = state["routes"].get(self.path)
        if route is not None:
            self._send(*route)
            return
        if self.path != "/api/v1/artifacts":
            self._send(404, b"{}")
            return
        fields, files = _parse_multipart(self.headers.get("Content-Type"), body)
        filename, data, mime = files["file"]
        token_path = f"/a/tok{len(state['artifacts'])}"
        state["artifacts"][token_path] = (filename, data, mime)
        state["uploads"].append((fields, filename, self.headers.get("Authorization")))
        url = f"http://127.0.0.1:{self.server.server_port}{token_path}"
        self._send(
            201,
            json.dumps({"url": url, "secret_flags": []}).encode("utf-8"),
            {"Content-Type": "application/json"},
        )


class LiveServerTestCase(FixTestCase):
    def setUp(self):
        super().setUp()
        self.server = self.start_server()
        self.port = self.server.server_port
        self.base = f"http://127.0.0.1:{self.port}"
        # Pulls land in root/a/b/dest so a traversal has somewhere to go.
        self.dest = self.root / "a" / "b" / "dest"
        self.dest.mkdir(parents=True)

    def start_server(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        server.state = {"routes": {}, "artifacts": {}, "uploads": [], "hits": []}
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def route(self, path, status, body=b"", headers=None):
        self.server.state["routes"][path] = (status, body, headers or {})

    def pull(self, path, *extra):
        return self.run_cli(
            ["--base-url", self.base, "pull", self.base + path, "--out", str(self.dest)]
            + list(extra)
        )

    def files_outside_dest(self):
        found = []
        for dirpath, _dirs, names in os.walk(self.root):
            for name in names:
                path = Path(dirpath) / name
                if self.config_dir in path.parents or self.dest in path.parents:
                    continue
                found.append(path)
        return found


class RoundTripTests(LiveServerTestCase):
    def test_push_then_pull_round_trips_real_bytes(self):
        veilfile.write_stored_key(OLD_KEY)
        payloads = {
            "report.json": (os.urandom(70000), "application/json"),
            "notes.md": (b"# notes\n" * 100, "text/markdown"),
            "my report (final).txt": (b"spaces and parens", "text/plain"),
            "résumé.pdf": (b"%PDF-1.4 fake", "application/pdf"),
            "données.json": (b'{"k": 1}', "application/json"),
            "100% done.csv": (b"a,b\n", "text/csv"),
        }
        src = self.root / "src"
        src.mkdir()
        for name, (data, _mime) in payloads.items():
            (src / name).write_bytes(data)
            code, out, err = self.run_cli(
                ["--base-url", self.base, "push", str(src / name), "--ttl-days", "3"]
            )
            self.assertEqual(code, 0, err)
            url = out.strip().splitlines()[0]
            code, out, err = self.run_cli(
                ["--base-url", self.base, "pull", url, "--out", str(self.dest)]
            )
            self.assertEqual(code, 0, err)
            saved = self.dest / name
            self.assertEqual(saved.read_bytes(), data, name)
            self.assertIn(str(saved), out)
        for fields, _name, auth in self.server.state["uploads"]:
            self.assertEqual(fields, {"ttl_days": "3"})
            self.assertEqual(auth, f"Bearer {OLD_KEY}")
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), sorted(payloads))


class HostileFilenameTests(LiveServerTestCase):
    CASES = [
        # (Content-Disposition, expected saved name)
        ('attachment; filename="x%2F..%2F..%2Fescaped.json"', "escaped.json"),
        ('attachment; filename="..%2F..%2Fescaped.json"', "escaped.json"),
        ('attachment; filename="%2E%2E%2F%2E%2E%2Fescaped.json"', "escaped.json"),
        ('attachment; filename="..%5C..%5Cescaped.json"', "escaped.json"),
        ('attachment; filename="..\\\\..\\\\escaped.json"', "escaped.json"),
        ('attachment; filename="../../escaped.json"', "escaped.json"),
        ('attachment; filename="/etc/escaped.json"', "escaped.json"),
        ('attachment; filename="%2Fetc%2Fescaped.json"', "escaped.json"),
        ('attachment; filename=".claude%2Fsettings.json"', "settings.json"),
        ('attachment; filename="C:%5CWindows%5Cescaped.json"', "escaped.json"),
        ('attachment; filename="C:escaped.json"', "escaped.json"),
        ("attachment; filename*=UTF-8''..%2F..%2Fescaped.json", "escaped.json"),
        ("attachment; filename*=UTF-8''%2E%2E%252F%2E%2E%252Fescaped.json", "%2F..%2Fescaped.json"),
        ('inline; filename="..%252F..%252Fescaped.json"', "%2F..%2Fescaped.json"),
        ("attachment; filename*=UTF-8''a%1B%5B31mb%07.txt", "a[31mb.txt"),
        # Line/paragraph separators: str.splitlines() would split the
        # printed path into a second, attacker-chosen line.
        ("attachment; filename*=UTF-8''report%E2%80%A8rm%20-rf%20~.json", "reportrm -rf ~.json"),
        ('inline; filename="a%E2%80%A9b.txt"', "ab.txt"),
        ('attachment; filename=".bashrc"', "bashrc"),
        ('attachment; filename="..%2F.."', "tok"),
        ('attachment; filename="%2E%2E"', "tok"),
        ('attachment; filename="."', "tok"),
        ('attachment; filename=" . "', "tok"),
        # Malformed RFC 2231 (the email parser raises): ignore the header.
        ("attachment; filename*0*=UTF-8''a; filename*=b.json", "tok"),
        ("attachment; filename*=%00''x.json", "tok"),
        ("attachment; filename*=idna''x.json", "tok"),
        ('attachment; filename="good.json"; filename*=%00\'\'x.json', "tok"),
    ]

    def test_hostile_names_stay_inside_destination(self):
        for disposition, expected in self.CASES:
            with self.subTest(disposition=disposition):
                shutil.rmtree(self.root / "a")
                self.dest.mkdir(parents=True)
                self.route(
                    "/a/tok",
                    200,
                    b'{"pwned": true}',
                    {"Content-Type": "application/json", "Content-Disposition": disposition},
                )
                code, out, err = self.pull("/a/tok")
                self.assertEqual(code, 0, err)
                saved = [p.name for p in self.dest.iterdir()]
                self.assertEqual(saved, [expected])
                self.assertEqual(out.splitlines(), [str(self.dest / expected)])
                self.assertEqual(self.files_outside_dest(), [])

    def test_hostile_names_in_cwd_mode(self):
        self.route(
            "/a/tok",
            200,
            b"x",
            {"Content-Disposition": 'attachment; filename="..%2F..%2Fescaped.json"'},
        )
        with mock.patch.object(veilfile.Path, "cwd", return_value=self.dest):
            code, out, err = self.run_cli(
                ["--base-url", self.base, "pull", self.base + "/a/tok"]
            )
        self.assertEqual(code, 0, err)
        self.assertTrue((self.dest / "escaped.json").is_file())
        self.assertEqual(self.files_outside_dest(), [])

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlink_escaping_destination_is_refused_even_with_force(self):
        outside = self.root / "outside.txt"
        outside.write_text("KEEP")
        os.symlink(outside, self.dest / "report.json")
        self.route(
            "/a/tok", 200, b"evil", {"Content-Disposition": 'attachment; filename="report.json"'}
        )
        code, _, err = self.pull("/a/tok", "--force")
        self.assertEqual(code, 1)
        self.assertEqual(outside.read_text(), "KEEP")
        self.assertTrue((self.dest / "report.json").is_symlink())

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_predictable_part_file_symlink_is_not_followed(self):
        outside = self.root / "outside.txt"
        outside.write_text("KEEP")
        os.symlink(outside, self.dest / "report.json.part")
        self.route(
            "/a/tok", 200, b"data", {"Content-Disposition": 'attachment; filename="report.json"'}
        )
        code, _, err = self.pull("/a/tok")
        self.assertEqual(code, 0, err)
        self.assertEqual(outside.read_text(), "KEEP")
        self.assertEqual((self.dest / "report.json").read_bytes(), b"data")


class WindowsNameTests(unittest.TestCase):
    """_clean_component under Windows rules (checked via sys.platform)."""

    def clean(self, name):
        with mock.patch.object(veilfile.sys, "platform", "win32"):
            return veilfile._clean_component(name)

    def test_stream_syntax_and_reserved_characters_are_replaced(self):
        self.assertEqual(self.clean("notes.md::$DATA"), "notes.md__$DATA")
        self.assertEqual(self.clean("a<b>c|d?e*f.txt"), "a_b_c_d_e_f.txt")
        self.assertEqual(self.clean("C:notes.md:evil"), "notes.md_evil")

    def test_device_names_are_defused(self):
        for name in ("CON", "nul.txt", "Com1.json", "LPT9", "aux .md", "CONOUT$"):
            with self.subTest(name=name):
                self.assertTrue(self.clean(name).startswith("_"), self.clean(name))
        self.assertEqual(self.clean("console.txt"), "console.txt")
        self.assertEqual(self.clean("COM10.txt"), "COM10.txt")

    def test_posix_keeps_colons(self):
        with mock.patch.object(veilfile.sys, "platform", "linux"):
            self.assertEqual(veilfile._clean_component("ab:c.txt"), "ab:c.txt")
            self.assertEqual(veilfile._clean_component("CON"), "CON")

    def test_bidi_override_is_stripped(self):
        self.assertEqual(veilfile._clean_component("photo\u202egnp.exe"), "photognp.exe")


class OverwriteTests(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.route(
            "/a/tok",
            200,
            b"# attacker readme",
            {"Content-Type": "text/markdown", "Content-Disposition": 'inline; filename="CLAUDE.md"'},
        )
        self.mine = self.dest / "CLAUDE.md"
        self.mine.write_text("MY OWN PROJECT INSTRUCTIONS")

    def test_existing_file_is_not_overwritten(self):
        code, _, err = self.pull("/a/tok")
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        self.assertIn("--force", err)
        self.assertEqual(self.mine.read_text(), "MY OWN PROJECT INSTRUCTIONS")
        self.assertEqual([p.name for p in self.dest.iterdir()], ["CLAUDE.md"])

    def test_force_overwrites(self):
        code, out, err = self.pull("/a/tok", "--force")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.mine.read_text(), "# attacker readme")
        self.assertEqual([p.name for p in self.dest.iterdir()], ["CLAUDE.md"])

    def test_explicit_out_file_is_not_overwritten_without_force(self):
        target = self.root / "explicit.md"
        target.write_text("KEEP")
        code, _, err = self.run_cli(
            ["--base-url", self.base, "pull", self.base + "/a/tok", "--out", str(target)]
        )
        self.assertEqual(code, 1)
        self.assertEqual(target.read_text(), "KEEP")
        code, _, err = self.run_cli(
            ["--base-url", self.base, "pull", self.base + "/a/tok", "--out", str(target), "--force"]
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(target.read_text(), "# attacker readme")

    def test_truncated_download_leaves_no_files(self):
        self.mine.unlink()
        self.route(
            "/a/short",
            200,
            b"short",
            {"Content-Length": "99", "Content-Disposition": 'attachment; filename="CLAUDE.md"'},
        )
        code, _, err = self.pull("/a/short")
        self.assertEqual(code, 1)
        self.assertIn("incomplete", err.lower())
        self.assertEqual(list(self.dest.iterdir()), [])


class PartFileTests(LiveServerTestCase):
    """The download goes to a fresh temp file next to the destination."""

    def test_name_at_the_filesystem_limit_still_downloads(self):
        # 255 characters is a legal file name (the server allows 512), but
        # "<name>.<hex>.part" is not: the temp file falls back to a short name.
        name = "n" * 250 + ".json"
        self.route(
            "/a/long", 200, b"long", {"Content-Disposition": f'attachment; filename="{name}"'}
        )
        code, out, err = self.pull("/a/long")
        self.assertEqual(code, 0, err)
        self.assertEqual([p.name for p in self.dest.iterdir()], [name])
        self.assertEqual((self.dest / name).read_bytes(), b"long")
        self.assertEqual(out.splitlines(), [str(self.dest / name)])

    def test_lower_filesystem_name_limit_gets_a_short_part_file(self):
        real_open = os.open
        opened = []

        def open_with_low_limit(path, flags, mode=0o777):
            opened.append(os.path.basename(path))
            if len(opened) == 1:
                raise OSError(errno.ENAMETOOLONG, "File name too long", path)
            return real_open(path, flags, mode)

        with mock.patch.object(veilfile.os, "open", open_with_low_limit):
            fd, tmp_path = veilfile._open_new_part_file(self.dest / "report.json")
        os.close(fd)
        self.assertRegex(opened[0], r"^report\.json\.[0-9a-f]{8}\.part$")
        self.assertRegex(opened[1], r"^\.veilfile-[0-9a-f]{8}\.part$")
        self.assertEqual(tmp_path, self.dest / opened[1])

    def test_other_part_file_errors_are_not_retried(self):
        with mock.patch.object(
            veilfile.os, "open", side_effect=PermissionError(errno.EACCES, "denied")
        ) as fake_open:
            with self.assertRaises(PermissionError):
                veilfile._open_new_part_file(self.dest / "report.json")
        self.assertEqual(fake_open.call_count, 1)


class NonAsciiHeaderTests(LiveServerTestCase):
    """Headers arrive latin-1 decoded: '\\xb2' is '²', which isdigit()
    accepts and int() rejects."""

    def test_push_429_with_superscript_retry_after(self):
        veilfile.write_stored_key(OLD_KEY)
        self.route(
            "/api/v1/artifacts",
            429,
            b'{"error": "Throttled.", "code": "rate_limited"}',
            {"Content-Type": "application/json", "Retry-After": "\xb2"},
        )
        upload = self.root / "notes.txt"
        upload.write_bytes(b"hello")
        code, _, err = self.run_cli(["--base-url", self.base, "push", str(upload)])
        self.assertEqual(code, 1)
        self.assertIn("Throttled.", err)

    def test_pull_with_superscript_content_length(self):
        self.route(
            "/a/tok",
            200,
            b"payload",
            {"Content-Length": "\xb2", "Content-Disposition": 'attachment; filename="x.json"'},
        )
        code, _, err = self.pull("/a/tok")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.dest / "x.json").read_bytes(), b"payload")


class RedirectTests(LiveServerTestCase):
    def test_redirect_to_another_host_is_refused(self):
        self.route("/a/tok", 302, b"", {"Location": f"http://localhost:{self.port}/foreign"})
        self.route(
            "/foreign",
            200,
            b"FOREIGN",
            {"Content-Disposition": 'attachment; filename="from-foreign.json"'},
        )
        code, _, err = self.pull("/a/tok")
        self.assertEqual(code, 1)
        self.assertIn("redirect", err.lower())
        self.assertEqual(list(self.dest.iterdir()), [])
        self.assertEqual([h for h in self.server.state["hits"] if h[1] == "/foreign"], [])

    def test_same_host_redirect_is_followed(self):
        self.route("/a/old", 301, b"", {"Location": "/a/tok"})
        self.route(
            "/a/tok", 200, b"moved", {"Content-Disposition": 'attachment; filename="moved.txt"'}
        )
        code, _, err = self.pull("/a/old")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.dest / "moved.txt").read_bytes(), b"moved")

    def test_api_key_is_not_replayed_to_a_redirected_host(self):
        veilfile.write_stored_key(OLD_KEY)
        self.route("/api/v1/usage", 302, b"", {"Location": f"http://localhost:{self.port}/steal"})
        self.route("/steal", 200, b"{}", {"Content-Type": "application/json"})
        code, _, err = self.run_cli(["--base-url", self.base, "status"])
        self.assertEqual(code, 1)
        self.assertEqual([h for h in self.server.state["hits"] if h[1] == "/steal"], [])

    def test_unparseable_redirect_is_a_clean_error(self):
        # urllib raises ValueError while parsing such a Location.
        veilfile.write_stored_key(OLD_KEY)
        self.route("/a/tok", 302, b"", {"Location": "http://[evil.example/x"})
        self.route("/api/v1/usage", 302, b"", {"Location": "http://[evil.example/x"})
        code, _, err = self.pull("/a/tok")
        self.assertEqual(code, 1)
        self.assertIn("Could not reach", err)
        self.assertEqual(list(self.dest.iterdir()), [])
        code, _, err = self.run_cli(["--base-url", self.base, "status"])
        self.assertEqual(code, 1)
        self.assertIn("Could not reach", err)

    def test_https_to_http_downgrade_is_refused(self):
        handler = veilfile._SameHostRedirectHandler()
        request = Request("https://veilfile.com/a/tok")
        with self.assertRaises(URLError):
            handler.redirect_request(
                request, io.BytesIO(b""), 302, "Found", _message({}), "http://veilfile.com/a/tok"
            )
        allowed = handler.redirect_request(
            Request("http://veilfile.com/a/tok"),
            io.BytesIO(b""),
            302,
            "Found",
            _message({}),
            "https://veilfile.com/a/tok",
        )
        self.assertEqual(allowed.full_url, "https://veilfile.com/a/tok")

    def follows(self, old_url, new_url):
        try:
            veilfile._SameHostRedirectHandler().redirect_request(
                Request(old_url), io.BytesIO(b""), 302, "Found", _message({}), new_url
            )
        except URLError:
            return False
        return True

    def test_only_same_origin_or_default_port_upgrade_is_followed(self):
        cases = [
            ("http://veilfile.com/a", "https://veilfile.com/a", True),
            ("http://veilfile.com:80/a", "https://veilfile.com:443/a", True),
            ("https://veilfile.com/a", "https://veilfile.com:443/b", True),
            ("https://veilfile.com/a", "https://VEILFILE.com/b", True),
            ("http://127.0.0.1:8000/a", "http://127.0.0.1:8000/b", True),
            ("https://veilfile.com/a", "https://veilfile.com:8443/a", False),
            ("http://veilfile.com/a", "https://veilfile.com:8443/a", False),
            ("http://veilfile.com/a", "http://veilfile.com:8080/a", False),
            ("http://127.0.0.1:8000/a", "http://127.0.0.1:9000/a", False),
            ("http://127.0.0.1:8000/a", "https://127.0.0.1:8000/a", False),
            ("http://veilfile.com/a", "http://veilfile.com:0/a", False),
            ("https://veilfile.com/a", "https://veilfile.com:99999/a", False),
            ("https://veilfile.com/a", "ftp://veilfile.com/a", False),
        ]
        for old_url, new_url, expected in cases:
            with self.subTest(old=old_url, new=new_url):
                self.assertEqual(self.follows(old_url, new_url), expected)

    def test_redirect_to_another_port_on_the_same_host_is_refused(self):
        # Another local process on another port must not get the API key
        # or choose what pull saves.
        other = self.start_server()
        other_base = f"http://127.0.0.1:{other.server_port}"
        other.state["routes"]["/a/tok"] = (
            200,
            b"OTHER",
            {"Content-Disposition": 'attachment; filename="other-port.json"'},
        )
        other.state["routes"]["/api/v1/usage"] = (
            200,
            b'{"plan": "free", "email": "x@example.com"}',
            {"Content-Type": "application/json"},
        )
        self.route("/a/tok", 302, b"", {"Location": other_base + "/a/tok"})
        self.route("/api/v1/usage", 302, b"", {"Location": other_base + "/api/v1/usage"})
        veilfile.write_stored_key(OLD_KEY)
        code, _, err = self.pull("/a/tok")
        self.assertEqual(code, 1)
        self.assertIn(f"Refusing to follow a redirect to {other_base}", err)
        self.assertEqual(list(self.dest.iterdir()), [])
        code, _, err = self.run_cli(["--base-url", self.base, "status"])
        self.assertEqual(code, 1)
        self.assertIn("Could not reach", err)
        self.assertEqual(other.state["hits"], [])


# ---------------------------------------------------------------------------
# The suites never reach a real server (the default base URL is production)
# ---------------------------------------------------------------------------


class NetworkGuardTests(FixTestCase):
    """192.0.2.0/24 is reserved for documentation, and DNS is stubbed out
    here, so even a broken guard reaches nothing real."""

    def test_cli_requests_outside_loopback_fail_loudly(self):
        veilfile.write_stored_key(OLD_KEY)
        with mock.patch("socket.getaddrinfo", side_effect=AssertionError("DNS lookup")) as dns:
            with self.assertRaises(NetworkUsedInTest):
                self.run_cli(["--base-url", "https://192.0.2.1", "status"])
            with self.assertRaises(NetworkUsedInTest):
                self.run_cli(
                    ["--base-url", "http://192.0.2.1", "pull", "http://192.0.2.1/a/tok"]
                )
            with self.assertRaises(NetworkUsedInTest):
                socket.create_connection(("veilfile.com", 443), timeout=0.5)
        dns.assert_not_called()

    def test_direct_socket_connect_is_refused(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        sock.settimeout(0.5)
        with self.assertRaises(NetworkUsedInTest):
            sock.connect(("192.0.2.1", 443))


# ---------------------------------------------------------------------------
# ops-9: the existing suite must not inherit VEILFILE_BASE_URL
# ---------------------------------------------------------------------------


def _existing_suite():
    package = __package__ or ""
    return importlib.import_module(
        f"{package}.test_veilfile" if package else "test_veilfile"
    )


class InheritedEnvironmentTests(unittest.TestCase):
    def test_existing_suite_refuses_outbound_connections(self):
        # A plain TestCase, so only CliTestCase's own guard is in place.
        # With DNS stubbed out, a missing guard fails here without a
        # connection attempt.
        base = _existing_suite()
        with mock.patch("socket.getaddrinfo", side_effect=AssertionError("DNS lookup")):
            case = base.CliTestCase("test_status_no_key")
            case.setUp()
            try:
                with self.assertRaises(NetworkUsedInTest):
                    socket.create_connection(("veilfile.com", 443), timeout=0.5)
            finally:
                case.doCleanups()

    def test_existing_suite_ignores_exported_base_url(self):
        base = _existing_suite()
        with mock.patch.dict(
            os.environ,
            {"VEILFILE_BASE_URL": "http://127.0.0.1:9", "VEILFILE_API_KEY": "vlf_x"},
        ):
            case = base.CliTestCase("test_status_no_key")
            case.setUp()
            try:
                parsed = veilfile.build_parser().parse_args(["status"])
                self.assertEqual(parsed.base_url, veilfile.DEFAULT_BASE_URL)
                self.assertNotIn("VEILFILE_API_KEY", os.environ)
            finally:
                case.doCleanups()
            # The suite's own cleanup restores the caller's environment.
            self.assertEqual(os.environ.get("VEILFILE_BASE_URL"), "http://127.0.0.1:9")
            self.assertEqual(os.environ.get("VEILFILE_API_KEY"), "vlf_x")


if __name__ == "__main__":
    unittest.main()
