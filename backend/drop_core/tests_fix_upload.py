"""Regression tests for the upload-pipeline fixes (cluster: upload).

Covers: one-time PUT bodies above Django's 2.5 MB in-memory limit, the
single-use claim (rejected PUTs no longer burn the URL), on_secret
persisted from create_upload_url, fail-closed on_secret / ttl_days
parsing, atomic monthly/storage quota reservation, early size rejection
for multipart uploads, server-derived MIME types, filename sanitization
and Content-Disposition, the session-upload email gate, minting
credential revocation, enforce-mode containment on the PUT path, and
storage-failure handling in serve_artifact.
"""

import base64
import io
import json
import os
import re
import secrets
import shutil
import tempfile
from datetime import timedelta
from unittest import mock
from urllib.parse import unquote, urlparse

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.utils import timezone
from oauth2_provider.models import get_access_token_model, get_application_model
from rest_framework.test import APIClient

from drop_core import services
from drop_core.models import (
    APIKey,
    Artifact,
    BlockedHash,
    EmailVerification,
    PendingUpload,
    SafetyEvent,
    UsageCounter,
    get_or_create_workspace,
)
from drop_core.oauth import OAUTH_SCOPE_MCP_USE, revoke_user_oauth_grants
from drop_core.plans import PLANS
from drop_core.services import MAX_FILE_SIZE, mint_upload_url, upload_artifact
from drop_core.storage import get_storage

User = get_user_model()
AccessToken = get_access_token_model()
Application = get_application_model()

MiB = 1024 * 1024
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SECRET = b'my token = "ghp_' + b"a" * 36 + b'"'


def _month():
    return timezone.now().strftime("%Y-%m")


def _npm_cli_filename(content_disposition):
    """What npm veilfile@0.2.0's filenameFromResponse() would save as.

    Mirrors npm/veilfile.js: the first of filename*=UTF-8''... or
    filename="...", then decodeURIComponent.
    """
    m = re.search(
        r"filename\*=UTF-8''([^;]+)|filename=\"([^\"]+)\"",
        content_disposition,
        re.IGNORECASE,
    )
    return unquote(m.group(1) or m.group(2))


class UploadFixBase(TestCase):
    def setUp(self):
        cache.clear()
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-fix-upload-")
        # Restore (not unset) the runner's DROP_LOCAL_DIR afterwards, so later
        # tests never fall back to backend/media/artifacts.
        local_dir = mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": self.tmpdir})
        local_dir.start()
        self.addCleanup(local_dir.stop)
        self.user = User.objects.create_user(username="uploader", password="pw12345678")
        self.workspace = get_or_create_workspace(self.user)
        self.key, self.raw_key = APIKey.create_key(self.workspace, "agent-key")
        EmailVerification.objects.create(
            user=self.user,
            email="uploader@example.com",
            is_verified=True,
            verified_at=timezone.now(),
        )
        self.client = APIClient()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.raw_key}"}

    def tearDown(self):
        os.environ.pop("VEILFILE_SAFETY_MODE", None)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _post(self, name="note.txt", content=b"hello", content_type="text/plain", **fields):
        data = {"file": SimpleUploadedFile(name, content, content_type=content_type)}
        data.update(fields)
        return self.client.post("/api/v1/artifacts/", data, **self.auth)

    def _put(self, pending_or_path, data, content_type="application/octet-stream", **extra):
        path = (
            pending_or_path
            if isinstance(pending_or_path, str)
            else f"/api/v1/uploads/{pending_or_path.token}"
        )
        return self.client.put(path, data=data, content_type=content_type, **extra)

    def _mcp(self, tool, arguments, raw_key=None):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        response = Client().post(
            "/mcp",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw_key or self.raw_key}",
        )
        body = response.json()
        self.assertNotIn("error", body, body)
        return json.loads(body["result"]["content"][0]["text"])


# ---------------------------------------------------------------------------
# upl-1 / mcp-3 / deploy-1 / ctr-1: one-time PUT bodies above 2.5 MiB
# ---------------------------------------------------------------------------


