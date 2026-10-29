"""Veilfile backend core tests."""

import io
import re
import os
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from drop_core.models import (
    APIKey,
    Artifact,
    DeviceCode,
    EmailVerification,
    FunnelEvent,
    UsageCounter,
    get_or_create_workspace,
)
from drop_core.device import _DEVICE_GRANT_TYPE, _issue_device_code
from drop_core.plans import PLANS
from drop_core.services import MAX_FILE_SIZE, revoke_artifact, upload_artifact
from drop_core.storage import get_storage

User = get_user_model()


def _month():
    return timezone.now().strftime("%Y-%m")


def _use_local_dir(testcase, path):
    """Point LocalStorage at ``path`` for one test. The previous
    DROP_LOCAL_DIR (the test runner's per-run temp dir) is restored
    afterwards rather than unset, so later tests never fall back to
    backend/media/artifacts."""
    patcher = mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": path})
    patcher.start()
    testcase.addCleanup(patcher.stop)


class VeilfileCoreTests(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        _use_local_dir(self, self.tmpdir)
        self.user = User.objects.create_user(username="alice", password="pw12345678")
        self.workspace = get_or_create_workspace(self.user)
        self.key, self.raw_key = APIKey.create_key(self.workspace, "agent-key")
        self.client = APIClient()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.raw_key}"}
        # Key-minting is gated on verified email; the default test user is
        # verified so existing key-management tests exercise the happy path.
        # EmailVerificationTests covers the unverified cases explicitly.
        from drop_core.models import EmailVerification

        EmailVerification.objects.create(
            user=self.user,
            email="alice@example.com",
            is_verified=True,
            verified_at=timezone.now(),
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- helpers -----------------------------------------------------------

    def _upload(self, name="note.txt", content=b"hello veilfile", **kwargs):
        file = SimpleUploadedFile(name, content, content_type="text/plain")
        data = {"file": file}
        data.update(kwargs)
        return self.client.post("/api/v1/artifacts/", data, **self.auth)

    # -- upload ------------------------------------------------------------

    def test_upload_201_and_response_shape(self):
        response = self._upload()
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(
            set(body.keys()), {"id", "url", "expires_at", "size_bytes", "secret_flags"}
        )
        self.assertTrue(body["url"].startswith("http://localhost:8000/a/"))
        self.assertEqual(body["size_bytes"], len(b"hello veilfile"))
        self.assertEqual(body["secret_flags"], [])
        artifact = Artifact.objects.get(id=body["id"])
        self.assertTrue(get_storage().exists(artifact.token))

    def test_upload_missing_key_401(self):
        response = self.client.post("/api/v1/artifacts/", {"file": SimpleUploadedFile("a.txt", b"x")})
        self.assertEqual(response.status_code, 401)

    def test_upload_bad_key_401(self):
        response = self.client.post(
            "/api/v1/artifacts/",
            {"file": SimpleUploadedFile("a.txt", b"x")},
            HTTP_AUTHORIZATION="Bearer vlf_thisisnotarealkey",
        )
        self.assertEqual(response.status_code, 401)

    def test_upload_wrong_prefix_401(self):
        response = self.client.post(
            "/api/v1/artifacts/",
            {"file": SimpleUploadedFile("a.txt", b"x")},
            HTTP_AUTHORIZATION="Bearer xyz_totallydifferent",
        )
        self.assertEqual(response.status_code, 401)

    def test_upload_malformed_header_401(self):
        response = self.client.post(
            "/api/v1/artifacts/",
            {"file": SimpleUploadedFile("a.txt", b"x")},
            HTTP_AUTHORIZATION="Bearer",
        )
        self.assertEqual(response.status_code, 401)

    def test_disallowed_extension_400(self):
        response = self._upload(name="payload.exe", content=b"MZ")
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "file_type_not_allowed")
        # Names the offending extension and reads the allowlist from code.
        self.assertIn("'.exe'", body["detail"])
        self.assertIn("png", body["detail"])
        self.assertIn("pdf", body["detail"])

    def test_magic_byte_mismatch_names_detected_kind(self):
        response = self._upload(name="fake.png", content=b"just some text")
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "file_type_not_allowed")
        self.assertIn("detected text", body["detail"])
        self.assertIn("'.png'", body["detail"])

    def test_blocked_archive_400(self):
        response = self._upload(name="bundle.zip", content=b"PK")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "file_type_not_allowed")

    def test_no_extension_400(self):
        response = self._upload(name="README", content=b"hi")
        self.assertEqual(response.status_code, 400)

    def test_oversize_413(self):
        response = self._upload(name="big.txt", content=b"x" * (MAX_FILE_SIZE + 1))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["code"], "file_too_large")

    def test_monthly_cap_429(self):
        UsageCounter.objects.create(
            workspace=self.workspace, year_month=_month(), uploads=PLANS["free"]["uploads"]
        )
        response = self._upload()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["code"], "monthly_cap_reached")

    # -- storage caps --------------------------------------------------------

    def _set_tiny_cap(self, cap_bytes):
        """Patch the free plan's storage cap to cap_bytes."""
        return mock.patch.dict(PLANS["free"], {"storage_gb": cap_bytes / 1024**3})

    def _seed_artifact(self, size_bytes, expired=False):
        return Artifact.objects.create(
            workspace=self.workspace,
            api_key=self.key,
            filename="seed.txt",
            mime_type="text/plain",
            size_bytes=size_bytes,
            expires_at=timezone.now() + timedelta(days=-1 if expired else 7),
        )

    def test_storage_cap_blocks_upload_over_cap(self):
        with self._set_tiny_cap(1024):
            self._seed_artifact(600)
            with self.assertRaises(ValueError) as ctx:
                upload_artifact(
                    self.workspace, self.key, "big.txt", b"x" * 500, "text/plain"
                )
            self.assertEqual(str(ctx.exception), "storage_cap_reached")

    def test_storage_cap_exact_boundary_succeeds(self):
        with self._set_tiny_cap(1024):
            self._seed_artifact(512)
            artifact = upload_artifact(
                self.workspace, self.key, "edge.txt", b"x" * 512, "text/plain"
            )
            self.assertEqual(artifact.size_bytes, 512)

    def test_storage_cap_ignores_expired_artifacts(self):
        with self._set_tiny_cap(1024):
            self._seed_artifact(900, expired=True)
            artifact = upload_artifact(
                self.workspace, self.key, "fresh.txt", b"x" * 500, "text/plain"
            )
            self.assertEqual(artifact.size_bytes, 500)

    def test_storage_cap_rest_returns_402(self):
        with self._set_tiny_cap(100):
            self._seed_artifact(90)
            response = self._upload(content=b"x" * 50)
            self.assertEqual(response.status_code, 402)
            self.assertEqual(response.json()["code"], "storage_cap_reached")

    def test_revoke_frees_storage_cap_space(self):
        with self._set_tiny_cap(1024):
            artifact = upload_artifact(
                self.workspace, self.key, "a.txt", b"x" * 900, "text/plain"
            )
            revoke_artifact(self.workspace, artifact.id)
            artifact2 = upload_artifact(
                self.workspace, self.key, "b.txt", b"y" * 900, "text/plain"
            )
            self.assertEqual(artifact2.size_bytes, 900)

    def test_ttl_days_clamped_to_plan_max(self):
        response = self._upload(ttl_days=999)
        self.assertEqual(response.status_code, 201)
        artifact = Artifact.objects.get(id=response.json()["id"])
        max_expiry = timezone.now() + timedelta(days=PLANS["free"]["ttl_days"], seconds=60)
        min_expiry = timezone.now() + timedelta(days=PLANS["free"]["ttl_days"] - 1)
        self.assertLessEqual(artifact.expires_at, max_expiry)
        self.assertGreater(artifact.expires_at, min_expiry)

    def test_ttl_clamped_sets_ttl_adjusted_flag(self):
        response = self._upload(ttl_days=999)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body.get("ttl_adjusted"))
        artifact = Artifact.objects.get(id=body["id"])
        max_expiry = timezone.now() + timedelta(days=PLANS["free"]["ttl_days"], seconds=60)
        self.assertLessEqual(artifact.expires_at, max_expiry)

    def test_ttl_within_max_has_no_adjusted_flag(self):
        response = self._upload(ttl_days=3)
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("ttl_adjusted", response.json())

    def test_ttl_default_has_no_adjusted_flag(self):
        response = self._upload()
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("ttl_adjusted", response.json())

    def test_secret_flags_detected(self):
        content = b'my token = "ghp_' + b"a" * 36 + b'" // do not share'
        response = self._upload(name="leak.txt", content=content)
        self.assertEqual(response.status_code, 201)
        self.assertIn("github_pat", response.json()["secret_flags"])

    def test_flagged_upload_includes_warning(self):
        content = b'my token = "ghp_' + b"a" * 36 + b'" // do not share'
        response = self._upload(name="leak.txt", content=content)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["secret_flags"])
        self.assertIn("warning", body)
        self.assertIn("secret_flags", body["warning"])

    def test_clean_upload_has_no_warning(self):
        response = self._upload(content=b"nothing sensitive here at all")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["secret_flags"], [])
        self.assertNotIn("warning", body)

    # -- one-time upload URLs (MCP create_upload_url) ------------------------

    def test_clean_file_has_no_flags(self):
        response = self._upload(content=b"nothing sensitive here at all")
        self.assertEqual(response.json()["secret_flags"], [])

    def _mint(self, filename="shot.png", **kwargs):
        from drop_core.services import mint_upload_url

        return mint_upload_url(self.workspace, self.key, filename, **kwargs)

    def test_put_to_upload_url_201(self):
        pending = self._mint()
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        response = self.client.put(
            f"/api/v1/uploads/{pending.token}", data=png, content_type="image/png"
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["size_bytes"], len(png))
        self.assertEqual(body["secret_flags"], [])
        self.assertIn("/a/", body["url"])
        self.assertTrue(
            Artifact.objects.filter(id=body["id"], filename="shot.png").exists()
        )
        self.assertEqual(
            self.client.get("/api/v1/usage/", **self.auth).json()["uploads_used"], 1
        )

    def test_upload_url_replay_404(self):
        pending = self._mint()
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        url = f"/api/v1/uploads/{pending.token}"
        self.assertEqual(
            self.client.put(url, data=png, content_type="image/png").status_code, 201
        )
        response = self.client.put(url, data=png, content_type="image/png")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "not_found")

    def test_upload_url_expired_404(self):
        from drop_core.models import PendingUpload

        pending = self._mint()
        PendingUpload.objects.filter(id=pending.id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        response = self.client.put(
            f"/api/v1/uploads/{pending.token}",
            data=b"hello",
            content_type="text/plain",
        )
        self.assertEqual(response.status_code, 404)

    def test_upload_url_unknown_token_404(self):
        response = self.client.put(
            "/api/v1/uploads/doesnotexist", data=b"x", content_type="text/plain"
        )
        self.assertEqual(response.status_code, 404)

    def test_upload_url_runs_full_pipeline(self):
        # Magic-byte verification applies to the PUT bytes too.
        pending = self._mint(filename="fake.png")
        response = self.client.put(
            f"/api/v1/uploads/{pending.token}",
            data=b"just some text",
            content_type="image/png",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "file_type_not_allowed")

    def test_upload_url_oversized_413(self):
        pending = self._mint()
        response = self.client.put(
            f"/api/v1/uploads/{pending.token}",
            data=b"x" * (25 * 1024 * 1024 + 1),
            content_type="application/octet-stream",
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["code"], "file_too_large")

    def test_upload_url_secret_scan_and_reject(self):
        content = b'my token = "ghp_' + b"a" * 36 + b'"'
        pending = self._mint(filename="leak.txt")
        body = self.client.put(
            f"/api/v1/uploads/{pending.token}",
            data=content,
            content_type="text/plain",
        ).json()
        self.assertIn("github_pat", body["secret_flags"])
        self.assertIn("warning", body)
        self.assertIn("github_pat", body["secret_flag_locations"])

        pending2 = self._mint(filename="leak.txt")
        response = self.client.put(
            f"/api/v1/uploads/{pending2.token}?on_secret=reject",
            data=content,
            content_type="text/plain",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "secret_detected")

    def test_upload_url_ttl_clamped_at_mint(self):
        pending = self._mint(ttl_days=9999)
        self.assertEqual(pending.ttl_days, 7)  # free plan max
        self.assertTrue(pending.ttl_adjusted)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        body = self.client.put(
            f"/api/v1/uploads/{pending.token}", data=png, content_type="image/png"
        ).json()
        artifact = Artifact.objects.get(id=body["id"])
        self.assertLessEqual(
            (artifact.expires_at - timezone.now()).total_seconds(), 7 * 86400 + 60
        )
        response = self._upload(content=b"nothing sensitive here at all")
        self.assertEqual(response.json()["secret_flags"], [])

    def test_on_secret_reject_blocks_flagged_upload(self):
        content = b'my token = "ghp_' + b"a" * 36 + b'"'
        response = self._upload(name="leak.txt", content=content, on_secret="reject")
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "secret_detected")
        self.assertIn("github_pat", body["detail"])
        # Refused before anything persisted: no artifact, no quota consumed.
        self.assertEqual(Artifact.objects.count(), 0)
        self.assertEqual(
            self.client.get("/api/v1/usage/", **self.auth).json()["uploads_used"], 0
        )

    def test_on_secret_reject_via_query_param(self):
        content = b'my token = "ghp_' + b"a" * 36 + b'"'
        file = SimpleUploadedFile("leak.txt", content, content_type="text/plain")
        response = self.client.post(
            "/api/v1/artifacts/?on_secret=reject", {"file": file}, **self.auth
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "secret_detected")

    def test_on_secret_reject_clean_upload_still_201(self):
        response = self._upload(
            content=b"nothing sensitive here at all", on_secret="reject"
        )
        self.assertEqual(response.status_code, 201)

    def test_secret_flag_locations(self):
        content = b'line one\nmy token = "ghp_' + b"a" * 36 + b'"\nline three\n'
        body = self._upload(name="leak.txt", content=content).json()
        self.assertEqual(body["secret_flags"], ["github_pat"])
        locs = body["secret_flag_locations"]["github_pat"]
        self.assertEqual(len(locs), 1)
        self.assertEqual(locs[0]["line"], 2)
        self.assertGreater(locs[0]["offset"], 0)

    def test_clean_upload_has_no_flag_locations(self):
        body = self._upload(content=b"nothing sensitive here at all").json()
        self.assertNotIn("secret_flag_locations", body)

    def test_aws_secret_key_detected_without_key_id(self):
        # 40 chars, no AKIA key id anywhere (AWS's documented example
        # shape; single-case hex such as a git SHA is not flagged).
        secret = b"wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        body = self._upload(
            name="creds.txt", content=b"the secret is\n" + secret + b"\n"
        ).json()
        self.assertIn("aws_secret_key", body["secret_flags"])
        self.assertNotIn("aws_access_key", body["secret_flags"])

    def test_upload_records_api_key_and_increments_counter(self):
        self._upload()
        artifact = Artifact.objects.get()
        self.assertEqual(artifact.api_key_id, self.key.id)
        counter = UsageCounter.objects.get(workspace=self.workspace, year_month=_month())
        self.assertEqual(counter.uploads, 1)

    def test_rejected_uploads_do_not_consume_quota(self):
        # Two different rejection reasons: blocked extension, magic-byte
        # mismatch. Neither may touch the monthly usage counter.
        self.assertEqual(
            self._upload(name="payload.exe", content=b"MZ").status_code, 400
        )
        self.assertEqual(
            self._upload(name="fake.png", content=b"not a real png").status_code,
            400,
        )
        counter = UsageCounter.objects.filter(
            workspace=self.workspace, year_month=_month()
        ).first()
        self.assertEqual(counter.uploads if counter else 0, 0)
        self.assertEqual(Artifact.objects.filter(workspace=self.workspace).count(), 0)

    # -- listing / revocation ----------------------------------------------

    def test_list_newest_first(self):
        first = self._upload(name="first.txt", content=b"1").json()
        second = self._upload(name="second.txt", content=b"2").json()
        response = self.client.get("/api/v1/artifacts/", **self.auth)
        self.assertEqual(response.status_code, 200)
        ids = [item["id"] for item in response.json()]
        self.assertEqual(ids, [second["id"], first["id"]])
        item = response.json()[0]
        self.assertEqual(
            set(item.keys()),
            {"id", "filename", "mime_type", "size_bytes", "url", "expires_at",
             "secret_flags", "created_at"},
        )

    def test_delete_revokes_artifact(self):
        artifact_id = self._upload().json()["id"]
        token = Artifact.objects.get(id=artifact_id).token
        response = self.client.delete(f"/api/v1/artifacts/{artifact_id}/", **self.auth)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Artifact.objects.filter(id=artifact_id).exists())
        self.assertFalse(get_storage().exists(token))

    def test_delete_unknown_404(self):
        response = self.client.delete(
            "/api/v1/artifacts/00000000-0000-0000-0000-000000000000/", **self.auth
        )
        self.assertEqual(response.status_code, 404)

    def test_cannot_delete_other_workspace_artifact(self):
        other_user = User.objects.create_user(username="other", password="pw12345678")
        other_ws = get_or_create_workspace(other_user)
        other_key, other_raw = APIKey.create_key(other_ws, "other-key")
        artifact = upload_artifact(other_ws, other_key, "x.txt", b"x", "text/plain")
        response = self.client.delete(f"/api/v1/artifacts/{artifact.id}/", **self.auth)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Artifact.objects.filter(id=artifact.id).exists())

    # -- public serving ------------------------------------------------------

    def test_serve_artifact_inline(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        file = SimpleUploadedFile("shot.png", png, content_type="image/png")
        token = Artifact.objects.get(
            id=self.client.post("/api/v1/artifacts/", {"file": file}, **self.auth).json()["id"]
        ).token
        response = self.client.get(f"/a/{token}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response["Content-Disposition"].startswith("inline"))
        self.assertEqual(b"".join(response.streaming_content), png)

    def test_serve_artifact_attachment(self):
        file = SimpleUploadedFile("data.json", b'{"a":1}', content_type="application/json")
        token = Artifact.objects.get(
            id=self.client.post("/api/v1/artifacts/", {"file": file}, **self.auth).json()["id"]
        ).token
        response = self.client.get(f"/a/{token}")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))

    def test_serve_unknown_token_404(self):
        self.assertEqual(self.client.get("/a/doesnotexist").status_code, 404)

    def test_serve_artifact_hardening_headers(self):
        artifact_id = self._upload().json()["id"]
        artifact = Artifact.objects.get(id=artifact_id)
        response = self.client.get(f"/a/{artifact.token}")
        self.assertEqual(response.status_code, 200)
        # Revocation must bite everywhere instantly: nothing may cache the bytes.
        self.assertEqual(response["Cache-Control"], "no-store")
        # Artifact URLs are unguessable secrets, never indexable content.
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        # Sandboxed so a browser-rendered file cannot touch the app origin.
        self.assertEqual(
            response["Content-Security-Policy"], "sandbox allow-downloads"
        )

    def test_serve_pdf_inline_with_sandbox_csp(self):
        pdf = b"%PDF-1.4\n%\xe2\xe3\n" + b"0" * 64
        file = SimpleUploadedFile("doc.pdf", pdf, content_type="application/pdf")
        artifact_id = self.client.post(
            "/api/v1/artifacts/", {"file": file}, **self.auth
        ).json()["id"]
        artifact = Artifact.objects.get(id=artifact_id)
        response = self.client.get(f"/a/{artifact.token}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response["Content-Disposition"].startswith("inline"))
        self.assertEqual(
            response["Content-Security-Policy"], "sandbox allow-downloads"
        )
        self.assertEqual(b"".join(response.streaming_content), pdf)

    def test_revocation_immediate_with_hardening_headers(self):
        artifact_id = self._upload().json()["id"]
        artifact = Artifact.objects.get(id=artifact_id)
        token = artifact.token
        self.assertEqual(self.client.get(f"/a/{token}").status_code, 200)
        response = self.client.delete(
            f"/api/v1/artifacts/{artifact_id}/", **self.auth
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.get(f"/a/{token}").status_code, 404)

    def test_expired_token_404(self):
        artifact_id = self._upload().json()["id"]
        artifact = Artifact.objects.get(id=artifact_id)
        artifact.expires_at = timezone.now() - timedelta(seconds=1)
        artifact.save()
        self.assertEqual(self.client.get(f"/a/{artifact.token}").status_code, 404)

    # -- key management (session auth) ---------------------------------------

    def test_key_create_returns_raw_once(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/v1/keys/", {"name": "second"}, content_type="application/json")
        # free plan allows exactly 1 key and one already exists
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()["code"], "key_limit_reached")

    def test_key_create_and_list_hides_hash(self):
        self.workspace.plan = "team"
        self.workspace.save()
        self.client.force_login(self.user)
        response = self.client.post("/api/v1/keys/", {"name": "ci-runner"}, content_type="application/json")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["api_key"].startswith("vlf_"))
        self.assertEqual(body["key_prefix"], body["api_key"][:12])

        listed = self.client.get("/api/v1/keys/").json()
        names = {k["name"] for k in listed}
        self.assertIn("ci-runner", names)
        for item in listed:
            self.assertNotIn("api_key", item)
            self.assertNotIn("key_hash", item)
            self.assertEqual(
                set(item.keys()),
                {"id", "name", "key_prefix", "created_at", "last_used_at", "is_active"},
            )

    def test_key_limit_402_on_free_plan(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/v1/keys/", {"name": "extra"}, content_type="application/json")
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()["code"], "key_limit_reached")

    def test_key_revoke_deactivates(self):
        self.client.force_login(self.user)
        response = self.client.delete(f"/api/v1/keys/{self.key.id}/")
        self.assertEqual(response.status_code, 204)
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)
        # revoked key no longer authenticates
        upload = self.client.post(
            "/api/v1/artifacts/",
            {"file": SimpleUploadedFile("a.txt", b"x")},
            **self.auth,
        )
        self.assertEqual(upload.status_code, 401)

    def test_key_delete_unknown_404(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.delete("/api/v1/keys/999999/").status_code, 404)

    def test_key_endpoints_require_login(self):
        # Bare requests get an honest 401: APIKeyAuthentication advertises
        # a WWW-Authenticate header, same as the usage endpoint.
        response = self.client.get("/api/v1/keys/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["WWW-Authenticate"], "Bearer")
        # usage/ accepts API-key auth, whose authenticator advertises a
        # WWW-Authenticate header, so bare requests get an honest 401.
        response = self.client.get("/api/v1/usage/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["WWW-Authenticate"], "Bearer")

    def test_keys_list_with_api_key(self):
        # An agent holding a workspace credential can list the workspace's
        # keys (this is what `veilfile logout` needs to find its own key).
        listed = self.client.get("/api/v1/keys/", **self.auth).json()
        prefixes = {k["key_prefix"] for k in listed}
        self.assertIn(self.key.key_prefix, prefixes)
        for item in listed:
            self.assertNotIn("api_key", item)
            self.assertNotIn("key_hash", item)

    def test_keys_list_with_bad_key_is_401(self):
        response = self.client.get(
            "/api/v1/keys/", HTTP_AUTHORIZATION="Bearer vlf_boguskey000000000000000000000000"
        )
        self.assertEqual(response.status_code, 401)

    def test_key_delete_with_api_key_revokes_self(self):
        # Full logout flow over key auth: list, match own prefix, delete.
        listed = self.client.get("/api/v1/keys/", **self.auth).json()
        match = next(
            k for k in listed if k["key_prefix"] == self.raw_key[:12]
        )
        response = self.client.delete(f"/api/v1/keys/{match['id']}/", **self.auth)
        self.assertEqual(response.status_code, 204)
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)
        # The revoked key no longer authenticates anywhere.
        self.assertEqual(
            self.client.get("/api/v1/keys/", **self.auth).status_code, 401
        )

    def test_key_create_with_api_key_is_403(self):
        # Credentials cannot mint credentials: under key auth request.user
        # is anonymous, so the email-verification gate denies issuance.
        # Only the dashboard session can create keys.
        response = self.client.post(
            "/api/v1/keys/",
            {"name": "sneaky"},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 403)

    def test_key_delete_scoped_to_own_workspace(self):
        other_user = User.objects.create_user(username="other", password="pw12345678")
        other_ws = get_or_create_workspace(other_user)
        _, other_raw = APIKey.create_key(other_ws, "other-key")
        # A key cannot delete keys from another workspace.
        response = self.client.delete(
            f"/api/v1/keys/{self.key.id}/",
            HTTP_AUTHORIZATION=f"Bearer {other_raw}",
        )
        self.assertEqual(response.status_code, 404)
        self.key.refresh_from_db()
        self.assertTrue(self.key.is_active)

    def test_billing_views_stay_session_only(self):
        # Billing drives Stripe operations off request.user; it is pinned
        # to SessionAuthentication and must ignore Bearer credentials.
        self.assertEqual(
            self.client.get("/api/v1/billing/status", **self.auth).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/billing/checkout",
                {"plan": "team"},
                content_type="application/json",
                **self.auth,
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post("/api/v1/billing/portal", **self.auth).status_code, 403
        )

    # -- usage ---------------------------------------------------------------

    def test_usage_with_api_key(self):
        self._upload()
        response = self.client.get("/api/v1/usage/", **self.auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["plan"], "free")
        self.assertEqual(body["uploads_used"], 1)
        self.assertEqual(body["uploads_cap"], 100)
        self.assertEqual(body["storage_used_bytes"], len(b"hello veilfile"))
        # Scoped to the key's account: the workspace owner's email, same as
        # the dashboard shows (empty here, mirroring test_usage_endpoint).
        self.assertEqual(body["email"], "")
        self.assertTrue(body["email_verified"])

    def test_usage_with_bad_key_is_401(self):
        response = self.client.get(
            "/api/v1/usage/", HTTP_AUTHORIZATION="Bearer vlf_nonexistent"
        )
        self.assertEqual(response.status_code, 401)

    def test_usage_scoped_to_key_workspace(self):
        other_user = User.objects.create_user(username="other", password="pw12345678")
        other_ws = get_or_create_workspace(other_user)
        _, other_raw = APIKey.create_key(other_ws, "other-key")
        self._upload()  # uploads into self.workspace only
        body = self.client.get(
            "/api/v1/usage/", HTTP_AUTHORIZATION=f"Bearer {other_raw}"
        ).json()
        self.assertEqual(body["uploads_used"], 0)
        self.assertEqual(body["storage_used_bytes"], 0)

    # -- error envelope ----------------------------------------------------

    def test_auth_failure_uses_error_envelope(self):
        response = self.client.get(
            "/api/v1/usage/", HTTP_AUTHORIZATION="Bearer vlf_bad"
        )
        self.assertEqual(response.status_code, 401)
        body = response.json()
        self.assertEqual(body, {
                "error": "The API key is not valid, or the connected app's access has ended. "
                "Send a current key, or reconnect the app.",
                "code": "unauthorized",
            })

    def test_forbidden_uses_error_envelope(self):
        # A forbidden (not merely unauthenticated) response still uses the
        # error envelope. API-key holders cannot mint keys: only the
        # dashboard session can (ctr-14: say so, not "verify your email").
        response = self.client.post(
            "/api/v1/keys/",
            {"name": "sneaky"},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body["code"], "session_required")
        self.assertNotIn("detail", body)

    def test_throttled_uses_error_envelope_and_keeps_retry_after(self):
        from rest_framework.exceptions import Throttled

        from drop_core.errors import veilfile_exception_handler

        response = veilfile_exception_handler(Throttled(wait=60), {})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "60")
        body = response.data
        self.assertEqual(body["code"], "rate_limited")
        self.assertNotIn("detail", body)

    def test_view_raised_errors_pass_through_untouched(self):
        response = self.client.delete(
            "/api/v1/artifacts/00000000-0000-0000-0000-000000000000/",
            **self.auth,
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"error": "Artifact not found.", "code": "not_found"},
        )

    def test_usage_endpoint(self):
        self.client.force_login(self.user)
        self._upload()
        response = self.client.get("/api/v1/usage/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "plan": "free",
                "uploads_used": 1,
                "uploads_cap": 100,
                "ttl_days": 7,
                "keys_used": 1,
                "keys_cap": 1,
                "storage_used_bytes": len(b"hello veilfile"),
                "storage_cap_bytes": PLANS["free"]["storage_gb"] * 1024**3,
                "email": "",
                "email_verified": True,
            },
        )

    # -- sweeper ---------------------------------------------------------------

    def test_sweeper_deletes_expired_only(self):
        expired = upload_artifact(self.workspace, self.key, "old.txt", b"old", "text/plain")
        fresh = upload_artifact(self.workspace, self.key, "new.txt", b"new", "text/plain")
        Artifact.objects.filter(id=expired.id).update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        out = io.StringIO()
        call_command("sweep_expired", stdout=out)
        self.assertIn("swept=1", out.getvalue())
        self.assertIn(f"bytes={len(b'old')}", out.getvalue())
        self.assertFalse(Artifact.objects.filter(id=expired.id).exists())
        self.assertFalse(get_storage().exists(expired.token))
        self.assertTrue(Artifact.objects.filter(id=fresh.id).exists())
        self.assertTrue(get_storage().exists(fresh.token))
        # idempotent: second run sweeps nothing
        out2 = io.StringIO()
        call_command("sweep_expired", stdout=out2)
        self.assertIn("swept=0", out2.getvalue())

    def test_sweeper_keeps_row_when_storage_delete_fails(self):
        # A storage failure must NOT delete the DB row (that would orphan
        # the file in storage). The next successful run retries and sweeps it.
        from unittest import mock

        expired = upload_artifact(self.workspace, self.key, "old.txt", b"old", "text/plain")
        Artifact.objects.filter(id=expired.id).update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        from django.core.management.base import CommandError

        failing_storage = mock.Mock()
        failing_storage.delete.side_effect = OSError("r2 down")
        with mock.patch("drop_core.storage.get_storage", return_value=failing_storage):
            out = io.StringIO()
            # Non-zero exit so cron/agents notice the failed deletes.
            with self.assertRaises(CommandError):
                call_command("sweep_expired", stdout=out)
        self.assertIn("swept=0", out.getvalue())
        self.assertIn("failed=1", out.getvalue())
        self.assertTrue(Artifact.objects.filter(id=expired.id).exists())
        # next run succeeds and sweeps it
        out2 = io.StringIO()
        call_command("sweep_expired", stdout=out2)
        self.assertIn("swept=1", out2.getvalue())
        self.assertFalse(Artifact.objects.filter(id=expired.id).exists())

    # -- signup ------------------------------------------------------------------

    def test_signup_creates_user_workspace_and_logs_in(self):
        response = self.client.post(
            "/accounts/signup/",
            {
                "username": "newdev",
                "password1": "s3cur3pass!!",
                "password2": "s3cur3pass!!",
                "email": "newdev@example.com",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/app/")
        user = User.objects.get(username="newdev")
        self.assertTrue(
            user.workspaces.filter(plan="free").exists()
        )
        self.assertIn("_auth_user_id", self.client.session)

    def test_signup_page_renders(self):
        self.assertEqual(self.client.get("/accounts/signup/").status_code, 200)

    def test_login_page_renders(self):
        self.assertEqual(self.client.get("/accounts/login/").status_code, 200)

    # -- model helpers -----------------------------------------------------------

    def test_api_key_verify_fast_path(self):
        self.assertIsNone(APIKey.verify("not-a-key"))
        self.assertIsNone(APIKey.verify(None))
        self.assertIsNone(APIKey.verify("vlf_" + "z" * 32))
        self.assertEqual(APIKey.verify(self.raw_key).id, self.key.id)


class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="resetuser", email="r@example.com", password="pw12345678"
        )

    def _request_reset(self, email):
        return self.client.post("/accounts/password_reset/", {"email": email})

    def _reset_url(self):
        from django.core import mail

        self.assertEqual(len(mail.outbox), 1)
        match = re.search(r"https?://\S*(/accounts/reset/\S+)", mail.outbox[0].body)
        self.assertIsNotNone(match, "reset email has no reset link")
        return match.group(1)

    def test_reset_form_renders(self):
        response = self.client.get("/accounts/password_reset/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reset password")

    def test_reset_sends_email_for_known_user(self):
        response = self._request_reset("r@example.com")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].endswith("/accounts/password_reset/done/"))
        url = self._reset_url()
        self.assertIn("/accounts/reset/", url)

    def test_reset_unknown_email_does_not_enumerate(self):
        from django.core import mail

        response = self._request_reset("nobody@example.com")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

    def test_reset_confirm_flow_sets_new_password(self):
        self._request_reset("r@example.com")
        url = self._reset_url()
        # GET follows Django's session-token redirect; the form then renders.
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a new password")
        response = self.client.post(
            response.redirect_chain[-1][0],
            {"new_password1": "newpass12345678", "new_password2": "newpass12345678"},
            follow=True,
        )
        self.assertTrue(response.redirect_chain[-1][0].endswith("/accounts/reset/done/"))
        self.assertTrue(
            self.client.login(username="resetuser", password="newpass12345678")
        )

    def test_reset_confirm_invalid_token(self):
        response = self.client.get("/accounts/reset/NA/bogus-token/", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "invalid or has expired")


