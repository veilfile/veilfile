"""The shell (spec 4.1, 6.2, 6.4): the head, the boot script, the four corners and <main>."""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

STATIC = Path(settings.BASE_DIR) / "static"
MARK_PATH = "M0 0H560V140H420V280H280V420H140V560H0Z M420 280H560V420H420Z M280 420H420V560H280Z"


def render_shell(user=None):
    request = RequestFactory().get("/")
    request.user = user or AnonymousUser()
    return render_to_string("shell.html", {}, request=request)


class ShellHeadTests(SimpleTestCase):
    def setUp(self):
        self.html = render_shell()

    def test_charset_comes_first_then_the_boot_script_then_styles(self):
        charset = self.html.index('<meta charset="utf-8">')
        boot = self.html.index('<script id="veilfile-shell-boot">')
        styles = self.html.index('<link rel="stylesheet"')
        self.assertLess(charset, boot)
        self.assertLess(boot, styles)

    def test_boot_script_reads_the_shared_preferences_and_falls_back(self):
        boot = self.html[self.html.index('<script id="veilfile-shell-boot">'):]
        boot = boot[:boot.index("</script>")]
        for expected in (
            'localStorage.getItem("veilfile-theme")',
            'localStorage.getItem("veilfile-motion")',
            # A device that was too slow earlier in this tab: the still page from the first paint (spec 3.3, 8).
            'sessionStorage.getItem("veilfile-veil-off") === "slow"',
            'root.setAttribute("data-veil-off", "slow");',
            'if (theme !== "light" && theme !== "dark") theme = "system";',
            "(prefers-reduced-motion: reduce)",
            'root.setAttribute("data-theme", theme);',
            'root.setAttribute("data-motion", motion);',
            # .js (which hides the menu behind MENU) only where shell.js, a module, can run.
            'if ("noModule" in HTMLScriptElement.prototype) root.classList.add("js");',
            "catch (error)",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, boot)

    def test_links_every_stylesheet_the_shell_uses(self):
        for path in ("css/fonts.css", "css/tokens.css", "css/base.css", "css/components/mark.css",
                     "css/components/lockup.css", "css/components/menu.css", "css/components/controls.css"):
            with self.subTest(path=path):
                self.assertIn(f'<link rel="stylesheet" href="/static/{path}">', self.html)
                self.assertTrue((STATIC / path).is_file())

    def test_preloads_the_reading_and_label_fonts(self):
        # Every shell page sets text in Newsreader and its corners in IBM Plex Mono. The display face
        # is preloaded only by pages that show display text (block preload_display), because an
        # unused preload wastes the download and warns in the console.
        for path in ("fonts/newsreader/normal-400-500-latin.woff2", "fonts/ibm-plex-mono/normal-400-latin.woff2"):
            with self.subTest(path=path):
                self.assertIn(f'<link rel="preload" href="/static/{path}" as="font" type="font/woff2" crossorigin>', self.html)
        self.assertNotIn("cormorant-garamond/italic-300-latin.woff2", self.html)

    def test_declares_the_favicons(self):
        for snippet in ('href="/static/img/favicon.svg" type="image/svg+xml" sizes="any"', 'href="/static/img/favicon-32.png"',
                        'href="/static/img/favicon-16.png"', 'rel="apple-touch-icon" href="/static/img/apple-touch-icon.png"'):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, self.html)

    def test_loads_shell_js_as_a_module_and_falls_back_if_it_cannot(self):
        # If shell.js fails to load, dropping .js shows the no-JavaScript layout: links, no switches.
        self.assertIn('<script type="module" src="/static/js/shell.js" '
                      """onerror="document.documentElement.classList.remove('js')"></script>""", self.html)

    def test_loads_every_behaviour_as_a_module(self):
        names = sorted(path.stem for path in (STATIC / "js" / "behaviors").glob("*.js"))
        self.assertTrue(names, "no behaviours in static/js/behaviors/")
        for name in names:
            with self.subTest(behaviour=name):
                self.assertIn(f'<script type="module" src="/static/js/behaviors/{name}.js"></script>', self.html)


class StoredChoicesTests(SimpleTestCase):
    """The visitor's saved appearance and motion (spec 4.1, 6.4) are localStorage `veilfile-theme` and `veilfile-motion`. The boot script in
    shell.html reads them before the first paint (500.html reads the theme alone) and shell.js writes them, on a click. The dashboard used to
    be a second page that read them and wrote its own defaults; it is on the shell now, and these tests keep it to one reader and one writer."""

    longMessage = False
    BASE = Path(settings.BASE_DIR)

    def files_with(self, *needles):
        """The templates and scripts, as paths under backend/, whose text holds any of the needles."""
        found = []
        for folder, suffixes in (("templates", (".html", ".txt", ".xml")), ("static/js", (".js",))):
            for path in sorted((self.BASE / folder).rglob("*")):
                if path.suffix in suffixes and any(needle in path.read_text(encoding="utf-8") for needle in needles):
                    found.append(path.relative_to(self.BASE).as_posix())
        return sorted(found)

    def test_only_the_boot_script_the_500_page_and_shell_js_name_the_keys(self):
        self.assertEqual(self.files_with("veilfile-theme", "veilfile-motion"), ["static/js/shell.js", "templates/500.html", "templates/shell.html"],
                         "backend/templates/shell.html reads both keys, backend/static/js/shell.js writes them and backend/templates/500.html reads the theme. "
                         "Another file that names them is a second reader or writer: read data-theme and data-motion, which the boot script sets on <html>, "
                         "or change AGENTS.md (\"Page set-up\") and this test together")

    def test_only_shell_js_writes_to_local_storage(self):
        self.assertEqual(self.files_with("localStorage.setItem"), ["static/js/shell.js"],
                         "backend/static/js/shell.js saves the visitor's choice, on a click. A page that writes a default on load would make a visitor "
                         "who never touched a switch stop following the system's colours and its reduced-motion setting")


class ShellBodyTests(SimpleTestCase):
    def setUp(self):
        self.html = render_shell()

    def test_skip_link_comes_before_the_corners_and_targets_main(self):
        self.assertLess(self.html.index('class="skip-link label"'), self.html.index("<header>"))
        self.assertIn('<main id="main" class="shell-main">', self.html)

    def test_lockup_is_the_mark_alone_with_its_name_for_screen_readers(self):
        # Top left shows only the mark (owner's call, 2026-10-09); the name is read out, not shown.
        start = self.html.index('<a class="lockup label interactive" href="/">')
        lockup = self.html[start:self.html.index("</a>", start)]
        self.assertIn(f'd="{MARK_PATH}"', lockup)
        self.assertIn('<span class="visually-hidden">Veilfile</span>', lockup)
        self.assertNotIn("<span>Veilfile</span>", lockup)

    def test_menu_lists_the_signed_out_groups_in_order(self):
        nav = self.html[self.html.index("<nav"):self.html.index("</nav>")]
        labels = re.findall(r'<a class="label interactive" href="[^"]+"[^>]*>([^<]+)</a>', nav)
        self.assertEqual(labels, ["About", "How it works", "Begin", "Promises", "Plans", "Docs", "Journal", "Sign in", "Terms", "Privacy", "Report abuse", "llms.txt"])
        self.assertIn('aria-controls="shell-menu"', nav)
        self.assertIn('id="shell-menu"', nav)

    def test_signed_in_visitors_see_the_dashboard(self):
        html = render_shell(user=get_user_model()(username="reader"))
        self.assertIn('href="/app/">Dashboard</a>', html)
        self.assertNotIn(">Sign in</a>", html)

    def bottom_corner(self, side):
        start = self.html.index(f'<div class="shell-corner shell-corner--bottom-{side}">')
        return self.html[start:self.html.index("</div>\n", self.html.index("</div>", start) + 1)]

    def test_the_appearance_switch_is_bottom_left_and_the_motion_switch_bottom_right(self):
        # The row is the appearance switch at the left margin and the motion switch (FX) at the right, at every width (owner's
        # decision, 2026-10-15); the tagline that was bottom left is gone.
        self.assertNotIn("Private · Ephemeral", self.html)
        left, right = self.bottom_corner("left"), self.bottom_corner("right")
        self.assertIn("data-appearance-toggle", left)
        self.assertNotIn("data-motion-toggle", left)
        # The visible word is FX; the accessible name is "FX: motion", which holds the visible label (WCAG 2.5.3) and says what it does;
        # aria-pressed carries the state and the visible "· on" is hidden from the name.
        self.assertIn('aria-pressed="true" aria-label="FX: motion" data-motion-toggle>FX <span aria-hidden="true" data-motion-state>· on</span>', right)
        self.assertNotIn("Motion", right)
        self.assertNotIn("data-appearance-toggle", right)
        self.assertEqual(self.html.count("data-appearance-toggle"), 1)
        self.assertEqual(self.html.count("data-motion-toggle"), 1)

    def test_each_corner_is_a_plain_corner(self):
        # The cloth is calm everywhere (spec 3.1), so a corner asks it for nothing: no clearing attribute.
        for corner in ("top-left", "top-right", "bottom-left", "bottom-right"):
            with self.subTest(corner=corner):
                self.assertIn(f'<div class="shell-corner shell-corner--{corner}">', self.html)