class PendingUploadBodySizeTests(UploadFixBase):
    def test_put_3_mib_body_succeeds(self):
        pending = mint_upload_url(self.workspace, self.key, "big.log")
        body = b"0123456789abcdef\n" * (3 * MiB // 17 + 1)
        self.assertGreater(len(body), 3 * MiB)
        response = self._put(pending, body, content_type="text/plain")
        self.assertEqual(response.status_code, 201, response.content[:300])
        artifact = Artifact.objects.get(id=response.json()["id"])
        self.assertEqual(artifact.size_bytes, len(body))
        with get_storage().open(artifact.token) as fh:
            self.assertEqual(fh.read(), body)

    def test_put_exactly_max_size_succeeds(self):
        pending = mint_upload_url(self.workspace, self.key, "max.txt")
        body = b"x" * MAX_FILE_SIZE
        response = self._put(pending, body, content_type="text/plain")
        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertEqual(response.json()["size_bytes"], MAX_FILE_SIZE)

    def test_oversize_put_is_json_413_and_does_not_burn_url(self):
        pending = mint_upload_url(self.workspace, self.key, "shot.png")
        response = self._put(pending, b"x" * (MAX_FILE_SIZE + 1))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["code"], "file_too_large")
        retry = self._put(pending, PNG, content_type="image/png")
        self.assertEqual(retry.status_code, 201)

    def test_rejected_type_put_does_not_burn_url(self):
        pending = mint_upload_url(self.workspace, self.key, "shot.png")
        response = self._put(pending, b"just some text", content_type="image/png")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "file_type_not_allowed")
        retry = self._put(pending, PNG, content_type="image/png")
        self.assertEqual(retry.status_code, 201)
        # ...and the success still consumes it.
        self.assertEqual(self._put(pending, PNG).status_code, 404)

    def test_rejected_secret_put_does_not_burn_url(self):
        pending = mint_upload_url(self.workspace, self.key, "env.txt", on_secret="reject")
        response = self._put(pending, SECRET, content_type="text/plain")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "secret_detected")
        retry = self._put(pending, b"scrubbed", content_type="text/plain")
        self.assertEqual(retry.status_code, 201)

    def test_claim_is_exclusive_while_pipeline_runs(self):
        pending = mint_upload_url(self.workspace, self.key, "shot.png")
        path = f"/api/v1/uploads/{pending.token}"
        inner = {}
        real_upload = services.upload_artifact

        def racing_upload(*args, **kwargs):
            # A second PUT to the same URL lands while the first is inside
            # the pipeline: it must not also succeed.
            inner["response"] = APIClient().put(
                path, data=PNG, content_type="image/png"
            )
            return real_upload(*args, **kwargs)

        with mock.patch("drop_core.views.upload_artifact", side_effect=racing_upload):
            first = self._put(path, PNG, content_type="image/png")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(inner["response"].status_code, 404)
        self.assertEqual(Artifact.objects.count(), 1)


# ---------------------------------------------------------------------------
# upl-2 / mcp-1 / ctr-3: on_secret from create_upload_url is honored
# ---------------------------------------------------------------------------


class PendingUploadOnSecretTests(UploadFixBase):
    def test_create_upload_url_reject_is_enforced_on_plain_put(self):
        result = self._mcp(
            "create_upload_url", {"filename": "env.txt", "on_secret": "reject"}
        )
        path = urlparse(result["upload_url"]).path
        response = self._put(path, SECRET, content_type="text/plain")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "secret_detected")
        self.assertEqual(Artifact.objects.count(), 0)
        self.assertEqual(PendingUpload.objects.get().on_secret, "reject")

    def test_query_param_cannot_loosen_minted_reject(self):
        pending = mint_upload_url(self.workspace, self.key, "env.txt", on_secret="reject")
        response = self._put(
            f"/api/v1/uploads/{pending.token}?on_secret=flag",
            SECRET,
            content_type="text/plain",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "secret_detected")

    def test_query_param_can_tighten_minted_flag(self):
        pending = mint_upload_url(self.workspace, self.key, "env.txt")
        self.assertEqual(pending.on_secret, "flag")
        response = self._put(
            f"/api/v1/uploads/{pending.token}?on_secret=reject",
            SECRET,
            content_type="text/plain",
        )
        self.assertEqual(response.json()["code"], "secret_detected")

    def test_default_mint_still_flags_and_stores(self):
        result = self._mcp("create_upload_url", {"filename": "env.txt"})
        response = self._put(urlparse(result["upload_url"]).path, SECRET)
        self.assertEqual(response.status_code, 201)
        self.assertIn("github_pat", response.json()["secret_flags"])


# ---------------------------------------------------------------------------
# upl-8: bad on_secret / ttl_days fail closed
# ---------------------------------------------------------------------------


