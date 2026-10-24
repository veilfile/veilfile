"""Tests for the veilfile CLI. Stdlib unittest only — no third-party deps."""

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import veilfile  # noqa: E402
from tests.offline import refuse_outbound_connections  # noqa: E402

TEST_KEY = "vlf_testkey0123456789abcdef"
OTHER_KEY = "vlf_otherkey9876543210fedcba"


class FakeResponse:
    """Minimal urlopen stand-in: context manager with read() and status."""

    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def getheaders(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeClock:
    """Deterministic time: sleep advances the clock, no real waiting."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ScriptedHttp:
    """urlopen replacement serving a scripted response list; records requests."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []  # (method, url, headers, body)

    def __call__(self, request, timeout=None):
        body = None
        if request.data:
            try:
                body = json.loads(request.data.decode("utf-8"))
            except ValueError:
                body = request.data
        self.requests.append(
            (
                request.get_method(),
                request.full_url,
                dict(request.header_items()),
                body,
            )
        )
        if not self._responses:
            raise AssertionError("unexpected extra HTTP request")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, payload = item
        if status >= 400:
            raise HTTPError(
                request.full_url, status, "error", {}, io.BytesIO(json.dumps(payload).encode())
            )
        return FakeResponse(status, payload)


def device_code_response(**overrides):
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


def token_success(key=TEST_KEY):
    return (200, {"api_key": key})


def usage_response(**overrides):
    payload = {
        "plan": "team",
        "uploads_used": 3,
        "uploads_cap": 2000,
        "storage_used_bytes": 12345,
        "storage_cap_bytes": 25 * 1024**3,
        "email": "lead@example.com",
        "email_verified": True,
    }
    payload.update(overrides)
    return (200, payload)


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_dir = Path(self.tmp.name) / "config"
        patches = [
            mock.patch.object(veilfile, "config_dir", return_value=self.config_dir),
            mock.patch.dict(os.environ, {}, clear=False),
            mock.patch("webbrowser.open", return_value=False),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        # Never inherit a real key or base URL from the shell. Popped inside
        # patch.dict so the caller's environment comes back afterwards.
        for name in ("VEILFILE_API_KEY", "VEILFILE_BASE_URL"):
            os.environ.pop(name, None)
        # The default base URL is production: never let a request out.
        refuse_outbound_connections(self)
        self.clock = FakeClock()
        sleep_patch = mock.patch("time.sleep", self.clock.sleep)
        mono_patch = mock.patch("time.monotonic", self.clock.monotonic)
        sleep_patch.start()
        mono_patch.start()
        self.addCleanup(sleep_patch.stop)
        self.addCleanup(mono_patch.stop)

    def run_cli(self, argv, http):
        """Run main() with scripted HTTP; return (exit_code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(veilfile, "_urlopen", http):
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    code = veilfile.main(argv)
                except SystemExit as exc:
                    code = exc.code
        return code, out.getvalue(), err.getvalue()

    def stored_key(self):
        path = self.config_dir / "credentials"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))["api_key"]

    # -- login -----------------------------------------------------------

    def test_login_success_stores_key_and_never_prints_it(self):
        http = ScriptedHttp(
            [device_code_response(), pending(), pending(), token_success(), usage_response()]
        )
        code, out, _ = self.run_cli(["login"], http)
        self.assertEqual(code, 0)
        self.assertEqual(self.stored_key(), TEST_KEY)
        self.assertNotIn(TEST_KEY, out)
        self.assertIn("lead@example.com", out)
        # Authorization header shape on the usage confirmation call.
        _, _, headers, _ = http.requests[-1]
        self.assertEqual(headers.get("Authorization"), f"Bearer {TEST_KEY}")

    def test_login_slow_down_increases_interval(self):
        http = ScriptedHttp(
            [
                device_code_response(),
                pending(),
                (400, {"error": "slow_down"}),
                token_success(),
                usage_response(),
            ]
        )
        code, _, _ = self.run_cli(["login"], http)
        self.assertEqual(code, 0)
        # Sleeps: 5 (pending), 5 (slow_down arrives), then 10 after backoff.
        self.assertEqual(self.clock.sleeps, [5, 5, 10])

    def test_login_expired_token_exits_cleanly(self):
        http = ScriptedHttp(
            [device_code_response(), pending(), (400, {"error": "expired_token"})]
        )
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        self.assertIsNone(self.stored_key())

    def test_login_access_denied_exits_cleanly(self):
        http = ScriptedHttp(
            [device_code_response(), (400, {"error": "access_denied"})]
        )
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertIn("denied", err)
        self.assertIsNone(self.stored_key())

    def test_login_stops_after_expiry_deadline(self):
        http = ScriptedHttp(
            [device_code_response(expires_in=12, interval=5)]
            + [pending()] * 10
            + [token_success()]
        )
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        # Polled at t=5, t=10, t=12; the success response (and the usage
        # confirmation) must never be fetched.
        self.assertFalse(
            any(r[1].endswith("/api/v1/usage") for r in http.requests)
        )
        self.assertLessEqual(len(http.requests), 4)

    def test_login_retries_transient_network_failure(self):
        http = ScriptedHttp(
            [
                device_code_response(),
                URLError("connection reset"),  # transient blip, not fatal
                pending(),
                token_success(),
                usage_response(),
            ]
        )
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0)
        self.assertIn("retrying", err)
        self.assertEqual(self.stored_key(), TEST_KEY)

    def test_login_retries_truncated_chunked_read(self):
        import http.client as http_client

        http = ScriptedHttp(
            [
                device_code_response(),
                http_client.IncompleteRead(b'{"error":', 30),
                pending(),
                token_success(),
                usage_response(),
            ]
        )
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 0)
        self.assertIn("retrying", err)
        self.assertEqual(self.stored_key(), TEST_KEY)

    def test_unreadable_error_body_is_a_clean_failure(self):
        import http.client as http_client

        class UnreadableHTTPError(HTTPError):
            def read(self, *args, **kwargs):
                raise http_client.IncompleteRead(b"partial", 100)

        self._write_key_file()
        http = ScriptedHttp(
            [
                UnreadableHTTPError(
                    "https://veilfile.com/api/v1/usage",
                    400,
                    "Bad Request",
                    {},
                    io.BytesIO(b""),
                )
            ]
        )
        code, _, err = self.run_cli(["status"], http)
        self.assertEqual(code, 1)
        self.assertIn("Could not read the response", err)

    def test_login_rate_limited(self):
        http = ScriptedHttp([(429, {"error": "rate_limited"})])
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertIn("Too many login attempts", err)

    def test_login_network_failure(self):
        http = ScriptedHttp([URLError("connection refused")])
        code, _, err = self.run_cli(["login"], http)
        self.assertEqual(code, 1)
        self.assertIn("Could not reach", err)

    def test_login_print_key_flag_is_explicit(self):
        http = ScriptedHttp(
            [device_code_response(), token_success(), usage_response()]
        )
        _, out, _ = self.run_cli(["login", "--print-key"], http)
        self.assertIn(TEST_KEY, out)

    def test_credentials_file_is_0600(self):
        http = ScriptedHttp(
            [device_code_response(), token_success(), usage_response()]
        )
        self.run_cli(["login"], http)
        path = self.config_dir / "credentials"
        self.assertTrue(path.is_file())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    # -- logout ----------------------------------------------------------

    def _write_key_file(self, key=TEST_KEY):
        veilfile.write_stored_key(key)

    def test_logout_revokes_matching_key_and_deletes_file(self):
        self._write_key_file()
        keys = [
            {"id": 7, "key_prefix": TEST_KEY[:12], "name": "terminal", "is_active": True},
            {"id": 9, "key_prefix": OTHER_KEY[:12], "name": "other", "is_active": True},
        ]
        http = ScriptedHttp([(200, keys), (204, {})])
        code, out, _ = self.run_cli(["logout"], http)
        self.assertEqual(code, 0)
        method, url, headers, _ = http.requests[1]
        self.assertEqual(method, "DELETE")
        self.assertTrue(url.endswith("/api/v1/keys/7"))
        self.assertEqual(headers.get("Authorization"), f"Bearer {TEST_KEY}")
        self.assertFalse((self.config_dir / "credentials").exists())
        self.assertIn("Logged out", out)

    def test_logout_when_server_already_rejected_key(self):
        self._write_key_file()
        http = ScriptedHttp([(401, {"detail": "invalid"})])
        code, out, _ = self.run_cli(["logout"], http)
        self.assertEqual(code, 0)
        self.assertIn("already invalid", out)
        self.assertFalse((self.config_dir / "credentials").exists())

    def test_logout_with_no_key_is_a_noop(self):
        http = ScriptedHttp([])
        code, out, _ = self.run_cli(["logout"], http)
        self.assertEqual(code, 0)
        self.assertIn("Not logged in", out)
        self.assertEqual(http.requests, [])

    def test_logout_unexpected_status_names_the_status(self):
        # A reached-but-rejecting server must not be reported as unreachable.
        self._write_key_file()
        http = ScriptedHttp([(500, {"error": "boom"})])
        code, _, err = self.run_cli(["logout"], http)
        self.assertEqual(code, 1)
        self.assertIn("HTTP 500", err)
        self.assertNotIn("Could not reach", err)
        self.assertTrue((self.config_dir / "credentials").exists())

    def test_logout_with_env_key_revokes_but_warns(self):
        os.environ["VEILFILE_API_KEY"] = TEST_KEY
        keys = [{"id": 7, "key_prefix": TEST_KEY[:12], "is_active": True}]
        http = ScriptedHttp([(200, keys), (204, {})])
        code, out, _ = self.run_cli(["logout"], http)
        self.assertEqual(code, 0)
        self.assertIn("VEILFILE_API_KEY", out)
        method, url, _, _ = http.requests[1]
        self.assertEqual(method, "DELETE")

    # -- status ----------------------------------------------------------

    def test_status_shows_account_without_key(self):
        self._write_key_file()
        http = ScriptedHttp([usage_response()])
        code, out, _ = self.run_cli(["status"], http)
        self.assertEqual(code, 0)
        self.assertIn("lead@example.com", out)
        self.assertIn("team", out)
        self.assertNotIn(TEST_KEY, out)

    def test_status_marks_unverified_email(self):
        self._write_key_file()
        http = ScriptedHttp([usage_response(email_verified=False)])
        code, out, _ = self.run_cli(["status"], http)
        self.assertEqual(code, 0)
        self.assertIn("not verified", out)

    def test_status_revoked_key(self):
        self._write_key_file()
        http = ScriptedHttp([(401, {"detail": "invalid"})])
        code, out, _ = self.run_cli(["status"], http)
        self.assertEqual(code, 1)
        self.assertIn("invalid or revoked", out)

    def test_status_no_key(self):
        http = ScriptedHttp([])
        code, out, _ = self.run_cli(["status"], http)
        self.assertEqual(code, 1)
        self.assertIn("Not logged in", out)

    def test_whoami_is_status_alias(self):
        self._write_key_file()
        http = ScriptedHttp([usage_response()])
        code, out, _ = self.run_cli(["whoami"], http)
        self.assertEqual(code, 0)
        self.assertIn("lead@example.com", out)

    def test_env_var_takes_precedence_over_file(self):
        self._write_key_file(OTHER_KEY)
        os.environ["VEILFILE_API_KEY"] = TEST_KEY
        http = ScriptedHttp([usage_response()])
        code, _, _ = self.run_cli(["status"], http)
        self.assertEqual(code, 0)
        _, _, headers, _ = http.requests[0]
        self.assertEqual(headers.get("Authorization"), f"Bearer {TEST_KEY}")

    # -- push ------------------------------------------------------------

    def _write_upload_file(self, name="notes.txt", content=b"hello veilfile"):
        path = Path(self.tmp.name) / name
        path.write_bytes(content)
        return path

    def test_push_success_prints_url(self):
        self._write_key_file()
        target = self._write_upload_file()
        artifact_url = "https://veilfile.com/a/abc123"
        http = ScriptedHttp(
            [(201, {"url": artifact_url, "secret_flags": [], "size_bytes": 14})]
        )
        code, out, err = self.run_cli(["push", str(target)], http)
        self.assertEqual(code, 0)
        self.assertIn(artifact_url, out)
        method, url, headers, _ = http.requests[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/api/v1/artifacts"))
        self.assertEqual(headers.get("Authorization"), f"Bearer {TEST_KEY}")
        content_type = headers.get("Content-type", "")
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))

    def test_push_warns_on_secret_flags(self):
        self._write_key_file()
        target = self._write_upload_file()
        http = ScriptedHttp(
            [
                (
                    201,
                    {
                        "url": "https://veilfile.com/a/abc123",
                        "secret_flags": ["github_token"],
                        "warning": "Ask before sharing.",
                    },
                )
            ]
        )
        code, out, err = self.run_cli(["push", str(target)], http)
        self.assertEqual(code, 0)
        self.assertIn("https://veilfile.com/a/abc123", out)
        self.assertIn("Warning", err)

    def test_push_storage_cap_is_a_clean_failure(self):
        self._write_key_file()
        target = self._write_upload_file()
        http = ScriptedHttp(
            [(402, {"error": "Storage cap reached.", "code": "storage_cap_reached"})]
        )
        code, _, err = self.run_cli(["push", str(target)], http)
        self.assertEqual(code, 1)
        self.assertIn("Storage cap", err)
        self.assertEqual(len(http.requests), 1)  # no retry

    def test_push_rejected_file_is_a_clean_failure(self):
        self._write_key_file()
        target = self._write_upload_file()
        http = ScriptedHttp(
            [(400, {"error": "Secret detected.", "code": "secret_detected"})]
        )
        code, _, err = self.run_cli(["push", str(target)], http)
        self.assertEqual(code, 1)
        self.assertIn("Upload rejected", err)

    def test_push_refuses_oversize_file_before_any_request(self):
        self._write_key_file()
        target = self._write_upload_file()
        http = ScriptedHttp([])
        with mock.patch.object(veilfile, "_MAX_UPLOAD_BYTES", 10):
            code, _, err = self.run_cli(["push", str(target)], http)
        self.assertEqual(code, 1)
        self.assertIn("upload cap", err)
        self.assertEqual(http.requests, [])

    def test_push_missing_file(self):
        self._write_key_file()
        http = ScriptedHttp([])
        code, _, err = self.run_cli(
            ["push", str(Path(self.tmp.name) / "nope.txt")], http
        )
        self.assertEqual(code, 1)
        self.assertIn("No such file", err)

    def test_push_requires_login(self):
        target = self._write_upload_file()
        http = ScriptedHttp([])
        code, out, _ = self.run_cli(["push", str(target)], http)
        self.assertEqual(code, 1)
        self.assertIn("Not logged in", out)

    def test_push_sends_ttl_days(self):
        self._write_key_file()
        target = self._write_upload_file()
        http = ScriptedHttp(
            [(201, {"url": "https://veilfile.com/a/x", "secret_flags": []})]
        )
        code, _, _ = self.run_cli(["push", str(target), "--ttl-days", "30"], http)
        self.assertEqual(code, 0)
        _, _, _, body = http.requests[0]
        self.assertIsInstance(body, bytes)
        self.assertIn(b'ttl_days', body)

    # -- pull ------------------------------------------------------------

    def _binary_http(self, payload: bytes, headers=None, status=200):
        """urlopen replacement for downloads: binary body + headers."""

        class BinaryResponse:
            def __init__(self):
                self.status = status
                self._headers = headers or {}
                self._stream = io.BytesIO(payload)

            def getheaders(self):
                return list(self._headers.items())

            def read(self, n=-1):
                return self._stream.read(n)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=None):
            if status >= 400:
                raise HTTPError(
                    request.full_url, status, "error", {}, io.BytesIO(b"")
                )
            return BinaryResponse()

        return fake_urlopen

    def test_pull_downloads_to_cwd(self):
        url = "https://veilfile.com/a/abc123"
        body = b"artifact bytes here"
        http = self._binary_http(
            body,
            {"Content-Length": str(len(body)), "Content-Type": "text/plain"},
        )
        with mock.patch.object(veilfile.Path, "cwd", return_value=Path(self.tmp.name)):
            code, out, _ = self.run_cli(["pull", url], http)
        self.assertEqual(code, 0)
        saved = Path(self.tmp.name) / "abc123"
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.read_bytes(), body)
        self.assertIn(str(saved), out)

    def test_pull_uses_content_disposition_filename(self):
        url = "https://veilfile.com/a/abc123"
        http = self._binary_http(
            b"data",
            {"Content-Disposition": 'attachment; filename="report.pdf"'},
        )
        with mock.patch.object(veilfile.Path, "cwd", return_value=Path(self.tmp.name)):
            code, _, _ = self.run_cli(["pull", url], http)
        self.assertEqual(code, 0)
        self.assertTrue((Path(self.tmp.name) / "report.pdf").is_file())

    def test_pull_out_flag(self):
        url = "https://veilfile.com/a/abc123"
        dest = Path(self.tmp.name) / "custom.bin"
        http = self._binary_http(b"0123456789")
        code, out, _ = self.run_cli(["pull", url, "--out", str(dest)], http)
        self.assertEqual(code, 0)
        self.assertEqual(dest.read_bytes(), b"0123456789")

    def test_pull_404_is_a_clean_failure(self):
        url = "https://veilfile.com/a/gone"
        http = self._binary_http(b"", status=404)
        code, _, err = self.run_cli(["pull", url], http)
        self.assertEqual(code, 1)
        self.assertIn("revoked, or expired", err)

    def test_pull_rejects_foreign_host(self):
        http = self._binary_http(b"evil")
        code, _, err = self.run_cli(["pull", "https://evil.example/x"], http)
        self.assertEqual(code, 1)
        self.assertIn("Refusing to download", err)

    def test_pull_size_mismatch_fails(self):
        url = "https://veilfile.com/a/abc123"
        http = self._binary_http(b"short", {"Content-Length": "99999"})
        with mock.patch.object(veilfile.Path, "cwd", return_value=Path(self.tmp.name)):
            code, _, err = self.run_cli(["pull", url], http)
        self.assertEqual(code, 1)
        self.assertIn("size mismatch", err)
        leftovers = list(Path(self.tmp.name).glob("*.part"))
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