class TopFadeTests(SimpleTestCase):
    """Below 64rem nothing is laid over the page along the top: its content fades out as it scrolls up toward the mark and
    MENU, through a mask on <main> that shell.js keeps at the top of the screen (spec 4.1)."""

    def test_no_bar_lies_over_the_page(self):
        base = (STATIC / "css/base.css").read_text(encoding="utf-8")
        self.assertNotIn("edge-veil", render_shell())
        self.assertNotIn("edge-veil", base)

    def test_only_main_fades_out_along_the_top_where_the_page_is_narrow(self):
        base = (STATIC / "css/base.css").read_text(encoding="utf-8")
        media = re.search(r"@media screen and \(max-width: 63\.99rem\) \{(.*?)\n\}\n", base, re.S)
        self.assertIsNotNone(media, "static/css/base.css: the mask along the top is in a `@media screen and (max-width: 63.99rem)` rule")
        rules = {selector.strip(): body for selector, body in re.findall(r"([^{}]+)\{([^}]*)\}", re.sub(r"/\*.*?\*/", "", media.group(1), flags=re.S))}
        mask = [selector for selector, body in rules.items() if "mask-image" in body]
        self.assertEqual(len(mask), 1, "static/css/base.css: one rule makes the mask along the top")
        selector, rule = mask[0], rules[mask[0]]
        self.assertIn(".shell-main", selector)
        # The footer waits at the page's end, where it cannot reach the top band: it carries no mask (which would make it
        # a stacking context and a layout read of its own on every scroll frame).
        self.assertNotIn("footer", selector)
        # What the browser scrolls to the top edge (a focused link, a #fragment's target) lands below the fade, not in the
        # band where it would be invisible (WCAG 2.4.11).
        padding = [selector for selector, body in rules.items() if re.search(r"scroll-padding-top:\s*calc\(var\(--fade-top\) \+ var\(--fade-band\)\)", body)]
        self.assertEqual(padding, [".js.shell-ready"], "static/css/base.css: the page keeps scroll-padding-top at the band and the fade, where the mask is")
        # The gradient's stops are placed from --fade-y, so the element is opaque below the band wherever it is (a mask
        # moved by its position would leave what is far below the band outside the image and hidden).
        self.assertRegex(rule, r"mask-image:\s*linear-gradient\(to bottom, transparent calc\(var\(--fade-y\) \+ var\(--fade-top\)\), "
                               r"var\(--mask-solid\) calc\(var\(--fade-y\) \+ var\(--fade-top\) \+ var\(--fade-band\)\)\)")
        self.assertNotIn("mask-position", rule)
        script = (STATIC / "js/shell.js").read_text(encoding="utf-8")
        self.assertIn('setProperty("--fade-y"', script, "static/js/shell.js moves the mask with the scroll: it sets --fade-y, which base.css uses")
        # <main> starts at the top of the document, so --fade-y is the scroll offset: no box is read to place it.
        self.assertIn("`${window.scrollY}px`", script, "static/js/shell.js sets --fade-y to the scroll offset")
        self.assertNotIn("getBoundingClientRect", script, "static/js/shell.js places the top fade from the scroll offset, not from a box it reads on every scroll frame")


class NoJavaScriptTests(SimpleTestCase):
    """Without JavaScript the menu's links stay visible and the switches that need it stay hidden."""

    def test_css_hides_only_behind_the_js_class(self):
        menu = (STATIC / "css/components/menu.css").read_text(encoding="utf-8")
        controls = (STATIC / "css/components/controls.css").read_text(encoding="utf-8")
        self.assertIn(".js .menu__list:not(.is-open)", menu)
        self.assertIn(".js .menu__toggle", menu)
        self.assertRegex(controls, r"\.controls\s*\{[^}]*display:\s*none")
        self.assertIn(".js .controls", controls)


class MenuStartsClosedTests(SimpleTestCase):
    """The menu starts closed on every page at every width and only MENU opens it (spec 4.4), as earendil.com's does; a change in place
    leaves it closed. The browser checks (menu_rest_problems, chapter_change_problems) read what the browser does; this holds the
    markup and the script to the same, so a menu pinned open or opened on load, which is what a chapter page's did, cannot come back."""

    def setUp(self):
        script = (STATIC / "js/shell.js").read_text(encoding="utf-8")
        self.menu = script[script.index("function setUpMenu"):script.index("function setUpAppearance")]

    def test_the_markup_arrives_closed(self):
        html = render_shell()
        self.assertIn('aria-expanded="false"', html)
        self.assertNotIn('aria-expanded="true"', html)
        self.assertNotIn("is-open", html)

    def test_the_script_opens_the_menu_only_for_MENU(self):
        calls = re.findall(r"setOpen\(((?:[^()]|\([^()]*\))*)\)", self.menu)
        self.assertEqual([call for call in calls if not call.startswith("false")], ["!isOpen(), false"],
                         "static/js/shell.js: setUpMenu opens the menu only when MENU is pressed (setOpen(!isOpen(), false)); every other call closes it")
        self.assertNotIn("pinned", self.menu, "static/js/shell.js: no menu stays open on its own, whatever the page or the width")

    def test_a_tap_on_the_sheet_itself_is_heard_on_the_sheet(self):
        # A WebKit tap on an element with no click listener of its own fires no click (the document's does not count), so the phone
        # sheet's backdrop tap, which closes it, is a listener on the sheet (site checks: the phone sheet in WebKit).
        listened = re.search(r'list\.addEventListener\("click", \(event\) => \{\s*(.*?)\s*\}\);', self.menu, re.S)
        self.assertTrue(listened, "static/js/shell.js: setUpMenu listens for a click on the sheet itself (list.addEventListener)")
        self.assertIn("PHONE.matches && event.target === list", listened.group(1))
        document = re.search(r'document\.addEventListener\("click", \(event\) => \{\s*(.*?)\s*\}\);', self.menu, re.S).group(1)
        self.assertNotIn("event.target === list", document, "static/js/shell.js: the sheet's own tap is heard on the sheet, not on the document")

    def test_a_change_in_place_shuts_the_menu_at_every_width(self):
        swapped = self.menu[self.menu.index('"veilfile:swapped"'):]
        self.assertRegex(swapped, r"if \(isShellMenu\) setOpen\(false, false\);")
        self.assertNotIn("PHONE", swapped, "static/js/shell.js: the menu is shut after a change in place at every width, not only on a phone")


class MotionCssTests(SimpleTestCase):
    def test_motion_off_stops_animations(self):
        # Motion off stops every CSS transition and animation; the menu has none of its own (its fade is shell.js' driver, which instant() takes to its end at once).
        base = (STATIC / "css/base.css").read_text(encoding="utf-8")
        menu = re.sub(r"/\*.*?\*/", "", (STATIC / "css/components/menu.css").read_text(encoding="utf-8"), flags=re.S)
        self.assertIn('[data-motion="off"] *,', base)
        for word in ("transition", "animation", "@keyframes"):
            self.assertNotIn(word, menu, f"menu.css: no {word}; the menu's fade, at every width, is --sheet, advanced per drawn frame by shell.js")


