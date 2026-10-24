"""What the self-hosted fonts can draw (spec 3.5), read from static/fonts/ranges.json.

tools/vendor_fonts.py writes that file; the content checks read it to catch characters
that no font on the site can draw.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from pages.unicode_ranges import merge, parse_ranges  # noqa: F401 (merge is re-exported for callers)

RANGES_FILE = Path(settings.BASE_DIR) / "static" / "fonts" / "ranges.json"
FAMILY_NAMES = {"display": "Cormorant Garamond", "reading": "Newsreader", "mono": "IBM Plex Mono"}


@dataclass(frozen=True)
class FontRanges:
    faces: dict  # face key, e.g. "newsreader/normal-400-500" -> list of (first, last) code points
    roles: dict  # "display" | "reading" | "mono" -> face key

    @classmethod
    def load(cls, path=RANGES_FILE):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        faces = {key: [(int(first), int(last)) for first, last in spans] for key, spans in data["faces"].items()}
        return cls(faces=faces, roles=dict(data["roles"]))

    def covers(self, role, char):
        point = ord(char)
        return any(first <= point <= last for first, last in self.faces[self.roles[role]])
