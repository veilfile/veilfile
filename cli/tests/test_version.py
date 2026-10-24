"""__version__ must match pyproject.toml, so a half-done bump fails before a
release (the release workflow also checks the tag against pyproject)."""

import re
import unittest
from pathlib import Path

import veilfile


class VersionTests(unittest.TestCase):
    def test_version_matches_pyproject(self):
        pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject)
        self.assertIsNotNone(match)
        self.assertEqual(veilfile.__version__, match.group(1))


if __name__ == "__main__":
    unittest.main()
