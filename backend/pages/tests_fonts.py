"""The self-hosted fonts (spec 3.5): every face the design uses, in every Unicode range offered, with licences."""
import json
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from pages.fonts import FontRanges, merge, parse_ranges

STATIC = Path(settings.BASE_DIR) / "static"
FONTS_CSS = STATIC / "css" / "fonts.css"
RANGES = STATIC / "fonts" / "ranges.json"
FACE = re.compile(r"@font-face\s*\{(?P<body>[^}]*)\}")
REQUIRED_FACES = {
    ("Cormorant Garamond", "italic", "300"),
    ("Cormorant Garamond", "italic", "400"),
    ("Newsreader", "normal", "400 500"),
    ("Newsreader", "italic", "400 500"),
    ("IBM Plex Mono", "normal", "400"),
}
FALLBACKS = {"Cormorant Garamond Fallback", "Newsreader Fallback", "IBM Plex Mono Fallback"}
PRELOADS = ("fonts/cormorant-garamond/italic-300-latin.woff2", "fonts/newsreader/normal-400-500-latin.woff2",
            "fonts/ibm-plex-mono/normal-400-latin.woff2")


def faces():
    """Each @font-face rule in fonts.css as a dict of its descriptors."""
    for match in FACE.finditer(FONTS_CSS.read_text(encoding="utf-8")):
        found = dict(re.findall(r"([\w-]+)\s*:\s*([^;]+);", match.group("body")))
        yield {name: value.strip().strip('"') if name == "font-family" else value.strip() for name, value in found.items()}


def face_key(face):
    return f'{face["font-family"].lower().replace(" ", "-")}/{face["font-style"]}-{face["font-weight"].replace(" ", "-")}'


class FontFilesTests(SimpleTestCase):
    def test_every_face_the_design_uses_is_declared(self):
        declared = {(f["font-family"], f["font-style"], f["font-weight"]) for f in faces() if "unicode-range" in f}
        self.assertLessEqual(REQUIRED_FACES, declared, f"missing: {REQUIRED_FACES - declared}")

    def test_every_declared_file_exists(self):
        for face in faces():
            for url in re.findall(r'url\("([^"]+)"\)', face.get("src", "")):
                with self.subTest(url=url):
                    self.assertTrue((FONTS_CSS.parent / url).resolve().is_file(), f"{url} is in fonts.css but missing")

    def test_each_face_ships_more_than_the_latin_range(self):
        # Spec 3.5: split by Unicode range, never subset to the characters on the site today.
        counts = {}
        for face in faces():
            if "unicode-range" in face:
                counts[face_key(face)] = counts.get(face_key(face), 0) + 1
        for key, count in counts.items():
            with self.subTest(face=key):
                self.assertGreaterEqual(count, 2, f"{key} ships only {count} range file")

    def test_fallbacks_are_metric_matched(self):
        fallbacks = [f for f in faces() if f["font-family"] in FALLBACKS]
        self.assertEqual({f["font-family"] for f in fallbacks}, FALLBACKS)
        for face in fallbacks:
            for descriptor in ("size-adjust", "ascent-override", "descent-override", "line-gap-override"):
                with self.subTest(face=face["font-family"], descriptor=descriptor):
                    self.assertRegex(face.get(descriptor, ""), r"^\d+(\.\d+)?%$")

    def test_licences_ship_with_the_fonts(self):
        for family in ("cormorant-garamond", "newsreader", "ibm-plex-mono"):
            with self.subTest(family=family):
                text = (STATIC / "fonts" / family / "OFL.txt").read_text(encoding="utf-8")
                self.assertIn("SIL OPEN FONT LICENSE", text.upper())

    def test_the_preloaded_files_exist(self):
        for path in PRELOADS:
            with self.subTest(path=path):
                self.assertTrue((STATIC / path).is_file())


class FontRangesTests(SimpleTestCase):
    def test_ranges_json_claims_nothing_outside_the_unicode_ranges_in_fonts_css(self):
        data = json.loads(RANGES.read_text(encoding="utf-8"))
        spans = {}
        for face in faces():
            if "unicode-range" in face:
                spans.setdefault(face_key(face), []).extend(parse_ranges(face["unicode-range"]))
        self.assertEqual(set(spans), set(data["faces"]))
        for key, found in spans.items():
            served = merge(found)
            for first, last in data["faces"][key]:
                with self.subTest(face=key, span=f"U+{first:04X}-{last:04X}"):
                    self.assertTrue(any(low <= first and last <= high for low, high in served))

    def test_ranges_json_leaves_out_characters_the_font_files_lack(self):
        # A unicode-range names a slice of Unicode, not the glyphs in the file, so ranges.json is cut
        # down to each file's own character map. These were read from the files with fontTools.
        ranges = FontRanges.load()
        lacking = {
            "display": "\u2010\u2011\u2030",                    # hyphen, non-breaking hyphen, per mille
            "reading": "\u2009\u2010\u2011\u2191\u2193\u2030",  # thin space, the hyphens, up and down arrows
            "mono": "\u2009\u2010\u2011\u2030",
        }
        for role, chars in lacking.items():
            for char in chars:
                with self.subTest(role=role, char=f"U+{ord(char):04X}"):
                    self.assertFalse(ranges.covers(role, char))

    def test_roles_name_shipped_faces(self):
        ranges = FontRanges.load()
        self.assertEqual(set(ranges.roles), {"display", "reading", "mono"})
        for role, key in ranges.roles.items():
            with self.subTest(role=role):
                self.assertIn(key, ranges.faces)

    def test_the_faces_draw_the_characters_the_site_uses_today(self):
        ranges = FontRanges.load()
        for role in ("display", "reading", "mono"):
            for char in "Aa1·—…é’“”–•":
                with self.subTest(role=role, char=char):
                    self.assertTrue(ranges.covers(role, char))

    def test_parse_and_merge(self):
        self.assertEqual(parse_ranges("U+0000-00FF, U+0131, u+0152-0153"), [(0, 255), (305, 305), (338, 339)])
        self.assertEqual(parse_ranges("U+4??"), [(0x400, 0x4FF)])  # wildcards, as CSS allows
        self.assertEqual(parse_ranges("U+0000-00FF, "), [(0, 255)])  # a trailing comma
        self.assertEqual(merge([(5, 9), (0, 4), (20, 30), (25, 40)]), [(0, 9), (20, 40)])

    def test_the_vendoring_tool_uses_the_same_parser(self):
        tool = (Path(settings.BASE_DIR).parent / "tools" / "vendor_fonts.py").read_text(encoding="utf-8")
        self.assertNotIn("def parse_ranges", tool, "tools/vendor_fonts.py: import parse_ranges from pages.unicode_ranges")
        self.assertIn("from pages.unicode_ranges import parse_ranges", tool)