class PhoneSheetCssTests(SimpleTestCase):
    """The phone menu's sheet (spec 4.4): a surface of the page's ground masked to fade across the left half, and a fade in and out paced like
    Come in (.7s each way, ease), as ONE value, --sheet, that shell.js advances once per drawn frame and CSS only reads: the links' opacity and
    move, the paper the cloth draws and the page's fade-out under the right half all follow it, so none of them can finish between two late
    frames (a CSS transition, an animation or a curve timed from a clock can: CI's WebKit blinked). From 64rem up the column fades by the same
    value. The browser checks (phone_sheet_problems, cloth_menu_sheet_problems, sheet_slow_frames_problems) read what the browser does with these rules."""

    def setUp(self):
        self.menu = (STATIC / "css/components/menu.css").read_text(encoding="utf-8")
        self.base = (STATIC / "css/base.css").read_text(encoding="utf-8")
        self.rules = re.sub(r"/\*.*?\*/", "", self.menu, flags=re.S)
        self.phone = self.menu[self.menu.index("@media (max-width: 63.99rem)"):]
        self.phone_rules = re.sub(r"/\*.*?\*/", "", self.phone, flags=re.S)
        self.every_width = self.rules[:self.rules.index("@media (max-width: 63.99rem)")]

    def test_the_menus_opacity_and_the_groups_move_are_read_from_the_one_value(self):
        # opacity .01 at 0, so that WebKit paints the sheet from its first frame (it does not paint a layer at 0), up to 1, at every width: the
        # column from 64rem up fades by it too. Below 64rem the groups come from 6px up into place; the column does not move, as Come in moves
        # nothing. Only the menu in the corner: a menu shown in the page (the catalog's samples) has no driver and stays as it is.
        self.assertRegex(self.every_width, r"\.js \.shell-corner \.menu__list \{\s*opacity: max\(\.01, var\(--sheet\)\);\s*\}")
        self.assertNotIn("@media (min-width: 64rem)", self.every_width, "menu.css: the menu's opacity rule is for every width, the column's included")
        self.assertRegex(self.phone_rules, r"\.js \.shell-corner \.menu__group \{\s*transform: translateY\(calc\(\(1 - var\(--sheet\)\) \* -6px\)\);\s*\}")
        self.assertNotIn("translateY", self.every_width, "menu.css: only the phone sheet's links move")

    def test_css_times_nothing_about_the_menu(self):
        # A transition, an animation or a keyframe is a function of time and can be over between two late frames; the driver is the only clock, at every width.
        for word in ("transition", "animation", "@keyframes", "@starting-style", "allow-discrete", "is-shown", "will-change"):
            self.assertNotIn(word, self.rules, f"menu.css has no {word}; --sheet, advanced per drawn frame by shell.js, is all of the menu's fade")
        self.assertNotRegex(self.rules, r"\.menu__list(?:\:not\(\.is-open\)|\.is-open)[^{]*\{[^}]*opacity", "menu.css: the menu's opacity is not set by its open state any more")

    def test_a_closing_menu_takes_no_pointer(self):
        # is-open stays until the close has run, so the sheet, or the column, is displayed while MENU already says it is shut: it must not take
        # the tap that belongs to the page behind it. Only with the script: components/menu.html renders the toggle with aria-expanded="false" as
        # the list's sibling, so without .js (no JavaScript, shell.js not loading or throwing) the links on the page would take no tap at all.
        self.assertRegex(self.every_width, r'\.js \.menu__toggle\[aria-expanded="false"\] \+ \.menu__list \{\s*pointer-events: none;\s*\}')
        self.assertNotRegex(self.rules, r'(?<!\.js )\.menu__toggle\[aria-expanded="false"\] \+ \.menu__list', "menu.css: the closing menu's pointer rule is scoped to .js; without it the menu's links are the page's and take taps")
        self.assertIn('aria-expanded="false"', (STATIC.parent / "templates/components/menu.html").read_text(encoding="utf-8"),
                      "components/menu.html renders the toggle shut, which is why the rule above needs .js")

    def test_the_open_sheet_is_clickable_to_ios(self):
        # iOS Safari fires no click for a tap on an element it does not take for clickable; shell.js closes the menu on a click on the sheet.
        self.assertRegex(self.phone, r"\.js \.menu__list\.is-open \{\s*cursor: pointer;\s*\}")
        self.assertNotIn("cursor", self.menu[:self.menu.index("@media (max-width: 63.99rem)")], "menu.css: the pointer cursor is the phone sheet's only")

    def test_the_sheet_has_no_ground_of_its_own_and_its_surface_is_masked_grain_on_paper(self):
        self.assertNotRegex(self.phone, r"background: var\(--paper\)", "menu.css: the sheet's surface is a ::before, masked; the sheet itself has none")
        surface = re.search(r"\.js \.menu__list::before \{([^}]*)\}", self.base)
        self.assertTrue(surface, "base.css: the sheet's surface (.js .menu__list::before), beside the grain it draws")
        for declaration in ("background-color: var(--paper);", "background-image: var(--grain);", "background-size: var(--grain-size, auto);",
                            "mask-image: linear-gradient(to right, transparent 0%, var(--mask-solid) 50%, var(--mask-solid) 100%);"):
            self.assertIn(declaration, surface.group(1))

    def test_the_page_stays_put_while_the_sheet_is_open(self):
        self.assertRegex(self.phone, r"html:has\(\.menu__list\.is-open\) \{\s*overflow: hidden;")

    def test_the_items_do_not_fade_one_by_one_at_any_width(self):
        # the menu fades as one piece, the column as the sheet does, as Come in fades (the owner's ask, 2026-10-31)
        self.assertNotIn("menu-item-in", self.menu)
        self.assertNotRegex(self.rules, r"\.menu__list[^{]*\bli\b[^{]*\{", "menu.css: nothing is set on the menu's items one by one")


class TapHighlightTests(SimpleTestCase):
    """The browser's own highlight of a tapped element is off on the shell (spec 3.4): the site's press feedback, the flat fill or the ink wash, is the
    feedback. The open phone sheet has a click listener and `cursor: pointer` (a tap on its surface closes it), so a browser takes the whole sheet for the
    tapped element and washes it, blue in Chromium and grey on an iPhone, for the frame or two after every tap, over the sheet that is closing; the corner
    controls and the links flashed a box of their own. The highlight colour is inherited, so one declaration on the page covers the sheet, its links and
    the controls. (Measured in Chromium at 375 x 3: a tap on the open sheet washed 100% of the screen, a tap on MENU, Paper, FX, Copy or the mark a box of
    the element's size; with the colour transparent none showed. Set on the sheet's links only it left the sheet's wash.)"""

    def setUp(self):
        self.css = {path: re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S) for path in sorted((STATIC / "css").rglob("*.css"))}
        self.base = self.css[STATIC / "css/base.css"]

    def test_the_page_takes_no_highlight_for_a_tap_and_everything_inherits_it(self):
        page = re.search(r"^html \{([^}]*)\}", self.base, re.M)
        self.assertTrue(page, "static/css/base.css: the `html` rule")
        self.assertIn("-webkit-tap-highlight-color: transparent;", page.group(1),
                      "static/css/base.css: `html` sets -webkit-tap-highlight-color: transparent, which the sheet, its links and every control inherit")

    def test_no_rule_gives_a_tapped_element_a_highlight_back(self):
        for path, text in self.css.items():
            for declaration in re.findall(r"-webkit-tap-highlight-color:\s*([^;}]*)", text):
                self.assertEqual(declaration.strip(), "transparent", f"{path.relative_to(STATIC)}: a tap highlight over the sheet or a control; the site's press feedback is the feedback")

    def test_what_answers_a_press_instead_is_there(self):
        # On a touchscreen a control and an inline link show the flat fill while pressed (spec 3.4), or the cloth's wash where it runs.
        self.assertRegex(self.base, r"@media \(pointer: coarse\) \{\s*\.interactive:active \{[^}]*\}\s*\.interactive:active::before \{\s*background: var\(--fill\);")
        self.assertRegex(self.base, r"@media \(pointer: coarse\) \{\s*\.chapter :is\(p, dd\) a:not\(\.interactive, \.label\):active,\s*\.reading :is\(p, li, td, th, blockquote, dd\) a:not\(\.interactive, \.label\):active \{\s*background-color: var\(--fill\);")