class FailClosedParameterTests(UploadFixBase):
    def test_on_secret_is_case_insensitive(self):
        for value in ("Reject", "REJECT", " reject "):
            response = self._post(name="leak.txt", content=SECRET, on_secret=value)
            self.assertEqual(response.status_code, 400, value)
            self.assertEqual(response.json()["code"], "secret_detected", value)
        self.assertEqual(Artifact.objects.count(), 0)

    def test_unknown_on_secret_is_400_and_stores_nothing(self):
        for value in ("block", "true", "rejected"):
            response = self._post(name="leak.txt", content=SECRET, on_secret=value)
            self.assertEqual(response.status_code, 400, value)
            body = response.json()
            self.assertEqual(body["code"], "invalid_request", value)
            self.assertIn("on_secret", body["detail"])
        self.assertEqual(Artifact.objects.count(), 0)

    def test_unknown_on_secret_query_on_put_is_400_without_burning(self):
        pending = mint_upload_url(self.workspace, self.key, "env.txt")
        response = self._put(
            f"/api/v1/uploads/{pending.token}?on_secret=Rejekt", SECRET
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_request")
        self.assertEqual(Artifact.objects.count(), 0)
        self.assertEqual(self._put(pending, b"clean").status_code, 201)

    def test_non_integer_ttl_is_400(self):
        self.workspace.plan = "scale"
        self.workspace.save()
        for value in ("0.5", "1.5", "1e3", "abc", "7d"):
            response = self._post(ttl_days=value)
            self.assertEqual(response.status_code, 400, value)
            body = response.json()
            self.assertEqual(body["code"], "invalid_request", value)
            self.assertIn("ttl_days", body["detail"])
        self.assertEqual(Artifact.objects.count(), 0)

    def test_integer_ttl_values_the_clis_send_still_work(self):
        # The Python CLI sends str(int); the npm CLI sends the raw argument.
        for value, expected_days, adjusted in (
            ("3", 3, False),
            ("999", 7, True),
            ("0", 1, True),
            ("-2", 1, True),
        ):
            response = self._post(ttl_days=value)
            self.assertEqual(response.status_code, 201, value)
            body = response.json()
            self.assertEqual(body.get("ttl_adjusted", False), adjusted, value)
            artifact = Artifact.objects.get(id=body["id"])
            days = (artifact.expires_at - timezone.now()).total_seconds() / 86400
            self.assertAlmostEqual(days, expected_days, delta=0.01)

    def test_services_reject_unparseable_ttl(self):
        with self.assertRaises(ValueError) as ctx:
            upload_artifact(self.workspace, self.key, "a.txt", b"x", None, ttl_days="0.5")
        self.assertEqual(str(ctx.exception), "invalid_request")


# ---------------------------------------------------------------------------
# upl-3: quota check-then-act is atomic
# ---------------------------------------------------------------------------


class AtomicQuotaTests(UploadFixBase):
    def test_monthly_cap_holds_when_upload_interleaves(self):
        cap = PLANS["free"]["uploads"]
        UsageCounter.objects.create(
            workspace=self.workspace, year_month=_month(), uploads=cap - 1
        )
        real_sum = services.workspace_storage_bytes
        calls = {"n": 0}

        def interleaving_sum(workspace):
            calls["n"] += 1
            if calls["n"] == 1:
                # A concurrent request runs between this upload's cap
                # check and its write.
                try:
                    upload_artifact(self.workspace, self.key, "inner.txt", b"inner", None)
                except ValueError:
                    pass
            return real_sum(workspace)

        with mock.patch.object(services, "workspace_storage_bytes", interleaving_sum):
            try:
                upload_artifact(self.workspace, self.key, "outer.txt", b"outer", None)
            except ValueError as exc:
                self.assertEqual(str(exc), "monthly_cap_reached")
        self.assertEqual(Artifact.objects.filter(workspace=self.workspace).count(), 1)
        counter = UsageCounter.objects.get(workspace=self.workspace, year_month=_month())
        self.assertEqual(counter.uploads, cap)

    def test_storage_cap_holds_when_upload_interleaves(self):
        real_sum = services.workspace_storage_bytes
        calls = {"n": 0}

        def stale_sum(workspace):
            calls["n"] += 1
            value = real_sum(workspace)
            if calls["n"] == 1:
                # The concurrent upload lands after this request summed
                # storage but before it wrote its row.
                try:
                    upload_artifact(self.workspace, self.key, "inner.txt", b"y" * 600, None)
                except ValueError:
                    pass
            return value

        with mock.patch.dict(PLANS["free"], {"storage_gb": 1024 / 1024**3}):
            with mock.patch.object(services, "workspace_storage_bytes", stale_sum):
                try:
                    upload_artifact(self.workspace, self.key, "outer.txt", b"x" * 600, None)
                except ValueError as exc:
                    self.assertEqual(str(exc), "storage_cap_reached")
        self.assertLessEqual(services.workspace_storage_bytes(self.workspace), 1024)
        self.assertEqual(Artifact.objects.filter(workspace=self.workspace).count(), 1)
        counter = UsageCounter.objects.get(workspace=self.workspace, year_month=_month())
        self.assertEqual(counter.uploads, 1)

    def test_storage_failure_releases_reserved_quota(self):
        class BrokenStorage:
            def save(self, *args, **kwargs):
                raise RuntimeError("r2 down")

        with mock.patch("drop_core.services.get_storage", return_value=BrokenStorage()):
            with self.assertLogs("drop_core", level="ERROR"):
                with self.assertRaises(services.StorageError):
                    upload_artifact(self.workspace, self.key, "a.txt", b"x", None)
        self.assertEqual(Artifact.objects.count(), 0)
        counter = UsageCounter.objects.get(workspace=self.workspace, year_month=_month())
        self.assertEqual(counter.uploads, 0)


# ---------------------------------------------------------------------------
# upl-4: REST multipart uploads are bounded before buffering
# ---------------------------------------------------------------------------


class MultipartSizeBoundTests(UploadFixBase):
    def test_declared_oversize_body_rejected_before_parsing(self):
        # Declared Content-Length far above the cap with a tiny real body:
        # the server must answer 413 from the header alone, without trying
        # to read (and buffer) the declared bytes.
        response = self.client.post(
            "/api/v1/artifacts/",
            {"file": SimpleUploadedFile("a.txt", b"x")},
            CONTENT_LENGTH=str(600 * MiB),
            **self.auth,
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["code"], "file_too_large")

    def test_oversize_file_never_read_into_memory(self):
        with mock.patch(
            "django.core.files.uploadedfile.TemporaryUploadedFile.read",
            side_effect=AssertionError("oversize upload was read"),
        ):
            response = self._post(name="big.txt", content=b"x" * (MAX_FILE_SIZE + 1))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["code"], "file_too_large")

    def test_max_size_file_still_accepted(self):
        response = self._post(name="max.txt", content=b"x" * MAX_FILE_SIZE)
        self.assertEqual(response.status_code, 201)


