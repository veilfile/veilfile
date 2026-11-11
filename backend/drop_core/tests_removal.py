"""Removal and leaving (team seats, ways out): what a person's row stops, what happens to their files, and the order of the locks.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it).
"""
import hashlib
import io
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from . import file_access, teams
from .key_caps import KeyNotAllowed, active_keys, create_key_within_cap
from .models import (
    APIKey,
    Artifact,
    DeviceCode,
    EmailVerification,
    MemberUsageCounter,
    Membership,
    PendingUpload,
    UsageCounter,
    VISIBILITY_ONLY_ME,
    VISIBILITY_TEAM,
    Workspace,
    administrator_of,
    current_member,
    current_membership,
)
from .oauth import resolve_oauth_credential
from .services import mint_upload_url, upload_artifact, workspace_storage_bytes
from .storage import LocalStorage
from .teams import SeatChangeRefused, leave_team, no_longer_on_team, remove_member
from .tests import _use_local_dir
from .tests_members import _issue_token, _make_app, make_user, second_member
from .tests_teams import team_of


class Base(TestCase):
    longMessage = False

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        _use_local_dir(self, self.tmpdir)
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.team, self.admin = team_of("ada")
        self.grace = second_member(self.team, "grace")
        self.key, self.raw = create_key_within_cap(self.team, "grace-key", member=self.grace)
        self.pending = PendingUpload.objects.create(
            workspace=self.team, api_key=self.key, member=self.grace, filename="a.txt", ttl_days=1,
            token="graces-url", expires_at=timezone.now() + timedelta(minutes=15),
        )

    def file(self, member, name, visibility=VISIBILITY_ONLY_ME, days=7, **fields):
        artifact = Artifact.objects.create(
            workspace=member.workspace, member=member, filename=name, size_bytes=5, visibility=visibility,
            expires_at=timezone.now() + timedelta(days=days), **fields,
        )
        LocalStorage().save(artifact.token, io.BytesIO(b"bytes"), "application/octet-stream")
        self.addCleanup(LocalStorage().delete, artifact.token)
        return artifact

    def approved_login(self, user, workspace):
        digest = hashlib.sha256(f"{user.pk}-{workspace.pk}".encode()).hexdigest()
        return DeviceCode.objects.create(
            user_code=f"U{user.pk}W{workspace.pk}"[:16], device_code_hash=digest, status=DeviceCode.STATUS_APPROVED,
            workspace=workspace, approved_by=user, expires_at=timezone.now() + timedelta(minutes=10),
        )

    def remove(self, revoke=False, caller=None, member=None):
        return remove_member(caller or self.admin, (member or self.grace).pk, revoke_files=revoke)

    def link(self, artifact):
        return self.client.get(f"/a/{artifact.token}")

    def put(self, token):
        return self.client.put(f"/api/v1/uploads/{token}", b"hi", content_type="application/octet-stream")