class StaleWordsTests(SimpleTestCase):
    """Only a change that cross-fades (a link in the page, Back or Forward) takes the phone sheet away at once (spec 4.4, 4.5); a link chosen from the sheet closes it
    by its driver like any close and the old content fades out with it. The words that said every change in place took it away at once were left behind by the
    change that made the sheet's path its own (2026-10-17); they are looked for in the comments, the checks' words and the docs that describe the sheet."""

    ROOT = Path(settings.BASE_DIR).parent
    FILES = ("backend/static/css/base.css", "backend/static/css/components/menu.css", "backend/static/css/tokens.css", "backend/static/js/shell.js", "backend/static/js/veil.js",
             "backend/pages/tests_shell.py", "backend/pages/tests_veil.py", "site-tests/run_site_checks.py", "AGENTS.md", "docs/superpowers/specs/2026-10-08-site-redesign-design.md")

    def test_no_text_says_that_a_change_in_place_takes_the_sheet_away_at_once(self):
        stale = re.compile(r"in place\s+takes? the (?:sheet|menu) away at once|as does a change in place|a change in place takes it away at once|\(which takes it away at once\)")
        for name in self.FILES:
            text = (self.ROOT / name).read_text(encoding="utf-8")
            if name.endswith("tests_shell.py"):   # this class names the words it looks for
                text = text[:text.index("class StaleWordsTests")] + text[text.index("class PhoneSheetDriverTests"):]
            with self.subTest(file=name):
                self.assertIsNone(stale.search(text), f"{name}: a change in place does not take the sheet away at once; only one that cross-fades does (a link chosen from the sheet closes it by its driver)")

    def test_the_change_fade_token_says_what_the_sheets_path_takes(self):
        # Every change is paced like Come in (--fade), which fades its line out over .7s and then the install line in over .7s: a change from the
        # sheet fades the old content out with the sheet's close and the new in after it, each as long as the token, as each half of a fade out and
        # in is where there is no view transition.
        tokens = (STATIC / "css/tokens.css").read_text(encoding="utf-8")
        line = next(line for line in tokens.splitlines() if "--change-fade:" in line)
        token = float(re.search(r"--change-fade:\s*([\d.]+)s", line).group(1))
        self.assertEqual(token, float(re.search(r"--fade:\s*([\d.]+)s", tokens).group(1)), "static/css/tokens.css: a change in place is paced like Come in (--fade)")
        self.assertIn(f"over the sheet's close ({token:g}s)".replace("0.", "."), line, "static/css/tokens.css: --change-fade's comment gives the length of the sheet path's fade out")
        self.assertIn(f"the new in over another {token:g}s".replace("0.", "."), line, "static/css/tokens.css: --change-fade's comment gives the length of the sheet path's fade in")
        self.assertNotRegex(line, r"\.35s|\.18s|\.17s|sum to it", "static/css/tokens.css: --change-fade's comment still gives the old lengths")


class PhoneSheetDriverTests(SimpleTestCase):
    """shell.js is the one clock of the phone sheet's fade (spec 4.4): it advances --sheet once per drawn frame, by the frame's share of
    the fade and never by more than a quarter of it, so a late frame cannot make the fade finish unseen. The browser checks
    (sheet_slow_frames_problems, sheet_slow_first_frame_problems, sheet_fade_problems) read what the browser does with it."""

    def setUp(self):
        script = (STATIC / "js/shell.js").read_text(encoding="utf-8")
        self.script = script
        self.menu = script[script.index("function setUpMenu"):script.index("function setUpAppearance")]
        self.driver = script[script.index("function driver"):script.index("function setUpMenu")]

    def test_the_value_is_written_to_each_element_that_reads_it_once_per_step(self):
        # not on the root: inherited, a write there recalculates the style of every element on the page, every frame of the fade (8.5 ms at 6,000
        # elements); and an explicit `--sheet: inherit` chain is never updated by WebKit. The readers are the sheet, its groups of links (list.children)
        # and what the sheet covers, <main> and the corners (`covered`: the display control bottom right is one).
        self.assertIn('[list, ...list.children, ...covered].forEach((reader) => reader.style.setProperty("--sheet", v));', self.menu,
                      "static/js/shell.js: the driver's value is --sheet on each element that reads it: the sheet, its groups, <main> and the corners")
        self.assertNotIn("root.style.setProperty(\"--sheet\"", self.script, "static/js/shell.js: --sheet is not written to the root")
        self.assertIn('.skip-link, main, .shell-corner', self.menu, "static/js/shell.js: `covered` holds <main> and the corners, the readers of --sheet outside the sheet")

    def test_a_step_is_the_frames_share_of_the_fade_and_never_more_than_a_quarter(self):
        self.assertRegex(self.driver, r"Math\.min\(\.25, \(time - \(last \|\| time\)\) / ms\)",
                         "static/js/shell.js: a step is min(.25, dt / the fade's length), 0 for the first frame of a fade (last is 0)")
        self.assertRegex(self.driver, r"to = goal;\s*last = 0;", "static/js/shell.js: a fade begun or turned round has no time behind its first step")

    def test_the_menu_opens_and_closes_over_come_ins_fade(self):
        # The owner's ask (2026-10-31): the menu's transitions are paced like Come in's, the --fade token (.7s), opening and closing alike.
        found = re.search(r"\}, (\d+)\);\n  if \(isShellMenu\)", self.menu)
        self.assertTrue(found, "static/js/shell.js: the menu's driver is driver(write, ms)")
        come_in = re.search(r"--fade:\s*([\d.]+)s", (STATIC / "css/tokens.css").read_text(encoding="utf-8"))
        self.assertEqual(int(found.group(1)), round(float(come_in.group(1)) * 1000), "static/js/shell.js: the menu fades in and out over Come in's --fade")

    def test_a_step_is_a_function_of_the_frame_not_of_a_clock(self):
        # one step for each frame time, whoever asks first (the cloth is handed the step; which of its callback and the driver's own comes first in a frame does not matter)
        self.assertIn("time !== last", self.driver, "static/js/shell.js: a step is made once for each frame's time")
        for part in (self.driver, self.menu):
            self.assertNotIn("performance.now", part, "static/js/shell.js: the driver reads the frame's time it is given, not a clock")
            self.assertNotIn("Date.now", part)
            self.assertNotIn("setTimeout", part, "static/js/shell.js: nothing in the driver or the menu is timed by a timer")

    def test_the_curve_is_one_easing_rising_or_falling_and_a_turn_starts_from_the_value_it_has(self):
        # What is left to go is (1 - p ** a) ** b, p the share of the fade that has run, rising (v = 1 - it) or falling (v = it), as CSS applies one
        # easing to a rise and a fall; p is recovered from v each step, so a turn-around neither snaps nor jumps
        self.assertIn("const p = (1 - Math.abs(to - v) ** (1 / 3.68)) ** (1 / 1.48) + ", self.driver,
                      "static/js/shell.js: the progress along the curve now is recovered from the value, so a reversal starts from where the sheet is")
        self.assertIn("v = Math.abs(to - (1 - Math.min(1, p) ** 1.48) ** 3.68);", self.driver)

    def test_the_curve_is_close_to_come_ins_ease(self):
        # How close the driver's 1 - (1 - p ** a) ** b is to CSS's ease, cubic-bezier(.25, .1, .25, 1), which Come in fades by (base.css), over the whole
        # fade, in opacity: the same both ways, since a fall is 1 minus the rise. The exponents are read from shell.js.
        def bezier(x1, y1, x2, y2):
            def curve(x):
                low, high = 0.0, 1.0
                for _ in range(50):
                    mid = (low + high) / 2
                    low, high = (mid, high) if 3 * (1 - mid) ** 2 * mid * x1 + 3 * (1 - mid) * mid ** 2 * x2 + mid ** 3 < x else (low, mid)
                t = (low + high) / 2
                return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t ** 2 * y2 + t ** 3
            return curve
        found = re.search(r"Math\.min\(1, p\) \*\* ([\d.]+)\) \*\* ([\d.]+)\);", self.driver)
        self.assertTrue(found, "static/js/shell.js: the curve's exponents (v = Math.abs(to - (1 - Math.min(1, p) ** a) ** b))")
        a, b = float(found.group(1)), float(found.group(2))
        self.assertIn(f"** (1 / {b:g})) ** (1 / {a:g})", self.driver, "static/js/shell.js: the progress is recovered by the same curve's inverse")
        ease = bezier(0.25, 0.1, 0.25, 1)
        grid = [index / 1000 for index in range(1001)]
        eased = [ease(share) for share in grid]
        off = lambda a, b: max(abs(1 - (1 - share ** a) ** b - value) for share, value in zip(grid, eased))
        self.assertLess(off(a, b), 0.023, f"static/js/shell.js: 1 - (1 - p ** {a}) ** {b} lies {off(a, b):.4f} of opacity from ease")
        # and they are the best of their kind: a neighbouring pair is no closer (the figure in spec 4.4 and AGENTS.md is .0227)
        for neighbour in ((a - 0.02, b), (a + 0.02, b), (a, b - 0.1), (a, b + 0.1)):
            self.assertGreater(off(*neighbour), off(a, b), f"static/js/shell.js: the curve with {neighbour} is closer to ease")

    def test_the_sheet_is_displayed_until_the_close_has_run_and_the_old_two_steps_are_gone(self):
        self.assertIn('list.classList.toggle("is-open", to || v);', self.menu, "static/js/shell.js: is-open (display) stays until the value has reached 0")
        for old in ("is-shown", "showSheet"):
            self.assertNotIn(old, self.script, f"static/js/shell.js: no {old}; the fade is the driver's, not a second class two frames after the first")

    def test_motion_off_and_a_change_that_cross_fades_jump_to_the_end_and_a_wide_screen_fades(self):
        # From 64rem up the column fades by the same driver as the sheet (the owner's ask, 2026-10-31: it used to open and close at once there)
        self.assertIn("sheet.go(+open, atOnce || instant() || !isShellMenu);", self.menu,
                      "static/js/shell.js: at once with Motion off or reduced motion, and for a menu shown in the page (it has no driver); the column fades like the sheet")
        self.assertRegex(self.menu, r'"veilfile:leaving", \(\) => \{\s*if \(isShellMenu && PHONE\.matches\) setOpen\(false, false, true\);',
                         "static/js/shell.js: a change that cross-fades takes the sheet away at once; one chosen from the sheet closes it by its driver")

    def test_the_cloth_is_handed_the_step_so_its_paper_is_the_value_of_its_own_frame(self):
        self.assertIn("let sheetStep;", self.script, "static/js/shell.js: veil.js has its own default (a share of 0) for a page with no shell menu")
        self.assertIn("sheetStep = sheet.step;", self.menu)
        self.assertRegex(self.script, r"start\(\{ canvas, gl, off: veilOff, ended: \(\) => \{ veil = null; \}, sheet: sheetStep \}\);",
                         "static/js/shell.js: veil.js is handed the driver's step: the step is made once per frame time, whoever asks first, so the order of the two callbacks does not matter, where reading --sheet would show the paper the last frame's value when the cloth's came first")