class BlogTests(TestCase):
    def test_index_lists_post(self):
        response = self.client.get("/blog/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Introducing Veilfile")

    def test_post_renders_markdown(self):
        response = self.client.get("/blog/introducing-veilfile/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Agents stash files constantly.")
        # markdown paragraphs become <p> tags
        self.assertContains(response, "<p>Agents stash files constantly.")

    def test_old_renaming_url_redirects(self):
        response = self.client.get("/blog/renaming-deadrop-to-veilfile/")
        self.assertEqual(response.status_code, 301)
        self.assertTrue(response["Location"].endswith("/blog/introducing-veilfile/"))

    def test_no_deadrop_references_in_blog(self):
        from blog import loader

        for post in loader.load_posts():
            haystack = (post.title + post.description + post.body_md).lower()
            self.assertNotIn("deadrop", haystack)
            self.assertNotIn("dead drop", haystack)

    def test_unknown_slug_404(self):
        self.assertEqual(self.client.get("/blog/nope/").status_code, 404)

    def test_feed_is_valid_rss(self):
        from xml.etree import ElementTree

        response = self.client.get("/blog/feed.xml")
        self.assertEqual(response.status_code, 200)
        self.assertIn("rss", response["Content-Type"])
        channel = ElementTree.fromstring(response.content).find("channel")
        self.assertEqual(channel.findtext("title"), "Veilfile Journal")
        self.assertContains(response, "Introducing Veilfile")
        self.assertContains(response, "https://veilfile.com/blog/introducing-veilfile/")
        self.assertNotContains(response, "renaming-deadrop-to-veilfile")

    def test_future_posts_hidden(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from blog import loader

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "future.md").write_text(
                "---\ntitle: Future\nslug: future\ndate: 2999-01-01\ndescription: x\n---\n\nBody.\n"
            )
            with mock.patch.object(loader, "POSTS_DIR", Path(tmp)):
                slugs = {p.slug for p in loader.load_posts()}
        self.assertNotIn("future", slugs)