# ---------------------------------------------------------------------------
# upl-7 / mcp-8 / ctr-11: Content-Type is derived on the server
# ---------------------------------------------------------------------------


class ServerMimeTypeTests(UploadFixBase):
    def _serve(self, artifact_id):
        artifact = Artifact.objects.get(id=artifact_id)
        response = self.client.get(f"/a/{artifact.token}")
        self.assertEqual(response.status_code, 200)
        return artifact, response

    def test_declared_text_html_is_served_as_plain_text(self):
        body = self._post(
            name="note.txt", content=b"<h1>Session expired</h1>", content_type="text/html"
        ).json()
        artifact, response = self._serve(body["id"])
        self.assertEqual(artifact.mime_type, "text/plain")
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertTrue(response["Content-Disposition"].startswith("inline"))
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Content-Security-Policy"], "sandbox allow-downloads")

    def test_svg_declared_markdown_is_plain_text(self):
        body = self._post(
            name="pic.md", content=b"<svg onload=alert(1)>", content_type="image/svg+xml"
        ).json()
        _artifact, response = self._serve(body["id"])
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")

    def test_npm_octet_stream_png_is_inline_image(self):
        body = self._post(
            name="shot.png", content=PNG, content_type="application/octet-stream"
        ).json()
        artifact, response = self._serve(body["id"])
        self.assertEqual(artifact.mime_type, "image/png")
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response["Content-Disposition"].startswith("inline"))

    def test_json_is_attachment_with_charset(self):
        body = self._post(
            name="data.json", content=b'{"a": 1}', content_type="text/html"
        ).json()
        artifact, response = self._serve(body["id"])
        self.assertEqual(artifact.mime_type, "application/json")
        self.assertEqual(response["Content-Type"], "application/json; charset=utf-8")
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))

    def test_put_ignores_declared_type(self):
        pending = mint_upload_url(self.workspace, self.key, "page.txt")
        body = self._put(pending, b"<script>x</script>", content_type="text/html").json()
        artifact, response = self._serve(body["id"])
        self.assertEqual(artifact.mime_type, "text/plain")
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")

    def test_mcp_mime_type_argument_is_ignored(self):
        result = self._mcp(
            "upload_artifact",
            {
                "filename": "page.txt",
                "content_base64": base64.b64encode(b"<h1>hi</h1>").decode("ascii"),
                "mime_type": "text/html",
            },
        )
        artifact, response = self._serve(result["id"])
        self.assertEqual(artifact.mime_type, "text/plain")
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")

    def test_legacy_rows_with_bad_mime_are_fixed_at_serve(self):
        cases = (
            ("legacy.txt", "text/html", "text/plain; charset=utf-8", "inline"),
            ("legacy.png", "image/svg+xml", "image/png", "inline"),
            ("legacy.json", "text/html", "application/json; charset=utf-8", "attachment"),
            ("legacy.pdf", "text/html", "application/pdf", "inline"),
            ("legacy", "text/html", "application/octet-stream", "attachment"),
        )
        for filename, stored, served, disposition in cases:
            artifact = Artifact.objects.create(
                workspace=self.workspace,
                filename=filename,
                mime_type=stored,
                size_bytes=4,
                expires_at=timezone.now() + timedelta(days=1),
            )
            get_storage().save(artifact.token, io.BytesIO(b"data"), stored)
            response = self.client.get(f"/a/{artifact.token}")
            self.assertEqual(response["Content-Type"], served, filename)
            self.assertTrue(
                response["Content-Disposition"].startswith(disposition), filename
            )


