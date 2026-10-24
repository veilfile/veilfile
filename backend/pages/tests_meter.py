"""The meter (spec 4.8, 6.2): one figure of the dashboard against its limit, as a mono label, the numbers in words and a hairline bar.

The numbers are always in the text, so the bar adds nothing a reader would miss. The bar itself is `role="meter"` (what a native <meter> means,
and not a progress bar, which says a task is under way), named by the label and described by the figures, over an SVG drawn from attributes:
a page may not carry an inline style. Every class sets longMessage = False and says which file to fix.
"""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase

from pages import catalog
from pages.tests_chapters import BANNED
from pages.tests_reading import declarations, rules
from pages.tests_shell import render_shell

STATIC = Path(settings.BASE_DIR) / "static"
FILE = "backend/templates/components/meter.html"
CSS = "backend/static/css/components/meter.css"
GB = 1024**3


def meter(**inputs):
    """components/meter.html as a page includes it: with these inputs and nothing else."""
    return render_to_string("components/meter.html", {"id": "t-one", "label": "Successful uploads this month", "used": 37, "cap": 100, **inputs})


def figures(html):
    """The words of the figures line as a reader sees them."""
    found = re.search(r'<p class="meter__figures" id="[^"]*">(.*?)</p>', html, re.S)
    assert found, f"{FILE}: a meter has a <p class=\"meter__figures\" id=...> line"
    return " ".join(re.sub(r"<[^>]+>", " ", found.group(1)).split())


def bar(html):
    """The attributes of the bar (the element with role=meter), or None when there is none."""
    found = re.search(r'<div ([^>]*class="meter__bar"[^>]*)>', html)
    return dict(re.findall(r'([\w-]+)="([^"]*)"', found.group(1))) if found else None