class DocsTests(TestCase):
    DOCS_SLUGS = ["quickstart", "agent", "how-it-works", "api"]

    def test_index_renders(self):
        response = self.client.get("/docs/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "If you're new here, start with the")
        self.assertContains(response, "/docs/quickstart/")

    def test_index_slug_renders(self):
        self.assertEqual(self.client.get("/docs/index/").status_code, 200)

    def test_every_page_returns_200(self):
        for slug in self.DOCS_SLUGS:
            with self.subTest(slug=slug):
                response = self.client.get(f"/docs/{slug}/")
                self.assertEqual(response.status_code, 200)

    def test_unknown_slug_404(self):
        self.assertEqual(self.client.get("/docs/nope/").status_code, 404)

    def test_docs_index_lists_pages(self):
        # Pages have no nav of their own now: the index lists each one with its description, and each page ends
        # with links to its neighbours (pages/tests_docs_reading.py).
        response = self.client.get("/docs/")
        for slug in self.DOCS_SLUGS:
            self.assertContains(response, f'href="/docs/{slug}/"')

    def test_docs_voice_rules(self):
        from docs import loader

        banned = [
            "genuinely",
            "utilize",
            "innovative",
            "innovation",
            "passionate about",
            "excited to share",
            "powerful force",
            "leverage",
        ]
        pages = loader.load_pages()
        self.assertEqual(
            {p.slug for p in pages},
            {"index"} | set(self.DOCS_SLUGS),
        )
        for page in pages:
            haystack = page.title + "\n" + page.description + "\n" + page.body_md
            with self.subTest(slug=page.slug):
                self.assertNotIn("\u2014", haystack)  # no em dashes, ever
                lowered = haystack.lower()
                for word in banned:
                    self.assertNotIn(word, lowered)


class LegalTests(TestCase):
    def test_terms_page(self):
        response = self.client.get("/terms/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Terms of Service")
        self.assertContains(response, "support@veilfile.com")
        self.assertContains(response, "https://veilfile.com")
        self.assertContains(response, "Commonwealth of Pennsylvania")

    def test_privacy_page(self):
        response = self.client.get("/privacy/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Privacy Policy")
        self.assertContains(response, "support@veilfile.com")
        self.assertContains(response, "Cloudflare R2")

    def test_legal_pages_have_no_draft_banner(self):
        for url in ("/terms/", "/privacy/"):
            response = self.client.get(url)
            self.assertNotContains(response, "DRAFT")
            self.assertNotContains(response, "onrender.com")

    def test_report_abuse_page(self):
        response = self.client.get("/report-abuse/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Report abuse")
        self.assertContains(response, "abuse@veilfile.com")
        self.assertContains(
            response, "National Center for Missing and Exploited Children"
        )

    def test_report_abuse_is_linked_from_every_legal_page(self):
        # The legal pages have no footer links: the menu's third group carries Terms, Privacy and
        # Report abuse (spec 4.4), so each document is one step from the abuse address.
        for url in ("/terms/", "/privacy/", "/report-abuse/"):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                menu = html[html.index('<nav class="menu"'):]
                menu = menu[:menu.index("</nav>")]
                self.assertIn('href="/report-abuse/"', menu)


class SignupPlanTests(TestCase):
    """Plan-aware signup: landing plan CTAs carry ?plan= through signup."""

    def setUp(self):
        from drop_billing.tests import TEST_ENV

        self.env = mock.patch.dict(os.environ, TEST_ENV, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def _stripe_mock(self):
        from drop_billing.tests import make_stripe_mock

        return make_stripe_mock()

    def _signup(self, plan=None, **overrides):
        url = "/accounts/signup/"
        if plan:
            url += f"?plan={plan}"
        data = {
            "username": "newdev",
            "password1": "s3cure-pass-123",
            "password2": "s3cure-pass-123",
            "email": "newdev@example.com",
        }
        data.update(overrides)
        return self.client.post(url, data)

    def test_plan_team_shows_blurb(self):
        response = self.client.get("/accounts/signup/?plan=team")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Team plan")

    def test_plan_scale_shows_blurb(self):
        response = self.client.get("/accounts/signup/?plan=scale")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Scale plan")

    def test_invalid_plan_falls_back_to_free(self):
        response = self.client.get("/accounts/signup/?plan=bogus")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Team plan")
        self.assertNotContains(response, "Scale plan")

    def test_free_signup_goes_to_dashboard(self):
        response = self._signup(plan="free")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/app/")

    def test_paid_signup_redirects_to_checkout(self):
        with mock.patch(
            "drop_billing.views.stripe", self._stripe_mock()
        ):
            response = self._signup(plan="team")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith("https://checkout.stripe.com/")
        )

    def test_paid_signup_falls_back_to_dashboard_when_billing_down(self):
        # The failure is logged and flagged so the dashboard can explain it.
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": ""}):
            with self.assertLogs("drop_billing", level="ERROR"):
                response = self._signup(plan="team")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/app/?billing=error&plan=team")

    def test_authenticated_user_with_paid_plan_skips_form(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(username="dev", password="x")
        self.client.force_login(user)
        stripe_mock = self._stripe_mock()
        with mock.patch("drop_billing.views.stripe", stripe_mock):
            # A GET only confirms; it never creates a checkout session.
            response = self.client.get("/accounts/signup/?plan=scale")
            self.assertEqual(response.status_code, 200)
            stripe_mock.checkout.Session.create.assert_not_called()
            response = self.client.post("/accounts/signup/?plan=scale")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith("https://checkout.stripe.com/")
        )


class TrailingSlashTests(TestCase):
    """Agent-facing endpoints accept URLs with or without trailing slash."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        _use_local_dir(self, self.tmpdir)
        self.user = User.objects.create_user(username="agent", password="pw12345678")
        self.workspace = get_or_create_workspace(self.user)
        self.key, self.raw_key = APIKey.create_key(self.workspace, "agent-key")
        self.client = APIClient()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.raw_key}"}

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _upload(self, url):
        file = SimpleUploadedFile("note.txt", b"hello veilfile", content_type="text/plain")
        return self.client.post(url, {"file": file}, **self.auth)

    def test_upload_without_trailing_slash(self):
        response = self._upload("/api/v1/artifacts")
        self.assertEqual(response.status_code, 201)

    def test_upload_with_trailing_slash(self):
        response = self._upload("/api/v1/artifacts/")
        self.assertEqual(response.status_code, 201)

    def test_list_without_trailing_slash(self):
        response = self.client.get("/api/v1/artifacts", **self.auth)
        self.assertEqual(response.status_code, 200)

    def test_delete_without_trailing_slash(self):
        created = self._upload("/api/v1/artifacts/")
        artifact_id = created.json()["id"]
        response = self.client.delete(
            f"/api/v1/artifacts/{artifact_id}", **self.auth
        )
        self.assertEqual(response.status_code, 204)


class SafetyTests(TestCase):
    """Phase 0 safety seam: magic-byte verification, blocked-hash table,
    safety modes, held-content serving refusal."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        _use_local_dir(self, self.tmpdir)
        self.user = User.objects.create_user(username="safety", password="pw12345678")
        self.workspace = get_or_create_workspace(self.user)
        self.key, self.raw_key = APIKey.create_key(self.workspace, "agent-key")
        self.client = APIClient()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.raw_key}"}

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _upload(self, name, content, **kwargs):
        file = SimpleUploadedFile(name, content, content_type="text/plain")
        data = {"file": file}
        data.update(kwargs)
        return self.client.post("/api/v1/artifacts/", data, **self.auth)

    # -- magic-byte verification -------------------------------------------

    def test_png_extension_with_exe_bytes_rejected(self):
        response = self._upload("evil.png", b"MZ\x90\x00" + b"\x00" * 64)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "file_type_not_allowed")

    def test_real_png_bytes_accepted(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        response = self._upload("shot.png", png)
        self.assertEqual(response.status_code, 201)

    def test_jpeg_bytes_named_png_accepted(self):
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
        response = self._upload("photo.png", jpeg)
        self.assertEqual(response.status_code, 201)

    def test_binary_bytes_as_txt_rejected(self):
        response = self._upload("notes.txt", b"\x00\x01\x02\xff\xfe" * 32)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "file_type_not_allowed")

    def test_utf8_text_accepted(self):
        response = self._upload("notes.txt", "héllo veilfile\n".encode("utf-8"))
        self.assertEqual(response.status_code, 201)

    def test_pdf_without_pdf_magic_rejected(self):
        response = self._upload("doc.pdf", b"not a pdf at all" * 8)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "file_type_not_allowed")

    # -- safety modes and blocked hashes ------------------------------------

    def _seed_blocked(self, content):
        import hashlib

        from drop_core.models import BlockedHash

        digest = hashlib.sha256(content).hexdigest()
        BlockedHash.objects.create(sha256=digest, reason="test seed")
        return digest

    def test_off_mode_is_inert(self):
        from drop_core import safety

        with mock.patch.dict(os.environ, {"VEILFILE_SAFETY_MODE": "off"}):
            verdict = safety.screen_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "image/png")
        self.assertEqual(verdict.action, "allow")

    def test_log_mode_records_event_but_upload_succeeds(self):
        from drop_core.models import Artifact, SafetyEvent

        bad = b"\x89PNG\r\n\x1a\n" + b"known-bad-bytes"
        self._seed_blocked(bad)
        with mock.patch.dict(os.environ, {"VEILFILE_SAFETY_MODE": "log"}):
            response = self._upload("shot.png", bad)
        self.assertEqual(response.status_code, 201)
        event = SafetyEvent.objects.get()
        self.assertEqual(event.signal, "blocked_hash_match")
        self.assertEqual(event.action_taken, "logged")
        # metadata only: no file bytes stored anywhere on the event
        for field in SafetyEvent._meta.get_fields():
            if field.name == "id":
                continue
            value = getattr(event, field.name)
            self.assertNotIsInstance(value, bytes, field.name)

    def test_enforce_mode_blocks_known_hash(self):
        from drop_core.models import Artifact, SafetyEvent, UsageCounter

        bad = b"\x89PNG\r\n\x1a\n" + b"known-bad-bytes"
        self._seed_blocked(bad)
        with mock.patch.dict(os.environ, {"VEILFILE_SAFETY_MODE": "enforce"}):
            response = self._upload("shot.png", bad)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "content_not_allowed")
        # refused bytes never persist: no row, no quota consumed
        self.assertEqual(Artifact.objects.count(), 0)
        self.assertEqual(
            UsageCounter.objects.get(
                workspace=self.workspace, year_month=_month()
            ).uploads,
            0,
        )
        # key suspended, event recorded
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)
        event = SafetyEvent.objects.get()
        self.assertEqual(event.action_taken, "blocked")

    def test_enforce_mode_clean_upload_unaffected(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        with mock.patch.dict(os.environ, {"VEILFILE_SAFETY_MODE": "enforce"}):
            response = self._upload("shot.png", png)
        self.assertEqual(response.status_code, 201)
        from drop_core.models import Artifact

        artifact = Artifact.objects.get()
        self.assertEqual(artifact.safety_status, "clear")
        self.assertEqual(len(artifact.content_sha256), 64)

    # -- held content ---------------------------------------------------------

    def test_held_artifact_returns_404(self):
        from drop_core.models import Artifact

        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        artifact_id = self._upload("shot.png", png).json()["id"]
        artifact = Artifact.objects.get(id=artifact_id)
        artifact.safety_status = "held"
        artifact.save(update_fields=["safety_status"])
        response = self.client.get(f"/a/{artifact.token}")
        self.assertEqual(response.status_code, 404)


class FunnelTests(TestCase):
    """First-party funnel instrumentation: no PII, no third parties."""

    # Anonymous stages only count real browsers (bots and UA-less
    # scripts are skipped), so the anonymous-stage tests send one.
    BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) Firefox/143.0"

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-funnel-")
        _use_local_dir(self, self.tmpdir)
        self.user = User.objects.create_user(username="alice", password="pw12345678")
        self.workspace = get_or_create_workspace(self.user)
        self.key, self.raw_key = APIKey.create_key(self.workspace, "agent-key")
        self.client = APIClient()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.raw_key}"}
        # Key minting requires a verified email; verify the funnel user so
        # key-issuance funnel stages exercise the happy path.
        from drop_core.models import EmailVerification

        EmailVerification.objects.create(
            user=self.user,
            email="alice@example.com",
            is_verified=True,
            verified_at=timezone.now(),
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _upload(self, name="note.txt", content=b"hello veilfile"):
        file = SimpleUploadedFile(name, content, content_type="text/plain")
        return self.client.post("/api/v1/artifacts/", {"file": file}, **self.auth)

    # -- anonymous stages ----------------------------------------------------

    def test_landing_visit_recorded_once_per_session(self):
        from django.test import Client

        c = Client(HTTP_USER_AGENT=self.BROWSER_UA)
        self.assertEqual(c.get("/").status_code, 200)
        c.get("/")
        c.get("/")
        self.assertEqual(FunnelEvent.objects.filter(stage="landing_visit").count(), 1)
        # A new browser session counts as a new visit.
        Client(HTTP_USER_AGENT=self.BROWSER_UA).get("/")
        self.assertEqual(FunnelEvent.objects.filter(stage="landing_visit").count(), 2)

    def test_landing_event_holds_no_pii(self):
        from django.test import Client

        c = Client(HTTP_USER_AGENT=self.BROWSER_UA)
        c.get("/")
        event = FunnelEvent.objects.get(stage="landing_visit")
        # Anonymous id is a random hex token, not an IP or user agent.
        self.assertRegex(event.anonymous_id, r"^[0-9a-f]{32}$")
        self.assertIsNone(event.workspace)
        self.assertIsNone(event.upload_number)
        field_names = {f.name for f in FunnelEvent._meta.get_fields()}
        self.assertTrue(
            field_names.isdisjoint(
                {"ip_address", "uploader_ip", "user_agent", "username", "email"}
            )
        )

    def test_signup_started_recorded_once_per_session(self):
        from django.test import Client

        c = Client(HTTP_USER_AGENT=self.BROWSER_UA)
        self.assertEqual(c.get("/accounts/signup/").status_code, 200)
        c.get("/accounts/signup/")
        self.assertEqual(FunnelEvent.objects.filter(stage="signup_started").count(), 1)

    def test_signup_completed_records_workspace_event(self):
        from django.test import Client

        c = Client(HTTP_USER_AGENT=self.BROWSER_UA)
        c.get("/accounts/signup/")  # establishes the anonymous funnel id
        response = c.post(
            "/accounts/signup/",
            {
                "username": "newdev",
                "password1": "pw12345678",
                "password2": "pw12345678",
                "email": "newdev@example.com",
            },
        )
        self.assertEqual(response.status_code, 302)
        new_user = User.objects.get(username="newdev")
        event = FunnelEvent.objects.get(stage="signup_completed")
        self.assertEqual(event.workspace.owner, new_user)
        # The anonymous id survives login, linking pre/post signup stages.
        started = FunnelEvent.objects.get(stage="signup_started")
        self.assertEqual(event.anonymous_id, started.anonymous_id)

    # -- authenticated stages --------------------------------------------------

    def test_key_issued_records_event(self):
        self.workspace.plan = "team"
        self.workspace.save()
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/v1/keys/", {"name": "ci"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 201)
        event = FunnelEvent.objects.get(stage="key_issued")
        self.assertEqual(event.workspace, self.workspace)

    def test_uploads_record_first_and_second_numbers(self):
        self.assertEqual(self._upload().status_code, 201)
        self.assertEqual(self._upload(name="two.txt").status_code, 201)
        numbers = list(
            FunnelEvent.objects.filter(stage="upload_completed")
            .order_by("created_at")
            .values_list("upload_number", flat=True)
        )
        self.assertEqual(numbers, [1, 2])
        events = FunnelEvent.objects.filter(stage="upload_completed")
        self.assertTrue(all(e.workspace == self.workspace for e in events))

    def test_mcp_upload_records_event(self):
        import base64

        from django.test import Client

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "upload_artifact",
                "arguments": {
                    "filename": "note.txt",
                    "content_base64": base64.b64encode(b"hello").decode("ascii"),
                    "mime_type": "text/plain",
                },
            },
        }
        response = Client().post(
            "/mcp",
            data=payload,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_key}",
        )
        self.assertEqual(response.status_code, 200)
        event = FunnelEvent.objects.get(stage="upload_completed")
        self.assertEqual(event.workspace, self.workspace)
        self.assertEqual(event.upload_number, 1)

    # -- summary -----------------------------------------------------------------

    def test_funnel_summary_counts_distinct_entities(self):
        from drop_core import funnel as funnel_mod

        summary = {row["stage"]: row for row in funnel_mod.funnel_summary()}
        self.assertEqual(
            [r["stage"] for r in funnel_mod.funnel_summary()],
            [
                "landing_visit",
                "signup_started",
                "signup_completed",
                "key_issued",
                "first_upload",
                "second_upload",
            ],
        )
        # Empty database: all zeros, no division errors.
        self.assertTrue(all(row["count"] == 0 for row in summary.values()))

        # Two uploads by one workspace count as one first_upload, one second_upload.
        self._upload()
        self._upload(name="two.txt")
        summary = {row["stage"]: row for row in funnel_mod.funnel_summary()}
        self.assertEqual(summary["first_upload"]["count"], 1)
        self.assertEqual(summary["second_upload"]["count"], 1)