# ---------------------------------------------------------------------------
# Server half of npm-1 / pycli-1 / ctr-2 / G1: filenames
# ---------------------------------------------------------------------------


class FilenameSanitizationTests(UploadFixBase):
    HOSTILE = (
        "..%2F..%2Fescaped.json",
        ".claude%2Fsettings.json",
        "..%5C..%5Cevil.txt",
        "C:evil.txt",
        "%2e%2e%2fdot.txt",
    )

    def _assert_safe(self, name):
        self.assertNotRegex(name, r"[/\\%:\"\x00-\x1f\x7f]")
        self.assertFalse(name.startswith((".", " ")), name)
        self.assertLessEqual(len(name.encode("utf-8")), 200)

    def test_rest_hostile_names_are_stored_safe(self):
        for hostile in self.HOSTILE:
            response = self._post(name=hostile, content=b'{"a": 1}')
            self.assertEqual(response.status_code, 201, hostile)
            artifact = Artifact.objects.get(id=response.json()["id"])
            self._assert_safe(artifact.filename)
            self.assertEqual(
                artifact.filename.rsplit(".", 1)[-1], hostile.rsplit(".", 1)[-1]
            )

    def test_old_clients_cannot_be_steered_outside_cwd(self):
        for hostile in self.HOSTILE:
            body = self._post(name=hostile, content=b'{"a": 1}').json()
            token = Artifact.objects.get(id=body["id"]).token
            disposition = self.client.get(f"/a/{token}")["Content-Disposition"]
            saved_as = _npm_cli_filename(disposition)
            self.assertEqual(os.path.basename(saved_as), saved_as, disposition)
            self.assertNotIn("\\", saved_as)
            self.assertFalse(saved_as.startswith("."), disposition)

    def test_mcp_and_put_names_are_sanitized(self):
        result = self._mcp(
            "upload_artifact",
            {
                "filename": "C:\\Users\\me\\..\\shot\x00\x1b[31m.txt",
                "content_base64": base64.b64encode(b"hello").decode("ascii"),
            },
        )
        stored = Artifact.objects.get(id=result["id"]).filename
        self._assert_safe(stored)
        self.assertTrue(stored.endswith(".txt"))
        minted = self._mcp("create_upload_url", {"filename": "..%2F.bashrc.txt"})
        self._assert_safe(minted["filename"])
        response = self._put(urlparse(minted["upload_url"]).path, b"hi")
        self._assert_safe(Artifact.objects.get(id=response.json()["id"]).filename)

    def test_edge_names(self):
        sanitize = services.sanitize_filename
        self.assertEqual(sanitize(".png"), "upload.png")
        self.assertEqual(sanitize("...png"), "upload.png")
        self.assertEqual(sanitize("dir/sub/shot.png"), "shot.png")
        self.assertEqual(sanitize("dir\\sub\\shot.png"), "shot.png")
        self.assertEqual(sanitize("  .hidden.txt"), "hidden.txt")
        self.assertEqual(sanitize("a\u202egnp.txt"), "a_gnp.txt")
        long_name = sanitize("x" * 600 + ".log")
        self.assertTrue(long_name.endswith(".log"))
        self.assertLessEqual(len(long_name.encode("utf-8")), 200)
        wide = sanitize("\u00e9" * 300 + ".md")
        self.assertTrue(wide.endswith(".md"))
        self.assertLessEqual(len(wide.encode("utf-8")), 200)

    def test_legacy_hostile_row_is_sanitized_at_serve(self):
        artifact = Artifact.objects.create(
            workspace=self.workspace,
            filename="..%2F..%2Fescaped.json",
            mime_type="application/json",
            size_bytes=2,
            expires_at=timezone.now() + timedelta(days=1),
        )
        get_storage().save(artifact.token, io.BytesIO(b"{}"), "")
        disposition = self.client.get(f"/a/{artifact.token}")["Content-Disposition"]
        self.assertNotIn("%", disposition)
        saved_as = _npm_cli_filename(disposition)
        self.assertEqual(os.path.basename(saved_as), saved_as)
        self.assertFalse(saved_as.startswith("."))

    def test_inline_disposition_uses_rfc6266_encoding(self):
        body = self._post(name="r\u00e9sum\u00e9.txt", content=b"cv").json()
        token = Artifact.objects.get(id=body["id"]).token
        disposition = self.client.get(f"/a/{token}")["Content-Disposition"]
        self.assertEqual(
            disposition, "inline; filename*=utf-8''r%C3%A9sum%C3%A9.txt"
        )
        body = self._post(name="plain name.txt", content=b"cv").json()
        token = Artifact.objects.get(id=body["id"]).token
        self.assertEqual(
            self.client.get(f"/a/{token}")["Content-Disposition"],
            'inline; filename="plain name.txt"',
        )


