"""The bar on a team that is on Free (team seats, billing): a member's credentials and writes are refused while the plan is Free,
and nothing is stopped, so access returns with the plan.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it).
"""
import base64
import json
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from dashboard.forms import issue_key_token
from drop_billing.tests_fix_billing import FixBillingBase, make_event, make_sub, sub_list

from .key_caps import KeyNotAllowed, active_keys, create_key_within_cap
from .models import (
    APIKey,
    Artifact,
    DeviceCode,
    MemberUsageCounter,
    Membership,
    UsageCounter,
    Workspace,
)
from .oauth import resolve_oauth_credential
from .plans import PLANS
from .services import mint_upload_url, upload_artifact
from .storage import LocalStorage
from .tests import _use_local_dir
from .tests_fix_device import _DeviceTestBase
from .tests_members import _issue_token, _make_app, make_user, second_member
from .tests_teams import team_of

KEY_SENTENCE = "Your team is on the Free plan, so no key was made. Ask your administrator to choose a paid plan under Billing."
UPLOAD_SENTENCE = "Your team is on the Free plan, so nothing was stored. Ask your administrator to choose a paid plan under Billing."
DEVICE_SENTENCE = "Your team is on the Free plan, so this login cannot make a key. Ask your administrator to choose a paid plan under Billing."


def drop(workspace):
    """The team's plan goes to Free the way a test without a webhook does it."""
    Workspace.objects.filter(pk=workspace.pk).update(plan="free")
    workspace.refresh_from_db()


def restore(workspace):
    Workspace.objects.filter(pk=workspace.pk).update(plan="team")
    workspace.refresh_from_db()


class LockedOnFree:
    """Stands in for Workspace.objects.select_for_update(): the row read under the lock says Free, as after a webhook that committed
    while the upload waited."""

    def only(self, *fields):
        return self

    def get(self, pk):
        row = Workspace.objects.get(pk=pk)
        row.plan = "free"
        return row


def bearer(raw):
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def rpc(client, name, arguments, raw):
    return client.post(
        "/mcp",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}),
        content_type="application/json",
        **bearer(raw),
    )


@override_settings(TEAM_SEATS=True)
class Base(TestCase):
    longMessage = False

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        _use_local_dir(self, self.tmpdir)
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.team, self.admin = team_of("ada")
        self.grace = second_member(self.team, "grace")
        self.key, self.raw = create_key_within_cap(self.team, "grace-key", member=self.grace)

    def post_file(self, **extra):
        return self.client.post("/api/v1/artifacts/", {"file": SimpleUploadedFile("a.txt", b"hi")}, **extra)

    def put(self, token):
        return self.client.put(f"/api/v1/uploads/{token}", b"hi", content_type="application/octet-stream")

    def used_up(self):
        UsageCounter.objects.update_or_create(
            workspace=self.team, year_month=timezone.now().strftime("%Y-%m"),
            defaults={"uploads": PLANS["free"]["uploads"]},
        )


class CredentialTests(Base):
    def test_a_barred_members_key_and_token_are_refused_and_untouched(self):
        from .models import is_barred

        raw_token, _, _ = _issue_token(self.grace.user, _make_app())
        self.assertIsNotNone(APIKey.verify(self.raw))
        self.assertIsNotNone(resolve_oauth_credential(raw_token))
        drop(self.team)
        self.assertTrue(is_barred(self.grace))
        self.assertIsNone(APIKey.verify(self.raw))
        self.assertIsNone(resolve_oauth_credential(raw_token))
        self.key.refresh_from_db()
        self.grace.refresh_from_db()
        self.assertTrue(self.key.is_active)
        self.assertIsNone(self.grace.removed_at)
        self.assertEqual(
            (self.grace.user.oauth2_provider_accesstoken.count(), self.grace.user.oauth2_provider_refreshtoken.filter(revoked__isnull=True).count()),
            (1, 1),
        )

    def test_access_returns_with_the_plan_and_nothing_is_remade(self):
        from .models import is_barred

        raw_token, _, _ = _issue_token(self.grace.user, _make_app())
        drop(self.team)
        self.assertIsNone(APIKey.verify(self.raw))
        restore(self.team)
        self.assertFalse(is_barred(self.grace))
        self.assertEqual(APIKey.verify(self.raw), self.key)
        self.assertIsNotNone(resolve_oauth_credential(raw_token))

    def test_the_administrator_of_a_free_team_keeps_access(self):
        drop(self.team)
        APIKey.create_key(self.team, "second", member=self.grace)
        self.assertEqual(active_keys(self.team).count(), 0)
        key, raw = create_key_within_cap(self.team, "ada-key", member=self.admin)
        self.assertEqual(list(active_keys(self.team)), [key])
        self.assertEqual(self.post_file(**bearer(raw)).status_code, 201)
        self.assertEqual(Artifact.objects.get().member, self.admin)

    def test_a_one_person_account_is_never_barred_and_answers_as_before(self):
        from .models import is_barred

        # Recorded on main with the switch on: APIKey.verify 1, an upload URL PUT 22 (after a first upload has warmed the sweep), a REST
        # upload 17 and the MCP list_artifacts call 3, on Free and on Team alike.
        warm, _ = team_of("warm")
        warm_key, _ = APIKey.create_key(warm, "k")
        self.assertEqual(self.put(mint_upload_url(warm, warm_key, "a.txt").token).status_code, 201)
        for plan in ("free", "team"):
            with self.subTest(plan=plan):
                workspace, row = team_of(f"solo-{plan}", plan=plan)
                key, raw = APIKey.create_key(workspace, "k", member=row)
                self.assertFalse(is_barred(row))
                self.assertFalse(is_barred(row, workspace))
                with CaptureQueriesContext(connection) as verify:
                    self.assertEqual(APIKey.verify(raw), key)
                url = mint_upload_url(workspace, key, "a.txt", member=row)
                with CaptureQueriesContext(connection) as puts:
                    self.assertEqual(self.put(url.token).status_code, 201)
                with CaptureQueriesContext(connection) as rest:
                    self.assertEqual(self.post_file(**bearer(raw)).status_code, 201)
                with CaptureQueriesContext(connection) as mcp:
                    self.assertEqual(rpc(self.client, "list_artifacts", {}, raw).status_code, 200)
                self.assertEqual((len(verify), len(puts), len(rest), len(mcp)), (1, 22, 17, 3))

    def test_a_role_read_under_the_lock_decides(self):
        drop(self.team)
        stale = Membership.objects.get(pk=self.grace.pk)
        self.assertEqual(stale.role, Membership.MEMBER)
        Membership.objects.filter(pk=self.admin.pk).update(role=Membership.MEMBER)
        Membership.objects.filter(pk=self.grace.pk).update(role=Membership.ADMINISTRATOR)
        APIKey.objects.filter(member=self.grace).update(is_active=False)
        key, raw = create_key_within_cap(self.team, "now-admin", member=stale)
        self.assertEqual(key.member, stale)