class EmailVerificationTests(TestCase):
    """Verified email required before API-key issuance.

    Existing keys keep working (agent use unchanged); only minting new
    keys is gated. Resends and signups are throttled (email-bombing
    protection).
    """

    def setUp(self):
        from django.test import Client

        self.client = Client()
        self.user = User.objects.create_user(
            username="unverified", email="unverified@example.com", password="pw12345678"
        )
        self.workspace = get_or_create_workspace(self.user)

    def _signup(self, **overrides):
        data = {
            "username": "newdev",
            "password1": "s3cur3pass!!",
            "password2": "s3cur3pass!!",
            "email": "newdev@example.com",
        }
        data.update(overrides)
        return self.client.post("/accounts/signup/", data)

    def _verify_token_from_email(self, index=0):
        from django.core import mail

        self.assertTrue(len(mail.outbox) > index)
        match = re.search(
            r"https?://\S*(/accounts/verify-email/\S+)", mail.outbox[index].body
        )
        self.assertIsNotNone(match, "verification email has no verify link")
        return match.group(1).rstrip("/").rsplit("/", 1)[-1]

    def _mark_verified(self, user):
        from drop_core.models import EmailVerification

        record, _ = EmailVerification.objects.get_or_create(
            user=user, defaults={"email": user.email or "x@example.com"}
        )
        record.is_verified = True
        record.verified_at = timezone.now()
        record.save()
        return record

    # -- signup ------------------------------------------------------------

    def test_signup_requires_email(self):
        data = {
            "username": "nodev",
            "password1": "s3cur3pass!!",
            "password2": "s3cur3pass!!",
        }
        response = self.client.post("/accounts/signup/", data)
        self.assertEqual(response.status_code, 200)  # form re-rendered
        self.assertFalse(User.objects.filter(username="nodev").exists())

    def test_signup_rejects_bad_email(self):
        response = self._signup(email="not-an-email")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="newdev").exists())

    def test_signup_rejects_duplicate_email(self):
        User.objects.create_user(
            username="first", email="taken@example.com", password="pw12345678"
        )
        response = self._signup(email="TAKEN@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="newdev").exists())

    def test_signup_sends_verification_email_and_starts_unverified(self):
        from django.core import mail
        from drop_core.models import EmailVerification

        response = self._signup()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/app/")
        user = User.objects.get(username="newdev")
        self.assertEqual(user.email, "newdev@example.com")
        record = EmailVerification.objects.get(user=user)
        self.assertFalse(record.is_verified)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("newdev@example.com", mail.outbox[0].to)
        self.assertIn("/accounts/verify-email/", mail.outbox[0].body)

    def test_signup_page_shows_email_field(self):
        response = self.client.get("/accounts/signup/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="email"')

    # -- verification flow ---------------------------------------------------

    def test_verify_link_marks_verified(self):
        from drop_core.models import EmailVerification

        self._signup()
        user = User.objects.get(username="newdev")
        token = self._verify_token_from_email()
        response = self.client.get(f"/accounts/verify-email/{token}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "verified")
        record = EmailVerification.objects.get(user=user)
        self.assertTrue(record.is_verified)
        self.assertIsNotNone(record.verified_at)

    def test_verify_invalid_token_rejected(self):
        from drop_core.models import EmailVerification

        self._signup()
        user = User.objects.get(username="newdev")
        response = self.client.get("/accounts/verify-email/bogus-token/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "invalid or has expired")
        self.assertFalse(EmailVerification.objects.get(user=user).is_verified)

    def test_verify_expired_token_rejected(self):
        from drop_core.models import EmailVerification

        self._signup()
        user = User.objects.get(username="newdev")
        token = self._verify_token_from_email()
        with mock.patch("drop_core.email_verification.TOKEN_MAX_AGE", -1):
            response = self.client.get(f"/accounts/verify-email/{token}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "invalid or has expired")
        self.assertFalse(EmailVerification.objects.get(user=user).is_verified)

    def test_verify_token_for_changed_email_rejected(self):
        from drop_core.models import EmailVerification

        self._signup()
        user = User.objects.get(username="newdev")
        token = self._verify_token_from_email()
        # User changes address before clicking: old token must not verify.
        record = EmailVerification.objects.get(user=user)
        record.email = "other@example.com"
        record.save()
        response = self.client.get(f"/accounts/verify-email/{token}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "invalid or has expired")
        record.refresh_from_db()
        self.assertFalse(record.is_verified)

    # -- key issuance gating ---------------------------------------------------

    def test_key_create_blocked_until_verified(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/v1/keys/", {"name": "agent"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "email_verification_required")
        self.assertEqual(self.workspace.api_keys.count(), 0)

    def test_key_create_allowed_after_verification(self):
        self._mark_verified(self.user)
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/v1/keys/", {"name": "agent"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["api_key"].startswith("vlf_"))

    def test_existing_keys_keep_working_when_unverified(self):
        # Grandfathering: agent API-key use is unchanged for pre-existing keys.
        key, raw = APIKey.create_key(self.workspace, "old-key")
        response = self.client.post(
            "/api/v1/artifacts/",
            {"file": SimpleUploadedFile("a.txt", b"hello")},
            HTTP_AUTHORIZATION=f"Bearer {raw}",
        )
        self.assertEqual(response.status_code, 201)

    def test_changing_email_resets_verification(self):
        from drop_core.models import EmailVerification

        self._mark_verified(self.user)
        self.client.force_login(self.user)
        response = self.client.post(
            "/accounts/verify-email/",
            {
                "action": "update_email",
                "email": "newaddr@example.com",
                "password": "pw12345678",
            },
        )
        self.assertEqual(response.status_code, 302)
        record = EmailVerification.objects.get(user=self.user)
        self.assertFalse(record.is_verified)
        self.assertEqual(record.email, "newaddr@example.com")

    # -- existing users without email ------------------------------------------

    def test_legacy_user_without_email_can_add_address(self):
        from django.core import mail
        from drop_core.models import EmailVerification

        legacy = User.objects.create_user(username="legacy", password="pw12345678")
        self.assertEqual(legacy.email, "")
        self.client.force_login(legacy)
        response = self.client.post(
            "/accounts/verify-email/",
            {
                "action": "update_email",
                "email": "legacy@example.com",
                "password": "pw12345678",
            },
        )
        self.assertEqual(response.status_code, 302)
        record = EmailVerification.objects.get(user=legacy)
        self.assertEqual(record.email, "legacy@example.com")
        self.assertFalse(record.is_verified)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("legacy@example.com", mail.outbox[0].to)

    def test_add_email_rejects_address_taken_by_another_user(self):
        from drop_core.models import EmailVerification

        other = User.objects.create_user(
            username="other", email="other@example.com", password="pw12345678"
        )
        self.client.force_login(self.user)
        response = self.client.post(
            "/accounts/verify-email/",
            {"action": "update_email", "email": "other@example.com"},
        )
        self.assertEqual(response.status_code, 200)  # form error, no redirect
        self.assertFalse(
            EmailVerification.objects.filter(user=self.user).exists()
        )

    def test_manage_page_requires_login(self):
        response = self.client.get("/accounts/verify-email/")
        self.assertEqual(response.status_code, 302)

    # -- resend throttling -------------------------------------------------------

    def test_resend_second_immediate_attempt_throttled(self):
        from drop_core.models import EmailVerification

        EmailVerification.objects.create(
            user=self.user, email="unverified@example.com"
        )
        self.client.force_login(self.user)
        first = self.client.post("/accounts/verify-email/", {"action": "resend"})
        self.assertEqual(first.status_code, 302)
        second = self.client.post("/accounts/verify-email/", {"action": "resend"})
        self.assertEqual(second.status_code, 429)

    def test_resend_per_account_hourly_limit(self):
        from drop_core.models import EmailSendLog

        self.client.force_login(self.user)
        for _ in range(5):
            EmailSendLog.objects.create(
                email="unverified@example.com",
                user=self.user,
                purpose=EmailSendLog.PURPOSE_VERIFICATION_SENT,
                created_at=timezone.now() - timedelta(minutes=70),
            )
        # Backdate last_sent_at so the 60s interval check passes; the
        # hourly count (5) must still block.
        from drop_core.models import EmailVerification

        record, _ = EmailVerification.objects.get_or_create(
            user=self.user, defaults={"email": "unverified@example.com"}
        )
        record.last_sent_at = timezone.now() - timedelta(minutes=61)
        record.save()
        for _ in range(5):
            EmailSendLog.objects.create(
                email="unverified@example.com",
                user=self.user,
                purpose=EmailSendLog.PURPOSE_VERIFICATION_SENT,
            )
        response = self.client.post("/accounts/verify-email/", {"action": "resend"})
        self.assertEqual(response.status_code, 429)

    def test_resend_per_address_daily_limit(self):
        from drop_core.models import EmailSendLog, EmailVerification

        for _ in range(5):
            EmailSendLog.objects.create(
                email="unverified@example.com",
                purpose=EmailSendLog.PURPOSE_VERIFICATION_SENT,
            )
        record, _ = EmailVerification.objects.get_or_create(
            user=self.user, defaults={"email": "unverified@example.com"}
        )
        record.last_sent_at = timezone.now() - timedelta(hours=2)
        record.save()
        self.client.force_login(self.user)
        response = self.client.post("/accounts/verify-email/", {"action": "resend"})
        self.assertEqual(response.status_code, 429)

    def test_resend_never_sends_to_unowned_address(self):
        # Resend always targets the address on the user's own record.
        from django.core import mail
        from drop_core.models import EmailVerification

        self.client.force_login(self.user)
        self.client.post(
            "/accounts/verify-email/",
            {
                "action": "update_email",
                "email": "mine@example.com",
                "password": "pw12345678",
            },
        )
        mail.outbox.clear()
        # Move past the 60s resend interval so only the address check matters.
        record = EmailVerification.objects.get(user=self.user)
        record.last_sent_at = timezone.now() - timedelta(minutes=5)
        record.save(update_fields=["last_sent_at"])
        self.client.post("/accounts/verify-email/", {"action": "resend"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["mine@example.com"])

    # -- signup throttling ---------------------------------------------------------

    def test_signup_rate_limited_per_ip(self):
        for i in range(10):
            response = self.client.post(
                "/accounts/signup/",
                {
                    "username": f"u{i}",
                    "password1": "s3cur3pass!!",
                    "password2": "s3cur3pass!!",
                    "email": f"u{i}@example.com",
                },
                REMOTE_ADDR="9.9.9.9",
            )
            self.assertIn(response.status_code, (200, 302))
        response = self.client.post(
            "/accounts/signup/",
            {
                "username": "u10",
                "password1": "s3cur3pass!!",
                "password2": "s3cur3pass!!",
                "email": "u10@example.com",
            },
            REMOTE_ADDR="9.9.9.9",
        )
        self.assertEqual(response.status_code, 429)
        self.assertFalse(User.objects.filter(username="u10").exists())

    # -- usage surface ---------------------------------------------------------------

    def test_usage_reports_verification_state(self):
        self.client.force_login(self.user)
        body = self.client.get("/api/v1/usage/").json()
        self.assertIn("email_verified", body)
        self.assertFalse(body["email_verified"])
        self._mark_verified(self.user)
        body = self.client.get("/api/v1/usage/").json()
        self.assertTrue(body["email_verified"])


class AccessibilityTests(TestCase):
    """WCAG 2.2 AA remediation: keyboard access, names/roles/values, focus.

    Server-renderable assertions for the October 2026 accessibility audit
    fixes. Behavior that needs a script (the quickstart's tabs pattern) is
    asserted against the shipped source, since there is no JS test harness.
    The audit's findings on the dashboard are held by the dashboard's own tests.
    """

    # -- skip link + landmarks (B1) ------------------------------------------------

    def test_skip_link_on_verify_email_page(self):
        # The skip link and the main it targets, in the shell's markup. /accounts/verify-email/ requires a session; check it
        # logged in. The shell's landmarks on every state of this page are also held by drop_core/tests_reset_verify_pages.py.
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            username="a11y", password="pw12345678"
        )
        self.client.force_login(user)
        response = self.client.get("/accounts/verify-email/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<a class="skip-link label" href="#main">')
        self.assertContains(response, '<main id="main" class="shell-main">')

    # -- visible focus indicator (B6) ----------------------------------------------

    def test_shell_stylesheet_has_the_skip_link_and_reduced_motion_rules(self):
        # The dashboard is on the shell, so the rules its own stylesheet carried are base.css's. That the focus outline is drawn, and that no
        # stylesheet takes it away, is held for every stylesheet by pages.tests_site_rules.FocusRingTests.
        from pathlib import Path

        css = (Path(__file__).resolve().parent.parent / "static" / "css" / "base.css").read_text()
        self.assertIn(".skip-link {", css)
        self.assertIn(".skip-link:focus-visible", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)

    # -- shell tabs keyboard pattern (B2) ------------------------------------------
    # The shell switcher moved from the React landing to the docs quickstart
    # (approved 2026-10-06 redesign). The APG tabs markup ships in the docs
    # markdown; the keyboard behavior ships in static/js/behaviors/tabs.js,
    # which the markup's data-tabs hands the switcher to. Both are asserted
    # against the shipped source, since there is no JS test harness.

    def test_env_switcher_apg_tabs_pattern(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent.parent
        md = (root / "backend" / "docs" / "pages" / "quickstart.md").read_text()
        self.assertIn('role="tablist"', md)
        self.assertIn('role="tabpanel"', md)
        self.assertIn('aria-controls="shellpanel-bash"', md)
        for shell in ("bash", "zsh", "fish", "powershell", "cmd"):
            self.assertIn(f'id="shelltab-{shell}"', md)
        self.assertIn("data-tabs", md)
        tabs = (root / "backend" / "static" / "js" / "behaviors" / "tabs.js").read_text()
        self.assertIn("ArrowRight", tabs)
        self.assertIn("ArrowLeft", tabs)
        self.assertIn("Home", tabs)
        self.assertIn("End", tabs)
        self.assertIn("aria-selected", tabs)
        # the page carries no script of its own (pages/tests_docs_reading.py)
        self.assertNotIn("<script", md)

    # -- the dashboard: B1, B3, B4, B5, M3, M4, M6, M7, m3, m4 ------------------------
    # These findings were held by reading the React dashboard's source. The dashboard is Django views on the shell now, so each is held on the
    # page as it is drawn: the skip link, <main>, the one <h1> and the title in dashboard/tests_app.py and tests_layout.py; the meters in
    # pages/tests_meter.py; a name for every table, its scoped headers and the Actions column in dashboard/tests_app_css.py (every table),
    # tests_files.py and tests_keys.py; the new key taking focus, and a refusal announced as an alert, in tests_keys.py; a one-line result
    # as role="status" in tests_app.py; Copy named by its file, with the page's one status line, in tests_files.py. The quickstart's own
    # Copy buttons are named in drop_core/tests_site_pages.py.

    # -- mailto (M2) -----------------------------------------------------------------

    def test_report_abuse_email_is_a_link(self):
        response = self.client.get("/report-abuse/")
        self.assertContains(
            response, '<a href="mailto:abuse@veilfile.com">abuse@veilfile.com</a>'
        )

    # -- scrollable markdown regions (M3) --------------------------------------------

    def test_render_markdown_marks_scroll_regions(self):
        from blog.render import render_markdown

        html = render_markdown("```\n" + "x" * 200 + "\n```")
        self.assertIn(
            '<pre tabindex="0" role="region" aria-label="Scrollable code block">',
            html,
        )
        html = render_markdown("| a | b |\n|---|---|\n| 1 | 2 |")
        self.assertIn(
            '<div class="table-scroll" tabindex="0" role="region" '
            'aria-label="Scrollable table"><table>',
            html,
        )
        self.assertIn("</table></div>", html)

    # -- docs current-page (M5) -------------------------------------------------------

    def test_menu_marks_where_the_reader_is_in_docs(self):
        # Docs pages have no nav of their own now: the menu's Docs link marks the page ("page" on the
        # index, "true" on a page inside the section).
        response = self.client.get("/docs/")
        self.assertContains(response, 'href="/docs/" aria-current="page">Docs</a>')
        response = self.client.get("/docs/quickstart/")
        self.assertContains(response, 'href="/docs/" aria-current="true">Docs</a>')

    # -- form errors (M7) and status messages (m4) -------------------------------------

    def test_signup_errors_linked_to_fields(self):
        response = self.client.post(
            "/accounts/signup/",
            {
                "username": "a11yuser",
                "password1": "s3cur3pass!!",
                "password2": "different!!",
                "email": "a11y@example.com",
            },
        )
        self.assertEqual(response.status_code, 200)
        # Per-field errors are linked to their inputs (no .errors container
        # renders for pure field errors).
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(
            response, 'aria-describedby="id_password2_error"'
        )
        self.assertContains(response, 'id="id_password2_error"')

    def test_login_errors_announced(self):
        response = self.client.post(
            "/accounts/login/",
            {"username": "nobody", "password": "wrong"},
        )
        self.assertEqual(response.status_code, 200)
        # A failed sign-in is announced: one role=alert summary that holds the sentence.
        from drop_core.tests_sign_in_pages import Page

        alerts = Page(response.content.decode()).alerts
        self.assertEqual(len(alerts), 1)
        self.assertIn("The username, email, or password is incorrect. Try again.", alerts[0])

    def test_verify_email_status_announced(self):
        from django.template.loader import render_to_string

        html = render_to_string(
            "registration/verify_email.html",
            {"verified": True, "sent": False},
        )
        self.assertRegex(
            html,
            r'<[a-z]+ [^>]*role="status"[^>]*>\s*Your email address is verified\.',
        )


class DeviceFlowTests(TestCase):
    """RFC 8628 device flow: issuance, polling, browser approval, key caps."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        _use_local_dir(self, self.tmpdir)
        cache.clear()  # rate-limit counters and stashed raw keys are per-test
        self.client = APIClient()
        self.user = User.objects.create_user(username="dev", password="pw12345678")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _verify(self, user=None):
        user = user or self.user
        EmailVerification.objects.create(
            user=user,
            email=f"{user.username}@example.com",
            is_verified=True,
            verified_at=timezone.now(),
        )

    def _token_body(self, raw_device_code, grant_type=_DEVICE_GRANT_TYPE):
        return {"grant_type": grant_type, "device_code": raw_device_code}

    def _approve(self, user_code, user=None):
        self.client.force_login(user or self.user)
        # The consent page requires an explicit "I started this" confirmation.
        return self.client.post(
            "/device/approve",
            {"user_code": user_code, "confirm": "yes"},
            follow=False,
        )

    # -- issuance --------------------------------------------------------

    def test_issuance_returns_all_fields(self):
        response = self.client.post("/api/v1/device/code", {}, format="json")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            set(body.keys()),
            {
                "device_code",
                "user_code",
                "verification_uri",
                "verification_uri_complete",
                "expires_in",
                "interval",
            },
        )
        self.assertRegex(body["user_code"], r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
        self.assertEqual(body["expires_in"], 600)
        self.assertEqual(body["interval"], 5)
        self.assertTrue(body["verification_uri"].endswith("/device"))
        self.assertIn(f"/device?user_code={body['user_code']}", body["verification_uri_complete"])
        # The secret device_code is never stored raw — only its sha256.
        import hashlib

        digest = hashlib.sha256(body["device_code"].encode()).hexdigest()
        self.assertTrue(
            DeviceCode.objects.filter(device_code_hash=digest).exists()
        )

    def test_device_codes_unique_across_50_issuances(self):
        seen_device = set()
        seen_user = set()
        for _ in range(50):
            _, raw_device_code, formatted = _issue_device_code()
            self.assertNotIn(raw_device_code, seen_device)
            self.assertNotIn(formatted, seen_user)
            seen_device.add(raw_device_code)
            seen_user.add(formatted)
        self.assertEqual(len(seen_device), 50)

    def test_issuance_rate_limited_per_ip(self):
        for _ in range(30):
            response = self.client.post("/api/v1/device/code", {}, format="json")
            self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/v1/device/code", {}, format="json")
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

    # -- polling before approval -------------------------------------------

    def test_poll_before_approval_then_slow_down(self):
        _, raw_device_code, _ = _issue_device_code()
        response = self.client.post(
            "/api/v1/device/token", self._token_body(raw_device_code), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "authorization_pending")
        # Immediate re-poll is faster than the 5s interval.
        response = self.client.post(
            "/api/v1/device/token", self._token_body(raw_device_code), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "slow_down")

    def test_poll_unknown_device_code_invalid_grant(self):
        response = self.client.post(
            "/api/v1/device/token",
            self._token_body("no-such-device-code"),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_grant")

    def test_poll_wrong_grant_type_rejected(self):
        _, raw_device_code, _ = _issue_device_code()
        response = self.client.post(
            "/api/v1/device/token",
            self._token_body(raw_device_code, grant_type="password"),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "unsupported_grant_type")

    # -- browser approval --------------------------------------------------

    def test_consent_page_renders_with_user_code(self):
        self._verify()
        _, _, formatted = _issue_device_code()
        self.client.force_login(self.user)
        response = self.client.get(f"/device?user_code={formatted}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, formatted)
        self.assertContains(response, "Approve")

    def test_device_page_without_code_shows_entry_form(self):
        self._verify()
        self.client.force_login(self.user)
        response = self.client.get("/device")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter the code shown in your terminal.")

    def test_device_page_requires_login(self):
        _, _, formatted = _issue_device_code()
        response = self.client.get(f"/device?user_code={formatted}")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_approve_by_unverified_user_403_no_key(self):
        # No EmailVerification row: login alone must not mint keys.
        _, _, formatted = _issue_device_code()
        response = self._approve(formatted)
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Verify your email first", status_code=403)
        self.assertEqual(APIKey.objects.count(), 0)
        device_code = DeviceCode.objects.get(user_code=formatted.replace("-", ""))
        self.assertEqual(device_code.status, DeviceCode.STATUS_PENDING)

    def test_approve_by_verified_user_mints_device_key(self):
        self._verify()
        _, raw_device_code, formatted = _issue_device_code()
        response = self._approve(formatted)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You approved this login")
        device_code = DeviceCode.objects.get(user_code=formatted.replace("-", ""))
        self.assertEqual(device_code.status, DeviceCode.STATUS_APPROVED)
        self.assertEqual(device_code.workspace.owner, self.user)
        self.assertEqual(device_code.approved_by, self.user)
        # Approval only records the decision: the key is minted by the
        # terminal's poll, so nothing exists (or is cached) yet.
        self.assertIsNone(device_code.api_key)
        self.assertEqual(APIKey.objects.count(), 0)
        self.assertIsNone(cache.get(f"device-key:{device_code.pk}"))
        response = self.client.post(
            "/api/v1/device/token", self._token_body(raw_device_code), format="json"
        )
        self.assertEqual(response.status_code, 200)
        device_code.refresh_from_db()
        self.assertTrue(device_code.api_key.device_issued)
        self.assertEqual(device_code.api_key.name, "Terminal login")
        self.assertEqual(
            APIKey.verify(response.json()["api_key"]).pk, device_code.api_key.pk
        )
        # Funnel records the issuance like a dashboard key creation.
        self.assertTrue(
            FunnelEvent.objects.filter(
                stage="key_issued", workspace=device_code.workspace
            ).exists()
        )

    def test_poll_after_approval_returns_key_then_consumed(self):
        self._verify()
        _, raw_device_code, formatted = _issue_device_code()
        self._approve(formatted)
        response = self.client.post(
            "/api/v1/device/token", self._token_body(raw_device_code), format="json"
        )
        self.assertEqual(response.status_code, 200)
        raw_key = response.json()["api_key"]
        self.assertTrue(raw_key.startswith("vlf_"))
        # The issued key authenticates as a normal API key.
        key = APIKey.verify(raw_key)
        self.assertIsNotNone(key)
        self.assertTrue(key.device_issued)
        # Second poll: the grant is consumed.
        response = self.client.post(
            "/api/v1/device/token", self._token_body(raw_device_code), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_grant")

    def test_deny_then_poll_access_denied(self):
        self._verify()
        _, raw_device_code, formatted = _issue_device_code()
        self.client.force_login(self.user)
        response = self.client.post("/device/deny", {"user_code": formatted})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You denied this request")
        device_code = DeviceCode.objects.get(user_code=formatted.replace("-", ""))
        self.assertEqual(device_code.status, DeviceCode.STATUS_DENIED)
        response = self.client.post(
            "/api/v1/device/token", self._token_body(raw_device_code), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "access_denied")
        self.assertEqual(APIKey.objects.count(), 0)

    # -- expiry ------------------------------------------------------------

    def test_expired_code_poll_and_browser(self):
        device_code, raw_device_code, formatted = _issue_device_code()
        device_code.expires_at = timezone.now() - timedelta(seconds=1)
        device_code.save(update_fields=["expires_at"])
        response = self.client.post(
            "/api/v1/device/token", self._token_body(raw_device_code), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "expired_token")
        device_code.refresh_from_db()
        self.assertEqual(device_code.status, DeviceCode.STATUS_EXPIRED)
        # Browser shows the expired state.
        self._verify()
        self.client.force_login(self.user)
        response = self.client.get(f"/device?user_code={formatted}")
        self.assertEqual(response.status_code, 410)
        self.assertContains(
            response, "This code has expired", status_code=410
        )

    # -- key caps ----------------------------------------------------------

    def test_device_keys_count_against_plan_cap(self):
        self._verify()
        workspace = get_or_create_workspace(self.user)
        # Free plan: key cap is 1, and the terminal key uses it.
        device_key, _ = APIKey.create_key(workspace, "Terminal login")
        device_key.device_issued = True
        device_key.save(update_fields=["device_issued"])
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/v1/keys/", {"name": "dashboard"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()["code"], "key_limit_reached")
        # Usage counts every active key against the cap.
        response = self.client.get("/api/v1/usage")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["keys_used"], 1)
        self.assertEqual(response.json()["keys_cap"], 1)

    def test_device_key_revocable_via_dashboard(self):
        self._verify()
        _, raw_device_code, formatted = _issue_device_code()
        self._approve(formatted)
        self.client.post(
            "/api/v1/device/token", self._token_body(raw_device_code), format="json"
        )
        device_code = DeviceCode.objects.get(user_code=formatted.replace("-", ""))
        self.client.force_login(self.user)
        response = self.client.delete(f"/api/v1/keys/{device_code.api_key.pk}/")
        self.assertEqual(response.status_code, 204)
        device_code.api_key.refresh_from_db()
        self.assertFalse(device_code.api_key.is_active)

    # -- end to end --------------------------------------------------------

    def test_end_to_end_code_approve_token_upload(self):
        self._verify()
        issue = self.client.post("/api/v1/device/code", {}, format="json")
        self.assertEqual(issue.status_code, 200)
        body = issue.json()
        self._approve(body["user_code"])
        token = self.client.post(
            "/api/v1/device/token",
            self._token_body(body["device_code"]),
            format="json",
        )
        self.assertEqual(token.status_code, 200)
        raw_key = token.json()["api_key"]
        upload = self.client.post(
            "/api/v1/artifacts/",
            {"file": SimpleUploadedFile("note.txt", b"hello veilfile")},
            HTTP_AUTHORIZATION=f"Bearer {raw_key}",
        )
        self.assertEqual(upload.status_code, 201)