# ---------------------------------------------------------------------------
# auth-2: session uploads need a verified email
# ---------------------------------------------------------------------------


class SessionUploadVerificationTests(UploadFixBase):
    def test_unverified_session_upload_is_403(self):
        user = User.objects.create_user(username="nobody", password="pw12345678")
        client = APIClient()
        client.force_login(user)
        response = client.post(
            "/api/v1/artifacts/",
            {"file": SimpleUploadedFile("a.txt", b"x")},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "email_verification_required")
        self.assertEqual(Artifact.objects.count(), 0)

    def test_verified_session_upload_still_works(self):
        client = APIClient()
        client.force_login(self.user)
        response = client.post(
            "/api/v1/artifacts/",
            {"file": SimpleUploadedFile("a.txt", b"x")},
        )
        self.assertEqual(response.status_code, 201)

    def test_api_key_upload_unaffected_by_verification(self):
        EmailVerification.objects.filter(user=self.user).update(is_verified=False)
        self.assertEqual(self._post().status_code, 201)


# ---------------------------------------------------------------------------
# auth-13 / ctr-17 / upl-11: the minting credential governs the URL
# ---------------------------------------------------------------------------


class MintingCredentialTests(UploadFixBase):
    def test_revoked_key_kills_its_upload_urls(self):
        pending = mint_upload_url(self.workspace, self.key, "shot.png")
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.delete(f"/api/v1/keys/{self.key.id}/").status_code, 204
        )
        self.client.logout()
        response = self._put(pending, PNG, content_type="image/png")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "not_found")
        self.assertEqual(Artifact.objects.count(), 0)

    def test_key_minted_url_records_no_oauth_application(self):
        result = self._mcp("create_upload_url", {"filename": "shot.png"})
        token = urlparse(result["upload_url"]).path.rsplit("/", 1)[-1]
        pending = PendingUpload.objects.get(token=token)
        self.assertEqual(pending.api_key_id, self.key.id)
        self.assertIsNone(pending.oauth_application_id)

    def test_put_artifact_is_attributed_to_minting_key(self):
        pending = mint_upload_url(self.workspace, self.key, "shot.png")
        body = self._put(pending, PNG, content_type="image/png").json()
        self.assertEqual(Artifact.objects.get(id=body["id"]).api_key_id, self.key.id)

    def _oauth_token(self):
        app = Application.objects.create(
            name="client",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris="http://127.0.0.1:9876/callback",
        )
        return AccessToken.objects.create(
            user=self.user,
            application=app,
            token=secrets.token_urlsafe(32),
            expires=timezone.now() + timedelta(hours=1),
            scope=OAUTH_SCOPE_MCP_USE,
        )

    def test_oauth_minted_url_works_until_grants_revoked(self):
        token = self._oauth_token()
        result = self._mcp(
            "create_upload_url", {"filename": "shot.png"}, raw_key=token.token
        )
        path = urlparse(result["upload_url"]).path
        self.assertEqual(
            PendingUpload.objects.get(token=path.rsplit("/", 1)[-1]).oauth_application_id,
            token.application_id,
        )
        self.assertEqual(self._put(path, PNG).status_code, 201)

        result = self._mcp(
            "create_upload_url", {"filename": "shot.png"}, raw_key=token.token
        )
        path = urlparse(result["upload_url"]).path
        revoke_user_oauth_grants(self.user)
        response = self._put(path, PNG)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Artifact.objects.count(), 1)

    def test_enforce_block_on_put_suspends_minting_key(self):
        os.environ["VEILFILE_SAFETY_MODE"] = "enforce"
        bad = b"known bad content"
        BlockedHash.objects.create(sha256=services.content_sha256(bad))
        pending = mint_upload_url(self.workspace, self.key, "bad.txt")
        with self.assertLogs("drop_core", level="WARNING"):
            response = self._put(
                pending,
                bad,
                content_type="text/plain",
                REMOTE_ADDR="10.0.0.5",
                HTTP_X_FORWARDED_FOR="203.0.113.7",
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "content_not_allowed")
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)
        event = SafetyEvent.objects.get()
        self.assertEqual(event.api_key_id, self.key.id)
        self.assertEqual(event.uploader_ip, "203.0.113.7")
        # A safety block does burn the URL.
        self.assertEqual(self._put(pending, b"fine").status_code, 404)


