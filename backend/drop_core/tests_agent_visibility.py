"""Agents choose a file's visibility when they upload (team seats PR E2)."""

import base64
import json
import os
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from drop_core.models import APIKey, Artifact, PendingUpload, UsageCounter, Workspace, administrator_of, get_or_create_workspace
from drop_core.services import RejectedUpload, mint_upload_url, upload_artifact
from drop_core.tests_allowances import second_member
from drop_core.tests_members import make_user

SERVICES = "backend/drop_core/services.py"
VIEWS = "backend/drop_core/views.py"
MCP = "backend/drop_mcp/views.py"
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


MCP_DESCRIPTION = (
    'Who on your team can see the file on their Files page: "only_me" or "team". It is honored only when your '
    "administrator lets each person decide; otherwise the team's setting applies, and the answer says which visibility "
    "the file got. The link works for anyone it is sent to either way."
)
MCP_BAD = 'Send visibility as "only_me" or "team".'
HI = base64.b64encode(b"hi").decode()


class McpVisibilityTests(VisibilityBase):
    def setUp(self):
        super().setUp()
        _, self.grace_raw = APIKey.create_key(self.workspace, "grace-key", member=self.grace)

    def mcp(self, method, params=None):
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, **({"params": params} if params is not None else {})}
        response = self.client.post(
            "/mcp", data=json.dumps(payload), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {self.grace_raw}"
        )
        return response.json()

    def call(self, name, **arguments):
        return self.mcp("tools/call", {"name": name, "arguments": arguments})

    def result(self, reply):
        self.assertNotIn("isError", reply.get("result", {}), f"{MCP}: the tool succeeds, not {reply}")
        return json.loads(reply["result"]["content"][0]["text"])

    def schemas(self):
        return {tool["name"]: tool["inputSchema"] for tool in self.mcp("tools/list")["result"]["tools"]}

    def test_both_tools_list_the_visibility_property(self):
        schemas = self.schemas()
        for name in ("upload_artifact", "create_upload_url"):
            with self.subTest(tool=name):
                expected = {"type": "string", "description": MCP_DESCRIPTION}
                self.assertEqual(schemas[name]["properties"].get("visibility"), expected, f"{MCP}: {name}'s schema")
                self.assertNotIn("visibility", schemas[name]["required"], f"{MCP}: optional on {name}")

    def test_upload_artifact_honors_the_choice_and_says_so(self):
        result = self.result(self.call("upload_artifact", filename="a.txt", content_base64=HI, visibility="team"))
        self.assertEqual(result.get("visibility"), "team", f"{MCP}: the result says it")
        self.assertEqual(Artifact.objects.get(pk=result["id"]).visibility, "team", f"{MCP}: the file has it")

    def test_create_upload_url_records_the_choice(self):
        self.result(self.call("create_upload_url", filename="a.txt", visibility="team"))
        self.assertEqual(PendingUpload.objects.get().visibility, "team", f"{MCP}: the upload URL records it")

    def test_a_bad_value_is_a_32602_on_both_tools(self):
        for name, arguments in (("upload_artifact", {"filename": "a.txt", "content_base64": HI}), ("create_upload_url", {"filename": "a.txt"})):
            with self.subTest(tool=name):
                error = self.call(name, visibility="public", **arguments).get("error", {})
                self.assertEqual((error.get("code"), error.get("message")), (-32602, MCP_BAD), f"{MCP}: {name} refuses it")
        self.assertEqual((Artifact.objects.count(), PendingUpload.objects.count()), (0, 0), f"{MCP}: nothing is stored")

    def test_with_the_switch_off_the_tools_are_mains(self):
        with override_settings(TEAM_SEATS=False):
            schemas = self.schemas()
            reply = self.call("upload_artifact", filename="a.txt", content_base64=HI, visibility="public")
        for name in ("upload_artifact", "create_upload_url"):
            self.assertNotIn("visibility", schemas[name]["properties"], f"{MCP}: {name}'s schema is main's")
        self.assertNotIn("visibility", self.result(reply), f"{MCP}: the result is main's")