class ChangeFromTheSheetScriptTests(SimpleTestCase):
    """A change chosen from the open phone sheet (spec 4.4, 4.5) fades and snaps nothing: the sheet starts its normal close at the tap, the old content
    fades out with it, the page is fetched meanwhile, and <main> is swapped while it is invisible, once the close has ended and the scroll lock with it;
    then the new content fades in. No view transition: no picture of a tall page, and no transition for a resize to cut short. Both fades are the sheet's
    driver (PhoneSheetDriverTests), one value advanced per drawn frame by at most a quarter, so a late frame cannot skip them. The browser checks
    (sheet_change_problems, in Chromium and WebKit) read what the browser does with it."""

    longMessage = False   # a failure prints the message, which names the file and the fix, not the whole script

    def setUp(self):
        script = (STATIC / "js/shell.js").read_text(encoding="utf-8")
        self.script = script
        self.change = script[script.index("async function changeTo"):script.index("// Where the page is scrolled")]
        self.clicks = script[script.index("function setUpChapterChanges"):]

    def test_a_link_in_the_open_phone_sheet_is_told_apart_at_the_click(self):
        self.assertIn('const sheeted = PHONE.matches && Boolean(link.closest("#shell-menu.is-open"));', self.clicks,
                      "static/js/shell.js: the click knows whether the link is in the open phone sheet")
        self.assertIn("changeTo(url.href, push, 0, sheeted);", self.clicks)

    def test_such_a_change_starts_no_view_transition(self):
        self.assertIn("const crossfade = motion && !sheeted && Boolean(document.startViewTransition);", self.change,
                      "static/js/shell.js: a change chosen from the sheet is not a cross-fade")
        sheeted = self.change[self.change.index("if (sheeted) {"):self.change.index("const controller")]
        self.assertNotIn("startViewTransition", sheeted)
        self.assertNotIn("veilfile:leaving", sheeted, "static/js/shell.js: the sheet's close is its driver's, not the at-once close a cross-fade asks for")

    def test_the_sheet_closes_by_its_driver_and_the_old_content_fades_out_with_it(self):
        self.assertIn("faded = Promise.all([closeSheet(), fade.go(0)]);", self.change,
                      "static/js/shell.js: the sheet closes and <main> fades out at the tap, and the swap waits for both")
        self.assertRegex(self.script, r"closeSheet = \(\) => setOpen\(false, false\);")
        self.assertIn("let closeSheet;", self.script)

    def test_main_is_swapped_when_the_close_has_ended_and_the_page_has_come(self):
        self.assertRegex(self.change, r"await faded;[^\n]*\n\s*if \(change !== changes\) return;\n",
                         "static/js/shell.js: the swap waits for the fade out (the close: the scroll lock is released with it) and the page")
        self.assertLess(self.change.index("await faded;"), self.change.index("const swap = "), "static/js/shell.js: nothing is swapped in before that")
        self.assertIn("} else await swap();", self.change, "static/js/shell.js: a change from the sheet swaps in this task, with no view transition")

    def test_the_swap_waits_for_the_sheet_to_be_shut_if_menu_took_it_back(self):
        # MENU pressed during the close turns the sheet's driver round, which resolves the close's promise at once: `faded` can end with the sheet open again, the
        # scroll lock on, and the swap would run under it. So the swap waits until the sheet is shut, as the visitor opened it; a newer change ends the wait.
        self.assertRegex(self.change, r"await faded;[^\n]*\n\s*if \(change !== changes\) return;\n\s*if \(sheeted\) \{[^\n]*\n\s*await whenShut\(\);\n\s*if \(change !== changes\) return;\n\s*\}",
                         "static/js/shell.js: after the fade out, a change from the sheet waits for the sheet to be shut, and a newer change ends the wait")
        self.assertLess(self.change.index("await whenShut();"), self.change.index("const swap = "), "static/js/shell.js: nothing is swapped in before the sheet is shut")
        menu = self.script[self.script.index("function setUpMenu"):self.script.index("function setUpAppearance")]
        self.assertIn("if (!(to || v)) shut.splice(0).forEach((resolve) => resolve());", menu, "static/js/shell.js: the sheet's driver says so when it is shut: no longer open, and not on the way to open")
        self.assertIn('whenShut = () => (list.classList.contains("is-open") ? new Promise((resolve) => shut.push(resolve)) : Promise.resolve());', menu,
                      "static/js/shell.js: the shell's menu answers whenShut (a promise that is resolved at once when the sheet is shut already)")

    def test_the_new_content_fades_in_by_the_same_driver(self):
        self.assertRegex(self.change, r"main\.classList\.remove\(\"is-leaving\"\);\n\s*holdMain\(false\);\n\s*fade\.go\(1\);\n\s*// The menu follows the page first",
                         "static/js/shell.js: once swapped, <main> is within reach again and fades in")

    def test_main_is_given_back_whatever_ends_the_change(self):
        # a failed fetch, a failed swap and a newer change that is not from the sheet (its own fade or cross-fade, which a faded <main> would
        # spoil) each put <main> back
        self.assertEqual(len(re.findall(r"fade\.go\(1\)", self.change)), 3, "static/js/shell.js: fade.go(1) after a swap, a failed fetch and a failed swap")
        self.assertIn("fade.go(1, true);", self.change, "static/js/shell.js: a change not from the sheet takes the page back to full strength")
        self.assertIn("if (crossfading) crossfading.skipTransition();", self.change, "static/js/shell.js: a cross-fade still running is skipped by a change from the sheet")

    def test_the_old_page_is_out_of_reach_from_the_tap_until_the_swap(self):
        # Invisible at opacity 0 for as long as the fetch and the font wait take, <main> must not be tappable or in the Tab order: a second tap on a link
        # nobody can see starts a change that was not chosen. The menu's updateInert reads `away`, so shutting the sheet again does not give it back.
        menu = self.script[self.script.index("function setUpMenu"):self.script.index("function setUpAppearance")]
        self.assertIn('covered.forEach((part) => { part.inert = sheet || (away && part.localName === "main"); });', menu,
                      "static/js/shell.js: updateInert keeps <main> inert while a change from the sheet holds the old page, whatever the sheet does")
        self.assertIn("reach = updateInert;", menu, "static/js/shell.js: holdMain asks the menu's updateInert")
        self.assertRegex(self.script, r"function holdMain\(on\) \{\n\s*away = on;\n\s*reach\(\);\n\}")
        sheeted = self.change[self.change.index("if (sheeted) {"):self.change.index("} else {")]
        self.assertLess(sheeted.index("holdMain(true);"), sheeted.index("closeSheet()"),
                        "static/js/shell.js: <main> is out of reach from the tap, before the sheet's close asks updateInert")
        # given back by the swap, a failed fetch, a failed swap and a change not from the sheet (whose old page is then at full strength again)
        self.assertEqual(len(re.findall(r"holdMain\(false\)", self.change)), 4,
                         "static/js/shell.js: holdMain(false) in a change not from the sheet, a failed fetch, the swap and a failed swap")
        self.assertLess(self.change.index("holdMain(false);\n      fade.go(1);\n      // The menu follows"), self.change.index("heading.focus("),
                        "static/js/shell.js: an inert heading cannot take focus")

    def test_both_fades_are_the_drivers_and_each_lasts_the_cross_fade(self):
        found = re.search(r"const fade = driver\(\(v, to, stepped\) => \{(.*?)\}, (\d+), 1\);", self.script, re.S)
        self.assertTrue(found, "static/js/shell.js: <main>'s fade is the sheet's driver (const fade = driver(write, ms, 1))")
        write, ms = found.groups()
        # out with the sheet, by the same length and curve: the two drivers see the same frames, so the two values are equal in every frame
        sheet = re.search(r"\}, (\d+)\);\n  if \(isShellMenu\)", self.script)
        self.assertEqual(ms, sheet.group(1), "static/js/shell.js: the old content fades out with the sheet's close, by the same driver's length")
        # in over as long: each half is the --change-fade token's length (tokens.css), as Come in fades its line out and then the install line in
        token = re.search(r"--change-fade:\s*([\d.]+)s", (STATIC / "css/tokens.css").read_text(encoding="utf-8"))
        self.assertEqual(int(ms), round(float(token.group(1)) * 1000), "static/js/shell.js: the fade out and the fade in each last the cross-fade's length")
        # nothing timed by CSS while the driver has <main>: a transition on its opacity would lag every step; given back at rest
        self.assertIn('main.style.transition = driven || stepped ? "none" : "";', write)
        self.assertIn('main.style.opacity = driven ? v : "";', write)
        self.assertIn("const driven = v < 1 || !to;", write)
        # and at the end of its own fade in, the opacity comes to rest before the transition is given back: given back together, the transition
        # would fade the last step (the last value the driver wrote to 1) over --change-fade after the driver has finished. Only a frame's step:
        # given back at once (a newer change, which fades <main> out with is-leaving in the same task) it fades from where <main> was.
        self.assertRegex(write, r'if \(stepped && !driven\) \{\s*getComputedStyle\(main\)\.opacity;[^\n]*\n\s*main\.style\.transition = "";\s*\}',
                         "static/js/shell.js: at the fade's own end the opacity takes effect (a style read) before the transition is given back")
        self.assertIn("write(v, to, true);", self.script, "static/js/shell.js: the driver tells write when a frame's step made the value")


