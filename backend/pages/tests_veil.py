"""The cloth (spec 3.1 to 3.3, 6.3, 8): veil.js's budget and colours, and how the shell loads it."""
import gzip
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from pages.tests_shell import render_shell
from pages.tests_tokens import INK as INK_TOKENS, PAPER as PAPER_TOKENS

STATIC = Path(settings.BASE_DIR) / "static"
VEIL = STATIC / "js" / "veil.js"
# Spec 3.2, "Shader colors": each role's Paper value, then its Ink value.
SHADER_COLOURS = {
    "PAPER": ((0.922, 0.902, 0.871), (0.104, 0.098, 0.091)),
    "LIT": ((0.992, 0.952, 0.880), (0.300, 0.302, 0.312)),
    "SHADE": ((0.735, 0.728, 0.712), (0.052, 0.049, 0.046)),
    "SHEEN": ((0.080, 0.078, 0.074), (0.125, 0.135, 0.150)),
    "WASH": ((0.235, 0.210, 0.185), (0.880, 0.850, 0.800)),
}


def shader_colours():
    """The five colours appearance() in veil.js blends between, as {name: (paper, ink)}."""
    found = {}
    for name, paper, ink in re.findall(r"(\w+)\s*=\s*mix\(vec3\(([^)]*)\),\s*vec3\(([^)]*)\),\s*k\)",
                                       VEIL.read_text(encoding="utf-8")):
        found[name] = (tuple(float(v) for v in paper.split(",")), tuple(float(v) for v in ink.split(",")))
    return found


def rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))


