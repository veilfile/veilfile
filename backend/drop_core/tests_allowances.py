import importlib
import os
import tempfile
from datetime import timedelta
from unittest import mock

from django.apps import apps
from django.contrib import admin as django_admin
from django.test import RequestFactory, TestCase
from django.utils import timezone

from .models import (
    APIKey,
    Artifact,
    Membership,
    PendingUpload,
    SafetyEvent,
    administrator_of,
    get_or_create_workspace,
)
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