class OrdinaryLoadFadeScriptTests(SimpleTestCase):
    """An ordinary load fades too (spec 4.5): a link to a page outside this page's layout family, which shell.js does not change in place, fades <main>
    out over --change-fade (is-leaving), with the menu closing alongside if the link is in it. Then the page goes, and the next one fades in. Hidden from
    its first paint by the boot script, it fades in from shell.js. Which links change in place is not touched, and neither is a form's submit. Back and Forward,
    a direct load and a reload never fade, and Motion off is instant. A page whose load brings nothing in its place is given back, whichever link went, and
    one that is only slow still fades in. The browser check (ordinary_fade_problems) reads what the browser does with it."""

    longMessage = False

    def setUp(self):
        script = (STATIC / "js/shell.js").read_text(encoding="utf-8")
        self.script = script
        self.clicks = script[script.index("function setUpChapterChanges"):script.index("window.addEventListener(\"popstate\"")]
        self.leave = script[script.index("function leave("):script.index("function setUpChapterChanges")]
        self.boot = render_shell()
        self.boot = self.boot[self.boot.index('<script id="veilfile-shell-boot">'):]
        self.boot = self.boot[:self.boot.index("</script>")]

    def function(self, name):
        start = self.script.index(f"function {name}(")
        return self.script[start:self.script.index("\n}\n", start)]

    def test_every_page_outside_the_layout_is_an_ordinary_load_after_the_same_guards(self):
        # The guards of a change in place come first and stay as they were: a left click with no modifier, no target, download or Come in, the
        # same origin and no #fragment. Then a page of this layout changes in place, exactly as before, and every other page goes to leave().
        call = 'if (!document.body.dataset.layout || !(document.body.dataset.layoutPaths || "").split(" ").includes(url.pathname)) return leave(event, url, link);'
        self.assertIn(call, self.clicks, "static/js/shell.js: a page outside this page's layout (or any page from a page with no layout) is an ordinary load, which leave() fades")
        for guard in ('if (!link || event.defaultPrevented || event.button !== 0) return;', "if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;",
                      'if ((link.target && link.target !== "_self") || link.hasAttribute("download") || link.hasAttribute("data-come-in")) return;',
                      "if (url.origin !== window.location.origin) return;", "if (url.hash) return;"):
            self.assertIn(guard, self.clicks)
            self.assertLess(self.clicks.index(guard), self.clicks.index(call), f"static/js/shell.js: `{guard}` leaves the link to the browser before leave() is asked")
        in_place = self.clicks[self.clicks.index(call):]
        self.assertRegex(in_place, r"\n\s*event\.preventDefault\(\);\n", "static/js/shell.js: a page of this layout still changes in place")
        self.assertIn("changeTo(url.href, push, 0, sheeted);", in_place)
        self.assertNotIn("leave(", self.script[self.script.index("async function changeTo"):self.script.index("// Where the page is scrolled")],
                         "static/js/shell.js: a change in place does not fade like an ordinary load as well")

    def test_motion_off_and_a_plain_file_are_left_to_the_browser(self):
        # Motion off and reduced motion (instant()) go at once, as a link does, and set no flag; so does a plain file such as llms.txt, which is no page
        # and runs no shell.js to fade in.
        self.assertIn(r"const page = !/\.\w+$/.test(url.pathname);", self.leave, "static/js/shell.js: an address whose last part has an extension (llms.txt) is a plain file")
        self.assertIn("if (!page || instant()) return;", self.leave)
        self.assertLess(self.leave.index("if (!page || instant()) return;"), self.leave.index("event.preventDefault();"),
                        "static/js/shell.js: with Motion off, and for a plain file, the browser follows the link itself")

    def test_main_fades_out_by_is_leaving_and_the_menu_closes_with_it_and_then_the_page_goes(self):
        fade = self.leave[self.leave.index("event.preventDefault();"):]
        self.assertIn('main.classList.add("is-leaving");', fade, "static/js/shell.js: <main> fades out by is-leaving and its transition over --change-fade (base.css)")
        self.assertIn('await fadedOut(main, link.closest("#shell-menu") && closeSheet());', fade,
                      "static/js/shell.js: a link in the menu closes it by its driver while <main> fades, and the page goes once both are done")
        self.assertRegex(fade, r"if \(change !== changes\) return;\n  \}\n  const went = leaving = \+\+changes;[^\n]*\n  fadeNext\(page\);\n  if \(fades\) location\.assign\(url\.href\);",
                         "static/js/shell.js: the page goes, flagged to fade in, unless a newer change has overtaken it")
        self.assertIn("const change = leaving = ++changes;", fade, "static/js/shell.js: an ordinary load is a change, so a newer one (a change in place, Back) overtakes it")

    def test_the_fade_out_waits_as_a_change_in_place_does(self):
        wait = self.script[self.script.index("const fadedOut"):self.script.index("async function changeTo")]
        self.assertIn("main.getAnimations().map((animation) => animation.finished.catch(() => {}))", wait)
        self.assertIn("new Promise((resolve) => setTimeout(resolve, 2 * fadeMs()))", wait, "static/js/shell.js: a page whose animations do not run does not wait for ever")
        self.assertIn("faded = fadedOut(main);", self.script[self.script.index("async function changeTo"):], "static/js/shell.js: a change in place waits for its fade out the same way")

    def test_a_newer_link_during_the_fade_goes_at_once(self):
        # The page is part faded out already: the newest link wins at once, by the browser, and the load overtaken never goes.
        self.assertIn("const fades = leaving !== changes;", self.leave, "static/js/shell.js: leave() knows an ordinary load is fading out (leaving === changes)")
        held = self.leave[self.leave.index("  if (fades) {\n"):]
        held = held[:held.index("\n  }\n")]
        self.assertIn("event.preventDefault();", held, "static/js/shell.js: only a link that fades is held; a newer one is the browser's at once")
        self.assertLess(self.leave.index("const fades = leaving !== changes;"), self.leave.index("event.preventDefault();"))
        tail = self.leave[self.leave.index(held) + len(held):]
        self.assertRegex(tail, r"const went = leaving = \+\+changes;[^\n]*\n\s*fadeNext\(page\);",
                         "static/js/shell.js: the newer link overtakes it and goes at once, flagged unless it is a plain file, and so does any link after it")
        self.assertIn("let leaving;", self.script, "static/js/shell.js: no ordinary load is fading out when the page arrives (undefined is no change)")

    def test_the_flag_is_set_only_as_the_page_goes_and_its_key_is_the_boot_scripts(self):
        sets = [value for value in re.findall(r"fadeNext\((\w+)\);", self.script) if value != "false"]
        self.assertEqual(sets, ["page"], "static/js/shell.js: the flag is set in one place, the tail of leave(), as the page goes")
        self.assertLess(self.leave.index("await fadedOut("), self.leave.index("fadeNext(page);"),
                        "static/js/shell.js: the flag is set as the page goes, never at the click (a change in place may still overtake the load)")
        key = re.search(r'const FADED_KEY = "([^"]+)";', self.script)
        self.assertTrue(key, "static/js/shell.js: FADED_KEY names the flag in sessionStorage")
        self.assertIn(f'sessionStorage.getItem("{key.group(1)}")', self.boot, "templates/shell.html: the boot script reads the flag shell.js sets")
        flag = self.function("fadeNext")
        self.assertIn("sessionStorage.setItem(FADED_KEY", flag)
        self.assertIn("sessionStorage.removeItem(FADED_KEY)", flag)
        self.assertIn("catch (error)", flag, "static/js/shell.js: blocked storage fades no page in, and breaks nothing")

    def test_the_boot_script_hides_main_from_the_first_paint_only_when_the_page_will_fade_in(self):
        # shell.js is a module, run after the page is parsed and perhaps after its first paint: <main> must already be hidden then, or it would show and blink out.
        self.assertIn('try { if (motion !== "off" && Date.now() - performance.now() - sessionStorage.getItem("veilfile-faded-nav") < 1000) root.setAttribute("data-arriving", ""); }'
                      ' catch (error) {}', self.boot)
        self.assertLess(self.boot.index('sessionStorage.getItem("veilfile-veil-off")'), self.boot.index("data-arriving"), "templates/shell.html: data-arriving is decided once Motion is")
        base = re.sub(r"/\*.*?\*/", "", (STATIC / "css/base.css").read_text(encoding="utf-8"), flags=re.S)
        self.assertRegex(base, r"\.shell-main\.is-leaving,\n\.js\[data-arriving\] \.shell-main \{\n  opacity: 0;\n\}",
                         "static/css/base.css: data-arriving hides <main> as is-leaving does, and only with .js, which shell.js failing to load or throwing takes away")

    def test_the_next_page_fades_in_from_hidden_and_the_flag_is_cleared_with_motion_off_too(self):
        arrive = self.function("arrive")
        self.assertRegex(arrive, r'main\.classList\.add\("is-leaving"\);\n\s*root\.removeAttribute\("data-arriving"\);\n\s*getComputedStyle\(main\)\.opacity;[^\n]*\n'
                                 r'\s*requestAnimationFrame\(\(\) => main\.classList\.remove\("is-leaving"\)\);',
                         "static/js/shell.js: arrive() starts <main> at is-leaving, already hidden, and takes it away on the next frame, so its transition runs over --change-fade")
        self.assertIn('if (root.hasAttribute("data-arriving")) {', arrive, "static/js/shell.js: only a page the boot script hid fades in: never with Motion off, a direct load, a reload or Back")
        self.assertRegex(arrive, r"\n  \}\n  fadeNext\(false\);$", "static/js/shell.js: the flag is cleared whether or not the page fades in, so a later load does not fade")
        setup = self.script[self.script.rindex("try {"):]
        self.assertLess(setup.index("arrive();"), setup.index("setUpChapterChanges();"))

    def test_a_page_kept_by_back_or_with_no_page_in_its_place_is_shown_at_once(self):
        self.assertRegex(self.function("setUpChapterChanges"), r'window\.addEventListener\("pageshow", \(event\) => \{\s*if \(event\.persisted\) \{\s*stay\(\);\s*fadeNext\(false\);',
                         "static/js/shell.js: a page the browser kept as it was left (Back, Forward) is shown at once, not left faded out, and clears the flag")
        self.assertIn("setTimeout(() => went === changes && stay(), FETCH_TIMEOUT_MS);", self.leave,
                      "static/js/shell.js: a load that brings no page in this one's place (a download, a 204) gives it back")
        stay = self.function("stay")
        self.assertRegex(stay, r'main\.style\.transition = "none";\n\s*main\.classList\.remove\("is-leaving"\);\n\s*getComputedStyle\(main\)\.opacity;[^\n]*\n\s*main\.style\.transition = "";')
        self.assertIn("++changes;", stay, "static/js/shell.js: whatever was on its way when the page was kept goes no further")

    def test_a_slow_page_still_fades_in_after_the_page_is_given_back(self):
        # PR #18's follow-up (a): FETCH_TIMEOUT_MS after the page went it is given back, since no page may come (a 204, a download), but a page that is only
        # slow comes later and must still fade in. So the flag is the time the page went and the fallback leaves it; the boot script fades in only a load
        # that began as it was set. The slow page's did; a reload, a direct load or Back after a 204 began after the page was given back, so never fades.
        self.assertIn("sessionStorage.setItem(FADED_KEY, Date.now());", self.function("fadeNext"), "static/js/shell.js: the flag is the time the page went")
        self.assertIn("&& stay(), FETCH_TIMEOUT_MS);", self.leave)
        self.assertNotIn("fadeNext(", self.function("stay"), "static/js/shell.js: a page given back because no page came leaves the flag for one that is only slow")
        began = re.search(r'Date\.now\(\) - performance\.now\(\) - sessionStorage\.getItem\("veilfile-faded-nav"\) < (\d+)\)', self.boot)
        self.assertTrue(began, "templates/shell.html: the page fades in only when its own load (which began at Date.now() - performance.now()) began as the flag was set")
        timeout = int(re.search(r"const FETCH_TIMEOUT_MS = (\d+);", self.script).group(1))
        self.assertLess(int(began.group(1)), timeout, "templates/shell.html: a load that began once the page was given back (a reload, a direct load) does not fade in")

    def test_every_way_the_page_goes_is_flagged_and_given_back_if_no_page_comes(self):
        # PR #18's follow-up (b): a newer link chosen while <main> fades out goes at once by the browser, and it too may bring no page (a 204, a download);
        # with no fallback for it the page stayed faded out until a reload. Both ways the page goes, location.assign after the fade and the browser following
        # a newer link, end in the one tail of leave(), which sets the flag and gives the page back if no page has come by FETCH_TIMEOUT_MS.
        tail = re.search(r"\n  \}\n  const went = leaving = \+\+changes;[^\n]*\n  fadeNext\(page\);\n  if \(fades\) location\.assign\(url\.href\);\n"
                         r"  setTimeout\(\(\) => went === changes && stay\(\), FETCH_TIMEOUT_MS\);\n\}\n", self.leave)
        self.assertTrue(tail, "static/js/shell.js: leave() ends in one tail that flags the page, sends it if leave() held the click, and gives it back if none comes")
        block = self.leave.index("  if (fades) {\n"), tail.start()
        returns = [match.start() for match in re.finditer(r"\breturn\b", self.leave)]
        self.assertTrue(returns and all(block[0] < at < block[1] for at in returns),
                        "static/js/shell.js: only a link that fades returns early (Motion off, a plain file, overtaken); a newer link always reaches the tail")
        self.assertEqual(self.leave.count("location.assign("), 1, "static/js/shell.js: the page is sent from the tail alone")

    def test_no_cross_document_view_transition_and_no_submit_is_held(self):
        # Out of scope here: a form's submit goes as it always has, and cross-document view transitions (@view-transition) are not used; the same-document
        # rules (:root's name none, the chapter's) stay as they are.
        self.assertNotIn('"submit"', self.script, "static/js/shell.js: a form's submit is never delayed")
        for path in sorted((STATIC / "css").rglob("*.css")):
            self.assertNotIn("@view-transition", path.read_text(encoding="utf-8"), f"{path.relative_to(STATIC)}: ordinary loads fade by shell.js, not by a cross-document view transition")
        self.assertRegex((STATIC / "css/base.css").read_text(encoding="utf-8"), r":root \{\s*view-transition-name: none;\s*\}")


