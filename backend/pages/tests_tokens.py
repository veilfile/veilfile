"""Design tokens (spec 3.2): the colors are exactly the spec's, for Paper and for Ink, and live only in tokens.css."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

CSS = Path(settings.BASE_DIR) / "static" / "css"
PAPER = {
    "--paper": "#ebe6de", "--ink": "#2c2925", "--muted": "#534e47",
    "--hair": "rgba(44, 41, 37, .2)", "--fill": "rgba(44, 41, 37, .12)", "--box": "rgba(240, 236, 229, .35)",
    "--grain": 'url("../img/grain-paper.png")',
}
INK = {
    "--paper": "#1b1917", "--ink": "#e6e0d6", "--muted": "#aea79c",
    "--hair": "rgba(230, 224, 214, .2)", "--fill": "rgba(230, 224, 214, .14)", "--box": "rgba(30, 28, 26, .35)",
    "--grain": 'url("../img/grain-ink.png")',
}


def luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first, second):
    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def rules(css):
    """Each innermost rule as (selector text, {custom property: value})."""
    found = []
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        properties = {name: value.strip() for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body)}
        found.append((" ".join(selector.split()), properties))
    return found


class TokenTests(SimpleTestCase):
    def setUp(self):
        self.css = (CSS / "tokens.css").read_text(encoding="utf-8")
        self.rules = rules(self.css)

    def find(self, *selector_parts):
        for selector, properties in self.rules:
            if all(part in selector for part in selector_parts) and "--paper" in properties:
                return properties
        self.fail(f"no rule with {selector_parts} defines --paper")

    def test_paper_is_the_default(self):
        properties = self.find(":root", '[data-theme="light"]')
        self.assertEqual({name: properties[name] for name in PAPER}, PAPER)

    def test_ink_applies_to_data_theme_dark(self):
        for selector, properties in self.rules:
            if selector == '[data-theme="dark"]':
                self.assertEqual({name: properties[name] for name in INK}, INK)
                return
        self.fail('no [data-theme="dark"] rule')

    def test_auto_follows_a_dark_system(self):
        self.assertIn("@media (prefers-color-scheme: dark)", self.css)
        properties = self.find(":root:not(", '[data-theme="system"]')
        self.assertEqual({name: properties[name] for name in INK}, INK)

    def colour_tokens(self):
        """The Paper block's tokens that are colours: all but the grain, an image, which stays discrete."""
        return {name: value for name, value in self.find(":root", '[data-theme="light"]').items() if not value.startswith("url(")}

    def registered(self):
        """Each @property rule in tokens.css as {token: {descriptor: value}}."""
        return {name: {key: value.strip() for key, value in re.findall(r"([\w-]+)\s*:\s*([^;]+);", body)}
                for name, body in re.findall(r"@property\s+(--[\w-]+)\s*\{([^{}]*)\}", self.css)}

    def test_every_colour_token_is_registered_so_it_fades_with_the_appearance_switch(self):
        # A custom property only transitions once it is registered with a syntax, and the scene fade (base.css) runs
        # on the tokens themselves: a colour token without an @property rule switches at once, not over --scene-fade.
        registered = self.registered()
        for name, value in self.colour_tokens().items():
            with self.subTest(token=name):
                rule = registered.get(name)
                self.assertIsNotNone(rule, f"static/css/tokens.css: add an @property rule for {name} (syntax \"<color>\", "
                                           f"inherits: true, initial-value its Paper value {value}), or it will not fade with the appearance switch")
                self.assertEqual(rule.get("syntax"), '"<color>"', f"static/css/tokens.css: @property {name} needs syntax: \"<color>\"")
                self.assertEqual(rule.get("inherits"), "true", f"static/css/tokens.css: @property {name} needs inherits: true")
                self.assertEqual(rule.get("initial-value"), value, f"static/css/tokens.css: @property {name} needs initial-value: {value}, its Paper value")

    def test_the_scene_fade_names_every_colour_token(self):
        base = (CSS / "base.css").read_text(encoding="utf-8")
        found = re.search(r"\nhtml\s*\{\s*transition:\s*([^;]+);", base)
        self.assertIsNotNone(found, "static/css/base.css: the scene fade is an `html { transition: --paper var(--scene-fade) ease, ... }` rule")
        for name in self.colour_tokens():
            with self.subTest(token=name):
                self.assertIn(f"{name} var(--scene-fade) ease", found.group(1),
                              f"static/css/base.css: add `{name} var(--scene-fade) ease` to the html transition, or {name} switches at once")

    def test_type_tokens_name_the_self_hosted_families_first(self):
        for name, family in (("--font-display", '"Cormorant Garamond"'), ("--font-reading", '"Newsreader"'), ("--font-mono", '"IBM Plex Mono"')):
            with self.subTest(token=name):
                self.assertRegex(self.css, rf"{name}:\s*{re.escape(family)}, {re.escape(family[:-1])} Fallback\"")

    def test_text_meets_wcag_aa_on_the_ground_in_both_appearances(self):
        # axe-core cannot measure text over the grain image, so the palette itself is checked here (spec 9).
        for name, palette in (("Paper", PAPER), ("Ink", INK)):
            with self.subTest(appearance=name):
                self.assertGreaterEqual(contrast(palette["--muted"], palette["--paper"]), 4.5)
                self.assertGreaterEqual(contrast(palette["--ink"], palette["--paper"]), 7)

    def test_the_mark_is_whole_pixel_cells(self):
        # The mark is 4 cells across: 24px (6px cells) on phones, 28px (7px cells) from 64rem up, so no cell is ever a fraction of a pixel.
        phone = re.search(r"--mark-size: (\d+)px;", self.css)
        wide = re.search(r"@media \(min-width: 64rem\) \{\s*:root \{\s*--mark-size: (\d+)px;", self.css)
        self.assertEqual((phone.group(1), wide.group(1)), ("24", "28"))
        for size in (phone.group(1), wide.group(1)):
            self.assertEqual(int(size) % 4, 0)

    def test_label_size_and_corners_follow_the_spec(self):
        self.assertIn("--label-size: clamp(13px, 0.35vw + 11.6px, 14px);", self.css)
        self.assertIn("--label-tracking: .14em;", self.css)
        # Block-direction offsets are a share of the viewport's height wherever they are used
        # (a percentage means height for the fixed corners but width in padding).
        self.assertIn("--edge-block: 5.5vh;", self.css)
        self.assertIn("--edge-inline: 5%;", self.css)
