"""Team seats: the switch, and the visibility a new file takes at upload.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it).
"""
import os
import tempfile
from unittest import mock

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from config.settings import team_seats_from

from .models import (
    FILE_VISIBILITY_ALWAYS_ONLY_ME,
    FILE_VISIBILITY_ALWAYS_TEAM,
    FILE_VISIBILITY_MEMBERS_CHOOSE,
    VISIBILITY_ONLY_ME,
    VISIBILITY_TEAM,
    Workspace,
    get_or_create_workspace,
)
from .services import upload_artifact
from .teams import team_seats_only
from .tests_members import make_user


class SwitchTests(SimpleTestCase):
    longMessage = False

    def test_it_is_off_unless_set_to_on(self):
        self.assertEqual(
            [team_seats_from(value) for value in (None, "", "off", "on")],
            [False, False, False, True],
            "backend/config/settings.py: team_seats_from must be true only for the value on, and the setting must read VEILFILE_TEAM_SEATS",
        )

    @override_settings(TEAM_SEATS=False)
    def test_a_view_behind_it_is_the_shells_404_signed_out_and_signed_in(self):
        ran = []

        def view(request):
            ran.append(request)
            return HttpResponse("secret")

        def login_required_stand_in(inner):
            return inner

        wrapped = team_seats_only(login_required_stand_in(view))
        for signed_in in (False, True):
            request = RequestFactory().get("/app/team/")
            request.user = mock.Mock(is_authenticated=signed_in)
            response = wrapped(request)
            body = response.content.decode()
            self.assertEqual(response.status_code, 404, "backend/drop_core/teams.py: team_seats_only must answer 404 while the switch is off")
            self.assertIn("<title>Not found | Veilfile</title>", body, "backend/drop_core/teams.py: the answer must be the shell's 404 page")
            self.assertNotIn("secret", body)
        self.assertEqual(ran, [], "backend/drop_core/teams.py: the wrapped view must not run while the switch is off")
        self.assertEqual(wrapped.__name__, "view", "backend/drop_core/teams.py: team_seats_only must use functools.wraps")

    @override_settings(TEAM_SEATS=True)
    def test_a_view_behind_it_runs_when_the_switch_is_on(self):
        request = RequestFactory().get("/app/team/")
        response = team_seats_only(lambda request: HttpResponse("ran"))(request)
        self.assertEqual(response.content, b"ran", "backend/drop_core/teams.py: team_seats_only must call the view while the switch is on")


class VisibilityAtUploadTests(TestCase):
    longMessage = False

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"DROP_LOCAL_DIR": tempfile.mkdtemp(prefix="veilfile-test-")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.workspace = get_or_create_workspace(make_user("ada"))

    def upload(self, workspace=None):
        return upload_artifact(workspace or self.workspace, None, "a.txt", b"hello", None)

    def set_setting(self, value):
        Workspace.objects.filter(pk=self.workspace.pk).update(file_visibility=value)

    def test_the_setting_starts_as_members_choose(self):
        self.assertEqual(Workspace.objects.get(pk=self.workspace.pk).file_visibility, FILE_VISIBILITY_MEMBERS_CHOOSE,
                         "backend/drop_core/models.py: file_visibility must default to members_choose")

    def test_a_new_file_starts_as_only_me_when_members_choose(self):
        self.assertEqual(self.upload().visibility, VISIBILITY_ONLY_ME, "backend/drop_core/services.py: members_choose must start a file as only me")

    def test_a_new_file_is_shared_when_the_team_always_shares(self):
        self.set_setting(FILE_VISIBILITY_ALWAYS_TEAM)
        self.assertEqual(self.upload().visibility, VISIBILITY_TEAM, "backend/drop_core/services.py: always_team must start a file as team")

    def test_a_new_file_is_only_me_when_the_team_never_shares(self):
        self.set_setting(FILE_VISIBILITY_ALWAYS_ONLY_ME)
        self.assertEqual(self.upload().visibility, VISIBILITY_ONLY_ME, "backend/drop_core/services.py: always_only_me must start a file as only me")

    def test_changing_the_setting_relabels_nothing(self):
        self.set_setting(FILE_VISIBILITY_ALWAYS_TEAM)
        shared = self.upload()
        self.set_setting(FILE_VISIBILITY_ALWAYS_ONLY_ME)
        private = self.upload()
        self.set_setting(FILE_VISIBILITY_ALWAYS_TEAM)
        shared.refresh_from_db()
        private.refresh_from_db()
        self.assertEqual((shared.visibility, private.visibility), (VISIBILITY_TEAM, VISIBILITY_ONLY_ME),
                         "backend/drop_core/services.py: a stored file's visibility must not follow the setting")

    def test_the_setting_is_read_under_the_lock(self):
        stale = Workspace.objects.get(pk=self.workspace.pk)
        self.set_setting(FILE_VISIBILITY_ALWAYS_TEAM)
        self.assertEqual(stale.file_visibility, FILE_VISIBILITY_MEMBERS_CHOOSE)
        self.assertEqual(self.upload(stale).visibility, VISIBILITY_TEAM,
                         "backend/drop_core/services.py: upload_artifact must read file_visibility from the locked row")