class FacesScriptTests(SimpleTestCase):
    """No change waits on a font (spec 4.5): shell.js loads the faces any page of the layout may show (the body's data-faces, tests_faces) when the menu
    opens and when a change begins, in parallel with the fetch, and waits for them before it swaps or starts a view transition, while the page still draws;
    the swap's own wait for the fonts (inside a transition's update callback, where it pauses the page's drawing) then finds them loaded. Both waits are bounded
    by one timer, FONT_WAIT_MS from the first, made only when a face is still loading. The browser checks (font_change_problems, in Chromium and WebKit) read
    what the browser does with it."""

    longMessage = False

    def setUp(self):
        script = (STATIC / "js/shell.js").read_text(encoding="utf-8")
        self.script = script
        self.change = script[script.index("async function changeTo"):script.index("// Where the page is scrolled")]
        self.menu = script[script.index("function setUpMenu"):script.index("function setUpAppearance")]

    def test_the_faces_are_the_bodys_and_each_is_loaded_by_the_browser(self):
        self.assertIn('(document.body.dataset.faces || "").split("|").filter(Boolean).map((face) => document.fonts.load(face).catch(() => {}))', self.script,
                      "static/js/shell.js: loadFaces loads each face named in <body data-faces> with document.fonts.load, and a face that fails does not fail the change")

    def test_they_are_asked_for_when_the_menu_opens_and_when_a_change_begins(self):
        self.assertIn("if (open) loadFaces();", self.menu, "static/js/shell.js: opening the menu, the way to every chapter, starts the faces loading")
        self.assertIn("const faces = loadFaces().then(() => { loaded = true; });", self.change, "static/js/shell.js: a change asks for them at once, in parallel with the fetch")

    def test_the_change_waits_for_them_before_it_swaps_or_starts_a_view_transition(self):
        wait = self.change.index("await bounded(faces);")
        self.assertLess(self.change.index("await faded;"), wait)
        self.assertLess(wait, self.change.index("const swap = "), "static/js/shell.js: the wait is before the swap, outside any view transition's update callback")
        self.assertLess(wait, self.change.index("document.startViewTransition(swap)"))
        self.assertIn("if (motion && !loaded) {", self.change, "static/js/shell.js: no wait, and no timer, when they are loaded; none with Motion off")

    def test_the_swaps_own_wait_is_made_only_while_a_face_is_loading(self):
        # `document.fonts.ready` is a resolved promise when nothing is loading, but bounded() would still make its timer: the wait is lazy, so a change that finds every face
        # loaded makes no timer at all (font_timer_problems counts them in the browser).
        self.assertRegex(self.change, r'if \(motion && document\.fonts\.status === "loading"\) \{\s*await bounded\(document\.fonts\.ready\);',
                         "static/js/shell.js: the swap waits for document.fonts.ready only when document.fonts.status is loading, so the timer is made only when a face is still loading")
        self.assertEqual(self.change.count("bounded(document.fonts.ready)"), 1)

    def test_the_swaps_own_wait_is_bounded_by_the_same_timer_and_finds_them_loaded(self):
        self.assertIn("await bounded(document.fonts.ready);", self.change)
        self.assertIn("late = late || new Promise((resolve) => setTimeout(resolve, FONT_WAIT_MS))", self.change,
                      "static/js/shell.js: one timer bounds both waits, made when the first is needed: a change waits FONT_WAIT_MS at most for a face still loading")
        self.assertEqual(self.change.count("FONT_WAIT_MS"), 1, "static/js/shell.js: the face bound is made in one place, so a change waits for a face FONT_WAIT_MS at most, not twice")