# ---------------------------------------------------------------------------
# upl-12: storage failures are logged 503s, missing objects stay 404
# ---------------------------------------------------------------------------


class ServeStorageFailureTests(UploadFixBase):
    def _artifact(self):
        body = self._post().json()
        return Artifact.objects.get(id=body["id"])

    def test_backend_error_is_logged_503(self):
        artifact = self._artifact()
        with mock.patch(
            "drop_core.storage.LocalStorage.open", side_effect=RuntimeError("bad creds")
        ):
            with self.assertLogs("drop_core", level="ERROR") as logs:
                response = self.client.get(f"/a/{artifact.token}")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn(str(artifact.id), "\n".join(logs.output))
        self.assertNotIn(artifact.token, "\n".join(logs.output))

    def test_503_does_not_log_the_token_path(self):
        # django.request logs 5xx as "<reason>: <path>" at ERROR, and the
        # path holds the bearer token.
        artifact = self._artifact()
        with mock.patch(
            "drop_core.storage.LocalStorage.open", side_effect=RuntimeError("bad creds")
        ):
            with self.assertLogs("drop_core", level="ERROR"):
                with self.assertNoLogs("django.request", level="WARNING"):
                    response = self.client.get(f"/a/{artifact.token}")
        self.assertEqual(response.status_code, 503)

    def test_storage_construction_error_is_503(self):
        artifact = self._artifact()
        with mock.patch(
            "drop_core.views.get_storage", side_effect=RuntimeError("no bucket")
        ):
            with self.assertLogs("drop_core", level="ERROR"):
                response = self.client.get(f"/a/{artifact.token}")
        self.assertEqual(response.status_code, 503)

    def test_missing_object_is_404(self):
        artifact = self._artifact()
        get_storage().delete(artifact.token)
        self.assertEqual(self.client.get(f"/a/{artifact.token}").status_code, 404)

    def test_s3_no_such_key_is_404(self):
        artifact = self._artifact()

        class NoSuchKey(Exception):
            response = {"Error": {"Code": "NoSuchKey", "Message": "gone"}}

        with mock.patch(
            "drop_core.storage.LocalStorage.open", side_effect=NoSuchKey()
        ):
            response = self.client.get(f"/a/{artifact.token}")
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Review follow-ups: safety-audit IP, hard-deleted minting keys, the
# invalid_request message, sanitize_filename idempotency
# ---------------------------------------------------------------------------


