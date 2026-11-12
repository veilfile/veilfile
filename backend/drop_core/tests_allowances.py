import importlib
import os
import tempfile
from datetime import timedelta
from unittest import mock

from django.apps import apps
from django.contrib import admin as django_admin
from django.db import IntegrityError, transaction
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .account import create_key as account_create_key
from .key_caps import KeyCapReached, MemberKeyCapReached, create_key_within_cap
from .models import (
    APIKey,
    Artifact,
    DeviceCode,
    MemberUsageCounter,
    Membership,
    PendingUpload,
    SafetyEvent,
    UsageCounter,
    administrator_of,
    get_or_create_workspace,
)
from .plans import PLANS
from .services import StorageError, _current_month, member_storage_bytes, mint_upload_url, upload_artifact
from .views import ERROR_MESSAGES
from .tests_fix_device import _DeviceTestBase
from .tests_members import legacy_rows, make_user

MODULE = importlib.import_module("drop_core.migrations.0014_backfill_members_again")
backfill = MODULE.backfill_members_again


class SecondBackfillTests(TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": tempfile.mkdtemp(prefix="veilfile-test-")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rows_with_no_member_go_to_the_administrator(self):
        workspace = get_or_create_workspace(make_user("ada"))
        legacy_rows(workspace)
        backfill(apps, None)
        admin = administrator_of(workspace)
        for model in (APIKey, Artifact, PendingUpload, SafetyEvent):
            self.assertEqual(set(model.objects.values_list("member", flat=True)), {admin.pk}, model.__name__)

    def test_it_can_run_twice(self):
        legacy_rows(get_or_create_workspace(make_user("ada")))
        backfill(apps, None)
        backfill(apps, None)
        self.assertEqual(Membership.objects.count(), 1)

    def test_it_does_not_overwrite_a_row_that_names_a_member(self):
        workspace = get_or_create_workspace(make_user("ada"))
        grace = Membership.objects.create(workspace=workspace, user=make_user("grace"))
        key, _ = APIKey.create_key(workspace, "agent", member=grace)
        backfill(apps, None)
        key.refresh_from_db()
        self.assertEqual(key.member, grace)

    def test_it_stops_the_deploy_when_a_row_cannot_be_filled(self):
        workspace = get_or_create_workspace(make_user("ada"))
        legacy_rows(workspace)
        Membership.objects.filter(workspace=workspace).update(removed_at=timezone.now())
        with self.assertRaisesMessage(RuntimeError, "keys 1, files 1, upload URLs 1"):
            backfill(apps, None)

    def test_a_safety_event_with_no_member_does_not_stop_the_deploy(self):
        workspace = get_or_create_workspace(make_user("ada"))
        legacy_rows(workspace)
        APIKey.objects.all().delete()
        Artifact.objects.all().delete()
        PendingUpload.objects.all().delete()
        Membership.objects.filter(workspace=workspace).update(removed_at=timezone.now())
        backfill(apps, None)
        self.assertTrue(SafetyEvent.objects.filter(member__isnull=True).exists())

    def test_it_fills_more_rows_than_a_batch(self):
        workspace = get_or_create_workspace(make_user("ada"))
        for number in range(5):
            APIKey.create_key(workspace, f"key{number}")
        APIKey.objects.update(member=None)
        with mock.patch.object(MODULE, "BATCH_SIZE", 2):
            backfill(apps, None)
        self.assertFalse(APIKey.objects.filter(member__isnull=True).exists())

    def test_it_fills_more_uuid_keyed_rows_than_a_batch(self):
        workspace = get_or_create_workspace(make_user("ada"))
        key, _ = APIKey.create_key(workspace, "agent")
        expires = timezone.now() + timedelta(days=1)
        for number in range(5):
            Artifact.objects.create(workspace=workspace, filename=f"a{number}.txt", size_bytes=1, expires_at=expires)
            PendingUpload.objects.create(
                workspace=workspace, api_key=key, filename="a.txt", ttl_days=1, token=f"token{number}", expires_at=expires
            )
        Artifact.objects.update(member=None)
        PendingUpload.objects.update(member=None)
        with mock.patch.object(MODULE, "BATCH_SIZE", 2):
            backfill(apps, None)
        self.assertEqual(Artifact.objects.filter(member__isnull=True).count(), 0)
        self.assertEqual(PendingUpload.objects.filter(member__isnull=True).count(), 0)
        self.assertEqual(Artifact.objects.count(), 5)
        self.assertEqual(PendingUpload.objects.count(), 5)

    def test_the_migration_is_not_atomic(self):
        self.assertFalse(MODULE.Migration.atomic)

    def test_the_admin_requires_a_member_on_keys_and_files(self):
        user = make_user("root")
        user.is_staff = user.is_superuser = True
        user.save()
        request = RequestFactory().get("/admin/")
        request.user = user
        for model in (APIKey, Artifact):
            model_admin = django_admin.site._registry[model]
            form = model_admin.get_form(request)
            self.assertTrue(form.base_fields["member"].required, model.__name__)


def second_member(workspace, name, **allowances):
    return Membership.objects.create(workspace=workspace, user=make_user(name), role=Membership.MEMBER, **allowances)


def set_allowance(member, **fields):
    # On the same instance, because upload_artifact and create_key_within_cap read the instance they are given.
    for name, value in fields.items():
        setattr(member, name, value)
    member.save(update_fields=list(fields))


def live_file(workspace, member, size, **fields):
    fields.setdefault("expires_at", timezone.now() + timedelta(days=1))
    return Artifact.objects.create(workspace=workspace, member=member, filename="a.txt", size_bytes=size, **fields)


def team_count(workspace):
    row = UsageCounter.objects.filter(workspace=workspace, year_month=_current_month()).first()
    return row.uploads if row else 0


def member_count(member):
    row = MemberUsageCounter.objects.filter(member=member, year_month=_current_month()).first()
    return row.uploads if row else 0


class AllowanceModelTests(TestCase):
    def test_a_new_membership_has_no_allowances(self):
        admin = administrator_of(get_or_create_workspace(make_user("ada")))
        self.assertEqual((admin.upload_allowance, admin.storage_allowance_bytes, admin.key_allowance), (None, None, None))

    def test_a_member_has_one_counter_a_month(self):
        admin = administrator_of(get_or_create_workspace(make_user("ada")))
        MemberUsageCounter.objects.create(member=admin, year_month="2026-11")
        with self.assertRaises(IntegrityError), transaction.atomic():
            MemberUsageCounter.objects.create(member=admin, year_month="2026-11")

    def test_a_members_storage_counts_only_their_live_files(self):
        workspace = get_or_create_workspace(make_user("ada"))
        grace = second_member(workspace, "grace")
        live_file(workspace, grace, 100)
        live_file(workspace, administrator_of(workspace), 7)
        live_file(workspace, grace, 50, expires_at=timezone.now() - timedelta(days=1))
        self.assertEqual(member_storage_bytes(grace), 100)

    def test_deleting_a_user_removes_their_counters(self):
        ada = make_user("ada")
        admin = administrator_of(get_or_create_workspace(ada))
        MemberUsageCounter.objects.create(member=admin, year_month="2026-11", uploads=3)
        ada.delete()
        self.assertFalse(MemberUsageCounter.objects.exists())


class UploadAllowanceTests(TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": tempfile.mkdtemp(prefix="veilfile-test-")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.workspace = get_or_create_workspace(make_user("ada"))
        self.workspace.plan = "team"
        self.workspace.save(update_fields=["plan"])
        self.admin = administrator_of(self.workspace)
        self.grace = second_member(self.workspace, "grace")
        self.cap = PLANS["team"]["uploads"]

    def upload(self, member, data=b"hi"):
        return upload_artifact(self.workspace, None, "a.txt", data, None, member=member)

    def fill_team_counter(self, uploads):
        UsageCounter.objects.create(workspace=self.workspace, year_month=_current_month(), uploads=uploads)

    def graces_key(self):
        _, raw = APIKey.create_key(self.workspace, "grace-key", member=self.grace)
        return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}

    def post_file(self, auth, data=b"hi"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return APIClient().post("/api/v1/artifacts/", {"file": SimpleUploadedFile("a.txt", data)}, **auth)

    def test_every_upload_is_counted_for_its_member(self):
        self.upload(self.grace)
        self.assertEqual((team_count(self.workspace), member_count(self.grace)), (1, 1))

    def test_a_member_at_their_monthly_allowance_gets_their_own_code(self):
        set_allowance(self.grace, upload_allowance=2)
        self.upload(self.grace)
        self.upload(self.grace)
        with self.assertRaisesMessage(ValueError, "member_monthly_allowance_reached"):
            self.upload(self.grace)
        self.assertEqual(
            (team_count(self.workspace), member_count(self.grace), Artifact.objects.count()), (2, 2, 2)
        )

    def test_a_member_with_no_allowance_can_use_the_whole_pool(self):
        self.fill_team_counter(self.cap - 1)
        self.upload(self.grace)
        with self.assertRaisesMessage(ValueError, "monthly_cap_reached"):
            self.upload(self.grace)

    def test_the_team_limit_wins_when_both_are_reached(self):
        set_allowance(self.grace, upload_allowance=0, storage_allowance_bytes=0)
        self.fill_team_counter(self.cap)
        with self.assertRaises(ValueError) as caught:
            self.upload(self.grace)
        self.assertEqual(str(caught.exception), "monthly_cap_reached")

    def test_an_allowance_above_the_plan_is_capped_by_the_plan(self):
        set_allowance(self.grace, upload_allowance=self.cap * 10)
        self.fill_team_counter(self.cap)
        with self.assertRaises(ValueError) as caught:
            self.upload(self.grace)
        self.assertEqual(str(caught.exception), "monthly_cap_reached")

    def test_a_teammate_using_the_pool_stops_a_member_under_their_allowance(self):
        # The administrator holds a live file as large as the team's whole cap, so grace's large
        # allowance does not help: she gets the team's code.
        team_bytes = PLANS["team"]["storage_gb"] * 1024**3
        set_allowance(self.grace, storage_allowance_bytes=team_bytes * 2)
        live_file(self.workspace, self.admin, team_bytes)
        with self.assertRaises(ValueError) as caught:
            self.upload(self.grace)
        self.assertEqual(str(caught.exception), "storage_cap_reached")

    def test_a_refusal_after_the_reservation_rolls_back_both_counters(self):
        set_allowance(self.grace, storage_allowance_bytes=1000)
        with mock.patch("drop_core.services.member_storage_bytes", side_effect=[0, 1001]):
            with self.assertRaisesMessage(ValueError, "member_storage_allowance_reached"):
                self.upload(self.grace)
        self.assertEqual(
            (team_count(self.workspace), member_count(self.grace), Artifact.objects.count()), (0, 0, 0)
        )

    def test_a_storage_failure_gives_back_both_counters(self):
        with mock.patch("drop_core.services.get_storage") as get_storage:
            get_storage.return_value.save.side_effect = OSError("down")
            with self.assertRaises(StorageError):
                self.upload(self.grace)
        self.assertEqual(
            (team_count(self.workspace), member_count(self.grace), Artifact.objects.count()), (0, 0, 0)
        )

    def test_an_allowance_below_current_use_refuses_new_uploads_only(self):
        first = self.upload(self.grace)
        self.upload(self.grace)
        set_allowance(self.grace, upload_allowance=1)
        with self.assertRaisesMessage(ValueError, "member_monthly_allowance_reached"):
            self.upload(self.grace)
        self.assertEqual(self.client.get(f"/a/{first.token}").status_code, 200)

    def test_the_count_starts_again_next_month(self):
        set_allowance(self.grace, upload_allowance=1)
        with mock.patch("drop_core.services._current_month", return_value="2026-11"):
            self.upload(self.grace)
            with self.assertRaisesMessage(ValueError, "member_monthly_allowance_reached"):
                self.upload(self.grace)
        with mock.patch("drop_core.services._current_month", return_value="2026-12"):
            self.upload(self.grace)
        self.assertEqual(
            set(MemberUsageCounter.objects.filter(member=self.grace).values_list("year_month", "uploads")),
            {("2026-11", 1), ("2026-12", 1)},
        )

    def test_a_one_person_account_gets_the_teams_codes_at_every_limit(self):
        _, raw = APIKey.create_key(self.workspace, "admin-key")
        auth = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
        self.fill_team_counter(self.cap)
        response = self.post_file(auth)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json(),
            {"error": ERROR_MESSAGES["monthly_cap_reached"], "code": "monthly_cap_reached"},
        )
        UsageCounter.objects.all().delete()
        live_file(self.workspace, self.admin, PLANS["team"]["storage_gb"] * 1024**3)
        response = self.post_file(auth)
        self.assertEqual(response.status_code, 402)
        self.assertEqual(
            response.json(),
            {"error": ERROR_MESSAGES["storage_cap_reached"], "code": "storage_cap_reached"},
        )

    def test_the_rest_answers(self):
        auth = self.graces_key()
        set_allowance(self.grace, upload_allowance=1)
        self.assertEqual(self.post_file(auth).status_code, 201)
        response = self.post_file(auth)
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("Retry-After", response)
        self.assertEqual(
            response.json(),
            {
                "error": ERROR_MESSAGES["member_monthly_allowance_reached"],
                "code": "member_monthly_allowance_reached",
            },
        )
        set_allowance(self.grace, upload_allowance=None, storage_allowance_bytes=1)
        response = self.post_file(auth, b"too big for one byte")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                "error": ERROR_MESSAGES["member_storage_allowance_reached"],
                "code": "member_storage_allowance_reached",
            },
        )

    def test_the_messages_are_the_approved_sentences(self):
        self.assertEqual(
            ERROR_MESSAGES["member_monthly_allowance_reached"],
            "You have reached the monthly upload allowance your administrator set. "
            "Uploads work again next month, or your administrator can raise it.",
        )
        self.assertEqual(
            ERROR_MESSAGES["member_storage_allowance_reached"],
            "You have reached the storage allowance your administrator set. "
            "Revoke files you no longer need, or ask your administrator to raise it.",
        )

    def test_a_refused_upload_url_can_be_retried(self):
        set_allowance(self.grace, upload_allowance=0)
        pending = mint_upload_url(self.workspace, None, "a.txt", member=self.grace)
        response = Client().put(f"/api/v1/uploads/{pending.token}", data=b"hello", content_type="text/plain")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["code"], "member_monthly_allowance_reached")
        pending.refresh_from_db()
        self.assertFalse(pending.used)
        Membership.objects.filter(pk=self.grace.pk).update(upload_allowance=5)
        response = Client().put(f"/api/v1/uploads/{pending.token}", data=b"hello", content_type="text/plain")
        self.assertEqual(response.status_code, 201)

    def test_the_mcp_tool_reports_the_code(self):
        import base64
        import json

        from drop_mcp.views import mcp_endpoint

        set_allowance(self.grace, upload_allowance=0)
        auth = self.graces_key()
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "upload_artifact",
                "arguments": {"filename": "a.txt", "content_base64": base64.b64encode(b"hi").decode()},
            },
        }
        request = RequestFactory().post(
            "/mcp", data=json.dumps(body).encode(), content_type="application/json", **auth
        )
        result = json.loads(mcp_endpoint(request).content.decode())["result"]
        self.assertIs(result["isError"], True)
        self.assertEqual(
            json.loads(result["content"][0]["text"]),
            {
                "error": ERROR_MESSAGES["member_monthly_allowance_reached"],
                "code": "member_monthly_allowance_reached",
            },
        )


