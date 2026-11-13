"""Agents choose a file's visibility when they upload (team seats PR E2)."""

import os
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from drop_core.models import APIKey, Artifact, UsageCounter, Workspace, administrator_of, get_or_create_workspace
from drop_core.services import RejectedUpload, mint_upload_url, upload_artifact
from drop_core.tests_allowances import second_member
from drop_core.tests_members import make_user

SERVICES = "backend/drop_core/services.py"
VIEWS = "backend/drop_core/views.py"
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


# The keys of a 201 on main, for an upload with no secret flags and no adjusted TTL.
MAIN_KEYS = ["expires_at", "id", "secret_flags", "size_bytes", "url"]


class RestVisibilityTests(VisibilityBase):
    def setUp(self):
        super().setUp()
        _, grace_raw = APIKey.create_key(self.workspace, "grace-key", member=self.grace)
        _, admin_raw = APIKey.create_key(self.workspace, "ada-key")
        self.grace_auth = {"HTTP_AUTHORIZATION": f"Bearer {grace_raw}"}
        self.admin_auth = {"HTTP_AUTHORIZATION": f"Bearer {admin_raw}"}

    def post(self, auth, **fields):
        return APIClient().post("/api/v1/artifacts/", {"file": SimpleUploadedFile("a.txt", b"hi"), **fields}, **auth)

    def test_a_member_chooses_team_and_a_teammate_sees_the_file(self):
        response = self.post(self.grace_auth, visibility="team")
        self.assertEqual((response.status_code, response.json().get("visibility")), (201, "team"), f"{VIEWS}: the answer")
        listed = APIClient().get("/api/v1/artifacts/", **self.admin_auth)
        self.assertIn(response.json()["id"], listed.content.decode(), f"{VIEWS}: the administrator lists the team file")

    def test_the_answer_says_what_the_team_rule_gave(self):
        self.setting("always_only_me")
        response = self.post(self.grace_auth, visibility="team")
        self.assertEqual((response.status_code, response.json().get("visibility")), (201, "only_me"), f"{VIEWS}: the rule")

    def test_a_bad_value_answers_400(self):
        response = self.post(self.grace_auth, visibility="public")
        expected = (400, {"error": REST_BAD, "code": "invalid_request", "detail": REST_BAD})
        self.assertEqual((response.status_code, response.json()), expected, f"{VIEWS}: refused as on_secret is")

    def test_the_put_answers_the_visibility_it_gave(self):
        pending = mint_upload_url(self.workspace, None, "a.txt", member=self.grace, visibility="team")
        response = self.client.put(f"/api/v1/uploads/{pending.token}", b"hi", content_type="application/octet-stream")
        self.assertEqual((response.status_code, response.json().get("visibility")), (201, "team"), f"{VIEWS}: the PUT")

    def test_with_the_switch_off_the_answer_is_mains(self):
        with override_settings(TEAM_SEATS=False):
            response = self.post(self.admin_auth, visibility="public")
        self.assertEqual(response.status_code, 201, f"{VIEWS}: ignored with the switch off")
        self.assertEqual(sorted(response.json()), MAIN_KEYS, f"{VIEWS}: main's keys, not {sorted(response.json())}")
