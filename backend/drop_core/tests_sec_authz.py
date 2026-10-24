"""Authorization regressions for the one-time upload URL (PUT
/api/v1/uploads/<token>): the URL must stop working whenever the minting
credential would no longer authenticate, including when its owner is
suspended or (for OAuth) unverified."""

import os
import secrets
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from oauth2_provider.models import get_access_token_model, get_application_model
from rest_framework.test import APIClient

from drop_core.models import APIKey, Artifact, EmailVerification, get_or_create_workspace
from drop_core.oauth import OAUTH_SCOPE_MCP_USE
from drop_core.services import mint_upload_url

User = get_user_model()
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class SuspendedOwnerUploadUrlTests(TestCase):
    def setUp(self):
        cache.clear()
        tmpdir = tempfile.mkdtemp(prefix="veilfile-sec-authz-")
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        patcher = mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": tmpdir})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = User.objects.create_user(username="owner", password="pw12345678")
        self.workspace = get_or_create_workspace(self.user)
        self.key, _raw = APIKey.create_key(self.workspace, "k")
        self.verification = EmailVerification.objects.create(
            user=self.user,
            email="owner@example.com",
            is_verified=True,
            verified_at=timezone.now(),
        )
        self.client = APIClient()

    def _put(self, pending):
        return self.client.put(
            f"/api/v1/uploads/{pending.token}",
            data=PNG,
            content_type="application/octet-stream",
        )

    def _oauth_pending(self):
        Application = get_application_model()
        app = Application.objects.create(
            name="client",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris="http://127.0.0.1:9876/callback",
        )
        get_access_token_model().objects.create(
            user=self.user,
            application=app,
            token=secrets.token_urlsafe(32),
            expires=timezone.now() + timedelta(hours=1),
            scope=OAUTH_SCOPE_MCP_USE,
        )
        return mint_upload_url(
            self.workspace, None, "shot.png", oauth_application_id=app.id
        )

    def test_live_urls_work(self):
        self.assertEqual(
            self._put(mint_upload_url(self.workspace, self.key, "a.png")).status_code,
            201,
        )
        self.assertEqual(self._put(self._oauth_pending()).status_code, 201)

    def test_suspended_owner_key_minted_url_is_dead(self):
        pending = mint_upload_url(self.workspace, self.key, "a.png")
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self._put(pending).status_code, 404)
        self.assertEqual(Artifact.objects.count(), 0)

    def test_suspended_owner_oauth_minted_url_is_dead(self):
        pending = self._oauth_pending()
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self._put(pending).status_code, 404)
        self.assertEqual(Artifact.objects.count(), 0)

    def test_unverified_owner_oauth_minted_url_is_dead(self):
        pending = self._oauth_pending()
        self.verification.is_verified = False
        self.verification.save(update_fields=["is_verified"])
        self.assertEqual(self._put(pending).status_code, 404)
        self.assertEqual(Artifact.objects.count(), 0)