class MeterTests(SimpleTestCase):
    longMessage = False

    def test_a_figure_with_a_limit_is_a_label_the_numbers_and_a_bar_that_names_both(self):
        html = meter()
        self.assertIn('<p class="meter__label label" id="t-one_label">Successful uploads this month</p>', html,
                      f'{FILE}: the label is <p class="meter__label label" id="<id>_label"> (mono, uppercase, muted: base.css .label)')
        self.assertEqual(figures(html), "37 of 100", f"{FILE}: the figures say used of cap, in words")
        attrs = bar(html)
        self.assertEqual(attrs, {"class": "meter__bar", "role": "meter", "aria-labelledby": "t-one_label", "aria-describedby": "t-one_figures",
                                 "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "37"},
                         f'{FILE}: the bar is role="meter", named by the label, described by the figures, with its minimum, maximum and value; found {attrs}')
        self.assertIn('<svg viewBox="0 0 1000 1" preserveAspectRatio="none" aria-hidden="true"><rect class="meter__fill" width="370" height="1"/></svg>', html,
                      f"{FILE}: the fill is a rect whose width is the figure in thousandths of the limit (37 of 100 is 370 of 1000), in a viewBox 1000 wide, hidden from screen readers (the text says the numbers)")
        self.assertLess(html.index("meter__label"), html.index("meter__figures"))
        self.assertLess(html.index("meter__figures"), html.index("meter__bar"), f"{FILE}: label, figures, bar, in that order")

    def test_the_names_the_bar_points_at_exist_on_the_page(self):
        html = meter(id="catalog-x-0")
        for token in (bar(html)["aria-labelledby"], bar(html)["aria-describedby"]):
            self.assertEqual(html.count(f'id="{token}"'), 1, f"{FILE}: aria-labelledby and aria-describedby name ids that the component prints once, built from its id input")

    def test_sizes_are_written_by_the_bytes_filter_and_the_bar_is_drawn_in_bytes(self):
        html = meter(label="Storage", used=GB * 3 // 2, cap=25 * GB, unit="bytes")
        self.assertEqual(figures(html), "1.5 GB of 25.0 GB", f"{FILE}: with unit=bytes, used and cap go through the bytes filter (1024 base, one decimal)")
        self.assertEqual(bar(html)["aria-valuemax"], str(25 * GB), f"{FILE}: the bar's numbers are bytes, not the words")
        self.assertIn('viewBox="0 0 1000 1"', html, f"{FILE}: the viewBox is 1000 wide whatever the limit, so a size in bytes never puts a number of ten digits in a coordinate")
        self.assertIn('<rect class="meter__fill" width="60" height="1"/>', html, f"{FILE}: 1.5 GB of 25 GB is 60 thousandths")
        self.assertEqual(figures(meter(used=0, cap=GB, unit="bytes")), "0 B of 1.0 GB", f"{FILE}: nothing used is 0 B")

    def test_counts_are_grouped_in_the_words_and_plain_in_the_bar(self):
        html = meter(used=1234, cap=10000)
        self.assertEqual(figures(html), "1,234 of 10,000", f"{FILE}: print counts with floatformat:\"0g\", which groups by thousands")
        self.assertEqual((bar(html)["aria-valuenow"], bar(html)["aria-valuemax"]), ("1234", "10000"), f"{FILE}: the bar's attributes are plain numbers")

    def test_over_its_limit_the_figures_say_so_in_words_and_the_bar_stays_full(self):
        html = meter(label="API keys", used=7, cap=5)
        self.assertEqual(figures(html), "7 of 5 Over the limit", f"{FILE}: past the cap the figures end with Over the limit, in words, not in colour")
        self.assertIn('<span class="meter__flag label">Over the limit</span>', html)
        self.assertTrue(html.lstrip().startswith('<div class="meter meter--over">'), f"{FILE}: the wrapper takes meter--over, which the stylesheet draws the bar's line solid for")
        self.assertEqual((bar(html)["aria-valuenow"], bar(html)["aria-valuemax"]), ("5", "5"), f"{FILE}: a meter's value cannot pass its maximum, so the bar is full at the cap")
        self.assertIn('<rect class="meter__fill" width="1000" height="1"/>', html, f"{FILE}: past the cap the fill is the whole bar")
        self.assertIn("7 of 5", html, f"{FILE}: the words keep the true figure, for the description")

    def test_at_its_limit_is_full_and_not_over(self):
        html = meter(used=5, cap=5)
        self.assertEqual(figures(html), "5 of 5")
        self.assertNotIn("meter--over", html, f"{FILE}: only a figure past its cap is over")
        self.assertNotIn("Over the limit", html)

    def test_no_limit_says_unlimited_in_words_and_draws_no_bar(self):
        html = meter(label="API keys", used=3, cap=None, unlimited=True)
        self.assertEqual(figures(html), "3 of unlimited", f"{FILE}: with unlimited and no cap the figures say of unlimited")
        self.assertIsNone(bar(html), f"{FILE}: no limit, nothing to fill: draw no bar")
        self.assertNotIn("<svg", html)
        self.assertNotIn("meter--over", html)

    def test_a_cap_given_with_unlimited_is_still_a_cap(self):
        self.assertEqual(figures(meter(used=3, cap=5, unlimited=True)), "3 of 5", f"{FILE}: unlimited only speaks when there is no cap")

    def test_a_figure_with_no_limit_and_a_unit_is_a_value(self):
        self.assertEqual(figures(meter(label="Links last up to", used=7, cap=None, unit="day")), "7 days", f"{FILE}: a unit word follows the number, plural when it should be")
        self.assertEqual(figures(meter(used=1, cap=None, unit="day")), "1 day")
        self.assertIsNone(bar(meter(used=7, cap=None, unit="day")), f"{FILE}: a value with no limit has no bar")

    def test_a_cap_of_zero_draws_no_bar(self):
        html = meter(used=0, cap=0)
        self.assertEqual(figures(html), "0 of 0")
        self.assertIsNone(bar(html), f"{FILE}: a bar needs a limit above 0; a viewBox 0 wide draws nothing and fails to render")

    def test_a_note_is_a_line_under_the_bar(self):
        html = meter(note="Counted by calendar month, in UTC.")
        self.assertIn('<p class="meter__note">Counted by calendar month, in UTC.</p>', html, f"{FILE}: note is printed under the bar")
        self.assertLess(html.index("meter__bar"), html.index("meter__note"))
        self.assertNotIn("meter__note", meter(), f"{FILE}: no note, no line")

    def test_words_are_escaped(self):
        html = meter(label='<b>Uploads</b> & "more"', note="<i>x</i>")
        for raw in ("<b>", "<i>"):
            self.assertNotIn(raw, html, f"{FILE}: print the label and the note escaped")
        self.assertIn("&lt;b&gt;Uploads&lt;/b&gt; &amp; &quot;more&quot;", html)

    def test_the_template_keeps_the_voice(self):
        text = (Path(settings.BASE_DIR) / "templates" / "components" / "meter.html").read_text(encoding="utf-8")
        words = re.sub(r"{%.*?%}|{#.*?#}|{{.*?}}", "", text)  # a template tag may say != ; the words may not shout
        self.assertNotIn("\N{EM DASH}", text, f"{FILE}: no em dashes; use a colon, a comma or two sentences")
        self.assertNotIn("!", words, f"{FILE}: no exclamation marks")
        for word in BANNED:
            self.assertNotIn(word, text.lower(), f"{FILE}: avoid {word.strip()!r} (AGENTS.md, voice)")

    def test_nothing_is_styled_inline(self):
        for inputs in ({}, {"used": 9, "cap": 5}, {"cap": None, "unlimited": True}, {"unit": "bytes", "used": GB, "cap": 2 * GB}):
            with self.subTest(inputs=inputs):
                self.assertNotRegex(meter(**inputs), r"\sstyle\s*=", f"{FILE}: no style attribute; the fill's width is an SVG attribute (tests_site_rules holds every shell template to it)")


class MeterCssTests(SimpleTestCase):
    longMessage = False

    def setUp(self):
        self.css = (STATIC / "css" / "components" / "meter.css").read_text(encoding="utf-8")

    def test_the_sheet_is_linked_after_base_css(self):
        html = render_shell()
        link = 'href="/static/css/components/meter.css"'
        self.assertIn(link, html, "backend/templates/shell.html: link css/components/meter.css with the other component sheets")
        self.assertGreater(html.index(link), html.index('href="/static/css/base.css"'),
                           "backend/templates/shell.html: link css/components/meter.css after css/base.css, so the component's rules come last")

    def test_the_bar_is_a_hairline_box_filled_with_ink(self):
        box = declarations(self.css, ".meter .meter__bar")
        self.assertEqual(box.get("border"), "1px solid var(--hair)", f"{CSS}: the bar is a 1px --hair box, as the code block and the main action are")
        self.assertEqual(box.get("background"), "var(--box)")
        fill = next(properties for selectors, properties in rules(self.css) if ".meter .meter__fill" in selectors)  # the first rule: the forced-colours one comes after
        self.assertEqual(fill.get("fill"), "var(--ink)", f"{CSS}: the fill is --ink")
        self.assertEqual(declarations(self.css, ".meter--over .meter__bar").get("border-color"), "var(--ink)", f"{CSS}: over its cap the bar's line goes solid --ink (no colour is added)")

    def test_a_forced_colours_screen_keeps_the_fill(self):
        self.assertRegex(self.css, r"@media \(forced-colors: active\) \{\s*\.meter \.meter__fill \{\s*fill: CanvasText;",
                         f"{CSS}: in forced colours the fill takes the system text colour, or it may match the box and show a full bar")

    def test_nothing_moves_or_stacks(self):
        for selectors, properties in rules(self.css):
            for name in ("z-index", "opacity", "transition", "animation", "position", "filter", "will-change"):
                self.assertNotIn(name, properties, f"{CSS}: `{selectors[0]}` sets {name}; a meter is still, in the page's flow, and must not make <main> a backdrop root (AGENTS.md, Site checks)")

    def test_the_sheet_has_no_colour_but_tokens_and_no_em_dash(self):
        self.assertNotRegex(self.css, r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch|lab|lch)\(", f"{CSS}: colours come from tokens.css")
        self.assertNotIn("\N{EM DASH}", self.css, f"{CSS}: no em dash, in comments too")


class MeterCatalogTests(TestCase):
    longMessage = False

    def setUp(self):
        staff = get_user_model().objects.create_user("meter-staff", "meter-staff@example.com", "a-test-only-password", is_staff=True)
        self.client.force_login(staff)
        self.html = self.client.get("/app/components/").content.decode()
        self.entry = next(entry for entry in catalog.entries() if entry.name == "meter")
        self.samples = (self.entry.context, *self.entry.states)

    def test_the_catalog_shows_a_figure_with_a_limit_in_bytes_over_its_limit_with_none_and_a_value(self):
        used_over = [s for s in self.samples if s.get("cap") and s["used"] > s["cap"]]
        self.assertTrue(used_over, "backend/pages/catalog.py: the meter's Entry shows a figure over 100% of its cap")
        self.assertTrue([s for s in self.samples if s.get("unlimited") and s.get("cap") is None], "backend/pages/catalog.py: the meter's Entry shows unlimited")
        self.assertTrue([s for s in self.samples if s.get("unit") == "bytes"], "backend/pages/catalog.py: the meter's Entry shows a size")
        self.assertTrue([s for s in self.samples if s.get("cap") is None and not s.get("unlimited")], "backend/pages/catalog.py: the meter's Entry shows a value with no limit")
        self.assertEqual(len({s["id"] for s in self.samples}), len(self.samples), "backend/pages/catalog.py: each state needs an id of its own")

    def test_every_state_is_drawn_in_every_frame(self):
        for sample in self.samples:
            for frame in range(4):
                with self.subTest(state=sample["id"], frame=frame):
                    self.assertIn(f'id="{sample["id"]}-{frame}_label"', self.html, "backend/pages/catalog.py: the frame draws every state of the meter, numbered by frame")

    def test_the_meters_are_shown_inside_their_list(self):
        self.assertEqual(self.entry.within, "meters", 'backend/pages/catalog.py: pass within="meters" to the meter Entry, so the states stack in the width the Overview gives them')
        self.assertEqual(self.html.count('<div class="meters">'), 4)

    def test_the_bars_and_the_over_flags_are_counted(self):
        with_bar = len([s for s in self.samples if s.get("cap")])
        self.assertEqual(self.html.count('role="meter"'), 4 * with_bar, "backend/pages/catalog.py: a state with a cap draws one bar in each of the four frames")
        over = len([s for s in self.samples if s.get("cap") and s["used"] > s["cap"]])
        self.assertEqual(self.html.count("Over the limit"), 4 * over)