@override_settings(TEAM_SEATS=True)
class RemovalTests(Base):
    def test_removal_takes_its_steps_in_the_documented_order(self):
        from django.db.models.query import QuerySet

        calls = []
        real_deny, real_still, real_lock = teams._deny_device_logins, teams._still_administrator, QuerySet.select_for_update

        def deny(*args, **kwargs):
            calls.append("deny")
            return real_deny(*args, **kwargs)

        def still(*args, **kwargs):
            calls.append("still")
            return real_still(*args, **kwargs)

        def lock(queryset, *args, **kwargs):
            calls.append(f"lock:{queryset.model.__name__}")
            return real_lock(queryset, *args, **kwargs)

        with mock.patch.object(teams, "_deny_device_logins", deny), mock.patch.object(
            teams, "_still_administrator", still
        ), mock.patch.object(QuerySet, "select_for_update", lock):
            self.remove()
        self.assertEqual(calls[:3], ["deny", "lock:Workspace", "still"])

    def test_the_locks_on_rows_with_a_joined_person_lock_the_row_alone(self):
        from django.db.models.sql.compiler import SQLCompiler

        seen = []
        real = SQLCompiler.as_sql

        def spy(compiler, *args, **kwargs):
            query = compiler.query
            if query.select_for_update and query.select_related:
                seen.append(query.select_for_update_of)
            return real(compiler, *args, **kwargs)

        with mock.patch.object(SQLCompiler, "as_sql", spy):
            teams.offer_administrator(self.admin, self.grace.pk)
            teams.accept_administrator_offer(Membership.objects.get(pk=self.grace.pk))
            self.remove(member=Membership.objects.get(pk=self.admin.pk), caller=Membership.objects.get(pk=self.grace.pk))
        self.assertTrue(seen)
        self.assertEqual(set(seen), {("self",)})

    def test_removal_stops_everything_the_row_had(self):
        login = self.approved_login(self.grace.user, self.team)
        raw, _, _ = _issue_token(self.grace.user, _make_app())
        self.assertIsNotNone(resolve_oauth_credential(raw))
        self.assertEqual(active_keys(self.team).count(), 1)
        self.remove()
        self.assertIsNone(APIKey.verify(self.raw))
        self.assertEqual(self.put("graces-url").json()["code"], "not_found")
        login.refresh_from_db()
        self.assertEqual(login.status, DeviceCode.STATUS_DENIED)
        self.assertIsNone(resolve_oauth_credential(raw))
        self.assertEqual(active_keys(self.team).count(), 0)
        self.assertIsNotNone(Membership.objects.get(pk=self.grace.pk).removed_at)

    def test_a_refused_removal_denies_no_device_login(self):
        login = self.approved_login(self.grace.user, self.team)
        real = teams._deny_device_logins

        def deny_then_lose_the_row(workspace_id, user_id):
            real(workspace_id, user_id)
            Membership.objects.filter(pk=self.grace.pk).update(removed_at=timezone.now())

        with mock.patch.object(teams, "_deny_device_logins", deny_then_lose_the_row):
            with self.assertRaises(SeatChangeRefused) as caught:
                self.remove()
        self.assertEqual(caught.exception.code, "person_gone")
        login.refresh_from_db()
        self.assertEqual(login.status, DeviceCode.STATUS_APPROVED, "a refusal must roll the denial back")

    def test_a_suspended_team_refuses_removal_and_leaving(self):
        login = self.approved_login(self.grace.user, self.team)
        Workspace.objects.filter(pk=self.team.pk).update(suspended_at=timezone.now())
        with self.assertRaises(SeatChangeRefused) as caught:
            self.remove()
        self.assertEqual(caught.exception.code, "team_suspended")
        with self.assertRaises(SeatChangeRefused) as caught:
            leave_team(self.grace)
        self.assertEqual(caught.exception.code, "team_suspended")
        self.assertIsNone(Membership.objects.get(pk=self.grace.pk).removed_at)
        self.assertTrue(APIKey.objects.get(pk=self.key.pk).is_active)
        login.refresh_from_db()
        self.assertEqual(login.status, DeviceCode.STATUS_APPROVED)

    def test_removal_lets_files_run_out_with_the_first_choice(self):
        mine = self.file(self.grace, "mine.txt")
        shared = self.file(self.grace, "shared.txt", VISIBILITY_TEAM)
        before = {pk: expires for pk, expires in Artifact.objects.values_list("pk", "expires_at")}
        stored = workspace_storage_bytes(self.team)
        self.assertEqual(self.remove(revoke=False), 0)
        self.assertEqual({pk: expires for pk, expires in Artifact.objects.values_list("pk", "expires_at")}, before)
        self.assertEqual(self.link(mine).status_code, 200)
        self.assertEqual(self.link(shared).status_code, 200)
        self.assertEqual(workspace_storage_bytes(self.team), stored)

    def test_revoking_all_expires_every_live_file_of_the_person(self):
        mine = self.file(self.grace, "mine.txt")
        shared = self.file(self.grace, "shared.txt", VISIBILITY_TEAM)
        admins = self.file(self.admin, "admins.txt", VISIBILITY_TEAM)
        old = self.file(self.grace, "old.txt", days=-1)
        UsageCounter.objects.create(workspace=self.team, year_month="2026-11", uploads=3)
        MemberUsageCounter.objects.create(member=self.grace, year_month="2026-11", uploads=2)
        counters = (list(UsageCounter.objects.values_list("uploads")), list(MemberUsageCounter.objects.values_list("uploads")))
        self.assertEqual(workspace_storage_bytes(self.team), 15)
        self.assertEqual(self.remove(revoke=True), 2)
        self.assertEqual(self.link(mine).status_code, 404)
        self.assertEqual(self.link(shared).status_code, 404)
        self.assertEqual(self.link(admins).status_code, 200)
        self.assertEqual(workspace_storage_bytes(self.team), 5)
        self.assertLess(Artifact.objects.get(pk=old.pk).expires_at, timezone.now())
        self.assertEqual(
            (list(UsageCounter.objects.values_list("uploads")), list(MemberUsageCounter.objects.values_list("uploads"))), counters
        )

    def test_revoking_all_spares_held_files(self):
        held = self.file(self.grace, "held.txt", safety_status="held")
        live = self.file(self.grace, "live.txt")
        expires = held.expires_at
        self.assertEqual(self.remove(revoke=True), 1)
        self.assertEqual(Artifact.objects.get(pk=held.pk).expires_at, expires)
        self.assertLess(Artifact.objects.get(pk=live.pk).expires_at, timezone.now() + timedelta(seconds=1))

    def test_the_administrator_and_another_team_are_refused(self):
        other, other_admin = team_of("kay")
        stranger = second_member(other, "lin")
        closed = second_member(self.team, "mo")
        Membership.objects.filter(pk=closed.pk).update(removed_at=timezone.now())
        login = self.approved_login(self.grace.user, self.team)
        for member_id in (self.admin.pk, closed.pk, stranger.pk):
            with self.assertRaises(SeatChangeRefused) as caught:
                remove_member(self.admin, member_id, revoke_files=True)
            self.assertEqual(caught.exception.code, "person_gone")
        self.assertIsNone(Membership.objects.get(pk=self.admin.pk).removed_at)
        self.assertIsNone(Membership.objects.get(pk=stranger.pk).removed_at)
        self.assertTrue(APIKey.objects.get(pk=self.key.pk).is_active)
        login.refresh_from_db()
        self.assertEqual(login.status, DeviceCode.STATUS_APPROVED)

    def test_a_caller_who_is_no_longer_administrator_cannot_remove(self):
        stale = Membership.objects.get(pk=self.admin.pk)
        Membership.objects.filter(pk=self.admin.pk).update(role=Membership.MEMBER)
        login = self.approved_login(self.grace.user, self.team)
        with self.assertRaises(PermissionDenied):
            remove_member(stale, self.grace.pk, revoke_files=True)
        self.assertIsNone(Membership.objects.get(pk=self.grace.pk).removed_at)
        self.assertTrue(APIKey.objects.get(pk=self.key.pk).is_active)
        login.refresh_from_db()
        self.assertEqual(login.status, DeviceCode.STATUS_APPROVED)

    def test_a_member_cannot_remove(self):
        lin = second_member(self.team, "lin")
        with self.assertRaises(PermissionDenied):
            remove_member(self.grace, lin.pk, revoke_files=False)

    def test_removing_twice_raises_person_gone(self):
        self.remove()
        with self.assertRaises(SeatChangeRefused) as caught:
            self.remove()
        self.assertEqual(caught.exception.code, "person_gone")

    def test_removal_works_on_a_free_team_and_for_an_unverified_person(self):
        free, free_admin = team_of("kay", plan=Workspace.PLAN_FREE)
        mo = second_member(free, "mo")
        EmailVerification.objects.filter(user=mo.user).update(email="new@example.com", is_verified=False)
        self.assertEqual(remove_member(free_admin, mo.pk, revoke_files=False), 0)
        self.assertIsNotNone(Membership.objects.get(pk=mo.pk).removed_at)
        lin = second_member(free, "lin")
        leave_team(lin)
        self.assertIsNotNone(Membership.objects.get(pk=lin.pk).removed_at)

    def test_removal_ends_a_waiting_offer_to_that_person(self):
        teams.offer_administrator(self.admin, self.grace.pk)
        self.remove()
        self.assertIsNone(Membership.objects.get(pk=self.grace.pk).offered_administrator_at)
        self.assertIsNone(teams.waiting_administrator_offer(self.team))


