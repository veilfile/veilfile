"""A held file waits for staff to release it before the sweep deletes it (team seats F2b; the owner's F1 decision 3,
2026-11-14). Every class sets longMessage = False.
"""
import shutil
import tempfile
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from pages.tests_chapters import visible_text

from . import file_access
from .models import Artifact
from .sweep import sweep_expired
from .tests import _use_local_dir
from .tests_members import second_member
from .tests_teams import team_of

EXCEPTION = (
    "The one exception is a file we hold after a report, which we keep until we have dealt with the report or for as "
    "long as the law requires."
)


class HeldKeepTests(TestCase):
    longMessage = False

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="veilfile-test-")
        _use_local_dir(self, self.tmpdir)
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.team, self.admin = team_of("ada")
        self.grace = second_member(self.team, "grace")

    def expired_file(self, name, *, held, days=2):
        return Artifact.objects.create(
            workspace=self.team, member=self.grace, filename=name, size_bytes=10,
            expires_at=timezone.now() - timedelta(days=days), safety_status="held" if held else "clear",
        )

    def exists(self, artifact):
        return Artifact.objects.filter(pk=artifact.pk).exists()

    def test_a_held_file_past_its_expiry_is_kept(self):
        held = self.expired_file("held.txt", held=True)
        ordinary = self.expired_file("ordinary.txt", held=False)
        result = sweep_expired()
        self.assertTrue(self.exists(held), "backend/drop_core/sweep.py: the sweep leaves a held file alone at its expiry")
        self.assertFalse(self.exists(ordinary))
        self.assertEqual((result.swept, result.complete), (1, True))

    def test_held_files_do_not_fill_a_batch(self):
        for n in range(3):
            self.expired_file(f"held-{n}.txt", held=True, days=5)
        ordinary = self.expired_file("ordinary.txt", held=False)
        sweep_expired(limit=2)
        self.assertFalse(self.exists(ordinary),
                         "backend/drop_core/sweep.py: held files are left out of the query, so they take no place in a batch")

    def test_a_released_file_goes_at_the_next_sweep(self):
        held = self.expired_file("held.txt", held=True)
        sweep_expired()
        file_access.mark_clear([held.pk])
        sweep_expired()
        self.assertFalse(self.exists(held), "backend/drop_core/sweep.py: once released, an expired file is swept")

    def test_a_kept_file_still_blocks_account_deletion(self):
        self.expired_file("held.txt", held=True)
        sweep_expired()
        self.assertTrue(file_access.held_files_at_stake([self.team.pk], [self.grace.pk]),
                        "backend/drop_core/file_access.py: a held file past its expiry still stops an account's deletion")

    def test_the_promise_names_the_exception(self):
        text = visible_text(self.client.get(reverse("promises")).content.decode())
        self.assertIn("It is not hidden; it is gone. " + EXCEPTION, text,
                      "backend/templates/chapters/promises.html: Gone means deleted names the held-file exception")
