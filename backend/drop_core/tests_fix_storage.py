"""Regression tests for the storage cluster: revoke/cascade/sweep never lose
track of stored bytes, storage failures are visible, the lazy sweep is
bounded, the S3/R2 backend is covered without network, and the secret
scanner is accurate and linear."""

import datetime
import io
import json
import os
import shutil
import tempfile
import time
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from drop_core.models import (
    APIKey,
    Artifact,
    EmailVerification,
    LoopHeartbeat,
    UsageCounter,
    get_or_create_workspace,
)
from drop_core.services import revoke_artifact, upload_artifact, workspace_storage_bytes
from drop_core.storage import LocalStorage

User = get_user_model()

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


class FailingDeleteStorage(LocalStorage):
    def delete(self, token):
        raise ConnectionError(f"R2 503 for https://r2.example/bucket/{token}")


class FailingSaveStorage(LocalStorage):
    def save(self, token, fileobj, mime_type, metadata=None):
        raise ConnectionError(f"R2 503 for https://r2.example/bucket/{token}")


class FailingOpenStorage(LocalStorage):
    def open(self, token):
        raise ConnectionError(f"R2 credential invalid for /bucket/{token}")


class MissingOpenStorage(LocalStorage):
    def open(self, token):
        raise FileNotFoundError("artifact object not found")