@override_settings(TEAM_SEATS=True)
class InFlightTests(Base):
    def test_an_upload_in_flight_is_refused_after_removal(self):
        stale = current_member(self.team, self.grace.user)
        self.remove()
        with self.assertRaisesMessage(ValueError, "member_removed"):
            upload_artifact(self.team, None, "a.txt", b"hi", "text/plain", member=stale)
        self.assertFalse(Artifact.objects.filter(filename="a.txt").exists())
        self.assertFalse(UsageCounter.objects.filter(uploads__gt=0).exists())
        self.assertFalse(MemberUsageCounter.objects.filter(uploads__gt=0).exists())

    def test_a_key_mint_in_flight_is_refused_after_removal(self):
        stale = current_member(self.team, self.grace.user)
        self.remove()
        with self.assertRaises(KeyNotAllowed) as caught:
            create_key_within_cap(self.team, "late", member=stale)
        self.assertEqual(caught.exception.code, "member_removed")
        self.assertFalse(APIKey.objects.filter(name="late").exists())


@override_settings(TEAM_SEATS=True)
class LeavingTests(Base):
    def test_leaving_is_removal_with_files_kept(self):
        login = self.approved_login(self.grace.user, self.team)
        mine = self.file(self.grace, "mine.txt")
        expires = mine.expires_at
        raw, _, _ = _issue_token(self.grace.user, _make_app())
        self.assertIsNone(leave_team(self.grace))
        self.assertIsNotNone(Membership.objects.get(pk=self.grace.pk).removed_at)
        self.assertIsNone(APIKey.verify(self.raw))
        self.assertEqual(self.put("graces-url").json()["code"], "not_found")
        login.refresh_from_db()
        self.assertEqual(login.status, DeviceCode.STATUS_DENIED)
        self.assertIsNone(resolve_oauth_credential(raw))
        self.assertEqual(Artifact.objects.get(pk=mine.pk).expires_at, expires)
        self.assertEqual(self.link(mine).status_code, 200)

    def test_an_administrator_with_people_cannot_leave(self):
        with self.assertRaises(SeatChangeRefused) as caught:
            leave_team(self.admin)
        self.assertEqual(caught.exception.code, "administrator_has_people")
        self.assertIsNone(Membership.objects.get(pk=self.admin.pk).removed_at)

    def test_a_person_on_their_own_cannot_leave(self):
        own = current_membership(make_user("kay"))
        with self.assertRaises(SeatChangeRefused) as caught:
            leave_team(own)
        self.assertEqual(caught.exception.code, "not_on_a_team")
        self.assertIsNone(Membership.objects.get(pk=own.pk).removed_at)

    def test_leaving_twice_says_not_on_a_team(self):
        leave_team(self.grace)
        with self.assertRaises(SeatChangeRefused) as caught:
            leave_team(self.grace)
        self.assertEqual(caught.exception.code, "not_on_a_team")