def contrast(pixel, text):
    """WCAG contrast between a colour of three 0..1 channels and a text colour of the same."""
    def relative(channels):
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    light, dark = sorted((relative(pixel), relative(text)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def paper_token(appearance):
    """--paper in tokens.css for "light" (the first block) or "dark" (the [data-theme="dark"] block)."""
    css = (STATIC / "css" / "tokens.css").read_text(encoding="utf-8")
    block = css.split('[data-theme="dark"]')[1 if appearance == "dark" else 0]
    return re.search(r"--paper:\s*#([0-9a-f]{6})", block).group(1)


class VeilTests(SimpleTestCase):
    def test_stays_under_15_kb_gzipped(self):
        size = len(gzip.compress(VEIL.read_bytes(), 9))
        self.assertLessEqual(size, 15 * 1024, f"static/js/veil.js is {size} bytes gzipped; the budget is 15 KB (spec 8)")

    def test_the_shader_blends_the_specs_five_colours(self):
        self.assertEqual(shader_colours(), SHADER_COLOURS,
                         "static/js/veil.js: appearance() must blend spec 3.2's five colours, Paper then Ink")

    def test_the_shaders_ground_is_the_paper_token(self):
        # Spec 3.2: "The CSS ground must equal the shader ground exactly, so nothing seams where the
        # canvas meets the page." In 8 bits, the shader's ground is --paper in each appearance.
        for index, appearance in enumerate(("light", "dark")):
            with self.subTest(appearance=appearance):
                ground = "".join(f"{round(value * 255):02x}" for value in shader_colours()["PAPER"][index])
                self.assertEqual(ground, paper_token(appearance),
                                 f"static/js/veil.js: PAPER for {appearance} must equal --paper in static/css/tokens.css")

    def test_exports_start(self):
        self.assertIn("export function start({ canvas, gl, off, ended, sheet = () => 0 })", VEIL.read_text(encoding="utf-8"))

    def test_the_shell_hands_veil_js_its_url_and_never_loads_it_as_a_script(self):
        html = render_shell()
        self.assertIn('data-veil-src="/static/js/veil.js"', html)
        self.assertNotRegex(html, r"<script[^>]*veil\.js",
                            "shell.html: veil.js is imported by shell.js only when Motion is on and WebGL works (spec 6.3)")


class LeapFramesTests(SimpleTestCase):
    """The browser checks hold cloth time still between moments far apart by leaping frames of 100 ms (leap_frames in
    site-tests/run_site_checks.py). The cloth reads such frames as a slow device, but judges a window only after WARMUP
    frames and by the median of WINDOW: a leap of fewer than WARMUP plus half a window never tips it. The helper's count
    is a number of its own, so it is tied to veil.js here; otherwise a change to either would fail whole families with
    "the cloth stopped drawing" instead of saying what is wrong."""

    def test_a_leap_of_long_frames_is_never_judged_slow(self):
        source = VEIL.read_text(encoding="utf-8")
        warmup, window = (int(re.search(rf"^const {name} = (\d+);", source, re.M).group(1)) for name in ("WARMUP", "WINDOW"))
        harness = (Path(settings.BASE_DIR).parent / "site-tests" / "run_site_checks.py").read_text(encoding="utf-8")
        count = int(re.search(r"def leap_frames\(page, count=(\d+)\)", harness).group(1))
        self.assertLess(count, warmup + window // 2,
                        f"site-tests/run_site_checks.py: leap_frames leaps {count} frames, but veil.js (WARMUP {warmup}, WINDOW {window}) would judge "
                        f"{count - warmup} long frames after its warm-up slow once they are half a window; leap fewer than {warmup + window // 2}")


class CalmTests(SimpleTestCase):
    """The cloth keeps small text legible everywhere (spec 3.1, 9) by its own range: CALM of its variation and a soft floor
    (Paper) or ceiling (Ink) on its luma, tied to --muted. No element asks for a clearing."""

    def constant(self, name):
        found = re.search(rf"^const {name} = ([0-9.]+);", VEIL.read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(found, f"static/js/veil.js: `const {name} = ...;` is the number the cloth's calm is tuned by (spec 3.1)")
        return float(found.group(1))

    def test_muted_text_holds_4_5_to_1_over_the_darkest_and_the_lightest_pixel_the_cloth_can_draw(self):
        # The worst pixel is the paper moved FLOOR down (Paper) or CEILING up (Ink) in every channel, and the grain
        # (added after: at most GRAIN_PAPER in Paper, GRAIN_INK in Ink) as far again. Measured over the cloth itself the
        # ratios are a little higher (4.682 and 4.838, over 1,496 moments): the cloth's colours do not move by the same
        # amount in every channel. Measuring them again, for a different --muted, is `contrast_problems` in
        # site-tests/run_site_checks.py.
        paper, ink = shader_colours()["PAPER"]
        darkest = tuple(value - self.constant("FLOOR") - self.constant("GRAIN_PAPER") for value in paper)
        lightest = tuple(value + self.constant("CEILING") + self.constant("GRAIN_INK") for value in ink)
        for name, pixel, token in (("Paper", darkest, PAPER_TOKENS["--muted"]), ("Ink", lightest, INK_TOKENS["--muted"])):
            with self.subTest(appearance=name):
                self.assertGreaterEqual(
                    contrast(pixel, rgb(token)), 4.5,
                    f"static/js/veil.js: with FLOOR {self.constant('FLOOR')} and CEILING {self.constant('CEILING')}, --muted {token} "
                    f"(static/css/tokens.css) would fall below 4.5:1 over the cloth's {'darkest' if name == 'Paper' else 'lightest'} pixel in {name}")

    def test_the_film_grain_stays_inside_what_the_calm_was_measured_with(self):
        # FLOOR and CEILING were measured with a grain of +-0.025 (spec 3.1); a stronger one needs them measured again. Ink's
        # grain is weaker than Paper's (spec 3.1), as earendil.com's is.
        paper, ink = self.constant("GRAIN_PAPER"), self.constant("GRAIN_INK")
        self.assertLessEqual(paper, 0.025, "static/js/veil.js: GRAIN_PAPER is stronger than the grain FLOOR and CEILING were measured with; measure them again")
        self.assertLessEqual(ink, 0.025, "static/js/veil.js: GRAIN_INK is stronger than the grain FLOOR and CEILING were measured with; measure them again")
        self.assertLess(ink, paper, "static/js/veil.js: the grain in Ink should be weaker than in Paper")
        self.assertEqual(self.constant("GRAIN_RATE"), 1.5, "static/js/veil.js: the grain drifts about 1.5 cycles a second (spec 3.1)")

    def test_the_cloth_is_calmed_not_cleared(self):
        self.assertLess(self.constant("CALM"), 1)
        # Not flattened either: a flat cloth has perfect contrast, so every check of the text would pass with the folds gone
        # (spec 3.1: they still read as cloth). 0.70 is the calm that was measured; the browser checks hold a floor on the
        # spread of the canvas's luma (whole_canvas_contrast_problems), this is the cheap guard beside it.
        self.assertGreaterEqual(self.constant("CALM"), 0.5, "static/js/veil.js: CALM this low flattens the folds; the cloth must still read as cloth (spec 3.1)")
        source = VEIL.read_text(encoding="utf-8")
        for gone in ("data-clearing", "u_clear", "boxDist", "clearings("):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, source, f"static/js/veil.js: the cloth is calm everywhere; there are no clearings ({gone})")

    def test_no_template_asks_for_a_clearing(self):
        templates = Path(settings.BASE_DIR) / "templates"
        for path in sorted(templates.rglob("*.html")):
            with self.subTest(template=str(path.relative_to(templates))):
                self.assertNotIn("data-clearing", path.read_text(encoding="utf-8"),
                                 f"{path.relative_to(templates)}: veil.js has no clearings; small text needs --muted or --ink and nothing else")


class PhoneSheetTests(SimpleTestCase):
    """The cloth draws the phone menu's sheet (spec 4.4, 3.4): paper over the cloth under the washes, with the sheet's own lengths and easings."""

    def setUp(self):
        self.veil = VEIL.read_text(encoding="utf-8")
        self.menu = (STATIC / "css" / "components" / "menu.css").read_text(encoding="utf-8")

    def test_the_shader_lays_paper_over_the_cloth_by_a_share_that_rises_to_the_middle_before_the_washes(self):
        mix = "col = mix(col, PAPER, u_sheet * clamp(uv.x * 2.0, 0.0, 1.0));"
        self.assertIn("uniform float u_sheet;", self.veil)
        self.assertIn(mix, self.veil, "static/js/veil.js: the sheet is paper mixed in by u_sheet times a share rising linearly from the left edge to half the width")
        order = [self.veil.index(part) for part in ("col = mix(PAPER, col, u_cloth);", mix, "for (int i = 0; i < ${MAX_INKS}", "vec3 phase = hash32")]
        self.assertEqual(order, sorted(order), "static/js/veil.js: the sheet comes after the cloth and before the washes and the grain, so both lie on it")

    def test_it_has_no_clock_or_curve_of_its_own_and_takes_the_share_of_the_frame_from_the_driver_shell_js_hands_it(self):
        # shell.js advances the one value (--sheet) once per drawn frame (tests_shell.PhoneSheetDriverTests); the paper draws that value, so it moves
        # in lockstep with the links and the page's fade-out, and a late frame cannot finish a fade of its own unseen.
        for old in ("bezier", "SHEET_IN", "SHEET_OUT", "stepSheet", "sheetFrom", "sheetSince"):
            self.assertNotIn(old, self.veil, f"static/js/veil.js: no {old}; the paper's share is the driver's value, not a timed curve of its own")
        self.assertIn("export function start({ canvas, gl, off, ended, sheet = () => 0 }) {", self.veil)
        self.assertIn("const share = leaving || !SHEETED.matches ? 0 : sheet(time);", self.veil,
                      "static/js/veil.js: u_sheet is the driver's value for this frame (the driver's step is idempotent for a frame's time), taken before anything reads "
                      "where the links are; 0 once the cloth is leaving, and 0 where the open menu is a column, where the driver jumps to the end and this guard keeps the paper off")
        self.assertIn("gl.uniform1f(at.sheet, share);", self.veil)

    def test_it_is_drawn_at_the_widths_where_the_menu_is_a_sheet(self):
        # shell.js keeps the value at 1 for an open menu at any width (a column from 64rem up): the paper must not cover the cloth there
        self.assertIn('const SHEETED = window.matchMedia("(max-width: 63.99rem)");', self.veil)
        self.assertIn("@media (max-width: 63.99rem) {", self.menu)

    def test_the_held_wash_follows_the_menu_being_open_not_the_sheet_being_displayed(self):
        # The sheet is displayed until its close has run (shell.js keeps is-open for that), but the wash is let go when the menu is shut.
        self.assertIn("""const SHEET = "[data-menu-toggle][aria-expanded='true'] + #shell-menu";""", self.veil)
        self.assertIn("const HELD = `${SHEET} a[aria-current='page']", self.veil)
        self.assertNotIn("is-open", self.veil, "static/js/veil.js: the wash follows aria-expanded, not the class that keeps the sheet displayed")
        self.assertNotIn("is-shown", self.veil)

    def test_the_sheet_is_inked_like_the_rest_and_a_change_that_cross_fades_takes_it_away_at_once(self):
        inkable = self.veil[self.veil.index("function inkable"):self.veil.index("// An element's box in CSS pixels")]
        self.assertNotIn("SHEET", inkable, "static/js/veil.js: the open sheet is inked now (it is drawn in the canvas, under the washes)")
        self.assertNotIn("WIDE", self.veil)
        # the driver takes the share to 0 at once at veilfile:leaving (tests_shell.PhoneSheetDriverTests) and the next frame draws it; Motion off is
        # `leaving ? 0 : ...` above
        self.assertNotIn("veilfile:leaving", self.veil, "static/js/veil.js: it does not listen for a change in place; the driver does, and the next frame draws its value")
        self.assertNotIn("onChange", self.veil)


class PointerInAChangeTests(SimpleTestCase):
    """AGENTS.md, "The pointer in a change": Chromium takes the pointer from the page while a change is paused and gives it back (a pointerout to the document element,
    a pointerover from it), and the wash under the control must carry on; the browser checks (ink_change_problems) make it happen, these hold the code's two halves."""

    def setUp(self):
        self.veil = VEIL.read_text(encoding="utf-8")

    def test_a_pointer_that_comes_back_from_the_document_element_takes_the_wash_it_let_go_back(self):
        over = self.veil[self.veil.index("pointerover:"):self.veil.index("pointerout:")]
        self.assertIn("relatedTarget === root", over, "static/js/veil.js: the pointer coming back from the document element resumes the wash")
        self.assertIn("resume(", over)
        self.assertEqual(self.veil.count("ink.dead = null"), 1, "static/js/veil.js: one function takes a wash back, for a held link and for the pointer")

    def test_a_pointer_that_leaves_still_lets_the_wash_go_at_once(self):
        out = self.veil[self.veil.index("pointerout:"):self.veil.index("focusin:")]
        self.assertNotIn("root", out, "static/js/veil.js: a pointerout is never held back or waited on (a wash held for a pointer that has gone would stay for ever)")
        self.assertIn("endInk(element)", out)

    def test_the_comment_points_at_words_that_exist(self):
        agents = (Path(settings.BASE_DIR).parent / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn('AGENTS.md, "The pointer in a change"', self.veil)
        self.assertIn("**The pointer in a change.**", agents, "AGENTS.md: veil.js sends its reader to this heading")