class KeyAllowanceTests(TestCase):
    def setUp(self):
        self.workspace = get_or_create_workspace(make_user("ada"))
        self.workspace.plan = "team"
        self.workspace.save(update_fields=["plan"])
        self.admin = administrator_of(self.workspace)
        self.grace = second_member(self.workspace, "grace")

    def test_a_member_at_their_key_allowance_gets_their_own_refusal(self):
        set_allowance(self.grace, key_allowance=1)
        create_key_within_cap(self.workspace, "one", member=self.grace)
        with self.assertRaises(MemberKeyCapReached):
            create_key_within_cap(self.workspace, "two", member=self.grace)
        self.assertEqual(APIKey.objects.filter(member=self.grace, is_active=True).count(), 1)

    def test_the_team_limit_wins_when_both_are_reached(self):
        for i in range(PLANS["team"]["keys"]):
            create_key_within_cap(self.workspace, f"k{i}", member=self.admin)
        set_allowance(self.grace, key_allowance=0)
        with self.assertRaises(KeyCapReached) as caught:
            create_key_within_cap(self.workspace, "more", member=self.grace)
        self.assertNotIsInstance(caught.exception, MemberKeyCapReached)

    def test_a_member_over_their_allowance_can_still_replace_their_own_key(self):
        old, _ = create_key_within_cap(self.workspace, "old", member=self.grace)
        set_allowance(self.grace, key_allowance=0)
        new, _ = create_key_within_cap(self.workspace, "new", replacing=old, member=self.grace)
        old.refresh_from_db()
        self.assertEqual((old.is_active, new.is_active, new.member), (False, True, self.grace))

    def test_a_rotation_never_replaces_a_teammates_key(self):
        theirs, _ = create_key_within_cap(self.workspace, "theirs", member=self.admin)
        new, _ = create_key_within_cap(self.workspace, "x", replacing=theirs, member=self.grace)
        theirs.refresh_from_db()
        self.assertEqual((theirs.is_active, new.is_active), (True, True))
        for i in range(PLANS["team"]["keys"] - APIKey.objects.filter(is_active=True).count()):
            create_key_within_cap(self.workspace, f"k{i}", member=self.admin)
        with self.assertRaises(KeyCapReached):
            create_key_within_cap(self.workspace, "y", replacing=theirs, member=self.grace)
        theirs.refresh_from_db()
        self.assertTrue(theirs.is_active)

    def test_no_allowance_changes_nothing(self):
        cap = PLANS["team"]["keys"]
        for i in range(cap - 1):
            create_key_within_cap(self.workspace, f"k{i}", member=self.admin)
        last, _ = create_key_within_cap(self.workspace, "last", member=self.admin)
        with self.assertRaises(KeyCapReached) as caught:
            create_key_within_cap(self.workspace, "more", member=self.admin)
        self.assertNotIsInstance(caught.exception, MemberKeyCapReached)
        # An over-cap workspace can still rotate a key.
        self.workspace.plan = "free"
        self.workspace.save(update_fields=["plan"])
        new, _ = create_key_within_cap(self.workspace, "rotated", replacing=last, member=self.admin)
        self.assertTrue(new.is_active)

    def test_the_create_key_answer(self):
        user = make_user("zed")
        workspace = get_or_create_workspace(user)
        set_allowance(administrator_of(workspace), key_allowance=0)
        answer = account_create_key(user, "mine")
        self.assertEqual(answer.status, 400)
        self.assertEqual(
            answer.body,
            {
                "error": "You have reached the API key allowance your administrator set. "
                "Revoke one of your keys, or ask your administrator to raise it.",
                "code": "member_key_allowance_reached",
            },
        )
        self.assertFalse(APIKey.objects.filter(workspace=workspace).exists())

        other = make_user("yan")
        other_workspace = get_or_create_workspace(other)
        for i in range(PLANS["free"]["keys"]):
            APIKey.create_key(other_workspace, f"k{i}")
        answer = account_create_key(other, "more")
        self.assertEqual(answer.status, 402)
        self.assertEqual(
            answer.body,
            {
                "error": "Your workspace has reached its plan's API key limit. Revoke a key or upgrade your plan.",
                "code": "key_limit_reached",
            },
        )


class DeviceKeyAllowanceTests(_DeviceTestBase):
    def test_a_device_login_at_the_allowance_is_refused_with_the_members_sentence(self):
        grant = self._start()
        self.assertEqual(self._approve(grant).status_code, 200)
        # The allowance is reached after approval, so the refusal comes at the poll.
        set_allowance(administrator_of(self.workspace), key_allowance=0)
        response = self._poll(grant)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "access_denied")
        self.assertEqual(
            response.json()["error_description"],
            "You have reached the API key allowance your administrator set. "
            "Revoke one of your keys in the dashboard, or ask your administrator to raise it.",
        )
        self.assertEqual(self._row(grant).status, DeviceCode.STATUS_DENIED)
        self.assertEqual(APIKey.objects.count(), 0)