@override_settings(TEAM_SEATS=True)
class WholePathTests(Base):
    def go_through(self, way_out):
        person = make_user("kay")
        own = current_membership(person)
        old_key, old_raw = create_key_within_cap(own.workspace, "old", member=own)
        old_file = self.file(own, "old.txt")
        sent = []
        teams.invite(self.admin, person.email, build_url=lambda raw: sent.append(raw) or raw)
        row = teams.accept_invitation(person, sent[0])
        self.assertEqual(current_membership(person), row)
        key, raw = create_key_within_cap(self.team, "team-key", member=row)
        way_out(self.admin, row)
        self.assertIsNone(APIKey.verify(raw))
        back = current_membership(person)
        self.assertEqual((back.workspace_id, back.workspace.plan, back.role), (own.workspace_id, Workspace.PLAN_FREE, Membership.ADMINISTRATOR))
        self.assertIsNone(APIKey.verify(old_raw), "the old keys stay stopped")
        self.assertIn(old_file.pk, file_access.files_visible_to(back).values_list("pk", flat=True))
        Membership.objects.filter(pk=row.pk).update(upload_allowance=1, key_allowance=1)
        teams.invite(self.admin, person.email, build_url=lambda raw: sent.append(raw) or raw)
        again = teams.accept_invitation(person, sent[1])
        self.assertEqual(again.pk, row.pk)
        self.assertEqual((again.upload_allowance, again.storage_allowance_bytes, again.key_allowance), (None, None, None))
        self.assertIsNone(again.removed_at)

    def test_the_whole_path_from_a_real_join_and_leaving(self):
        self.go_through(lambda admin, row: leave_team(row))

    def test_the_whole_path_from_a_real_join_and_removal(self):
        self.go_through(lambda admin, row: remove_member(admin, row.pk, revoke_files=False))


