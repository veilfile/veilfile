"""Agents choose a file's visibility when they upload (team seats PR E2)."""

import os
import tempfile
from unittest import mock

from django.test import TestCase, override_settings

from drop_core.models import Artifact, UsageCounter, Workspace, administrator_of, get_or_create_workspace
from drop_core.services import RejectedUpload, mint_upload_url, upload_artifact
from drop_core.tests_allowances import second_member
from drop_core.tests_members import make_user

SERVICES = "backend/drop_core/services.py"
REST_BAD = 'The visibility value is not valid. Send "only_me" or "team".'


@override_settings(TEAM_SEATS=True)
class VisibilityBase(TestCase):
    """Ada's team on Team, with Grace as a member, under the default setting, members_choose."""

    longMessage = False

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": tempfile.mkdtemp(prefix="veilfile-test-")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.workspace = get_or_create_workspace(make_user("ada"))
        self.workspace.plan = "team"
        self.workspace.save(update_fields=["plan"])
        self.admin = administrator_of(self.workspace)
        self.grace = second_member(self.workspace, "grace")

    def setting(self, value):
        Workspace.objects.filter(pk=self.workspace.pk).update(file_visibility=value)
        self.workspace.refresh_from_db()

    def upload(self, visibility=None, member=None):
        return upload_artifact(self.workspace, None, "a.txt", b"hi", None, member=member or self.grace, visibility=visibility)


class ServiceVisibilityTests(VisibilityBase):
    def test_under_members_choose_the_choice_is_honored(self):
        for value in ("only_me", "team"):
            with self.subTest(value=value):
                self.assertEqual(self.upload(value).visibility, value, f"{SERVICES}: {value} is honored")

    def test_with_no_choice_members_choose_gives_only_me(self):
        self.assertEqual(self.upload().visibility, "only_me", f"{SERVICES}: today's default")

    def test_a_team_rule_wins_over_the_choice(self):
        for setting, chosen, given in (("always_team", "only_me", "team"), ("always_only_me", "team", "only_me")):
            with self.subTest(setting=setting):
                self.setting(setting)
                self.assertEqual(self.upload(chosen).visibility, given, f"{SERVICES}: {setting} decides")

    def test_a_bad_value_is_refused_before_anything_is_stored(self):
        with self.assertRaises(RejectedUpload) as refused:
            self.upload("public")
        self.assertEqual((refused.exception.code, refused.exception.detail), ("invalid_request", REST_BAD), SERVICES)
        self.assertEqual((Artifact.objects.count(), UsageCounter.objects.count()), (0, 0), f"{SERVICES}: nothing is stored")

    def test_an_upload_url_carries_the_choice_to_its_put(self):
        pending = mint_upload_url(self.workspace, None, "a.txt", member=self.grace, visibility="team")
        pending.refresh_from_db()
        self.assertEqual(pending.visibility, "team", f"{SERVICES}: the mint records the choice")
        self.assertEqual(self.upload(pending.visibility).visibility, "team", f"{SERVICES}: the PUT applies it")

    def test_the_setting_read_at_the_put_decides(self):
        pending = mint_upload_url(self.workspace, None, "a.txt", member=self.grace, visibility="team")
        self.setting("always_only_me")
        self.assertEqual(self.upload(pending.visibility).visibility, "only_me", f"{SERVICES}: the setting at the PUT wins")

    def test_with_the_switch_off_the_choice_is_ignored(self):
        with override_settings(TEAM_SEATS=False):
            for value in ("team", "public"):
                with self.subTest(value=value):
                    self.assertEqual(self.upload(value, member=self.admin).visibility, "only_me", f"{SERVICES}: ignored")
            pending = mint_upload_url(self.workspace, None, "a.txt", visibility="public")
            self.assertEqual(pending.visibility, "", f"{SERVICES}: an upload URL records nothing")
