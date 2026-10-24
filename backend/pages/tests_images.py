"""The "Let go" mark, favicons and still grain (spec 3.3, 3.6), read back from the files."""
import importlib.util
import re
import struct
import zlib
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

IMG = Path(settings.BASE_DIR) / "static" / "img"
MARK_ROWS = ("####", "###.", "##.#", "#.#.")
MARK_PATH = "M0 0H560V140H420V280H280V420H140V560H0Z M420 280H560V420H420Z M280 420H420V560H280Z"
PAPER, INK = (0xEB, 0xE6, 0xDE, 255), (0x2C, 0x29, 0x25, 255)


def load_make_images():
    """tools/make_images.py (the generator is not a package) as a module."""
    path = Path(settings.BASE_DIR).parent / "tools" / "make_images.py"
    spec = importlib.util.spec_from_file_location("make_images", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_png(path):
    """(width, height, pixel(x, y) -> (r, g, b, a)) for an 8-bit RGBA PNG whose rows use filter 0."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    position, idat = 8, b""
    while position < len(data):
        length, kind = struct.unpack(">I4s", data[position:position + 8])
        body = data[position + 8:position + 8 + length]
        if kind == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", body[:10])
            assert (depth, color) == (8, 6), f"{path.name} is not 8-bit RGBA"
        elif kind == b"IDAT":
            idat += body
        position += 12 + length
    raw, stride = zlib.decompress(idat), width * 4 + 1
    return width, height, lambda x, y: tuple(raw[y * stride + 1 + x * 4:y * stride + 5 + x * 4])


class MarkTests(SimpleTestCase):
    def test_mark_svg_draws_the_spec_path(self):
        svg = (IMG / "mark.svg").read_text(encoding="utf-8")
        self.assertIn('viewBox="0 0 560 560"', svg)
        self.assertIn(f'd="{MARK_PATH}"', svg)

    def test_favicon_svg_follows_the_system_appearance(self):
        svg = (IMG / "favicon.svg").read_text(encoding="utf-8")
        self.assertIn(f'd="{MARK_PATH}"', svg)
        self.assertIn("prefers-color-scheme:dark", svg)
        self.assertIn("#2c2925", svg)
        self.assertIn("#e6e0d6", svg)

    def test_png_favicons_draw_the_grid_on_paper(self):
        for name, size, padding in (("favicon-16.png", 16, 0), ("favicon-32.png", 32, 0), ("apple-touch-icon.png", 180, 30)):
            width, height, pixel = read_png(IMG / name)
            self.assertEqual((width, height), (size, size), name)
            cell = (size - 2 * padding) // 4
            for row, cells in enumerate(MARK_ROWS):
                for column, mark in enumerate(cells):
                    centre = (padding + column * cell + cell // 2, padding + row * cell + cell // 2)
                    with self.subTest(image=name, row=row, column=column):
                        self.assertEqual(pixel(*centre), INK if mark == "#" else PAPER)
            if padding:
                self.assertEqual(pixel(padding // 2, padding // 2), PAPER, f"{name} has paper around the mark")

    def test_link_preview_image_is_the_mark_on_paper(self):
        width, height, pixel = read_png(IMG / "og.png")
        self.assertEqual((width, height), (1200, 630))
        self.assertEqual(pixel(10, 10), PAPER)
        left, top, cell = 460, 175, 70  # a 280px mark, centred
        for row, cells in enumerate(MARK_ROWS):
            for column, mark in enumerate(cells):
                centre = (left + column * cell + cell // 2, top + row * cell + cell // 2)
                with self.subTest(row=row, column=column):
                    self.assertEqual(pixel(*centre), INK if mark == "#" else PAPER)

    def test_still_grain_matches_the_shaders_on_each_ground(self):
        # The shader adds a zero-mean, bell-shaped grain (spec 3.1: the sum of three flat values, within +-GRAIN_PAPER or
        # GRAIN_INK of luma in veil.js, a standard deviation of a third of that, and Ink's weaker); the still page's tile
        # must match it closely (spec 3.3) on its own ground: pure white or black specks whose alpha lightens or darkens the
        # ground by that much. The shader's own spread, read back from its canvas in the browser checks (cloth_grain_problems),
        # is 2.15 and 1.54 levels of 255 for the peaks 0.025 and 0.018.
        make_images = load_make_images()
        for name, ground, constant in (("grain-paper.png", (0xEB, 0xE6, 0xDE), "GRAIN_PAPER"), ("grain-ink.png", (0x1B, 0x19, 0x17), "GRAIN_INK")):
            peak = make_images.veil_constant(constant) * 255   # levels
            wanted = peak / 3
            with self.subTest(tile=name):
                width, height, pixel = read_png(IMG / name)
                self.assertEqual((width, height), (128, 128))
                luma = lambda r, g, b: 0.2126 * r + 0.7152 * g + 0.0722 * b
                base = luma(*ground)
                deltas = []
                for y in range(height):
                    for x in range(width):
                        red, green, blue, alpha = pixel(x, y)
                        self.assertIn((red, green, blue), ((255, 255, 255), (0, 0, 0)), f"{name}: specks are pure white or black")
                        a = alpha / 255
                        deltas.append(luma(*(round(c * (1 - a) + red * a) for c in ground)) - base)
                mean = sum(deltas) / len(deltas)
                spread = (sum((d - mean) ** 2 for d in deltas) / len(deltas)) ** 0.5
                self.assertLess(abs(mean), 0.5, f"{name}: the grain shifts its ground by {mean:.2f} levels; it should average out")
                self.assertTrue(0.92 * wanted <= spread <= 1.08 * wanted,
                                f"{name}: the grain's spread is {spread:.2f} levels; the shader's {constant} of {peak / 255:.3f} gives {wanted:.2f}")
                self.assertLessEqual(max(abs(d) for d in deltas), peak + 1, f"{name}: a speck moves its ground more than the shader's grain can")
                self.assertGreaterEqual(max(abs(d - mean) for d in deltas), 2.3 * spread,
                                        f"{name}: the grain should be bell-shaped, a few specks far out; a flat noise reaches 1.7 times its spread")

    def test_the_inks_still_grain_is_weaker_than_papers(self):
        spreads = {}
        for name, ground in (("grain-paper.png", (0xEB, 0xE6, 0xDE)), ("grain-ink.png", (0x1B, 0x19, 0x17))):
            width, height, pixel = read_png(IMG / name)
            luma = lambda r, g, b: 0.2126 * r + 0.7152 * g + 0.0722 * b
            deltas = [luma(*(round(c * (1 - a / 255) + red * a / 255) for c in ground)) - luma(*ground)
                      for y in range(height) for x in range(width) for red, _, _, a in [pixel(x, y)]]
            mean = sum(deltas) / len(deltas)
            spreads[name] = (sum((d - mean) ** 2 for d in deltas) / len(deltas)) ** 0.5
        self.assertLess(spreads["grain-ink.png"], 0.85 * spreads["grain-paper.png"],
                        f"the Ink tile's spread ({spreads['grain-ink.png']:.2f} levels) should be under 0.85 of Paper's ({spreads['grain-paper.png']:.2f}), as the shader's is")


class StillGrainSizeTests(SimpleTestCase):
    """The still grain's 128 px tile is drawn so that one tile pixel is one device pixel (spec 3.3): base.css sets --grain-size (what html's
    background-size and the phone menu sheet's surface use) to 128 divided by the pixel ratio in CSS px, by resolution, for each ratio screens
    really have (1.25, 1.5, 1.75, 2, 2.625, 2.75, 3, 4), so that the tile is a whole 128 device pixels at each and repeats on the pixel grid
    (a tile of 85.33 CSS px is 106.67 device px at 1.25, one of 42.67 is 117.33 at 2.75). The browser checks read it back at those ratios
    (grain_scale_problems), and from the screenshot."""

    # From the ratio a rule starts at (halfway to the one before it) to the ratio it is for.
    RULES = {"1.125": 1.25, "1.375": 1.5, "1.625": 1.75, "1.875": 2.0, "2.3125": 2.625, "2.6875": 2.75, "2.875": 3.0, "3.5": 4.0}

    def test_each_resolution_rule_shows_one_tile_pixel_per_device_pixel_and_a_whole_tile(self):
        css = (Path(settings.BASE_DIR) / "static" / "css" / "base.css").read_text(encoding="utf-8")
        rules = re.findall(r"@media \(-webkit-min-device-pixel-ratio: ([0-9.]+)\), \(min-resolution: ([0-9.]+)dppx\) \{\s*"
                           r":root \{ --grain-size: ([0-9.]+)px ([0-9.]+)px; \}", css)
        self.assertEqual([rule[0] for rule in rules], list(self.RULES), "static/css/base.css: a --grain-size rule for each of the ratios 1.25, 1.5, 1.75, 2, 2.625, 2.75, 3 and 4")
        width, _, _ = read_png(IMG / "grain-paper.png")
        for webkit, resolution, wide, high in rules:
            ratio = self.RULES[resolution]
            with self.subTest(ratio=ratio):
                self.assertEqual(webkit, resolution, "the -webkit- query and min-resolution should name the same ratio")
                self.assertEqual(wide, high, "the tile is square")
                device = float(wide) * ratio
                self.assertAlmostEqual(device, width, delta=0.005,
                                       msg=f"a {wide} px tile on a {ratio}x screen is {device:.3f} device px; it should be the tile's own {width}, whole")

    def test_the_rules_come_in_ascending_order_so_each_overrides_the_one_before(self):
        css = (Path(settings.BASE_DIR) / "static" / "css" / "base.css").read_text(encoding="utf-8")
        starts = [float(start) for start in re.findall(r"@media \(-webkit-min-device-pixel-ratio: ([0-9.]+)\), \(min-resolution: [0-9.]+dppx\) \{\s*:root \{ --grain-size", css)]
        self.assertEqual(starts, sorted(starts))


class MarkCardTests(SimpleTestCase):
    def test_a_mark_size_that_is_not_a_multiple_of_four_draws_the_largest_whole_grid_that_fits(self):
        pixel = load_make_images().mark_card(1200, 630, 281)  # 281 px asked: 4 cells of 70 px, 280 px square
        left, top, cell = 460, 175, 70
        for y in range(top - 2, top + 283):  # every pixel on and around the mark, including its last row and column
            for x in range(left - 2, left + 283):
                pixel(x, y)  # must not raise
        for row, cells in enumerate(MARK_ROWS):
            for column, mark in enumerate(cells):
                centre = (left + column * cell + cell // 2, top + row * cell + cell // 2)
                with self.subTest(row=row, column=column):
                    self.assertEqual(pixel(*centre), INK if mark == "#" else PAPER)
        self.assertEqual(pixel(left + 280, top), PAPER, "paper just right of the mark")
        self.assertEqual(pixel(left, top + 280), PAPER, "paper just below the mark")

    def test_every_size_from_nothing_to_a_few_cells_draws_without_raising(self):
        make_images = load_make_images()
        for mark in range(0, 20):
            pixel = make_images.mark_card(24, 24, mark)
            with self.subTest(mark=mark):
                drawn = [pixel(x, y) for y in range(24) for x in range(24)]
                self.assertEqual(INK in drawn, mark >= 4, "a mark of at least one pixel per cell shows ink")