class WebhookTests(FixBillingBase):
    longMessage = False

    def setUp(self):
        super().setUp()
        self.set_billing("team", customer="cus_A", sub_id="sub_A", status="active")
        self.grace = second_member(self.ws, "grace")
        self.key, self.raw = create_key_within_cap(self.ws, "agent", member=self.grace)
        self.lister = self.patch_stripe("list")

    def stripe_says(self, status):
        self.lister.return_value = sub_list(make_sub("sub_A", status, "price_team"))

    def test_a_signed_webhook_bars_and_a_paid_invoice_restores(self):
        invoice = {"id": "in_1", "object": "invoice", "customer": "cus_A"}
        with override_settings(TEAM_SEATS=True):
            self.assertEqual(APIKey.verify(self.raw), self.key)
            self.stripe_says("past_due")
            self.assertEqual(self.post_event(make_event("invoice.payment_failed", invoice)).status_code, 200)
            self.assertEqual(self.reload().plan, "team")
            self.assertEqual(APIKey.verify(self.raw), self.key)
            self.stripe_says("unpaid")
            self.assertEqual(self.post_event(make_event("customer.subscription.updated", make_sub("sub_A", "unpaid", "price_team"))).status_code, 200)
            self.assertEqual(self.reload().plan, "free")
            self.assertIsNone(APIKey.verify(self.raw))
            self.stripe_says("active")
            self.assertEqual(self.post_event(make_event("invoice.paid", invoice)).status_code, 200)
            self.assertEqual(self.reload().plan, "team")
            self.assertEqual(APIKey.verify(self.raw), self.key)
            self.lister.return_value = sub_list()
            self.assertEqual(self.post_event(make_event("customer.subscription.deleted", make_sub("sub_A", "canceled", "price_team"))).status_code, 200)
            self.assertEqual(self.reload().plan, "free")
            self.assertIsNone(APIKey.verify(self.raw))


class UploadTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.grace.user)

    def test_a_barred_member_is_told_about_the_bar_before_the_caps(self):
        drop(self.team)
        self.used_up()
        response = self.post_file()
        self.assertEqual((response.status_code, response.json()["code"]), (403, "team_on_free"))

    def test_an_upload_waiting_on_the_lock_sees_the_drop(self):
        with mock.patch("drop_core.services.Workspace.objects.select_for_update", return_value=LockedOnFree()):
            with self.assertRaises(ValueError) as caught:
                upload_artifact(self.team, None, "a.txt", b"hi", None, member=self.grace)
        self.assertEqual(str(caught.exception), "team_on_free")
        self.assertEqual(UsageCounter.objects.get(workspace=self.team).uploads, 0)
        self.assertEqual(MemberUsageCounter.objects.get(member=self.grace).uploads, 0)
        self.assertFalse(Artifact.objects.exists())

    def test_a_session_upload_answers_team_on_free(self):
        drop(self.team)
        response = self.post_file()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": UPLOAD_SENTENCE, "code": "team_on_free"})
        self.assertFalse(Artifact.objects.exists())

    def test_the_mcp_upload_answers_the_same_sentence(self):
        arguments = {"filename": "a.txt", "content_base64": base64.b64encode(b"hi").decode()}
        with mock.patch("drop_core.services.Workspace.objects.select_for_update", return_value=LockedOnFree()):
            response = rpc(self.client, "upload_artifact", arguments, self.raw)
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertTrue(result["isError"], result)
        self.assertIn(UPLOAD_SENTENCE, json.dumps(result))
        self.assertFalse(Artifact.objects.exists())

    def test_an_upload_url_is_refused_and_left_unused(self):
        pending = mint_upload_url(self.team, self.key, "a.txt", member=self.grace)
        drop(self.team)
        response = self.put(pending.token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": UPLOAD_SENTENCE, "code": "team_on_free"})
        pending.refresh_from_db()
        self.assertFalse(pending.used)
        restore(self.team)
        self.assertEqual(self.put(pending.token).status_code, 201)
        self.assertTrue(Artifact.objects.exists())

    def test_a_dead_credentials_url_still_answers_404(self):
        pending = mint_upload_url(self.team, self.key, "a.txt", member=self.grace)
        APIKey.objects.filter(pk=self.key.pk).update(is_active=False)
        drop(self.team)
        self.assertEqual(self.put(pending.token).status_code, 404)
        pending.refresh_from_db()
        self.assertTrue(pending.used)

    def test_a_suspended_free_team_answers_workspace_suspended(self):
        Workspace.objects.filter(pk=self.team.pk).update(suspended_at=timezone.now())
        drop(self.team)
        response = self.post_file()
        self.assertEqual((response.status_code, response.json()["code"]), (403, "workspace_suspended"))


class KeyTests(Base):
    def test_a_barred_member_cannot_make_a_key(self):
        drop(self.team)
        APIKey.objects.filter(pk=self.key.pk).update(is_active=False)
        with self.assertRaises(KeyNotAllowed) as caught:
            create_key_within_cap(self.team, "new", member=self.grace)
        self.assertEqual(caught.exception.code, "team_on_free")
        self.client.force_login(self.grace.user)
        # The page has no form for her, so the token is issued as the page issues it and the POST is forged.
        session = self.client.session
        token = issue_key_token(session)
        session.save()
        page = self.client.post("/app/keys/", {"name": "new", "token": token})
        self.assertIn(KEY_SENTENCE, page.content.decode())
        response = self.client.post("/api/v1/keys/", {"name": "new"}, content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": KEY_SENTENCE, "code": "team_on_free"})
        self.assertEqual(APIKey.objects.filter(is_active=True).count(), 0)


class DeviceTests(_DeviceTestBase):
    longMessage = False

    def setUp(self):
        super().setUp()
        Workspace.objects.filter(pk=self.workspace.pk).update(plan="team")
        self.workspace.refresh_from_db()
        self.grace = second_member(self.workspace, "grace")
        self.graces = APIClient()
        self.graces.force_login(self.grace.user)

    def test_a_device_login_is_refused_before_anything_is_written(self):
        with override_settings(TEAM_SEATS=True):
            grant = self._start()
            drop(self.workspace)
            page = self.graces.get(f"/device?user_code={grant['user_code']}")
            self.assertContains(page, DEVICE_SENTENCE)
            self.assertNotContains(page, ">Approve</button>")
            response = self._approve(grant, browser=self.graces)
            self.assertContains(response, DEVICE_SENTENCE, status_code=403)
            self.assertEqual(self._row(grant).status, DeviceCode.STATUS_PENDING)
            self.assertEqual(APIKey.objects.count(), 0)

    def test_a_poll_of_a_code_approved_before_the_drop_is_denied(self):
        with override_settings(TEAM_SEATS=True):
            grant = self._start()
            self.assertEqual(self._approve(grant, browser=self.graces).status_code, 200)
            drop(self.workspace)
            response = self._poll(grant)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {"error": "access_denied", "error_description": KEY_SENTENCE})
            self.assertEqual(self._row(grant).status, DeviceCode.STATUS_DENIED)
            self.assertEqual(APIKey.objects.count(), 0)


class WaysOutTests(Base):
    def test_removal_leaving_revoking_and_links_ignore_the_plan(self):
        from .teams import leave_team, remove_member

        lin = second_member(self.team, "lin")
        mine = Artifact.objects.create(
            workspace=self.team, member=self.grace, filename="mine.txt", size_bytes=5,
            expires_at=timezone.now() + timedelta(days=7),
        )
        import io
        LocalStorage().save(mine.token, io.BytesIO(b"bytes"), "application/octet-stream")
        self.addCleanup(LocalStorage().delete, mine.token)
        drop(self.team)
        self.assertEqual(self.client.get(f"/a/{mine.token}").status_code, 200)
        self.client.force_login(self.grace.user)
        self.assertEqual(self.client.delete(f"/api/v1/artifacts/{mine.id}/").status_code, 204)
        self.assertEqual(remove_member(self.admin, lin.pk, revoke_files=False), 0)
        leave_team(Membership.objects.get(pk=self.grace.pk))
        self.assertEqual(Membership.objects.filter(workspace=self.team, role=Membership.MEMBER, removed_at__isnull=True).count(), 0)
