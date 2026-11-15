"""The command-line share skill for pi and omp, and the address that serves it (plan 2026-11-15-pi-and-omp)."""
import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

SKILL = Path(settings.BASE_DIR) / "pages" / "skills" / "veilfile" / "SKILL.md"


def front_matter(text):
    head = text.split("---\n")[1]
    return dict(line.split(": ", 1) for line in head.strip().splitlines())


class AgentSkillTests(TestCase):
    def test_the_skill_is_served_as_the_repo_file(self):
        response = self.client.get(reverse("veilfile-skill"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/markdown; charset=utf-8")
        self.assertEqual(response.content, SKILL.read_bytes())

    def test_the_skill_follows_the_agent_skills_format(self):
        meta = front_matter(SKILL.read_text(encoding="utf-8"))
        self.assertEqual(meta["name"], "veilfile")
        self.assertEqual(meta["name"], SKILL.parent.name)
        self.assertTrue(0 < len(meta["description"]) <= 1024)

    def test_the_skill_names_only_published_commands(self):
        # The commands of the published CLIs, npm 0.4.1 and PyPI 0.4.0: the skill must work with what users have.
        published = {"login", "logout", "status", "whoami", "push", "pull"}
        named = set(re.findall(r"`veilfile (\w+)", SKILL.read_text(encoding="utf-8")))
        self.assertTrue(named)
        self.assertLessEqual(named, published)

    def test_both_clis_mark_a_flagged_push_with_warning(self):
        """The skill tells the agent that a line starting "Warning:" after a push means a flagged secret."""
        root = Path(settings.BASE_DIR).parent
        self.assertIn("`Warning: secret scan flagged ", (root / "npm" / "veilfile.js").read_text(encoding="utf-8"))
        self.assertIn('f"Warning: {warning}', (root / "cli" / "veilfile.py").read_text(encoding="utf-8"))

    def test_a_missing_skill_file_is_a_404(self):
        with mock.patch("pages.views.VEILFILE_SKILL", SKILL.parent / "missing.md"):
            self.assertEqual(self.client.get(reverse("veilfile-skill")).status_code, 404)


class PiAndOmpDocsTests(TestCase):
    def test_agent_setup_has_a_pi_and_omp_section(self):
        from pages import registry

        html = self.client.get("/docs/agent/").content.decode()
        self.assertIn('id="pi-and-omp"', html)
        self.assertIn('href="#pi-and-omp"', html)
        self.assertIn(f'curl -fsSL {registry.SITE_ORIGIN}{reverse("veilfile-skill")} -o ~/.agents/skills/veilfile/SKILL.md', html)
        self.assertIn('<a href="/plans/">Plans</a>', html)
        self.assertIn('<meta name="description" content="Connect Claude, Codex, Cursor, pi, omp and other agents to Veilfile, '
                      'with setup for each one and what to do when a secret is flagged."', html)