@override_settings(TEAM_SEATS=True)
class NoLongerOnTeamTests(Base):
    def test_no_longer_on_team_lasts_seven_days(self):
        own = current_membership(self.grace.user)
        self.assertEqual(own, self.grace)
        self.assertFalse(no_longer_on_team(self.grace))
        leave_team(self.grace)
        back = current_membership(self.grace.user)
        self.assertTrue(no_longer_on_team(back))
        Membership.objects.filter(pk=self.grace.pk).update(removed_at=timezone.now() - timedelta(days=6))
        self.assertTrue(no_longer_on_team(back))
        Membership.objects.filter(pk=self.grace.pk).update(removed_at=timezone.now() - timedelta(days=8))
        self.assertFalse(no_longer_on_team(back))

    def test_it_is_false_for_someone_who_never_left_and_for_someone_back_on_a_team(self):
        self.assertFalse(no_longer_on_team(administrator_of(self.team)))
        self.assertFalse(no_longer_on_team(current_membership(make_user("kay"))))
        leave_team(self.grace)
        sent = []
        teams.invite(self.admin, self.grace.user.email, build_url=lambda raw: sent.append(raw) or raw)
        row = teams.accept_invitation(self.grace.user, sent[0])
        self.assertFalse(no_longer_on_team(row))


@override_settings(TEAM_SEATS=True)
class InactiveMemberTests(Base):
    def test_an_inactive_members_upload_url_dies(self):
        app = _make_app()
        token, _, _ = _issue_token(self.grace.user, app)
        by_grant = mint_upload_url(self.team, None, "b.txt", oauth_application_id=app.pk, member=self.grace)
        # While the person is active, a URL of each kind takes a file.
        working_key_url = PendingUpload.objects.create(
            workspace=self.team, api_key=self.key, member=self.grace, filename="c.txt", ttl_days=1,
            token="active-key-url", expires_at=timezone.now() + timedelta(minutes=15),
        )
        working_grant_url = mint_upload_url(self.team, None, "d.txt", oauth_application_id=app.pk, member=self.grace)
        for url in (working_key_url.token, working_grant_url.token):
            answer = self.put(url)
            self.assertEqual(answer.status_code, 201, answer.content)
        self.assertEqual(Artifact.objects.count(), 2)
        Artifact.objects.all().delete()
        type(self.grace.user).objects.filter(pk=self.grace.user_id).update(is_active=False)
        unknown = self.put("not-a-token")
        for url in (self.pending.token, by_grant.token):
            answer = self.put(url)
            self.assertEqual((answer.status_code, answer.json()), (unknown.status_code, unknown.json()))
        self.assertFalse(Artifact.objects.exists())


class SwitchOffTests(Base):
    def test_with_the_switch_off_the_helpers_ask_nothing(self):
        with override_settings(TEAM_SEATS=False), CaptureQueriesContext(connection) as queries:
            with self.assertRaises(PermissionDenied):
                remove_member(self.admin, self.grace.pk, revoke_files=True)
            with self.assertRaises(SeatChangeRefused):
                leave_team(self.grace)
            self.assertFalse(no_longer_on_team(self.admin))
        self.assertEqual(len(queries), 0)
        self.assertIsNone(Membership.objects.get(pk=self.grace.pk).removed_at)