class ClothDrawnSheetCssTests(SimpleTestCase):
    """While the cloth runs it draws the phone menu's sheet (spec 4.4, veil.js u_sheet): the CSS surface is not drawn, <main> is masked out of
    the right half by what is left of the sheet, and the display control bottom right fades with it. Wherever the cloth is not drawing (Motion
    off, a still page, no WebGL, a lost context) none of that applies and the CSS surface is the sheet. The browser checks
    (cloth_menu_sheet_problems, cloth_sheet_ink_problems, cloth_sheet_motion_problems) read what the browser does with these rules."""

    def setUp(self):
        base = (STATIC / "css/base.css").read_text(encoding="utf-8")
        self.base = re.sub(r"/\*.*?\*/", "", base, flags=re.S)
        self.menu = (STATIC / "css/components/menu.css").read_text(encoding="utf-8")
        self.drawn = self.base[self.base.index("@property --sheet"):]
        self.cloth = r':root\[data-veil="on"\]:not\(\[data-motion="off"\]\)'

    def rule(self, selector):
        found = re.search(rf"{selector}\s*\{{([^}}]*)\}}", self.drawn)
        self.assertTrue(found, f"static/css/base.css: no rule for {selector}")
        return found.group(1)

    def test_the_css_surface_is_not_drawn_while_the_cloth_draws_the_sheet(self):
        self.assertIn("display: none;", self.rule(self.cloth + r" \.menu__list::before"))
        self.assertRegex(self.drawn, r"@media \(max-width: 63\.99rem\) \{")

    def test_the_cloth_is_known_to_draw_by_veil_js_marker_and_motion_not_off(self):
        # data-veil="on" is set by veil.js after its first frame and deleted when it stops; data-motion is "off" the moment it is told to stop,
        # so the CSS surface is back at once, however long the cloth takes to fade.
        veil = (STATIC / "js/veil.js").read_text(encoding="utf-8")
        self.assertIn('root.dataset.veil = "on"', veil)
        self.assertIn("delete root.dataset.veil", veil)
        rules = re.findall(r"([^{}]+)\{([^}]*)\}", re.sub(r"@media[^{]*\{", "", self.drawn.split("}", 1)[1]))   # past the @property rule, without the @media line: the selectors of the block
        keyed = [selector for selector, body in rules if re.search(r"mask-image|display: none|opacity: calc", body)]
        self.assertGreaterEqual(len(keyed), 3)
        for selector in keyed:
            self.assertIn('[data-veil="on"]:not([data-motion="off"])', selector, f"static/css/base.css: {selector.strip()} applies only while the cloth draws")

    def test_the_share_is_one_value_that_css_only_reads(self):
        # shell.js writes --sheet on each element that reads it, once per drawn frame (PhoneSheetDriverTests); <main>'s mask, the display control's
        # fade and the sheet's links read it. Nothing in the stylesheets sets it, and nothing moves it by a transition: it is already advanced per frame.
        # It is not inherited: written on the root and inherited, it would make the browser recalculate every element's style every frame of the fade.
        self.assertRegex(self.base, r'@property --sheet \{\s*syntax: "<number>";\s*inherits: false;\s*initial-value: 0;\s*\}')
        self.assertEqual(len(re.findall(r"--sheet:", self.base)), 0, "static/css/base.css: --sheet is registered (@property) and set nowhere in CSS (no `--sheet: inherit` chain: WebKit never updates it)")
        self.assertNotIn("--sheet 0", self.base)
        self.assertNotRegex(self.base, r"transition[^;]*--sheet", "static/css/base.css: nothing transitions --sheet; shell.js advances it per drawn frame")
        self.assertNotIn("is-shown", self.base)
        self.assertIn("opacity: calc(1 - var(--sheet));", self.rule(self.cloth + r" \.shell-corner--bottom-right"))

    def test_main_has_only_its_own_fade_as_a_transition(self):
        # the `.shell-main` rule at the top of the cloth-drawn block that added --sheet to its transition is gone: main fades with --change-fade only
        self.assertEqual(len(re.findall(r"\.shell-main[^{]*\{[^}]*transition:", self.base)), 1, "static/css/base.css: <main> has one transition rule, its own fade")

    def test_main_is_masked_by_the_mirror_of_the_sheet_and_the_top_fade(self):
        rule = self.rule(self.cloth + r"\.js\.shell-ready \.shell-main")
        top = re.search(r"mask-image: (linear-gradient\(to bottom, [^;]*?\)\)),\s*\n", rule)
        self.assertTrue(top, "static/css/base.css: the cloth-drawn sheet's mask keeps the top fade as its first layer")
        fade = re.search(r"\.js\.shell-ready \.shell-main \{[^}]*?mask-image: (linear-gradient\(to bottom, [^;]*?\)\));", self.base[:self.base.index("@property --sheet")], re.S)
        self.assertEqual(top.group(1), fade.group(1), "static/css/base.css: the top fade is the same gradient in both rules")
        self.assertIn("linear-gradient(to right, var(--mask-solid) 0%, color-mix(in srgb, var(--mask-solid) calc((1 - var(--sheet)) * 100%), transparent) 50%);", rule)
        self.assertIn("mask-composite: intersect;", rule)
        self.assertIn("-webkit-mask-composite: source-in;", rule)