class SpyStorage(LocalStorage):
    """LocalStorage that records every call."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deleted = []
        self.batches = []
        self.saved = []

    def save(self, token, fileobj, mime_type, metadata=None):
        self.saved.append((token, metadata))
        super().save(token, fileobj, mime_type)

    def delete(self, token):
        self.deleted.append(token)
        super().delete(token)

    def delete_many(self, tokens):
        self.batches.append(list(tokens))
        return super().delete_many(tokens)


class FailingSaveSpyStorage(SpyStorage):
    def save(self, token, fileobj, mime_type, metadata=None):
        raise OSError("disk full")


class SlowDeleteStorage(SpyStorage):
    delay = 0.02

    def delete(self, token):
        time.sleep(self.delay)
        super().delete(token)


@contextmanager
def use_storage(storage):
    """Route every get_storage() caller to ``storage``."""
    with ExitStack() as stack:
        for target in (
            "drop_core.storage.get_storage",
            "drop_core.services.get_storage",
            "drop_core.views.get_storage",
        ):
            stack.enter_context(mock.patch(target, return_value=storage))
        yield storage


class _Base(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-fix-storage-")
        env = mock.patch.dict(
            os.environ, {"DROP_LOCAL_DIR": self.tmpdir, "DROP_STORAGE": "local"}
        )
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        cache.clear()
        self.addCleanup(cache.clear)
        self._hold_lazy_sweep()
        self.user = User.objects.create_user(username="owner", password="pw12345678")
        self.ws = get_or_create_workspace(self.user)
        self.key, self.raw_key = APIKey.create_key(self.ws, "agent")
        EmailVerification.objects.create(
            user=self.user,
            email="owner@example.com",
            is_verified=True,
            verified_at=timezone.now(),
        )
        self.api = APIClient()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.raw_key}"}

    def _mcp_tool(self, name, arguments):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        response = self.api.post("/mcp", payload, format="json", **self.auth)
        self.assertEqual(response.status_code, 200)
        return json.loads(response.json()["result"]["content"][0]["text"])

    def _hold_lazy_sweep(self):
        # Mark a sweep as just run so uploads don't trigger one.
        cache.set("drop_last_sweep_ts", time.time(), 3600)

    def _upload(self, data=b"hello veilfile", name="note.txt"):
        return upload_artifact(self.ws, self.key, name, data, "text/plain")

    def _stored(self, artifact_or_token):
        token = getattr(artifact_or_token, "token", artifact_or_token)
        return os.path.exists(os.path.join(self.tmpdir, token))

    def _expired_rows(self, n, with_files=True):
        rows = []
        for i in range(n):
            artifact = Artifact.objects.create(
                workspace=self.ws,
                filename=f"old{i}.txt",
                size_bytes=3,
                expires_at=timezone.now() - timedelta(days=1, seconds=n - i),
            )
            if with_files:
                LocalStorage().save(artifact.token, io.BytesIO(b"old"), "text/plain")
            rows.append(artifact)
        return rows


# ---------------------------------------------------------------------------
# revoke (upl-9, mcp-5, ops-3)
# ---------------------------------------------------------------------------


class RevokeTests(_Base):
    def test_storage_failure_keeps_row_expired_and_logs_artifact_id(self):
        artifact = self._upload()
        with use_storage(FailingDeleteStorage()):
            with self.assertLogs("drop_core.services", "ERROR") as logs:
                result = revoke_artifact(self.ws, artifact.id)
        self.assertTrue(result)
        self.assertIn(f"id={artifact.id}", "\n".join(logs.output))
        # The bytes are still tracked by a row the sweeper will retry...
        row = Artifact.objects.get(id=artifact.id)
        self.assertTrue(row.is_expired())
        self.assertTrue(self._stored(artifact))
        # ...but the link is dead and the space is freed for the cap.
        self.assertEqual(self.api.get(f"/a/{artifact.token}").status_code, 404)
        self.assertEqual(workspace_storage_bytes(self.ws), 0)

    def test_failed_revoke_is_retried_by_sweep(self):
        artifact = self._upload()
        with use_storage(FailingDeleteStorage()):
            revoke_artifact(self.ws, artifact.id)
        out = io.StringIO()
        call_command("sweep_expired", stdout=out)
        self.assertIn("swept=1", out.getvalue())
        self.assertFalse(Artifact.objects.filter(id=artifact.id).exists())
        self.assertFalse(self._stored(artifact))

    def test_failed_revoke_can_be_retried_by_revoking_again(self):
        artifact = self._upload()
        with use_storage(FailingDeleteStorage()):
            revoke_artifact(self.ws, artifact.id)
        self.assertIs(revoke_artifact(self.ws, artifact.id), True)
        self.assertFalse(Artifact.objects.filter(id=artifact.id).exists())
        self.assertFalse(self._stored(artifact))

    def test_link_is_dead_before_the_storage_call(self):
        artifact = self._upload()
        seen = []

        class Probe(LocalStorage):
            def delete(self, token):
                seen.append(Artifact.objects.get(token=token).is_expired())
                super().delete(token)

        with use_storage(Probe()):
            revoke_artifact(self.ws, artifact.id)
        self.assertEqual(seen, [True])

    def test_success_deletes_once_and_logs_nothing(self):
        artifact = self._upload()
        spy = SpyStorage()
        with use_storage(spy), self.captureOnCommitCallbacks(execute=True):
            with self.assertNoLogs("drop_core", "WARNING"):
                self.assertIs(revoke_artifact(self.ws, artifact.id), True)
        self.assertEqual(spy.deleted, [artifact.token])
        self.assertFalse(Artifact.objects.filter(id=artifact.id).exists())
        self.assertFalse(self._stored(artifact))

    def test_rest_delete_with_storage_down_returns_204_and_link_dies(self):
        artifact = self._upload()
        with use_storage(FailingDeleteStorage()):
            response = self.api.delete(f"/api/v1/artifacts/{artifact.id}/", **self.auth)
        self.assertEqual(response.status_code, 204)
        self.assertTrue(Artifact.objects.get(id=artifact.id).is_expired())
        self.assertEqual(self.api.get(f"/a/{artifact.token}").status_code, 404)

    def test_mcp_revoke_reports_storage_not_deleted(self):
        artifact = self._upload()
        with use_storage(FailingDeleteStorage()):
            result = self._mcp_tool("revoke_artifact", {"id": str(artifact.id)})
        self.assertEqual(result, {"revoked": True, "storage_deleted": False})
        other = self._upload(name="b.txt")
        self.assertEqual(
            self._mcp_tool("revoke_artifact", {"id": str(other.id)}), {"revoked": True}
        )


# ---------------------------------------------------------------------------
# admin / cascade deletes (upl-10, ctr-9, auth-11 storage part)
# ---------------------------------------------------------------------------


class CascadeDeleteTests(_Base):
    def test_user_delete_removes_bytes(self):
        a, b = self._upload(name="a.txt"), self._upload(name="b.txt")
        with self.captureOnCommitCallbacks(execute=True):
            self.user.delete()
        self.assertFalse(Artifact.objects.exists())
        self.assertFalse(self._stored(a))
        self.assertFalse(self._stored(b))

    def test_workspace_delete_removes_bytes(self):
        artifact = self._upload()
        with self.captureOnCommitCallbacks(execute=True):
            self.ws.delete()
        self.assertFalse(self._stored(artifact))

    def test_queryset_delete_removes_bytes(self):
        artifact = self._upload()
        with self.captureOnCommitCallbacks(execute=True):
            Artifact.objects.filter(workspace=self.ws).delete()
        self.assertFalse(self._stored(artifact))

    def test_bytes_survive_until_commit_and_a_rollback(self):
        artifact = self._upload()

        class Boom(Exception):
            pass

        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    Artifact.objects.filter(id=artifact.id).delete()
                    # Not deleted inside the transaction...
                    self.assertTrue(self._stored(artifact))
                    raise Boom
            except Boom:
                pass
        # ...and never deleted when it rolls back.
        self.assertTrue(Artifact.objects.filter(id=artifact.id).exists())
        self.assertTrue(self._stored(artifact))

    def test_admin_delete_selected_removes_bytes(self):
        artifact = self._upload()
        admin = User.objects.create_superuser("root", "root@example.com", "pw12345678")
        self.client.force_login(admin)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/admin/drop_core/artifact/",
                {
                    "action": "delete_selected",
                    "_selected_action": [str(artifact.id)],
                    "post": "yes",
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Artifact.objects.filter(id=artifact.id).exists())
        self.assertFalse(self._stored(artifact))

    def test_admin_user_delete_removes_bytes(self):
        artifact = self._upload()
        admin = User.objects.create_superuser("root", "root@example.com", "pw12345678")
        self.client.force_login(admin)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/admin/auth/user/{self.user.pk}/delete/", {"post": "yes"}
            )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self._stored(artifact))

    def test_storage_failure_is_logged_with_id_and_key_and_delete_completes(self):
        artifact = self._upload()
        with use_storage(FailingDeleteStorage()):
            with self.assertLogs("drop_core.signals", "ERROR") as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    self.ws.delete()
        output = "\n".join(logs.output)
        self.assertIn(f"id={artifact.id}", output)
        self.assertIn(f"key={artifact.token}", output)
        self.assertFalse(Artifact.objects.exists())

    def test_already_missing_object_is_not_an_error(self):
        artifact = self._upload()
        os.remove(os.path.join(self.tmpdir, artifact.token))
        with self.assertNoLogs("drop_core", "WARNING"):
            with self.captureOnCommitCallbacks(execute=True):
                artifact.delete()

    def test_sweep_does_not_delete_twice(self):
        (artifact,) = self._expired_rows(1)
        spy = SpyStorage()
        with use_storage(spy), self.captureOnCommitCallbacks(execute=True):
            call_command("sweep_expired", stdout=io.StringIO())
        self.assertEqual(spy.deleted, [artifact.token])

    def test_cascade_deletes_in_one_batched_call_and_logs_the_count_first(self):
        artifacts = [self._upload(name=f"{i}.txt") for i in range(5)]
        spy = SpyStorage()
        with use_storage(spy), self.assertLogs("drop_core.signals", "INFO") as logs:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                self.ws.delete()
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(spy.batches), 1)
        self.assertCountEqual(spy.batches[0], [a.token for a in artifacts])
        self.assertIn("deleting stored bytes of 5 deleted artifacts", logs.output[0])
        for artifact in artifacts:
            self.assertFalse(self._stored(artifact))

    def test_rolled_back_savepoint_keeps_its_bytes_out_of_the_batch(self):
        gone, kept, also_gone = (self._upload(name=f"{n}.txt") for n in ("g", "k", "a"))

        class Boom(Exception):
            pass

        spy = SpyStorage()
        with use_storage(spy), self.captureOnCommitCallbacks(execute=True):
            Artifact.objects.filter(id=gone.id).delete()
            try:
                with transaction.atomic():
                    Artifact.objects.filter(id=kept.id).delete()
                    raise Boom
            except Boom:
                pass
            # Joins the first batch again: same savepoint stack.
            Artifact.objects.filter(id=also_gone.id).delete()
        self.assertEqual(spy.batches, [[gone.token, also_gone.token]])
        self.assertTrue(Artifact.objects.filter(id=kept.id).exists())
        self.assertTrue(self._stored(kept))
        self.assertFalse(self._stored(gone))
        self.assertFalse(self._stored(also_gone))

    def test_deletes_in_separate_capture_blocks_are_not_merged(self):
        first, second = self._upload(name="1.txt"), self._upload(name="2.txt")
        spy = SpyStorage()
        with use_storage(spy):
            with self.captureOnCommitCallbacks(execute=True):
                first.delete()
            with self.captureOnCommitCallbacks(execute=True):
                second.delete()
        self.assertEqual(spy.batches, [[first.token], [second.token]])
        self.assertFalse(self._stored(second))

    def test_failure_logs_are_capped(self):
        from drop_core import signals

        for i in range(4):
            self._upload(name=f"{i}.txt")
        with use_storage(FailingDeleteStorage()), mock.patch.object(
            signals, "_MAX_FAILURE_LOGS", 2
        ):
            with self.assertLogs("drop_core.signals", "ERROR") as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    self.ws.delete()
        self.assertEqual(len(logs.output), 3)
        self.assertIn("2 more deleted artifacts", logs.output[-1])

    def test_backend_without_delete_many_still_works(self):
        artifact = self._upload()
        deleted = []

        class Minimal:
            def delete(self, token):
                deleted.append(token)

        with use_storage(Minimal()), self.captureOnCommitCallbacks(execute=True):
            artifact.delete()
        self.assertEqual(deleted, [artifact.token])


# ---------------------------------------------------------------------------
# sweep reporting (ops-4) and bounded lazy sweep (upl-13, ops-6)
# ---------------------------------------------------------------------------


class SweepTests(_Base):
    def test_command_reports_failures_and_exits_nonzero(self):
        self._expired_rows(2)
        out = io.StringIO()
        with use_storage(FailingDeleteStorage()):
            with self.assertLogs("drop_core.sweep", "ERROR"):
                with self.assertRaises(CommandError):
                    call_command("sweep_expired", stdout=out)
        self.assertIn("swept=0", out.getvalue())
        self.assertIn("failed=2", out.getvalue())
        self.assertEqual(Artifact.objects.count(), 2)

    def test_command_success_reports_zero_failures(self):
        self._expired_rows(1)
        out = io.StringIO()
        call_command("sweep_expired", stdout=out)
        self.assertEqual(out.getvalue().strip(), "swept=1 bytes=3 failed=0")

    def test_command_can_be_bounded(self):
        self._expired_rows(5)
        out = io.StringIO()
        call_command("sweep_expired", "--limit", "2", stdout=out)
        self.assertEqual(
            out.getvalue().strip(),
            "swept=2 bytes=6 failed=0 (stopped early; the next run continues)",
        )
        self.assertEqual(Artifact.objects.count(), 3)
        # A fake clock that advances one second per storage delete, so the
        # budget does not depend on how fast the queries run.
        clock = {"now": 100.0}

        class Ticking(SpyStorage):
            def delete(self, token):
                clock["now"] += 1.0
                super().delete(token)

        ticking = Ticking()
        with use_storage(ticking), mock.patch("drop_core.sweep.time") as fake_time:
            fake_time.monotonic.side_effect = lambda: clock["now"]
            # The command records its heartbeat with time.time().
            fake_time.time.side_effect = time.time
            call_command("sweep_expired", "--time-budget", "1.5", stdout=io.StringIO())
        self.assertEqual(len(ticking.deleted), 2)

    def test_sweep_result_counts(self):
        from drop_core.sweep import sweep_expired

        self._expired_rows(3)
        result = sweep_expired()
        self.assertEqual((result.swept, result.freed, result.failed), (3, 9, 0))
        self.assertTrue(result.complete)

    def test_lazy_sweep_is_batched_and_comes_back_soon(self):
        from drop_core import sweep

        self._expired_rows(7)
        cache.clear()
        with mock.patch.object(sweep, "LAZY_SWEEP_BATCH_SIZE", 3):
            sweep.maybe_sweep()
            self.assertEqual(Artifact.objects.count(), 4)
            # Not on the very next upload...
            sweep.maybe_sweep()
            self.assertEqual(Artifact.objects.count(), 4)
            # ...but once the short retry interval has passed, not in 24h.
            later = time.time() + sweep.LAZY_SWEEP_RETRY_SECONDS + 1
            with mock.patch.object(sweep.time, "time", return_value=later):
                sweep.maybe_sweep()
            self.assertEqual(Artifact.objects.count(), 1)

    def test_lazy_sweep_respects_time_budget(self):
        from drop_core import sweep

        self._expired_rows(40)
        cache.clear()
        slow = SlowDeleteStorage()
        slow.delay = 0.03
        started = time.monotonic()
        with use_storage(slow), mock.patch.object(
            sweep, "LAZY_SWEEP_TIME_BUDGET_SECONDS", 0.1
        ):
            sweep.maybe_sweep()
        self.assertLess(time.monotonic() - started, 0.6)
        self.assertLess(len(slow.deleted), 10)

    def test_lazy_sweep_stops_at_first_storage_failure(self):
        from drop_core import sweep

        self._expired_rows(5)
        cache.clear()
        calls = []

        class Down(LocalStorage):
            def delete(self, token):
                calls.append(token)
                raise ConnectionError("down")

        with use_storage(Down()), self.assertLogs("drop_core.sweep", "WARNING"):
            sweep.maybe_sweep()
        self.assertEqual(len(calls), 1)
        self.assertEqual(Artifact.objects.count(), 5)

    def test_lazy_sweep_claims_its_slot_before_sweeping(self):
        from drop_core import sweep

        self._expired_rows(1)
        cache.clear()
        seen = []

        class Probe(LocalStorage):
            def delete(self, token):
                seen.append(cache.get(sweep._CACHE_KEY))
                super().delete(token)

        with use_storage(Probe()):
            sweep.maybe_sweep()
        self.assertEqual(len(seen), 1)
        self.assertIsNotNone(seen[0])

    def test_upload_is_not_held_up_by_a_large_backlog(self):
        self._expired_rows(120, with_files=False)
        cache.clear()
        slow = SlowDeleteStorage()
        with use_storage(slow):
            artifact = self._upload()
        self.assertTrue(self._stored(artifact))
        # One bounded batch at most, not the whole backlog.
        self.assertLessEqual(len(slow.deleted), 50)
        self.assertGreaterEqual(
            Artifact.objects.filter(expires_at__lte=timezone.now()).count(), 70
        )


# ---------------------------------------------------------------------------
# background-loop heartbeat and missing-pass watcher (R-02 Option A)
# ---------------------------------------------------------------------------


class LoopPassWatcherTests(_Base):
    def _at(self, ts):
        from drop_core import sweep

        return mock.patch.object(sweep.time, "time", return_value=ts)

    def _pass_at(self, ts):
        """Record a loop pass at ``ts``, as the command does."""
        from drop_core import sweep

        with self._at(ts):
            sweep.record_loop_pass()

    def _last_pass(self):
        return LoopHeartbeat.objects.get().last_pass.timestamp()

    def test_command_records_and_refreshes_the_heartbeat(self):
        from drop_core import sweep

        first = time.time()
        later = first + sweep.SWEEP_INTERVAL_SECONDS
        lazy_marker = cache.get(sweep._CACHE_KEY)
        self.assertFalse(LoopHeartbeat.objects.exists())
        with self._at(first):
            call_command("sweep_expired", stdout=io.StringIO())
        self.assertAlmostEqual(self._last_pass(), first, places=5)
        with self._at(later):
            call_command("sweep_expired", stdout=io.StringIO())
        self.assertAlmostEqual(self._last_pass(), later, places=5)
        # One row, updated in place; the lazy sweep's cache marker is left alone.
        self.assertEqual(LoopHeartbeat.objects.count(), 1)
        self.assertEqual(cache.get(sweep._CACHE_KEY), lazy_marker)

    def test_a_cleared_cache_does_not_lose_the_heartbeat(self):
        # The heartbeat used to be a cache key, which a cull of the cache
        # table (MAX_ENTRIES) or a cleared cache dropped without a trace.
        from drop_core import sweep

        call_command("sweep_expired", stdout=io.StringIO())
        cache.clear()
        with self.assertNoLogs("drop_core.sweep", "WARNING"):
            sweep.maybe_sweep()

    def test_a_pass_with_failed_deletes_still_records_the_heartbeat(self):
        self._expired_rows(1)
        with use_storage(FailingDeleteStorage()):
            with self.assertLogs("drop_core.sweep", "ERROR"):
                with self.assertRaises(CommandError):
                    call_command("sweep_expired", stdout=io.StringIO())
        self.assertTrue(LoopHeartbeat.objects.exists())

    def test_a_pass_that_fails_then_succeeds_sweeps_the_good_row_and_keeps_the_bad(self):
        # The oldest row is visited first; its delete fails, the next one's
        # succeeds, and the pass still finishes and records the heartbeat.
        bad, good = self._expired_rows(2)

        class FailsForBad(LocalStorage):
            def delete(self, token):
                if token == bad.token:
                    raise ConnectionError("R2 503")
                super().delete(token)

        now = time.time()
        out = io.StringIO()
        with self._at(now), use_storage(FailsForBad()):
            with self.assertLogs("drop_core.sweep", "ERROR"):
                with self.assertRaises(CommandError):
                    call_command("sweep_expired", stdout=out)
        self.assertEqual(out.getvalue().strip(), "swept=1 bytes=3 failed=1")
        self.assertFalse(Artifact.objects.filter(id=good.id).exists())
        self.assertFalse(self._stored(good))
        self.assertTrue(Artifact.objects.filter(id=bad.id).exists())
        self.assertTrue(self._stored(bad))
        self.assertAlmostEqual(self._last_pass(), now, places=5)

    def test_fresh_heartbeat_logs_nothing(self):
        from drop_core import sweep

        now = time.time()
        self._pass_at(now - 30 * 60 * 60)
        with self.assertNoLogs("drop_core.sweep", "WARNING"):
            sweep.maybe_sweep()

    def test_stale_heartbeat_logs_an_error(self):
        from drop_core import sweep

        now = time.time()
        self._pass_at(now - 37 * 60 * 60)
        with self._at(now), self.assertLogs("drop_core.sweep", "WARNING") as logs:
            sweep.maybe_sweep()
        self.assertEqual(
            [r.levelname for r in logs.records], ["ERROR"], logs.output
        )
        self.assertIn("missed a pass", logs.output[0])
        self.assertIn("37.0h", logs.output[0])

    def test_heartbeat_older_than_four_days_still_logs_an_error(self):
        # An expiring heartbeat downgraded a long outage to a WARNING once it
        # expired; the row never expires, so the stale value keeps logging ERROR.
        from drop_core import sweep

        now = time.time()
        self._pass_at(now - 100 * 60 * 60)
        with self._at(now), self.assertLogs("drop_core.sweep", "WARNING") as logs:
            sweep.maybe_sweep()
        self.assertEqual(
            [r.levelname for r in logs.records], ["ERROR"], logs.output
        )
        self.assertIn("missed a pass", logs.output[0])
        self.assertIn("100.0h", logs.output[0])

    def test_missing_heartbeat_logs_a_warning_not_an_error(self):
        from drop_core import sweep

        self.assertFalse(LoopHeartbeat.objects.exists())
        with self.assertLogs("drop_core.sweep", "WARNING") as logs:
            sweep.maybe_sweep()
        self.assertEqual(
            [r.levelname for r in logs.records], ["WARNING"], logs.output
        )
        self.assertIn("no background loop pass on record", logs.output[0])

    def test_alarm_is_throttled_to_one_per_six_hours(self):
        from drop_core import sweep

        now = time.time()
        self._pass_at(now - 40 * 60 * 60)
        with self._at(now), self.assertLogs("drop_core.sweep", "ERROR") as first:
            sweep.maybe_sweep()
        self.assertEqual(len(first.records), 1)
        # Every upload in the next 6h stays quiet...
        for offset in (1, 60 * 60, 6 * 60 * 60 - 1):
            with self._at(now + offset), self.assertNoLogs("drop_core.sweep", "WARNING"):
                sweep.maybe_sweep()
        # ...and the alarm fires again once 6h have passed.
        with self._at(now + 6 * 60 * 60), self.assertLogs(
            "drop_core.sweep", "ERROR"
        ) as again:
            sweep.maybe_sweep()
        self.assertEqual(len(again.records), 1)

    def test_the_alarm_throttle_stays_in_the_cache(self):
        # Losing the throttle to a cull costs one early repeat of the alarm,
        # which is still an ERROR because the heartbeat itself is a row.
        from drop_core import sweep

        now = time.time()
        self._pass_at(now - 40 * 60 * 60)
        with self._at(now), self.assertLogs("drop_core.sweep", "ERROR"):
            sweep.maybe_sweep()
        self.assertEqual(cache.get(sweep._LOOP_PASS_ALARM_KEY), now)
        cache.clear()
        with self._at(now + 1), self.assertLogs("drop_core.sweep", "WARNING") as again:
            sweep.maybe_sweep()
        self.assertEqual(
            [r.levelname for r in again.records], ["ERROR"], again.output
        )

    def test_watcher_failure_never_reaches_the_upload(self):
        from drop_core import sweep

        with mock.patch.object(
            sweep, "_check_loop_pass", side_effect=RuntimeError("cache down")
        ), self.assertLogs("drop_core.sweep", "ERROR"):
            sweep.maybe_sweep()


# ---------------------------------------------------------------------------
# upload / serve failure branches (ctr-20, ops-4, ops-8) and metadata (ops-7)
# ---------------------------------------------------------------------------


class UploadAndServeFailureTests(_Base):
    def test_save_failure_logs_real_artifact_id_and_rolls_back(self):
        spy = FailingSaveSpyStorage()
        with use_storage(spy), self.assertNoLogs("drop_core.signals"):
            with self.assertLogs("drop_core.services", "ERROR") as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.api.post(
                        "/api/v1/artifacts/",
                        {"file": SimpleUploadedFile("n.txt", b"hello"), "ttl_days": 1},
                        **self.auth,
                    )
        self.assertEqual(response.status_code, 502)
        # Nothing was stored, so no cleanup delete is scheduled.
        self.assertEqual(spy.deleted, [])
        self.assertEqual(response.json()["code"], "storage_unavailable")
        output = "\n".join(logs.output)
        self.assertNotIn("id=None", output)
        self.assertRegex(output, rf"id={_UUID} workspace={self.ws.id}")
        self.assertFalse(Artifact.objects.exists())
        counter = UsageCounter.objects.filter(workspace=self.ws).first()
        self.assertEqual(counter.uploads if counter else 0, 0)

    def test_local_save_failure_leaves_no_partial_file(self):
        class Breaks(io.RawIOBase):
            def __init__(self):
                self.calls = 0

            def read(self, size=-1):
                self.calls += 1
                if self.calls > 1:
                    raise OSError("connection reset")
                return b"x" * 10

        storage = LocalStorage()
        with self.assertRaises(OSError):
            storage.save("partialtoken", Breaks(), "text/plain")
        self.assertFalse(storage.exists("partialtoken"))

    def test_upload_stores_object_metadata(self):
        spy = SpyStorage()
        with use_storage(spy):
            artifact = self._upload()
        (token, metadata) = spy.saved[0]
        self.assertEqual(token, artifact.token)
        self.assertEqual(
            metadata,
            {
                "artifact-id": str(artifact.id),
                "workspace-id": str(self.ws.id),
                "expires-at": artifact.expires_at.isoformat(),
            },
        )

    def test_serve_storage_outage_is_503_and_logged_without_token(self):
        artifact = self._upload()
        with use_storage(FailingOpenStorage()):
            with self.assertLogs("drop_core.views", "ERROR") as logs:
                # django.request would log the path, i.e. the live token.
                with self.assertNoLogs("django.request"):
                    response = self.client.get(f"/a/{artifact.token}")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "30")
        output = "\n".join(logs.output)
        self.assertIn(f"id={artifact.id}", output)
        self.assertNotIn(artifact.token, output)

    def test_serve_missing_object_is_404(self):
        artifact = self._upload()
        with use_storage(MissingOpenStorage()):
            self.assertEqual(self.client.get(f"/a/{artifact.token}").status_code, 404)


# ---------------------------------------------------------------------------
# reconcile_storage (ops-3/upl-10 backstop, ops-7)
# ---------------------------------------------------------------------------


class ReconcileStorageTests(_Base):
    ORPHAN = "o" * 43
    FRESH_ORPHAN = "f" * 43

    def setUp(self):
        super().setUp()
        self.live = self._upload()
        # Enough live objects that one orphan is a plausible share.
        self.others = [self._upload(name=f"o{i}.txt") for i in range(4)]
        storage = LocalStorage()
        for token in (self.ORPHAN, self.FRESH_ORPHAN):
            storage.save(token, io.BytesIO(b"orphan"), "text/plain")
        with open(os.path.join(self.tmpdir, "README.txt"), "w") as fh:
            fh.write("not an artifact")
        for name in (self.ORPHAN, self.live.token, "README.txt"):
            self._age(name)
        for artifact in self.others:
            self._age(artifact.token)

    def _age(self, name):
        old = time.time() - 3 * 24 * 3600
        os.utime(os.path.join(self.tmpdir, name), (old, old))

    def _old_orphans(self, n):
        tokens = [chr(ord("p") + i) * 43 for i in range(n)]
        for token in tokens:
            LocalStorage().save(token, io.BytesIO(b"orphan"), "text/plain")
            self._age(token)
        return tokens

    def test_dry_run_reports_without_deleting(self):
        out = io.StringIO()
        call_command("reconcile_storage", stdout=out)
        self.assertIn("objects=8 orphans=1 deleted=0 failed=0", out.getvalue())
        self.assertIn(f"orphan {self.ORPHAN}", out.getvalue())
        self.assertTrue(self._stored(self.ORPHAN))

    def test_summary_names_database_and_storage(self):
        out = io.StringIO()
        call_command("reconcile_storage", stdout=out)
        summary = out.getvalue().strip().splitlines()[-1]
        self.assertIn(" db=sqlite:", summary)
        self.assertIn(f" storage=local:{self.tmpdir}", summary)

    def test_delete_refuses_when_artifact_table_is_empty(self):
        # A wrong DATABASE_URL (e.g. unset, falling back to a fresh SQLite
        # file) must not read every customer object as an orphan.
        Artifact.objects.all().delete()
        out = io.StringIO()
        with self.assertRaisesMessage(CommandError, "Artifact table is empty"):
            call_command("reconcile_storage", "--delete", stdout=out)
        self.assertIn("deleted=0", out.getvalue())
        self.assertTrue(self._stored(self.ORPHAN))
        self.assertTrue(self._stored(self.live))
        out = io.StringIO()
        call_command("reconcile_storage", "--delete", "--force", stdout=out, stderr=io.StringIO())
        self.assertIn("orphans=6 deleted=6 failed=0", out.getvalue())
        self.assertFalse(self._stored(self.live))
        self.assertTrue(self._stored(self.FRESH_ORPHAN))

    def test_delete_refuses_when_too_many_objects_are_orphans(self):
        extra = self._old_orphans(2)
        err = io.StringIO()
        call_command("reconcile_storage", stdout=io.StringIO(), stderr=err)
        self.assertIn("warning: 3 of 8 artifact objects (38%) have no row", err.getvalue())
        with self.assertRaisesMessage(CommandError, "refusing --delete: 3 of 8"):
            call_command("reconcile_storage", "--delete", stdout=io.StringIO())
        for token in [self.ORPHAN] + extra:
            self.assertTrue(self._stored(token))
        out = io.StringIO()
        call_command("reconcile_storage", "--delete", "--force", stdout=out, stderr=io.StringIO())
        self.assertIn("orphans=3 deleted=3 failed=0", out.getvalue())
        for token in [self.ORPHAN] + extra:
            self.assertFalse(self._stored(token))
        self.assertTrue(self._stored(self.live))

    def test_delete_removes_only_old_orphans(self):
        out = io.StringIO()
        call_command("reconcile_storage", "--delete", stdout=out)
        self.assertIn("orphans=1 deleted=1 failed=0", out.getvalue())
        self.assertFalse(self._stored(self.ORPHAN))
        self.assertTrue(self._stored(self.FRESH_ORPHAN))
        self.assertTrue(self._stored(self.live))
        self.assertTrue(self._stored("README.txt"))

    def test_delete_failure_exits_nonzero(self):
        out, err = io.StringIO(), io.StringIO()
        with use_storage(FailingDeleteStorage()):
            with self.assertRaisesMessage(CommandError, "could not be deleted"):
                call_command("reconcile_storage", "--delete", stdout=out, stderr=err)
        self.assertIn("orphans=1 deleted=0 failed=1", out.getvalue())
        self.assertIn(f"delete failed {self.ORPHAN}", err.getvalue())
        self.assertTrue(self._stored(self.ORPHAN))

    def test_grace_period_has_a_floor(self):
        with self.assertRaisesMessage(CommandError, "at least 1"):
            call_command("reconcile_storage", "--grace-hours", "0", stdout=io.StringIO())
        self.assertTrue(self._stored(self.ORPHAN))


# ---------------------------------------------------------------------------
# S3/R2 backend via botocore Stubber, no network (ops-8, ops-7)
# ---------------------------------------------------------------------------

S3_ENV = {
    "DROP_STORAGE": "s3",
    "AWS_S3_ENDPOINT_URL": "https://acct.r2.cloudflarestorage.example",
    "AWS_S3_BUCKET": "veilfile-test",
    "AWS_ACCESS_KEY_ID": "test-key-id",
    "AWS_SECRET_ACCESS_KEY": "test-secret",
}


class S3StorageTests(TestCase):
    def setUp(self):
        from botocore.stub import Stubber

        from drop_core import storage as storage_module

        env = mock.patch.dict(os.environ, S3_ENV)
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(storage_module._s3_storages.clear)
        storage_module._s3_storages.clear()
        cache.clear()
        self.addCleanup(cache.clear)
        cache.set("drop_last_sweep_ts", time.time(), 3600)
        with self.assertLogs("drop_core.storage", "INFO") as logs:
            self.storage = storage_module.get_storage()
        self.assertNotIn("test-secret", "\n".join(logs.output))
        self.stub = Stubber(self.storage.client)
        self.stub.activate()
        self.addCleanup(self.stub.deactivate)

    def _client_error(self, method, code, status):
        self.stub.add_client_error(
            method, service_error_code=code, http_status_code=status
        )

    def test_get_storage_selects_and_reuses_s3(self):
        from drop_core.storage import S3Storage, get_storage

        self.assertIsInstance(self.storage, S3Storage)
        self.assertEqual(self.storage.bucket, "veilfile-test")
        self.assertIs(get_storage(), self.storage)

    def test_missing_configuration_raises(self):
        from drop_core.storage import S3Storage

        for name in ("AWS_S3_ENDPOINT_URL", "AWS_S3_BUCKET"):
            with mock.patch.dict(os.environ, {name: ""}):
                with self.assertRaises(RuntimeError):
                    S3Storage()

    def test_save_puts_object_with_type_and_metadata(self):
        self.stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "veilfile-test",
                "Key": "tok",
                "Body": b"hello",
                "ContentType": "text/plain",
                "Metadata": {"workspace-id": "w1"},
            },
        )
        self.storage.save("tok", io.BytesIO(b"hello"), "text/plain", metadata={"workspace-id": "w1"})
        self.stub.assert_no_pending_responses()

    def test_save_without_metadata_or_type(self):
        self.stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "veilfile-test",
                "Key": "tok",
                "Body": b"x",
                "ContentType": "application/octet-stream",
            },
        )
        self.storage.save("tok", io.BytesIO(b"x"), None)
        self.stub.assert_no_pending_responses()

    def test_save_error_propagates(self):
        self._client_error("put_object", "InternalError", 500)
        with self.assertRaises(Exception):
            self.storage.save("tok", io.BytesIO(b"x"), "text/plain")

    def test_open_returns_streaming_body(self):
        from botocore.response import StreamingBody

        self.stub.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(b"data"), 4), "ContentLength": 4},
            {"Bucket": "veilfile-test", "Key": "tok"},
        )
        body = self.storage.open("tok")
        self.assertEqual(body.read(), b"data")

    def test_open_missing_key_is_file_not_found(self):
        self._client_error("get_object", "NoSuchKey", 404)
        with self.assertRaises(FileNotFoundError):
            self.storage.open("tok")

    def test_open_backend_error_is_not_file_not_found(self):
        from botocore.exceptions import ClientError

        self._client_error("get_object", "AccessDenied", 403)
        with self.assertRaises(ClientError):
            self.storage.open("tok")

    def test_delete(self):
        self.stub.add_response(
            "delete_object", {}, {"Bucket": "veilfile-test", "Key": "tok"}
        )
        self.storage.delete("tok")
        self.stub.assert_no_pending_responses()

    def test_exists(self):
        from botocore.exceptions import ClientError

        self.stub.add_response("head_object", {}, {"Bucket": "veilfile-test", "Key": "a"})
        self._client_error("head_object", "404", 404)
        self._client_error("head_object", "403", 403)
        self.assertTrue(self.storage.exists("a"))
        self.assertFalse(self.storage.exists("b"))
        with self.assertRaises(ClientError):
            self.storage.exists("c")

    def test_list_objects_follows_pagination(self):
        when = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        self.stub.add_response(
            "list_objects_v2",
            {
                "Contents": [{"Key": "a", "LastModified": when}],
                "IsTruncated": True,
                "NextContinuationToken": "next",
            },
            {"Bucket": "veilfile-test"},
        )
        self.stub.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": "b", "LastModified": when}], "IsTruncated": False},
            {"Bucket": "veilfile-test", "ContinuationToken": "next"},
        )
        self.assertEqual(
            [key for key, _ in self.storage.list_objects()], ["a", "b"]
        )

    def test_upload_serve_revoke_through_s3(self):
        from botocore.response import StreamingBody
        from botocore.stub import ANY

        user = User.objects.create_user(username="s3owner", password="pw12345678")
        ws = get_or_create_workspace(user)
        self.stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "veilfile-test",
                "Key": ANY,
                "Body": b"hello r2",
                "ContentType": "text/plain",
                "Metadata": ANY,
            },
        )
        artifact = upload_artifact(ws, None, "n.txt", b"hello r2", "text/plain")
        self.stub.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(b"hello r2"), 8), "ContentLength": 8},
            {"Bucket": "veilfile-test", "Key": artifact.token},
        )
        response = self.client.get(f"/a/{artifact.token}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"hello r2")
        self.stub.add_response(
            "delete_object", {}, {"Bucket": "veilfile-test", "Key": artifact.token}
        )
        self.assertIs(revoke_artifact(ws, artifact.id), True)
        self.stub.assert_no_pending_responses()
        self.assertFalse(Artifact.objects.exists())

    def test_client_timeouts_and_retries_are_bounded(self):
        config = self.storage.client.meta.config
        self.assertEqual(config.connect_timeout, 3)
        self.assertEqual(config.read_timeout, 10)
        self.assertEqual(config.retries, {"total_max_attempts": 2, "mode": "standard"})

    def test_delete_many_batches_by_1000_and_reports_real_failures(self):
        keys = [f"k{i}" for i in range(1001)]
        self.stub.add_response(
            "delete_objects",
            {
                "Errors": [
                    {"Key": "k5", "Code": "AccessDenied", "Message": "denied"},
                    {"Key": "k6", "Code": "NoSuchKey", "Message": "gone"},
                ]
            },
            {
                "Bucket": "veilfile-test",
                "Delete": {"Objects": [{"Key": k} for k in keys[:1000]], "Quiet": True},
            },
        )
        self.stub.add_response(
            "delete_object", {}, {"Bucket": "veilfile-test", "Key": "k1000"}
        )
        self.assertEqual(self.storage.delete_many(keys), {"k5": "AccessDenied: denied"})
        self.stub.assert_no_pending_responses()

    def test_delete_many_stops_at_the_first_failed_call(self):
        keys = [f"k{i}" for i in range(2500)]
        self._client_error("delete_objects", "InternalError", 500)
        failures = self.storage.delete_many(keys)
        self.assertEqual(set(failures), set(keys))
        self.stub.assert_no_pending_responses()

    def test_cascade_deletes_through_delete_objects(self):
        from botocore.stub import ANY

        user = User.objects.create_user(username="s3cascade", password="pw12345678")
        ws = get_or_create_workspace(user)
        tokens = []
        for i in range(3):
            artifact = Artifact.objects.create(
                workspace=ws,
                filename=f"{i}.txt",
                size_bytes=1,
                expires_at=timezone.now() + timedelta(days=1),
            )
            tokens.append(artifact.token)
        self.stub.add_response(
            "delete_objects", {}, {"Bucket": "veilfile-test", "Delete": ANY}
        )
        with self.captureOnCommitCallbacks(execute=True):
            ws.delete()
        self.stub.assert_no_pending_responses()

    def test_save_may_have_stored(self):
        from botocore.exceptions import ClientError, ReadTimeoutError

        refused = ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        self.assertFalse(self.storage.save_may_have_stored(refused))
        timeout = ReadTimeoutError(endpoint_url="https://r2.example")
        self.assertTrue(self.storage.save_may_have_stored(timeout))
        self.assertFalse(LocalStorage().save_may_have_stored(OSError("disk full")))

    def _upload_expecting_storage_error(self, username):
        from drop_core.services import StorageError

        user = User.objects.create_user(username=username, password="pw12345678")
        ws = get_or_create_workspace(user)
        with self.assertLogs("drop_core.services", "ERROR"):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                with self.assertRaises(StorageError):
                    upload_artifact(ws, None, "n.txt", b"hello r2", "text/plain")
        self.assertFalse(Artifact.objects.filter(workspace=ws).exists())
        return callbacks

    def test_upload_timeout_deletes_what_r2_may_have_stored(self):
        from botocore.exceptions import ReadTimeoutError
        from botocore.stub import ANY

        self.stub.add_response(
            "delete_object", {}, {"Bucket": "veilfile-test", "Key": ANY}
        )
        timeout = ReadTimeoutError(endpoint_url="https://r2.example")
        with mock.patch.object(self.storage.client, "put_object", side_effect=timeout):
            callbacks = self._upload_expecting_storage_error("s3timeout")
        self.assertEqual(len(callbacks), 1)
        self.stub.assert_no_pending_responses()

    def test_upload_refused_by_r2_schedules_no_cleanup(self):
        self._client_error("put_object", "AccessDenied", 403)
        with self.assertNoLogs("drop_core.signals"):
            callbacks = self._upload_expecting_storage_error("s3refused")
        self.assertEqual(callbacks, [])
        self.stub.assert_no_pending_responses()

    def test_reconcile_delete_on_s3_requires_postgresql(self):
        # DATABASE_URL unset falls back to SQLite; with prod R2 credentials
        # every object would look orphaned. Not even --force overrides it.
        with self.assertRaisesMessage(CommandError, "only reconciled against PostgreSQL"):
            call_command(
                "reconcile_storage", "--delete", "--force", stdout=io.StringIO()
            )
        # Refused before listing or deleting anything.
        self.stub.assert_no_pending_responses()

    def test_reconcile_on_s3_with_postgresql(self):
        old = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        orphan = "o" * 43
        self.stub.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": orphan, "LastModified": old}], "IsTruncated": False},
            {"Bucket": "veilfile-test"},
        )
        self.stub.add_response(
            "delete_object", {}, {"Bucket": "veilfile-test", "Key": orphan}
        )
        out = io.StringIO()
        with mock.patch(
            "drop_core.management.commands.reconcile_storage.connection"
        ) as conn:
            conn.vendor = "postgresql"
            conn.settings_dict = {
                "NAME": "veilfile",
                "HOST": "db.internal",
                "PASSWORD": "pg-secret",
            }
            call_command(
                "reconcile_storage",
                "--delete",
                "--force",
                stdout=out,
                stderr=io.StringIO(),
            )
        self.stub.assert_no_pending_responses()
        self.assertIn(
            "orphans=1 deleted=1 failed=0 db=postgresql:veilfile@db.internal "
            "storage=s3:veilfile-test",
            out.getvalue(),
        )
        self.assertNotIn("pg-secret", out.getvalue())


# ---------------------------------------------------------------------------
# secret scanner (upl-5, upl-6)
# ---------------------------------------------------------------------------

# Shape-only, non-functional samples. Token-shaped ones are written in two
# pieces so that no whole token sits in the source for secret scanners to flag.
_R = "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8S9t0"
_HEX64 = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"


class SecretScannerTests(SimpleTestCase):
    POSITIVES = [
        ("private_key", b"-----BEGIN OPENSSH PRIVATE KEY-----\nb3Blbg==\n"),
        ("private_key", b"-----BEGIN EC PRIVATE KEY-----\nMHcC\n"),
        ("private_key", b"-----BEGIN DSA PRIVATE KEY-----\nMIIB\n"),
        ("private_key", b"-----BEGIN RSA PRIVATE KEY-----\nMIIE\n"),
        ("private_key", b"-----BEGIN PRIVATE KEY-----\nMIIE\n"),
        ("private_key", b"-----BEGIN ENCRYPTED PRIVATE KEY-----\nMIIF\n"),
        ("private_key", b"-----BEGIN PGP PRIVATE KEY BLOCK-----\n\nlQ\n"),
        ("private_key", b"---- BEGIN SSH2 ENCRYPTED PRIVATE KEY ----\n"),
        ("private_key", b"PuTTY-User-Key-File-3: ssh-ed25519\n"),
        ("stripe_key", b"STRIPE_KEY=sk_live_" + _R[:24].encode()),
        ("stripe_key", b"key: sk_test_" + _R[:30].encode()),
        ("stripe_key", b'"rk_live_' + _R[:24].encode() + b'"'),
        ("stripe_key", b"whsec_" + _R[:32].encode()),
        ("github_pat", b'token = "ghp_' + b"a" * 36 + b'"'),
        ("github_pat", b"github_pat_11ABCDEFG0123456789_" + _R.encode() + _R.encode()),
        ("github_token", b"gho_" + b"A" * 36),
        ("github_token", b"x ghu_" + _R[:36].encode()),
        ("github_token", b"ghs_" + _R[:36].encode()),
        ("github_token", b"ghr_" + _R[:36].encode()),
        ("slack_token", b"xoxb-" + b"123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx"),
        ("slack_token", b"xapp-" + b"1-A0123456789-1234567890123-abcdef"),
        ("google_api_key", b"AIza" + _R[:35].encode()),
        ("anthropic_key", b"key=sk-ant-api03-" + _R.encode() + b"-AA"),
        ("openai_key", b"OPENAI_API_KEY=sk-proj-" + _R.encode() + b"_" + _R[:20].encode()),
        ("openai_key", b"Authorization: Bearer sk-" + (_R + _R)[:48].encode()),
        # Lowercase-hex sk- keys (OpenRouter, DeepSeek, DashScope).
        ("openai_key", b'{"key": "sk-or-v1-' + _HEX64.encode() + b'"}'),
        ("openai_key", b"Authorization: Bearer sk-" + _HEX64[:32].encode()),
        ("openai_key", b"sk-abcdefghijklmnopqrstuvwxyz0123456789abcdefghijkl\n"),
        # Escaped newline in a JSON log line is a token boundary.
        ("stripe_key", b'{"log": "line one\\nsk_live_' + _R[:24].encode() + b'"}'),
        ("aws_access_key", b"AKIAIOSFODNN7EXAMPLE"),
        ("aws_access_key", b"ASIAABCDEFGHIJKLMNOP"),
        ("aws_secret_key", b"secret = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n"),
        ("generic_secret", b'DB_PASSWORD="hunter2hunter2"\n'),
        ("generic_secret", b"STRIPE_SECRET_KEY = 'abcdefgh12345678'\n"),
        ("generic_secret", b'{"api_key": "abcd1234efgh5678"}'),
        ("generic_secret", b"DJANGO_SECRET_KEY=django-insecure-9x7k2m4p8q\n"),
        ("generic_secret", b"export DB_PASSWORD=Tr0ub4dor3xyz\n"),
        ("generic_secret", b'password              = "hunter2hunter2"\n'),
        ("generic_secret", b'secretKey: "abcd1234efgh5678"\n'),
        ("generic_secret", b"DB_PASSWORD_FOR_THE_PROD_ENV=Abcdef123456789\n"),
        ("generic_secret", b"SECRET_KEY_BASE=abcdef0123456789abcdef0123456789\n"),
    ]

    NEGATIVES = [
        b"commit 3f786850e387550fdab836ed7e6dc881de23001b\nAuthor: x\n",
        b"3F786850E387550FDAB836ED7E6DC881DE23001B\n",
        b"ABCD" * 10 + b"\n",
        b"abcdefghijabcdefghijabcdefghijabcdefghij\n",
        b"We need a risk-assessment-framework for this.\n",
        b"the desk-reservation-system-v2 is down\n",
        b"sk-learn-is-a-python-library-for-ml\n",
        b"ask_live_" + _R[:24].encode(),
        b"/Users/alice/project/x1 is here\n",
        b"SECRET_KEY=${DJANGO_SECRET_KEY_FROM_VAULT}\n",
        b"PASSWORD=****************\n",
        b"SSH_PRIVATE_KEY_PATH=/home/user/.ssh/id_ed25519\n",
        b"TOKEN_ENDPOINT=https://oauth2.example.com/token\n",
        b"CSRF_TOKEN_COOKIE_NAME=csrftoken_veilfile\n",
        b"-----BEGIN PUBLIC KEY-----\nMIIB\n",
        b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n",
        b"-----BEGIN CERTIFICATE-----\n",
        b"xoxb-1\n",
        b"id=550e8400-e29b-41d4-a716-446655440000\n",
        b"The password: must be at least 8 characters.\n",
        b"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n",
        # Ordinary code and config that names secrets without holding one.
        b"ACCESS_TOKEN_LIFETIME = timedelta(minutes=15)\n",
        b"ACCESS_TOKEN_LIFETIME=timedelta(minutes=15)\n",
        b"TOKEN_ENDPOINT = urljoin(BASE_URL, 'token')\n",
        b"GITHUB_TOKEN_URL = settings.GITHUB_BASE + '/token'\n",
        b"PASSWORD_MIN_LENGTH = settings.Min12Length\n",
        b"API_KEY_HEADER = HTTPHeader.XApiKey\n",
        b'    secret_name: "prod/db/Password2"\n',
        b'password_reset_token: "{{ reset_token }}"\n',
        b'password: "{{ vault_db_password }}"\n',
        b"SECRET_ARN=arn:aws:secretsmanager:us-east-1:123456789012:secret:prod/db\n",
        b"MAX_TOKENS=409600000000\n",
        b"TOKENIZER_NAME=bert-base-uncased-v2\n",
        b"GITHUB_TOKEN=${{ secrets.GITHUB_TOKEN }}\n",
        b"REGION=SOUTHEASTASIAPACIFICREGIONCODE\n",
        b"sk-learn-v2-release-notes-and-more\n",
    ]

    def test_positives(self):
        from drop_core.secrets import scan_bytes

        for name, sample in self.POSITIVES:
            with self.subTest(sample=sample[:40]):
                self.assertIn(name, scan_bytes(sample))

    def test_negatives(self):
        from drop_core.secrets import scan_bytes

        for sample in self.NEGATIVES:
            with self.subTest(sample=sample[:40]):
                self.assertEqual(scan_bytes(sample), [])

    def test_flag_names_listed_once(self):
        from drop_core.secrets import scan_bytes

        sample = b'PASSWORD="hunter2hunter2"\nAPI_SECRET=Abcdef123456789\n'
        self.assertEqual(scan_bytes(sample), ["generic_secret"])

    def test_locations_are_capped_and_flagged_truncated(self):
        from drop_core.secrets import MAX_LOCATIONS_PER_FLAG, locate_flags

        data = ((b"AKIA" + b"ABCDEFGHIJKLMNOP\n") * 60000)[: 1024 * 1024]
        started = time.monotonic()
        locations, truncated = locate_flags(data)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertTrue(truncated)
        self.assertEqual(len(locations["aws_access_key"]), MAX_LOCATIONS_PER_FLAG)
        self.assertEqual(locations["aws_access_key"][2], {"line": 3, "offset": 42})

    def test_locations_lines_and_offsets(self):
        from drop_core.secrets import flag_locations, locate_flags

        data = (
            b"line one\n"
            b'DB_PASSWORD="hunter2hunter2"\n'
            b"AKIAIOSFODNN7EXAMPLE\n"
            b"API_SECRET=Abcdef123456789\n"
        )
        locations, truncated = locate_flags(data)
        self.assertFalse(truncated)
        self.assertEqual(locations["aws_access_key"], [{"line": 3, "offset": 38}])
        self.assertEqual(
            [hit["line"] for hit in locations["generic_secret"]], [2, 4]
        )
        self.assertEqual(flag_locations(data), locations)
        self.assertEqual(locate_flags(b"nothing here"), ({}, False))

    def test_every_pattern_is_linear_on_adversarial_input(self):
        from drop_core.secrets import _PATTERNS, locate_flags, scan_bytes

        mib = 1024 * 1024

        def fill(unit):
            return (unit * (mib // len(unit) + 1))[:mib]

        hostile = [
            fill("a"), fill("aB1/"), fill("sk-"), fill(" sk-aaaaaaaaaaaaaaaaaaaaaaaaa"),
            fill("-----BEGIN "), "-----BEGIN " + fill("A "),
            "password='" + fill("x"), fill("password_aaaa_bbbb_cccc"),
            "secret_" + fill("a"), fill("SECRET" * 10 + "\n"), fill("SECRET"),
            fill("A_TOKEN_B_TOKEN_C_TOKEN_D_TOKEN_E_TOKEN_F_TOKEN= \n"),
            fill("export AWS_SECRET_ACCESS_KEY=" + "x" * 11 + "\n"),
            "\n" * mib, fill(" \t"), fill("AKIA"), fill("xoxb-"), fill("ghp_gho_"),
            "github_pat_" + fill("_"), fill("whsec_"), fill("AIza"), fill("'\""),
            fill(":="), fill("api-key api_key apikey "), fill("PuTTY-User-Key-File-"),
            fill("commit 3f786850e387550fdab836ed7e6dc881de23001b\n"),
            "sk-" + fill("a1-"), "sk-" + fill("a1"), fill("sk-a1b2c3d4e5f6g7h8i9j0-"),
            fill("password_key_token_secret"), fill("password" + " " * 40 + "="),
            fill("secret" + "\t" * 31 + ":" + "\t" * 31), fill("A_TOKEN_X_Y_Z_W="),
            fill("SECRET_A_B_C_D_E_F_G="), fill("\\n"), fill("\\nAKIA"),
        ]
        for text in hostile:
            data = text.encode()
            for name, pattern, _ in _PATTERNS:
                started = time.monotonic()
                for _ in pattern.finditer(text):
                    pass
                elapsed = time.monotonic() - started
                with self.subTest(pattern=name, sample=text[:24]):
                    self.assertLess(elapsed, 1.0)
            started = time.monotonic()
            scan_bytes(data)
            locate_flags(data)
            self.assertLess(time.monotonic() - started, 3.0)


class SecretFlagResponseTests(_Base):
    def test_rest_response_caps_locations(self):
        data = ((b"AKIA" + b"ABCDEFGHIJKLMNOP\n") * 60000)[: 1024 * 1024]
        response = self.api.post(
            "/api/v1/artifacts/",
            {"file": SimpleUploadedFile("creds.log", data)},
            **self.auth,
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(len(body["secret_flag_locations"]["aws_access_key"]), 20)
        self.assertIs(body["secret_flag_locations_truncated"], True)
        self.assertLess(len(response.content), 20000)

    def test_small_flagged_upload_has_no_truncation_marker(self):
        response = self.api.post(
            "/api/v1/artifacts/",
            {"file": SimpleUploadedFile("creds.log", b"AKIAIOSFODNN7EXAMPLE\n")},
            **self.auth,
        )
        body = response.json()
        self.assertEqual(body["secret_flags"], ["aws_access_key"])
        self.assertNotIn("secret_flag_locations_truncated", body)

    def test_mcp_upload_caps_locations(self):
        import base64

        data = (b"AKIA" + b"ABCDEFGHIJKLMNOP\n") * 2000
        result = self._mcp_tool(
            "upload_artifact",
            {"filename": "creds.log", "content_base64": base64.b64encode(data).decode()},
        )
        self.assertEqual(len(result["secret_flag_locations"]["aws_access_key"]), 20)
        self.assertIs(result["secret_flag_locations_truncated"], True)