class SafetyAuditIpTests(UploadFixBase):
    # SafetyEvent.uploader_ip is varchar(64). Postgres refuses a longer
    # value (DataError, the block becomes a 500 and nothing is contained);
    # SQLite stores it, so these assert the stored value directly.
    # The chains end in a Cloudflare edge, as a request through Cloudflare does:
    # the entry before it is the visitor (client_ip uses it), and here it is the
    # one the test makes unusable. Without the edge the last entry is the client.
    CF_EDGE = "172.69.4.12"
    LONG_XFF = "198.51.100.9, " + "A" * 80 + ", " + CF_EDGE

    def setUp(self):
        super().setUp()
        os.environ["VEILFILE_SAFETY_MODE"] = "enforce"
        self.bad = b"known bad content"
        BlockedHash.objects.create(sha256=services.content_sha256(self.bad))

    def _assert_contained(self, response, expected_ip):
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "content_not_allowed")
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)
        event = SafetyEvent.objects.get()
        self.assertEqual(event.api_key_id, self.key.id)
        self.assertEqual(event.uploader_ip, expected_ip)

    def test_rest_block_with_overlong_forwarded_for_still_contains(self):
        with self.assertLogs("drop_core", level="WARNING"):
            response = self.client.post(
                "/api/v1/artifacts/",
                {"file": SimpleUploadedFile("bad.txt", self.bad)},
                REMOTE_ADDR="10.0.0.5",
                HTTP_X_FORWARDED_FOR=self.LONG_XFF,
                **self.auth,
            )
        self._assert_contained(response, "10.0.0.5")

    def test_put_block_with_overlong_forwarded_for_still_contains(self):
        pending = mint_upload_url(self.workspace, self.key, "bad.txt")
        with self.assertLogs("drop_core", level="WARNING"):
            response = self._put(
                pending,
                self.bad,
                REMOTE_ADDR="10.0.0.5",
                HTTP_X_FORWARDED_FOR=self.LONG_XFF,
            )
        self._assert_contained(response, "10.0.0.5")

    def test_non_ip_forwarded_for_falls_back_to_peer(self):
        with self.assertLogs("drop_core", level="WARNING"):
            response = self.client.post(
                "/api/v1/artifacts/",
                {"file": SimpleUploadedFile("bad.txt", self.bad)},
                REMOTE_ADDR="10.0.0.5",
                HTTP_X_FORWARDED_FOR=f"unknown, {self.CF_EDGE}",
                **self.auth,
            )
        self._assert_contained(response, "10.0.0.5")

    def test_forwarded_ip_is_recorded(self):
        with self.assertLogs("drop_core", level="WARNING"):
            response = self.client.post(
                "/api/v1/artifacts/",
                {"file": SimpleUploadedFile("bad.txt", self.bad)},
                REMOTE_ADDR="10.0.0.5",
                HTTP_X_FORWARDED_FOR=f"2001:DB8::1, {self.CF_EDGE}",
                **self.auth,
            )
        self._assert_contained(response, "2001:db8::1")

    def test_service_caps_uploader_ip_to_column_width(self):
        os.environ["VEILFILE_SAFETY_MODE"] = "log"
        with self.assertLogs("drop_core", level="WARNING"):
            upload_artifact(
                self.workspace,
                self.key,
                "bad.txt",
                self.bad,
                None,
                uploader_ip="B" * 100,
            )
        self.assertEqual(SafetyEvent.objects.get().uploader_ip, "B" * 64)


class HardDeletedKeyTests(UploadFixBase):
    def test_hard_deleted_key_kills_its_upload_urls(self):
        pending = mint_upload_url(self.workspace, self.key, "shot.png")
        self.key.delete()  # Django admin, or an account-deletion cascade
        response = self._put(pending, PNG, content_type="image/png")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "not_found")
        self.assertEqual(Artifact.objects.count(), 0)


class PutStorageFailureLogTests(UploadFixBase):
    def test_put_502_does_not_log_the_live_upload_token(self):
        class BrokenStorage:
            def save(self, *args, **kwargs):
                raise RuntimeError("r2 down")

        pending = mint_upload_url(self.workspace, self.key, "note.txt")
        with mock.patch("drop_core.services.get_storage", return_value=BrokenStorage()):
            with self.assertLogs("drop_core", level="ERROR") as logs:
                with self.assertNoLogs("django.request", level="WARNING"):
                    response = self._put(pending, b"hello")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(pending.token, "\n".join(logs.output))
        # The claim was released: the same URL still works.
        self.assertEqual(self._put(pending, b"hello").status_code, 201)


class InvalidRequestMessageTests(UploadFixBase):
    # npm veilfile@0.2.0 and PyPI veilfile 0.3.0 print only body["error"].
    def test_rest_error_names_the_bad_parameter(self):
        body = self._post(ttl_days="1.5").json()
        self.assertEqual(body["code"], "invalid_request")
        self.assertIn("ttl_days", body["error"])
        body = self._post(on_secret="block").json()
        self.assertEqual(body["code"], "invalid_request")
        self.assertIn("on_secret", body["error"])

    def test_put_error_names_the_bad_parameter(self):
        pending = mint_upload_url(self.workspace, self.key, "env.txt")
        body = self._put(
            f"/api/v1/uploads/{pending.token}?on_secret=Rejekt", b"hi"
        ).json()
        self.assertEqual(body["code"], "invalid_request")
        self.assertIn("on_secret", body["error"])


class SanitizeIdempotencyTests(TestCase):
    def test_sanitize_is_idempotent(self):
        sanitize = services.sanitize_filename
        names = [
            "<x..b ..",
            '"%.∕...1.',
            "\U0001F600..1.",
            "\\. '\"..Z ",
            "a..png",
            "report.txt. . ",
        ]
        # Long names without a usable extension, truncated next to a dot.
        for pad in ("a", "é", "a."):
            for n in range(180, 230):
                for tail in ("..def-ghi", ". .ab-c", ".png.x-y"):
                    names.append(pad * n + tail)
        for name in names:
            once = sanitize(name)
            self.assertEqual(sanitize(once), once, repr(name))
            self.assertFalse(once.endswith((".", " ")), repr(name))
            self.assertLessEqual(len(once.encode("utf-8")), 200)
