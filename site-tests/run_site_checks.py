#!/usr/bin/env python3
"""Browser checks for every page on the new shell (spec 10; CI jobs `site`, `site-cloth`, `site-ink`, `site-reading`, `site-phone`, `site-forms`, `site-catalog` and `site-dashboard`).

Each page opens at phone and desktop widths, in Paper and Ink, with Motion on and off. A page
fails on sideways scrolling, console errors, a font family that never loads, or accessibility
violations (axe-core, WCAG 2.1 A and AA). The menu and the display controls are exercised, and
the conditions a visitor can bring (no JavaScript, shell.js failing to load, blocked storage, a
saved value this version does not know, a system asking for reduced motion, the menu at rest on
each page, narrow and short screens) each get a run of their own. At every scroll position, nothing in
the page may show under a corner label or through the open menu (screenshots with and without
<main> are compared). Screenshots of every page and state
go to --out, for reviewing a pull request by eye, with review-by-eye.txt listing what axe
could not decide. If a page cannot be opened at all (the app is down, the session is stale),
the run stops at once and says why.

Usage, with the app running (see AGENTS.md, "Site checks"):
  python site-tests/run_site_checks.py --base-url http://127.0.0.1:8000 \
      --session-file site-tests/session.txt --out site-tests/artifacts [--part pages|cloth|ink|reading|phone|forms|catalog|dashboard|all]

The checks come in parts, which CI runs as jobs (`site`, `site-cloth`, `site-ink`, `site-reading`, `site-phone`, `site-forms`, `site-catalog` and `site-dashboard`, each with its own 20 minutes) and `--part all`, the
default, runs one after the other: `pages` (every page and what a visitor can bring, then the in-place changes), `cloth` (the WebGL cloth's
families), `ink` (the ink washes, the phone menu's sheet included: the cloth's second half) and `reading` (the Journal, the Docs
and the legal documents: long columns, section lists, code blocks, previous and next, and the changes among them). Opening each page with
Motion on and off is the pages part's; the reading part opens each of its pages in Paper and Ink with Motion on only (Motion changes only
how a change fades, which its change checks watch). The `forms` part is sign in, sign up, password reset, email verification, the device flow, OAuth
consent and the error pages, each in the states it has; it needs the fixtures that site-tests/make_form_fixtures.py writes beside the session file.
The `catalog` part is the component catalog (/app/components/, which every component shows in its states) with the pages part's own checks of a page; it is
not in `pages` for time. The `dashboard` part is the signed-in dashboard (/app/ and the pages that hang off it): its five sections with the pages part's checks of a page, then their
changes in place, Sign out, a new key, a revoke, rows that stack and times in the visitor's zone; it needs the fixtures that site-tests/make_dashboard_fixtures.py writes beside the
session file, and signs in as the users it makes, not as the staff login.
"""
import argparse
import gc
import json
import re
import struct
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
AXE_SCRIPT = HERE / "node_modules" / "axe-core" / "axe.min.js"
FONT_FAMILIES = ("Cormorant Garamond", "Newsreader", "IBM Plex Mono")
DISPLAY_FAMILY = FONT_FAMILIES[0]  # the headings and the threshold's line
WIDTHS = (375, 1280)
APPEARANCES = ("light", "dark")  # the stored values for Paper and Ink
MOTIONS = ("on", "off")
OVERFLOW = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
LINES = """(selector) => { const range = document.createRange();
    range.selectNodeContents(document.querySelector(selector));
    return new Set([...range.getClientRects()].map((rect) => Math.round(rect.top))).size; }"""
ROOT_ATTRIBUTE = "(name) => document.documentElement.getAttribute(name)"
# The menu's sheet has come in: it is displayed and its one value, --sheet (shell.js steps it once per drawn frame and writes it on the sheet and
# on each other element that reads it: spec 4.4), has reached 1, so its links are in place at full opacity. It must read 1 at three polls running
# (a poll is a frame): a browser prints a number to six digits, so the last step but one, 0.99999998, already reads 1, and the step that makes it
# exactly 1 is still to come, which would overwrite whatever a check had done to the sheet in the meantime. A check that reads a link's place,
# or presses one, waits for this: before it the links are fading and up to 6px above their place. On a page whose clock the check holds (the
# cloth's virtual clock, FREEZE) nothing moves until frames are let pass: wait_sheet_in_place does that first.
SHEET_IN_PLACE = """() => { const sheet = document.querySelector('#shell-menu');
  const in_place = getComputedStyle(sheet).display !== 'none' && getComputedStyle(sheet).getPropertyValue('--sheet').trim() === '1';
  window.__sheetInPlace = in_place ? (window.__sheetInPlace || 0) + 1 : 0;
  return window.__sheetInPlace >= 3; }"""
# Narrow and short viewports: the reference phone width, a landscape phone, a zoomed laptop.
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]
# What axe could not decide (most often contrast over the grain or a fixed corner): printed as
# "review by eye" lines and written to review-by-eye.txt beside the screenshots.
REVIEW_NOTES = []
SMALL_VIEWPORTS = ((320, 640), (667, 375), (900, 500))
SHELL_SCRIPT = re.compile(r".*/js/shell(\.[0-9a-f]+)?\.js$")
TOKENS_STYLESHEET = re.compile(r".*/css/tokens(\.[0-9a-f]+)?\.css$")
VEIL_SCRIPT = re.compile(r".*/js/veil(\.[0-9a-f]+)?\.js$")
# One software rasteriser everywhere (SwiftShader, bundled with Chromium), so WebGL exists on any
# machine and CI matches a laptop. Without the second switch Chromium logs a deprecation warning.
GL_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
# What the existing checks get instead of veil.js: the page stays as it is without the cloth (the
# flat fills, data-motion as stored), and no GPU work slows the run. The cloth checks load the real one.
VEIL_STUB = "export function start() {}"
CORNER_OVERLAPS = """() => {
  const box = (corner) => document.querySelector(`.shell-corner--${corner}`).getBoundingClientRect();
  const hit = (a, b) => a.width > 0 && b.width > 0 && a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
  return [["top-left", "top-right"], ["bottom-left", "bottom-right"]]
    .filter(([a, b]) => hit(box(a), box(b))).map(([a, b]) => `${a} and ${b}`);
}"""
TEXT_UNDER_MENU = """() => {
  const links = [...document.querySelectorAll("#shell-menu a")].map((link) => link.getBoundingClientRect());
  const walker = document.createTreeWalker(document.querySelector("main"), NodeFilter.SHOW_TEXT);
  const covered = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!node.textContent.trim()) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const rects = [...range.getClientRects()];
    if (rects.some((r) => links.some((l) => l.left < r.right && r.left < l.right && l.top < r.bottom && r.top < l.bottom))) {
      covered.push(node.textContent.trim().slice(0, 40));
    }
  }
  return covered;
}"""
# How far apart, in px, the text rows of two corners on the same edge sit (top: lockup and MENU;
# bottom: the appearance switch and Motion).
CORNER_ROWS = """() => {
  const centre = (el) => { const r = el.getBoundingClientRect(); return r.top + r.height / 2; };
  const pairs = [
    ["top", document.querySelector(".shell-corner--top-left .lockup"), document.querySelector("header [data-menu-toggle]")],
    ["bottom", document.querySelector(".shell-corner--bottom-left button"), document.querySelector(".shell-corner--bottom-right button")],
  ];
  return pairs.filter(([, a, b]) => a && b).map(([edge, a, b]) => [edge, Math.abs(centre(a) - centre(b))]);
}"""
# The font families a page sets visible text in (the first family of each element with text).
USED_FAMILIES = """() => { const used = new Set();
  for (const el of document.querySelectorAll("body *")) {
    if (!el.getClientRects().length || getComputedStyle(el).visibility === "hidden") continue;
    if (![...el.childNodes].some((node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim())) continue;
    used.add(getComputedStyle(el).fontFamily.split(",")[0].replace(/["']/g, "").trim());
  }
  return [...used]; }"""
# Whether each element matching the selector, where it lies inside the viewport, is painted on top
# at its centre. Inert parts are skipped by hit testing, so they are made hit-testable while looking.
COVERED = """(selector) => {
  const inert = [...document.querySelectorAll("[inert]")];
  inert.forEach((part) => { part.inert = false; });
  const covered = [...document.querySelectorAll(selector)].filter((el) => {
    const r = el.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    if (!r.width || y < 0 || y > innerHeight) return false;
    const hit = document.elementFromPoint(x, y);
    return !(hit && el.contains(hit));
  }).map((el) => el.textContent.trim().replace(/\\s+/g, " "));
  inert.forEach((part) => { part.inert = true; });
  return covered;
}"""
# Whether the element matching the selector, where it lies inside the viewport, is hidden by the open menu
# sheet itself: what is painted on top at its centre is #shell-menu, the nav holding it, or something in the
# sheet that is not a link. Where the menu is a column (from 64rem up) a link on top is not the sheet: that is the defect
# menu_reach_problems looks for. Below 64rem the sheet covers the screen and is solid behind its links, so a link on top of
# the element is the sheet over it too. The point looked at is the centre of the part of the element that lies inside the viewport: a
# footer control on a page a line or so taller than a short screen (About at 667x375 on a runner whose text wrapped one line more, so that
# the controls' box began at 366 to 375 and its centre fell at or past the bottom edge) shows only a strip at the bottom, the sheet is
# painted over that strip, and the centre of the whole box, which is off the screen, would read as "not under the sheet".
UNDER_SHEET = """(selector) => {
  const inert = [...document.querySelectorAll("[inert]")];
  inert.forEach((part) => { part.inert = false; });
  const el = document.querySelector(selector), sheet = document.querySelector("#shell-menu");
  const r = el.getBoundingClientRect();
  const top = Math.max(r.top, 0), bottom = Math.min(r.bottom, innerHeight);
  const x = r.left + r.width / 2, y = (top + bottom) / 2;
  const hit = r.width && bottom > top ? document.elementFromPoint(x, y) : null;
  inert.forEach((part) => { part.inert = true; });
  return Boolean(hit && sheet && !el.contains(hit) && (!hit.closest("a") || matchMedia("(max-width: 63.99rem)").matches)
                 && (sheet.contains(hit) || hit === sheet.closest("[data-menu]")));
}"""
# What a visitor can see agree or disagree about the page they are on: the address, the title, the canonical
# address, the menu's current mark, the heading (in <main>, and the page's first anywhere) and whether <main> is showing.
PAGE_STATE = """() => ({
    path: location.pathname,
    title: document.title,
    canonical: [...document.querySelectorAll('link[rel="canonical"]')].map((link) => link.href),
    marks: [...document.querySelectorAll('#shell-menu a[aria-current]')].map((a) => a.textContent.trim()),
    threshold: Boolean(document.querySelector('[data-threshold-line]')),
    heading: ((document.querySelector('main h1') || {}).textContent || '').trim(),
    topHeading: ((document.querySelector('h1') || {}).textContent || '').trim(),
    opacity: getComputedStyle(document.querySelector('main')).opacity,
    leaving: document.querySelector('main').classList.contains('is-leaving'),
  })"""
# What each tab of an install line says about itself, its panel and focus (a tablist, spec 9).
TAB_STATE = """(line) => [...line.querySelectorAll('[role="tab"]')].map((tab) => {
    const panel = document.getElementById(tab.getAttribute('aria-controls'));
    return { name: tab.textContent.trim(), selected: tab.getAttribute('aria-selected'), tabindex: tab.getAttribute('tabindex'),
             hidden: panel ? panel.hasAttribute('hidden') : null, focused: tab === document.activeElement }; })"""
# How far an element's fade has got: its opacity now and the opacity transitions still running on it.
OPACITY_FADE = """(element) => ({ opacity: getComputedStyle(element).opacity,
    fading: element.getAnimations().filter((animation) => animation.transitionProperty === 'opacity').length })"""
# Records an in-place chapter change from inside the page, with performance.now(), so the checks time the
# scripts and not the machine: each view transition (the call, when its update callback ran, when its animation
# began and when it finished, how many frames the cloth had drawn at the last two, and the name <main> carried for it), the click, the moment the
# new page's body had been read (the fetch is done), the waits (timers of 50 ms or more) the scripts start between that and the view transition's update callback (the swap), <main> being faded out
# (is-leaving) and when its first opacity fade began (the frame the browser started it in), how opaque <main> was when the
# content was swapped, and each end of an opacity fade of <main>. Installed before the page's own scripts run; a context
# that has no view transitions deletes startViewTransition in an init script of its own, added first.
CHANGE_PROBE = """(() => {
  const marks = window.__change = { calls: [], clicked: null, fetched: null, leaving: null, fadeStart: null, swapped: null, swappedFrame: null, swappedOpacity: null, ends: [], waits: [], updating: false };
  const now = () => performance.now();
  const setTimeout_ = window.setTimeout;   // the waits the page's scripts start after the page has arrived (a wait of the script's own)
  window.setTimeout = function (callback, delay, ...rest) {
    if (marks.fetched !== null && !marks.updating && delay >= 50) marks.waits.push(delay);
    return setTimeout_.call(this, callback, delay, ...rest);
  };
  const draws = () => (window.__cloth ? window.__cloth.draws : null);
  const start = Document.prototype.startViewTransition;
  if (start) Document.prototype.startViewTransition = function (callback) {
    const nameOfMain = () => getComputedStyle(document.querySelector("main")).viewTransitionName;
    const call = { at: now(), updated: null, ready: null, finished: null, drawsReady: null, drawsFinished: null, style: null, fontsLoading: null, fadeMs: null,
      names: { call: nameOfMain(), updated: null, ready: null },   // <main>'s view-transition-name, when the call was made, the content swapped and the animation began
      sheetOpen: Boolean(document.querySelector("#shell-menu.is-open")) && matchMedia("(max-width: 63.99rem)").matches,
      sheetShown: matchMedia("(max-width: 63.99rem)").matches && Boolean(document.querySelector("#shell-menu")) && getComputedStyle(document.querySelector("#shell-menu")).display !== "none" };   // a sheet fading out is still shown
    marks.calls.push(call);
    const transition = start.call(this, typeof callback === "function" ? () => { call.updated = now(); call.names.updated = nameOfMain(); marks.updating = true; return callback(); } : callback);
    transition.ready.then(() => { call.ready = now(); call.names.ready = nameOfMain(); call.drawsReady = draws();
      const style = (pseudo, property) => getComputedStyle(document.documentElement, pseudo)[property];
      call.style = { duration: style("::view-transition-group(chapter)", "animationDuration"), pointer: style("::view-transition", "pointerEvents"),
                     rootName: getComputedStyle(document.documentElement).viewTransitionName,
                     rootAnimations: document.getAnimations().filter((animation) => animation.effect && /\\(root\\)/.test(animation.effect.pseudoElement || "")).length };   // > 0 where the root was captured
      call.fontsLoading = [...document.fonts].filter((face) => face.status === "loading").length;
      const fade = document.getAnimations().find((animation) => animation.effect && animation.effect.pseudoElement === "::view-transition-old(chapter)" && animation.animationName === "-ua-view-transition-fade-out");
      call.fadeMs = fade ? fade.effect.getComputedTiming().duration : null; }, () => {});
    transition.finished.then(() => { call.finished = now(); call.drawsFinished = draws(); }, () => {});
    return transition;
  };
  document.addEventListener("click", () => { if (marks.clicked === null) marks.clicked = now(); }, true);
  const fetched = window.fetch;
  window.fetch = (...args) => fetched(...args).then((response) => {
    const text = response.text.bind(response);
    response.text = () => text().then((body) => { if (marks.fetched === null) marks.fetched = now(); return body; });
    return response; });
  document.addEventListener("DOMContentLoaded", () => {
    const main = document.querySelector("main");
    new MutationObserver((records) => { for (const record of records) {
      if (record.type === "attributes" && main.classList.contains("is-leaving") && marks.leaving === null) marks.leaving = now();
      if (record.type === "childList" && marks.swapped === null) { marks.swapped = now(); marks.swappedFrame = window.__cloth ? window.__cloth.frames : null; marks.swappedOpacity = getComputedStyle(main).opacity; } } })
      .observe(main, { attributes: true, attributeFilter: ["class"], childList: true });
    main.addEventListener("transitionstart", (event) => { if (event.target === main && event.propertyName === "opacity" && marks.fadeStart === null) marks.fadeStart = now(); });
    main.addEventListener("transitionend", (event) => { if (event.target === main && event.propertyName === "opacity") marks.ends.push(now()); });
  });
})();"""
# The change is over: every view transition has finished, <main> is not faded out and no fade of it is still running.
CHANGE_OVER = """() => { const main = document.querySelector('main');
    return window.__change.calls.every((call) => call.finished !== null) && !main.classList.contains('is-leaving')
      && !document.getAnimations().some((animation) => animation.playState === 'running' && animation.effect.target === main); }"""
# A cross-fade of 2 s instead of --change-fade, for a check that must put the pointer into the middle of one.
SLOW_CHANGE = "::view-transition-group(chapter) { animation-duration: 2s !important; }"
# <main>'s view-transition-name now.
MAIN_NAME = "() => getComputedStyle(document.querySelector('main')).viewTransitionName"
# A page that is slow to run a view transition's update callback (a busy page, a tab in the background): the browser
# calls it at the next rendering opportunity, at least a frame after startViewTransition. Here the callback's own
# promise is held for 600 ms before the content is swapped, and every history.pushState is recorded in window.pushed.
LATE_SWAP = """(() => { const start = Document.prototype.startViewTransition; window.pushed = []; window.lateSwaps = 0;
  const push = history.pushState.bind(history); history.pushState = (state, title, url) => { window.pushed.push(new URL(url, location.href).pathname); return push(state, title, url); };
  Document.prototype.startViewTransition = function (callback) { window.lateSwaps += 1;
    return start.call(this, () => new Promise((resolve) => setTimeout(() => resolve(callback()), 600))); }; })();"""
# A device that is late with the first frame of a fade: after shell.js has handled a click on a chapter link (a listener
# on window runs after the document's, in the same task), the page is busy for LATE_FRAME_MS, so the fade out, which starts at
# the next frame, begins that much late. 350 ms, half the fade, is late enough that a timer of the fade's own length (700 ms) from
# the click swaps while <main> is still about 0.2 opaque (ease, base.css), and early enough that the fade ends (at about 350 + 700 ms
# and a frame) 300 ms or more ahead of the 1,400 ms backstop (twice --change-fade): a slow runner that is 100 ms late with the frame
# still sees the fade end first. (With the .35s token it was 150 ms, against a 700 ms backstop.)
LATE_FRAME_MS = 350
LATE_FRAME = f"""window.addEventListener("click", (event) => {{ if (event.target.closest && event.target.closest('a[href="/plans/"]')) {{
  const end = performance.now() + {LATE_FRAME_MS}; while (performance.now() < end); }} }});"""
# A fade that never reports its end (a tab in the background runs no animations): <main>'s animations are one that never finishes.
FADE_NEVER_ENDS = """(() => { const real = Element.prototype.getAnimations;
  Element.prototype.getAnimations = function (...options) { return this.matches("main") ? [{ finished: new Promise(() => {}) }] : real.apply(this, options); }; })();"""
# A browser that refuses the clipboard: both calls reject, as when the page may not read or write it.
# An init script that takes view transitions away, as in a browser that has none (the fade-only path of shell.js).
NO_VIEW_TRANSITIONS = "delete Document.prototype.startViewTransition;"
# The two ways an in-place change can fade: the checks that watch what a change leaves behind run in both.
VIEW_MODES = (("with view transitions", ()), ("without view transitions", (NO_VIEW_TRANSITIONS,)))
CLIPBOARD_REFUSED = """Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
    writeText: () => Promise.reject(new DOMException("Refused.", "NotAllowedError")),
    readText: () => Promise.reject(new DOMException("Refused.", "NotAllowedError")) } });"""
BLOCK_STORAGE = (
    'Object.defineProperty(window, "localStorage", { configurable: true, '
    'get() { throw new DOMException("Storage is blocked.", "SecurityError"); } });'
)


@dataclass(frozen=True)
class SitePage:
    path: str
    name: str  # used in screenshot file names
    signed_in: bool = False
    chapter: bool = False  # a chapter: its menu marks it, once MENU has opened it (the menu starts closed everywhere)
    threshold: bool = False  # the threshold: Come in gives way to the install line


# Every page that uses the new shell. Add each page here as it moves onto the shell.
PAGES = (
    SitePage("/", "threshold", threshold=True),
    SitePage("/about/", "about", chapter=True),
    SitePage("/how/", "how", chapter=True),
    SitePage("/begin/", "begin", chapter=True),
    SitePage("/promises/", "promises", chapter=True),
    SitePage("/plans/", "plans", chapter=True),
)

# The component catalog is checked by the `catalog` part, in a loop of its own (run_catalog), not by `pages`: with the form components and their
# states it took 530 s of the pages part's 20 minutes on CI (159 s opening it in every width, appearance and Motion, 371 s in its edge cases), a part
# of its own. It is the one page that needs the staff session. The cloth and ink parts still open it (page_named, SHEET_COVER_PAGES).
CATALOG_PAGES = (
    SitePage("/app/components/", "component-catalog", signed_in=True),
)

# The reading pages are checked by the `reading` part, in a loop of their own (run_reading), not by `pages`: a page in both would
# run twice, and the pages part already takes about 12 of CI's 20 minutes. The Journal's newest post is not listed: reading_pages()
# adds it after the Journal, from the Journal's first entry, so a new post needs no edit here. A Docs page or legal document that
# should be checked too goes here.
READING_PAGES = (
    SitePage("/blog/", "journal"),
    SitePage("/docs/", "docs"),
    SitePage("/docs/quickstart/", "docs-quickstart"),
    SitePage("/terms/", "terms"),
)


def seed_storage(theme, motion):
    """An init script that saves the given preferences once per tab (None removes the key)."""
    values = json.dumps({"veilfile-theme": theme, "veilfile-motion": motion})
    return (
        "(() => { try { if (sessionStorage.getItem('site-checks-seeded')) return; "
        f"const values = {values}; "
        "for (const [key, value] of Object.entries(values)) { "
        "if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } "
        "sessionStorage.setItem('site-checks-seeded', '1'); } catch (error) {} })();"
    )


def new_context(browser, base_url, session_key, site_page, *, width, theme, motion, height=900,
                javascript=True, reduced_motion="no-preference", block_storage=False, cloth=False, touch=False,
                device_scale_factor=1, timezone_id=None):
    zone = {"timezone_id": timezone_id} if timezone_id else {}   # the zone the browser reports (the dashboard's times are written in it)
    context = browser.new_context(viewport={"width": width, "height": height}, has_touch=touch,
                                  java_script_enabled=javascript, reduced_motion=reduced_motion,
                                  device_scale_factor=device_scale_factor, **zone)
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base_url)
    if site_page.signed_in:
        context.add_cookies([{"name": "sessionid", "value": session_key, "url": base_url}])
    context.add_init_script(BLOCK_STORAGE if block_storage else seed_storage(theme, motion))
    if not cloth:
        context.route(VEIL_SCRIPT, lambda route: route.fulfill(status=200, content_type="text/javascript", body=VEIL_STUB))
    shut_after_routes(context)
    return context


def shut_after_routes(context):
    """Make context.close() let go of the routes first, so that a route handler still in flight when the context closes (a request the page
    made a moment before, answered by a handler of ours or Playwright's own continue after it) cannot fail on the closed target in a task
    that nothing awaits, which ends as 'Task exception was never retrieved' on stderr, where a run's log loses it. Playwright's own advice for
    this is unroute_all(behavior="ignoreErrors") before the end. What a handler does wrong while a check is still running is not touched:
    it still raises there."""
    close = context.close

    def close_quietly(**options):
        try:
            context.unroute_all(behavior="ignoreErrors")
            for page in context.pages:
                page.unroute_all(behavior="ignoreErrors")
        except PlaywrightError:
            pass  # already closed
        close(**options)

    context.close = close_quietly


# WebGL's own complaints (an invalid call, a leaked context) arrive as console warnings, not errors.
WEBGL_WARNING = re.compile(r"WebGL|Too many active WebGL contexts|CONTEXT_LOST|Automatic fallback to software WebGL")


def open_page(context, url, warnings=False, until="networkidle", media=None):
    """Open url; stop the whole run with a plain reason if the page itself cannot be had. With
    warnings, WebGL's console warnings are kept among the errors (all but the one about read-back,
    which the cloth checks cause themselves). until is when the page counts as open: "load" for a
    page with a request held back, which would never go quiet. media is emulated before the page
    loads (page.emulate_media options, such as forced_colors="active")."""
    page = context.new_page()
    if media:
        page.emulate_media(**media)
    errors = []

    def heard(message):
        if message.type == "error" or (warnings and message.type == "warning" and WEBGL_WARNING.search(message.text)
                                       and "GPU stall due to ReadPixels" not in message.text):
            errors.append(message.text)

    page.on("console", heard)
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        response = page.goto(url, wait_until=until)
    except PlaywrightError as error:
        raise SystemExit(f"Could not open {url}: {error.message.splitlines()[0]}. Is the app running at --base-url?")
    status = response.status if response else "nothing"
    if status != 200:
        hint = " Is the session file current? Run make_staff_session.py again (AGENTS.md, Site checks)." if status in (302, 403, 404) else ""
        raise SystemExit(f"{url} answered {status}; every check needs the page itself.{hint}")
    return page, errors


WEBGL_INFO = """(strict) => {
  const gl = document.createElement("canvas").getContext("webgl", strict ? { failIfMajorPerformanceCaveat: true } : {});
  if (!gl) return null;
  const debug = gl.getExtension("WEBGL_debug_renderer_info");
  return { renderer: gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : gl.RENDERER),
           uniforms: gl.getParameter(gl.MAX_FRAGMENT_UNIFORM_VECTORS), cores: navigator.hardwareConcurrency };
}"""


POINTER_INFO = """() => ({ hover: matchMedia("(hover: hover)").matches ? "hover" : "none",
  pointer: ["fine", "coarse", "none"].find((kind) => matchMedia(`(pointer: ${kind})`).matches) || "unknown" })"""


def pointer_stop(device, where):
    """The checks expect a desktop browser: where the page shows a coarse pointer (a touchscreen) and no touch is emulated,
    :hover gives no flat fill (base.css) and the hover checks would fail for that reason alone."""
    if device["pointer"] == "coarse":
        raise SystemExit(f"This Chromium reports a coarse pointer (a touchscreen) {where} where no touch is emulated; the checks "
                         f"expect a desktop browser, in which :hover gives the flat fill (base.css).")


def webgl_precondition(browser, base_url, after=None):
    """Stop at once if this Chromium has no WebGL (the cloth checks would all fail for that reason
    alone), and print what renders it, so every log records the machine's GPU path. With after, it is a
    look in the middle of the run: something took WebGL away (a full-page screenshot taller than 8,192 px
    does), and one plain message beats a page of unrelated failures. Otherwise it also reads the hover and pointer
    media, from a page of the site under test in a plain desktop context, and returns them: the hover fills are
    gated on them (base.css), and a data: or about:blank document does not always report what a navigated page does."""
    page = browser.new_page()
    info = page.evaluate(WEBGL_INFO, True)
    # shell.js asks for failIfMajorPerformanceCaveat: true, so a context that only exists without it is no
    # WebGL for the cloth: ask again without the option, to say which of the two it is.
    anything = info or page.evaluate(WEBGL_INFO, False)
    device = None
    if not after:
        try:
            page.goto(base_url + "/", wait_until="domcontentloaded")
        except PlaywrightError as error:
            page.close()
            raise SystemExit(f"Could not open {base_url}/: {error.message.splitlines()[0]}. Is the app running at --base-url?")
        device = page.evaluate(POINTER_INFO)
    page.close()
    if not info:
        lost = f" after {after}" if after else ""
        if anything:
            raise SystemExit(f"This Chromium reports its WebGL ({anything['renderer']}) as a major performance caveat{lost} "
                             f"(launched with {' '.join(GL_ARGS)}); shell.js asks for failIfMajorPerformanceCaveat: true, "
                             f"so the cloth would not start; the cloth checks need a context without the caveat.")
        raise SystemExit(f"This Chromium has no WebGL at all{lost} (launched with {' '.join(GL_ARGS)}); the cloth checks need it.")
    if not after:
        print(f"WebGL: {info['renderer']}; {info['uniforms']} fragment uniform vectors; {info['cores']} cores")
        print(f"Pointer on {base_url}/: {device['pointer']}; hover: {device['hover']} (the hover fills answer unless the pointer is coarse)")
        pointer_stop(device, f"on {base_url}/")
        return device


# A full-page screenshot taller than 8192 px (the catalog without JavaScript at 375px is about 8,700)
# takes WebGL away from the whole browser for the rest of the run (Chromium with SwiftShader), and every
# later page would find "no WebGL". Pages taller than 8,000 px are cut there.
MAX_SHOT_HEIGHT = 8000


def full_page_screenshot(page, path):
    if page.evaluate("() => document.documentElement.scrollHeight") <= MAX_SHOT_HEIGHT:
        page.screenshot(path=str(path), full_page=True)
    else:
        page.screenshot(path=str(path), full_page=True,
                        clip={"x": 0, "y": 0, "width": page.viewport_size["width"], "height": MAX_SHOT_HEIGHT})


def grain_problems(page, label, appearance):
    """The still grain is the tile made for this appearance's ground (spec 3.3)."""
    tile = "grain-ink" if appearance == "dark" else "grain-paper"
    found = page.evaluate("() => getComputedStyle(document.documentElement).backgroundImage")
    problems = [] if tile in found else [f"{label}: the page's grain is {found}; it should be {tile}.png"]
    # It is the page's one grain: nothing is laid over the page at any width with a grain or a ground of its own (the phone's
    # top used to be: now the page fades out there), so an element that is fixed and painted is a problem, the corners and the
    # canvas apart (cloth_top_fade_problems looks at what that top shows).
    over = page.evaluate("""() => [...document.querySelectorAll('body *')].filter((element) => {
        const style = getComputedStyle(element);
        return style.position === 'fixed' && !element.closest('.shell-corner') && !element.matches('.skip-link, canvas.veil')
          && (style.backgroundImage !== 'none' || style.backgroundColor !== 'rgba(0, 0, 0, 0)'); })
      .map((element) => element.className || element.tagName)""")
    if over:
        problems.append(f"{label}: {over} lie over the page with a background of their own; the page's grain and ground should show everywhere")
    return problems


GRAIN_TILE = 128   # px: the still grain's tile (tools/make_images.py)


# The pixel ratios the still grain is sized for (base.css has a rule for each): the whole ratios of Macs and iPhones and the fractional ones of
# Windows scaling (125%, 150%, 175%) and Android (2.625, 2.75).
GRAIN_RATIOS = (1, 1.25, 1.5, 1.75, 2, 2.625, 2.75, 3, 4)
GRAIN_REPEAT = 0.25   # the most a row of the screenshot may differ from itself one tile (128 device px) on, in channel levels of 255 on average: an exact tile measures 0 to 0.1, one drawn at 106.67 or 117.33 device px 0.55 or more
GRAIN_ROW_SPREAD = 0.5   # the least a row of the screenshot may vary, in channel levels of 255 (mean distance from the row's mean, over the rows and channels): a flat row repeats perfectly, so a page whose grain is gone would pass GRAIN_REPEAT. A page with the grain measures 1.08 (Ink at 3x, the weakest of the 13 cases) to 1.79 (Paper); a flat one 0


def grain_row_figures(channels, rows):
    """(change, spread) of the rows of a screenshot (from `png_rows`), every fourth row and the three colour channels: change is how far a row
    differs from itself GRAIN_TILE device px along and spread how far it varies from its own mean, both in channel levels of 255 on average.
    The width is the screenshot's own (a clip of 787.5 device px comes out 788 or 787 wide), the channels what the PNG holds."""
    sample, width = rows[::4], len(rows[0]) // channels
    change = spread = 0
    for row in sample:
        for channel in range(3):
            line = row[channel::channels]
            change += sum(abs(here - there) for here, there in zip(line, line[GRAIN_TILE:])) / (width - GRAIN_TILE)
            mean = sum(line) / width
            spread += sum(abs(value - mean) for value in line) / width
    return change / (len(sample) * 3), spread / (len(sample) * 3)


def grain_scale_problems(browser, base_url, session_key):
    """The still grain (spec 3.3) is drawn at the size of the screen's pixels, as the cloth's grain follows the screen: one pixel of
    its 128 px tile is one device pixel, so on a 2x screen the tile is 64 CSS px and on a 3x screen 42.67 (a tile pixel the size of a
    CSS pixel would show every speck 2 or 3 pixels across). At the fractional ratios the tile must still come to a whole number of
    device pixels, 128, or it is resampled and does not repeat on the pixel grid (at 1.25 a tile of 85.33 CSS px is 106.67 device px, at
    2.75 a tile of 42.67 is 117.33). Read from the computed background-size of the page (auto is the tile's own 128 CSS px, one device
    pixel each at pixel ratio 1) at the ratios of GRAIN_RATIOS, on a phone and on a wide screen, in Paper and in Ink; and then from the
    screenshot, which must repeat exactly every 128 device px along a row (GRAIN_REPEAT); and the page's ground is the one element that
    draws the grain."""
    problems = []
    cases = [(375, dsf, "light") for dsf in GRAIN_RATIOS] + [(375, 3, "dark"), (1280, 1, "light"), (1280, 1.25, "dark"), (1280, 2, "dark")]
    for width, dsf, theme in cases:
        label = f"/about/ at {width}px, pixel ratio {dsf:g}, {'Ink' if theme == 'dark' else 'Paper'}, Motion off: the still grain"
        context = new_context(browser, base_url, session_key, page_named("/about/"), width=width, theme=theme, motion="off", device_scale_factor=dsf)
        page, errors = open_page(context, base_url + "/about/")
        size = page.evaluate("() => getComputedStyle(document.documentElement).backgroundSize")
        found = re.findall(r"[\d.]+(?=px)", size)
        tile = float(found[0]) if size != "auto" and len(found) == 2 and found[0] == found[1] else (GRAIN_TILE if size in ("auto", "auto auto") else None)
        if tile is None:
            problems.append(f"{label}: the grain's background-size is {size!r}; it should be auto or a square in px")
        elif abs(tile * dsf / GRAIN_TILE - 1) > 0.0005 or abs(tile * dsf - round(tile * dsf)) > 0.005:
            problems.append(f"{label}: the grain's tile is {tile:g} CSS px, so it is {tile * dsf:.2f} device pixels and one of its pixels is {tile * dsf / GRAIN_TILE:.3f}; "
                            f"it should be {GRAIN_TILE}, whole, a tile of {GRAIN_TILE / dsf:.4f} CSS px (background-size on html, by resolution)")
        unseen = page.add_style_tag(content="main, header, footer, .shell-corner, canvas { visibility: hidden !important; }")  # only the page's ground
        channels, rows = png_rows(page.screenshot(clip={"x": 0, "y": 0, "width": 300, "height": 20}))
        unseen.evaluate("(style) => style.remove()")
        change, spread = grain_row_figures(channels, rows)
        if change > GRAIN_REPEAT:
            problems.append(f"{label}: the grain does not repeat on the pixel grid: a row of the screenshot differs by {change:.2f} levels on average from itself "
                            f"{GRAIN_TILE} device px along, where an exact tile differs by under {GRAIN_REPEAT}; its tile is not drawn at {GRAIN_TILE} device pixels")
        if spread < GRAIN_ROW_SPREAD:
            problems.append(f"{label}: a row of the screenshot varies by {spread:.2f} levels on average from its own mean, under {GRAIN_ROW_SPREAD}: "
                            f"the page's ground shows no grain, so its repeat says nothing")
        drawn = page.evaluate("""() => [...document.querySelectorAll('html, body, body *')].filter((element) => {
            const style = getComputedStyle(element); return style.backgroundImage.includes('grain'); }).map((element) => element.tagName.toLowerCase())""")
        if drawn != ["html"]:
            problems.append(f"{label}: {drawn} draw the grain; only the page's ground (html) should, and sizes it")
        if width < 1024:  # the open menu sheet's surface is the one other thing that draws it, at the same size (spec 4.4)
            page.locator("header [data-menu-toggle]").click()
            sheet = page.evaluate("""() => ({ own: getComputedStyle(document.querySelector('#shell-menu'), '::before').backgroundSize,
                drawn: [...document.querySelectorAll('body *')].flatMap((element) => ['::before', '::after']
                  .filter((pseudo) => getComputedStyle(element, pseudo).backgroundImage.includes('grain'))
                  .map((pseudo) => element.tagName.toLowerCase() + (element.id ? '#' + element.id : '') + pseudo)) })""")
            if sheet["drawn"] != ["div#shell-menu::before"]:
                problems.append(f"{label}: {sheet['drawn']} draw the grain with a pseudo-element; only the open menu sheet's surface (div#shell-menu::before) should")
            if sheet["own"] != size:
                problems.append(f"{label}: the open menu sheet's grain is drawn at {sheet['own']} where the page's is {size}; they should be the same tile pixel for pixel")
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()
    return problems


# The cloth checks load the real veil.js and drive it with a virtual clock: every display frame is
# 1/60 s of cloth time (or the step a check sets), however long the machine takes to draw it, and time
# can be held still. They read the canvas back after a draw, so they measure exactly what the shader
# painted, which axe cannot see. Nothing they assert depends on the machine's speed. Only the frames a
# check reads are drawn, so the checks cost little CPU even on a software GPU.
CLOTH_PROBE = """(() => {
  const cloth = window.__cloth = { draws: 0, frames: 0, contexts: 0, gl: null, last: null, want: false, wait: null, drawAll: false, noInk: false, force: {}, uniforms: {}, history: [] };
  const clock = cloth.clock = { t: 1000, step: 1000 / 60, stopAt: Infinity };
  const realFrame = window.requestAnimationFrame.bind(window);
  let lastReal = -1;
  window.requestAnimationFrame = (callback) => realFrame((real) => {
    if (real !== lastReal) {   // callbacks that share a display frame share its time, as in a browser
      lastReal = real;
      if (cloth.frames >= clock.stopAt) clock.step = 0;
      clock.t += clock.step; cloth.frames += 1;
    }
    callback(clock.t);
  });
  performance.now = () => clock.t;
  const getContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (kind, ...rest) {
    const gl = getContext.call(this, kind, ...rest);
    if (gl && /webgl/.test(String(kind))) { cloth.contexts += 1; cloth.gl = gl; }
    return gl;
  };
  // The vec4 arrays the cloth uploads (u_inkP, u_inkQ: its ink washes), as it last uploaded them, by name: what
  // the shader was asked to draw, which a check can read without measuring pixels (see inks_drawn).
  const names = new WeakMap();
  const getUniformLocation = WebGLRenderingContext.prototype.getUniformLocation;
  WebGLRenderingContext.prototype.getUniformLocation = function (program, name) {
    const location = getUniformLocation.call(this, program, name);
    if (location) names.set(location, name);
    return location;
  };
  // Scalars too (u_soft, u_cloth, u_grain, u_sheet): by name in cloth.uniforms as the cloth asked for them, and a check can force a value for
  // one of them (cloth.force.u_soft = 1) to see the frame as it would be drawn with that value: the wash with no widening, the
  // film grain alone on the paper (u_cloth 0), the phone sheet away (u_sheet 0).
  const uniform1f = WebGLRenderingContext.prototype.uniform1f;
  WebGLRenderingContext.prototype.uniform1f = function (location, value) {
    let upload = value;
    if (names.has(location)) {
      const name = names.get(location);
      cloth.uniforms[name] = value;
      if (name in cloth.force) upload = cloth.force[name];
    }
    return uniform1f.call(this, location, upload);
  };
  const uniform4fv = WebGLRenderingContext.prototype.uniform4fv;
  WebGLRenderingContext.prototype.uniform4fv = function (location, value, ...rest) {
    let upload = value;
    if (names.has(location)) {
      const name = names.get(location);
      cloth.uniforms[name] = Array.from(value);
      // Every frame's washes (their boxes, then their strengths, which follow in the same upload), for a check that
      // must see each frame and not the last one, with the canvas's size in pixels that frame (the boxes are in its pixels, and the
      // cloth's resolution steps when frames run long: WASH_FRAME's at).
      if (name === "u_inkQ" && cloth.history.length < 4000) cloth.history.push({ frame: cloth.frames, p: cloth.uniforms.u_inkP, q: cloth.uniforms.u_inkQ, w: this.canvas.width, h: this.canvas.height });
      if (name === "u_inkQ" && cloth.noInk) upload = new Float32Array(value.length);   // the shader is told no wash has any strength: the cloth alone
    }
    return uniform4fv.call(this, location, upload, ...rest);
  };
  // The rasteriser is what costs time on a software GPU, and the cloth's state (time, inks, the
  // adaptive measure) does not depend on drawing, so a draw call is counted and, unless a check is about
  // to read the frame (cloth.want) or asks for every frame (cloth.drawAll), not made. Checks that count
  // draws count calls.
  const draw = WebGLRenderingContext.prototype.drawArrays;
  WebGLRenderingContext.prototype.drawArrays = function (...args) {
    cloth.draws += 1;
    if (!cloth.want && !cloth.drawAll) return undefined;
    const result = draw.apply(this, args);
    if (cloth.want) {
      cloth.want = false;
      queueMicrotask(() => {
        const w = this.drawingBufferWidth, h = this.drawingBufferHeight, px = new Uint8Array(w * h * 4);
        this.readPixels(0, 0, w, h, this.RGBA, this.UNSIGNED_BYTE, px);
        cloth.last = { w, h, px, cssW: this.canvas.clientWidth, cssH: this.canvas.clientHeight };
        if (cloth.wait) cloth.wait(true);
      });
    }
    return result;
  };
  // Resolves true when the next frame has been read, false if none is drawn within 10 s (the cloth has stopped).
  cloth.capture = () => new Promise((done) => {
    const timer = setTimeout(() => { cloth.want = false; cloth.wait = null; done(false); }, 10000);
    cloth.wait = (read) => { clearTimeout(timer); cloth.wait = null; done(read); };
    cloth.want = true;
  });
  const linear = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  // A viewport rectangle (CSS px) of the last captured frame: relative luminance (for contrast) and
  // gamma luma (for what a person sees change).
  cloth.stats = ({ x, y, width, height }) => {
    const { w, h, px, cssW, cssH } = cloth.last, sx = w / cssW, sy = h / cssH;
    const x0 = Math.max(0, Math.floor(x * sx)), x1 = Math.min(w, Math.ceil((x + width) * sx));
    const y0 = Math.max(0, Math.floor(h - (y + height) * sy)), y1 = Math.min(h, Math.ceil(h - y * sy));
    let n = 0, sum = 0, sum2 = 0, lo = 1, hi = 0, luma = 0, luma2 = 0;
    for (let j = y0; j < y1; j++) for (let i = x0; i < x1; i++) {
      const k = (j * w + i) * 4, v = 0.2126 * linear(px[k]) + 0.7152 * linear(px[k + 1]) + 0.0722 * linear(px[k + 2]);
      const g = (0.2126 * px[k] + 0.7152 * px[k + 1] + 0.0722 * px[k + 2]) / 255;
      n += 1; sum += v; sum2 += v * v; lo = Math.min(lo, v); hi = Math.max(hi, v);
      luma += g; luma2 += g * g;
    }
    const mean = sum / n, lumaMean = luma / n;
    return { n, mean, std: Math.sqrt(Math.max(0, sum2 / n - mean * mean)), min: lo, max: hi, luma: lumaMean,
             spread: Math.sqrt(Math.max(0, luma2 / n - lumaMean * lumaMean)) };   // the gamma luma's own spread: what a person sees vary
  };
})();"""
# A browser with no WebGL, as far as the page can tell.
NO_WEBGL = """(() => {
  const refuse = (Canvas) => { if (!Canvas) return; const original = Canvas.prototype.getContext;
    Canvas.prototype.getContext = function (kind, ...rest) { return /webgl/.test(String(kind)) ? null : original.call(this, kind, ...rest); }; };
  refuse(window.HTMLCanvasElement); refuse(window.OffscreenCanvas);
})();"""
# A GPU whose compiler refuses the shader.
SHADER_FAILS = """(() => {
  const original = WebGLRenderingContext.prototype.getShaderParameter;
  WebGLRenderingContext.prototype.getShaderParameter = function (shader, name) {
    return name === this.COMPILE_STATUS ? false : original.call(this, shader, name); };
  WebGLRenderingContext.prototype.getShaderInfoLog = () => "site checks: a shader that does not compile";
})();"""
VEIL_STATE = """() => {
  const root = document.documentElement, button = document.querySelector("footer [data-motion-toggle]");
  let saved = null;
  try { saved = localStorage.getItem("veilfile-motion"); } catch (error) { saved = "blocked"; }
  return { veil: root.dataset.veil || null, off: root.dataset.veilOff || null, motion: root.getAttribute("data-motion"),
           canvases: document.querySelectorAll("canvas").length, draws: window.__cloth ? window.__cloth.draws : null,
           contexts: window.__cloth ? window.__cloth.contexts : null,
           pressed: button ? button.getAttribute("aria-pressed") : null, saved };
}"""
FREEZE = "window.__cloth.clock.stopAt = 0;"  # hold cloth time still from the first frame, for exact comparisons
CANVAS_SHAPE = """() => {
  const canvas = document.querySelector("canvas.veil");
  if (!canvas) return null;
  const style = getComputedStyle(canvas);
  return { hidden: canvas.getAttribute("aria-hidden"), position: style.position, pointer: style.pointerEvents,
           z: style.zIndex, first: document.body.firstElementChild === canvas, scale: canvas.width / innerWidth,
           spans: canvas.getBoundingClientRect().width - innerWidth };
}"""


def page_named(path):
    return next(site_page for site_page in PAGES + CATALOG_PAGES if site_page.path == path)


def first_step(width, device_scale_factor=1):
    """The cloth's first step for a window `width` CSS px wide on a screen with this device pixel ratio (spec 3.1): 0.4 of
    the ratio (a canvas pixel is 2.5 screen pixels), kept between 0.8 and 1.2, or between 0.65 and 1.2 on a phone (40rem,
    640 px, and narrower). Written out here and not read from veil.js, so a change to the rule fails until the spec says so."""
    return min(1.2, max(0.65 if width <= 640 else 0.8, 0.4 * device_scale_factor))


# The whole ladder (spec 3.1, 8) of a 375 px phone, as the spec states it: the first step, then each step three quarters of the
# one above, down to the floor, 0.28 (0.35 on a wide screen), which also takes a step that would come within 15% above it. The
# canvas's widths are 375 px times a step, whole pixels: the frame models (frame_model) tell the steps apart by them.
PHONE_LADDERS = {
    1: (0.65, 0.4875, 0.365625, 0.28),
    3: (1.2, 0.9, 0.675, 0.50625, 0.379688, 0.28),
}


def phone_widths(ladder):
    return [round(375 * step) for step in ladder]


def cloth_context(browser, base_url, session_key, site_page, *, width=1024, height=640, theme="light", motion="on",
                  reduced_motion="no-preference", scripts=(), touch=False, device_scale_factor=1):
    """A context that runs the real veil.js under CLOTH_PROBE, and the list of its requests for veil.js.
    1024x640 is the smallest wide layout: the cheapest canvas that has the corners and the open menu. With touch,
    a finger (tap) and not a mouse is how the page is used. The device pixel ratio is 1 unless a check sets
    device_scale_factor: the cloth's first step follows it (spec 3.1, first_step)."""
    context = new_context(browser, base_url, session_key, site_page, width=width, height=height, theme=theme,
                          motion=motion, reduced_motion=reduced_motion, cloth=True, touch=touch,
                          device_scale_factor=device_scale_factor)
    context.add_init_script(CLOTH_PROBE)
    for script in scripts:
        context.add_init_script(script)
    requests = []
    context.on("request", lambda request: requests.append(request.url) if VEIL_SCRIPT.match(request.url) else None)
    return context, requests


def cloth_running(page, timeout=30000):
    """Whether the cloth has drawn (veil.js sets <html data-veil="on"> after its first frame)."""
    try:
        page.wait_for_function("() => document.documentElement.dataset.veil === 'on'", timeout=timeout)
        return True
    except PlaywrightError:
        return False


def run_frames(page, count, step=1000 / 60):
    """Let count animation frames pass, each step ms of cloth time, then hold time still (frames go on
    being drawn, so a capture is possible, but nothing moves or eases). If the cloth stops on the way
    (Motion off, the still page) it asks for no more frames, so that ends the wait too."""
    page.evaluate("([count, step]) => { const c = window.__cloth; c.clock.step = step; c.clock.stopAt = c.frames + count; }",
                  [count, step])
    page.wait_for_function("() => window.__cloth.frames > window.__cloth.clock.stopAt + 1"
                           " || !document.documentElement.dataset.veil", timeout=240000)


def wait_sheet_in_place(page, timeout=5000):
    """The menu's sheet has come in (SHEET_IN_PLACE). On a page with the cloth's probe (window.__cloth) frames come on the probe's virtual clock, which a
    check holds still, and shell.js steps the sheet by the frames' times, so 48 frames (0.8 s of cloth time, more than the sheet's
    0.7 s) are let pass first; with Motion off, or no cloth, run_frames returns at once and the sheet is in place already."""
    if page.evaluate("() => Boolean(window.__cloth)"):
        run_frames(page, 48)
    page.wait_for_function(SHEET_IN_PLACE, timeout=timeout)


# The menu is shut and has gone: MENU says shut and the list is not displayed. The menu, the column from 64rem up as well as the sheet, stays
# displayed for the 0.7 s of its close (shell.js' driver), taking no pointer, while MENU already says shut.
MENU_GONE = """() => { const toggle = document.querySelector('header [data-menu-toggle]'), menu = document.querySelector('#shell-menu');
  return (!toggle || toggle.getAttribute('aria-expanded') === 'false') && (!menu || getComputedStyle(menu).display === 'none'); }"""


def menu_gone(page, timeout=5000):
    """True once the menu is shut and its close has run (MENU_GONE), waited for up to timeout ms (a bound, never a delay); False if it still shows
    then. For a page whose frames run on their own: the cloth's virtual clock (run_frames) holds the driver still."""
    try:
        page.wait_for_function(MENU_GONE, timeout=timeout)
        return True
    except PlaywrightError:
        return False


def leap_frames(page, count=58):
    """Let count frames of 100 ms pass (the longest step the cloth takes in one frame, so cloth time moves by
    0.06 per frame at the chapters' pace), then hold time still. The cloth reads frames that long as a slow
    device, but judges a window of 60 frames only after 30 of warm-up, and by their median, so a leap that
    starts from a tab coming back (visibilitychange resets the count) and has under 30 long frames after the
    warm-up, which the held frames that follow make up to the window, is never judged slow. The 58 is tied to veil.js's
    WARMUP and WINDOW by pages.tests_veil.LeapFramesTests: change one and that test names the other."""
    page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    run_frames(page, count, step=100)


class ClothStopped(Exception):
    """The cloth stopped drawing (Motion off, the still page, an error) while a check waited to read it."""


def capture(page):
    """Read back the next frame the cloth draws. If none comes within 10 s, the cloth has stopped."""
    if not page.evaluate("() => window.__cloth.capture()"):
        raise ClothStopped(f"the cloth stopped drawing ({page.evaluate(VEIL_STATE)})")


def canvas_stats(page, rect):
    return page.evaluate("(rect) => window.__cloth.stats(rect)", rect)


# Where the cloth's pixels lie on the page, the one convention every helper that reads the uploaded washes uses (veil.js lays
# them out the same way): in the canvas's own box, x counting from its left and y up from its bottom, in canvas pixels. The
# box is not always the window (base.css gives it 100vw by 100%, and a phone's innerHeight moves with the toolbar while the
# fixed canvas does not). frame.x(px) and frame.y(py) are a point in CSS pixels from the page's top left; frame.rx and
# frame.ry a radius.
# The canvas's frame as it is now; frame.at(w, h) is the frame of a canvas w by h pixels in the same box, for a frame of the history (CLOTH_PROBE
# records each one's size): the cloth's resolution steps when frames run long (adaptive rendering), as frames of 100 ms or more do, so a wash
# recorded before a step and read with the canvas's size after it would be placed by a third too far (one rung of the ladder) from where it was.
WASH_FRAME = """const frame = (() => { const canvas = document.querySelector('canvas.veil'), b = canvas.getBoundingClientRect();
  const at = (w = canvas.width, h = canvas.height) => { const sx = w / b.width, sy = h / b.height;
    return { x: (px) => b.left + px / sx, y: (py) => b.bottom - py / sy, rx: (r) => r / sx, ry: (r) => r / sy }; };
  return Object.assign(at(), { at }); })();"""
INKS_DRAWN = "() => { const { u_inkP: p, u_inkQ: q } = window.__cloth.uniforms; " + WASH_FRAME + """
  const inks = [];
  for (let i = 0; p && i < p.length / 4; i++) if (q[i * 4 + 1] > 0.0005) inks.push({ x: frame.x(p[i * 4]), y: frame.y(p[i * 4 + 1]),
    rx: frame.rx(p[i * 4 + 2]), ry: frame.ry(p[i * 4 + 3]), grow: q[i * 4], strength: q[i * 4 + 1], age: q[i * 4 + 2] });
  return inks; }"""
# Where the washes the cloth last uploaded appear on the screen: each one's centre in the page's CSS pixels from its top left.
INKS_ON_SCREEN = "() => { const { u_inkP: p, u_inkQ: q } = window.__cloth.uniforms; " + WASH_FRAME + """
  const found = [];
  for (let i = 0; p && i < p.length / 4; i++) if (q[i * 4 + 1] > 0.0005) found.push({ x: frame.x(p[i * 4]), y: frame.y(p[i * 4 + 1]), strength: q[i * 4 + 1] });
  return found; }"""
# The wash drawn under an element in the cloth's last frame (its centre, in CSS pixels, from the top left), if any.
WASH_UNDER = "(selector) => { const { u_inkP: p, u_inkQ: q } = window.__cloth.uniforms; " + WASH_FRAME + """
  const b = document.querySelector(selector).getBoundingClientRect(), cx = b.left + b.width / 2, cy = b.top + b.height / 2;
  for (let i = 0; p && i < p.length / 4; i++) {
    if (q[i * 4 + 1] > 0.0005 && Math.abs(frame.x(p[i * 4]) - cx) < 2 && Math.abs(frame.y(p[i * 4 + 1]) - cy) < 2) {
      return { grow: q[i * 4], strength: q[i * 4 + 1], age: q[i * 4 + 2] }; } }
  return null; }"""


# The strength each frame's wash under a point (its x and y in CSS pixels from the top left) was drawn at, from every frame the cloth drew
# (0 where there was none): [frame, strength] pairs, for the frames from the one given on. A point, not an element: the element may have gone,
# like a menu link once the menu has shut, and then has no box to look at. `rise` (px, default 0) lets the wash sit anywhere from the point up to
# that far above it, within 2 px: a wash follows its link only in the frames that draw the link, and a link that fades out while it moves
# (the sheet's links rise 6px as it closes) is last drawn somewhere on the way, which depends on how often frames come.
WASH_HISTORY_AT = "([point, since, rise = 0]) => { " + WASH_FRAME + """
  const [cx, cy] = point;
  return window.__cloth.history.filter((entry) => entry.frame >= since).map(({ frame: number, p, q, w, h }) => {
    const drawn = frame.at(w, h);
    for (let i = 0; i < p.length / 4; i++) {
      const y = drawn.y(p[i * 4 + 1]);
      if (q[i * 4 + 1] > 0 && Math.abs(drawn.x(p[i * 4]) - cx) < 2 && y > cy - rise - 2 && y < cy + 2) return [number, q[i * 4 + 1]]; }
    return [number, 0]; }); }"""
# Every wash each frame drew, from the frame given on, for `count` frames: [frame, [[x, y, strength], ...]], centres in CSS pixels from the top left.
# What a check that found no wash where it looked reports, so that a wash drawn somewhere else, or none at all, can be told apart.
WASHES_FROM = "([since, count]) => { " + WASH_FRAME + """
  return window.__cloth.history.filter((entry) => entry.frame >= since).slice(0, count).map(({ frame: number, p, q, w, h }) => {
    const washes = [], drawn = frame.at(w, h);
    for (let i = 0; i < p.length / 4; i++) if (q[i * 4 + 1] > 0) washes.push([Math.round(drawn.x(p[i * 4])), Math.round(drawn.y(p[i * 4 + 1])), +q[i * 4 + 1].toFixed(2)]);
    return [number, washes]; }); }"""


def wash_strength(page, selector):
    """How strong the wash drawn under the element is in the cloth's last frame (0 when there is none)."""
    wash = page.evaluate(WASH_UNDER, selector)
    return wash["strength"] if wash else 0


def cloth_screenshot(page, name):
    """A viewport screenshot of the cloth as it is now, saved to the evidence directory (spec 10); nothing
    when there is none (a one-off run). The probe draws only the frames a check reads, so the cloth is
    drawn on every frame for the moment it takes (time is held still, so it is the frame just captured)."""
    if not EVIDENCE["dir"]:
        return
    page.evaluate("() => { window.__cloth.drawAll = true; }")
    page.evaluate("() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))")
    page.screenshot(path=str(EVIDENCE["dir"] / name))
    page.evaluate("() => { window.__cloth.drawAll = false; }")


def cloth_presence_problems(browser, base_url, session_key):
    """The cloth runs only when Motion is on and WebGL works (spec 3.3, 6.3): as one fixed canvas first
    in <body>, behind everything, out of the pointer's way and the accessibility tree (spec 9), at a resolution
    that follows the screen (spec 3.1, first_step): 0.4 of the device pixel ratio, kept between 0.8 (0.65 on a
    phone) and 1.2, so a canvas pixel is 2.5 screen pixels wherever the range allows. The checks' Chromium has a
    pixel ratio of 1 (0.8, or 0.65 on a phone); cases at 1.5, 2, 3 and 3.5 show the rest. With Motion off, or
    reduced motion and nothing saved, veil.js is never fetched and nothing is drawn."""
    problems = []
    about = page_named("/about/")
    cases = (
        ("Motion saved on", about, 1024, 640, "on", "no-preference", True, 1),
        ("nothing saved", about, 1024, 640, None, "no-preference", True, 1),
        ("Motion saved off", about, 1024, 640, "off", "no-preference", False, 1),
        ("reduced motion, nothing saved", about, 1024, 640, None, "reduce", False, 1),
        ("reduced motion, Motion saved on", about, 1024, 640, "on", "reduce", True, 1),
        ("a phone", about, 375, 667, "on", "no-preference", True, 1),
        ("a phone at pixel ratio 1.5 (under its 0.65)", about, 375, 667, "on", "no-preference", True, 1.5),
        ("a phone at pixel ratio 2", about, 375, 667, "on", "no-preference", True, 2),
        ("a phone at pixel ratio 3", about, 375, 667, "on", "no-preference", True, 3),
        ("a phone at pixel ratio 3.5 (over its 1.2)", about, 375, 667, "on", "no-preference", True, 3.5),
        ("a 2x screen", about, 1024, 640, "on", "no-preference", True, 2),
        ("the threshold", page_named("/"), 1024, 640, "on", "no-preference", True, 1),
        ("the component catalog", page_named("/app/components/"), 1024, 640, "on", "no-preference", True, 1),
    )
    for name, site_page, width, height, motion, reduced, runs, dsf in cases:
        label = f"{site_page.path} cloth, {name}"
        context, requests = cloth_context(browser, base_url, session_key, site_page, width=width, height=height,
                                          motion=motion, reduced_motion=reduced, device_scale_factor=dsf)
        page, errors = open_page(context, base_url + site_page.path, warnings=True)
        if runs:
            if not cloth_running(page):
                problems.append(f"{label}: the cloth never started ({page.evaluate(VEIL_STATE)})")
            else:
                shape, state = page.evaluate(CANVAS_SHAPE), page.evaluate(VEIL_STATE)
                scale = first_step(width, dsf)
                if state["canvases"] != 1 or not shape or (shape["hidden"], shape["position"], shape["pointer"], shape["z"], shape["first"]) != (
                        "true", "fixed", "none", "-1", True):
                    problems.append(f"{label}: expected one canvas first in <body>, aria-hidden, fixed, no pointer events, "
                                    f"z-index -1; found {shape} with {state['canvases']} canvas(es)")
                elif abs(shape["scale"] - scale) > 0.02:
                    problems.append(f"{label}: the cloth renders at {shape['scale']:.2f} of the CSS width; it should be {scale}")
                if shape and abs(shape["spans"]) > 1:
                    problems.append(f"{label}: the canvas is {shape['spans']:+.0f} px wider than the window; it must span it exactly")
                if name == "Motion saved on":  # the canvas is page content to a printer: a full dark page in Ink
                    page.emulate_media(media="print")
                    printed = page.evaluate("() => getComputedStyle(document.querySelector('canvas.veil')).display")
                    page.emulate_media(media="screen")
                    if printed != "none":
                        problems.append(f"{label}: the cloth should not print (the canvas is display: {printed} in print media)")
                if len(requests) != 1:
                    problems.append(f"{label}: veil.js was fetched {len(requests)} times")
        else:
            page.wait_for_timeout(1500)
            state = page.evaluate(VEIL_STATE)
            if state["canvases"] or state["veil"] or state["draws"] or requests:
                problems.append(f"{label}: nothing should run and veil.js should never be fetched ({state}; requests {requests})")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    # Forced colours (Windows High Contrast) give text system colours the cloth could not keep contrast with, so the
    # cloth does not run: the still page for the visit, Motion off, the visitor's saved choice untouched (spec 9).
    label = "/about/ cloth, forced colours"
    context, requests = cloth_context(browser, base_url, session_key, about)
    page, errors = open_page(context, base_url + about.path, warnings=True, media={"forced_colors": "active"})
    page.wait_for_timeout(1500)
    state = page.evaluate(VEIL_STATE)
    expected = {"off": "forced-colors", "motion": "off", "pressed": "false", "saved": "on", "canvases": 0, "veil": None, "draws": 0}
    if {key: state[key] for key in expected} != expected or requests:
        problems.append(f"{label}: expected the still page for the visit with veil.js never fetched ({expected}); found {state}, "
                        f"requests {requests}")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()

    # A classic scrollbar (Windows, Linux) takes room from the page but not from the window: the canvas must still span the
    # window (.veil is 100vw, not 100%: a fixed box adds nothing to the page's width), so that no seam is left at the
    # scrollbar's gutter and the drawing lines up with the page to the last pixel. (Its pixels follow its own box, not
    # innerWidth; every wash is placed in that box's frame.) Headless Chromium hides its scrollbars, so this one case runs
    # in a second browser that shows them.
    label = "/about/ cloth under a classic scrollbar"
    classic = browser.browser_type.launch(args=GL_ARGS, ignore_default_args=["--hide-scrollbars"])
    try:
        context, _ = cloth_context(classic, base_url, session_key, about)
        page, errors = open_page(context, base_url + about.path, warnings=True)
        if not cloth_running(page):
            problems.append(f"{label}: the cloth never started")
        else:
            page.add_style_tag(content="html { overflow-y: scroll }")  # the page may be short; the bar is there anyway
            box = page.evaluate("""() => { const b = document.querySelector("canvas.veil").getBoundingClientRect();
              return { inner: innerWidth, client: document.documentElement.clientWidth, left: b.left, right: b.right,
                       sideways: document.documentElement.scrollWidth - document.documentElement.clientWidth }; }""")
            if box["sideways"] > 0:
                problems.append(f"{label}: the canvas makes the page {box['sideways']} px wider than its window (it scrolls sideways)")
            if box["client"] >= box["inner"]:
                problems.append(f"{label}: this Chromium shows no scrollbar, so the check proves nothing ({box})")
            elif abs(box["left"]) > 1 or abs(box["right"] - box["inner"]) > 1:
                problems.append(f"{label}: with a {box['inner'] - box['client']} px scrollbar the canvas spans {box['left']:.0f} to "
                                f"{box['right']:.0f} px; it must span the window, 0 to {box['inner']}")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()
    finally:
        classic.close()
    return problems


def cloth_failure_problems(browser, base_url, session_key):
    """When the cloth cannot run (no WebGL, veil.js failing to load, a shader that does not compile), the
    visitor gets the still page with Motion off for this visit only (spec 3.3): data-motion and the
    Motion button say off, data-veil-off says why, storage keeps their choice, and the page still works."""
    problems = []
    about = page_named("/about/")
    cases = (  # name, init scripts, abort veil.js, the reason, whether veil.js is fetched
        ("no WebGL", (NO_WEBGL,), False, "no-webgl", False),
        ("veil.js failing to load", (), True, "error", True),
        ("a shader that does not compile", (SHADER_FAILS,), False, "error", True),
    )
    for name, scripts, abort, reason, fetched in cases:
        label = f"/about/ cloth, {name}"
        context, requests = cloth_context(browser, base_url, session_key, about, scripts=scripts)
        if abort:
            context.route(VEIL_SCRIPT, lambda route: route.abort())
        page, errors = open_page(context, base_url + about.path, warnings=True)
        try:
            page.wait_for_function("() => Boolean(document.documentElement.dataset.veilOff)", timeout=30000)
        except PlaywrightError:
            pass
        state = page.evaluate(VEIL_STATE)
        expected = {"off": reason, "motion": "off", "pressed": "false", "saved": "on", "canvases": 0, "veil": None}
        found = {key: state[key] for key in expected}
        if found != expected:
            problems.append(f"{label}: expected {expected}, found {found}")
        if bool(requests) != fetched:
            problems.append(f"{label}: veil.js was {'not ' if fetched else ''}expected to be fetched; requests {requests}")
        theme = page.evaluate(ROOT_ATTRIBUTE, "data-theme")
        page.locator("footer [data-appearance-toggle]").click()
        if page.evaluate(ROOT_ATTRIBUTE, "data-theme") == theme:
            problems.append(f"{label}: the appearance switch no longer answers")
        allowed = ("Failed to load resource",) if abort else ()
        problems += [f"{label}: console: {error}" for error in errors if not error.startswith(allowed or ("\0",))]
        context.close()
    return problems


def cloth_lifecycle_problems(browser, base_url, session_key):
    """The cloth fades, it never pops: the first reveal fades it in over about a second, and Motion off
    fades it out over about a second and then stops everything: no canvas, no drawing (spec 3.3). Both
    are measured part-way, since the end state alone would pass a cloth that vanished at once. On
    brings it back from the module already loaded. Ten clicks in a row leave one running cloth, no
    leaked WebGL context, and a button that says what data-motion says."""
    problems = []
    about = page_named("/about/")

    label = "/about/ cloth, first reveal"
    context, _ = cloth_context(browser, base_url, session_key, about, scripts=(FREEZE,))
    page, errors = open_page(context, base_url + about.path, warnings=True)
    if not cloth_running(page):
        problems.append(f"{label}: the cloth never started")
    else:
        whole = page.evaluate("() => ({ x: 0, y: 0, width: innerWidth, height: innerHeight })")
        run_frames(page, 18)  # 0.3 s of cloth time: time stood still until now, and the cloth is still at nothing
        capture(page)
        early = canvas_stats(page, whole)["std"]
        run_frames(page, 240)
        capture(page)
        full = canvas_stats(page, whole)["std"]
        # The paper under the cloth has a spread of its own (the grain), which is what "no cloth yet" reads as, and is a
        # good half of the settled, calmed cloth's. 0.3 s in, a fade of about a second stands at 0.60 (measured); 0.5 to 0.75 is
        # allowed, and a cloth that appears at once reads 1.0.
        if not 0.5 * full < early < 0.75 * full:
            problems.append(f"{label}: the cloth should fade in over about a second when it starts, not appear at once "
                            f"(spread {full:.3f} settled, {early:.3f} 0.3 s after the start)")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()

    label = "/about/ cloth, Motion switched"
    context, requests = cloth_context(browser, base_url, session_key, about)
    page, errors = open_page(context, base_url + about.path, warnings=True)
    if not cloth_running(page):
        context.close()
        return problems + [f"{label}: the cloth never started"]
    button = page.locator("footer [data-motion-toggle]")
    whole = page.evaluate("() => ({ x: 0, y: 0, width: innerWidth, height: innerHeight })")
    run_frames(page, 120)  # 2 s of cloth time: the cloth is all but full strength
    capture(page)
    full = canvas_stats(page, whole)["std"]
    button.click()
    run_frames(page, 18)  # 0.3 s into the fade: the cloth is still there, and weaker
    if not page.evaluate("() => Boolean(document.querySelector('canvas.veil'))"):
        problems.append(f"{label} off: the cloth was gone 0.3 s after the click; it should still be fading")
    else:
        capture(page)
        early = canvas_stats(page, whole)["std"]
        # Gone reads as about 0.4 of the settled spread (the paper's own); 0.3 s into a fade of about a second it
        # reads 0.67. A fade twice as fast reads 0.50, twice as slow 0.82.
        if not 0.55 * full < early < 0.8 * full:
            problems.append(f"{label} off: Motion off should fade the cloth out over about a second, not at once "
                            f"(spread {full:.3f} settled, {early:.3f} 0.3 s after the click)")
        inked = page.evaluate("() => document.querySelectorAll('.is-inked').length")
        if inked:  # FX, under the pointer, is washed while the cloth is on (the menu is shut); the wash goes with it
            problems.append(f"{label} off: the ink should not come back while the cloth fades out "
                            f"({inked} element(s) inked 0.3 s after the click)")
    run_frames(page, 200)  # 3.3 s of cloth time: from full strength the fade takes about 2.5 s, then the teardown
    state = page.evaluate(VEIL_STATE)
    if state["canvases"] or state["veil"] or (state["motion"], state["pressed"], state["saved"]) != ("off", "false", "off"):
        problems.append(f"{label} off: the cloth should have faded out and gone ({state})")
    page.wait_for_timeout(500)
    if page.evaluate(VEIL_STATE)["draws"] != state["draws"]:
        problems.append(f"{label} off: the cloth still draws after it has gone; Motion off means no GPU work")
    button.click()
    if not cloth_running(page):
        problems.append(f"{label} back on: the cloth did not come back")
    for _ in range(10):  # an even number: it ends on
        button.click()
    run_frames(page, 150)
    state = page.evaluate(VEIL_STATE)
    if (state["canvases"], state["veil"], state["motion"], state["pressed"], state["contexts"]) != (1, "on", "on", "true", 2):
        problems.append(f"{label} ten times: expected one running cloth, two contexts made in all and a button "
                        f"saying on ({state})")
    if len(requests) != 1:
        problems.append(f"{label}: veil.js was fetched {len(requests)} times; the module is loaded once")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()

    label = "/about/ cloth, Motion switched on from off"
    context, requests = cloth_context(browser, base_url, session_key, about, motion="off")
    page, errors = open_page(context, base_url + about.path, warnings=True)
    page.wait_for_timeout(1000)
    if requests:
        problems.append(f"{label}: veil.js was fetched before Motion was switched on")
    page.locator("footer [data-motion-toggle]").click()
    if not cloth_running(page) or len(requests) != 1:
        problems.append(f"{label}: switching Motion on should fetch veil.js once and start the cloth ({page.evaluate(VEIL_STATE)})")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()

    # Motion goes off while veil.js is still on its way: when it arrives, the cloth must not start.
    label = "/about/ cloth, Motion switched off while veil.js loads"
    held = []
    context, requests = cloth_context(browser, base_url, session_key, about)
    context.route(VEIL_SCRIPT, lambda route: held.append(route))  # the request waits, unanswered
    page, errors = open_page(context, base_url + about.path, warnings=True, until="load")
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(100)
    if not held:
        problems.append(f"{label}: veil.js was never requested")
    else:
        page.locator("footer [data-motion-toggle]").click()
        held[0].continue_()
        page.wait_for_timeout(1500)
        state = page.evaluate(VEIL_STATE)
        if state["canvases"] or state["veil"] or (state["motion"], state["pressed"], state["saved"]) != ("off", "false", "off"):
            problems.append(f"{label}: Motion was switched off before veil.js arrived, so the cloth should not start ({state})")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()
    return problems


def cloth_input_problems(browser, base_url, session_key):
    """The canvas never takes the pointer or the focus (spec 9): no point of the page hits it, the Tab
    key never lands on it, and the controls answer with the cloth behind them."""
    problems = []
    about = page_named("/about/")
    label = "/about/ cloth, input"
    context, _ = cloth_context(browser, base_url, session_key, about)
    page, errors = open_page(context, base_url + about.path, warnings=True)
    if not cloth_running(page):
        context.close()
        return [f"{label}: the cloth never started"]
    hits = page.evaluate("""() => { const canvas = document.querySelector("canvas.veil"), found = [];
      for (let i = 1; i < 8; i++) for (let j = 1; j < 6; j++) {
        const x = innerWidth * i / 8, y = innerHeight * j / 6;
        if (document.elementsFromPoint(x, y).includes(canvas)) found.push([Math.round(x), Math.round(y)]);
      }
      return found; }""")
    if hits:
        problems.append(f"{label}: the canvas is under the pointer at {hits}; it must take no pointer events")
    for _ in range(12):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement && document.activeElement.tagName") == "CANVAS":
            problems.append(f"{label}: the Tab key landed on the canvas")
            break
    theme = page.evaluate(ROOT_ATTRIBUTE, "data-theme")
    page.locator("footer [data-appearance-toggle]").click()
    if page.evaluate(ROOT_ATTRIBUTE, "data-theme") == theme:
        problems.append(f"{label}: the appearance switch does not answer with the cloth behind it")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()
    return problems


def cloth_appearance_problems(browser, base_url, session_key):
    """A switch of appearance fades the cloth across in about 1.5 seconds (spec 3.2), not at once; Auto
    follows the system as it changes; and every colour of the page fades with it (the tokens transition over
    --scene-fade), the labels as much as the ground, unless Motion is off."""
    problems = []
    about = page_named("/about/")
    label = "/about/ cloth, appearance"
    context, _ = cloth_context(browser, base_url, session_key, about)
    page, errors = open_page(context, base_url + about.path, warnings=True)
    if not cloth_running(page):
        context.close()
        return [f"{label}: the cloth never started"]
    whole = page.evaluate("() => ({ x: 0, y: 0, width: innerWidth, height: innerHeight })")

    def luma_after(frames):
        run_frames(page, frames)
        capture(page)
        return canvas_stats(page, whole)["luma"]

    paper = luma_after(60)
    cloth_screenshot(page, "cloth-1024-paper.png")
    page.locator("footer [data-appearance-toggle]").click()  # Paper to Ink
    steps = [luma_after(18) for _ in range(6)]  # 0.3 s each
    ink = steps[-1]
    cloth_screenshot(page, "cloth-1024-ink.png")
    if not (paper > 0.6 and ink < 0.3):
        problems.append(f"{label}: Paper to Ink should take the cloth from light (over 0.6) to dark (under 0.3); it went {paper:.2f} to {ink:.2f}")
    elif steps[0] < ink + 0.1 or any(later > earlier + 0.01 for earlier, later in zip(steps, steps[1:])):
        problems.append(f"{label}: Paper to Ink should fade steadily over about 1.5 s, not jump; luma every 0.3 s: {[round(s, 3) for s in steps]}")
    page.locator("footer [data-appearance-toggle]").click()  # Ink to Auto, with the system light
    page.emulate_media(color_scheme="dark")
    if luma_after(120) > 0.3:
        problems.append(f"{label}: Auto should follow the system to Ink")
    page.emulate_media(color_scheme="light")
    if luma_after(120) < 0.6:
        problems.append(f"{label}: Auto should follow the system back to Paper")
    fade = page.evaluate("() => { const style = getComputedStyle(document.documentElement); return [style.transitionProperty, style.transitionDuration]; }")
    properties, durations = (part.split(", ") for part in fade)
    if not {"--paper", "--ink", "--muted"} <= set(properties) or any(not name.startswith("--") for name in properties) \
            or set(durations) != {"1.4s"}:
        problems.append(f"{label}: the whole scene should fade over --scene-fade (1.4s), on the colour tokens and not on the page's ground "
                        f"and text alone; html transitions {properties} over {durations}")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()

    # The whole scene fades: a label has its own colour besides the page's ground, and neither may switch at once while
    # the cloth takes 1.5 s. Measured 0.4 s in, and at the end. With Motion off the switch is instant. (On a phone the
    # page's ground is all that shows at the top where the page fades out, so it too has to fade with the cloth.)
    read = """() => ({ label: getComputedStyle(document.querySelector('footer .shell-corner--bottom-right .label')).color,
                     ground: getComputedStyle(document.documentElement).backgroundColor })"""
    for motion in ("on", "off"):
        label = f"/about/ cloth on a phone, appearance, Motion {motion}"
        context, _ = cloth_context(browser, base_url, session_key, about, width=375, height=667, motion=motion)
        page, errors = open_page(context, base_url + about.path, warnings=True)
        if motion == "on" and not cloth_running(page):
            problems.append(f"{label}: the cloth never started")
            context.close()
            continue
        before = {name: colour_channels(value) for name, value in page.evaluate(read).items()}
        page.locator("footer [data-appearance-toggle]").click()  # Paper to Ink
        page.wait_for_timeout(400 if motion == "on" else 100)
        middle = {name: colour_channels(value) for name, value in page.evaluate(read).items()}
        page.wait_for_timeout(1500 if motion == "on" else 0)
        end = {name: colour_channels(value) for name, value in page.evaluate(read).items()}
        final = {"label": colour_channels(page.evaluate(TOKEN, "--muted")), "ground": colour_channels(page.evaluate(TOKEN, "--paper"))}
        for name, what in (("label", "a label's colour"), ("ground", "the page's ground")):
            if before[name] == final[name]:
                problems.append(f"{label}: {what} should differ between Paper and Ink for this check to mean anything ({before[name]})")
            elif motion == "on" and not all(min(a, b) < m < max(a, b) for a, m, b in zip(before[name], middle[name], final[name])):
                problems.append(f"{label}: {what} should be fading 0.4 s after the switch, between {before[name]} and {final[name]}; it is {middle[name]}")
            elif motion == "off" and middle[name] != final[name]:
                problems.append(f"{label}: with Motion off {what} should change at once to {final[name]}; it is {middle[name]}")
            if end[name] != final[name]:
                problems.append(f"{label}: {what} should have reached {final[name]} by the end of the fade; it is {end[name]}")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()
    return problems


def cloth_change_problems(browser, base_url, session_key):
    """An in-place chapter change and Come in keep the same canvas and the same WebGL context: the cloth
    continues uninterrupted (spec 4.5), and while the content cross-fades it goes on drawing."""
    problems = []
    for start, act, done, label in (
            ("/about/", lambda page: menu_link(page, "/plans/").click(),
             f"() => location.pathname === '/plans/' && ({CHANGE_OVER})()",
             "/about/ cloth, to Plans in place"),
            ("/", lambda page: page.locator("[data-come-in]").click(),
             "() => { const box = document.querySelector('[data-install]'); return Boolean(box) && !box.hidden; }",
             "/ cloth, Come in")):
        site_page = page_named(start)
        context, _ = cloth_context(browser, base_url, session_key, site_page, scripts=(CHANGE_PROBE,))
        page, errors = open_page(context, base_url + start, warnings=True)
        if not cloth_running(page):
            problems.append(f"{label}: the cloth never started")
            context.close()
            continue
        page.evaluate("() => { document.querySelector('canvas.veil').dataset.mark = 'kept'; }")
        before = page.evaluate(VEIL_STATE)
        act(page)
        page.wait_for_function(done, timeout=20000)  # the change has happened (the fades are CSS, on real time)
        page.wait_for_timeout(500)  # a moment for anything that went wrong to show
        for call in page.evaluate("() => window.__change.calls"):  # the cloth's draw calls, counted from the transition's start to its end
            if call["drawsFinished"] - call["drawsReady"] < 3:
                problems.append(f"{label}: the cloth drew {call['drawsFinished'] - call['drawsReady']} frame(s) while the content cross-faded; "
                                f"it should keep moving throughout")
        after = page.evaluate(VEIL_STATE)
        kept = page.evaluate("() => { const canvas = document.querySelector('canvas.veil'); return canvas && canvas.dataset.mark; }")
        if kept != "kept" or after["contexts"] != before["contexts"] or after["veil"] != "on" or after["canvases"] != 1:
            problems.append(f"{label}: the same canvas and context should carry on ({before} then {after}, mark {kept})")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()
    return problems


TOKEN = "(name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()"
BOX_RECT = """(selector) => {
  const element = document.querySelector(selector);
  if (!element || !element.getClientRects().length) return null;
  const b = element.getBoundingClientRect(), x = Math.max(0, b.left), y = Math.max(0, b.top);
  const width = Math.min(innerWidth, b.right) - x, height = Math.min(innerHeight, b.bottom) - y;
  return width > 4 && height > 4 ? { x, y, width, height } : null;
}"""


# The words of each match, as a rectangle: across, from the first letter to the last; down, the band the letters fill, from the top of
# the tallest to the bottom of the lowest (the font's own measure of the text as drawn, text-transform applied) and a pixel more of
# antialiasing each way. A range's rectangle is the font's content area, which for a 14px label is 18 px, and its capitals fill only
# the 10 in the middle: four pixels of empty line above and below them, where a neighbour's ink wash reaching up or down to the words
# does not touch a letter. The band sits on the baseline the content area gives (its top plus the font's ascent); when the font's
# ascent and descent do not add up to the content area's height (the face is not the one the page lays out with) or the words wrap
# onto more lines, the whole content area is measured, which is only stricter. words_band_problems proves the band against pixels.
WORD_BAND_PAD = 1   # CSS px of antialiasing above the tallest letter and below the lowest
WORD_RECTS = """([selector, pad]) => [...document.querySelectorAll(selector)].flatMap((element) => {
  const range = document.createRange(); range.selectNodeContents(element);
  const list = element.closest(".menu__list");   // the open menu scrolls inside its corner: only the words it shows. The phone's sheet is fixed to the screen, not in its corner: the screen
  const owner = list && getComputedStyle(list).position === "fixed" ? null : element.closest(".shell-corner");
  const words = range.getBoundingClientRect(), shown = owner ? owner.getBoundingClientRect() : { left: 0, top: 0, right: innerWidth, bottom: innerHeight };
  let top = words.top, bottom = words.bottom;
  const style = getComputedStyle(element), context = document.createElement("canvas").getContext("2d");
  context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  const text = (style.textTransform === "uppercase" ? element.textContent.toUpperCase() : element.textContent).trim();
  const metrics = context.measureText(text);
  if (Math.abs(metrics.fontBoundingBoxAscent + metrics.fontBoundingBoxDescent - words.height) <= 1) {
    const baseline = words.top + metrics.fontBoundingBoxAscent;
    top = baseline - metrics.actualBoundingBoxAscent - pad; bottom = baseline + metrics.actualBoundingBoxDescent + pad;
  }
  const x = Math.max(0, words.left, shown.left), y = Math.max(0, top, shown.top);
  const width = Math.min(innerWidth, words.right, shown.right) - x, height = Math.min(innerHeight, bottom, shown.bottom) - y;
  return width > 4 && height > 4 ? [{ x, y, width, height }] : [];
})"""


def colour_channels(colour):
    """(red, green, blue) of a colour written #rrggbb or rgb(r, g, b) / rgba(r, g, b, a): a token registered with
    @property comes back from getComputedStyle as rgb(), an unregistered one as it was written."""
    if colour.startswith("#"):
        return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return tuple(int(float(part)) for part in re.findall(r"[\d.]+", colour)[:3])


def relative_luminance(colour):
    """WCAG relative luminance of a colour (#rrggbb, or the rgb() a registered token is read as)."""
    channels = [value / 255 for value in colour_channels(colour)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def gamma_luma(colour):
    """The luma (0 to 1) of a colour the way the cloth's canvas is read back: the gamma-encoded channels, weighted."""
    red, green, blue = colour_channels(colour)
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255


def worst_contrast(text, stats):
    """The lowest contrast between text (a relative luminance) and any pixel the cloth drew under it."""
    return min((max(text, value) + 0.05) / (min(text, value) + 0.05) for value in (stats["min"], stats["max"]))


# The box of the wash held under an element (spec 3.4: an ellipse of radii 0.48 of its width plus 6 and 0.8 of its
# height plus 4 CSS px, here two fifths larger for its ragged rim and its spread), as a rectangle; null when
# nothing matches.
WASH_HOLE = """(selector) => { const element = document.querySelector(selector); if (!element) return null;
  const b = element.getBoundingClientRect(), rx = (b.width * 0.48 + 6) * 1.4, ry = (b.height * 0.8 + 4) * 1.4;
  return { x: b.left + b.width / 2 - rx, y: b.top + b.height / 2 - ry, width: rx * 2, height: ry * 2 }; }"""


def without(rect, hole):
    """The parts of rect (x, y, width, height) that lie outside hole: the bands above, below, left and right of it."""
    x0, y0, x1, y1 = rect["x"], rect["y"], rect["x"] + rect["width"], rect["y"] + rect["height"]
    hx0, hy0, hx1, hy1 = hole["x"], hole["y"], hole["x"] + hole["width"], hole["y"] + hole["height"]
    if hx0 >= x1 or hx1 <= x0 or hy0 >= y1 or hy1 <= y0:
        return [rect]
    top, bottom = max(y0, hy0), min(y1, hy1)
    parts = [(x0, y0, x1, hy0), (x0, hy1, x1, y1), (x0, top, hx0, bottom), (hx1, top, x1, bottom)]
    return [{"x": a, "y": b, "width": c - a, "height": d - b} for a, b, c, d in parts if c - a > 4 and d - b > 4]


def text_contrast_problems(page, label, selectors, token="--muted", floor=4.5, words=False, required=(), apart=()):
    """In the frame just captured, text in token's colour over each selector's box keeps floor:1 or better
    against every pixel the cloth drew there (spec 9). With words, every match is measured over its own
    words only (WORD_RECTS: the band its letters fill, not the line they sit in), and only where its corner
    shows it (the open menu scrolls inside its corner), so a neighbour's ink wash at a box's edge, or in the
    empty line above and below its capitals, is not counted. A box leaves out the wash held under each element of
    apart (the selected install tab, whose --ink words are measured by their own call, words=True): the
    cloth's calm is not what keeps those words legible, the wash is. A selector with nothing shown is skipped,
    unless it is in required: a check that measured nothing proves nothing, so that is a problem."""
    problems = []
    text = relative_luminance(page.evaluate(TOKEN, token))
    for selector in selectors:
        if words:
            rects = page.evaluate(WORD_RECTS, [selector, WORD_BAND_PAD])
        else:
            rect = page.evaluate(BOX_RECT, selector)
            rects = [rect] if rect else []
            for hole in filter(None, (page.evaluate(WASH_HOLE, other) for other in apart)):
                rects = [part for each in rects for part in without(each, hole)]
        if not rects:
            if selector in required:
                problems.append(f"{label}: nothing of {selector} is shown, so its contrast could not be measured")
            continue
        ratio = min(worst_contrast(text, canvas_stats(page, rect)) for rect in rects)
        if ratio < floor:
            problems.append(f"{label}: {token} text over {selector} could fall to {ratio:.2f}:1 over the cloth; "
                            f"it must stay at {floor}:1 or better (spec 9)")
    return problems


def words_band_problems(page, label, selector, shown=4):
    """WORD_RECTS measures words over the band their letters fill; this proves the band against the page's own pixels, so a band
    that slipped off the letters (a baseline a few pixels out, a face that is not the page's) or fell back to the whole line
    cannot quietly measure less, or something else, than the words. Each of the first `shown` matches is photographed (without
    the cloth) with six pixels of room above and below: the rows that hold a letter (a pixel 40 levels of 255 or more from its row's median, the
    ground, which the letters, 4.5:1 or better above it, are far over) lie inside the band, and the band is no more than 2.5 px
    taller than they are at either end (a pixel of antialiasing, WORD_BAND_PAD, and a little over: a band of the whole line
    is 4 px too tall)."""
    rects = page.evaluate(WORD_RECTS, [selector, WORD_BAND_PAD])[:shown]
    if not rects:
        return [f"{label}: nothing of {selector} is shown, so the band of its words could not be checked"]
    problems = []
    page.evaluate("() => { document.querySelector('canvas.veil').style.visibility = 'hidden'; }")  # a wash's rim at the line's edge is not a letter
    clips = [{"x": int(rect["x"]), "y": int(max(0, rect["y"] - 6)), "width": int(rect["width"]) + 1, "height": int(rect["height"]) + 14} for rect in rects]  # whole pixels
    pictures = [page.screenshot(clip=clip) for clip in clips]
    page.evaluate("() => { document.querySelector('canvas.veil').style.visibility = ''; }")
    for rect, clip, picture in zip(rects, clips, pictures):
        channels, rows = png_rows(picture)
        scale = len(rows) / clip["height"]
        lettered = []
        for index, row in enumerate(rows):
            lumas = [0.2126 * row[i] + 0.7152 * row[i + 1] + 0.0722 * row[i + 2] for i in range(0, len(row), channels)]
            ground = sorted(lumas)[len(lumas) // 2]
            if any(abs(value - ground) >= 40 for value in lumas):
                lettered.append(index)
        if not lettered:
            problems.append(f"{label}: no letters found in the words at y {rect['y']:.1f}, so the band of {selector} could not be checked")
            continue
        top, bottom = clip["y"] + lettered[0] / scale, clip["y"] + (lettered[-1] + 1) / scale
        band_top, band_bottom = rect["y"], rect["y"] + rect["height"]
        if band_top > top + 0.5 or band_bottom < bottom - 0.5:
            problems.append(f"{label}: the band measured for the words at y {rect['y']:.1f} ({band_top:.1f} to {band_bottom:.1f}) leaves letters out: "
                            f"they fill {top:.1f} to {bottom:.1f}")
        elif top - band_top > 2.5 or band_bottom - bottom > 2.5:
            problems.append(f"{label}: the band measured for the words at y {rect['y']:.1f} ({band_top:.1f} to {band_bottom:.1f}) is looser than "
                            f"the letters, which fill {top:.1f} to {bottom:.1f}: it is not the band of their ink")
    return problems


def whole_canvas_contrast_problems(browser, base_url, session_key):
    """Whatever the cloth draws, small text keeps its contrast (spec 3.1, 9): at 1280x800 and 375x667, in Paper and
    in Ink, at several moments far apart in cloth time, the worst contrast of --muted against the darkest and the
    lightest pixel of the whole canvas, grain included, is 4.5:1 or better, and --ink holds 7:1. Nothing is calmed
    for the text, so this is the cloth's own range (the calm and the floor in veil.js). The threshold is where it is
    measured: its menu is shut and nothing is under the pointer, so the canvas holds the cloth alone (an ink wash,
    like the open menu's current chapter, is darker or lighter on purpose and carries --ink words, which the other
    cases measure). Time is held still between moments (FREEZE, leap_frames): a frame moves cloth time by at most
    0.1 s, so the moments are a few seconds of it apart."""
    problems = []
    threshold = page_named("/")
    for width, height in ((1280, 800), (375, 667)):
        for theme in ("light", "dark"):
            label = f"/ cloth at {width}px, {theme}, the whole canvas"
            context, _ = cloth_context(browser, base_url, session_key, threshold, width=width, height=height, theme=theme, scripts=(FREEZE,))
            page, errors = open_page(context, base_url + threshold.path, warnings=True)
            if not cloth_running(page):
                problems.append(f"{label}: the cloth never started")
                context.close()
                continue
            page.mouse.move(4, 320)  # the left margin, where nothing answers the pointer
            whole = page.evaluate("() => ({ x: 0, y: 0, width: innerWidth, height: innerHeight })")
            muted, ink = (relative_luminance(page.evaluate(TOKEN, token)) for token in ("--muted", "--ink"))
            run_frames(page, 200)  # eased in
            worst = {"--muted": (99, 0), "--ink": (99, 0)}
            flattest = (9, 0)
            for moment in range(6):
                if moment:
                    leap_frames(page)
                capture(page)
                if page.evaluate(INKS_DRAWN):
                    problems.append(f"{label}: an ink wash is on the canvas, which would count in its range ({page.evaluate(INKS_DRAWN)})")
                stats = canvas_stats(page, whole)
                flattest = min(flattest, (stats["spread"], moment))
                for token, text in (("--muted", muted), ("--ink", ink)):
                    worst[token] = min(worst[token], (worst_contrast(text, stats), moment))
            for token, floor in (("--muted", 4.5), ("--ink", 7.0)):
                ratio, moment = worst[token]
                if ratio < floor:
                    problems.append(f"{label}: {token} text could fall to {ratio:.2f}:1 over the cloth anywhere on the canvas (moment {moment}); "
                                    f"it must stay at {floor}:1 or better (spec 9)")
            # The other side of the trade: a flat cloth has perfect contrast, so the folds must still show. The gamma luma's
            # spread over the whole canvas (film grain included, which is 0.0084 of it alone in Paper and 0.0061 in Ink) was 0.020 to
            # 0.041 in Paper and 0.016 to 0.033 in Ink at CALM 0.70, at both sizes, over 1,496 moments (the six here are among the
            # steadier: 0.025 and 0.020 the least); at CALM 0.35 it is 0.015 to 0.024 and 0.012 to 0.017 (twelve moments), and at 0.10
            # 0.009 to 0.011 and 0.007 to 0.008, the grain's. The floors sit a little under the least seen at 0.70, and a CALM of
            # 0.35 falls under them at 375 px in both appearances.
            spread_floor = 0.018 if theme == "light" else 0.014
            if flattest[0] < spread_floor:
                problems.append(f"{label}: the cloth's folds are too flat to read as cloth: the spread of its luma over the canvas fell to {flattest[0]:.4f} "
                                f"(moment {flattest[1]}) where {spread_floor} is the least the calmed cloth shows (CALM in veil.js, spec 3.1)")
            problems += [f"{label}: console: {error}" for error in errors]
            context.close()
    return problems


# Seeds of the held wash's rim that reach furthest at 1024x640 in Ink: the first three from a sweep of 12, the last two (0.1625 and
# 0.2375) from a sweep of 80 seeds 0.0125 apart, the only ones of them whose rim reaches into the next link's line (1.5 px of its four
# empty ones above the capitals, which the line's whole box counted as 3.76:1 and 3.93:1; WORD_RECTS measures the letters' band).
WASH_SEEDS = (0.125, 0.708, 0.875, 0.1625, 0.2375)


def contrast_problems(browser, base_url, session_key):
    """Text over the moving cloth keeps its contrast (spec 3.1, 9), whatever the cloth draws and wherever the text
    sits: nothing is cleared for it. The whole canvas first (whole_canvas_contrast_problems); then each place
    small text sits. A chapter's block, the corner labels and the open menu hold muted text at 4.5:1 or better
    over every pixel the shader draws there, in both appearances, at desktop and phone widths, at two moments,
    scrolled, after an in-place change and after Come in, and so does COME IN on the threshold; the threshold's
    line, display text, holds 3:1. The corner that grows into the open menu holds its words once the menu is open,
    and the bottom corners that wait at the end of the page on a phone hold theirs wherever the page is scrolled."""
    problems = whole_canvas_contrast_problems(browser, base_url, session_key)
    about, begin, threshold = page_named("/about/"), page_named("/begin/"), page_named("/")
    corners = [f".shell-corner--{corner}" for corner in CORNERS]
    # The open menu holds the current chapter's ink wash (spec 3.4), and the words on it are --ink, not --muted:
    # the top-right corner is measured by its words, MENU and the other links in --muted, the current chapter's in --ink.
    wide_labels = [corners[0], *corners[2:]]
    menu_muted, menu_current = "header [data-menu-toggle], #shell-menu a:not([aria-current])", "#shell-menu a[aria-current]"

    def opened(site_page, label, seed=None, **options):
        # seed fixes what the page's Math.random gives, which is the seed of each ink wash's ragged rim (veil.js)
        scripts = (FREEZE,) + ((f"Math.random = () => {seed};",) if seed is not None else ())
        context, _ = cloth_context(browser, base_url, session_key, site_page, scripts=scripts, **options)
        page, errors = open_page(context, base_url + site_page.path, warnings=True)
        if not cloth_running(page):
            problems.append(f"{label}: the cloth never started")
            context.close()
            return None, None, None
        return context, page, errors

    # Both appearances; wide (every corner, the menu opened with MENU) and a phone (every corner, the menu shut: MENU alone is the top right).
    for theme in ("light", "dark"):
        for width, height, labels, moments, menu in ((1024, 640, wide_labels, (200, 240), True), (375, 667, corners, (200,), False)):
            label = f"/about/ cloth at {width}px, {theme}"
            context, page, errors = opened(about, label, width=width, height=height, theme=theme)
            if not page:
                continue
            if menu:  # the menu starts closed; pressed, its current page's link holds an ink wash, which the words below are measured on
                set_menu(page, True)
                page.mouse.move(4, 320)  # the pointer was left on MENU, whose wash would darken its words (they are --ink then)
            # Wide, all four corners show (the top right as the open menu, measured by its words below); on a phone
            # the bottom corners wait at the end of the page, which may lie below the fold.
            required = (".chapter", *labels) if menu else (".chapter", *corners[:2])
            for count in moments:  # eased in, then four seconds of cloth later
                run_frames(page, count)
                capture(page)
                problems += text_contrast_problems(page, label, [".chapter", *labels], required=required)
                if menu:
                    problems += text_contrast_problems(page, label, [menu_muted], words=True, required=(menu_muted,))
                    problems += text_contrast_problems(page, label, [menu_current], token="--ink", words=True, required=(menu_current,))
            if menu:  # MENU and the first links, whose letters the band of WORD_RECTS must hold
                problems += words_band_problems(page, label, menu_muted)
            problems += [f"{label}: console: {error}" for error in errors]
            context.close()

    # The current page's link in the open menu holds an ink wash, whose ragged rim reaches further or less far by the seed it was
    # drawn with (a random one in a real visit): the words of MENU and of the links beside it keep their contrast for the seeds in
    # which it reaches furthest at the largest the menu is read at 1024 wide (14px labels). With MENU less than 1em above the column
    # these seeds lightened its words under 4.5:1 in Ink (3.5:1), which a random seed showed in about one run in four.
    for theme in ("light", "dark"):
        for seed in WASH_SEEDS:
            label = f"/about/ cloth at 1024px, {theme}, the held wash drawn with seed {seed}"
            context, page, errors = opened(about, label, seed=seed, width=1024, height=640, theme=theme)
            if not page:
                continue
            set_menu(page, True)  # the held wash is drawn when the menu opens, with this seed
            page.mouse.move(4, 320)  # the pointer was left on MENU, whose wash would darken its words (they are --ink then)
            for count in (200, 240):
                run_frames(page, count)
                capture(page)
                problems += text_contrast_problems(page, label, [menu_muted], words=True, required=(menu_muted,))
            if not page.evaluate(INKS_DRAWN):
                problems.append(f"{label}: no wash was drawn, so the seed was not tried")
            problems += [f"{label}: console: {error}" for error in errors]
            context.close()

    # The phone's open menu sheet is drawn by the cloth (paper, the film grain and the washes), and its links hold their contrast on it: MENU
    # and the other links in --muted and the current page's, on its wash, in --ink, at two moments and, for the wash seeds that reach furthest,
    # the way the column's are measured at 1024 wide (the labels are 13px here and the links 45px apart).
    for theme in ("light", "dark"):
        for seed in (None, *WASH_SEEDS):
            label = f"/about/ cloth at 375px, {theme}, the open menu sheet" + (f", the held wash drawn with seed {seed}" if seed is not None else "")
            context, page, errors = opened(about, label, seed=seed, width=375, height=667, theme=theme)
            if not page:
                continue
            set_menu(page, True)  # the held wash is drawn when the sheet opens, with this seed
            page.mouse.move(4, 320)  # the pointer was left on MENU, whose wash would darken its words (they are --ink then)
            for count in ((200, 240) if seed is None else (200,)):
                run_frames(page, count)
                capture(page)
                problems += text_contrast_problems(page, label, [menu_muted], words=True, required=(menu_muted,))
                problems += text_contrast_problems(page, label, [menu_current], token="--ink", words=True, required=(menu_current,))
            if seed is None:
                problems += words_band_problems(page, label, menu_muted)
            if not page.evaluate(INKS_DRAWN):
                problems.append(f"{label}: no wash was drawn on the sheet, so its words were not measured on one")
            problems += [f"{label}: console: {error}" for error in errors]
            context.close()

    # Scrolled: the block's words keep their contrast.
    label = "/begin/ cloth, scrolled"
    context, page, errors = opened(begin, label)
    if page:
        run_frames(page, 200)
        page.evaluate("() => window.scrollTo(0, 400)")
        run_frames(page, 30)
        capture(page)
        if page.evaluate("() => scrollY") <= 0:
            problems.append(f"{label}: the page did not scroll, so the block was not measured scrolled")
        problems += text_contrast_problems(page, label, [".chapter"], required=(".chapter",),
                                               apart=('[role="tab"][aria-selected="true"]',))  # Begin's install line, if it shows
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    # On a phone the bottom corners wait at the end of the page and scroll with it: their labels hold their contrast
    # at the end of the page, wherever the cloth is.
    label = "/begin/ cloth on a phone, scrolled to the end"
    context, page, errors = opened(begin, label, width=375, height=667)
    if page:
        run_frames(page, 200)
        page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
        run_frames(page, 3)
        capture(page)
        if page.evaluate("() => scrollY") <= 0:
            problems.append(f"{label}: the page did not scroll, so the corners were not measured scrolled")
        problems += text_contrast_problems(page, label, corners[2:], required=tuple(corners[2:]))
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    # After an in-place change the new chapter's words hold their contrast.
    label = "/about/ cloth, to Plans in place"
    context, page, errors = opened(about, label)
    if page:
        run_frames(page, 200)
        menu_link(page, "/plans/").click()
        page.wait_for_function("() => location.pathname === '/plans/'", timeout=20000)  # the swap has happened
        run_frames(page, 200)
        capture(page)
        problems += text_contrast_problems(page, label, [".chapter"], required=(".chapter",))
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    # The menu opened on the threshold, where it starts shut: once it is open its words hold their contrast.
    label = "/ cloth, the menu opened"
    context, page, errors = opened(threshold, label)
    if page:
        set_menu(page, True)
        page.mouse.move(4, 320)  # the pointer was left on MENU, whose wash would darken its words (they are --ink then)
        run_frames(page, 200)
        capture(page)
        problems += text_contrast_problems(page, label, [menu_muted], words=True, required=(menu_muted,))
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    # The threshold: its line holds 3:1 on the cloth and COME IN, a small label, 4.5:1; after Come in so does the install line.
    for theme in ("light", "dark"):
        label = f"/ cloth, {theme}"
        context, page, errors = opened(threshold, label, theme=theme)
        if not page:
            continue
        run_frames(page, 200)
        capture(page)
        problems += text_contrast_problems(page, label, ["[data-threshold-line]"], token="--ink", floor=3.0,
                                               required=("[data-threshold-line]",))
        problems += text_contrast_problems(page, label, ["[data-come-in]"], required=("[data-come-in]",))
        page.locator("[data-come-in]").click()
        page.wait_for_function("() => { const box = document.querySelector('[data-install]'); return Boolean(box) && !box.hidden; }",
                               timeout=20000)  # Come in's fade-out is a real-time timer
        run_frames(page, 200)
        capture(page)
        selected = '[data-install] [role="tab"][aria-selected="true"]'  # holds its wash: its --ink words are measured on their own
        problems += text_contrast_problems(page, f"{label}, after Come in", ["[data-install]"], required=("[data-install]",), apart=(selected,))
        problems += text_contrast_problems(page, f"{label}, after Come in", [selected], token="--ink", words=True, required=(selected,))
        # The wash left out of the box above is not left out for the other tabs: their --muted words sit beside it and are
        # measured on their own, over every pixel under them (5.1:1 at the least in Paper and 5.5:1 in Ink when this was
        # written, with the words a pixel clear of the hole), and the selector must match or the check says so.
        others = '[data-install] [role="tab"][aria-selected="false"]'
        problems += text_contrast_problems(page, f"{label}, after Come in, beside the selected tab's wash", [others], words=True, required=(others,))
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()
    return problems


CANVAS_BOX = """() => { const canvas = document.querySelector('canvas.veil'), b = canvas.getBoundingClientRect();
  return { top: b.top, height: b.height, pixels: [canvas.width, canvas.height], client: [canvas.clientWidth, canvas.clientHeight] }; }"""
INK_PATCH = """(selector) => { const b = document.querySelector(selector).getBoundingClientRect();
  const rx = b.width * 0.48 + 6, ry = b.height * 0.8 + 4;   // the wash's radii (spec 3.4)
  return { x: b.left + b.width / 2 - rx * 0.7, y: b.top + b.height / 2 - ry * 0.7, width: rx * 1.4, height: ry * 1.4 }; }"""
# The outer part of that wash on its right: a fresh wash (a drop that has only begun to spread) has not reached it.
INK_RING = """(selector) => { const b = document.querySelector(selector).getBoundingClientRect();
  const rx = b.width * 0.48 + 6, ry = b.height * 0.8 + 4, cx = b.left + b.width / 2, cy = b.top + b.height / 2;
  return { x: cx + rx * 0.5, y: cy - ry * 0.35, width: rx * 0.35, height: ry * 0.7 }; }"""
INKED = "(selector) => document.querySelector(selector).classList.contains('is-inked')"
OWN_FILL = "(selector) => getComputedStyle(document.querySelector(selector), '::before').backgroundColor"


def ink_frame_problems(browser, base_url, session_key):
    """The washes stay on their controls when the canvas's box is not the window (a phone's toolbar moves while
    pulling to refresh, and innerHeight with it, but the fixed canvas does not move): the canvas draws in its own
    CSS box and every wash is placed in that box's frame. Here the canvas is made 60 px shorter than the window,
    starting 60 px down (an injected rule), the pointer (a finger, at 375 px) is put on the npm tab after Come in,
    and the wash the cloth is asked to draw must lie on the tab where it appears on the screen, within 3 px; and
    the canvas's pixels must follow its own box (the cloth checks it each frame: nothing resized the window)."""
    problems = []
    npm = '[role="tab"][id$="-tab-npm"]'
    for width, height, touch, dsf in ((1280, 800, False, 1), (375, 667, True, 1), (375, 667, True, 3)):
        label = f"/ cloth at {width}px{f', pixel ratio {dsf}' if dsf != 1 else ''}, the wash under a tab with the canvas not the window's box"
        context, _ = cloth_context(browser, base_url, session_key, page_named("/"), width=width, height=height, scripts=(FREEZE,), touch=touch,
                                   device_scale_factor=dsf)
        page, errors = open_page(context, base_url + "/", warnings=True)
        if not cloth_running(page):
            problems.append(f"{label}: the cloth never started")
            context.close()
            continue
        page.locator("[data-come-in]").tap() if touch else page.locator("[data-come-in]").click()
        page.wait_for_function("() => { const box = document.querySelector('[data-install]'); return Boolean(box) && !box.hidden; }", timeout=20000)
        page.add_style_tag(content=".veil { top: 60px !important; height: calc(100% - 60px) !important; }")
        page.mouse.move(4, 320)
        run_frames(page, 30)
        page.locator(npm).tap() if touch else page.hover(npm)  # a tapped tab is the selected one: it holds its wash
        run_frames(page, 90)
        box = page.evaluate(CANVAS_BOX)
        if abs(box["top"] - 60) > 0.5 or abs(box["height"] - (height - 60)) > 1:
            problems.append(f"{label}: the check could not move the canvas's box (it is {box['height']:.0f} px tall at {box['top']:.0f}); it proves nothing")
        scale = first_step(width, dsf)
        expected = [round(box["client"][0] * scale), round(box["client"][1] * scale)]
        if any(abs(have - want) > 1 for have, want in zip(box["pixels"], expected)):
            problems.append(f"{label}: the canvas has {box['pixels'][0]}x{box['pixels'][1]} pixels in a box {box['client'][0]}x{box['client'][1]} CSS px; "
                            f"it should draw {expected[0]}x{expected[1]} ({scale} of its own box, not of the window)")
        centre = page.evaluate("(selector) => { const b = document.querySelector(selector).getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; }", npm)
        washes = page.evaluate(INKS_ON_SCREEN)
        near = [wash for wash in washes if abs(wash["x"] - centre["x"]) <= 3 and abs(wash["y"] - centre["y"]) <= 3]
        if not near:
            where = ", ".join(f"({wash['x']:.0f}, {wash['y']:.0f})" for wash in washes) or "none"
            problems.append(f"{label}: the wash should be centred on the npm tab at ({centre['x']:.0f}, {centre['y']:.0f}), but the cloth draws "
                            f"washes at {where} on the screen")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()
    return problems


# The age (seconds of cloth time) of the youngest wash drawn under an element in each frame from the one given on:
# [frame, age] pairs, age null where no wash was drawn there. A wash that was let go and begun again is young.
WASH_AGES = "([selector, since]) => { " + WASH_FRAME + """
  const b = document.querySelector(selector).getBoundingClientRect(), cx = b.left + b.width / 2, cy = b.top + b.height / 2;
  return window.__cloth.history.filter((entry) => entry.frame >= since).map(({ frame: number, p, q, w, h }) => {
    let youngest = null;
    const drawn = frame.at(w, h);
    for (let i = 0; i < p.length / 4; i++) {
      if (q[i * 4 + 1] > 0 && Math.abs(drawn.x(p[i * 4]) - cx) < 2 && Math.abs(drawn.y(p[i * 4 + 1]) - cy) < 2) {
        youngest = youngest === null ? q[i * 4 + 2] : Math.min(youngest, q[i * 4 + 2]); } }
    return [number, youngest]; }); }"""


# A layout flushed in the task that begins a change: a face arriving as the change starts (shell.js loads the layout's faces at the start of every change, and the
# first change from a page that has not loaded them, About's for Cormorant italic 400, lays out when they come), a late image, a resize. Chromium reads the pointer
# again where it rests some 11 ms after a layout (a hover update of its own), and if the view transition has begun by then it finds the transition's own picture under
# the pointer: from the old page being taken to the animation's first frame the pseudo-elements take pointer events whatever the page says (pointer-events reads
# "auto" on them there, with `!important` too). The pointer is taken from the control (pointerout, to the document element) and given back (pointerover) at the
# first frame. A late frame lets it happen with no layout of ours (frames held 33 ms: 21 of 30 changes from About, none when its faces were loaded first); this
# makes it certain, at the speed of any machine.
LAYOUT_AT_CHANGE = """(() => { const start = Document.prototype.startViewTransition; if (!start) return;
  Document.prototype.startViewTransition = function (callback) { const main = document.querySelector("main");
    main.style.paddingBottom = `${parseFloat(main.style.paddingBottom || 0) + 1}px`; main.getBoundingClientRect(); return start.call(this, callback); }; })();"""
# How many times the pointer was taken from the appearance switch to the document element (the transition's picture, hit as the document element).
TAKEN_PROBE = """(() => { window.__taken = 0; document.addEventListener("pointerout", (event) => {
  if (event.target.closest && event.target.closest("[data-appearance-toggle]") && event.relatedTarget === document.documentElement) window.__taken += 1; }, true); })();"""


def ink_change_problems(browser, base_url, session_key):
    """A control the pointer rests on keeps its wash, as it is, while the content cross-fades: the page underneath stays
    hittable, so the pointer is not taken from the control and given back (which let the wash go and began a new one,
    growing from nothing). The pointer rests on the appearance switch of /about/ for 2.5 s of cloth time, Plans is chosen
    by a click made in the page (the pointer does not move) with the cross-fade slowed to 2 s, and in every frame the
    cloth drew from the swap on the wash under the switch must be the old one (older than it was) and the switch must
    still carry is-inked and no flat fill. Then twice more with a layout flushed in the task that begins the change
    (LAYOUT_AT_CHANGE: Chromium takes the pointer from the page and gives it back around the pause of the transition, which
    no stylesheet can stop, and the wash has to survive it; a change from About begins with a face arriving, and a late frame
    lets it happen unasked): the pointer given back at once (the wash carries on, the same one), and the pointer gone while
    the update callback is held (the wash is let go and fades, nothing brings it back)."""
    problems = []
    for case in ("rest", "layout", "away"):
        problems += ink_change_findings(browser, base_url, session_key, case)
    return problems


def ink_change_findings(browser, base_url, session_key, case):
    """One run of ink_change_problems: "rest" (nothing else happens), "layout" (a layout is flushed as the change begins, so
    Chromium takes the pointer and gives it back; a note if it did not) and "away" (the same, and the pointer moves off the
    switch while the update callback is held, so it is not given back)."""
    problems = []
    switch = "footer [data-appearance-toggle]"
    label = "/about/ cloth, the wash on a control the pointer rests on, through a chapter change"
    scripts = (FREEZE, CHANGE_PROBE) + {"rest": (), "layout": (LAYOUT_AT_CHANGE, TAKEN_PROBE), "away": (LAYOUT_AT_CHANGE, TAKEN_PROBE, LATE_SWAP)}[case]
    label += {"rest": "", "layout": ", with a layout in the task that begins it", "away": ", the pointer gone before it is given back"}[case]
    context, _ = cloth_context(browser, base_url, session_key, page_named("/about/"), scripts=scripts)
    page, errors = open_page(context, base_url + "/about/", warnings=True)
    if not cloth_running(page):
        problems.append(f"{label}: the cloth never started")
        context.close()
        return problems
    page.add_style_tag(content=SLOW_CHANGE)
    page.mouse.move(4, 320)
    run_frames(page, 60)
    page.hover(switch)
    run_frames(page, 150)
    page.evaluate("() => document.querySelector('#shell-menu a[href=\"/plans/\"]').click()")
    if case == "away":
        # The callback is held 600 ms (LATE_SWAP): the pointer is taken a few ms after the change begins, and moves off while the page is paused. Polled by a
        # timer, not by frames: the page draws none while it is paused.
        page.wait_for_function("() => window.__taken > 0 || window.__change.calls.some((call) => call.updated !== null)", timeout=20000, polling=20)
        page.mouse.move(4, 320)
    page.wait_for_function("() => window.__change.calls.length > 0 && window.__change.calls[0].ready !== null", timeout=20000)
    run_frames(page, 200)  # 3.3 s: the whole cross-fade, in frames of the cloth's own time
    ready = page.evaluate("() => window.__change.calls[0].finished !== null && location.pathname === '/plans/'")
    if case != "rest" and not page.evaluate("() => window.__taken"):
        REVIEW_NOTES.append(f"nothing to measure: {label}: this Chromium did not take the pointer from the switch when a layout was flushed as the change began, so "
                            f"the wash's survival of it was not looked at (it is, in the chapter changes of Chromium that do)")
    if case == "away":
        if not ready or page.evaluate(INKED, switch) or wash_strength(page, switch) > 0:
            problems.append(f"{label}: the pointer left the switch while the change was held, so its wash should have been let go and gone 3.3 s later "
                            f"(is-inked {page.evaluate(INKED, switch)}, strength {wash_strength(page, switch):.2f}, change finished {ready})")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()
        return problems
    since = page.evaluate("() => window.__change.swappedFrame")
    ages = page.evaluate(WASH_AGES, [switch, since])
    if not ready or not ages:
        problems.append(f"{label}: the change did not finish ({ready}), or the cloth drew no frames in it ({len(ages)})")
    else:
        young = [age for _, age in ages if age is not None and age < 2.0]
        missing = [frame for frame, age in ages if age is None]
        if young:
            problems.append(f"{label}: the wash under the switch was begun again during the change (a wash {min(young):.2f} s old, where the one the pointer "
                            f"rested on was 2.5 s old); the pointer should not have been taken from the switch")
        if missing:
            problems.append(f"{label}: no wash was drawn under the switch in {len(missing)} of {len(ages)} frames of the change")
        if not page.evaluate(INKED, switch) or page.evaluate(OWN_FILL, switch) != "rgba(0, 0, 0, 0)":
            problems.append(f"{label}: after the change the switch should still carry is-inked and no flat fill "
                            f"(is-inked {page.evaluate(INKED, switch)}, fill {page.evaluate(OWN_FILL, switch)})")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()
    return problems


# The first line box of an inline element (a link that wraps has one for each line), as getBoundingClientRect's union is not one.
FIRST_LINE = """(selector) => { const rect = document.querySelector(selector).getClientRects()[0];
  return rect ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height } : null; }"""


def ink_phone_problems(browser, base_url, session_key):
    """The ink shows on phones too (spec 3.4): a tap on a control grows a wash under it (the finger's pointerover) that
    fades when it lifts (pointerout), and the selected install tab holds its wash; no flat fill takes the wash's place
    once it has faded (a phone keeps the :hover of the last thing touched, so the hover fill must not apply where
    nothing can hover). The open menu sheet is inked like the rest, because the cloth draws it (cloth_sheet_ink_problems has its
    washes; here a press on a link in it, which is where a finger's wash begins). Measured at 375x667, where the layout is a phone's."""
    problems = []
    threshold, about = page_named("/"), page_named("/about/")
    npm, pip = '[role="tab"][id$="-tab-npm"]', '[role="tab"][id$="-tab-pip"]'
    flat = lambda page, selector: page.evaluate(OWN_FILL, selector) != "rgba(0, 0, 0, 0)"

    label = "/ cloth on a phone, touch, the install tabs"
    context, _ = cloth_context(browser, base_url, session_key, threshold, width=375, height=667, scripts=(FREEZE,), touch=True)
    page, errors = open_page(context, base_url + "/", warnings=True)
    if not cloth_running(page):
        problems.append(f"{label}: the cloth never started")
    else:
        run_frames(page, 30)
        page.locator("[data-come-in]").tap()
        page.wait_for_function("() => { const box = document.querySelector('[data-install]'); return Boolean(box) && !box.hidden; }", timeout=20000)
        run_frames(page, 30)
        page.locator(npm).tap()
        run_frames(page, 10)
        if wash_strength(page, npm) <= 0 or not page.evaluate(INKED, npm):
            problems.append(f"{label}: tapping the npm tab should grow a wash under it (strength {wash_strength(page, npm):.2f}, "
                            f"is-inked {page.evaluate(INKED, npm)})")
        run_frames(page, 150)
        held = wash_strength(page, npm)
        if held < 0.99 or not page.evaluate(INKED, npm) or flat(page, npm):
            problems.append(f"{label}: the selected npm tab should hold its wash 2.5 s after the tap, with no flat fill "
                            f"(strength {held:.2f}, is-inked {page.evaluate(INKED, npm)}, flat fill {flat(page, npm)})")
        page.locator(pip).tap()
        run_frames(page, 150)
        if wash_strength(page, pip) < 0.99 or not page.evaluate(INKED, pip) or page.evaluate(INKED, npm) or wash_strength(page, npm) > 0 \
                or flat(page, pip) or flat(page, npm):
            problems.append(f"{label}: after pip is tapped its wash should be held (strength {wash_strength(page, pip):.2f}) and npm's gone "
                            f"(strength {wash_strength(page, npm):.2f}, is-inked {page.evaluate(INKED, npm)}); neither should wear a flat fill "
                            f"(pip {flat(page, pip)}, npm {flat(page, npm)})")
        # Something that is not selected: the wash blooms under the tap and fades once the finger has lifted.
        switch = "footer [data-appearance-toggle]"
        page.locator(switch).tap()
        run_frames(page, 10)
        if wash_strength(page, switch) <= 0:  # it has begun to fade by now (the finger lifted), so it is not is-inked any more
            problems.append(f"{label}: tapping the appearance switch should grow a wash under it (strength {wash_strength(page, switch):.2f})")
        run_frames(page, 150)
        can_hover = page.evaluate("() => matchMedia('(hover: hover)').matches")
        if wash_strength(page, switch) > 0 or page.evaluate(INKED, switch) or flat(page, switch):
            problems.append(f"{label}: 2.5 s after the tap the switch's wash should have faded and no flat fill should have taken its place "
                            f"(strength {wash_strength(page, switch):.2f}, is-inked {page.evaluate(INKED, switch)}, flat fill {flat(page, switch)}; "
                            f"(hover: hover) is {can_hover})")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()

    # A tablet in landscape (64rem and up, where the page runs between the corners) with a touchscreen: a tap blooms a wash and
    # lets it fade as the finger lifts, and the :hover a touchscreen keeps for the last thing it touched must not put the flat
    # fill back where the wash was (the rectangle the cloth's wash replaces); the hover fills answer to (pointer: coarse).
    label = "/about/ cloth on a tablet at 1024x768, touch, a tap on the appearance switch"
    context, _ = cloth_context(browser, base_url, session_key, about, width=1024, height=768, scripts=(FREEZE,), touch=True)
    page, errors = open_page(context, base_url + about.path, warnings=True)
    if not cloth_running(page):
        problems.append(f"{label}: the cloth never started")
    else:
        run_frames(page, 30)
        switch = "footer [data-appearance-toggle]"
        page.locator(switch).tap()
        run_frames(page, 10)
        if wash_strength(page, switch) <= 0:
            problems.append(f"{label}: the tap should grow a wash under the switch (strength {wash_strength(page, switch):.2f})")
        run_frames(page, 150)
        hovered = page.evaluate('(selector) => document.querySelector(selector).matches(":hover")', switch)
        if wash_strength(page, switch) > 0 or page.evaluate(INKED, switch) or flat(page, switch):
            problems.append(f"{label}: 2.5 s after the tap the wash should have faded and no flat fill taken its place (strength "
                            f"{wash_strength(page, switch):.2f}, is-inked {page.evaluate(INKED, switch)}, flat fill {flat(page, switch)}, "
                            f"still :hover {hovered})")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()

    # The open sheet on a touchscreen, which cannot hover: a press on a link is the pointer arriving, and the cloth, which draws the sheet,
    # blooms a wash under it, with no flat fill in its place (the flat fill is the answer where the cloth does not run: Motion off, the
    # still page). The press is made with the mouse, which sets :active the same way; the media that decide (pointer: coarse) are those of a
    # touch context.
    label = "/about/ cloth on a phone, touch, a press on a link in the open menu sheet"
    context, _ = cloth_context(browser, base_url, session_key, about, width=375, height=667, scripts=(FREEZE,), touch=True)
    page, errors = open_page(context, base_url + about.path, warnings=True)
    if not cloth_running(page):
        problems.append(f"{label}: the cloth never started")
    else:
        run_frames(page, 30)
        page.locator("header [data-menu-toggle]").tap()
        link = '#shell-menu a[href="/plans/"]'
        wait_sheet_in_place(page)   # the sheet has come in: a link has a box to wash
        box = page.evaluate(BOX_RECT, link)
        if not page.locator("#shell-menu").is_visible() or not box or not page.evaluate("() => matchMedia('(pointer: coarse)').matches"):
            problems.append(f"{label}: the sheet is not open, the link has no box or the pointer is not coarse, so nothing was checked")
        else:
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.down()
            run_frames(page, 20)
            if wash_strength(page, link) <= 0 or not page.evaluate(INKED, link) or flat(page, link):
                problems.append(f"{label}: a link pressed in the sheet should grow a wash under it and wear no flat fill (strength {wash_strength(page, link):.2f}, "
                                f"is-inked {page.evaluate(INKED, link)}, flat fill {flat(page, link)})")
            page.mouse.move(4, 600)  # let go elsewhere: a release on the link would be a click, which makes it the current chapter
            page.mouse.up()
            run_frames(page, 150)
            if flat(page, link) or page.evaluate(INKED, link) or wash_strength(page, link) > 0:
                problems.append(f"{label}: once the press has ended the wash should have faded and no flat fill taken its place (strength {wash_strength(page, link):.2f}, "
                                f"is-inked {page.evaluate(INKED, link)}, flat fill {flat(page, link)})")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()

    # A prose link on a touchscreen, pressed: the dotted underline gives way and the flat fill answers, as the pointer does
    # elsewhere (the cloth is not running, so no wash takes the fill's place).
    label = "/begin/ on a phone, touch, a press on a prose link"
    context = new_context(browser, base_url, session_key, page_named("/begin/"), width=375, height=667, theme=None, motion=None, touch=True)
    page, errors = open_page(context, base_url + "/begin/")
    link = 'main .chapter p a[href="/app/"]'
    # A link that wraps has a union box that is wider and taller than either of its lines, and whose middle may fall between them:
    # the press is inside its first line (getClientRects), the one place that is surely the link.
    page.evaluate("(selector) => { document.querySelector(selector).textContent = 'the dashboard, where every file you have shared can be found, extended or revoked'; }", link)
    page.locator(link).scroll_into_view_if_needed()
    lines = page.evaluate("(selector) => document.querySelector(selector).getClientRects().length", link)
    box = page.evaluate(FIRST_LINE, link)
    if not box:
        problems.append(f"{label}: the link has no box, so nothing was checked")
    elif lines < 2:
        problems.append(f"{label}: the link did not wrap, so the press was not tried on a link of more than one line")
    else:
        page.mouse.move(box["x"] + min(4, box["width"] / 2), box["y"] + box["height"] / 2)
        page.mouse.down()
        page.wait_for_timeout(300)
        pressed = page.evaluate("(selector) => { const style = getComputedStyle(document.querySelector(selector)); return [style.backgroundColor, style.textDecorationColor]; }", link)
        if pressed[0] == "rgba(0, 0, 0, 0)" or pressed[1] != "rgba(0, 0, 0, 0)":
            problems.append(f"{label}: a pressed prose link should take the flat fill and lose its dotted underline (fill {pressed[0]}, underline {pressed[1]})")
        page.mouse.move(4, 600)  # let go elsewhere, not on the link
        page.mouse.up()
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    return problems


COUNT_INKED = "() => document.querySelectorAll('.is-inked').length"
# Selected tabs added to the page (tabs hold their wash while they are selected, spec 3.4): crowd-tab-0, crowd-tab-1, ...
CROWD_TABS = """(count) => { for (let i = 0; i < count; i++) { const tab = document.createElement('button'); tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-selected', 'true'); tab.className = 'label interactive'; tab.id = 'crowd-tab-' + i; tab.textContent = 'Tab ' + i;
    document.querySelector('main .chapter').append(tab); } }"""


def ink_crowd_problems(browser, base_url, session_key):
    """The cloth draws four washes at most (MAX_INKS). Four held at once (the current chapter in the open menu and three
    selected tabs, which only a page with that many could show: tabs are put in /about/ here) leave no room for a fifth:
    a control the pointer comes to then keeps its flat fill, it must not be marked as washed (is-inked takes the fill away)
    with no wash drawn under it. And when one of the four lets go, its wash fades for 1.8 s and must give up its place
    at once, not hold it: the next control the pointer comes to gets a wash."""
    problems = []
    about = page_named("/about/")
    label = "/about/ cloth, four held washes and a fifth control"
    switch = "footer [data-appearance-toggle]"
    context, _ = cloth_context(browser, base_url, session_key, about)
    page, errors = open_page(context, base_url + about.path, warnings=True)
    if not cloth_running(page):
        problems.append(f"{label}: the cloth never started")
        context.close()
        return problems
    hold_menu(page)
    page.evaluate(CROWD_TABS, 3)
    run_frames(page, 60)
    held = page.evaluate("() => document.querySelectorAll('.is-inked').length")
    if held != 4:
        problems.append(f"{label}: {held} elements carry a wash; the menu's current chapter and three tabs should make four")
    page.hover(switch)
    run_frames(page, 30)
    washed, inked, flat = wash_strength(page, switch) > 0, page.evaluate(INKED, switch), page.evaluate(OWN_FILL, switch) != "rgba(0, 0, 0, 0)"
    if inked and not washed:
        problems.append(f"{label}: the fifth control is marked as washed (is-inked, so no flat fill) but has no wash drawn under it; it shows neither")
    elif not inked and not flat:
        problems.append(f"{label}: the fifth control has no wash, as there is no room, and no flat fill either")
    # One of the four lets go; its wash fades but the place is free for the next control the pointer comes to.
    page.mouse.move(4, 320)
    run_frames(page, 10)
    page.evaluate("() => document.getElementById('crowd-tab-0').setAttribute('aria-selected', 'false')")
    run_frames(page, 10)
    page.hover(switch)
    run_frames(page, 30)
    if not (wash_strength(page, switch) > 0 and page.evaluate(INKED, switch)):
        problems.append(f"{label}: a tab let go of its wash 0.5 s ago, and the control the pointer came to still has none: the wash that is "
                        f"fading holds its place for its whole 1.8 s instead of giving it up")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()
    return problems


def ink_places_problems(browser, base_url, session_key):
    """Four washes at most (MAX_INKS), and what happens to the washes that do not fit or that give up their place.
    (1) A selected tab that finds all four places held (the menu's current chapter and three tabs) is refused its wash, and
    must not be counted as holding one: it is tried again, and when one of the four lets go it gets that place.
    (2) A control the pointer left 0.5 s ago has a wash that is fading, and the pointer coming back gives it a second, live
    one; when a fifth control then needs a place the fading wash goes, and its element, which has the live one, must keep
    its mark (is-inked, which takes the flat fill away): without it the control wears the wash and the flat fill together."""
    problems = []
    about = page_named("/about/")
    switch, motion = "footer [data-appearance-toggle]", "footer [data-motion-toggle]"

    def opened(label):
        context, _ = cloth_context(browser, base_url, session_key, about)
        page, errors = open_page(context, base_url + about.path, warnings=True)
        if not cloth_running(page):
            problems.append(f"{label}: the cloth never started")
            context.close()
            return None, None, None
        return context, page, errors

    label = "/about/ cloth, a fifth selected tab refused its place"
    context, page, errors = opened(label)
    if page:
        hold_menu(page)   # the menu's current chapter takes the first of the four places
        page.evaluate(CROWD_TABS, 4)
        run_frames(page, 60)
        fifth = "#crowd-tab-3"
        if wash_strength(page, fifth) > 0 or page.evaluate(INKED, fifth):
            problems.append(f"{label}: the fifth selected tab should be refused a wash while the menu's current chapter and three tabs hold the four "
                            f"(strength {wash_strength(page, fifth):.2f}, is-inked {page.evaluate(INKED, fifth)}), so nothing was checked")
        page.evaluate("() => document.getElementById('crowd-tab-0').setAttribute('aria-selected', 'false')")
        run_frames(page, 10)
        if wash_strength(page, fifth) < 0.1 or not page.evaluate(INKED, fifth):
            problems.append(f"{label}: a tab let go of its place, and the selected tab that was refused one still has no wash "
                            f"(strength {wash_strength(page, fifth):.2f}, is-inked {page.evaluate(INKED, fifth)}); it should be tried again")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    label = "/about/ cloth, a control the pointer left and came back to, then a fifth control"
    context, page, errors = opened(label)
    if page:
        hold_menu(page)
        page.evaluate(CROWD_TABS, 1)   # with the menu's current chapter: two held
        page.mouse.move(4, 320)
        run_frames(page, 30)
        page.hover(switch)
        run_frames(page, 30)
        page.mouse.move(4, 320)
        run_frames(page, 30)   # its wash has been fading for 0.5 s
        page.hover(switch)
        run_frames(page, 10)   # and is fading still: the held two, the fading one and the live one make four
        marked = page.evaluate(COUNT_INKED)
        if marked != 3 or not page.evaluate(INKED, switch):
            problems.append(f"{label}: the menu's current chapter, the selected tab and the hovered control should each carry a wash, so nothing was checked "
                            f"({marked} do)")
        page.focus(motion)   # a fifth wash is wanted: the fading one gives up its place
        run_frames(page, 10)
        if not page.evaluate(INKED, motion):
            problems.append(f"{label}: the control that took focus should have the fading wash's place, so nothing was checked")
        if wash_strength(page, switch) < 0.5 or not page.evaluate(INKED, switch):
            problems.append(f"{label}: the pointer is on the control and its wash is live (strength {wash_strength(page, switch):.2f}), but it has lost its mark "
                            f"(is-inked {page.evaluate(INKED, switch)}) when its fading wash gave up the place, so the flat fill shows under the wash")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()
    return problems


# How rough the edge of the install tab's wash is in the canvas's own pixels: the roughness is how far the wash alone (a frame with
# the wash minus the same frame without it, so the film grain, the folds and the paper cancel; the probe's noInk) differs from its
# own average at the same distance from the ellipse's edge, in the band where the edge lies, as a share of the wash's peak.
# A smooth round edge differs from its average by little; pixel-sized noise on it (granulation, ragged outline) differs a lot. The
# ellipse is the one the cloth was asked to draw (the tab's box, as veil.js sizes it).
WASH_ROUGHNESS = """async (selector) => {
  const cloth = window.__cloth;
  const read = async () => { if (!(await cloth.capture())) return null; const { w, h, px } = cloth.last; return { w, h, px: px.slice() }; };
  const withWash = await read(); cloth.noInk = true; const without = await read(); cloth.noInk = false;
  if (!withWash || !without || withWash.w !== without.w || withWash.h !== without.h) return null;
  const { w, h } = withWash, canvas = document.querySelector('canvas.veil'), frame = canvas.getBoundingClientRect(), sx = w / frame.width, sy = h / frame.height;
  const box = document.querySelector(selector).getBoundingClientRect();
  const cx = (box.left + box.width / 2 - frame.left) * sx, cy = h - (box.top + box.height / 2 - frame.top) * sy;
  const rx = (box.width * 0.48 + 6) * sx, ry = (box.height * 0.8 + 4) * sy, R = (rx + ry) / 2;
  const luma = (px, k) => (0.2126 * px[k] + 0.7152 * px[k + 1] + 0.0722 * px[k + 2]) / 255;
  const sums = {}, counts = {}, pixels = [];
  for (let j = Math.max(0, Math.floor(cy - 1.5 * ry)); j <= Math.min(h - 1, Math.ceil(cy + 1.5 * ry)); j++) {
    for (let i = Math.max(0, Math.floor(cx - 1.5 * rx)); i <= Math.min(w - 1, Math.ceil(cx + 1.5 * rx)); i++) {
      const rho = Math.hypot((i + 0.5 - cx) / rx, (j + 0.5 - cy) / ry), bin = Math.round((rho - 1) * R * 2), k = (j * w + i) * 4;
      const wash = Math.abs(luma(withWash.px, k) - luma(without.px, k));
      sums[bin] = (sums[bin] || 0) + wash; counts[bin] = (counts[bin] || 0) + 1;
      if (rho >= 0.5 && rho <= 1.4) pixels.push([bin, wash]);
    } }
  const peak = Math.max(...Object.keys(sums).map((bin) => sums[bin] / counts[bin]));
  const spread = Math.sqrt(pixels.reduce((total, [bin, wash]) => total + (wash - sums[bin] / counts[bin]) ** 2, 0) / pixels.length);
  return { peak, roughness: spread / peak, canvas: w };
}"""


# What the shader does with u_soft, measured with the pinned seed (Math.random 0.37): the roughness (above) of the wash as the cloth draws it
# over the roughness of the same wash with u_soft forced to 1 (no widening), at each step of a phone's ladder, Paper then Ink:
#   pixel ratio 1    0.65  1.000      0.49  0.73 | 0.74     0.37  0.74 | 0.74     0.28  0.59 | 0.56
#   pixel ratio 3    1.20  1.000      0.90  0.82 | 0.83     0.68  0.60 | 0.61     0.51  0.58 | 0.55     0.38  0.28 | 0.30     0.28  0.23 | 0.26
# and 1.000 at every step when nothing is widened (the grain is a fixed number of canvas pixels whatever the scale). The first step is
# exactly 1.000 because the cloth asks for u_soft 1 there, so the pixels cannot tell a wrong u_soft there from a right one by more than
# the wash's own sensitivity to it (a k of 1.23, a phone at 0.65 with the rule `0.8 / scale`, gives 0.81; of 0.81 gives 0.96 to 0.99; of 0.67,
# a phone at 1.2 with the same rule, 1.08). That is too little to hold a limit with a margin on both sides, and the cross-device ratio
# the family used before (a phone's wash over a desktop's at 0.8: 1.02 to 1.09 when right, as the tab is a different share of a
# different canvas, 0.88 at a k of 1.23) had about 0.10 each side. So the first step is held where it is decided, in the number the
# cloth hands the shader: u_soft is 1 there, within SOFT_EDGE_ONE, and the wrong rules above give 1.23, 0.81, 0.67, 0.54, 1.5 and 1.85 (0.19
# or more off). The pixels hold the other side, that the shader does soften what it is told to: at the last step, under SOFT_EDGE_BELOW of
# the plain wash (0.56 to 0.59 on the shorter ladder, 0.23 to 0.26 on the longer: 0.21 under the limit at the least; 1.0 when the shader
# ignores u_soft: 0.20 over it). The number itself is held there too: the first step over the last, within SOFT_EDGE_ONE of it.
SOFT_EDGE_ONE = 0.02     # the least u_soft is allowed to be off by, a share of what it should be
SOFT_EDGE_BELOW = 0.8    # at the last step the wash drawn is under this share of the roughness of the same wash with no widening


def wash_roughness(browser, base_url, session_key, label, *, width, height, theme, want, dsf=1, ms=None, down=0, problems):
    """WASH_ROUGHNESS of the wash held under the threshold's selected install tab, with the cloth at `want` of the CSS resolution:
    the window width x height CSS px, pixel ratio dsf, the same wash each time (Math.random pinned). ms is a frame model (frame_model)
    that takes the ladder `down` steps (a window of 90 frames each). Returns the roughness of the wash as the cloth draws it
    ("roughness"), of the same wash with the shader told not to widen it (u_soft 1: "plain"), and the u_soft the cloth asked for
    ("soft"); problems go to `problems`, and None comes back when no wash was drawn."""
    npm = '[role="tab"][id$="-tab-npm"]'
    scripts = ("Math.random = () => 0.37;", FREEZE) + ((frame_model(ms),) if ms else ())
    context, _ = cloth_context(browser, base_url, session_key, page_named("/"), width=width, height=height, theme=theme, scripts=scripts,
                               device_scale_factor=dsf)
    page, errors = open_page(context, base_url + "/", warnings=True)
    found = None
    if not cloth_running(page):
        problems.append(f"{label}: the cloth never started")
    else:
        for _ in range(down + 1 if ms else 0):  # the steps down, a window of 90 frames each, and one more to settle
            run_frames(page, 90, step=1)
        run_frames(page, 30)
        page.locator("[data-come-in]").click()
        page.wait_for_function("() => { const box = document.querySelector('[data-install]'); return Boolean(box) && !box.hidden; }", timeout=20000)
        run_frames(page, 30)
        page.locator(npm).click()
        page.mouse.move(width - 3, height - 3)   # away: the selected tab holds its wash
        run_frames(page, 150)   # grown (1.4 s)
        scale = page.evaluate(SCALE)
        found = page.evaluate(WASH_ROUGHNESS, npm)
        asked = page.evaluate("() => window.__cloth.uniforms.u_soft")   # what the cloth asked of the shader for this frame
        page.evaluate("() => { window.__cloth.force.u_soft = 1; }")     # and the same wash drawn with no widening at all
        plain = page.evaluate(WASH_ROUGHNESS, npm)
        page.evaluate("() => { delete window.__cloth.force.u_soft; }")
        if scale is None or abs(scale - want) > 0.005:
            problems.append(f"{label}: the cloth should be at {want:.3f} of the CSS resolution for this, and is at {scale}")
            found = None
        elif not found or found["peak"] < 0.03 or not plain or plain["peak"] < 0.03:
            problems.append(f"{label}: no wash was drawn under the selected tab ({found}, {plain})")
            found = None
        else:
            found = {"roughness": found["roughness"], "plain": plain["roughness"], "soft": asked}
    problems.extend(f"{label}: console: {error}" for error in errors)
    context.close()
    return found


def soft_asked(browser, base_url, session_key, label, *, width, height, want, dsf, problems):
    """The u_soft the cloth hands the shader at its first step, on a window width x height CSS px at pixel ratio dsf, where the
    canvas should be at `want` of the CSS resolution; None (with a problem) when it is not or the cloth does not start."""
    context, _ = cloth_context(browser, base_url, session_key, page_named("/"), width=width, height=height, scripts=(FREEZE,),
                               device_scale_factor=dsf)
    page, errors = open_page(context, base_url + "/", warnings=True)
    asked = None
    if not cloth_running(page):
        problems.append(f"{label}: the cloth never started")
    else:
        run_frames(page, 30)
        scale, asked = page.evaluate(SCALE), page.evaluate("() => window.__cloth.uniforms.u_soft")
        if scale is None or abs(scale - want) > 0.005:
            problems.append(f"{label}: the cloth should be at {want:.3f} of the CSS resolution for this, and is at {scale}")
            asked = None
    problems.extend(f"{label}: console: {error}" for error in errors)
    context.close()
    return asked


def ink_soft_edge_problems(browser, base_url, session_key):
    """A wash is drawn for a canvas at the device's own first step (spec 3.1: 0.8 to 1.2, from 0.65 on a phone), where its ragged,
    granulated edge is smooth and crisp once the canvas is stretched to the screen, and where it must stay exactly as it is
    (a healthy device's wash is not softer, not fainter). Below that step (a phone the ladder has taken down, as far as 0.28,
    where a canvas pixel is 3.6 CSS px, or one a slow GPU left there) the same edge and grain show their pixels as stair-steps
    (spec 3.4), so the shader widens the edge and takes the grain out by how many times coarser the canvas is than the device's first
    step (u_soft). Two things are held. (1) At its first step a device asks the shader for u_soft 1 (within SOFT_EDGE_ONE): a wide
    screen at pixel ratio 1 (0.8), and a phone at 1, 2 and 3 (0.65, 0.8, 1.2); a rule that widens by a fixed 0.8 or 0.65 over the scale
    gives a phone at 0.65 a k of 1.23 and one at 1.2 a k of 0.67 (the table above says why the number is read, not the pixels).
    (2) At 0.28, on a phone at pixel ratio 1 and at 3, in Paper and in Ink, with the same seed and the wash held under the selected
    install tab, u_soft is the first step over 0.28 (2.32 and 4.29), and the wash drawn is rougher at its edge than the same wash with
    no widening by under SOFT_EDGE_BELOW: 1.0 when the shader does not widen, since the grain is a fixed number of canvas pixels
    whatever the scale."""
    problems = []
    for width, dsf, want in ((1024, 1, 0.8), (375, 1, 0.65), (375, 2, 0.8), (375, 3, 1.2)):
        label = f"/ cloth at {width}px, pixel ratio {dsf}, its first step {want}"
        asked = soft_asked(browser, base_url, session_key, label, width=width, height=640 if width > 640 else 667, want=want, dsf=dsf, problems=problems)
        if asked is not None and abs(asked - 1) > SOFT_EDGE_ONE:
            problems.append(f"{label}: a device at its first step draws the wash as it was made: the cloth should ask the shader for u_soft 1, "
                            f"and asks for {asked:.3f} (the first step over the scale, which is 1 on a healthy device)")
    for dsf in (1, 3):
        ladder = PHONE_LADDERS[dsf]
        for theme, appearance in (("light", "Paper"), ("dark", "Ink")):
            label = f"/ cloth in {appearance}, the selected tab's wash at 375px, pixel ratio {dsf}, {ladder[-1]} of the CSS resolution"
            found = wash_roughness(browser, base_url, session_key, label, width=375, height=667, theme=theme, want=phone_widths(ladder)[-1] / 375, dsf=dsf,
                                   ms=per_step(GPU_MS[dsf]["slow"], phone_widths(ladder)), down=len(ladder) - 1, problems=problems)
            if not found:
                continue
            should = ladder[0] / ladder[-1]
            if abs(found["soft"] - should) > SOFT_EDGE_ONE * should:
                problems.append(f"{label}: the shader should be asked to widen the edge by the first step over the scale, {should:.2f}; it is asked for {found['soft']:.2f}")
            if found["roughness"] > SOFT_EDGE_BELOW * found["plain"]:
                problems.append(f"{label}: a wash drawn at {ladder[-1]} of the CSS resolution is as rough at its edge as the same wash with no widening "
                                f"({found['roughness']:.3f} of its peak, {found['plain']:.3f} with none; it should be under {SOFT_EDGE_BELOW} of that): its pixels, "
                                f"3.6 CSS px across, show as stair-steps; the shader should widen the edge and take the grain down by u_soft")
    return problems


def ink_problems(browser, base_url, session_key):
    """With the cloth running, the pointer, a finger or keyboard focus grows an ink wash under a control, and its
    flat fill steps aside; the wash lives while the pointer is on the control, a click or not, and fades when the
    pointer leaves; the current chapter holds its wash while the menu is open and so does the selected install tab;
    a prose link's fill steps aside too (spec 3.4). The washes follow the canvas's own box (ink_frame_problems), and
    phones have them (ink_phone_problems), the open menu sheet of a phone included (cloth_sheet_ink_problems). With Motion off the flat fill is the answer."""
    problems = []
    about, begin = page_named("/about/"), page_named("/begin/")
    switch = "footer [data-appearance-toggle]"
    for theme in ("light", "dark"):
        seen = {}
        for hovered in (False, True):  # the same frames, with and without the pointer on the switch
            label = f"/about/ cloth, {theme}, ink on the appearance switch"
            context, _ = cloth_context(browser, base_url, session_key, about, theme=theme, scripts=(FREEZE,))
            page, errors = open_page(context, base_url + about.path, warnings=True)
            if not cloth_running(page):
                problems.append(f"{label}: the cloth never started")
                context.close()
                continue
            page.mouse.move(4, 320)  # the left margin, where nothing answers the pointer
            run_frames(page, 60)
            if hovered:
                page.hover(switch)
            run_frames(page, 90)  # the wash grows over 1.4 s
            capture(page)
            grown = canvas_stats(page, page.evaluate(INK_PATCH, switch))["luma"]
            if hovered and (not page.evaluate(INKED, switch) or page.evaluate(OWN_FILL, switch) != "rgba(0, 0, 0, 0)"):
                problems.append(f"{label}: while inked, the switch should carry is-inked and no flat fill "
                                f"(is-inked {page.evaluate(INKED, switch)}, fill {page.evaluate(OWN_FILL, switch)})")
            # A click does not end the wash: with the pointer still on the switch, 2.5 s later it is there at the
            # strength it had, and the flat fill has not come back. The click also switches the appearance, which
            # the cloth fades across, so the wash is what differs from the run whose pointer was away (it clicks too).
            if hovered:
                page.locator(switch).click()
            else:
                page.evaluate("(selector) => document.querySelector(selector).click()", switch)
            run_frames(page, 150)
            capture(page)
            kept = canvas_stats(page, page.evaluate(INK_PATCH, switch))["luma"]
            if hovered and (not page.evaluate(INKED, switch) or page.evaluate(OWN_FILL, switch) != "rgba(0, 0, 0, 0)"):
                problems.append(f"{label}: 2.5 s after a click with the pointer still on the switch, it should still carry is-inked and no flat "
                                f"fill (is-inked {page.evaluate(INKED, switch)}, fill {page.evaluate(OWN_FILL, switch)})")
            page.mouse.move(4, 320)
            run_frames(page, 150)  # the wash fades over 1.8 s
            capture(page)
            seen[hovered] = (grown, kept, canvas_stats(page, page.evaluate(INK_PATCH, switch))["luma"])
            if hovered and page.evaluate(INKED, switch):
                problems.append(f"{label}: 2.5 s after the pointer left, the switch should have given up is-inked")
            problems += [f"{label}: console: {error}" for error in errors]
            context.close()
        if len(seen) == 2:
            grew, kept, faded = (seen[True][i] - seen[False][i] for i in range(3))
            if abs(grew) < 0.03 or (grew > 0) != (theme == "dark"):
                problems.append(f"/about/ cloth, {theme}: hovering the switch should lay a wash under it "
                                f"({'lighter' if theme == 'dark' else 'darker'} by 0.03 of luma or more); it changed {grew:+.3f}")
            if abs(kept) < 0.6 * abs(grew):  # the appearance has switched, which turns the wash's polarity and changes its size a little
                problems.append(f"/about/ cloth, {theme}: a click should not end the wash; 2.5 s after it, with the pointer still on the switch, "
                                f"the wash should be as strong as before it ({grew:+.3f} of luma before the click, {kept:+.3f} after)")
            if abs(faded) > 0.01:
                problems.append(f"/about/ cloth, {theme}: the wash should have faded 2.5 s after the pointer left; {faded:+.3f} remains")

    label = "/about/ cloth, ink"
    context, _ = cloth_context(browser, base_url, session_key, about)
    page, errors = open_page(context, base_url + about.path, warnings=True)
    if cloth_running(page):
        run_frames(page, 60)
        current = '#shell-menu a[aria-current="page"]'
        if page.evaluate(INKED, current):
            problems.append(f"{label}: the menu starts closed, so the current chapter's link should hold no wash yet")
        for _ in range(6):  # keyboard focus on MENU grows a wash too
            page.keyboard.press("Tab")
            if page.evaluate("() => document.activeElement.matches('[data-menu-toggle]')"):
                break
        run_frames(page, 10)
        if not page.evaluate(INKED, "header [data-menu-toggle]"):
            problems.append(f"{label}: keyboard focus on MENU should grow a wash")
        page.keyboard.press("Enter")  # opens the menu, as a keyboard visitor does: the current chapter holds its wash
        run_frames(page, 60)
        if not page.evaluate(INKED, current) or page.evaluate(OWN_FILL, current) != "rgba(0, 0, 0, 0)":
            problems.append(f"{label}: the current chapter should hold its wash in the open menu, without its flat fill")
    else:
        problems.append(f"{label}: the cloth never started")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()

    label = "/begin/ cloth, a prose link"
    link = 'main .chapter p a[href="/app/"]'
    context, _ = cloth_context(browser, base_url, session_key, begin)
    page, errors = open_page(context, base_url + begin.path, warnings=True)
    if cloth_running(page):
        page.hover(link)
        run_frames(page, 10)
        fill = page.evaluate("(selector) => getComputedStyle(document.querySelector(selector)).backgroundColor", link)
        if not page.evaluate(INKED, link) or fill != "rgba(0, 0, 0, 0)":
            problems.append(f"{label}: a hovered prose link should take the wash and lose its flat fill (is-inked "
                            f"{page.evaluate(INKED, link)}, fill {fill})")
    else:
        problems.append(f"{label}: the cloth never started")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()

    # A click on a menu link changes the chapter in place. The link's wash, begun by the pointer, carries on at full strength
    # through the click up to the swap (nothing ends it at the click, and the cross-fade takes no pointer from the page, so the
    # pointer does not leave the link: ink_change_problems; opening the menu has loaded the faces, so no layout comes with the change
    # to have the browser take the pointer for a moment, "The pointer in a change" in AGENTS.md); the swap shuts the menu (spec 4.4), and the wash is then let go of
    # like any other: it fades over 1.8 s from the strength it had, not at once (a cut would show as the cloth jumping where it
    # was) and not held under a link that is gone. The cloth's time runs in frames of 0.3 s from the click on, so a wash cut
    # at the click or at the swap is seen (a fade reads 0.83 after the first frame it is let go for), and the strength the cloth
    # was asked to draw it at is read in every frame from the click on, at the link's place (WASH_HISTORY_AT: the link has no box
    # once the menu is shut).
    label = "/about/ cloth, ink on a menu link after a click"
    link = '#shell-menu a[href="/plans/"]'
    context, _ = cloth_context(browser, base_url, session_key, about, scripts=(FREEZE, CHANGE_PROBE))
    page, errors = open_page(context, base_url + about.path, warnings=True)
    if cloth_running(page):
        set_menu(page, True)
        page.mouse.move(4, 320)
        run_frames(page, 60)
        page.hover(link)
        run_frames(page, 90)
        capture(page)
        if wash_strength(page, link) < 0.99:
            problems.append(f"{label}: hovering a menu link should lay a full wash under it (strength {wash_strength(page, link):.2f} after 1.5 s)")
        box = page.evaluate(BOX_RECT, link)
        place = [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2]
        page.evaluate("() => { const c = window.__cloth; c.clock.step = 300; c.clock.stopAt = Infinity; }")
        clicked = page.evaluate("() => window.__cloth.frames")
        page.locator(link).click()
        page.wait_for_function("() => location.pathname === '/plans/'", timeout=20000)  # the swap has happened
        # 4.2 s of cloth time, from the swap on: the column fades out over its 0.7 s first (five frames of 0.3 s, a quarter of the fade at most a
        # step), under the pointer, and the wash fades over 1.8 s once it is let go, at the latest when the column has gone
        run_frames(page, 14, step=300)
        swapped = page.evaluate("() => window.__change.swappedFrame")
        history = page.evaluate(WASH_HISTORY_AT, [place, clicked])  # in every frame the cloth drew
        before = [strength for frame, strength in history if frame <= swapped]
        after = [strength for frame, strength in history if frame > swapped]
        if page.evaluate("() => location.pathname") != "/plans/":
            problems.append(f"{label}: the change to Plans did not happen")
        elif not before or min(before) < 0.97:
            problems.append(f"{label}: the wash under the link should carry on at full strength from the click to the swap, "
                            f"but it fell to {min(before or [0]):.2f} of it")
        elif not after or after[0] < 0.75 or after[-1] > 0.02:
            problems.append(f"{label}: once the swap shuts the menu the wash should be let go of and fade over 1.8 s: {after[0] if after else 0:.2f} of "
                            f"its strength in the first frame after the swap (a cut reads 0), {after[-1] if after else 0:.2f} 4.2 s on (it should be gone)")
        if page.locator("#shell-menu").is_visible():
            problems.append(f"{label}: the menu is open after the change in place, so nothing was checked")
    else:
        problems.append(f"{label}: the cloth never started")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()

    # The selected install tab holds its wash, like the current chapter in the open menu (spec 3.4), so the wash
    # rather than the flat fill marks it: a click on a tab with the pointer left on it, then the pointer away, then
    # another tab, on the threshold after Come in. The strength the cloth was asked to draw each wash at is read
    # (WASH_UNDER): the selected tab's stays full however long the pointer is away; the tab given up fades over
    # 1.8 s, not at once; and the selected tab never wears the flat fill (the rectangle the wash stands in for).
    # Come in itself, once it has been clicked and has gone, leaves no wash at a zero box (a wash at the page's
    # top left corner, 6 by 4 px): its wash fades where the label was.
    label = "/ cloth, the selected install tab"
    context, _ = cloth_context(browser, base_url, session_key, page_named("/"), width=1280, height=800, scripts=(FREEZE,))
    page, errors = open_page(context, base_url + "/", warnings=True)
    if cloth_running(page):
        npm, pip = '[role="tab"][id$="-tab-npm"]', '[role="tab"][id$="-tab-pip"]'
        run_frames(page, 30)
        page.locator("[data-come-in]").click()  # the pointer is on COME IN, whose wash has grown
        page.wait_for_function("() => { const box = document.querySelector('[data-install]'); return Boolean(box) && !box.hidden; }",
                               timeout=20000)  # Come in's fade-out is a real-time timer
        run_frames(page, 30)
        zero = [wash for wash in page.evaluate(INKS_DRAWN) if abs(wash["rx"] - 6) < 0.01 and abs(wash["ry"] - 4) < 0.01]
        if zero:
            problems.append(f"{label}: Come in left a wash at a zero box (at {zero[0]['x']:.0f}, {zero[0]['y']:.0f}) once it was gone; "
                            f"it should fade where the label was")
        flat = lambda selector: page.evaluate(OWN_FILL, selector) != "rgba(0, 0, 0, 0)"
        page.locator(npm).click()  # the pointer stays on npm
        for frames, moment in ((150, "2.5 s after the click, the pointer still on it"), (150, "2.5 s after the pointer left")):
            if moment.endswith("left"):
                page.mouse.move(4, 320)
            run_frames(page, frames)
            held = wash_strength(page, npm)
            if held < 0.99 or not page.evaluate(INKED, npm) or flat(npm):
                problems.append(f"{label}: the selected npm tab should hold a full wash and no flat fill {moment} "
                                f"(strength {held:.2f}, is-inked {page.evaluate(INKED, npm)}, flat fill {flat(npm)})")
        page.locator(pip).click()
        run_frames(page, 3)
        if wash_strength(page, npm) < 0.9 or wash_strength(page, pip) < 0.99 or flat(pip) or not page.evaluate(INKED, pip):
            problems.append(f"{label}: just after pip is chosen its wash should be held (strength {wash_strength(page, pip):.2f}, fill {flat(pip)}) "
                            f"and npm's should have begun to fade, not vanished (strength {wash_strength(page, npm):.2f})")
        run_frames(page, 120)
        if wash_strength(page, npm) > 0 or page.evaluate(INKED, npm) or wash_strength(page, pip) < 0.99 or flat(pip) or flat(npm):
            problems.append(f"{label}: 2 s after pip is chosen npm's wash should be gone (strength {wash_strength(page, npm):.2f}, is-inked "
                            f"{page.evaluate(INKED, npm)}) and pip's held (strength {wash_strength(page, pip):.2f}); neither should wear a flat fill "
                            f"(npm {flat(npm)}, pip {flat(pip)})")
        # A wash spreads a little as it ages (the shader's own 2% a second) but a held one is held for as long as the
        # page is read: after ten seconds more it must not have grown over its neighbours (the shader is given its
        # age). Frames of 24 ms, so that the cloth does not read its own frames as too slow to go on.
        run_frames(page, 400, step=24)
        grown = page.evaluate(WASH_UNDER, pip)
        if grown is None or grown["age"] > 4.001:
            problems.append(f"{label}: a held wash should stop spreading as it ages, but ten seconds on the shader was given an age of "
                            f"{'no wash' if grown is None else '{:.0f} s'.format(grown['age'])}; it would grow over its neighbours")
    else:
        problems.append(f"{label}: the cloth never started")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()

    problems += ink_frame_problems(browser, base_url, session_key)

    problems += ink_change_problems(browser, base_url, session_key)

    problems += ink_phone_problems(browser, base_url, session_key)

    problems += ink_crowd_problems(browser, base_url, session_key)

    problems += ink_places_problems(browser, base_url, session_key)

    problems += ink_soft_edge_problems(browser, base_url, session_key)

    # Motion off: no wash, the flat fill.
    label = "/about/ with Motion off"
    context, _ = cloth_context(browser, base_url, session_key, about, motion="off")
    page, errors = open_page(context, base_url + about.path, warnings=True)
    page.locator(switch).scroll_into_view_if_needed()
    page.hover(switch)
    page.wait_for_timeout(300)
    if page.evaluate(INKED, switch) or page.evaluate(OWN_FILL, switch) == "rgba(0, 0, 0, 0)":
        problems.append(f"{label}: hovering the switch should give the flat fill, not a wash")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()
    return problems


SCALE = "() => { const canvas = document.querySelector('canvas.veil'); return canvas ? canvas.width / canvas.getBoundingClientRect().width : null; }"
HIDE_TAB = """(hidden) => {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => (hidden ? "hidden" : "visible") });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}"""


# The virtual clock's frame length, decided per frame from the canvas as it is then (the probe's step is what run_frames sets,
# and a frozen clock stays frozen): `ms` is a JavaScript expression of `width`, the canvas's width in pixels (per_step builds one
# from a frame length for each step of the ladder). A display that caps frames is a constant; a GPU that is slow is a length that
# falls with the size.
def frame_model(ms):
    return ("(() => { const clock = window.__cloth.clock; let asked = clock.step; Object.defineProperty(clock, 'step', { configurable: true,"
            " get: () => { if (asked === 0) return 0; const canvas = document.querySelector('canvas.veil'), width = canvas ? canvas.width : 244;"
            f" return {ms}; }}, set: (value) => {{ asked = value; }} }}); }})();")


def per_step(ms, widths):
    """A frame_model expression: ms[i] while the canvas is the i-th step's width (widths, the widest first), told apart halfway."""
    cuts = [(wide + narrow) / 2 for wide, narrow in zip(widths, widths[1:])]
    expression = str(ms[-1])
    for cut, value in reversed(list(zip(cuts, ms))):
        expression = f"width > {cut:g} ? {value} : {expression}"
    return expression


CAPPED = "30 fps whatever the canvas size (iOS Low Power Mode caps animation frames at 30 a second)"
CAPPED_MS = "1000 / 30"
# What each kind of device does to the frame length at each step of a phone's ladder (PHONE_LADDERS, by pixel ratio), in ms. The limits
# are veil.js's: over 24 is slow, a step must leave the median under 0.85 of what caused it to have helped, over 40 at the lowest is the still page.
GPU_MS = {
    1: {"slow": (55, 40, 25, 15),                    # helped by every step, healthy (under 24) at the last
        "still": (150, 120, 80, 60),                 # helped by every step, and still over the limit at the last
        "capped": (80, 60, 36, CAPPED_MS),           # slow, helped by the second and third steps, then the cap at 30 fps: the last step does not help
        "after_step": (26, 23, 23, 23)},             # just over the limit at the first step; at the second under it, though only 12% shorter
    3: {"slow": (100, 80, 60, 45, 32, 15),
        "still": (160, 130, 105, 85, 70, 58),
        "capped": (120, 95, 75, 58, 36, CAPPED_MS),
        "after_step": (26, 23, 23, 23, 23, 23)},
}


def keeps_first_step(page, first):
    """Whether the canvas still has the phone's first step, `first` of the CSS resolution (False when the cloth has gone, which has no canvas)."""
    scale = page.evaluate(SCALE)
    return scale is not None and abs(scale - first) <= 0.02


def adaptive_problems(browser, base_url, session_key):
    """The adaptive cloth (spec 8), on a phone, where frames are cheap, at pixel ratio 1 and 3: the ladder follows the screen
    (spec 3.1), 0.65, 0.49, 0.37 and 0.28 at 1, and 1.2, 0.9, 0.68, 0.51, 0.38 and 0.28 at 3 (PHONE_LADDERS), so the rules are
    held on the shorter ladder and the longer."""
    problems = []
    for dsf in (1, 3):
        problems += adaptive_phone_problems(browser, base_url, session_key, dsf)
    return problems


def adaptive_phone_problems(browser, base_url, session_key, dsf):
    """The adaptive cloth (spec 8) on a phone with pixel ratio dsf. A healthy device keeps its first step. Frames that run long and
    that a smaller canvas shortens (a slow GPU: the frame length is chosen from the canvas's width, frame_model) lower the resolution
    step by step and keep it, and if they are still over the limit at the last step give the still page for the visit: data-veil-off
    "slow", Motion and its button off, storage untouched, and the same on the visit's next page until Motion is switched on. Frames
    held at 33.3 ms whatever the canvas size (iOS Low Power Mode) are the display's cap: the step that did not help is undone, the
    cloth stays at its first step and the tab remembers it; a step that brings the frames under the limit is kept even if it shortened
    them by under 15%. A step that is kept is remembered for the tab: the next page starts at the last step kept, whether the first page's
    walk had ended or not, and only goes on down (second_page). One long gap (a tab coming back) changes nothing. A hidden tab draws nothing and picks up where it was. A lost
    WebGL context gives the still page."""
    problems = []
    ladder, ms = PHONE_LADDERS[dsf], GPU_MS[dsf]
    widths, count = phone_widths(ladder), len(ladder)
    first, second = ladder[0], ladder[1]
    fmt = lambda step: f"{step:.2f}"
    steps = ", ".join(fmt(step) for step in ladder)
    who = "a phone" if dsf == 1 else f"a phone at pixel ratio {dsf}"
    about = page_named("/about/")
    remembered = "() => sessionStorage.getItem('veilfile-veil-off')"  # what the tab remembers of a device too slow

    def opened(label, scripts=()):
        context, _ = cloth_context(browser, base_url, session_key, about, width=375, height=667, scripts=(FREEZE, *scripts),
                                   device_scale_factor=dsf)
        page, errors = open_page(context, base_url + about.path, warnings=True)
        if not cloth_running(page):
            problems.append(f"{label}: the cloth never started")
            context.close()
            return None, None, None
        return context, page, errors

    def scales_over(page, rounds=4):
        scales = [page.evaluate(SCALE)]  # where the cloth starts, before its first window (90 frames) has been judged
        for _ in range(rounds):  # 90 frames at a time: a 30-frame settle and a 60-frame measure
            run_frames(page, 90, step=1)
            scales.append(page.evaluate(SCALE))
            if scales[-1] is None:
                break
        return [round(scale, 3) for scale in scales if scale is not None]

    def reached(scales, scale):  # a 375 px canvas is whole pixels: 0.375 comes out as 0.376
        return any(abs(s - scale) < 0.005 for s in scales)

    def under_second(scales):  # a walk that went below the ladder's second step
        return any(scale < second - 0.01 for scale in scales)

    def second_page(label, page, rounds=2):
        """A full page load in the same tab, as a visitor's next page is: where its cloth begins and the scales over its first rounds
        (None and [] when its cloth never started)."""
        page.goto(base_url + "/plans/", wait_until="networkidle")
        if not cloth_running(page):
            problems.append(f"{label}: the next page's cloth never started")
            return None, []
        begun = page.evaluate(SCALE)
        return begun, scales_over(page, rounds)

    label = f"/about/ cloth on {who}, healthy"
    context, page, errors = opened(label)
    if page:
        run_frames(page, 300)
        if not keeps_first_step(page, first):
            problems.append(f"{label}: a healthy device should keep its first step, {fmt(first)} of the CSS resolution; it has {page.evaluate(SCALE)}")
        run_frames(page, 1, step=5000)  # one five-second gap, as when a tab comes back
        run_frames(page, 200)
        state = page.evaluate(VEIL_STATE)
        if not keeps_first_step(page, first) or state["veil"] != "on":
            problems.append(f"{label}: one long gap should change nothing ({state}, scale {page.evaluate(SCALE)})")
        page.evaluate(HIDE_TAB, True)
        draws = page.evaluate(VEIL_STATE)["draws"]
        page.wait_for_timeout(600)
        if page.evaluate(VEIL_STATE)["draws"] != draws:
            problems.append(f"{label}: a hidden tab should draw nothing")
        page.evaluate(HIDE_TAB, False)
        page.wait_for_timeout(600)
        if page.evaluate(VEIL_STATE)["draws"] == draws or not keeps_first_step(page, first):
            problems.append(f"{label}: the cloth should pick up where it was when the tab shows again")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    # What slows the frames matters. A display that caps them (iOS Low Power Mode: 30 a second, 33.3 ms) makes the frames
    # long at any size, so a lower resolution cannot help and is only blocky: the ladder is undone, and the tab remembers it,
    # so its other pages start at the first step without probing again. A GPU that is slow gets faster frames from a smaller canvas,
    # and keeps the lower scale. Each is a frame length chosen per frame from the canvas's own width (frame_model).
    capped = "() => sessionStorage.getItem('veilfile-veil-capped')"

    label = f"/about/ cloth on {who} held at {CAPPED}"
    context, page, errors = opened(label, (frame_model(CAPPED_MS),))
    if page:
        scales = scales_over(page)
        if abs(scales[-1] - first) > 0.02 or under_second(scales):
            problems.append(f"{label}: frames of 33.3 ms at every size are the display's limit, not the GPU's: one step down is tried, "
                            f"does not help and is undone, so the cloth should end at {fmt(first)} and never go below {fmt(second)}, the ladder's "
                            f"second step; its scale went {scales}")
        if page.evaluate(capped) is None:
            problems.append(f"{label}: the tab should remember that the display caps its frames (veilfile-veil-capped in sessionStorage)")
        state = page.evaluate(VEIL_STATE)
        if state["veil"] != "on" or state["off"] is not None or page.evaluate(remembered) is not None:
            problems.append(f"{label}: 33.3 ms is under the still page's limit, so the cloth should go on ({state}, remembered {page.evaluate(remembered)})")
        # A new page in the tab starts at the first step and does not step down to find out again.
        page.goto(base_url + "/plans/", wait_until="networkidle")
        if not cloth_running(page):
            problems.append(f"{label}: the next page's cloth never started")
        else:
            begun = page.evaluate(SCALE)
            scales = scales_over(page)
            if abs(begun - first) > 0.02 or any(abs(scale - first) > 0.02 for scale in scales):
                problems.append(f"{label}: a page loaded after that should start at {fmt(first)} and not step down; it began at {begun:.3f} and went {scales}")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    label = f"/about/ cloth on {who} whose GPU is slow, faster at every smaller size"
    context, page, errors = opened(label, (frame_model(per_step(ms["slow"], widths)),))
    if page:
        scales = scales_over(page, rounds=count + 1)  # every step down, a window of 90 frames each
        if scales != sorted(scales, reverse=True) or not all(reached(scales, step) for step in ladder) or abs(scales[-1] - ladder[-1]) > 0.005:
            problems.append(f"{label}: frames that a smaller canvas makes faster should step the resolution down through {steps} and keep it at "
                            f"{fmt(ladder[-1])}; it went {scales}")
        state = page.evaluate(VEIL_STATE)
        if state["veil"] != "on" or state["off"] is not None:
            problems.append(f"{label}: frames of {ms['slow'][-1]} ms at {fmt(ladder[-1])} are healthy, so the cloth should go on ({state})")
        if page.evaluate(capped) is not None:
            problems.append(f"{label}: the ladder helped, so the tab should not remember a cap ({page.evaluate(capped)!r})")
        # The walk was paid for once: the tab's next page starts where it ended, and stays (frames are healthy there).
        begun, scales = second_page(label, page)
        if begun is not None and (abs(begun - ladder[-1]) > 0.005 or any(abs(scale - ladder[-1]) > 0.005 for scale in scales)):
            problems.append(f"{label}: a page loaded after the walk should start at {fmt(ladder[-1])}, where it ended, and stay; it began at {begun:.3f} and went {scales}")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    # A walk that is cut short by the next page is not lost either: the steps it had kept are the next page's start, and it goes on from there.
    label = f"/about/ cloth on {who} whose GPU is slow, then a second page in the same tab before the walk is over"
    context, page, errors = opened(label, (frame_model(per_step(ms["slow"], widths)),))
    if page:
        scales = scales_over(page, rounds=3)  # two steps down judged and kept (to ladder[2]), the third (ladder[3]) just taken
        if not (reached(scales, ladder[2]) and reached(scales, ladder[3])):
            problems.append(f"{label}: the walk should have gone down to {fmt(ladder[3])} in three windows; it went {scales}")
        begun, scales = second_page(label, page, rounds=count)
        kept = ladder[2]  # the last step the first page had judged and kept: the one below it was still being tried
        if begun is not None and (abs(begun - kept) > 0.02 or any(scale > kept + 0.02 for scale in scales) or scales != sorted(scales, reverse=True)
                                  or abs(scales[-1] - ladder[-1]) > 0.005):
            problems.append(f"{label}: a page loaded after the first two steps were kept should start at {fmt(kept)}, go on down only, and end at "
                            f"{fmt(ladder[-1])} where frames are healthy; it began at {begun:.3f} and went {scales}")
        if page.evaluate(capped) is not None or page.evaluate(VEIL_STATE)["veil"] != "on":
            problems.append(f"{label}: the ladder helped, so the tab should not remember a cap and the cloth should go on "
                            f"({page.evaluate(capped)!r}, {page.evaluate(VEIL_STATE)})")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    label = f"/about/ cloth on {who} whose first step down brings it to healthy frames, if only by a little"
    context, page, errors = opened(label, (frame_model(per_step(ms["after_step"], widths)),))
    if page:
        scales = scales_over(page)
        # 26 ms at the first step is over the limit (24); 23 ms at the second is under it, but not under 0.85 of 26 (22.1): the step reached healthy frames, so it is kept
        if abs(scales[0] - first) > 0.02 or abs(scales[-1] - second) > 0.005 or under_second(scales):
            problems.append(f"{label}: a step that brings the frames under the limit is kept even if it shortened them by less than 15% "
                            f"(26 ms before, 23 after), so the cloth should start at {fmt(first)}, go down to {fmt(second)} and stay; it went {scales}")
        if page.evaluate(capped) is not None:
            problems.append(f"{label}: the step reached healthy frames, so the tab should not remember a cap ({page.evaluate(capped)!r})")
        if page.evaluate(VEIL_STATE)["veil"] != "on":
            problems.append(f"{label}: 23 ms is healthy, so the cloth should go on ({page.evaluate(VEIL_STATE)})")
        begun, scales = second_page(label, page)
        if begun is not None and (abs(begun - second) > 0.005 or any(abs(scale - second) > 0.005 for scale in scales)):
            problems.append(f"{label}: a page loaded after that should start at {fmt(second)}, the step that was kept, and stay; it began at {begun:.3f} and went {scales}")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    label = f"/about/ cloth on {who} whose GPU is slow, until the display's cap"
    context, page, errors = opened(label, (frame_model(per_step(ms["capped"], widths)),))
    if page:
        scales = scales_over(page, rounds=count + 2)  # every step down and the last one undone, a window of 90 frames each
        # each step helps (kept) down to the one before the last; the last holds the cap at 30 fps (33.3 ms), which does not help: that step is undone
        if abs(scales[0] - first) > 0.02 or abs(scales[-1] - ladder[-2]) > 0.005:
            problems.append(f"{label}: the cloth should start at {fmt(first)}, and the steps that helped be kept and the one that did not help undone, "
                            f"ending at {fmt(ladder[-2])}; it went {scales}")
        if page.evaluate(VEIL_STATE)["veil"] != "on":
            problems.append(f"{label}: {ms['capped'][-2]} ms is under the still page's limit, so the cloth should go on ({page.evaluate(VEIL_STATE)})")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    label = f"/about/ cloth on {who} that cannot keep up even at the lowest resolution"
    context, page, errors = opened(label, (frame_model(per_step(ms["still"], widths)),))
    if page:
        stepped = scales_over(page, rounds=count + 2)  # every step down and the window that finds the lowest one too slow
        state = page.evaluate(VEIL_STATE)
        if stepped != sorted(stepped, reverse=True) or not all(reached(stepped, step) for step in ladder):
            problems.append(f"{label}: a GPU that a smaller canvas helps should step the resolution down {steps} and never up; it went {stepped}")
        expected = {"off": "slow", "motion": "off", "pressed": "false", "saved": "on", "canvases": 0, "veil": None}
        if {key: state[key] for key in expected} != expected:
            problems.append(f"{label}: at the lowest step, still over the limit, it should give the still page for the visit, storage untouched ({state})")
        # For the visit: a full page load later in the same tab starts no cloth and fetches no veil.js, the first paint is
        # already the still page, and Motion on, the visitor asking for the cloth, forgets the verdict.
        if page.evaluate(remembered) != "slow":
            problems.append(f"{label}: the still page should be remembered for the tab (veilfile-veil-off in sessionStorage)")
        later = []
        context.on("request", lambda request: later.append(request.url) if VEIL_SCRIPT.match(request.url) else None)
        page.goto(base_url + "/plans/", wait_until="networkidle")
        state = page.evaluate(VEIL_STATE)
        if {key: state[key] for key in expected} != expected or later:
            problems.append(f"{label}: a page loaded after the still page should be the still page too, with no cloth started "
                            f"({state}; veil.js requested {later})")
        page.locator("footer [data-motion-toggle]").click()
        if not cloth_running(page) or page.evaluate(remembered) is not None or page.evaluate(VEIL_STATE)["saved"] != "on":
            problems.append(f"{label}: Motion on should start the cloth again and forget that the device was too slow "
                            f"({page.evaluate(VEIL_STATE)}, remembered {page.evaluate(remembered)})")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    label = f"/about/ cloth on {who} whose frames are over the limit at every size"
    context, page, errors = opened(label, (frame_model("50"),))
    if page:
        scales = scales_over(page)
        state = page.evaluate(VEIL_STATE)
        expected = {"off": "slow", "motion": "off", "pressed": "false", "saved": "on", "canvases": 0, "veil": None}
        if {key: state[key] for key in expected} != expected or page.evaluate(remembered) != "slow" or under_second(scales):
            problems.append(f"{label}: 50 ms at every size is no GPU's doing: one step down is tried and undone, never going below {fmt(second)}, "
                            f"the ladder's second step, and as the frames are still over the still page's limit that gives the still page for the tab "
                            f"({state}, scales {scales}, remembered {page.evaluate(remembered)})")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    if dsf == 1:  # the loss of the context does not depend on the ladder
        label = f"/about/ cloth on {who}, the WebGL context lost"
        context, page, errors = opened(label)
        if page:
            run_frames(page, 30)
            page.evaluate("() => window.__cloth.gl.getExtension('WEBGL_lose_context').loseContext()")
            try:
                page.wait_for_function("() => document.documentElement.dataset.veilOff === 'lost'", timeout=10000)
            except PlaywrightError:
                pass
            state = page.evaluate(VEIL_STATE)
            expected = {"off": "lost", "motion": "off", "pressed": "false", "saved": "on", "canvases": 0, "veil": None}
            if {key: state[key] for key in expected} != expected:
                problems.append(f"{label}: a lost context should give the still page for the visit ({state})")
            if page.evaluate(remembered) is not None:
                problems.append(f"{label}: only a device too slow is remembered for the tab; a lost context is tried again on the next page")
            problems += [f"{label}: console: {error}" for error in errors if "CONTEXT_LOST" not in error]  # the loss itself is announced
            context.close()

    return problems


# The strip of the screen between the mark and MENU on a phone, across the part of the top band where the corners' rows are
# (the band is --fade-top tall, 65 px at 667 px, then fades over 32): clear of both corners' words.
TOP_STRIP = {"x": 80, "y": 4, "width": 210, "height": 56}
TEXT_IN_STRIP = """(strip) => { const walker = document.createTreeWalker(document.querySelector('main'), NodeFilter.SHOW_TEXT); let count = 0;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!node.textContent.trim()) continue;
    const range = document.createRange(); range.selectNodeContents(node);
    if ([...range.getClientRects()].some((r) => r.left < strip.x + strip.width && strip.x < r.right && r.top < strip.y + strip.height && strip.y < r.bottom)) count += 1;
  }
  return count; }"""
# Scrolls a page of text so that a paragraph starts a little below the top of the screen: its first lines are in the top band.
TEXT_AT_TOP = """() => { const p = [...document.querySelectorAll('main .chapter > p')].find((p) => p.getBoundingClientRect().height > 80);
  window.scrollTo(0, p.getBoundingClientRect().top + scrollY - 10); return scrollY; }"""
NEXT_FRAMES = "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))"


# Where a link is on the screen, and how tall the band is that the page has faded out along the top of a phone (--fade-top and
# --fade-band: the corners' rows, then the fade), in pixels.
LINK_IN_BAND = """(selector) => { const probe = document.createElement('div');
    probe.style.height = 'calc(var(--fade-top) + var(--fade-band))'; document.body.append(probe);
    const band = probe.getBoundingClientRect().height; probe.remove();
    return { top: document.querySelector(selector).getBoundingClientRect().top, band }; }"""


def cloth_top_fade_problems(browser, base_url, session_key):
    """On a phone the page fades out at the top instead of passing under a bar (spec 4.1): as it scrolls up toward the
    top edge it never reaches the mark and MENU, and nothing is laid over the cloth or the still ground there.
    Scrolled so that a paragraph sits in the top band of /begin/ at 375x667:
    (b) with the cloth running, the strip between the corners looks like the cloth: the screenshot's mean luma
    there is the canvas readback's, within 0.012, so no flat band lies across it (a bar of paper and grain,
    which is what it was, is lighter or darker than the cloth by more than that) and no text shows in it;
    (c) with Motion off, that strip is the still grain ground: its mean and spread are those of the same strip
    where the page has nothing (scrolled to the top, where main's padding is), within 0.012 and 30%.
    The corners' own words are left out of both (under_corner_problems looks at them)."""
    problems = []
    begin = page_named("/begin/")
    strip = TOP_STRIP
    clip = {"x": strip["x"], "y": strip["y"], "width": strip["width"], "height": strip["height"]}

    for theme in ("light", "dark"):
        label = f"/begin/ cloth at 375px, {theme}, the top of the page"
        context, _ = cloth_context(browser, base_url, session_key, begin, width=375, height=667, theme=theme, scripts=(FREEZE,))
        page, errors = open_page(context, base_url + begin.path, warnings=True)
        if not cloth_running(page):
            problems.append(f"{label}: the cloth never started")
            context.close()
            continue
        run_frames(page, 200)
        page.evaluate(TEXT_AT_TOP)
        page.evaluate(NEXT_FRAMES)
        run_frames(page, 3)
        if page.evaluate("() => scrollY") <= 0 or not page.evaluate(TEXT_IN_STRIP, strip):
            problems.append(f"{label}: no text lies in the strip between the corners, so nothing was checked "
                            f"(scrolled {page.evaluate('() => scrollY')} px)")
        capture(page)
        want = canvas_stats(page, strip)["luma"]
        page.evaluate("() => { window.__cloth.drawAll = true; }")
        page.evaluate(NEXT_FRAMES)
        have, _ = png_luma(page.screenshot(clip=clip))
        page.evaluate("() => { window.__cloth.drawAll = false; }")
        if abs(have - want) > 0.012:
            problems.append(f"{label}: the strip between the mark and MENU shows {have:.3f} of luma on the screen where the cloth is {want:.3f}; "
                            f"nothing should lie over the cloth there (a bar), and no text should show")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    for theme in ("light", "dark"):
        label = f"/begin/ at 375px, {theme}, Motion off, the top of the page"
        context = new_context(browser, base_url, session_key, begin, width=375, height=667, theme=theme, motion="off")
        page, errors = open_page(context, base_url + begin.path)
        bare = png_luma(page.screenshot(clip=clip))  # scrolled to the top: main's padding, nothing else
        page.evaluate(TEXT_AT_TOP)
        page.evaluate(NEXT_FRAMES)
        if page.evaluate("() => scrollY") <= 0 or not page.evaluate(TEXT_IN_STRIP, strip):
            problems.append(f"{label}: no text lies in the strip between the corners, so nothing was checked")
        faded = png_luma(page.screenshot(clip=clip))
        if abs(faded[0] - bare[0]) > 0.012 or abs(faded[1] - bare[1]) > 0.3 * bare[1] + 0.002:
            problems.append(f"{label}: the strip between the mark and MENU should be the grain ground (luma {bare[0]:.3f}, spread {bare[1]:.4f} "
                            f"where the page has nothing); with text scrolled into it, it is {faded[0]:.3f} and {faded[1]:.4f}")
        # Only <main> fades: it starts at the top of the document, so where the top of the screen falls on it is the scroll
        # offset. The footer waits at the page's end and cannot reach the top band, so it carries no mask (which would
        # make it a stacking context and a layout read of its own on every scroll frame).
        mask = page.evaluate("""() => { const footer = document.querySelector('body > footer'), main = document.querySelector('main');
            return { footer: getComputedStyle(footer).maskImage, footerY: footer.style.getPropertyValue('--fade-y'),
                     main: getComputedStyle(main).maskImage, mainY: parseFloat(main.style.getPropertyValue('--fade-y')), scrolled: scrollY,
                     top: main.getBoundingClientRect().top }; }""")
        if abs(mask["top"] + mask["scrolled"]) > 0.5:
            problems.append(f"{label}: <main> is {mask['top'] + mask['scrolled']:.1f}px from the top of the document; shell.js places the fade as if it were at the top")
        if mask["main"] == "none" or abs(mask["mainY"] - mask["scrolled"]) > 0.5:
            problems.append(f"{label}: <main> should fade out along the top with its mask {mask['main']!r} placed at the scroll offset "
                            f"({mask['scrolled']:.1f}px), but --fade-y is {mask['mainY']}")
        if mask["footer"] != "none" or mask["footerY"]:
            problems.append(f"{label}: the footer carries a mask ({mask['footer']!r}, --fade-y {mask['footerY']!r}); it cannot reach the top band, "
                            f"so only <main> fades")
        # What the browser scrolls to the top edge (a focused link, a #fragment's target) must land below the fade, not in
        # the band where it is invisible: a link a few pixels from the top that takes focus moves down to the page's own
        # top edge (scroll-padding-top, base.css).
        link = 'main .chapter p a[href="/app/"]'
        page.evaluate("(selector) => { const link = document.querySelector(selector); window.scrollTo(0, 0); window.scrollTo(0, link.getBoundingClientRect().top - 5); }", link)
        page.evaluate(NEXT_FRAMES)
        before = page.evaluate(LINK_IN_BAND, link)
        if before["top"] >= before["band"] - 2:  # the precondition: a link that was never in the band moves nowhere, and passes
            problems.append(f"{label}: the link is {before['top']:.0f}px from the top of the screen before it takes focus, not in the band "
                            f"({before['band']:.0f}px) where the page has faded out, so nothing was checked")
        page.evaluate("(selector) => document.querySelector(selector).focus()", link)
        page.evaluate(NEXT_FRAMES)
        focused = page.evaluate(LINK_IN_BAND, link)
        if focused["top"] < focused["band"] - 2:
            problems.append(f"{label}: a link that took focus is {focused['top']:.0f}px from the top of the screen, in the band where the page has "
                            f"faded out; it should be at least {focused['band']:.0f}px down (scroll-padding-top is the band and the fade)")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()
    return problems


# Where along the sheet's width it is looked at, as shares of the width, and how much of the paper the sheet shows there: it is drawn
# by the cloth (veil.js, spec 4.4) as paper mixed over the cloth by a share that rises linearly from 0 at the left edge to 1 at half the
# width (Earendil's mask: transparent at the left edge, solid from the middle), and <main> is masked out by the share that is left.
SHEET_SPOTS = (0.04, 0.12, 0.25, 0.38, 0.62, 0.85, 0.98)   # the last is near the right edge, as far from the mask's fade as the sheet goes
# The most the screen may differ from what the canvas and <main>'s mask should make of it, in luma (0 to 1): <main> is painted black in
# Paper and white in Ink for the look, so that what is left of it, from the canvas's own paper (about 0.92 in Paper, 0.10 in Ink) to
# that colour, is most of the luma range: a mask that leaves 1% of <main> in the right half is at least 0.009 off and fails. A correct
# sheet measures within 0.0025 in both appearances.
SHEET_LUMA_TOLERANCE = 0.006
# The most the canvas may differ from the blend of the cloth and the paper by the sheet's share, at a spot, in luma. The cloth is only
# ever about 0.1 of luma from the paper, so this is the check of the ramp's shape (an error of 5% of the share is 0.002 off), not of
# the right half's leak, which the next constant sees: a correct sheet measures within 0.001 (the grain's mean over the spot).
SHEET_BLEND_TOLERANCE = 0.003
# The right half of the sheet must be the paper and nothing of the cloth: with the cloth's variation magnified twelve times (u_cloth 12,
# which saturates most pixels to black or white) the frame must be the frame with no cloth at all (u_cloth 0), pixel for pixel, from just
# past the middle to the right edge, to within a mean of this in luma. A share of the magnified cloth showing through is that share of
# about 0.4: 0.1% of it is 0.0004, over the limit; a correct sheet reads 0.0000.
SHEET_LEAK = 0.0003
# The paper the right half shows is the appearance's --paper with the film grain on it: its mean luma within this of the token's (the
# shader's paper and the token round differently by 0.0003 in Ink), and its spread (the grain's: 0.0084 in Paper, 0.0061 in Ink over the whole
# canvas) between these.
SHEET_PAPER_TOLERANCE = 0.003
SHEET_GRAIN = (0.004, 0.012)
# What the sheet is made of while the cloth draws it: what the shader was asked for (u_sheet), what CSS leaves of it (--sheet as <main> reads it, which
# it inherits from the root, the display control bottom right's opacity, <main>'s mask layers, the surface a pseudo-element of the sheet draws
# when the cloth does not) and the markers.
SHEET_LAYERS = """() => { const sheet = document.querySelector('#shell-menu'), main = document.querySelector('main'), fx = document.querySelector('.shell-corner--bottom-right');
  const surface = getComputedStyle(sheet, '::before'), root = document.documentElement;
  return { shader: window.__cloth ? window.__cloth.uniforms.u_sheet : null, main: getComputedStyle(main).getPropertyValue('--sheet').trim(), fx: getComputedStyle(fx).opacity,
           surface: surface.display, content: surface.content, mask: getComputedStyle(main).maskImage, maskLayers: (getComputedStyle(main).maskImage.match(/linear-gradient/g) || []).length,
           veil: root.dataset.veil || null, motion: root.getAttribute('data-motion') }; }"""
# How much two frames kept with GRAIN_KEEP differ, on average, in the columns from a share of the canvas's width to another.
REGION_CHANGE = """([a, b, from, to]) => { const { w, h } = window.__cloth.last, first = window.__cloth.kept[a], second = window.__cloth.kept[b];
  const x0 = Math.floor(w * from), x1 = Math.ceil(w * to); let sum = 0, n = 0;
  for (let j = 0; j < h; j++) for (let i = x0; i < x1; i++) { sum += Math.abs(second[j * w + i] - first[j * w + i]); n += 1; }
  return sum / n; }"""


def sheet_alpha(share):
    return min(1.0, share / 0.5)


def sheet_frame(page, name, **force):
    """Keep the next frame the cloth draws (as luma, GRAIN_KEEP) under name, drawn with the uniforms in force (u_cloth=12) in place of its own."""
    page.evaluate("(force) => { Object.assign(window.__cloth.force, force); }", force)
    capture(page)
    page.evaluate(GRAIN_KEEP, name)
    page.evaluate("(force) => { for (const key of Object.keys(force)) delete window.__cloth.force[key]; }", force)


def sheet_page(browser, base_url, session_key, problems, label, *, theme, motion="on", touch=False, frames=90, scripts=(FREEZE,), path="/about/", come_in=False):
    """A phone (375x667) on path with the cloth running and the menu sheet open and settled: its fade (shell.js' driver, which steps by the cloth's
    virtual frames here) has ended and the cloth has run `frames` frames (1.5 s at the default: the sheet's share has reached 1 in 0.7 s, and the
    current page's link has grown its wash, which takes 1.4 s), the pointer left in the margin where nothing answers it. With Motion off, the sheet the page draws alone. With come_in
    (the threshold) Come in is clicked first, so that the install line is on the page, as it is for most visitors. The context, the
    page and its console errors, or (None, None, None) with a problem when the cloth never started."""
    site_page = page_named(path)
    context, _ = cloth_context(browser, base_url, session_key, site_page, width=375, height=667, theme=theme, motion=motion, scripts=scripts, touch=touch)
    page, errors = open_page(context, base_url + path, warnings=True)
    if motion == "on":
        if not cloth_running(page):
            problems.append(f"{label}: the cloth never started")
            context.close()
            return None, None, None
        run_frames(page, 30)
    if come_in:
        page.locator("[data-come-in]").click()
        page.wait_for_function("() => { const box = document.querySelector('[data-install]'); return Boolean(box) && !box.hidden; }",
                               timeout=20000)  # Come in's fade-out is a real-time timer
        page.wait_for_function("() => document.getAnimations().length === 0", timeout=5000)   # the install line has finished fading in
    toggle = page.locator("header [data-menu-toggle]")
    toggle.tap() if touch else toggle.click()
    wait_sheet_in_place(page)
    page.mouse.move(4, 320)
    if motion == "on":
        run_frames(page, frames)
    return context, page, errors


# Every page the sheet is looked at on, besides what cloth_menu_sheet_problems looks at in full on /about/: the cloth draws the sheet and only
# <main>'s mask keeps the page out of its right half, so what each page puts in <main> (the threshold's line, the install line whose box
# blurs what lies behind it, Begin's code blocks and their Copy buttons, a plan's table) has to be taken away by it. (page, Come in clicked.) The
# catalog is one of them, though its page checks are the catalog part's: the sheet is the ink part's.
SHEET_COVER_PAGES = tuple((site_page, False) for site_page in CATALOG_PAGES + PAGES) + ((page_named("/"), True),)
# <main> and everything in it painted in the colour farthest from the paper, black in Paper and white in Ink: the words, the boxes of the
# children and of their pseudo-elements (the install box, a code block's, a Copy button's fill), borders, outlines and images' stand-ins.
SHEET_PAINT = """main, main *, main *::before, main *::after {{ background-color: {far} !important; background-image: none !important; color: {far} !important;
  -webkit-text-fill-color: {far} !important; border-color: {far} !important; outline-color: {far} !important; fill: {far} !important; stroke: {far} !important;
  text-shadow: none !important; box-shadow: none !important; }}"""
# The most the right half of the screen may differ, in luma (0 to 1), between <main> painted and <main> hidden: on average over all of it,
# and over the 16 by 16 pixels where it is greatest. They are 0 when nothing of <main> shows (the cloth is frozen, so the same frame is drawn
# twice; Chromium and Playwright's WebKit both read 0.0000 on every page). A mask that leaves 1% of <main> measures 0.0105 on average and 0.0118
# in its worst block; a box that shows through alone, such as the install box or a code block, 0.05 to 0.5 on average and 0.9 in its block;
# a leaking element of 4 by 4 pixels, 0.0001 on average (under the first limit) and 0.056 in its block (over the second).
SHEET_COVER_MEAN, SHEET_COVER_BLOCK = 0.0005, 0.01


def sheet_cover_problems(page, label, theme):
    """Nothing of this page's <main> shows in the right half of the open sheet while the cloth draws it (spec 4.4), where <main> alone is
    masked out of it and the CSS surface that used to lie over everything is not drawn. <main> and all it holds are painted black (Paper) or
    white (Ink); the screen's right half, from just past the middle to the edge, must equal the screen with <main> hidden, pixel for pixel
    (SHEET_COVER_MEAN, SHEET_COVER_BLOCK), at the top of the page and, where it scrolls, at its end, which is where the bottom corners wait;
    the display control bottom right has faded; and <main> is painted at the left edge (the mask's fade), or nothing was compared. The strip
    between the middle and the links shows what the canvas has there. The cloth draws every frame (the checks' probe skips the drawing
    otherwise, and the screen would keep an old one), held still."""
    problems = sheet_layer_problems(page, label)
    view = page.viewport_size
    right = {"x": view["width"] * 0.52, "y": 0, "width": view["width"] * 0.48, "height": view["height"]}
    edge = {"x": 0, "y": 0, "width": view["width"] * 0.1, "height": view["height"]}
    strip = {"x": view["width"] * 0.5 + 8, "y": 200, "width": 28, "height": 400}
    far = "#000" if theme == "light" else "#fff"
    page.add_style_tag(content=SHEET_PAINT.format(far=far))
    capture(page)
    page.evaluate("() => { window.__cloth.drawAll = true; }")
    end = page.evaluate("() => document.documentElement.scrollHeight - innerHeight")
    seen_control = False
    for y in sorted({0, max(0, end)}):
        page.evaluate("(y) => window.scrollTo(0, y)", y)
        page.evaluate(NEXT_FRAMES)   # shell.js moves the top fade with the scroll, once a frame
        where = f"{label}, scrolled to {y}px" if y else label
        shown, shown_edge = page.screenshot(clip=right), page.screenshot(clip=edge)
        page.evaluate("() => { document.querySelector('main').style.visibility = 'hidden'; }")
        hidden, hidden_edge = page.screenshot(clip=right), page.screenshot(clip=edge)
        page.evaluate("() => { document.querySelector('main').style.visibility = ''; }")
        mean, worst = png_difference(shown, hidden)
        if mean > SHEET_COVER_MEAN or worst > SHEET_COVER_BLOCK:
            problems.append(f"{where}: <main> shows in the right half of the sheet: with it painted and with it hidden the screen differs by {mean:.4f} of luma on average "
                            f"(the limit is {SHEET_COVER_MEAN}) and by {worst:.4f} over its worst 16 by 16 pixels (the limit is {SHEET_COVER_BLOCK}); the mask on <main> "
                            f"is all that keeps the page out of the sheet, and it should take everything in <main> away from the middle on")
        if png_difference(shown_edge, hidden_edge)[0] < 0.2:
            problems.append(f"{where}: painting <main> changes the left edge of the screen by under 0.2 of luma, so nothing was compared")
        controls = page.evaluate(BOX_RECT, ".shell-corner--bottom-right")
        if controls:
            seen_control = True
            page.evaluate(HIDE_LINKS, True)   # the sheet's last links can lie over the control's box: its words are not the control
            capture(page)
            on_canvas = canvas_stats(page, controls)["luma"]
            have, _ = png_luma(page.screenshot(clip=controls))
            page.evaluate(HIDE_LINKS, False)
            if abs(have - on_canvas) > SHEET_LUMA_TOLERANCE:
                problems.append(f"{where}: the display control bottom right shows through the right half of the sheet: its box reads {have:.3f} of luma on the screen "
                                f"where the canvas has {on_canvas:.3f}; it should have faded with the sheet")
    if not seen_control:
        problems.append(f"{label}: the display control bottom right was out of the screen at the top of the page and at its end, so it was not checked")
    page.evaluate("() => window.scrollTo(0, 0)")
    page.evaluate(NEXT_FRAMES)
    capture(page)
    on_canvas = canvas_stats(page, strip)["luma"]
    have, _ = png_luma(page.screenshot(clip=strip))
    if abs(have - on_canvas) > SHEET_LUMA_TOLERANCE:
        problems.append(f"{label}: the strip of the sheet between the middle and the links reads {have:.3f} of luma on the screen where the canvas has {on_canvas:.3f}: "
                        f"something is painted over the paper")
    page.evaluate("() => { window.__cloth.drawAll = false; }")
    return problems


def sheet_layer_problems(page, label):
    """The open sheet is drawn by the cloth and the page keeps none of it in CSS (SHEET_LAYERS): the shader was asked for the sheet (u_sheet 1),
    <main> is masked out of the right half (--sheet 1), the display control bottom right has faded with it and the CSS surface is not drawn."""
    problems = []
    layers = page.evaluate(SHEET_LAYERS)
    if layers["shader"] != 1:
        problems.append(f"{label}: the shader was asked to draw the sheet at {layers['shader']}, not 1: the cloth should draw it ({layers})")
    if layers["main"] != "1" or layers["fx"] != "0" or layers["maskLayers"] != 2:
        problems.append(f"{label}: <main>'s --sheet is {layers['main']!r}, the display control's opacity {layers['fx']} and <main> has {layers['maskLayers']} mask layers; "
                        f"with the sheet open and drawn by the cloth they should be 1, 0 and 2 (the top fade and the mask that takes <main> out of the right half, "
                        f"the control faded with it)")
    if layers["surface"] != "none":
        problems.append(f"{label}: the sheet's CSS surface is still drawn (display {layers['surface']}) while the cloth draws the sheet; it is the fallback for when it does not")
    return problems


def cloth_menu_sheet_problems(browser, base_url, session_key):
    """The phone's menu sheet is drawn by the cloth while it runs (spec 4.4): at 375x667, in Paper and in Ink, with the menu open and the
    cloth held still, (1) the shader draws the sheet (u_sheet 1) and the page keeps none of it in CSS: <main> is masked out of the right
    half (--sheet 1), the display control bottom right has faded with it and the sheet's CSS surface (a pseudo-element) is not drawn;
    (2) the right half is the paper and the film grain, from the canvas: a patch of it between the middle and the links reads the
    appearance's --paper in mean luma and the grain's spread, and nothing of the cloth shows through it (the frame with the cloth magnified
    twelve times equals the frame with no cloth, SHEET_LEAK); (3) the left half shows the scene, the cloth blended with the paper by
    the share that rises linearly from the left edge to the middle (SHEET_BLEND_TOLERANCE); (4) the screen shows what the canvas does, <main>
    painted black or white behind its words so that any share of it left is a difference: the mask over <main> is the sheet's mirror
    (SHEET_LUMA_TOLERANCE: a right half that lets 1% of <main> show through fails), and the display control is gone from the right half;
    MENU is still above all. Then the fallbacks, in which the CSS draws the sheet as it always has: with Motion off, where the surface
    is the page's ground (paper and its grain, at its size) and nothing is washed, and when the cloth's context is lost with the sheet
    open, where the surface comes back at once and <main> is not masked. Last, every page of PAGES and the threshold after Come in: what
    each puts in <main> has only the mask to keep it out of the right half now (sheet_cover_problems)."""
    problems = []
    width, height = 375, 667
    # Between the middle and the links' left edges (the widest, How it works, starts at 230): no link and no wash lies there.
    strip = {"x": width * 0.5 + 8, "y": 200, "width": 28, "height": 400}
    spots = [{"x": share * width - 4, "y": height / 2 - 60, "width": 8, "height": 120} for share in SHEET_SPOTS]
    for theme, appearance in (("light", "Paper"), ("dark", "Ink")):
        label = f"/about/ cloth at {width}px, {appearance}, the open menu sheet"
        context, page, errors = sheet_page(browser, base_url, session_key, problems, label, theme=theme)
        if not page:
            continue
        starts = page.evaluate("() => [...document.querySelectorAll('#shell-menu a')].map((link) => [link.textContent.trim(), link.getBoundingClientRect().left])")
        problems += [f"{label}: the link {name!r} starts at {left:.0f}px, left of the middle ({width / 2:.0f}px) where the sheet is not yet solid"
                     for name, left in starts if left < width / 2]
        if min(left for _, left in starts) < strip["x"] + strip["width"] + 4:
            problems.append(f"{label}: a link starts at {min(left for _, left in starts):.0f}px, in the strip {strip['x']:.0f} to {strip['x'] + strip['width']:.0f}px "
                            f"that is read for the paper, so nothing was checked")
        problems += sheet_layer_problems(page, label)
        # (2) the right half is paper and grain, from the canvas, and none of the cloth
        paper = gamma_luma(page.evaluate(TOKEN, "--paper"))
        capture(page)
        patch = canvas_stats(page, strip)
        if abs(patch["luma"] - paper) > SHEET_PAPER_TOLERANCE:
            problems.append(f"{label}: the right half reads {patch['luma']:.4f} of luma on the canvas where --paper is {paper:.4f}: it should be the paper")
        if not SHEET_GRAIN[0] <= patch["spread"] <= SHEET_GRAIN[1]:
            problems.append(f"{label}: the right half's spread on the canvas is {patch['spread']:.4f} of luma; the film grain on the paper reads {SHEET_GRAIN[0]} to {SHEET_GRAIN[1]}")
        sheet_frame(page, "cloth", u_cloth=12)
        sheet_frame(page, "bare", u_cloth=0)
        if page.evaluate(REGION_CHANGE, ["cloth", "bare", 0.0, 0.4]) < 0.02:
            problems.append(f"{label}: magnifying the cloth twelve times changes the left of the canvas by under 0.02 of luma, so nothing was checked")
        leak = page.evaluate(REGION_CHANGE, ["cloth", "bare", 0.52, 1.0])
        if leak > SHEET_LEAK:
            problems.append(f"{label}: the cloth shows through the right half of the sheet: with it magnified twelve times the frame differs from the frame with none "
                            f"by {leak:.4f} of luma on average (the limit is {SHEET_LEAK}); the sheet should be the paper there, a share of 1")
        # (3) the left half: the cloth blended with the paper by the sheet's share
        capture(page)
        drawn = [canvas_stats(page, spot)["luma"] for spot in spots]
        page.evaluate("() => { window.__cloth.force.u_sheet = 0; }")
        capture(page)
        bare = [canvas_stats(page, spot)["luma"] for spot in spots]
        page.evaluate("() => { delete window.__cloth.force.u_sheet; }")
        for share, have, cloth in zip(SHEET_SPOTS, drawn, bare):
            alpha = sheet_alpha(share)
            want = (1 - alpha) * cloth + alpha * paper
            if abs(have - want) > SHEET_BLEND_TOLERANCE:
                problems.append(f"{label}: at {share:.0%} across, mid-height, the canvas reads {have:.4f} of luma where the sheet should leave {want:.4f}: the cloth "
                                f"({cloth:.4f}) blended with the paper ({paper:.4f}) by {alpha:.0%}; the sheet fades out across the left half, nothing at the left edge, solid from the middle")
        # (4) the screen: <main> is painted behind its words in the colour farthest from the paper, and its words, the links and MENU's
        # neighbours are hidden, so what the screen shows over the canvas is only what the mask leaves of that colour
        page.add_style_tag(content=f"main {{ background: {'#000' if theme == 'light' else '#fff'} !important; }} main > *, #shell-menu a {{ visibility: hidden !important; }}")
        behind = 0.0 if theme == "light" else 1.0
        capture(page)
        page.evaluate("() => { window.__cloth.drawAll = true; }")
        page.evaluate(NEXT_FRAMES)
        for share, spot in zip(SHEET_SPOTS, spots):
            cloth = canvas_stats(page, spot)["luma"]
            have, _ = png_luma(page.screenshot(clip=spot))
            kept = 1 - sheet_alpha(share)   # what the mask leaves of <main>
            want = (1 - kept) * cloth + kept * behind
            if abs(have - want) > SHEET_LUMA_TOLERANCE:
                problems.append(f"{label}: at {share:.0%} across, mid-height, the screen shows {have:.3f} of luma where the sheet should show {want:.3f}: the canvas "
                                f"({cloth:.3f}) with {kept:.0%} of <main> ({behind:.0f}) over it; <main> is masked out as the sheet comes in, nothing left from the middle")
        controls = page.evaluate(BOX_RECT, ".shell-corner--bottom-right")
        if not controls:
            problems.append(f"{label}: the display control bottom right is out of the screen, so it was not checked")
        else:
            on_canvas = canvas_stats(page, controls)["luma"]
            have, _ = png_luma(page.screenshot(clip=controls))
            if abs(have - on_canvas) > SHEET_LUMA_TOLERANCE:
                problems.append(f"{label}: the display control bottom right shows through the right half of the sheet: its box reads {have:.3f} of luma on the screen "
                                f"where the canvas has {on_canvas:.3f}; it should have faded with the sheet")
        page.evaluate("() => { window.__cloth.drawAll = false; }")
        top = page.evaluate("() => { const toggle = document.querySelector('header [data-menu-toggle]'), b = toggle.getBoundingClientRect(); return document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2) === toggle; }")
        if not top:
            problems.append(f"{label}: MENU is not on top of the sheet")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

        # The fallbacks: whatever stops the cloth draws the sheet as it always was, in CSS.
        for how in ("Motion off", "a lost context"):
            label = f"/about/ cloth at {width}px, {appearance}, the open menu sheet, {how}"
            context, page, errors = sheet_page(browser, base_url, session_key, problems, label, theme=theme, motion="off" if how == "Motion off" else "on", frames=30)
            if not page:
                continue
            if how == "a lost context":
                page.evaluate("() => window.__cloth.gl.getExtension('WEBGL_lose_context').loseContext()")
                page.wait_for_function("() => !document.documentElement.dataset.veil", timeout=10000)
            layers = page.evaluate(SHEET_LAYERS)
            if layers["surface"] == "none" or layers["content"] in ("none", "normal"):
                problems.append(f"{label}: the sheet's CSS surface is not drawn (display {layers['surface']}, content {layers['content']}); without the cloth it is the sheet")
            if layers["main"] != "1" or layers["fx"] != "1" or layers["maskLayers"] != 1:
                problems.append(f"{label}: <main>'s --sheet is {layers['main']!r} (the sheet's links read it: 1), the display control's opacity {layers['fx']} and <main> has "
                                f"{layers['maskLayers']} mask layers; without the cloth the control stays 1 and <main> keeps its top fade alone, one layer (the CSS surface is the sheet)")
            if page.evaluate("() => document.querySelectorAll('canvas').length"):
                problems.append(f"{label}: a canvas is still on the page")
            # The surface is the page's ground: the tile of this appearance, drawn at the size the page's own grain is.
            surface = page.evaluate("""() => { const sheet = document.querySelector('#shell-menu'), own = getComputedStyle(sheet, '::before'), page = getComputedStyle(document.documentElement);
                return { image: own.backgroundImage, size: own.backgroundSize, colour: own.backgroundColor,
                         pageImage: page.backgroundImage, pageSize: page.backgroundSize, pageColour: page.backgroundColor }; }""")
            if (surface["image"], surface["size"], surface["colour"]) != (surface["pageImage"], surface["pageSize"], surface["pageColour"]):
                problems.append(f"{label}: the sheet's surface is {surface['colour']} with {surface['image']} at {surface['size']}; it should be the page's ground, "
                                f"{surface['pageColour']} with {surface['pageImage']} at {surface['pageSize']}")
            have, _ = png_luma(page.screenshot(clip=strip))
            if abs(have - paper) > 0.02:
                problems.append(f"{label}: the right half of the sheet shows {have:.3f} of luma where the paper is {paper:.3f}; it should be solid")
            link = '#shell-menu a[href="/plans/"]'
            page.hover(link)
            page.wait_for_timeout(300)
            if page.evaluate(INKED, link) or page.evaluate(OWN_FILL, link) == "rgba(0, 0, 0, 0)":
                problems.append(f"{label}: a hovered link in the sheet should show the flat fill and no wash (is-inked {page.evaluate(INKED, link)}, "
                                f"fill {page.evaluate(OWN_FILL, link)})")
            problems += [f"{label}: console: {error}" for error in errors]
            context.close()

    # The other pages, and the threshold after Come in: what lies in <main> there has only the mask to keep it out of the right half.
    for theme, appearance in (("light", "Paper"), ("dark", "Ink")):
        for site_page, come_in in SHEET_COVER_PAGES:
            label = f"{site_page.path}{' after Come in' if come_in else ''} cloth at {width}px, {appearance}, the open menu sheet"
            context, page, errors = sheet_page(browser, base_url, session_key, problems, label, theme=theme, path=site_page.path, come_in=come_in, frames=30)
            if not page:
                continue
            problems += sheet_cover_problems(page, label, theme)
            problems += [f"{label}: console: {error}" for error in errors]
            context.close()
    return problems


# The least a wash changes the luma of the canvas under its inner patch for it to be seen: the wash is 0.10 of the ink's colour over the
# body and 0.26 at the rim (veil.js), so 0.07 to 0.1 of luma in either appearance on paper; a link that is washed reads far over this.
SHEET_INK_SEEN = 0.03
HIDE_LINKS = "(hidden) => document.querySelectorAll('#shell-menu a, header [data-menu-toggle]').forEach((link) => { link.style.visibility = hidden ? 'hidden' : ''; })"


def wash_seen_problems(page, label, selector, what):
    """The wash under selector is on the open sheet, in the canvas and on the screen: over the inner patch of its ellipse the frame
    drawn with the washes differs from the frame drawn without them by SHEET_INK_SEEN of luma or more, and with the links hidden (their
    words are not the wash) the screen shows what the canvas does there, within SHEET_LUMA_TOLERANCE: nothing of the sheet, <main> or the
    display control covers it."""
    problems = []
    patch = page.evaluate(INK_PATCH, selector)
    capture(page)
    washed = canvas_stats(page, patch)["luma"]
    page.evaluate("() => { window.__cloth.noInk = true; }")
    capture(page)
    bare = canvas_stats(page, patch)["luma"]
    page.evaluate("() => { window.__cloth.noInk = false; }")
    if abs(washed - bare) < SHEET_INK_SEEN:
        problems.append(f"{label}: {what} changes the canvas by only {abs(washed - bare):.3f} of luma over its patch ({washed:.3f} with the wash, {bare:.3f} without); it should show")
    capture(page)
    page.evaluate(HIDE_LINKS, True)
    page.evaluate("() => { window.__cloth.drawAll = true; }")
    page.evaluate(NEXT_FRAMES)
    on_canvas = canvas_stats(page, patch)["luma"]
    have, _ = png_luma(page.screenshot(clip=patch))
    page.evaluate("() => { window.__cloth.drawAll = false; }")
    page.evaluate(HIDE_LINKS, False)
    if abs(have - on_canvas) > SHEET_LUMA_TOLERANCE:
        problems.append(f"{label}: {what} reads {on_canvas:.3f} of luma on the canvas and {have:.3f} on the screen; something is painted over it")
    return problems


def cloth_sheet_ink_problems(browser, base_url, session_key):
    """The ink shows on the phone's menu sheet (spec 3.4, 4.4), at 375x667 in Paper and in Ink. With the sheet open: the current
    page's link holds its wash (a full wash, is-inked, no flat fill) and it is on the screen; a link the pointer is on takes a wash
    that grows to full, as does one the keyboard focus is on, and a finger's tap on a link (made as a touch, its navigation cancelled) blooms
    one that fades as the finger lifts, each on the sheet and with no flat fill. After the sheet closes (a tap on its left half) the
    held wash is let go: it lingers on the cloth where the link was, visible a short moment later, and fades over 1.8 s; the sheet's
    share falls to 0 over its 0.7 s. A tap on a link that changes the page in place closes the sheet like any close (the shader's share falls through
    frames between 0 and 1 over its 0.7 s, and <main>'s mask with it; only a change that cross-fades, a link in the page or Back, takes the sheet away
    at once) and lets the wash go the same way: it fades over 1.8 s from the strength it had, not cut. With Motion off nothing is washed (cloth_menu_sheet_problems has the fallback)."""
    problems = []
    held = '#shell-menu a[aria-current="page"]'
    other = '#shell-menu a[href="/plans/"]'
    third = '#shell-menu a[href="/how/"]'
    flat = lambda page, selector: page.evaluate(OWN_FILL, selector) != "rgba(0, 0, 0, 0)"
    for theme, appearance in (("light", "Paper"), ("dark", "Ink")):
        label = f"/about/ cloth at 375px, {appearance}, the open menu sheet, ink"
        context, page, errors = sheet_page(browser, base_url, session_key, problems, label, theme=theme, frames=120)
        if not page:
            continue
        # The current page's link holds its wash.
        if wash_strength(page, held) < 0.99 or not page.evaluate(INKED, held) or flat(page, held):
            problems.append(f"{label}: the current page's link should hold a full wash and no flat fill (strength {wash_strength(page, held):.2f}, "
                            f"is-inked {page.evaluate(INKED, held)}, flat fill {flat(page, held)})")
        problems += wash_seen_problems(page, label, held, "the held wash on the current page's link")
        place = page.evaluate("""(selector) => { const b = document.querySelector(selector).getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; }""", held)
        # A link the pointer is on.
        page.hover(other)
        run_frames(page, 90)
        if wash_strength(page, other) < 0.99 or not page.evaluate(INKED, other) or flat(page, other):
            problems.append(f"{label}: a hovered link should take a full wash and no flat fill (strength {wash_strength(page, other):.2f}, "
                            f"is-inked {page.evaluate(INKED, other)}, flat fill {flat(page, other)})")
        problems += wash_seen_problems(page, label, other, "the wash under a hovered link")
        if wash_strength(page, held) < 0.99:
            problems.append(f"{label}: the current page's wash gave way when another link was hovered (strength {wash_strength(page, held):.2f}); it is held")
        page.mouse.move(4, 320)
        run_frames(page, 150)
        if wash_strength(page, other) > 0 or page.evaluate(INKED, other) or flat(page, other):
            problems.append(f"{label}: the hovered link's wash should have faded 2.5 s after the pointer left, with no flat fill in its place (strength "
                            f"{wash_strength(page, other):.2f}, is-inked {page.evaluate(INKED, other)}, flat fill {flat(page, other)})")
        # Keyboard focus: MENU has it after the click; Tab goes to the first link, the current page's, then the second.
        page.locator("header [data-menu-toggle]").focus()
        page.keyboard.press("Tab")
        page.keyboard.press("Tab")
        run_frames(page, 90)
        focused = page.evaluate("() => { const a = document.activeElement; return a && a.matches('#shell-menu a') ? a.getAttribute('href') : null; }")
        if focused != "/how/" or not page.evaluate(INKED, third) or wash_strength(page, third) < 0.99 or flat(page, third):
            problems.append(f"{label}: keyboard focus on the second link ({focused}) should grow a full wash and no flat fill (strength {wash_strength(page, third):.2f}, "
                            f"is-inked {page.evaluate(INKED, third)}, flat fill {flat(page, third)})")
        else:
            problems += wash_seen_problems(page, label, third, "the wash under the link keyboard focus is on")
        page.evaluate("() => document.activeElement.blur()")
        # The sheet closes with a tap on its left half: the held wash lingers on the cloth, behind where the sheet was.
        run_frames(page, 150)
        before = page.evaluate("() => window.__cloth.frames")
        page.mouse.click(40, 333)
        run_frames(page, 13)   # the close's first frame has no time behind it (shell.js), so 12 frames of 1/60 s are 200 ms of its 0.7 s
        mid = page.evaluate("() => window.__cloth.uniforms.u_sheet")
        if mid is None or not 0.05 < mid < 0.95:
            problems.append(f"{label}: 12 frames (200 ms) into its 0.7 s close the sheet's share is {mid}; it should be falling, not gone or still full")
        run_frames(page, 17)   # half a second in all
        # The wash is where its link was when the link went. The groups of links rise 6px as the sheet fades out (menu.css) and the wash follows
        # the link in the frames that draw it, so it ends somewhere between the link's place and 6px above it, wherever the last frame that
        # drew the link fell (frames 100 ms apart, as a slow runner's, leave it a few px up). The strength is the thing read: it
        # does not depend on where. A wash lost, held, or on another link (they are 45px apart) is still found out.
        history = page.evaluate(WASH_HISTORY_AT, [place, before, 6])
        lingering = history[-1][1] if history else 0
        if not 0.5 < lingering < 0.95:
            problems.append(f"{label}: half a second after the sheet closed the held wash should be fading where the link was, at about 0.7 of its strength, not gone or "
                            f"still held; it is {lingering:.2f}")
        if page.evaluate(INKED, held):
            problems.append(f"{label}: the link still carries is-inked after the sheet closed; the wash is let go")
        patch = {"x": place[0] - 30, "y": place[1] - 14, "width": 60, "height": 28}
        capture(page)
        washed = canvas_stats(page, patch)["luma"]
        page.evaluate("() => { window.__cloth.noInk = true; }")
        capture(page)
        bare = canvas_stats(page, patch)["luma"]
        page.evaluate("() => { window.__cloth.noInk = false; }")
        if abs(washed - bare) < SHEET_INK_SEEN * 0.7:
            problems.append(f"{label}: half a second after the sheet closed the lingering wash changes the cloth by only {abs(washed - bare):.3f} of luma where the link was; it should still show")
        run_frames(page, 20)   # 0.83 s in all: the close (0.7 s) is over
        if page.evaluate("() => window.__cloth.uniforms.u_sheet") != 0 or page.evaluate("() => document.querySelector('#shell-menu').classList.contains('is-open')"):
            displayed = page.evaluate("() => getComputedStyle(document.querySelector('#shell-menu')).display !== 'none'")   # read as it is: SHEET_IN_PLACE keeps a count
            problems.append(f"{label}: 0.83 s after the sheet closed its share is {page.evaluate('() => window.__cloth.uniforms.u_sheet')} and it is "
                            f"{'still' if displayed else 'no longer'} displayed; the share should be 0 and the sheet gone")
        sheet_closed = page.evaluate(SHEET_LAYERS)
        if sheet_closed["main"] != "0" or sheet_closed["fx"] != "1":
            problems.append(f"{label}: after the sheet closed <main>'s --sheet is {sheet_closed['main']!r} and the display control's opacity {sheet_closed['fx']}; they are back to 0 and 1")
        run_frames(page, 100)   # 2.5 s after the close: gone
        if page.evaluate(INKS_DRAWN):
            problems.append(f"{label}: 2.5 s after the sheet closed a wash is still drawn ({page.evaluate(INKS_DRAWN)})")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    # A finger's tap on a link in the sheet (navigation cancelled): a wash blooms under it and fades as the finger lifts.
    for theme, appearance in (("light", "Paper"), ("dark", "Ink")):
        label = f"/about/ cloth at 375px, {appearance}, a tap on a link in the open menu sheet"
        context, page, errors = sheet_page(browser, base_url, session_key, problems, label, theme=theme, touch=True, frames=60)
        if not page:
            continue
        page.evaluate("() => document.addEventListener('click', (event) => { if (event.target.closest('#shell-menu a')) event.preventDefault(); }, true)")
        if not page.evaluate("() => matchMedia('(pointer: coarse)').matches"):
            problems.append(f"{label}: the pointer is not coarse, so a touch was not tried")
        page.locator(other).tap()
        run_frames(page, 10)
        strength = wash_strength(page, other)   # the fill the finger's :active leaves for a moment as it lifts is every control's, and fades in .15 s
        if strength < 0.8:
            problems.append(f"{label}: a tap on a link in the sheet should bloom a wash under it that is just beginning to fade (strength {strength:.2f})")
        else:
            problems += wash_seen_problems(page, label, other, "the wash a tap blooms under a link")
        run_frames(page, 150)
        if wash_strength(page, other) > 0 or page.evaluate(INKED, other) or flat(page, other):
            problems.append(f"{label}: 2.5 s after the tap the wash should have faded and no flat fill taken its place (strength {wash_strength(page, other):.2f}, "
                            f"is-inked {page.evaluate(INKED, other)}, flat fill {flat(page, other)})")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    # A tap on a link that changes the page in place: the sheet closes by its driver like any close and the old content fades out with it (no view
    # transition, and the old page never reappears at full strength), then the new content fades in; the wash is let go and fades.
    label = "/about/ cloth at 375px, Paper, a tap on a menu link that changes the page in place"
    context, page, errors = sheet_page(browser, base_url, session_key, problems, label, theme="light", touch=True, frames=60, scripts=(FREEZE, CHANGE_PROBE))
    if page:
        # Every frame from the tap on, read after shell.js and the cloth have stepped in it: this listener is later than shell.js' own on the document, so
        # the frame callbacks it asks for run after the driver's (the cloth steps the sheet first, then the driver, then this reads).
        page.evaluate("""() => { window.__fall = [];
            const record = () => { const main = document.querySelector('main'), style = getComputedStyle(main);
              window.__fall.push({ shader: window.__cloth.uniforms.u_sheet, main: Number(style.getPropertyValue('--sheet')), opacity: Number(style.opacity),
                                   shown: getComputedStyle(document.querySelector('#shell-menu')).display !== 'none',
                                   heading: (main.querySelector('h1') || {}).textContent });
              requestAnimationFrame(record); };
            document.addEventListener('click', (event) => { if (event.target.closest('#shell-menu a')) { window.__fall = []; requestAnimationFrame(record); } }); }""")
        box = page.evaluate(BOX_RECT, other)
        place = [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2]
        page.evaluate("() => { const c = window.__cloth; c.clock.step = 100; c.clock.stopAt = Infinity; }")   # cloth time runs in frames of 0.1 s from the tap on
        since = page.evaluate("() => window.__cloth.frames")
        page.locator(other).tap()
        page.wait_for_function("() => location.pathname === '/plans/'", timeout=20000)
        run_frames(page, 30, step=100)   # 3 s of cloth time more
        fall = page.evaluate("() => window.__fall")
        old, new = "A quiet place for what your agents make", "Plans"
        closing = [frame for frame in fall if frame["heading"] == old]
        between = [frame["shader"] for frame in closing if 0.02 < frame["shader"] < 0.98]
        if len(between) < 2:
            problems.append(f"{label}: the shader's share shows {len(between)} frames between 0 and 1 while the sheet closes after the tap; it should fade like any close, "
                            f"not be taken away at once ({[round(frame['shader'], 2) for frame in closing]})")
        shares = [frame["shader"] for frame in closing]
        if any(later > earlier + 0.001 for earlier, later in zip(shares, shares[1:])) or any(abs(frame["shader"] - frame["main"]) > SHEET_SAME_VALUE for frame in fall):
            problems.append(f"{label}: the shader's share should fall and not rise, and equal <main>'s --sheet in the same frame ({[(round(frame['shader'], 2), round(frame['main'], 2)) for frame in closing]})")
        if any(abs(frame["opacity"] - frame["main"]) > 0.001 for frame in closing):
            problems.append(f"{label}: the old content should fade out with the sheet, at the sheet's own value ({[(round(frame['opacity'], 2), round(frame['main'], 2)) for frame in closing]})")
        if any(frame["opacity"] > 0.999 for frame in closing[2:]):
            problems.append(f"{label}: the old content shows at full strength after the tap's first frame ({[round(frame['opacity'], 2) for frame in closing]})")
        arriving = [frame["opacity"] for frame in fall if frame["heading"] == new]
        if len([value for value in arriving if 0.02 < value < 0.98]) < 2 or arriving[-1:] != [1.0]:
            problems.append(f"{label}: the new content should fade in over frames in between and end at 1 ({[round(value, 2) for value in arriving]})")
        if page.evaluate("() => window.__change.calls.length"):
            problems.append(f"{label}: a view transition began; a change from the sheet fades by its own driver")
        if any(frame["shown"] for frame in fall if frame["heading"] == new):
            problems.append(f"{label}: the sheet is still displayed when the new content shows")
        # The tapped link rises with its group as the sheet closes (6px over the close, menu.css) and its wash with it, so the wash is read anywhere
        # up to 6px above the link's place, as for the held wash above: within 2px of it only the tap's frame and the next qualify at the close's
        # pace (1.2 px, then 2.8 px up), and a runner whose first frame with the wash comes one later read no wash at all (the links are 45px apart).
        history = page.evaluate(WASH_HISTORY_AT, [place, since, 6])
        strengths = [strength for _, strength in history]
        seen = [strength for strength in strengths if strength > 0]
        if not seen or max(seen) < 0.9:
            drawn = page.evaluate(WASHES_FROM, [since, 12])
            problems.append(f"{label}: the tapped link never showed a wash of its own ({max(seen) if seen else 0:.2f} at most); the link was at "
                            f"{[round(value) for value in place]}, {page.evaluate('() => window.__cloth.history.length')} frames recorded, and the washes "
                            f"drawn from the frame before the tap were [frame, [[x, y, strength], ...]] {drawn}")
        elif any(later > earlier + 0.001 for earlier, later in zip(seen, seen[1:])):
            problems.append(f"{label}: the wash grew again after it began to fade: {[round(value, 2) for value in seen]}")
        elif any(earlier - later > 0.25 for earlier, later in zip(seen, seen[1:])) or (strengths and strengths[-1] > 0.02):
            problems.append(f"{label}: the wash should fade over 1.8 s from the strength it had (steps of 0.1 s are 0.06 of it), not be cut, and be gone 3 s on: "
                            f"{[round(value, 2) for value in strengths]}")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()
    return problems


def cloth_sheet_motion_problems(browser, base_url, session_key):
    """The sheet's one value (spec 4.4) as the cloth page shows it, frame by frame on the cloth's virtual clock (frames of 1/60 s, the probe lets it
    run while a check reads and holds it still after): shell.js steps --sheet once per drawn frame, 700 ms opening and closing, and the links,
    the paper the cloth draws, <main>'s mask and the display control read that one value in the same frame. sheet_fade_findings (the pages part's
    sheet_fade_problems reads the same on a page with no cloth) replays the four fades (open, close, a close that interrupts the opening, an
    opening that interrupts the close) against the driver's own model at every
    frame and wants, in the same frame, the shader's share (u_sheet) EQUAL to --sheet (the paper moves in lockstep with the links and the page's
    fade-out: reading --sheet in the cloth's own frame would give it the last frame's value, which the cloth is handed the driver's step to avoid),
    <main> reading the same --sheet, the display control at 1 minus it and the links at their place by it. Here the opening and the closing are also
    held to Come in's own pace, ease in 700 ms rising and falling, whatever the driver's curve: the share at each frame lies within SHEET_EASE_ERROR
    (and a frame's worth of slack) of the CSS curve at the time since the fade's first frame, and shows at least five frames between 0 and 1."""
    problems = []
    frame_ms = 1000 / 60
    label = "/about/ cloth at 375px, the sheet's value, frame by frame"
    context, _ = cloth_context(browser, base_url, session_key, page_named("/about/"), width=375, height=667, scripts=(FREEZE,))
    page, errors = open_page(context, base_url + "/about/", warnings=True)
    if not cloth_running(page):
        problems.append(f"{label}: the cloth never started")
        context.close()
        return problems
    run_frames(page, 30)
    page.mouse.move(4, 320)
    clock_runs = "() => { const c = window.__cloth; c.clock.step = %f; c.clock.stopAt = Infinity; }" % frame_ms   # the virtual clock runs while a probe reads
    page.evaluate(clock_runs)
    for mode, rest in (("open", False), ("close", True)):
        where = f"{label}, {mode}"
        if not page.evaluate(SHEET_AT_REST, rest):
            problems.append(f"{where}: the sheet did not come to rest {'open' if rest else 'shut'}, so nothing was checked")
            continue
        samples = page.evaluate(FADE_PROBE, [mode, FADE_BOUND_MS, 0, "display"])["samples"]
        frames = [sample for sample in samples if sample["mark"] == "frame"]
        between = [sample for sample in frames if 0.02 < sample["share"] < 0.98]
        if len(between) < 5:
            problems.append(f"{where}: the share is between 0 and 1 for {len(between)} frames; at 60 frames a second it should be five or more: "
                            f"{[round(sample['share'], 3) for sample in frames[:12]]}")
        origin = frames[0]["t"]   # the fade's first frame, which has no time behind it
        for sample in frames:
            share = min(1.0, (sample["t"] - origin) / 700)
            true = SHEET_EASE(share) if mode == "open" else 1 - SHEET_EASE(share)
            if abs(sample["share"] - true) > SHEET_EASE_ERROR + 0.004:
                problems.append(f"{where}: {sample['t'] - origin:.0f} ms into the fade the share is {sample['share']:.3f}; "
                                f"ease {'rising' if mode == 'open' else 'falling'} makes {true:.3f}, and the driver's curve may lie {SHEET_EASE_ERROR} from it")
                break
    problems += sheet_fade_findings(page, label, cloth=True)
    page.evaluate(FREEZE)
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()

    # From 64rem up the open menu is a column in the margin, not a sheet: the cloth draws none of it. shell.js steps its value to 1 for an open menu
    # at any width (the column fades by the same driver as the sheet; the cloth's own guard, SHEETED, keeps the paper off), so the cloth, which
    # draws the paper of the value it is given, must not be given it there: the paper would cover the left half of the cloth whenever the menu is open.
    label = "/about/ cloth at 1024px, the open menu column"
    context, _ = cloth_context(browser, base_url, session_key, page_named("/about/"), width=1024, height=640, scripts=(FREEZE,))
    page, errors = open_page(context, base_url + "/about/", warnings=True)
    if not cloth_running(page):
        problems.append(f"{label}: the cloth never started")
    else:
        run_frames(page, 30)
        page.locator("header [data-menu-toggle]").click()
        run_frames(page, 30)
        shown = page.evaluate("() => ({ share: window.__cloth.uniforms.u_sheet, open: document.querySelector('#shell-menu').classList.contains('is-open'), main: getComputedStyle(document.querySelector('main')).getPropertyValue('--sheet').trim() })")
        if not shown["open"]:
            problems.append(f"{label}: the menu did not open, so nothing was checked")
        elif shown["share"] != 0:
            problems.append(f"{label}: the cloth is asked to draw the sheet at {shown['share']} while the menu is a column (shell.js' value for the open menu is {shown['main']!r}); "
                            f"it should be 0, or the paper covers the left half of the cloth")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()
    return problems


def cloth_backdrop_problems(browser, base_url, session_key):
    """What is painted behind the install box includes the cloth, so its slight backdrop blur (spec 4.2) smooths the
    film grain behind it instead of letting it through. <main> must not be a backdrop root while nothing changes: a
    view-transition-name that stays on it makes it one, and a backdrop-filter inside a backdrop root sees only what
    is painted inside it, which is nothing. At 1280 (below 64rem the top fade's mask makes <main> a backdrop root too,
    for which nothing is checked), on the threshold after Come in, with the cloth held still: the spread of the luma
    along the box's top padding, where there is no text, with its blur and with the same frame without it, must be at
    least 8% lower with it. The cloth is told to draw none of its folds (u_cloth forced to 0, as in cloth_grain_problems),
    so that what is measured is the film grain alone, which is what the blur is meant to smooth: the folds are low in
    frequency, the slight blur does not remove them, and a fold under the strip would make the ratio depend on where
    the box sits (moving the box up 9.75 px raised it from about 0.85 to 0.93). A blur that sees the grain lowers its
    spread to 0.34 to 0.83 of the sharp frame's; one that sees nothing leaves it at exactly 1.000."""
    problems = []
    for theme in ("light", "dark"):
        label = f"/ cloth at 1280px, {theme}, the install box's backdrop blur"
        context, _ = cloth_context(browser, base_url, session_key, page_named("/"), width=1280, height=800, theme=theme, scripts=(FREEZE,))
        page, errors = open_page(context, base_url + "/", warnings=True)
        if not cloth_running(page):
            problems.append(f"{label}: the cloth never started")
            context.close()
            continue
        run_frames(page, 30)
        page.locator("[data-come-in]").click()
        page.wait_for_function("() => { const box = document.querySelector('[data-install]'); return Boolean(box) && !box.hidden; }", timeout=20000)
        # The reveal's fade runs on real time; while it runs the install block is a backdrop root and the box's blur
        # sees nothing behind it, so a slow machine measured mid-fade would fail for that reason alone.
        page.wait_for_function("() => { const box = document.querySelector('[data-install]');"
                               " return getComputedStyle(box).opacity === '1' && !box.getAnimations().length; }", timeout=20000)
        page.mouse.move(4, 320)
        run_frames(page, 30)
        box = page.evaluate(BOX_RECT, ".install-line__panel:not([hidden]) .install-line__box")
        clip = {"x": box["x"] + 4, "y": box["y"] + 2, "width": box["width"] - 8, "height": 8}   # inside the border, above the text
        page.evaluate("() => { window.__cloth.force.u_cloth = 0; window.__cloth.drawAll = true; }")   # the grain alone, no folds
        page.evaluate(NEXT_FRAMES)
        blurred = png_luma(page.screenshot(clip=clip))[1]
        page.add_style_tag(content=".install-line__box { -webkit-backdrop-filter: none !important; backdrop-filter: none !important; }")
        page.evaluate(NEXT_FRAMES)
        sharp = png_luma(page.screenshot(clip=clip))[1]
        page.evaluate("() => { delete window.__cloth.force.u_cloth; window.__cloth.drawAll = false; }")
        if blurred > 0.92 * sharp:
            problems.append(f"{label}: the box's backdrop blur leaves the grain behind it as it is (spread {blurred:.4f} of luma with the blur, "
                            f"{sharp:.4f} without); the blur should smooth the grain, so that the spread with it is under 0.92 of the spread without (<main> is named "
                            f"{page.evaluate(MAIN_NAME)!r} for view transitions)")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()
    return problems


# The film grain alone (cloth_grain_problems): the cloth is told to draw none of itself (u_cloth forced to 0, so the canvas is the
# appearance's paper and the grain on it), and each frame read back is kept as its gamma luma, for statistics and for how a pixel
# changes from one frame to another.
GRAIN_KEEP = """(name) => { const { w, h, px } = window.__cloth.last, luma = new Float32Array(w * h);
  for (let i = 0; i < w * h; i++) luma[i] = (0.2126 * px[i * 4] + 0.7152 * px[i * 4 + 1] + 0.0722 * px[i * 4 + 2]) / 255;
  (window.__cloth.kept = window.__cloth.kept || {})[name] = luma; return luma.length; }"""
GRAIN_STATS = """(name) => { const luma = window.__cloth.kept[name]; let sum = 0, sum2 = 0;
  for (const v of luma) { sum += v; sum2 += v * v; }
  const mean = sum / luma.length, std = Math.sqrt(sum2 / luma.length - mean * mean); let reach = 0;
  for (const v of luma) reach = Math.max(reach, Math.abs(v - mean));
  return { n: luma.length, mean, std, reach }; }"""
GRAIN_CHANGE = """([a, b]) => { const first = window.__cloth.kept[a], second = window.__cloth.kept[b]; let sum = 0;
  for (let i = 0; i < first.length; i++) sum += Math.abs(second[i] - first[i]);
  return sum / first.length; }"""


# What the grain measures, read as above at 1024x640 (a pixel's change is the mean over the canvas of |later - earlier| luma, as a share of the
# grain's spread), and what the old grain, a fresh uniform +-0.025 every frame, measured; each limit sits about halfway between the two:
#                              this grain (Paper | Ink)     the old grain      limit
#   spread (luma)                0.0084 | 0.0061            0.0145 | 0.0145    Paper 0.0070 to 0.0100, Ink 0.0048 to 0.0072
#   Ink's spread / Paper's       0.72                       1.00               under GRAIN_INK_SHARE 0.85
#   a frame later (1/60 s)       0.145 | 0.145              1.15 | 1.15        under GRAIN_FRAME_MOVE 0.6
#   a quarter second later       1.50 | 1.49                1.15 (a grain that never moves: 0)   over GRAIN_QUARTER_MOVE 0.75
#   farthest from the mean       0.0237 | 0.0191            0.0236 | 0.0237    GRAIN_REACH 0.027: the calm's +-0.025 and 8-bit rounding
#   farthest / spread            2.8 | 3.2                  1.6 | 1.7          over GRAIN_BELL 2.3
#   mean minus the paper         +0.0002 | -0.0004          0                  within GRAIN_MEAN 0.001 (Ink's: the shader's paper, 26.5 levels, is read as 27)
GRAIN_FRAME_MOVE = 0.6
GRAIN_QUARTER_MOVE = 0.75
GRAIN_MEAN = 0.001
GRAIN_REACH = 0.027
GRAIN_BELL = 2.3
GRAIN_INK_SHARE = 0.85   # the most Ink's grain spread may be of Paper's (not veil.js's GRAIN_INK, which is the grain's strength in Ink)
GRAIN_SPREAD = {"Paper": (0.0070, 0.0100), "Ink": (0.0048, 0.0072)}


def cloth_grain_problems(browser, base_url, session_key):
    """The film grain (spec 3.1): each pixel has a fixed random phase and drifts through its brightness about 1.5 times a second,
    a shimmer and not static; the grain is bell-shaped, so most pixels are quiet and few reach far, zero-mean, and weaker in Ink
    than in Paper. Read with the cloth told to draw nothing of itself (the canvas is the paper and the grain on it), in Paper and in Ink,
    at the cloth's first step: (1) with time held still the grain is held still (the virtual clock controls it); (2) one display frame
    later a pixel has changed by a small share of the grain's spread (GRAIN_FRAME_MOVE; a fresh random value each frame gives 1.1); (3) a
    quarter of a second later it has moved on, by more than GRAIN_QUARTER_MOVE (a grain that does not drift gives 0); (4) the mean stays
    on the paper (GRAIN_MEAN); (5) the grain reaches further than a flat noise of the same spread would (GRAIN_BELL times it; flat gives
    1.7), and no further than GRAIN_REACH, the +-0.025 the calm (FLOOR, CEILING) was measured with; (6) the spread in Ink is under
    GRAIN_INK_SHARE of the spread in Paper, and each is within its band (GRAIN_SPREAD)."""
    problems = []
    threshold = page_named("/")   # its menu is shut and nothing is under the pointer, so no wash is held anywhere
    spread = {}
    for theme, appearance in (("light", "Paper"), ("dark", "Ink")):
        label = f"/ cloth grain in {appearance}"
        context, _ = cloth_context(browser, base_url, session_key, threshold, theme=theme, scripts=(FREEZE,))
        page, errors = open_page(context, base_url + threshold.path, warnings=True)
        if not cloth_running(page):
            problems.append(f"{label}: the cloth never started")
            context.close()
            continue
        page.evaluate("() => { window.__cloth.force.u_cloth = 0; }")
        run_frames(page, 30)
        paper = gamma_luma(page.evaluate(TOKEN, "--paper"))
        for name, count in (("start", 0), ("held", 0), ("frame", 1), ("quarter", 14)):
            if count:
                run_frames(page, count)
            capture(page)
            page.evaluate(GRAIN_KEEP, name)
        if page.evaluate(INKS_DRAWN):
            problems.append(f"{label}: an ink wash is on the canvas, which would count in the grain ({page.evaluate(INKS_DRAWN)})")
        stats = page.evaluate(GRAIN_STATS, "start")
        std = stats["std"]
        held, frame, quarter = (page.evaluate(GRAIN_CHANGE, ["start", name]) for name in ("held", "frame", "quarter"))
        spread[appearance] = std
        if held != 0:
            problems.append(f"{label}: with time held still the grain should be held still; it changed by {held:.5f} of luma on average")
        if frame > GRAIN_FRAME_MOVE * std:
            problems.append(f"{label}: one display frame later a pixel has changed by {frame:.5f} of luma on average, {frame / std:.2f} of the grain's spread "
                            f"({std:.5f}); the grain should drift (under {GRAIN_FRAME_MOVE}), not take a fresh random value each frame (about 1.1)")
        if quarter < GRAIN_QUARTER_MOVE * std:
            problems.append(f"{label}: a quarter of a second later a pixel has changed by only {quarter / std:.2f} of the grain's spread; "
                            f"the grain should be drifting (over {GRAIN_QUARTER_MOVE})")
        if abs(stats["mean"] - paper) > GRAIN_MEAN:
            problems.append(f"{label}: the grain moves the paper's luma by {stats['mean'] - paper:+.5f} on average; it should average out (within {GRAIN_MEAN})")
        if stats["reach"] > GRAIN_REACH:
            problems.append(f"{label}: the grain reaches {stats['reach']:.4f} of luma from its mean; the calm was measured with no more than {GRAIN_REACH}")
        if stats["reach"] < GRAIN_BELL * std:
            problems.append(f"{label}: the grain reaches only {stats['reach'] / std:.2f} times its spread; a bell-shaped grain, most pixels quiet and a few far out, "
                            f"reaches {GRAIN_BELL} times it or more (a flat noise reaches 1.7)")
        low, high = GRAIN_SPREAD[appearance]
        if not low <= std <= high:
            problems.append(f"{label}: the grain's spread is {std:.5f} of luma; it should be between {low} and {high} to read as subtle and still be there")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()
    if len(spread) == 2 and spread["Ink"] >= GRAIN_INK_SHARE * spread["Paper"]:
        problems.append(f"/ cloth grain: the grain in Ink ({spread['Ink']:.5f}) should be weaker than in Paper ({spread['Paper']:.5f}), "
                        f"under {GRAIN_INK_SHARE} of it")
    return problems


# Every family of cloth checks, in two parts that CI runs as two jobs (`site-cloth` and `site-ink`, each under its own 20 minutes) and that run
# in this order; new ones go at the end of the part they belong to. `cloth` is the cloth itself (it runs, fails, stops, answers its inputs, is
# legible, adapts to the device); `ink` is the washes and the phone sheet that carries them. Measured before CI moved to hosted runners, in seconds (runs 38973926967 and
# 38976280746): cloth about 545 (contrast 258, adaptive 175, the other nine under 25 each), ink about 460 (the sheet's cloth family 218, ink 178,
# the sheet's washes 53, its motion under 25). Moving adaptive to the ink part would make it 370 and 635, so they stay as they are.
CLOTH_FAMILIES = [cloth_presence_problems, cloth_failure_problems, cloth_lifecycle_problems, cloth_input_problems,
                  cloth_appearance_problems, cloth_change_problems, contrast_problems,
                  adaptive_problems, cloth_top_fade_problems, cloth_backdrop_problems, cloth_grain_problems]
INK_FAMILIES = [ink_problems, cloth_menu_sheet_problems, cloth_sheet_ink_problems, cloth_sheet_motion_problems]
# What the run says it follows when it looks at WebGL again before a part that did not start the run (a screenshot taller than 8,192 px takes WebGL away). The
# parts that draw the cloth (cloth, ink) follow the page checks, which take the tall screenshots, and each other; the reading, phone, forms, catalog and dashboard parts stub the
# cloth out and need no look, so they come last, in that order (a label for every part but the last, since another follows it: pages.tests_site_parts wants one
# for every part another follows).
PREVIOUS_STAGE = {"pages": "the page checks", "cloth": "the cloth families", "ink": "the ink families", "reading": "the reading page checks", "phone": "the phone menu checks",
                  "forms": "the forms and error page checks", "catalog": "the component catalog checks"}


def run_families(browser, base_url, session_key, part, families, previous=None):
    """The families of a part, each timed, so a slow run shows where its time went. If WebGL is gone, or the cloth does not start in a
    family, the others would only wait out the same 30 s timeouts: say so once. After another stage of the same run (`previous`) WebGL is
    looked at again first, since a screenshot can take it away from the run; as a part of its own the run began with that look."""
    if previous:
        try:
            webgl_precondition(browser, base_url, after=f"{PREVIOUS_STAGE.get(previous, previous)} (failures above that mention Motion probably follow from it)")
        except SystemExit as stop:
            return [str(stop)]
    problems = []
    for family in families:
        started = time.perf_counter()
        try:
            found = family(browser, base_url, session_key)
        except ClothStopped as stopped:
            found = [f"{part}: {family.__name__}: {stopped}"]
            for context in browser.contexts:  # the family left its contexts open
                context.close()
        problems += found
        print(f"{part}: {family.__name__} took {time.perf_counter() - started:.0f} s")
        if any("the cloth never started" in problem for problem in found):
            problems.append(f"{part}: the cloth did not start in the family above; the remaining families were not run")
            break
    return problems


def cloth_problems(browser, base_url, session_key, previous=None):
    """The cloth part: CLOTH_FAMILIES (CI's job `site-cloth`)."""
    return run_families(browser, base_url, session_key, "cloth", CLOTH_FAMILIES, previous)


def ink_part_problems(browser, base_url, session_key, previous=None):
    """The ink part: INK_FAMILIES (CI's job `site-ink`)."""
    return run_families(browser, base_url, session_key, "ink", INK_FAMILIES, previous)


def axe_problems(page, label):
    """WCAG 2.1 A and AA violations on the page as it is now; undecided results become review notes."""
    # axe-core cannot measure contrast through a background image, so take the grain away first
    # (the page's own).
    page.add_style_tag(content="html { background-image: none !important; }")
    # Whatever is fading in has to be done, or axe measures the contrast of a colour half way; a long page's screenshot, just before,
    # can leave it half way. Bounded. (The menu's fade, 0.7 s, is shell.js' driver and no animation: a caller with the menu open waits
    # for SHEET_IN_PLACE first.)
    page.evaluate("""() => Promise.race([Promise.all(document.getAnimations().map((animation) => animation.finished.catch(() => null))),
        new Promise((done) => setTimeout(done, 3000))])""")
    if not page.evaluate("() => typeof axe !== 'undefined'"):
        page.add_script_tag(path=str(AXE_SCRIPT))
    result = page.evaluate(
        """async (tags) => { const r = await axe.run(document, { runOnly: { type: "tag", values: tags } });
             const where = (v) => v.nodes.map((n) => n.target.join(" ")).join(", ");
             return { violations: r.violations.map((v) => `${v.id} (${v.impact}): ${v.help} at ${where(v)}`),
                      incomplete: r.incomplete.flatMap((v) => v.nodes.map((n) => `${v.id}: ${v.help} at ${n.target.join(" ")}`)) }; }""",
        AXE_TAGS,
    )
    REVIEW_NOTES.extend(f"{label}: {note}" for note in result["incomplete"])
    return [f"{label}: accessibility: {violation}" for violation in result["violations"]]


def sideways_problems(page, label, showing=""):
    """The page is no wider than the window; `showing` says what was on the page when it was measured."""
    overflow = page.evaluate(OVERFLOW)
    return [f"{label}: the page scrolls sideways by {overflow}px{showing}"] if overflow > 0 else []


def page_problems(page, label, *, display_text=False):
    """Sideways scrolling, fonts that never load, accessibility. `display_text` says the page plainly has
    text in the display face (a chapter, the threshold): finding none there means the helper is broken."""
    problems = sideways_problems(page, label)
    page.evaluate("() => document.fonts.ready.then(() => true)")
    shown = page.evaluate(USED_FAMILIES)
    if display_text and DISPLAY_FAMILY not in shown:
        problems.append(f"{label}: no visible text is set in {DISPLAY_FAMILY}, but this page has display text "
                        f"(families found: {shown}); the check of which fonts load has nothing to go on")
    used = [family for family in shown if family in FONT_FAMILIES]
    unloaded = page.evaluate(
        """(families) => families.filter((family) => ![...document.fonts].some(
            (face) => face.family.replace(/["']/g, "") === family && face.status === "loaded"))""",
        used,
    )
    problems += [f"{label}: the font {family} never loaded" for family in unloaded]
    return problems + axe_problems(page, label)


def menu_problems(page, label):
    problems = []
    toggle = page.locator("header [data-menu-toggle]")
    if toggle.get_attribute("aria-expanded") != "false":
        problems.append(f"{label}: the menu starts open (menu_rest_problems says more)")
        page.keyboard.press("Escape")
    toggle.click()
    if toggle.get_attribute("aria-expanded") != "true" or not page.locator("#shell-menu").is_visible():
        problems.append(f"{label}: MENU does not open the menu")
    else:
        page.wait_for_function(SHEET_IN_PLACE, timeout=5000)  # the menu has finished fading in (0.7 s) before axe measures it
    problems += menu_sideways_problems(page, f"{label} with the menu open")
    problems += axe_problems(page, f"{label} with the menu open")
    box = page.locator("#shell-menu").bounding_box()  # None when MENU failed to open it (reported above)
    if box and content_shows_under(page, box):
        problems.append(f"{label}: page content shows through the open menu")
    page.keyboard.press("Escape")
    if toggle.get_attribute("aria-expanded") != "false" or not menu_gone(page):
        problems.append(f"{label}: Escape does not close the menu")
    if not page.evaluate("() => document.activeElement && document.activeElement.hasAttribute('data-menu-toggle')"):
        problems.append(f"{label}: Escape does not return focus to MENU")
    return problems


def appearance_problems(page, label):
    """From Paper, the switch cycles Ink, Auto, Paper, saves each choice, and keeps it across a reload."""
    problems = []
    button = page.locator("footer [data-appearance-toggle]")
    for stored, word in (("dark", "Ink"), ("system", "Auto"), ("light", "Paper")):
        button.click()
        shown = (page.evaluate(ROOT_ATTRIBUTE, "data-theme"), (button.text_content() or "").strip(),
                 button.get_attribute("aria-label"), page.evaluate("() => localStorage.getItem('veilfile-theme')"))
        if shown != (stored, word, f"Appearance: {word}", stored):
            problems.append(f"{label}: the appearance switch showed {shown}, expected {(stored, word, f'Appearance: {word}', stored)}")
    button.click()  # Ink again, then reload: the choice must survive
    page.reload(wait_until="networkidle")
    if page.evaluate(ROOT_ATTRIBUTE, "data-theme") != "dark":
        problems.append(f"{label}: the saved appearance did not survive a reload")
    return problems


def motion_problems(page, label):
    problems = []
    button = page.locator("footer [data-motion-toggle]")
    before = page.evaluate(ROOT_ATTRIBUTE, "data-motion")
    button.click()
    after = page.evaluate(ROOT_ATTRIBUTE, "data-motion")
    expected = "off" if before == "on" else "on"
    if after != expected or button.get_attribute("aria-pressed") != str(after == "on").lower():
        problems.append(f"{label}: Motion did not switch from {before} to {expected}")
    if page.evaluate("() => localStorage.getItem('veilfile-motion')") != expected:
        problems.append(f"{label}: Motion did not save the choice")
    if (page.locator("footer [data-motion-state]").text_content() or "").strip() != f"· {expected}":
        problems.append(f"{label}: Motion's visible state does not say {expected}")
    problems += motion_name_problems(page, f"{label} after Motion was switched {expected}")
    return problems


def labelled_link_problems(page, label):
    """A labelled link (a.interactive) in the page answers the cursor with the one flat fill on its
    ::before (spec 3.4): it takes no prose-link underline at rest and no fill on the anchor itself."""
    problems = []
    links = page.locator("main a.interactive")
    for index in range(links.count()):
        link = links.nth(index)
        if not link.is_visible():  # nothing to hover, and nothing for a visitor to see
            continue
        text = (link.text_content() or "").strip()
        underline = link.evaluate("(a) => getComputedStyle(a).textDecorationLine")
        try:
            link.hover(timeout=3000)
        except PlaywrightError as error:  # covered by something, or moved away: a finding, not the end of the run
            problems.append(f"{label}: the labelled link {text!r} cannot be hovered: {error.message.splitlines()[0]}")
            continue
        page.wait_for_timeout(500)  # past the fill fade, so a second fill has fully arrived
        fill = link.evaluate("(a) => getComputedStyle(a).backgroundColor")
        page.mouse.move(0, 0)
        if underline != "none" or fill not in ("rgba(0, 0, 0, 0)", "transparent"):
            problems.append(f"{label}: the labelled link {text!r} takes the prose-link underline or a second fill "
                            f"(underline {underline}, fill on the link {fill} when hovered)")
    return problems


CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")


def png_rows(data):
    """(channels, unfiltered rows) of an 8-bit RGB or RGBA PNG, as Playwright writes screenshots."""
    position, idat = 8, b""
    while position < len(data):
        length, kind = struct.unpack(">I4s", data[position:position + 8])
        body = data[position + 8:position + 8 + length]
        if kind == b"IHDR":
            width, height, _, color = struct.unpack(">IIBB", body[:10])
        elif kind == b"IDAT":
            idat += body
        position += 12 + length
    channels = {2: 3, 6: 4}[color]
    stride, raw = width * channels, zlib.decompress(idat)
    rows, previous = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for x in range(stride):
            a = line[x - channels] if x >= channels else 0
            b = previous[x]
            c = previous[x - channels] if x >= channels else 0
            if kind == 1:
                line[x] = (line[x] + a) & 255
            elif kind == 2:
                line[x] = (line[x] + b) & 255
            elif kind == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        previous = line
    return channels, rows


def png_luma(data):
    """(mean, spread) of the gamma luma, 0 to 1, of an 8-bit PNG screenshot (the same luma cloth.stats reads)."""
    channels, rows = png_rows(data)
    values = [(0.2126 * row[i] + 0.7152 * row[i + 1] + 0.0722 * row[i + 2]) / 255 for row in rows for i in range(0, len(row), channels)]
    mean = sum(values) / len(values)
    return mean, (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5


def png_difference(first, second, block=16):
    """(mean, worst) of the difference in gamma luma, 0 to 1, between two 8-bit PNG screenshots of the same size: its mean over every pixel,
    and its mean over the block by block pixels where it is greatest (a small thing that shows in one place is lost in the first)."""
    channels, rows = png_rows(first)
    _, other = png_rows(second)
    total, blocks = 0.0, {}
    for y, (a, b) in enumerate(zip(rows, other)):
        if a == b:
            continue
        for x in range(0, len(a), channels):
            gap = abs(0.2126 * (a[x] - b[x]) + 0.7152 * (a[x + 1] - b[x + 1]) + 0.0722 * (a[x + 2] - b[x + 2])) / 255
            if gap:
                total += gap
                key = (x // channels // block, y // block)
                blocks[key] = blocks.get(key, 0.0) + gap
    return total / (len(rows) * len(rows[0]) // channels), max(blocks.values(), default=0.0) / (block * block)


# Where content_shows_under saves its two captures when it finds something, so a run's artifacts
# (on CI too) show what was found; run() points it at the output directory.
EVIDENCE = {"dir": None, "count": 0}


def content_shows_under(page, box, margin=4, cover=None, hide="main"):
    """Whether hiding <main> (or the element hide names) changes what is painted in box: if it does, page content
    shows there. cover is a selector for what sits in box (a corner label); it is hidden for both captures, so only
    what lies under it is compared, never its own text, which Chromium on Linux can rasterise
    differently from one capture to the next. Only a strong leak counts: a pixel that differs by more than 40 levels of 255, so a
    surface that is 85% opaque passes (a text pixel is about 200 levels from the paper, 15% of that is 30); how solid the sheet
    really is, to within 1%, is cloth_menu_sheet_problems'."""
    view = page.viewport_size
    left, top = max(0, box["x"] - margin), max(0, box["y"] - margin)
    right = min(view["width"], box["x"] + box["width"] + margin)
    bottom = min(view["height"], box["y"] + box["height"] + margin)
    if right - left < 1 or bottom - top < 1:
        return False
    clip = {"x": left, "y": top, "width": right - left, "height": bottom - top}
    set_cover = "([selector, value]) => document.querySelectorAll(selector).forEach((e) => { e.style.visibility = value; })"
    if cover:
        page.evaluate(set_cover, [cover, "hidden"])
    shown_png = page.screenshot(clip=clip)
    page.evaluate("(selector) => { document.querySelector(selector).style.visibility = 'hidden'; }", hide)
    hidden_png = page.screenshot(clip=clip)
    page.evaluate("(selector) => { document.querySelector(selector).style.visibility = ''; }", hide)
    if cover:
        page.evaluate(set_cover, [cover, ""])
    channels, shown = png_rows(shown_png)
    _, hidden = png_rows(hidden_png)
    found = any(abs(a - b) > 40 for row_a, row_b in zip(shown, hidden)
                for i, (a, b) in enumerate(zip(row_a, row_b)) if i % channels != 3)
    if found and EVIDENCE["dir"]:
        EVIDENCE["count"] += 1
        for name, data in (("shown", shown_png), ("hidden", hidden_png)):
            (EVIDENCE["dir"] / f"content-under-{EVIDENCE['count']}-{name}.png").write_bytes(data)
    return found


def under_corner_problems(page, label, stops=None):
    """Wherever the page is scrolled, nothing in <main> shows under a corner label: from 64rem up the page runs between
    the corners, below it the page fades out along the top, before the mark and MENU, and the bottom corners wait at its end.
    The page is looked at every quarter of the screen; with `stops` (a long reading page, dozens of screens tall on a phone),
    at that many positions spread from its top to its end."""
    problems, seen = [], set()
    height = page.viewport_size["height"]
    bottom = page.evaluate("() => document.documentElement.scrollHeight - innerHeight")
    if stops and stops > 1 and bottom > 0:
        positions = sorted({round(bottom * index / (stops - 1)) for index in range(stops)})
    else:
        positions = range(0, bottom + 1, max(1, height // 4))
    for y in positions:
        page.evaluate(f"() => window.scrollTo(0, {y})")
        page.evaluate(NEXT_FRAMES)  # the page fades out along the top of a phone's screen: shell.js moves that with the scroll, once a frame
        for corner in CORNERS:
            box = page.locator(f".shell-corner--{corner}").bounding_box()
            if corner not in seen and box and content_shows_under(page, box, cover=f".shell-corner--{corner}"):
                seen.add(corner)
                captures = f" (captures: content-under-{EVIDENCE['count']}-*.png)" if EVIDENCE["dir"] else ""
                problems.append(f"{label}: page content shows under the {corner} corner at {y}px down{captures}")
    page.evaluate("() => window.scrollTo(0, 0)")
    return problems


def overlaps(a, b):
    return a["x"] < b["x"] + b["width"] and b["x"] < a["x"] + a["width"] and a["y"] < b["y"] + b["height"] and b["y"] < a["y"] + a["height"]


# How far a menu link's box may end past the edge of the viewport after the sheet or column was scrolled to bring it in. A scroll lands
# on whole pixels and a link sits at a fraction of one, so a link can be left a fraction of a pixel out: with no slack at all 14 of the
# 42 pages and small viewports fail, by 0.203 px (Journal's bottom at 375.203 in a viewport 375 high, the sheet scrolled to 22 where 22.203
# would bring it in); over viewports 300 to 516 high, at pixel ratios 1 and 2, the most was 0.219 px. The slack is 0.25, just above the
# measured 0.22: a link left a quarter pixel or more out is a real overshoot, not a scroll landing on a whole pixel.
REACH_SLACK = 0.25


def menu_reach_problems(page, label):
    """Every link in the open menu can be brought into view, and none sits on the display controls."""
    problems = []
    toggle = page.locator("header [data-menu-toggle]")
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
    page.wait_for_function(SHEET_IN_PLACE)  # a sheet fading in is up to 6px above its place
    problems += menu_sideways_problems(page, label)
    controls = page.locator(".shell-corner--bottom-right").bounding_box()
    if page.evaluate(UNDER_SHEET, "footer [data-motion-toggle]"):  # the open sheet itself is painted over them
        controls = None
    height = page.viewport_size["height"]
    for link in page.locator("#shell-menu a").all():
        name = (link.text_content() or "").strip()
        try:
            link.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass
        box = link.bounding_box()
        if box is None or box["y"] < -REACH_SLACK or box["y"] + box["height"] > height + REACH_SLACK:
            problems.append(f"{label}: the menu link {name} cannot be brought into view")
        elif controls and overlaps(box, controls):
            problems.append(f"{label}: the menu link {name} sits on the display controls")
    page.keyboard.press("Escape")
    return problems


# What each bottom corner holds: the words of its buttons (by which switch they are) and any other text.
BOTTOM_ROW = """() => { const corner = (name) => document.querySelector(`.shell-corner--bottom-${name}`);
  const parts = (name) => ({ appearance: corner(name).querySelectorAll('[data-appearance-toggle]').length,
                             motion: corner(name).querySelectorAll('[data-motion-toggle]').length,
                             text: corner(name).textContent.replace(/\\s+/g, ' ').trim(),
                             box: (({ left, right, top, bottom }) => ({ left, right, top, bottom }))(corner(name).getBoundingClientRect()) });
  return { left: parts('left'), right: parts('right'), width: innerWidth,
           appearances: document.querySelectorAll('footer [data-appearance-toggle]').length, motions: document.querySelectorAll('footer [data-motion-toggle]').length }; }"""


def motion_name_problems(page, label):
    """The motion switch's visible word is FX, so that the bottom row reads PAPER / FX · ON, and its accessible name keeps the
    word visitors see (WCAG 2.5.3, label in name) and says what the switch does: "FX: motion", with aria-pressed carrying on or off
    and the visible "· on" hidden from the name. Read through the browser's own accessible name (get_by_role)."""
    problems = []
    button = page.locator("footer [data-motion-toggle]")
    pressed = button.get_attribute("aria-pressed") == "true"
    visible = (button.inner_text() or "").strip()
    if not re.fullmatch(r"FX · (ON|OFF)", visible):
        problems.append(f"{label}: the motion switch shows {visible!r}; it should show FX · ON or FX · OFF")
    footer = page.locator("footer")   # the catalog shows samples of the switch in <main>
    named = footer.get_by_role("button", name=re.compile(r"\bFX\b"))
    if named.count() != 1:
        problems.append(f"{label}: {named.count()} buttons have FX in their accessible name; the motion switch should, once, so that its name holds its visible label")
    if footer.get_by_role("button", name=re.compile("motion", re.I)).count() != 1:
        problems.append(f"{label}: the motion switch's accessible name should say motion, for a screen reader's user who cannot see what FX means")
    if footer.get_by_role("button", name="FX: motion", exact=True, pressed=pressed).count() != 1:
        problems.append(f"{label}: the motion switch should be named exactly 'FX: motion' and be a toggle button, pressed={pressed}, with its state out of its name")
    return problems


def bottom_row_problems(page, label):
    """The bottom row (spec 4.1): the appearance switch is bottom left, the motion switch (worded FX) bottom right, each once, and the
    tagline that used to be bottom left is gone, at every width. They sit at the two margins of the row."""
    problems = []
    row = page.evaluate(BOTTOM_ROW)
    left, right = row["left"], row["right"]
    if (left["appearance"], left["motion"]) != (1, 0) or not re.fullmatch(r"Ink|Auto|Paper", left["text"]):
        problems.append(f"{label}: the bottom left corner should hold the appearance switch and nothing else; it holds "
                        f"{left['appearance']} appearance switch(es), {left['motion']} Motion and the words {left['text']!r}")
    if (right["appearance"], right["motion"]) != (0, 1) or not re.fullmatch(r"FX · (on|off)", right["text"]):
        problems.append(f"{label}: the bottom right corner should hold the motion switch, worded FX · on or FX · off, and nothing else; it holds "
                        f"{right['appearance']} appearance switch(es), {right['motion']} motion switch(es) and the words {right['text']!r}")
    problems += motion_name_problems(page, label)
    if (row["appearances"], row["motions"]) != (1, 1):
        problems.append(f"{label}: each switch should appear once in the page's footer (the catalog's samples are in <main>); the appearance switch {row['appearances']} times, Motion {row['motions']}")
    if left["box"]["left"] > row["width"] * 0.1 or right["box"]["right"] < row["width"] * 0.9 or left["box"]["right"] > right["box"]["left"]:
        problems.append(f"{label}: the bottom corners should sit at the left and right margins; the left spans {left['box']['left']:.0f} to "
                        f"{left['box']['right']:.0f}, the right {right['box']['left']:.0f} to {right['box']['right']:.0f} of {row['width']}")
    # The row shows: with the page at its end (where a phone's bottom corners wait) hiding the footer changes what is
    # painted where each button is. Anything laid over the row, or a mask that fades it out, would leave it as it is.
    page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
    page.evaluate(NEXT_FRAMES)
    for side, name in (("left", "the appearance switch"), ("right", "the motion switch")):
        box = page.locator(f".shell-corner--bottom-{side} button").bounding_box()
        if box and not content_shows_under(page, box, margin=2, hide="footer"):
            problems.append(f"{label}: {name} cannot be seen at the end of the page (hiding the footer changes nothing where it is)")
    page.evaluate("() => window.scrollTo(0, 0)")
    return problems


# The corners' type and mark as they are drawn: the size and tracking of each kind of label, and the mark's box.
CORNER_TYPE = """() => { const style = (selector) => { const element = document.querySelector(selector); return element ? getComputedStyle(element) : null; };
  const label = (selector) => { const s = style(selector); return s ? { size: parseFloat(s.fontSize), tracking: parseFloat(s.letterSpacing) } : null; };
  const mark = document.querySelector('.lockup .mark');
  return { menu: label('header [data-menu-toggle]'), link: label('#shell-menu a'), appearance: label('footer [data-appearance-toggle]'),
           motion: label('footer [data-motion-toggle]'), lockup: label('.lockup'), mark: mark ? mark.getBoundingClientRect().width : null }; }"""


def corner_type_problems(browser, base_url, session_key):
    """The corners are friendly to read (owner's decision, 2026-10-16): the labels are 13px on phones and 14px on wide screens,
    rising between with the width (clamp(13px, 0.35vw + 11.6px, 14px), 14px from 686px), their tracking .14em, and the mark is 24px
    on phones (6px cells) and 28px from 64rem up (7px cells): whole pixels per cell. Read as drawn at widths from a phone to a
    wide screen, on the threshold (whose menu is shut: the link in it is read with it open)."""
    problems = []
    for width in (375, 600, 686, 1023, 1024, 1440):
        label = f"/ at {width}px: the corners' type"
        context = new_context(browser, base_url, session_key, page_named("/"), width=width, theme="light", motion="off")
        page, errors = open_page(context, base_url + "/")
        page.locator("header [data-menu-toggle]").click()
        found = page.evaluate(CORNER_TYPE)
        size = min(14.0, max(13.0, 0.0035 * width + 11.6))
        for name, read in found.items():
            if name == "mark":
                continue
            if read is None:
                problems.append(f"{label}: the {name} label was not found")
                continue
            if abs(read["size"] - size) > 0.05:
                problems.append(f"{label}: the {name} label is {read['size']:g}px; it should be {size:.2f}px (13px on phones, 14px from 686px wide)")
            if abs(read["tracking"] - 0.14 * read["size"]) > 0.02:
                problems.append(f"{label}: the {name} label's tracking is {read['tracking']:.2f}px; it should be .14em, {0.14 * read['size']:.2f}px")
        mark = 28 if width >= 1024 else 24
        if found["mark"] is None or abs(found["mark"] - mark) > 0.01:
            problems.append(f"{label}: the mark is {found['mark']}px; it should be {mark}px, cells of {mark // 4}px")
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()
    return problems


def corner_row_problems(page, label):
    return [f"{label}: the {edge} corners' text rows are {gap:.1f}px apart; they should be level"
            for edge, gap in page.evaluate(CORNER_ROWS) if gap > 1]


def corner_problems(page, label, stops=None):
    """The corners' rows are level and, wherever the page is scrolled (at `stops` positions, if given), nothing in <main> shows under a corner."""
    return corner_row_problems(page, label) + under_corner_problems(page, label, stops)


def menu_sideways_problems(page, label):
    """The open menu column does not scroll sideways: its links' hit areas (and focus outlines) reach past
    their text, and the column's padding must hold them, or a browser with always-shown scrollbars draws a
    horizontal one under the menu. Headless Chromium hides scrollbars, but scrollWidth still says."""
    sizes = page.evaluate("""() => { const list = document.querySelector('#shell-menu');
        return list && list.getClientRects().length ? { client: list.clientWidth, scroll: list.scrollWidth } : null; }""")
    if sizes is None:
        return [f"{label}: the menu is not showing, so its width could not be checked"]
    if sizes["scroll"] > sizes["client"]:
        return [f"{label}: the open menu column scrolls sideways (scrollWidth {sizes['scroll']}, clientWidth {sizes['client']})"]
    return []


def set_menu(page, open_):
    """Leave the menu open or closed, whichever way the page left it. It starts closed on every page at every width, and an
    in-place change closes it (spec 4.4), so a check that needs a menu link opens it with MENU, as a visitor does."""
    toggle = page.locator("header [data-menu-toggle]")
    if (toggle.get_attribute("aria-expanded") == "true") != open_:
        if open_:
            toggle.click()
        else:
            page.keyboard.press("Escape")


def hold_menu(page):
    """Open the menu with the keyboard (Tab to MENU, Enter) and leave the pointer in the left margin, so that the current page's link
    holds its wash (spec 3.4) and nothing else is washed: MENU's own, from the focus it had, fades over 1.8 s, which 120 frames of cloth
    time outlast. With the keyboard and not a click so that a focus() from a script later still draws a focus ring, which after a
    click it would not (:focus-visible follows the last kind of input). The cloth's context only (run_frames)."""
    for _ in range(6):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement.matches('[data-menu-toggle]')"):
            break
    page.keyboard.press("Enter")
    page.evaluate("() => document.activeElement.blur()")
    page.mouse.move(4, 320)
    run_frames(page, 120)


def menu_link(page, href):
    """The menu's link to href, with the menu opened first if it is shut."""
    set_menu(page, True)
    return page.locator(f'#shell-menu a[href="{href}"]')


def install_line_height_problems(line, label):
    """Switching tabs must not change the install line's height: the threshold centres it, so any
    difference moves the whole block (the longest follow-up line wraps, so every panel reserves it)."""
    heights, boxes = [], []
    for tab in line.locator('[role="tab"]').all():
        tab.click()
        name = tab.text_content().strip()
        heights.append((name, line.bounding_box()["height"]))
        boxes.append((name, line.locator('[role="tabpanel"]:not([hidden]) .install-line__box').bounding_box()["height"]))
    problems = []
    if max(height for _, height in heights) - min(height for _, height in heights) > 1:
        shown = ", ".join(f"{name} {height:.0f}px" for name, height in heights)
        problems.append(f"{label}: the install line changes height with the tab ({shown}); every panel should reserve the same height")
    # The panels share one cell as tall as the tallest (MCP's follow-up line wraps); a shorter panel must not hand that
    # extra height to its rows, or its command box grows (install_line.css, align-content on .install-line__panel).
    if max(height for _, height in boxes) - min(height for _, height in boxes) > 0.5:
        shown = ", ".join(f"{name} {height:.2f}px" for name, height in boxes)
        problems.append(f"{label}: the command boxes are not one height ({shown}); a shorter panel's rows are stretching "
                        f"to the shared cell (install_line.css: .install-line__panel needs align-content: start)")
    return problems


def install_tab_problems(page, line, label):
    """The selected tab differs from the others by more than colour (WCAG 1.4.1): it wears the
    flat fill of the menu's current link. Looked at with the pointer away and nothing keyboard-focused."""
    problems = []
    tabs = line.locator('[role="tab"]')
    fill = "(tab) => getComputedStyle(tab, '::before').backgroundColor"
    for chosen in range(tabs.count()):
        tabs.nth(chosen).click()
        page.mouse.move(0, 0)
        page.wait_for_timeout(300)
        for index in range(tabs.count()):
            filled = tabs.nth(index).evaluate(fill) not in ("rgba(0, 0, 0, 0)", "transparent")
            if filled != (index == chosen):
                name = (tabs.nth(index).text_content() or "").strip()
                problems.append(f"{label}: with {(tabs.nth(chosen).text_content() or '').strip()} chosen, the {name} tab "
                                f"{'has' if filled else 'lacks'} the selected fill; only the chosen tab should have it")
    return problems


def tab_state_problems(line, label, chosen, after, *, focused=True):
    """Only the chosen tab is selected (aria-selected="true", tabindex 0, the others false and -1), only its panel
    is shown, and, after a key or a click, focus is on it."""
    problems = []
    for index, tab in enumerate(line.evaluate(TAB_STATE)):
        wanted = {"aria-selected": "true" if index == chosen else "false", "tabindex": "0" if index == chosen else "-1",
                  "panel hidden": index != chosen}
        found = {"aria-selected": tab["selected"], "tabindex": tab["tabindex"], "panel hidden": tab["hidden"]}
        if focused:
            wanted["focus"], found["focus"] = index == chosen, tab["focused"]
        wrong = [f"{key} {found[key]!r} (expected {wanted[key]!r})" for key in wanted if found[key] != wanted[key]]
        if wrong:
            problems.append(f"{label}: after {after}, the {tab['name']} tab has {', '.join(wrong)}")
    return problems


def install_tab_keys_problems(page, line, label):
    """The tab keys: ArrowLeft wraps from the first tab to the last, Home and End go to the ends, and after
    each move exactly one tab is selected and in the tab order and exactly its panel is shown."""
    tabs = line.locator('[role="tab"]')
    last = tabs.count() - 1
    problems = []
    tabs.nth(0).click()
    for key, chosen in (("ArrowLeft", last), ("Home", 0), ("End", last), ("Home", 0)):
        page.keyboard.press(key)
        problems += tab_state_problems(line, label, chosen, key)
    return problems


# The parts of an install line that must not move or change width when Copy changes its word.
INSTALL_PARTS = (("box", ".install-line__box"), ("command", ".install-line__box code"), ("Copy button", "[data-copy]"))


def install_part_boxes(panel):
    return {part: panel.locator(selector).bounding_box() for part, selector in INSTALL_PARTS}


def install_part_problems(panel, before, label, word):
    """Copy now says `word` (Copied, or the longer Selected): the box, the command and the button have not moved."""
    problems = []
    for part, now in install_part_boxes(panel).items():
        if abs(now["x"] - before[part]["x"]) > 1 or abs(now["width"] - before[part]["width"]) > 1:
            problems.append(f"{label}: the {part} moves or changes width when Copy says {word} "
                            f"(x {before[part]['x']:.0f} to {now['x']:.0f}, width {before[part]['width']:.0f} to {now['width']:.0f})")
    return problems


def install_line_problems(page, line, label):
    """The install line (spec 4.2): tabs by click and arrow keys; Copy says Copied, tells screen
    readers and copies the command without its prompt; the tab's line appears after Copy."""
    problems = tab_state_problems(line, label, 0, "the page opened", focused=False)
    if line.evaluate("(line) => line.querySelector('.install-line__box').getBoundingClientRect().right"
                     " > line.getBoundingClientRect().right + 1"):
        problems.append(f"{label}: the install box is wider than the space it sits in; the command should scroll inside it")
    problems += install_line_height_problems(line, label)
    problems += install_tab_problems(page, line, label)
    problems += install_tab_keys_problems(page, line, label)
    # What follows assumes a working tablist (one panel showing, the one chosen). A broken one can defeat it with
    # a Playwright error, which must not end the run and throw away the tab problems found above.
    try:
        install_line_use_problems(page, line, label, problems)
    except PlaywrightError as error:
        problems.append(f"{label}: the install line could not be used any further: {error.message.splitlines()[0]}")
    return problems


def install_line_use_problems(page, line, label, problems):
    """The install line used as a visitor does, after its tabs have been looked at: a tab chosen by click and
    by arrow, the browser's own shortcuts left alone, Copy, its announcement and the command without its prompt.
    Adds to `problems` as it goes, so whatever was found before an error is kept."""
    tabs = line.locator('[role="tab"]')
    tabs.nth(1).click()
    shown = line.locator('[role="tabpanel"]:not([hidden]) [data-copy-text]')
    if (shown.text_content() or "").strip() != "npm install -g veilfile":
        problems.append(f"{label}: choosing npm does not show the npm command")
    page.keyboard.press("ArrowRight")
    if tabs.nth(2).get_attribute("aria-selected") != "true" or tabs.nth(2).evaluate("(tab) => tab !== document.activeElement"):
        problems.append(f"{label}: the right arrow does not move to the pip tab")
    tabs.nth(0).click()
    # A browser's own shortcuts (Alt+Left and Right, Cmd+Left and Right) belong to the browser, not the tabs.
    page.evaluate("() => { window.keyPrevented = null; document.addEventListener('keydown', "
                  "(event) => { if (event.key === 'ArrowRight') window.keyPrevented = event.defaultPrevented; }); }")
    page.keyboard.press("Alt+ArrowRight")
    if tabs.nth(0).get_attribute("aria-selected") != "true":
        problems.append(f"{label}: Alt and the right arrow change the tab; the browser's Forward shortcut is taken")
    if page.evaluate("() => window.keyPrevented"):
        problems.append(f"{label}: Alt and the right arrow are cancelled while a tab has focus")
    tabs.nth(0).click()  # back to MCP, whatever the shortcut did
    panel = line.locator('[role="tabpanel"]:not([hidden])')
    before = install_part_boxes(panel)
    panel.locator("[data-copy]").click()
    page.wait_for_timeout(300)
    if (panel.locator("[data-copy]").text_content() or "").strip() != "Copied":
        problems.append(f"{label}: Copy does not say Copied")
    problems += install_part_problems(panel, before, label, "Copied")
    if (line.locator("[data-copy-status]").text_content() or "").strip() != "Copied":
        problems.append(f"{label}: Copy is not announced to screen readers")
    if page.evaluate("() => navigator.clipboard.readText()") != "https://veilfile.com/mcp":
        problems.append(f"{label}: Copy did not put the MCP address on the clipboard")
    page.wait_for_timeout(1200)
    if panel.locator("[data-copy-then]").evaluate("(line) => getComputedStyle(line).opacity") != "1":
        problems.append(f"{label}: the line after Copy does not appear")
    # A second Copy within the 1.8 s is announced too: the status is cleared and set again, since a screen
    # reader may not read out a region that is set to the text it already has.
    status = line.locator("[data-copy-status]")
    page.wait_for_function("(node) => node.textContent === ''", arg=status.element_handle(), timeout=3000)
    status.evaluate("""(node) => { window.statusSeen = [];
        new MutationObserver(() => window.statusSeen.push(node.textContent))
            .observe(node, { childList: true, characterData: true, subtree: true }); }""")
    panel.locator("[data-copy]").click()
    page.wait_for_timeout(300)
    panel.locator("[data-copy]").click()
    page.wait_for_timeout(300)
    seen = iter(page.evaluate("() => window.statusSeen"))
    if not all(any(text == wanted for text in seen) for wanted in ("Copied", "", "Copied")):
        problems.append(f"{label}: a second Copy within 1.8 s is not announced again; the status should empty and be set again")
    # A command shown with a prompt ($ pip install veilfile) is copied without it.
    line.locator('[role="tab"]', has_text="pip").click()
    line.locator('[role="tabpanel"]:not([hidden]) [data-copy]').click()
    page.wait_for_timeout(300)
    copied = page.evaluate("() => navigator.clipboard.readText()")
    if copied != "pip install veilfile":
        problems.append(f"{label}: Copy on the pip tab put {copied!r} on the clipboard, not 'pip install veilfile'")


def come_in_problems(page, label):
    """Come in (spec 4.2): the line and COME IN fade out, then the install line appears alone, centred,
    already in place; focus moves to Copy; the install line then works."""
    problems = []
    line, install = page.locator("[data-threshold-line]"), page.locator("[data-install]")
    if not line.is_visible() or install.is_visible():
        return [f"{label}: the threshold does not start with the line and Come in"]
    page.locator("[data-come-in]").click()
    page.wait_for_timeout(1700)  # 0.7 s out, then 0.7 s in
    if line.is_visible() or not install.is_visible():
        return [f"{label}: Come in does not give way to the install line"]
    if not page.evaluate("() => Boolean(document.activeElement && document.activeElement.hasAttribute('data-copy'))"):
        problems.append(f"{label}: focus does not move to Copy")
    box, width = install.bounding_box(), page.viewport_size["width"]
    if abs(box["x"] + box["width"] / 2 - width / 2) > 2:
        problems.append(f"{label}: the install line is not centred")
    page.wait_for_timeout(300)
    if install.bounding_box() != box:
        problems.append(f"{label}: the install line moves after it appears")
    return problems + install_line_problems(page, install.locator("[data-tabs]"), label)


def try_click(problems, page, selector, label, **options):
    """Click the element; a Playwright timeout (nothing there, or something painted over it) is recorded as a
    problem, not left to end the run. True when the click happened. A link in the menu is pressed as a visitor
    does, MENU first when the menu is shut (it starts shut and an in-place change shuts it, spec 4.4)."""
    try:
        if selector.startswith("#shell-menu a"):
            set_menu(page, True)
        page.locator(selector).click(timeout=10000, **options)
        return True
    except PlaywrightError as error:
        problems.append(f"{label}: could not click {selector}: {error.message.splitlines()[0]}")
        return False


def try_history(problems, page, direction, label):
    """Go back or forward in the history; a Playwright timeout is recorded as a problem. True when it went."""
    try:
        getattr(page, f"go_{direction}")(timeout=10000)
        return True
    except PlaywrightError as error:
        problems.append(f"{label}: going {direction} failed: {error.message.splitlines()[0]}")
        return False


def try_state(problems, page, label):
    """PAGE_STATE of the page now showing; a Playwright error is recorded as a problem and gives None."""
    try:
        return page.evaluate(PAGE_STATE)
    except PlaywrightError as error:
        problems.append(f"{label}: could not read the page: {error.message.splitlines()[0]}")
        return None


def chapter_head_findings(page, label, title, address, mark):
    """A chapter changed in place has its own title, the one canonical address, and its own menu mark. A helper of the in-place change checks of the pages
    part and of the changes from the phone sheet, so not named ..._problems, which pages.tests_site_parts counts as a check that runs in one part only."""
    problems = []
    head = page.evaluate("""() => [document.title, document.querySelectorAll('link[rel="canonical"]').length,
        (document.querySelector('link[rel="canonical"]') || {}).href]""")
    if head != [title, 1, address]:
        problems.append(f"{label}: the title and canonical address read {head}")
    marks = page.evaluate("""() => [...document.querySelectorAll('#shell-menu a[aria-current]')]
        .map((a) => [a.textContent.trim(), a.getAttribute('aria-current')])""")
    if marks != [[mark, "page"]]:
        problems.append(f"{label}: the menu marks {marks}")
    return problems


def chapter_change_problems(browser, base_url, session_key, site_page):
    """Chapters change in place (spec 4.5), tried from About: the shell stays while the address, title,
    head, content and menu mark change together; back and forward work; a second click wins; anything
    unexpected (a failed fetch, another layout) is an ordinary page load. Review Focus 1, 2 and 4."""
    problems, url = [], base_url + site_page.path
    stayed = "() => window.stayedPut === true"

    def arrive(page, path, heading, label):
        try:
            page.wait_for_function(
                "([path, heading]) => location.pathname === path && document.querySelector('main h1')"
                " && document.querySelector('main h1').textContent.trim() === heading",
                arg=[path, heading], timeout=5000)
            page.wait_for_timeout(900)  # the cross-fade, or the fade back in
            return True
        except PlaywrightError:
            problems.append(f"{label}: never arrived at {path}")
            return False

    context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
    # How's page can be held on its way (for the two clicks below), so that the second click is certain to come
    # while the first change is still waiting: a change in place swaps within a frame of the page arriving.
    holding, held_how = [False], []
    context.route("**/how/", lambda route: held_how.append(route) if holding[0] and route.request.resource_type == "fetch" else route.continue_())
    page, errors = open_page(context, url)
    page.evaluate("() => { window.stayedPut = true; }")
    label = f"{site_page.path} to Plans in place"
    clicked = try_click(problems, page, '#shell-menu a[href="/plans/"]', label)  # from the open menu: MENU is pressed first
    if clicked and arrive(page, "/plans/", "Plans", label):
        if not page.evaluate(stayed):
            problems.append(f"{label}: the whole page reloaded")
        if page.locator("header [data-menu-toggle]").get_attribute("aria-expanded") != "false" or not menu_gone(page):
            problems.append(f"{label}: the menu is open after the change; a link in it leaves the menu closed at every width, as the next page of earendil.com arrives")
        problems += chapter_head_findings(page, label, "Plans | Veilfile", "https://veilfile.com/plans/", "Plans")
        if not page.evaluate("() => document.activeElement === document.querySelector('main h1')"):
            problems.append(f"{label}: focus does not move to the new heading")
    if clicked:  # there is a change to go back from
        if try_history(problems, page, "back", f"{label}, then back") and \
                arrive(page, "/about/", "A quiet place for what your agents make", f"{label}, then back") and \
                (not page.evaluate(stayed) or page.title() != "About | Veilfile"):
            problems.append(f"{label}, then back: About did not come back in place with its title")
        if try_history(problems, page, "forward", f"{label}, then forward"):
            arrive(page, "/plans/", "Plans", f"{label}, then forward")

    # The second of two clicks wins: Begin shows with its own title, canonical address and mark, and the click that
    # was overtaken (How, whose page arrives after Begin has been shown) leaves no history entry behind, so
    # exactly one was added. Both ways a change can go (VIEW_MODES): the fade-only path has timers of its own, and
    # the same case runs again without view transitions, in a page of its own, at the end.
    def second_click_wins(page, label, holding, held_how):
        entries = page.evaluate("() => history.length")
        holding[0] = True
        if try_click(problems, page, '#shell-menu a[href="/how/"]', label) and \
                try_click(problems, page, '#shell-menu a[href="/begin/"]', label) and arrive(page, "/begin/", "Begin", label):
            holding[0] = False
            for route in held_how:
                route.continue_()
            page.wait_for_timeout(1500)
            if page.evaluate("() => location.pathname") != "/begin/" or page.evaluate(
                    "() => document.querySelector('main').classList.contains('is-leaving')"):
                problems.append(f"{label}: the first click overrode the second, or the page stayed faded")
            problems.extend(chapter_head_findings(page, label, "Begin | Veilfile", "https://veilfile.com/begin/", "Begin"))
            added = page.evaluate("() => history.length") - entries
            if added != 1:
                problems.append(f"{label}: the history grew by {added} entries, not 1; the click that was overtaken should leave none")

    second_click_wins(page, f"{site_page.path} with two chapters clicked at once, with view transitions", holding, held_how)

    label = f"{site_page.path} to the threshold by the mark"
    if try_click(problems, page, ".shell-corner--top-left .lockup", label):
        try:
            page.wait_for_function("() => location.pathname === '/' && document.querySelector('[data-threshold-line]')", timeout=5000)
            page.wait_for_timeout(900)
            if not page.evaluate(stayed) or page.locator("#shell-menu").is_visible():
                problems.append(f"{label}: the threshold did not come in place with the menu closed")
        except PlaywrightError:
            problems.append(f"{label}: never arrived at the threshold")

    label = f"{site_page.path} to the dashboard, a page outside the chapters"
    page.goto(base_url + "/begin/", wait_until="networkidle")
    page.evaluate("() => { window.stayedPut = true; }")
    fetched = []
    page.on("request", lambda request: fetched.append(request.url) if request.resource_type == "fetch" else None)
    if try_click(problems, page, 'main a[href="/app/"]', label):  # signed out, /app/ redirects to sign-in
        try:
            page.wait_for_url("**/accounts/login/**", timeout=5000)
            if page.evaluate(stayed) or fetched:
                problems.append(f"{label}: should be an ordinary page load, never fetched first (fetched {fetched})")
        except PlaywrightError:
            problems.append(f"{label}: never arrived at sign-in")

    label = "back to the threshold after Come in"
    page.goto(base_url + "/", wait_until="networkidle")
    page.evaluate("() => { window.stayedPut = true; }")
    if try_click(problems, page, "[data-come-in]", label):
        page.wait_for_timeout(1700)
    if try_click(problems, page, "header [data-menu-toggle]", label) and \
            try_click(problems, page, '#shell-menu a[href="/begin/"]', label) and arrive(page, "/begin/", "Begin", label) and \
            try_history(problems, page, "back", label):
        try:
            page.wait_for_function("() => location.pathname === '/' && document.querySelector('[data-threshold-line]')"
                                   " && !document.querySelector('[data-threshold-line]').hidden", timeout=5000)
            if not page.evaluate(stayed) or page.locator("[data-install]").is_visible():
                problems.append(f"{label}: the threshold should come back in place, with the line and Come in")
        except PlaywrightError:
            problems.append(f"{label}: going back did not bring the line back")

    label = f"{site_page.path} to the Docs, another layout"
    loaded_docs = False
    if try_click(problems, page, "header [data-menu-toggle]", label):  # the threshold's menu starts closed
        came_from = try_state(problems, page, label)
        if came_from and try_click(problems, page, '#shell-menu a[href="/docs/"]', label):
            try:
                page.wait_for_url("**/docs/", timeout=5000)
                if page.evaluate(stayed):
                    problems.append(f"{label}: should load the Docs as an ordinary page")
                loaded_docs = True
            except PlaywrightError:
                problems.append(f"{label}: never arrived at the Docs")
    if loaded_docs:
        # Back across the full load returns to the page we came from, whether the browser restores it or loads
        # it again, and Forward returns to the Docs (Review Focus 2). A step that times out is a problem here.
        label = f"{site_page.path} to the Docs, then back and forward"
        docs = try_state(problems, page, label)
        if docs and try_history(problems, page, "back", label):
            try:
                page.wait_for_function("() => location.pathname !== '/docs/'", timeout=5000)
                page.wait_for_timeout(900)  # a fade back in, if the page was restored half way
            except PlaywrightError:
                problems.append(f"{label}: going back never left the Docs")
            else:
                back = try_state(problems, page, label)
                shown = ("path", "title", "canonical", "marks", "threshold", "heading")
                if back and any(back[key] != came_from[key] for key in shown):
                    problems.append(f"{label}: back reads { {key: back[key] for key in shown} }, "
                                    f"but the page we came from read { {key: came_from[key] for key in shown} }")
                if back and (back["opacity"] != "1" or back["leaving"]):
                    problems.append(f"{label}: <main> is not visible after going back ({back['opacity']})")
        if docs and try_history(problems, page, "forward", label):
            try:
                page.wait_for_url("**/docs/", timeout=5000)
                page.wait_for_timeout(300)
            except PlaywrightError:
                problems.append(f"{label}: going forward never returned to the Docs")
            else:
                forward = try_state(problems, page, label)
                if forward and any(forward[key] != docs[key] for key in ("path", "title", "canonical", "marks", "threshold")):
                    problems.append(f"{label}: forward reads {forward}, but the Docs read {docs}")
                if forward and (not forward["topHeading"] or forward["topHeading"] != docs["topHeading"]):
                    problems.append(f"{label}: forward shows the Docs heading {forward['topHeading']!r}, "
                                    f"but the Docs read {docs['topHeading']!r}")
                if forward and (forward["opacity"] != "1" or forward["leaving"] or not page.locator("main").is_visible()):
                    problems.append(f"{label}: <main> is not visible after going forward ({forward['opacity']})")
    problems += [f"{site_page.path}, changing chapters: console error: {error}" for error in errors]
    context.close()

    # A fetch that fails, answers with an error or redirects away: an ordinary page load, never a blank page.
    # The browser itself logs a load that failed or answered 500; nothing else may be logged.
    allowed = {"abort": ("net::ERR_FAILED",), "500": ("status of 500",), "redirect": ()}
    for failure in ("abort", "500", "redirect"):
        context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)

        def failing(failure):
            # One parameter only: Playwright hands a handler (route, request) for every parameter it names.
            def fail(route):
                if route.request.resource_type != "fetch":
                    return route.continue_()
                if failure == "abort":
                    return route.abort()
                if failure == "500":
                    return route.fulfill(status=500, body="Server error")
                return route.fulfill(status=302, headers={"Location": "/accounts/login/?next=/promises/"})
            return fail

        context.route("**/promises/", failing(failure))
        page, errors = open_page(context, url)
        page.evaluate("() => { window.stayedPut = true; }")
        label = f"{site_page.path} to Promises when the fetch fails ({failure})"
        if try_click(problems, page, '#shell-menu a[href="/promises/"]', label):
            try:
                page.wait_for_url("**/promises/", timeout=5000)
                heading = (page.locator("main h1").text_content() or "").strip()
                if page.evaluate(stayed) or heading != "What we promise":
                    problems.append(f"{label}: should have fallen back to an ordinary page load")
            except PlaywrightError:
                problems.append(f"{label}: never arrived at Promises")
        problems += [f"{label}: console error: {error}" for error in errors if not any(text in error for text in allowed[failure])]
        context.close()

    # A change dropped mid-fade, or a swap that fails, must not leave <main> faded out while the ordinary
    # page load that follows is under way (Review Focus 4): click a chapter, then during its fade another
    # whose fetch fails (or, on its own, a chapter whose swap throws); hold the ordinary load and watch <main>.
    # Each in both ways a change fades: a view transition never fades <main> itself, and the fade-only path must
    # give it back.
    for mode, scripts in VIEW_MODES:
        for failure in ("a second click whose fetch fails", "a swap that fails"):
            context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
            for script in scripts:
                context.add_init_script(script)
            held, seen, aborted = [], [], failure.startswith("a second")

            def hold(route):
                if route.request.resource_type == "fetch":
                    return route.abort() if aborted else route.continue_()
                held.append(route)  # the ordinary load waits until the check has looked at <main>

            context.route("**/begin/", hold)
            page, errors = open_page(context, url)
            page.evaluate("() => { window.stayedPut = true; }")
            # A page that is waiting on a navigation will not answer evaluate: <main> reports its own opacity.
            page.expose_function("reportOpacity", lambda opacity: seen.append(opacity))
            page.evaluate("() => setInterval(() => window.reportOpacity("
                          "getComputedStyle(document.querySelector('main')).opacity), 100)")
            label = f"{site_page.path} to Begin after {failure}, {mode}"
            started = True
            if aborted:
                started = try_click(problems, page, '#shell-menu a[href="/how/"]', label)
                page.wait_for_timeout(250)  # How's fade has begun
            else:
                page.evaluate("() => { history.pushState = () => { throw new Error('no history'); }; }")
            # the load is held: do not wait for it
            clicked = started and try_click(problems, page, '#shell-menu a[href="/begin/"]', label, no_wait_after=True)
            if clicked:
                page.wait_for_timeout(1800)  # long enough for a dropped change's fade to have finished
                if not held:
                    problems.append(f"{label}: should have fallen back to an ordinary page load")
                elif not seen or seen[-1] == "0":
                    problems.append(f"{label}: <main> is left faded out while the page loads")
            for route in held:
                route.continue_()
            if clicked and arrive(page, "/begin/", "Begin", label) and page.evaluate(stayed):
                problems.append(f"{label}: should have been an ordinary page load")
            problems += [f"{label}: console error: {error}" for error in errors
                         if not (aborted and "net::ERR_FAILED" in error)]
            context.close()

    # A fetch that never answers (Review Focus 4): click Plans, whose change starts to fade <main>, then How,
    # whose fetch is never answered. shell.js gives up on a fetch after 8 s (its shipped default, used here
    # as it is) and the latest change falls back to an ordinary page load. The load is held so that <main>
    # can be looked at while it is pending: it must not be left faded out.
    for mode, scripts in VIEW_MODES:
        context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
        for script in scripts:
            context.add_init_script(script)
        held, stalled, seen = [], [], []

        def stall(route):
            if route.request.resource_type == "fetch":
                return stalled.append(route)  # never answered
            held.append(route)  # the ordinary load waits until the check has looked at <main>

        context.route("**/how/", stall)
        page, errors = open_page(context, url)
        page.evaluate("() => { window.stayedPut = true; }")
        page.expose_function("reportOpacity", lambda opacity: seen.append(opacity))
        page.evaluate("() => setInterval(() => window.reportOpacity("
                      "getComputedStyle(document.querySelector('main')).opacity), 100)")
        label = f"{site_page.path} to How when its fetch never answers, {mode}"
        if try_click(problems, page, '#shell-menu a[href="/plans/"]', label):
            page.wait_for_timeout(250)  # Plans' fade has begun
            if try_click(problems, page, '#shell-menu a[href="/how/"]', label, no_wait_after=True):
                for _ in range(110):  # up to 11 s for the fetch to be given up on and the ordinary load to begin
                    if held:
                        break
                    page.wait_for_timeout(100)
                if not held:
                    problems.append(f"{label}: never fell back to an ordinary page load (<main> opacity {seen[-1] if seen else 'unknown'})")
                else:
                    page.wait_for_timeout(900)  # long enough for <main> to have faded back in
                    if not seen or seen[-1] != "1":
                        problems.append(f"{label}: <main> is left faded out while the page loads (opacity {seen[-1] if seen else 'unknown'})")
                    for route in held:
                        route.continue_()
                    if arrive(page, "/how/", "The life of a file", label) and page.evaluate(stayed):
                        problems.append(f"{label}: should have been an ordinary page load")
        problems += [f"{label}: console error: {error}" for error in errors]
        for route in stalled:  # let go of the fetch that was never answered, so closing the context is quiet
            try:
                route.abort()
            except PlaywrightError:
                pass  # shell.js had already given up on it
        context.close()

    # Motion off: the change is instant.
    context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion="off")
    page, errors = open_page(context, url)
    label = f"{site_page.path} with Motion off"
    if try_click(problems, page, '#shell-menu a[href="/plans/"]', label):
        page.wait_for_timeout(400)
        if (page.locator("main h1").text_content() or "").strip() != "Plans":
            problems.append(f"{label}: the chapter change is not instant")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    problems += reduced_motion_change_problems(browser, base_url, session_key, site_page)

    # A jump within the page (the skip link) belongs to the browser: nothing is fetched or swapped.
    context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
    page, errors = open_page(context, url)
    label = f"{site_page.path} skip link"
    page.evaluate("() => { window.swaps = 0; new MutationObserver(() => { window.swaps += 1; })"
                  ".observe(document.querySelector('main'), { childList: true }); }")
    page.locator(".skip-link").focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(1200)
    if page.evaluate("() => window.swaps") or not page.url.endswith("#main"):
        problems.append(f"{label}: a jump within the page should not swap the content")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    # A phone: the open menu sheet makes the page under it inert, so the new heading can take focus
    # only once the sheet has closed.
    context = new_context(browser, base_url, session_key, site_page, width=375, height=667, theme=None, motion=None)
    context.add_init_script(CHANGE_PROBE)
    page, errors = open_page(context, url)
    label = f"{site_page.path} to Plans in place on a phone"
    if try_click(problems, page, "header [data-menu-toggle]", label) and \
            try_click(problems, page, '#shell-menu a[href="/plans/"]', label) and arrive(page, "/plans/", "Plans", label):
        if page.locator("#shell-menu").is_visible() or page.evaluate("() => document.querySelector('main').inert"):
            problems.append(f"{label}: the menu sheet stays open, or the page stays inert")
        # A change chosen from the open sheet has no view transition (spec 4.5): the sheet closes by its own driver, the old content fades out with it and
        # the new fades in (sheet_change_problems has the frames), so there is no picture of the old chapter to be drawn above the sheet.
        calls = page.evaluate("() => window.__change.calls")
        if calls:
            problems.append(f"{label}: {len(calls)} view transition(s) began; a change chosen from the open menu sheet fades by its own driver and has none")
        if not page.evaluate("() => document.activeElement === document.querySelector('main h1')"):
            problems.append(f"{label}: focus does not move to the new heading")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    # Two chapters clicked at once, again where the browser has no view transitions: the fade out and the swap have timers
    # and a wait of their own, so the click that was overtaken is stopped there, not in the transition's swap.
    context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
    context.add_init_script(NO_VIEW_TRANSITIONS)
    holding, held_how = [False], []
    context.route("**/how/", lambda route: held_how.append(route) if holding[0] and route.request.resource_type == "fetch" else route.continue_())
    page, errors = open_page(context, url)
    label = f"{site_page.path} with two chapters clicked at once, without view transitions"
    second_click_wins(page, label, holding, held_how)
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    return problems


FADE_MS = 700   # --change-fade (tokens.css): an ordinary load's fade out, and the next page's fade in
# Watches ordinary loads from inside every page of a context (ordinary_fade_problems): each click on a link and whether shell.js took it (cancelled its
# default), <main>'s opacity and is-leaving and the menu's --sheet in every frame, the boot script's data-arriving, the flag shell.js leaves in
# sessionStorage for the next page (as the page began and as it went) and the moment it went (beforeunload, which a load fires as it starts,
# location.assign's as well as a link's). A page keeps what it saw in sessionStorage as it goes ("site-checks-left"), so the next can say what the last did.
ORDINARY_PROBE = """(() => {
  const now = () => performance.timeOrigin + performance.now();
  const flag = () => { try { return sessionStorage.getItem("veilfile-faded-nav"); } catch (error) { return "blocked"; } };
  const probe = window.__ordinary = { path: location.pathname, flag: flag(), arriving: [], frames: [], clicks: [], left: null };
  new MutationObserver((records) => records.forEach((record) => record.target === document.documentElement && probe.arriving.push(record.target.hasAttribute("data-arriving"))))
    .observe(document, { subtree: true, attributes: true, attributeFilter: ["data-arriving"] });   // <html> is not there yet when this runs
  const read = () => { const main = document.querySelector("main"), list = document.querySelector("#shell-menu");
    return main && { at: now(), opacity: +getComputedStyle(main).opacity, leaving: main.classList.contains("is-leaving"),
                     sheet: list ? +(getComputedStyle(list).getPropertyValue("--sheet") || 0) : null }; };
  const frame = () => { const seen = read(); if (seen) probe.frames.push(seen); if (probe.frames.length < 900) requestAnimationFrame(frame); };
  requestAnimationFrame(frame);
  document.addEventListener("click", (event) => { const link = event.target.closest && event.target.closest("a[href]");
    if (link) probe.clicks.push({ at: now(), href: link.getAttribute("href"), taken: null }); }, true);
  window.addEventListener("click", (event) => { const click = probe.clicks[probe.clicks.length - 1]; if (click && click.taken === null) click.taken = event.defaultPrevented; });
  window.addEventListener("beforeunload", () => { const toggle = document.querySelector("header [data-menu-toggle]");
    probe.left = { ...read(), path: location.pathname, flag: flag(), expanded: toggle && toggle.getAttribute("aria-expanded") };
    try { sessionStorage.setItem("site-checks-left", JSON.stringify(probe)); } catch (error) {} });
})();"""
# The page at the address has come and settled: the probe is its own, <main> is at full strength with nothing fading, and data-arriving has gone.
ORDINARY_SETTLED = """(path) => { const main = document.querySelector("main");
  return location.pathname === path && Boolean(window.__ordinary) && window.__ordinary.path === path && document.readyState === "complete" && Boolean(main)
    && !main.classList.contains("is-leaving") && getComputedStyle(main).opacity === "1" && !document.documentElement.hasAttribute("data-arriving"); }"""
# What the page that went saw (its probe, kept as it went, then forgotten so the next trip reads its own), what this page sees, and the flag now.
ORDINARY_READ = """() => { const left = JSON.parse(sessionStorage.getItem("site-checks-left")); sessionStorage.removeItem("site-checks-left");
  return [left, window.__ordinary, sessionStorage.getItem("veilfile-faded-nav")]; }"""
# Clicks made on links of the page by script (an Alt click, a middle button, a link with a target...), each stopped by a listener on window after shell.js has
# had it, so that nothing loads: whether shell.js took it, and <main> and the flag two frames later.
UNTOUCHED_CLICKS = """async (cases) => {
  const main = document.querySelector("main"), frames = () => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
  let taken = null;
  const stop = (event) => { taken = event.defaultPrevented; event.preventDefault(); };
  window.addEventListener("click", stop);
  const seen = [];
  for (const [name, selector, href, attributes, init] of cases) {
    let link = selector && document.querySelector(selector);
    if (!link) { link = document.createElement("a"); link.href = href; link.textContent = name;
      for (const [key, value] of Object.entries(attributes)) link.setAttribute(key, value);
      main.append(link); }
    taken = null;
    link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window, button: 0, ...init }));
    await frames();
    seen.push({ name, taken, leaving: main.classList.contains("is-leaving"), opacity: getComputedStyle(main).opacity, flag: sessionStorage.getItem("veilfile-faded-nav") });
    if (!selector) link.remove();
  }
  window.removeEventListener("click", stop);
  return seen; }"""


def ordinary_departure_findings(label, left, *, faded, menu=False):
    """What the page that went saw from the last link clicked (ORDINARY_PROBE). With faded: shell.js took the click, <main> faded out from it over about
    --change-fade (frames between, never coming back up) and the page went once it had, flagged for the next to fade in; with menu, the menu closed
    alongside, the two part way in the same frames. Without: the browser followed the link at once, nothing faded and no flag was set. A helper of
    ordinary_fade_problems, so not named ..._problems."""
    if not left or not left.get("left") or not left.get("clicks"):
        return [f"{label}: the page that went kept no record of the click and of going (ORDINARY_PROBE)"]
    click, went = left["clicks"][-1], left["left"]
    after = [frame for frame in left["frames"] if frame["at"] >= click["at"]]
    took = went["at"] - click["at"]
    if not faded:
        dimmed = any(frame["leaving"] or frame["opacity"] < 0.99 for frame in after)
        if click["taken"] or took > 300 or dimmed or went["flag"]:
            return [f"{label}: the browser should follow the link at once, with nothing faded and no flag; shell.js took the click: {click['taken']}, the page "
                    f"went {took:.0f} ms after it, <main> dimmed: {dimmed}, flag {went['flag']!r}"]
        return []
    problems = []
    if not click["taken"]:
        problems.append(f"{label}: shell.js did not take the click, so the page went without fading out")
    if not FADE_MS * 0.9 <= took <= FADE_MS * 2 + 600:
        problems.append(f"{label}: the page went {took:.0f} ms after the click; it should go once <main> has faded out over --change-fade ({FADE_MS} ms)")
    if went["opacity"] > 0.02 or not went["leaving"]:
        problems.append(f"{label}: <main> was at opacity {went['opacity']} (is-leaving: {went['leaving']}) as the page went; it should have faded out")
    between = [frame for frame in after if 0.05 < frame["opacity"] < 0.95]
    if len(between) < 2:
        problems.append(f"{label}: <main> showed {len(between)} frame(s) between full strength and gone; it should fade out, not cut")
    rises = [(a["opacity"], b["opacity"]) for a, b in zip(after, after[1:]) if b["opacity"] > a["opacity"] + 0.02]
    if rises:
        problems.append(f"{label}: <main> came back up while it faded out ({rises[:3]})")
    if went["flag"] != "1":
        problems.append(f"{label}: the flag for the next page to fade in was {went['flag']!r} as the page went, not '1'")
    if menu:
        both = [frame for frame in after if frame["sheet"] is not None and 0.1 < frame["sheet"] < 0.9 and 0.1 < frame["opacity"] < 0.9]
        if went["expanded"] != "false" or (went["sheet"] or 0) > 0.01 or not both:
            problems.append(f"{label}: the menu should close while <main> fades out, the two together, and be shut as the page goes; MENU said expanded "
                            f"{went['expanded']!r} and --sheet was {went['sheet']} as it went, and {len(both)} frame(s) had both part way")
    return problems


def ordinary_arrival_findings(label, now, flag, *, faded):
    """What the page that came saw of itself (ORDINARY_PROBE), and the flag once shell.js had run. With faded: it came flagged, <main> was hidden from its
    first frame (data-arriving, set by the boot script and taken away by shell.js) and faded in over about --change-fade, never falling back. Without: it
    came unflagged and showed <main> at full strength from its first frame. Either way the flag is gone, so a later load does not fade in. A helper of
    ordinary_fade_problems."""
    problems = [] if flag is None else [f"{label}: the flag is still {flag!r} after the page came; a later direct load or reload would fade in"]
    frames = now["frames"]
    if not frames:
        return problems + [f"{label}: the page drew no frame of <main>"]
    if not faded:
        dimmed = [frame for frame in frames if frame["opacity"] < 0.99 or frame["leaving"]]
        if now["flag"] or now["arriving"] or dimmed:
            problems.append(f"{label}: the page should come in at once with nothing faded; it came with the flag {now['flag']!r}, data-arriving went "
                            f"{now['arriving']} and {len(dimmed)} frame(s) of <main> were under full strength")
        return problems
    if now["flag"] != "1":
        problems.append(f"{label}: the page came without the flag (it read {now['flag']!r}), so it could not fade in")
    if now["arriving"][:1] != [True] or now["arriving"][-1:] != [False]:
        problems.append(f"{label}: data-arriving went {now['arriving']}; the boot script should set it and shell.js take it away")
    if frames[0]["opacity"] > 0.01:
        problems.append(f"{label}: <main> showed at opacity {frames[0]['opacity']} in its first frame; it should be hidden from the first paint and fade in")
    hidden = max((index for index, frame in enumerate(frames) if frame["opacity"] <= 0.01), default=0)
    full = next((index for index, frame in enumerate(frames) if index > hidden and frame["opacity"] >= 0.99), None)
    if full is None:
        return problems + [f"{label}: <main> never came to full strength"]
    span = frames[full]["at"] - frames[hidden]["at"]
    between = [frame for frame in frames[hidden:full] if 0.05 < frame["opacity"] < 0.95]
    if len(between) < 2 or span < FADE_MS * 0.6:
        problems.append(f"{label}: <main> came in over {span:.0f} ms with {len(between)} frame(s) between hidden and full strength; "
                        f"it should fade in over --change-fade ({FADE_MS} ms)")
    falls = [(a["opacity"], b["opacity"]) for a, b in zip(frames[hidden:], frames[hidden + 1:]) if b["opacity"] < a["opacity"] - 0.02]
    if falls:
        problems.append(f"{label}: <main> fell back while it faded in ({falls[:3]})")
    return problems


def ordinary_fade_problems(browser, base_url, session_key):
    """An ordinary load fades too (spec 4.5): a link to a page outside the page's layout family (or any link on a form or error page, which have none)
    fades <main> out over --change-fade, the menu closing alongside when the link is in it, and the page that comes is hidden from its first paint and fades
    in. Tried from a chapter to a form page by a link in the page, from a form page to the threshold by the mark, from a chapter to the Docs by the menu at
    1280 and from the phone's sheet at 375. A change in place does not fade like this as well. Motion off and reduced motion go at once with no flag; a
    modified click, a middle button, a link with a target or download, a #fragment, another site and a plain file (llms.txt) are the browser's. A newer link
    while <main> fades out goes at once, and the load it overtook never goes (a change in place overtakes it too). A reload, a direct load and Back never
    fade, and a page the browser keeps for Back is shown at once. With the next page's shell.js late <main> stays hidden until it comes; with it failing
    <main> shows. A load that brings no page (a 204) gives the page back."""
    problems = []
    about = page_named("/about/")

    def context_for(**options):
        context = new_context(browser, base_url, session_key, about, theme=None, **{"width": 1280, "motion": "on", **options})
        context.add_init_script(ORDINARY_PROBE)
        return context

    def trip(page, label, selector, path, *, faded=True, menu=False):
        """Click the link and judge the way out and the way in; False when the page at path never came. A link in the menu is clicked once the menu is
        open and at rest, as a visitor would, so that its close is seen from the start."""
        if menu:
            set_menu(page, True)
            try:
                page.wait_for_function(SHEET_IN_PLACE, timeout=5000)
            except PlaywrightError:
                problems.append(f"{label}: the menu never came to rest open")
        if not try_click(problems, page, selector, label, no_wait_after=True):
            return False
        try:
            page.wait_for_function(ORDINARY_SETTLED, arg=path, timeout=10000)
        except PlaywrightError as error:
            problems.append(f"{label}: never came to {path}: {error.message.splitlines()[0]}")
            return False
        left, now, flag = page.evaluate(ORDINARY_READ)
        problems.extend(ordinary_departure_findings(label, left, faded=faded, menu=menu))
        problems.extend(ordinary_arrival_findings(label, now, flag, faded=faded))
        return True

    def at_rest(page, label, how):
        """After a load that must not fade (a reload, a direct load, Back): <main> at full strength at once, nothing faded, no flag. The frames are judged on a
        page loaded again, not on one the browser kept (window.__kept, set before it was left), whose frames are of its own fade out."""
        state = page.evaluate("""() => { const main = document.querySelector("main");
            return { opacity: getComputedStyle(main).opacity, leaving: main.classList.contains("is-leaving"), running: main.getAnimations().length,
                     arriving: document.documentElement.hasAttribute("data-arriving"), flag: sessionStorage.getItem("veilfile-faded-nav"),
                     probe: window.__ordinary && !window.__kept && { flag: window.__ordinary.flag, arriving: window.__ordinary.arriving,
                       dimmed: window.__ordinary.frames.filter((frame) => frame.opacity < 0.99 || frame.leaving).length } }; }""")
        probe = state["probe"] or {}
        if state["opacity"] != "1" or state["leaving"] or state["running"] or state["arriving"] or state["flag"] or probe.get("arriving") or probe.get("dimmed"):
            problems.append(f"{label}: {how} should show <main> at once with nothing faded and no flag; it read {state}")

    # Motion on, at 1280: a chapter to a form page by a link in the page (Begin's Dashboard link, which sign in answers for a visitor signed out), a reload
    # of it, the form page to the threshold by the mark, a change in place from there, the Docs from the menu, Back, and a direct load.
    context = context_for()
    page, errors = open_page(context, base_url + "/begin/")
    label = "ordinary loads: Begin to sign in by a link in the page"
    if trip(page, label, 'main a[href="/app/"]', "/accounts/login/"):
        page.reload(wait_until="load")
        try:
            page.wait_for_function(ORDINARY_SETTLED, arg="/accounts/login/", timeout=10000)
            at_rest(page, f"{label}, then a reload", "a reload")
        except PlaywrightError:
            problems.append(f"{label}, then a reload: the page never settled")
        trip(page, "ordinary loads: sign in to the threshold by the mark", ".shell-corner--top-left .lockup", "/")
    page.evaluate("() => { window.stayedPut = true; window.__ordinary.clicks = []; window.__ordinary.frames = []; }")
    label = "ordinary loads: the threshold to About, a change in place"
    if try_click(problems, page, '#shell-menu a[href="/about/"]', label) and settle_on(page, "/about/", "A quiet place for what your agents make"):
        state = page.evaluate("""() => ({ stayed: window.stayedPut === true, flag: sessionStorage.getItem("veilfile-faded-nav"),
            clicks: window.__ordinary.clicks, leaving: window.__ordinary.frames.filter((frame) => frame.leaving).length })""")
        # Chromium has view transitions, so a change in place cross-fades and never sets is-leaving: no fade out of an ordinary load on top of it.
        if not state["stayed"] or state["flag"] or state["leaving"] or [click["taken"] for click in state["clicks"]][-1:] != [True]:
            problems.append(f"{label}: should change in place, cross-faded, with no ordinary fade and no flag; it read {state}")
    page.evaluate("() => { window.stayedPut = undefined; window.__kept = true; }")
    label = "ordinary loads: About to the Docs from the menu at 1280"
    if trip(page, label, '#shell-menu a[href="/docs/"]', "/docs/", menu=True):
        try:
            page.go_back(wait_until="load")
            page.wait_for_function("() => location.pathname === '/about/' && document.readyState === 'complete'", timeout=10000)
            at_rest(page, f"{label}, then Back", "Back")
        except PlaywrightError as error:
            problems.append(f"{label}, then Back: {error.message.splitlines()[0]}")
    page.goto(base_url + "/promises/", wait_until="load")
    try:
        page.wait_for_function(ORDINARY_SETTLED, arg="/promises/", timeout=10000)
        at_rest(page, "ordinary loads: a direct load after them", "a direct load")
    except PlaywrightError:
        problems.append("ordinary loads: a direct load after them never settled")
    # A page the browser keeps as it was left and gives back for Back (Playwright's Chromium keeps none, so its pageshow is played): shown at once.
    kept = page.evaluate("""() => { const main = document.querySelector("main"); main.classList.add("is-leaving"); sessionStorage.setItem("veilfile-faded-nav", "1");
        window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
        return { opacity: getComputedStyle(main).opacity, leaving: main.classList.contains("is-leaving"), running: main.getAnimations().length,
                 flag: sessionStorage.getItem("veilfile-faded-nav") }; }""")
    if kept != {"opacity": "1", "leaving": False, "running": 0, "flag": None}:
        problems.append(f"ordinary loads: a page kept for Back and shown again (pageshow, persisted) should show <main> at once and clear the flag; it read {kept}")
    problems += [f"ordinary loads, at 1280: console error: {error}" for error in errors]
    context.close()

    # From the open phone sheet at 375: the sheet closes by its driver while <main> fades out.
    context = context_for(width=375, height=667)
    page, errors = open_page(context, base_url + "/about/")
    trip(page, "ordinary loads: About to the Docs from the phone's sheet", '#shell-menu a[href="/docs/"]', "/docs/", menu=True)
    problems += [f"ordinary loads, from the phone's sheet: console error: {error}" for error in errors]
    context.close()

    # Motion off, and reduced motion with nothing saved: the browser follows the link at once, no fade, no flag.
    for how, options in (("Motion off", {"motion": "off"}), ("reduced motion with nothing saved", {"motion": None, "reduced_motion": "reduce"})):
        context = context_for(**options)
        page, errors = open_page(context, base_url + "/about/")
        trip(page, f"ordinary loads with {how}: About to the Docs", '#shell-menu a[href="/docs/"]', "/docs/", faded=False)
        problems += [f"ordinary loads with {how}: console error: {error}" for error in errors]
        context.close()

    # The links shell.js leaves to the browser, each clicked by script and stopped once shell.js has had it.
    context = context_for()
    page, errors = open_page(context, base_url + "/about/")
    cases = [[f"{key} click", None, "/docs/", {}, {key.lower() + "Key": True}] for key in ("Ctrl", "Meta", "Shift", "Alt")]
    cases += [["middle button", None, "/docs/", {}, {"button": 1}], ["a link with a target", None, "/docs/", {"target": "_blank"}, {}],
              ["a download", None, "/docs/", {"download": ""}, {}], ["a #fragment", None, "/docs/#sections", {}, {}],
              ["another site", None, "https://example.com/", {}, {}], ["llms.txt, a plain file, from the menu", '#shell-menu a[href="/llms.txt"]', None, {}, {}]]
    for seen in page.evaluate(UNTOUCHED_CLICKS, cases):
        if seen["taken"] or seen["leaving"] or seen["opacity"] != "1" or seen["flag"]:
            problems.append(f"ordinary loads: {seen['name']} should be left to the browser, with nothing faded and no flag; it read {seen}")
    problems += [f"ordinary loads, links left to the browser: console error: {error}" for error in errors]
    context.close()

    # A newer link while <main> fades out: from the sign-in page the mark, then Sign up part way through the fade. Sign up comes at once, faded in, and the
    # threshold is never asked for.
    context = context_for()
    page, errors = open_page(context, base_url + "/accounts/login/")
    navigations = []
    page.on("request", lambda request: navigations.append(request.url) if request.is_navigation_request() and request.frame == page.main_frame else None)
    label = "ordinary loads: the mark and then Sign up while sign in fades out"
    part_way = "() => { const opacity = +getComputedStyle(document.querySelector('main')).opacity; return opacity > 0.1 && opacity < 0.9; }"
    if try_click(problems, page, ".shell-corner--top-left .lockup", label, no_wait_after=True):
        try:
            page.wait_for_function(part_way, timeout=5000)
            page.locator('.form-links a[href="/accounts/signup/"]').click(timeout=5000, no_wait_after=True)
            page.wait_for_function(ORDINARY_SETTLED, arg="/accounts/signup/", timeout=10000)
            left, now, flag = page.evaluate(ORDINARY_READ)
            clicks, went = (left or {}).get("clicks") or [], (left or {}).get("left") or {}
            first, second = clicks[-2:] if len(clicks) >= 2 else ({}, {})
            if not first.get("taken") or second.get("taken") is not False or went.get("at", 0) - second.get("at", 0) > 300 or went.get("flag") != "1":
                problems.append(f"{label}: the mark should start the fade and Sign up go at once, flagged; it read clicks {left and left.get('clicks')}, went {went}")
            problems.extend(ordinary_arrival_findings(label, now, flag, faded=True))
            if any(url.rstrip("/") == base_url.rstrip("/") for url in navigations):
                problems.append(f"{label}: the threshold, which Sign up overtook, was still asked for ({navigations})")
        except PlaywrightError as error:
            problems.append(f"{label}: {error.message.splitlines()[0]}")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    # A change in place overtakes an ordinary load still fading out: the Docs from the menu, then the mark (the threshold is About's family). The threshold
    # comes in place and the Docs are never asked for.
    context = context_for()
    page, errors = open_page(context, base_url + "/about/")
    navigations = []
    page.on("request", lambda request: navigations.append(request.url) if request.is_navigation_request() and request.frame == page.main_frame else None)
    page.evaluate("() => { window.stayedPut = true; }")
    label = "ordinary loads: the Docs from the menu and then the mark while About fades out"
    if try_click(problems, page, '#shell-menu a[href="/docs/"]', label, no_wait_after=True):
        try:
            page.wait_for_function(part_way, timeout=5000)
            page.locator(".shell-corner--top-left .lockup").click(timeout=5000)
            page.wait_for_function("""() => location.pathname === '/' && document.querySelector('[data-threshold-line]')
                && !document.querySelector('main').classList.contains('is-leaving') && getComputedStyle(document.querySelector('main')).opacity === '1'""", timeout=10000)
            page.wait_for_timeout(FADE_MS * 3)   # the Docs would have been asked for by now
            state = page.evaluate("() => ({ stayed: window.stayedPut === true, flag: sessionStorage.getItem('veilfile-faded-nav'), path: location.pathname })")
            if not state["stayed"] or state["flag"] or state["path"] != "/" or any("/docs/" in url for url in navigations):
                problems.append(f"{label}: the threshold should come in place with no flag and the Docs never asked for; it read {state}, asked for {navigations}")
        except PlaywrightError as error:
            problems.append(f"{label}: {error.message.splitlines()[0]}")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    # An ordinary load overtakes a change in place still on its way: Plans from the menu with its page held, then the Docs from the menu, then Plans' page let
    # go. The Docs come, and Plans is never swapped in: the page that went was About, faded out.
    context = context_for()
    held = []
    context.route("**/plans/", lambda route: held.append(route) if route.request.resource_type == "fetch" else route.continue_())
    page, errors = open_page(context, base_url + "/about/")
    label = "ordinary loads: Plans in place, held on its way, and then the Docs from the menu"
    set_menu(page, True)
    if try_click(problems, page, '#shell-menu a[href="/plans/"]', label):
        for _ in range(100):
            if held:
                break
            page.wait_for_timeout(50)
        if not held:
            problems.append(f"{label}: Plans' page was never asked for, so there was no change in place to overtake")
        elif try_click(problems, page, '#shell-menu a[href="/docs/"]', label, no_wait_after=True):
            page.wait_for_function(part_way, timeout=5000)
            for route in held:
                route.continue_()
            try:
                page.wait_for_function(ORDINARY_SETTLED, arg="/docs/", timeout=10000)
                left, now, flag = page.evaluate(ORDINARY_READ)
                went = (left or {}).get("left") or {}
                if went.get("path") != "/about/" or not went.get("leaving") or went.get("opacity", 1) > 0.02:
                    problems.append(f"{label}: the Docs should overtake Plans, which is never swapped in; the page went from {went.get('path')} with <main> at "
                                    f"opacity {went.get('opacity')} (is-leaving: {went.get('leaving')})")
                problems.extend(ordinary_arrival_findings(label, now, flag, faded=True))
            except PlaywrightError as error:
                problems.append(f"{label}: never came to the Docs: {error.message.splitlines()[0]}")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    # The next page's shell.js late: <main> stays hidden from the first frame until it comes, then fades in (never shown and blinked out). Failing: <main> shows.
    for failure in ("late", "failing"):
        context = context_for()
        holding, held = [False], []

        def holder(failure):
            # One parameter only: Playwright hands a handler (route, request) for every parameter it names.
            def hold(route):
                if not holding[0]:
                    return route.continue_()
                return held.append(route) if failure == "late" else route.abort()
            return hold

        context.route(SHELL_SCRIPT, holder(failure))
        page, errors = open_page(context, base_url + "/about/")
        holding[0] = True
        label = f"ordinary loads: About to the Docs with the Docs' shell.js {failure}"
        if try_click(problems, page, '#shell-menu a[href="/docs/"]', label, no_wait_after=True):
            try:
                if failure == "late":
                    page.wait_for_function("() => window.__ordinary && window.__ordinary.path === '/docs/' && window.__ordinary.frames.length >= 20", timeout=10000)
                    early = page.evaluate("() => window.__ordinary.frames.map((frame) => frame.opacity)")
                    if not early or max(early) > 0.01:
                        problems.append(f"{label}: <main> showed (opacity up to {max(early or [None])}) before shell.js came; it should stay hidden and fade in once it runs")
                    holding[0] = False
                    for route in held:
                        route.continue_()
                    page.wait_for_function(ORDINARY_SETTLED, arg="/docs/", timeout=10000)
                    left, now, flag = page.evaluate(ORDINARY_READ)
                    problems.extend(ordinary_arrival_findings(label, now, flag, faded=True))
                else:
                    page.wait_for_function("""() => location.pathname === '/docs/' && !document.documentElement.classList.contains('js')
                        && getComputedStyle(document.querySelector('main')).opacity === '1'""", timeout=10000)
            except PlaywrightError as error:
                problems.append(f"{label}: {error.message.splitlines()[0]}")
        problems += [f"{label}: console error: {error}" for error in errors if failure == "late" or ("shell.js" not in error and "net::ERR_FAILED" not in error)]
        context.close()

    # A load that brings no page (a 204, or a download): the page stays, so after shell.js has waited for one (FETCH_TIMEOUT_MS, 8 s) <main> is given back and
    # the flag cleared.
    context = context_for()
    context.route("**/docs/", lambda route: route.fulfill(status=204) if route.request.resource_type == "document" else route.continue_())
    page, errors = open_page(context, base_url + "/about/")
    label = "ordinary loads: About to an address that answers 204"
    if try_click(problems, page, '#shell-menu a[href="/docs/"]', label, no_wait_after=True):
        try:
            page.wait_for_function("""() => location.pathname === '/about/' && !document.querySelector('main').classList.contains('is-leaving')
                && getComputedStyle(document.querySelector('main')).opacity === '1' && sessionStorage.getItem('veilfile-faded-nav') === null
                && window.__ordinary.frames.some((frame) => frame.leaving)""", timeout=14000)
        except PlaywrightError:
            problems.append(f"{label}: <main> was not given back with the flag cleared within 14 s; the page stays when no page comes in its place")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    return problems


def settle_on(page, path, heading):
    """Wait for an in-place change to arrive at a chapter and finish cross-fading (or fading back in); False if it never arrives."""
    try:
        page.wait_for_function(
            "([path, heading]) => location.pathname === path && document.querySelector('main h1')"
            " && document.querySelector('main h1').textContent.trim() === heading",
            arg=[path, heading], timeout=5000)
        page.wait_for_timeout(900)  # the cross-fade, or the fade back in
        return True
    except PlaywrightError:
        return False


def reduced_motion_change_problems(browser, base_url, session_key, site_page):
    """A system that asks for reduced motion follows the motion rule of base.css and shell.js: with nothing saved
    Motion is off and an in-place chapter change is instant (no view transition, no fade of <main>); with Motion
    saved on, the visitor's choice wins and the content cross-fades like any other change."""
    problems, url = [], base_url + site_page.path
    for saved, fades in ((None, False), ("on", True)):
        context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=saved,
                              reduced_motion="reduce")
        context.add_init_script(CHANGE_PROBE)
        page, errors = open_page(context, url)
        label = f"{site_page.path} with reduced motion and {saved or 'nothing'} saved, to Plans in place"
        if try_click(problems, page, '#shell-menu a[href="/plans/"]', label):
            if settle_on(page, "/plans/", "Plans"):
                marks = page.evaluate("() => window.__change")
                ran = len(marks["calls"]) + len(marks["ends"])  # view transitions, and the fades of <main> where there are none
                if fades and not ran:
                    problems.append(f"{label}: no view transition or fade ran; the content should cross-fade")
                elif not fades and ran:
                    problems.append(f"{label}: {len(marks['calls'])} view transition(s) and {len(marks['ends'])} fade(s) of <main> ran; "
                                    f"the change should be instant")
                if not fades and marks["swapped"] - marks["fetched"] > 100:
                    problems.append(f"{label}: the swap came {marks['swapped'] - marks['fetched']:.0f} ms after the page arrived; it should be at once")
                if page.evaluate("() => getComputedStyle(document.querySelector('main')).opacity") != "1":
                    problems.append(f"{label}: <main> is not fully showing after the change")
            else:
                problems.append(f"{label}: never arrived at Plans")
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()
    return problems


def token_ms(page, name):
    """A time token of tokens.css (".35s", "700ms") in milliseconds; None when the page has no such token."""
    found = re.fullmatch(r"([\d.]+)(m?s)", page.evaluate(TOKEN, name))
    return float(found.group(1)) * (1000 if found.group(2) == "s" else 1) if found else None


def chapter_transition_problems(browser, base_url, session_key, site_page):
    """An in-place chapter change cross-fades at Come in's pace (spec 4.5), timed from inside the page (CHANGE_PROBE).
    Where the browser has view transitions (Chromium does), Motion on uses one, scoped to <main> so the cloth and
    the corners stay live, lasting about --change-fade (500 to 1000 ms from its start to its end), and the content
    is swapped as soon as the page has arrived: no wait of the script's own. With Motion off, or reduced motion
    and nothing saved, there is none and the swap is instant. Where the browser has none, <main> fades out from
    the click, while the page is on its way (a fetch held for 600 ms), and in again, each half --change-fade:
    the whole change takes the fetch plus two fades and a little."""
    problems, url = [], base_url + site_page.path

    def opened(*, motion="on", reduced="no-preference", scripts=(), route=None):
        context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=motion, reduced_motion=reduced)
        for script in (*scripts, CHANGE_PROBE):
            context.add_init_script(script)
        if route:
            context.route("**/plans/", route)
        page, errors = open_page(context, url)
        return context, page, errors

    def waits(page, label, condition):
        try:
            page.wait_for_function(condition, timeout=8000)
            return True
        except PlaywrightError:
            problems.append(f"{label}: never finished ({page.evaluate('() => window.__change')})")
            return False

    label = f"{site_page.path} to Plans in place with Motion on"
    context, page, errors = opened()
    fade = token_ms(page, "--change-fade")
    if fade is None:
        problems.append(f"{label}: tokens.css has no --change-fade")
    named = page.evaluate(MAIN_NAME)
    if named != "none":
        problems.append(f"{label}: <main> is named {named!r} for view transitions while no change runs; it would be a stacking context and "
                        f"a backdrop root all the time, and the cloth would not show through any backdrop blur inside it")
    menu_link(page, "/plans/").click()
    if waits(page, label, "() => window.__change.calls.length > 0 && window.__change.calls.every((call) => call.finished !== null)"):
        marks = page.evaluate("() => window.__change")
        call = marks["calls"][0]
        if call["names"] != {"call": "chapter", "updated": "chapter", "ready": "chapter"}:
            problems.append(f"{label}: <main> should be named 'chapter' for the whole cross-fade; it was {call['names']} when the transition was "
                            f"called, when the content was swapped and when the animation began")
        page.wait_for_timeout(100)
        named = page.evaluate(MAIN_NAME)
        if named != "none":
            problems.append(f"{label}: <main> is still named {named!r} once the cross-fade has finished; the name is for the change only")
        # The animation's own length, not the time between two frames, which a slow machine stretches.
        if call["fadeMs"] is None or not 500 <= call["fadeMs"] <= 1000:
            problems.append(f"{label}: the cross-fade lasts {call['fadeMs']} ms; it should last about --change-fade ({fade} ms), between 500 and 1000")
        if len(marks["calls"]) != 1:
            problems.append(f"{label}: {len(marks['calls'])} view transitions ran; one change should run one")
        if marks["leaving"] is not None:
            problems.append(f"{label}: <main> was faded out (is-leaving) as well as cross-faded; the view transition is the fade")
        waited = [wait for wait in marks["waits"] if wait != FONT_BOUND_MS]   # the bound on a face that is still loading (shell.js, FONT_WAIT_MS) is not a wait of the script's own
        if waited:  # the old script waited a whole fade (700 ms) after the page arrived; the view transition is called at once
            problems.append(f"{label}: a script waited {waited} ms after the page arrived and before swapping the content; "
                            f"the view transition swaps within a frame")
        if call["style"] and (call["style"]["rootAnimations"] or call["style"]["rootName"] != "none"):
            problems.append(f"{label}: the rest of the page (the cloth and the corners) should stay live while <main> cross-fades, not be replaced by a "
                            f"picture of itself that takes the pointer (the root's view-transition-name is {call['style']['rootName']!r}; "
                            f"{call['style']['rootAnimations']} animation(s) of ::view-transition-*(root) ran)")
        if call["style"] and call["style"]["pointer"] != "none":
            problems.append(f"{label}: ::view-transition takes pointer events ({call['style']['pointer']}); the page under the cross-fade should be hit instead")
        if call["style"] and fade is not None and call["style"]["duration"] != f"{fade / 1000:g}s":
            problems.append(f"{label}: the chapter's group lasts {call['style']['duration']}, not --change-fade ({fade / 1000:g}s)")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    for how, motion, reduced in (("Motion off", "off", "no-preference"), ("reduced motion and nothing saved", None, "reduce")):
        label = f"{site_page.path} to Plans in place with {how}"
        context, page, errors = opened(motion=motion, reduced=reduced)
        menu_link(page, "/plans/").click()
        if waits(page, label, "() => window.__change.swapped !== null"):
            marks = page.evaluate("() => window.__change")
            if marks["calls"] or marks["leaving"] is not None or marks["ends"]:
                problems.append(f"{label}: the change should be instant, but it used {len(marks['calls'])} view transition(s) and "
                                f"{len(marks['ends'])} fade(s) of <main>")
            if marks["swapped"] - marks["fetched"] > 100:
                problems.append(f"{label}: the swap came {marks['swapped'] - marks['fetched']:.0f} ms after the page arrived; it should be at once")
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()

    # A newer change begins while the first cross-fades: the browser skips the first transition, and its end must not
    # take the name from <main> under the second (which would then have nothing to cross-fade: the new chapter would
    # simply appear). The second click is made in the page, so that this proves the name and not what the pointer can reach.
    label = f"{site_page.path} to Plans, then Begin while Plans cross-fades"
    context, page, errors = opened()
    page.add_style_tag(content=SLOW_CHANGE)  # Begin's click needs a fetch first: with a cross-fade of 2 s it always lands inside Plans'
    menu_link(page, "/plans/").click()
    if waits(page, label, "() => window.__change.calls.length > 0 && window.__change.calls[0].ready !== null"):
        page.evaluate("() => document.querySelector('#shell-menu a[href=\"/begin/\"]').click()")
        if waits(page, label, "() => location.pathname === '/begin/' && window.__change.calls.length === 2 && window.__change.calls.every((call) => call.finished !== null)"):
            second = page.evaluate("() => window.__change.calls[1]")
            if second["names"]["ready"] != "chapter" or second["fadeMs"] is None:
                problems.append(f"{label}: the newer change has no 'chapter' to cross-fade (<main> was named {second['names']['ready']!r} when its animation began, "
                                f"its cross-fade lasted {second['fadeMs']}): the end of the first transition took the name away")
            page.wait_for_timeout(100)
            named = page.evaluate(MAIN_NAME)
            if named != "none":
                problems.append(f"{label}: <main> is still named {named!r} after both changes have finished")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    # A newer click while the older change's content is still to be swapped in. The browser calls the update callback a
    # frame or more after startViewTransition, and a click in that gap is not seen by the swap unless it looks: the
    # overtaken change would then show its page for a moment and leave a history entry of its own.
    label = f"{site_page.path} to Plans, then Begin before Plans has been swapped in"
    context, page, errors = opened(scripts=(LATE_SWAP,))
    entries = page.evaluate("() => history.length")
    menu_link(page, "/plans/").click()
    if waits(page, label, "() => window.lateSwaps === 1"):
        page.evaluate("() => document.querySelector('#shell-menu a[href=\"/begin/\"]').click()")
        if waits(page, label, "() => location.pathname === '/begin/' && document.querySelector('main h1').textContent.trim() === 'Begin'"):
            page.wait_for_timeout(1500)  # the overtaken change's swap comes 600 ms after its transition began, if it is going to
            shown = page.evaluate("() => [location.pathname, window.pushed, history.length]")
            if shown[1] != ["/begin/"]:
                problems.append(f"{label}: the pages put on the history were {shown[1]}; only /begin/ should be, the overtaken change should not swap "
                                f"its page in at all")
            if shown[0] != "/begin/" or shown[2] - entries != 1:
                problems.append(f"{label}: ended on {shown[0]} with {shown[2] - entries} new history entries, not /begin/ with 1")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    # A browser that captures the root all the same (one that does not honour view-transition-name: none on it, taken here by
    # naming the root again): the cloth and the corners must not be replaced by two pictures of themselves cross-fading, which is
    # what the root's own rules in base.css prevent: its old picture is not shown and its new one does not fade in.
    label = f"{site_page.path} to Plans in place with the root captured"
    context, page, errors = opened()
    page.add_style_tag(content=SLOW_CHANGE + " :root { view-transition-name: root !important; }")
    menu_link(page, "/plans/").click()
    if waits(page, label, "() => window.__change.calls.length > 0 && window.__change.calls[0].ready !== null"):
        shown = page.evaluate("""() => { const style = (pseudo, property) => getComputedStyle(document.documentElement, pseudo)[property];
            return { old: style('::view-transition-old(root)', 'display'), fade: style('::view-transition-new(root)', 'animationName'),
                     captured: document.getAnimations().some((animation) => (animation.effect.pseudoElement || '').includes('(root)')) }; }""")
        if not shown["captured"]:
            problems.append(f"{label}: the root was not captured, so nothing was checked")
        elif shown["old"] != "none" or shown["fade"] != "none":
            problems.append(f"{label}: with the root captured, the page's old picture should not show (display {shown['old']!r}) and its new one should not "
                            f"fade in (animation {shown['fade']!r}); the cloth and the corners would be replaced by pictures of themselves")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    # A real click on the menu and on another chapter's link while the first cross-fades: the page underneath is still there for
    # the pointer. Chromium hands the pointer to the transition's own pseudo-elements instead (the document element is the
    # target of anything it hits) unless the page says otherwise, and then the click finds no link and is dropped. The change
    # has shut the menu (spec 4.4), so the pointer's way to Begin is MENU and then the link, both inside the cross-fade. The
    # cross-fade is slowed to 2 s so that the clicks are certain to land in it; the pointer's own clicks, not the page's.
    label = f"{site_page.path} to Plans, then a click of the pointer on MENU and on Begin while Plans cross-fades"
    context, page, errors = opened()
    page.add_style_tag(content=SLOW_CHANGE)
    menu_link(page, "/plans/").click()
    if waits(page, label, "() => window.__change.calls.length > 0 && window.__change.calls[0].ready !== null"):
        toggle = page.evaluate(BOX_RECT, "header [data-menu-toggle]")
        page.mouse.click(toggle["x"] + toggle["width"] / 2, toggle["y"] + toggle["height"] / 2)
        begin = page.evaluate(BOX_RECT, '#shell-menu a[href="/begin/"]')
        # the column the swap shut is still displayed for its 0.7 s fade, so its links have boxes whether or not the click reached MENU: MENU must say open
        menu_open = page.evaluate("() => document.querySelector('header [data-menu-toggle]').getAttribute('aria-expanded') === 'true'")
        running = page.evaluate("() => window.__change.calls[0].finished === null")
        if not running:
            problems.append(f"{label}: the cross-fade was over before the pointer reached the link, so nothing was checked")
        elif not begin or not menu_open:
            problems.append(f"{label}: a click of the pointer on MENU while the content cross-fades did not open the menu; the page underneath "
                            f"should stay hittable (the transition's pseudo-elements take no pointer events)")
        else:
            at = [begin["x"] + begin["width"] / 2, begin["y"] + begin["height"] / 2]
            reached = page.evaluate("([x, y]) => { const e = document.elementFromPoint(x, y); const a = e && e.closest('a'); return a ? a.getAttribute('href') : e && e.tagName; }", at)
            if reached != "/begin/":
                problems.append(f"{label}: while the content cross-fades the pointer reaches {reached!r} where the Begin link is; the page underneath "
                                f"should stay hittable (the transition's pseudo-elements take no pointer events)")
            page.mouse.click(*at)
            if waits(page, label, "() => location.pathname === '/begin/' && window.__change.calls.every((call) => call.finished !== null)"):
                if page.evaluate("() => document.querySelector('main h1').textContent.trim()") != "Begin":
                    problems.append(f"{label}: the second chapter did not win")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    # The old content has gone when its fade has ended, not when a timer started at the click says so: the fade starts at
    # the next frame, which a slow device is late with, and the new content then replaced one that was still half showing.
    label = f"{site_page.path} to Plans in place without view transitions, with the first frame of the fade late"
    context, page, errors = opened(scripts=(NO_VIEW_TRANSITIONS, LATE_FRAME))
    menu_link(page, "/plans/").click()
    if waits(page, label, "() => window.__change.swapped !== null"):
        shown = page.evaluate("() => window.__change.swappedOpacity")
        if shown is None or float(shown) > 0.05:
            problems.append(f"{label}: the old content was swapped for the new while <main> was still {shown} opaque; the swap should wait for the fade out to end")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    # A fade that never ends must not hold the change for ever: the timer bounds the wait, at twice --change-fade. The token is
    # made .6s first, so that a number typed into shell.js, which the token's own .7s would agree with (1,400 ms), is found out:
    # since the swap waits for the fade's own end, this timer is the one wait that has a number to follow the token.
    label = f"{site_page.path} to Plans in place without view transitions, with a fade that never reports its end and --change-fade at .6s"
    context, page, errors = opened(scripts=(NO_VIEW_TRANSITIONS, FADE_NEVER_ENDS))
    page.add_style_tag(content=":root { --change-fade: .6s; }")
    fade = token_ms(page, "--change-fade")
    if fade != 600:
        problems.append(f"{label}: --change-fade reads {fade} ms after it was made .6s, so nothing was checked")
    menu_link(page, "/plans/").click()
    if fade is not None and waits(page, label, "() => window.__change.swapped !== null"):
        marks = page.evaluate("() => window.__change")
        took = marks["swapped"] - marks["clicked"]
        if took < 2 * fade - 50:
            problems.append(f"{label}: the change was swapped in after {took:.0f} ms; the wait for the fade out should last twice --change-fade ({2 * fade:.0f} ms) when the fade does not end")
        elif took > 2 * fade + 800:
            problems.append(f"{label}: the change was swapped in after {took:.0f} ms; the timer should bound the wait at twice --change-fade ({2 * fade:.0f} ms)")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    label = f"{site_page.path} to Plans in place without view transitions"
    held = []
    context, page, errors = opened(scripts=("delete Document.prototype.startViewTransition;",),
                                   route=lambda route: held.append(route) if route.request.resource_type == "fetch" else route.continue_())
    fade = token_ms(page, "--change-fade")
    menu_link(page, "/plans/").click()
    page.wait_for_timeout(600)  # the fetch is slow: the fade out must not wait for it
    for route in held:
        route.continue_()
    if waits(page, label, "() => window.__change.ends.length >= 2"):
        marks = page.evaluate("() => window.__change")
        asked, arrived = marks["clicked"], marks["fetched"] - marks["clicked"]
        if marks["calls"]:
            problems.append(f"{label}: a view transition ran in a browser that has none")
        if marks["leaving"] is None or marks["leaving"] - asked > 100:
            began = "never" if marks["leaving"] is None else "{:.0f} ms after the click".format(marks["leaving"] - asked)
            problems.append(f"{label}: <main> should start to fade out within 100 ms of the click, while the page is still on its way "
                            f"(it began {began}; the page arrived after {arrived:.0f} ms)")
        if fade is not None:
            took = marks["ends"][-1] - asked
            if took > arrived + 2 * fade + 200:
                problems.append(f"{label}: the whole change took {took:.0f} ms; the page arrived after {arrived:.0f} ms, so with two fades of "
                                f"{fade:.0f} ms it should take under {arrived + 2 * fade + 200:.0f}")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    return problems


def history_scroll_problems(browser, base_url, session_key, site_page):
    """Back and Forward between chapters changed in place scroll the way a native Back does: the page we
    return to comes back where it was scrolled, a chapter reached by a click or by Forward starts at the
    top, and the page under a fade does not jump while it fades. Begin is the long chapter; 500px high
    makes it (and Plans) scroll."""
    problems = []
    # Both ways an in-place change can go (VIEW_MODES). A cross-fade scrolls the page inside the transition; a fade out
    # and in does it while the page is invisible, and the page under the fade must stay where it is until then.
    for mode, scripts in VIEW_MODES:
        context = new_context(browser, base_url, session_key, site_page, width=1280, height=500, theme=None, motion=None)
        for script in (*scripts, CHANGE_PROBE):
            context.add_init_script(script)
        page, errors = open_page(context, base_url + "/begin/")
        label = f"/begin/ to Plans in place, then back and forward, {mode}"
        page.evaluate("() => { window.stayedPut = true; window.scrollTo(0, 600); }")
        start = page.evaluate("() => scrollY")  # where Begin is left
        if start < 500:
            problems.append(f"{label}: Begin scrolls only {start}px at 1280x500; the check needs a page that scrolls")
        menu_link(page, "/plans/").click()
        if not settle_on(page, "/plans/", "Plans"):
            problems.append(f"{label}: never arrived at Plans")
        elif page.evaluate("() => scrollY") > 2:
            problems.append(f"{label}: Plans did not start at the top (scrolled {page.evaluate('() => scrollY')}px)")
        else:
            calls = page.evaluate("() => window.__change.calls.length")
            if scripts == () and not calls:  # a change from a scrolled page cross-fades like any other: crossfade_scroll_problems looks at how
                problems.append(f"{label}: a change from a scrolled page used no view transition; it should cross-fade")
            elif scripts and calls:
                problems.append(f"{label}: a view transition ran in a browser that has none")
            # Every frame from Back on: Plans, still showing while Begin is on its way, must stay where it is (the
            # change would move the scroll position, so a fade out in place and the swap, unseen, scrolls).
            page.evaluate("""() => { window.frames = []; const sample = () => { const h = document.querySelector('main h1');
                window.frames.push([h ? h.textContent.trim() : '', scrollY]); requestAnimationFrame(sample); }; sample(); }""")
            page.go_back()
            if not settle_on(page, "/begin/", "Begin"):
                problems.append(f"{label}: back never arrived at Begin")
            else:
                jumped = page.evaluate("() => window.frames.filter(([heading, y]) => heading === 'Plans' && Math.abs(y) > 2).map(([, y]) => y)")
                if jumped:
                    problems.append(f"{label}: the page under the fade jumps to {jumped[0]}px while Begin comes back")
                now = page.evaluate("() => scrollY")
                if abs(now - start) > 2:
                    problems.append(f"{label}: back returns to Begin scrolled {now}px, not the {start}px where it was left")
                if not page.evaluate("() => window.stayedPut"):
                    problems.append(f"{label}: back reloaded the whole page")
                page.go_forward()
                if not settle_on(page, "/plans/", "Plans"):
                    problems.append(f"{label}: forward never arrived at Plans")
                elif page.evaluate("() => scrollY") > 2:
                    problems.append(f"{label}: forward shows Plans scrolled {page.evaluate('() => scrollY')}px; it should start at the top")
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()
    context = new_context(browser, base_url, session_key, site_page, width=1280, height=500, theme=None, motion=None)
    context.add_init_script(CHANGE_PROBE)
    page, errors = open_page(context, base_url + "/begin/")
    # Back across a full load lands on the chapter we came from, showing, and scrolled where it was left.
    # Plans scrolls only 121px at 500px high, so 100px is near its end.
    label = "/begin/ to Plans in place, then the Docs and back"
    page.goto(base_url + "/begin/", wait_until="networkidle")
    page.evaluate("() => window.scrollTo(0, 600)")
    menu_link(page, "/plans/").click()
    if settle_on(page, "/plans/", "Plans"):
        page.evaluate("() => window.scrollTo(0, 100)")
        menu_link(page, "/docs/").click()
        try:
            page.wait_for_url("**/docs/", timeout=5000)
            page.go_back()
            page.wait_for_function("() => location.pathname === '/plans/'", timeout=5000)
            page.wait_for_timeout(900)
            state = page.evaluate(PAGE_STATE)
            if state["heading"] != "Plans" or state["opacity"] != "1" or state["leaving"]:
                problems.append(f"{label}: Plans did not come back showing ({state['heading']!r}, opacity {state['opacity']})")
            now = page.evaluate("() => scrollY")
            if abs(now - 100) > 2:
                problems.append(f"{label}: Plans comes back scrolled {now}px, not the 100px where it was left")
        except PlaywrightError:
            problems.append(f"{label}: never went to the Docs and back")
    else:
        problems.append(f"{label}: never arrived at Plans")
    # The same for a page the visitor never left in place (the browser's own restoration; guards a regression).
    label = "/plans/ loaded directly, then the Docs and back"
    page.goto(base_url + "/about/", wait_until="networkidle")  # a different URL, so Plans gets an entry of its own
    page.goto(base_url + "/plans/", wait_until="networkidle")
    page.evaluate("() => window.scrollTo(0, 100)")
    menu_link(page, "/docs/").click()
    try:
        page.wait_for_url("**/docs/", timeout=5000)
        page.go_back()
        page.wait_for_function("() => location.pathname === '/plans/'", timeout=5000)
        page.wait_for_timeout(900)
        now = page.evaluate("() => scrollY")
        if abs(now - 100) > 2:
            problems.append(f"{label}: Plans comes back scrolled {now}px, not the 100px where it was left")
    except PlaywrightError:
        problems.append(f"{label}: never went to the Docs and back")
    # A reload keeps the scroll too, on a page reached by an in-place Back (its entry is one we scroll by hand).
    label = "/begin/ reached by Back in place, then a reload"
    page.goto(base_url + "/begin/", wait_until="networkidle")
    menu_link(page, "/plans/").click()
    if settle_on(page, "/plans/", "Plans"):
        page.go_back()
        if settle_on(page, "/begin/", "Begin"):
            page.evaluate("() => window.scrollTo(0, 450)")
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(900)
            now = page.evaluate("() => scrollY")
            if abs(now - 450) > 2:
                problems.append(f"{label}: reloads at {now}px, not the 450px where it was left")
        else:
            problems.append(f"{label}: back never arrived at Begin")
    else:
        problems.append(f"{label}: never arrived at Plans")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    return problems


# Holds every animation of the view transition's pseudo-elements (the cross-fade) still at the start or, with "end", a
# millisecond before its end, and says what each one animates: the pictures should only fade, never move or change size.
HOLD_CROSSFADE = """(end) => { const own = ["offset", "easing", "composite", "computedOffset"];
    const animations = document.getAnimations().filter((animation) => animation.effect && (animation.effect.pseudoElement || "").startsWith("::view-transition"));
    animations.forEach((animation) => { animation.pause(); animation.currentTime = end ? animation.effect.getComputedTiming().duration - 1 : 0; });
    return animations.map((animation) => [animation.effect.pseudoElement,
      [...new Set(animation.effect.getKeyframes().flatMap((keyframe) => Object.keys(keyframe)))].filter((name) => !own.includes(name))]); }"""
# Lets what HOLD_CROSSFADE held run to its end.
RELEASE_CROSSFADE = """() => document.getAnimations().filter((animation) => animation.effect && (animation.effect.pseudoElement || "").startsWith("::view-transition"))
    .forEach((animation) => animation.finish())"""


def picture_difference(first, second, block=16):
    """The share of the screen, 0 to 1, where two screenshots of the same size differ clearly: both are averaged over
    squares of block pixels, which a chapter drawn a pixel apart (a snapshot beside the live page, with fractional
    sizes) or with other antialiasing still matches, and a square counts when its mean luma differs by more than 8 of 255.
    The same chapter drawn twice differs in none; one that moved a few hundred pixels differs in a fifth or more."""
    def squares(data):
        channels, rows = png_rows(data)
        means = []
        for top in range(0, len(rows) - block + 1, block):
            band = rows[top:top + block]
            means.append([sum(row[i + c] for row in band for i in range(left * channels, (left + block) * channels, channels) for c in range(3))
                          / (3 * block * block) for left in range(0, len(rows[0]) // channels - block + 1, block)])
        return means
    a, b = squares(first), squares(second)
    differing = sum(abs(x - y) > 8 for row_a, row_b in zip(a, b) for x, y in zip(row_a, row_b))
    return differing / sum(len(row) for row in a)


def crossfade_scroll_problems(browser, base_url, session_key, site_page):
    """A change from a page that is scrolled cross-fades like any other (spec 4.5), and the old chapter does not slide.
    The new chapter is shown from the top (or where Back left it), so the swap scrolls the page inside the transition; the
    old chapter's picture must stay where it was seen, not travel from there to the new scroll position. Each case
    slows the cross-fade to 2 s (SLOW_CHANGE), holds it at its start and compares what the screen shows with the
    page as it was a moment before: the same. Held at its end, the new chapter is where the settled page shows it.
    Tried on a wide screen (the column's link, the menu open) and on a phone (the same link pressed from script with the sheet shut: a change from the open sheet has
    no cross-fade, sheet_change_problems, and a link in the page or Back is what has one there); a change where the
    page was at the top when clicked and the visitor scrolled while the page was on its way; and Back to a scrolled
    page. Every animation of the pictures must be a fade."""
    problems = []
    for width, height, scrolled, column in ((1280, 500, 400, {"x": 300, "y": 0, "width": 680, "height": 500}),
                                            (375, 667, 500, {"x": 0, "y": 100, "width": 375, "height": 567})):
        held, holding = [], [False]  # the fetch of Plans, held back while holding[0]
        context = new_context(browser, base_url, session_key, site_page, width=width, height=height, theme=None, motion=None)
        context.add_init_script(CHANGE_PROBE)
        context.route("**/plans/", lambda route: held.append(route) if holding[0] and route.request.resource_type == "fetch" else route.continue_())
        page, errors = open_page(context, base_url + "/begin/")
        page.add_style_tag(content=SLOW_CHANGE)
        phone = width < 1024

        def to_plans():
            if not phone:  # the column's link; on a phone a change from the open sheet fades by its own driver with no view transition (sheet_change_problems), so the
                page.evaluate("() => document.querySelector('[data-menu-toggle]').click()")   # link is pressed with the sheet shut, as a link in the page or Back would
            page.evaluate("() => document.querySelector('#shell-menu a[href=\"/plans/\"]').click()")

        def started(count):
            return page.wait_for_function(f"() => window.__change.calls.length === {count} && window.__change.calls[{count - 1}].ready !== null", timeout=8000)

        def holds_still(label, before, count):
            """The held start of call number count shows what the page showed before; its animations only fade."""
            moved = page.evaluate(HOLD_CROSSFADE, False)
            page.evaluate(NEXT_FRAMES)
            page.wait_for_timeout(50)
            difference = picture_difference(before, page.screenshot(clip=column))
            if difference > 0.02:
                problems.append(f"{label}: the old chapter is not where it was a moment before the cross-fade began ({difference:.1%} of the screen "
                                f"differs from what it showed a moment before); it slid")
            for pseudo, names in moved:
                if any(name not in ("opacity", "mixBlendMode") for name in names):
                    problems.append(f"{label}: {pseudo} animates {names}; the pictures of a cross-fade should only fade")
            return moved

        # 1. A scrolled page, clicked while it shows.
        holding[0] = False
        bare_top = page.screenshot(clip=TOP_STRIP) if phone else None  # the strip between the mark and MENU, with nothing of the page in it
        if phone:  # a paragraph's first lines in the top band, where the new chapter's picture must have the fade too
            scrolled = page.evaluate(TEXT_AT_TOP)
        else:
            page.evaluate(f"() => window.scrollTo(0, {scrolled})")
        label = f"/begin/ scrolled {scrolled}px at {width}px to Plans in place"
        page.wait_for_timeout(300)
        before = page.screenshot(clip=column)
        to_plans()
        try:
            started(1)
        except PlaywrightError:
            problems.append(f"{label}: no view transition began; a change from a scrolled page should cross-fade like any other "
                            f"(calls: {page.evaluate('() => window.__change.calls.length')}, is-leaving: {page.evaluate('() => window.__change.leaving !== null')})")
            context.close()
            continue
        if not page.evaluate("() => window.__change.calls[0].ready !== null && scrollY < 2"):
            problems.append(f"{label}: Plans did not come in from the top")
        holds_still(label, before, 1)
        page.evaluate(HOLD_CROSSFADE, True)
        page.evaluate(NEXT_FRAMES)
        page.wait_for_timeout(50)
        end = page.screenshot(clip=column)
        page.evaluate(RELEASE_CROSSFADE)
        page.wait_for_function(CHANGE_OVER, timeout=8000)
        page.wait_for_timeout(100)
        settled = picture_difference(end, page.screenshot(clip=column))
        if settled > 0.02:
            problems.append(f"{label}: at the end of the cross-fade Plans is not where the settled page shows it ({settled:.1%} of the screen differs)")

        # 2. Back to the scrolled page: the old chapter is Plans, at the top; the new one comes in scrolled where it was left.
        label = f"/plans/ to /begin/ scrolled {scrolled}px, by Back, at {width}px"
        before = page.screenshot(clip=column)
        page.go_back()
        try:
            started(2)
            holds_still(label, before, 2)
            page.evaluate(HOLD_CROSSFADE, True)
            page.evaluate(NEXT_FRAMES)
            page.wait_for_timeout(50)
            end = page.screenshot(clip=column)
            end_top = page.screenshot(clip=TOP_STRIP) if phone else None
            page.evaluate(RELEASE_CROSSFADE)
            page.wait_for_function(CHANGE_OVER, timeout=8000)
            page.wait_for_timeout(100)
            if abs(page.evaluate("() => scrollY") - scrolled) > 2:
                problems.append(f"{label}: Begin came back scrolled {page.evaluate('() => scrollY')}px, not {scrolled}px")
            settled = picture_difference(end, page.screenshot(clip=column))
            if settled > 0.02:
                problems.append(f"{label}: at the end of the cross-fade Begin is not where the settled page shows it ({settled:.1%} of the screen differs)")
            if phone and picture_difference(end_top, bare_top) > 0.02:
                problems.append(f"{label}: at the end of the cross-fade the strip between the mark and MENU shows page text; the new chapter's picture "
                                f"was taken with the top fade where it was before the scroll")
        except PlaywrightError:
            problems.append(f"{label}: no view transition began for Back")

        # 3. Clicked at the top; the visitor scrolls while the page is on its way, so the page is not where it was at the click.
        label = f"/begin/ clicked at the top, scrolled {scrolled}px while Plans was on its way, at {width}px"
        page.goto(base_url + "/begin/", wait_until="networkidle")
        page.add_style_tag(content=SLOW_CHANGE)
        page.evaluate("() => { window.scrollTo(0, 0); }")
        before_calls = page.evaluate("() => window.__change.calls.length")
        holding[0] = True
        del held[:]
        to_plans()
        page.wait_for_timeout(300)
        page.evaluate(f"() => window.scrollTo(0, {scrolled})")
        page.wait_for_timeout(300)
        # The column menu is open until the change begins, and closes then: what is compared is the page under it.
        unseen = page.add_style_tag(content="#shell-menu { visibility: hidden !important; }")
        before = page.screenshot(clip=column)
        unseen.evaluate("(style) => style.remove()")
        holding[0] = False
        for route in held:
            route.continue_()
        try:
            started(before_calls + 1)
            holds_still(label, before, before_calls + 1)
        except PlaywrightError:
            problems.append(f"{label}: no view transition began")
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()
    return problems


def fragment_link_problems(browser, base_url, session_key, site_page):
    """A link to another chapter with a #fragment is an ordinary page load, so the browser scrolls to the
    fragment; an in-place change would ignore it and show the chapter from the top."""
    context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
    page, errors = open_page(context, base_url + site_page.path)
    label = f"{site_page.path} with a link to /begin/#main"
    page.evaluate("""() => { window.stayedPut = true; const link = document.createElement('a');
        link.id = 'check-fragment-link'; link.href = '/begin/#main'; link.textContent = 'Begin, at its main';
        document.querySelector('main p').append(' ', link); }""")
    page.locator("#check-fragment-link").click()
    problems = []
    try:
        page.wait_for_url("**/begin/#main", timeout=5000)
        page.wait_for_timeout(900)  # an in-place change would be fading in
        if page.evaluate("() => window.stayedPut"):
            problems.append(f"{label}: should be an ordinary page load, but the chapter changed in place and the fragment was ignored")
    except PlaywrightError:
        problems.append(f"{label}: never arrived at /begin/#main")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    return problems


# Records, with performance.now() inside the page, the two ends of a script's wait for the fade out, so the
# check measures the script's own wait and not how fast the machine is. Installed before the click. Come in
# (come-in.js): is-faded added to the first [data-threshold-line] to hidden set on it. An in-place change
# (shell.js): is-leaving added to <main> to <main>'s children replaced, which is the later of the fade out and
# the page's arrival (a fast local page, here). Each script waits fadeMs() for its fade. The marks are read back
# from window.fadeOutMarks as { start, end }.
FADE_OUT_MARKS = """(threshold) => {
  const marks = window.fadeOutMarks = {};
  const mark = (name) => { if (marks[name] === undefined) marks[name] = performance.now(); };
  new MutationObserver((records) => {
    for (const record of records) {
      const target = record.target;
      if (target.nodeType !== Node.ELEMENT_NODE) continue;
      if (threshold && record.type === 'attributes' && target.matches('[data-threshold-line]')) {
        if (record.attributeName === 'class' && target.classList.contains('is-faded')) mark('start');
        if (record.attributeName === 'hidden' && target.hidden && marks.start !== undefined) mark('end');
      }
      if (!threshold && target.matches('main')) {
        if (record.type === 'attributes' && record.attributeName === 'class' && target.classList.contains('is-leaving')) mark('start');
        if (record.type === 'childList' && marks.start !== undefined) mark('end');
      }
    }
  }).observe(document, { attributes: true, childList: true, subtree: true });
}"""


def fade_length_problems(browser, base_url, session_key, site_page):
    """The scripts wait out a fade by the length of a token in tokens.css: Come in (come-in.js) by --fade, which a number
    typed into the script would not follow, so with --fade made .2s the wait before Come in replaces the line is about
    200 ms, not the 700 ms of the .7s token. An in-place change that fades <main> out first (shell.js, where the browser
    has no view transitions) waits for the fade's own end, and the transition's length is --change-fade in the
    stylesheet, so this check sees the wait follow the token (with it .1s, about 100 ms, not the 700 ms of the .7s token),
    but it cannot see a number typed into shell.js's wait for the fade's end: the stylesheet decides that length, and
    nothing in the script is a length to follow. The one number there is the timer that bounds the wait, which
    chapter_transition_problems checks follows the token (twice --change-fade, with it made .6s, when a fade never ends).
    What is timed is the scripts' wait, inside the page (FADE_OUT_MARKS, CHANGE_PROBE), so that a loaded machine, which
    slows everything, does not fail it. For a change in place that is from when the fade out began (its first frame) or when
    the page arrived, whichever is later: neither a late first frame nor a slow fetch is the script's wait."""
    problems = []
    rewritten = []
    token, shortened, limit = ("--fade", ".2s", 450) if site_page.threshold else ("--change-fade", ".1s", 250)

    def quicker(route):
        response = route.fetch()
        body, count = re.subn(rf"{token}:\s*[^;]+;", f"{token}: {shortened};", response.text(), count=1)
        rewritten.append(count)
        route.fulfill(response=response, body=body)

    context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
    context.route(TOKENS_STYLESHEET, quicker)
    if not site_page.threshold:
        context.add_init_script(NO_VIEW_TRANSITIONS)  # a view transition waits for nothing; the fade-only path is the one that waits
        context.add_init_script(CHANGE_PROBE)
    page, errors = open_page(context, base_url + site_page.path)
    if rewritten != [1]:
        problems.append(f"{site_page.path}: tokens.css has no {token} to shorten for this check")
    if site_page.threshold:
        label, arrived = f"{site_page.path} with {token} at {shortened}, Come in", (
            "() => { const line = document.querySelector('[data-install]');"
            " return !line.hidden && getComputedStyle(line).opacity === '1'; }")
        page.evaluate(FADE_OUT_MARKS, True)
        page.locator("[data-come-in]").click()
    else:
        label, arrived = f"{site_page.path} with {token} at {shortened}, to Plans in place", (
            "() => location.pathname === '/plans/' && getComputedStyle(document.querySelector('main')).opacity === '1'")
        menu_link(page, "/plans/").click()
    try:
        page.wait_for_function(arrived, polling="raf", timeout=3000)
        if site_page.threshold:
            marks = page.evaluate("() => window.fadeOutMarks")
            started, ended = marks.get("start"), marks.get("end")
        else:  # from when the fade out began, a frame after the class, or when the page arrived if that was later: neither the machine's lateness nor the fetch counts
            marks = page.evaluate("() => window.__change")
            started = None if marks["fadeStart"] is None or marks["fetched"] is None else max(marks["fadeStart"], marks["fetched"])
            ended = marks["swapped"]
        if started is None or ended is None:
            problems.append(f"{label}: the wait for the fade out was not seen (marks: {marks})")
        else:
            waited = ended - started
            if waited > limit:  # the shortened token gives about its own length; a script still waiting the old length, or more, gives more
                problems.append(f"{label}: waited {waited:.0f} ms for the fade out; it should follow the shortened token ({shortened})")
    except PlaywrightError:
        problems.append(f"{label}: never finished")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    if not site_page.threshold:  # a view transition's cross-fade lasts as long as --change-fade says: made 1 s, it lasts about a second
        stretched = []

        def slower(route):
            response = route.fetch()
            body, count = re.subn(r"--change-fade:\s*[^;]+;", "--change-fade: 1s;", response.text(), count=1)
            stretched.append(count)
            route.fulfill(response=response, body=body)

        context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
        context.route(TOKENS_STYLESHEET, slower)
        context.add_init_script(CHANGE_PROBE)
        page, errors = open_page(context, base_url + site_page.path)
        label = f"{site_page.path} with --change-fade at 1s, to Plans in place"
        if stretched != [1]:
            problems.append(f"{site_page.path}: tokens.css has no --change-fade to lengthen for this check")
        menu_link(page, "/plans/").click()
        try:
            page.wait_for_function("() => window.__change.calls.length > 0 && window.__change.calls.every((call) => call.finished !== null)", timeout=8000)
            lasted = page.evaluate("() => window.__change.calls[0].fadeMs")
            if lasted is None or abs(lasted - 1000) > 1:
                problems.append(f"{label}: the cross-fade lasts {lasted} ms; it should follow the token (1000 ms)")
        except PlaywrightError:
            problems.append(f"{label}: no view transition finished")
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()
    return problems


READING_FONT_FILES = re.compile(r".*/fonts/newsreader/.*")   # the reading font (Newsreader), which the threshold does not preload


def font_wait_problems(browser, base_url, session_key, site_page):
    """The threshold does not preload the reading font (Newsreader), so on a slow connection it can still be
    loading when a chapter reached from the threshold is swapped in. The chapter must not start to show before
    the font is there, or fallback text flashes: the change waits for the faces of its layout before it swaps the content in
    or starts a view transition (shell.js asks for them when the menu opens and when the change begins), and where there are no
    view transitions <main> does not start to fade in before them. The font file is held back until the change is waiting for it (release_when_waited_for:
    never for a fixed time after the click, which a slow runner's late wait ends before it begins), a late font, inside the wait's bound (400 ms from the wait's
    beginning); a font that never arrives does not hold the
    change for ever: held until the change is over, it still happens within about the bound."""
    problems = []
    for mode, scripts in VIEW_MODES:
        context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
        for script in (*scripts, CHANGE_PROBE, FONT_TIMER_PROBE):
            context.add_init_script(script)
        page, errors = open_page(context, base_url + site_page.path)
        label = f"{site_page.path} to Begin in place with the reading font late, {mode}"
        samples = []
        asked, release = hold_font(page, READING_FONT_FILES)
        page.expose_function("sample", lambda opacity, loaded: samples.append((float(opacity), bool(loaded))))
        page.evaluate("""() => setInterval(() => window.sample(getComputedStyle(document.querySelector('main')).opacity,
            document.fonts.check('1em Newsreader')), 16)""")
        page.locator("header [data-menu-toggle]").click()
        menu_link(page, "/begin/").click()
        try:
            release_when_waited_for(page, release, "((document.querySelector('main h1') || {}).textContent || '').trim() === 'Begin'")   # the change waits; the font, held back, has not come
            if not asked:
                problems.append(f"{label}: the reading font was never asked for, so there is nothing to wait for")
            # Polled on a timer, not on frames: a view transition pauses the page's drawing while its update callback waits.
            page.wait_for_function("() => document.querySelector('main h1').textContent.trim() === 'Begin'", polling=10, timeout=5000)
            page.wait_for_timeout(1500)
            calls = page.evaluate("() => window.__change.calls")
            gave_up = gave_up_on_face(page)
            if scripts == () and not calls:
                problems.append(f"{label}: no view transition ran")
            elif calls:
                if calls[0]["ready"] is None:
                    problems.append(f"{label}: the view transition never began")
                elif calls[0]["fontsLoading"]:
                    face_not_loaded(problems, gave_up, label, "the chapter starts to cross-fade in before the reading font has loaded, so fallback text shows", "Newsreader")
            else:
                faded = next((i for i, (opacity, _) in enumerate(samples) if opacity < 0.05), None)
                rising = next((i for i, (opacity, _) in enumerate(samples) if faded is not None and i > faded and opacity > 0.05), None)
                if rising is None:
                    problems.append(f"{label}: the page never faded out and back in")
                elif not samples[rising][1]:
                    face_not_loaded(problems, gave_up, label, "the chapter starts to fade in before the reading font has loaded, so fallback text shows", "Newsreader")
        except PlaywrightError:
            problems.append(f"{label}: never arrived at Begin")
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()

    # A font that never arrives: held until the change is over. The wait for it is bounded, so the chapter still comes (the bound is FONT_BOUND_MS
    # from the page's arrival, and the change's own fade and fetch are well inside the seconds given here).
    for mode, scripts in VIEW_MODES:
        context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
        for script in scripts:
            context.add_init_script(script)
        page, errors = open_page(context, base_url + site_page.path)
        label = f"{site_page.path} to Begin in place with the reading font never arriving, {mode}"
        held = []
        page.route(READING_FONT_FILES, lambda route: held.append(route))
        page.locator("header [data-menu-toggle]").click()
        menu_link(page, "/begin/").click()
        try:
            page.wait_for_function("() => document.querySelector('main h1').textContent.trim() === 'Begin'", polling=10, timeout=FONT_BOUND_MS + 2500)
        except PlaywrightError:
            problems.append(f"{label}: the chapter did not come within {FONT_BOUND_MS + 2500} ms; the wait for a font is bounded at {FONT_BOUND_MS}")
        for route in held:
            route.continue_()
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()

    # The font is asked for while the page is on its way, not once the content that needs it has been swapped in: a view
    # transition pauses the page's drawing while it waits for a font, so every millisecond the font spends in flight before
    # the swap is one the page keeps moving. From the threshold it is the menu's opening, the only way to a chapter, that
    # starts it. The page is held back here, so the font's request is certainly the earlier one.
    label = f"{site_page.path} to Begin in place, the reading font before the page"
    context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
    page, errors = open_page(context, base_url + site_page.path)
    fonts, pages = [], []
    page.route(READING_FONT_FILES, lambda route: fonts.append(route))
    page.route("**/begin/", lambda route: pages.append(route) if route.request.resource_type == "fetch" else route.continue_())
    page.locator("header [data-menu-toggle]").click()
    menu_link(page, "/begin/").click()
    wait_until(page, lambda: pages and fonts)   # both are asked for as the click is made; held, so nothing else is going on
    if not pages:
        problems.append(f"{label}: Begin was never fetched, so nothing was checked")
    elif not fonts:
        problems.append(f"{label}: the reading font was not asked for while Begin was on its way; it should be asked for by then")
    for route in (*fonts, *pages):
        route.continue_()
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    return problems


CHAPTER_CENTRE = """() => { const main = document.querySelector('.shell-main'), chapter = document.querySelector('.chapter');
    const style = getComputedStyle(main), box = main.getBoundingClientRect();
    const first = chapter.firstElementChild.getBoundingClientRect(), last = chapter.lastElementChild.getBoundingClientRect();
    return { scrolls: document.documentElement.scrollHeight > innerHeight,
             off: (first.top + last.bottom) / 2 - (box.top + parseFloat(style.paddingTop) + box.bottom - parseFloat(style.paddingBottom)) / 2 }; }"""
TYPE = """(element) => { const style = getComputedStyle(element);
    return [style.fontFamily, style.fontStyle, style.fontWeight, style.fontSize, style.lineHeight, style.fontVariantNumeric]; }"""


def chapter_style_problems(browser, base_url, session_key, site_page):
    """Small rules of the chapters' stylesheets, read as computed values: a short chapter is centred by its
    words, not by the margin under its last line; a labelled link in prose takes no prose-link underline or
    fill; and a plan's name is set like a chapter's heading (one definition, tokens.css)."""
    problems = []
    context = new_context(browser, base_url, session_key, site_page, width=1280, height=800, theme=None, motion=None)
    page, errors = open_page(context, base_url + site_page.path)
    label = f"{site_page.path} at 1280x800"
    if site_page.chapter:
        centre = page.evaluate(CHAPTER_CENTRE)  # from the first line's top to the last line's bottom
        if not centre["scrolls"] and abs(centre["off"]) > 2:
            problems.append(f"{label}: the chapter's words sit {centre['off']:.0f}px from the centre of the space between the corners")
        page.evaluate("""() => { const p = document.createElement('p'); p.id = 'check-links';
            p.innerHTML = '<a id="check-label" class="label" href="#check-links">A label</a> '
                + '<a id="check-plain" href="#check-links">a link in prose</a>';
            document.querySelector('.chapter').append(p); }""")
        decoration = "(a) => getComputedStyle(a).textDecorationLine"
        fill = "(a) => getComputedStyle(a).backgroundColor"
        page.locator("#check-label").hover()
        page.wait_for_timeout(300)  # the fill fades in
        if page.locator("#check-label").evaluate(decoration) != "none" or page.locator("#check-label").evaluate(fill) != "rgba(0, 0, 0, 0)":
            problems.append(f"{label}: a .label link in prose takes the prose-link underline or fill; only a plain link should")
        page.locator("#check-plain").hover()
        page.wait_for_timeout(300)
        if page.locator("#check-plain").evaluate(decoration) != "underline" or page.locator("#check-plain").evaluate(fill) == "rgba(0, 0, 0, 0)":
            problems.append(f"{label}: a plain link in prose should keep its dotted underline and take the fill on hover")
    if page.locator(".plan__name").count():
        page.evaluate("""() => { const name = document.querySelector('.plan__name'), heading = document.createElement('h2');
            heading.id = 'check-heading'; heading.className = 'chapter__heading'; heading.textContent = 'A heading';
            name.closest('.plan').parentElement.append(heading); }""")
        name, heading = (page.locator(selector).first.evaluate(TYPE) for selector in (".plan__name", "#check-heading"))
        if name != heading:
            problems.append(f"{label}: a plan's name is set {name}, a chapter heading {heading}; they should be the same")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    return problems


def phone_menu_problems(page, label):
    """On a phone the open menu is a full sheet: what lies under it is inert, so Tab stays in the menu. The sheet's surface is
    the page's ground (paper and its grain) and fades out across its left half, so the scene shows there; behind the links, the
    right half, it is solid: no page content shows strongly through (content_shows_under: a pixel off by over 40 levels), the display
    controls at the page's end included. That the surface is solid to within 1% is cloth_menu_sheet_problems'."""
    problems = []
    covered = "() => document.querySelector('main').inert && document.querySelector('.shell-corner--top-left').inert"
    if page.locator("#shell-menu").is_visible():
        problems.append(f"{label}: the menu starts open on a phone")
    page.locator("header [data-menu-toggle]").click()
    if not page.evaluate(covered):
        problems.append(f"{label}: the page under the open menu sheet is not inert")
    problems += [f"{label}: {name!r} in the open menu sheet is covered by something painted over it"
                 for name in page.evaluate(COVERED, "#shell-menu a")]
    page.wait_for_function(SHEET_IN_PLACE, timeout=5000)  # the sheet has finished fading in (0.7 s)
    view = page.viewport_size
    half = {"x": view["width"] / 2, "y": 0, "width": view["width"] / 2, "height": view["height"]}
    if content_shows_under(page, half, margin=0):
        problems.append(f"{label}: page content shows strongly through the right half of the open menu sheet, behind the links, which should be solid")
    # On narrow screens the bottom corners wait at the end of the page: look there, sheet still open.
    page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
    under_sheet = page.evaluate(COVERED, "footer [data-appearance-toggle], footer [data-motion-toggle]")
    if len(under_sheet) != 2:  # the sheet is over both bottom corners: the appearance switch bottom left, the motion switch bottom right
        problems.append(f"{label}: a bottom corner can be reached through the open menu sheet (the sheet covers {under_sheet} of the appearance switch and the motion switch)")
    # The right one is behind the solid half and not to be seen; the left one is under the fading half, where the scene shows.
    box = page.locator(".shell-corner--bottom-right button").bounding_box()
    if box and content_shows_under(page, box, margin=2, hide="footer"):
        problems.append(f"{label}: the motion switch bottom right shows through the open menu sheet, whose right half should be solid")
    page.evaluate("() => window.scrollTo(0, 0)")
    problems += menu_sideways_problems(page, f"{label} with the menu sheet open")
    # axe cannot see the sheet's surface (a pseudo-element), so it takes for the links' ground whatever lies under it: the page's own
    # content, which it may find a different colour. The solid half is checked above (nothing shows through it), so <main> is
    # hidden for axe, which then reads the page's ground, the colour the surface is.
    unseen = page.add_style_tag(content="main { visibility: hidden !important; }")
    problems += axe_problems(page, f"{label} with the menu sheet open")
    unseen.evaluate("(style) => style.remove()")
    page.keyboard.press("Escape")
    if page.evaluate("() => document.querySelector('main').inert"):
        problems.append(f"{label}: the page stays inert after the menu closes")
    return problems


# The phone menu sheet as the page shows it now: whether and how it is drawn, what CSS runs on it (nothing should: its fade is shell.js' one value,
# --sheet, stepped per drawn frame, and a CSS transition or animation is a function of time that a late frame can find over, so any on the sheet or its
# groups is a problem), the first group of links' move (the groups all make the same one, and the sheet has no transform of its own), and the
# animations on its items (none, at any width: the menu fades as one piece, the column from 64rem up as the sheet does).
SHEET_STATE = """() => { const sheet = document.querySelector('#shell-menu'), style = getComputedStyle(sheet), group = sheet.querySelector('.menu__group');
  const named = (animation) => animation.constructor.name === 'CSSTransition' ? [animation.transitionProperty]
    : Object.keys(animation.effect.getKeyframes()[0]).filter((key) => !['offset', 'computedOffset', 'easing', 'composite'].includes(key));
  return { display: style.display, opacity: style.opacity, transform: style.transform, pointer: style.pointerEvents, cursor: style.cursor,
           sheetValue: style.getPropertyValue('--sheet').trim(), lift: group ? new DOMMatrix(getComputedStyle(group).transform).m42 : null,
           animations: [...sheet.getAnimations(), ...(group ? group.getAnimations() : [])].flatMap((animation) => named(animation).map((property) => ({
             kind: animation.constructor.name, property }))),
           items: [...sheet.querySelectorAll('li')].reduce((count, li) => count + li.getAnimations().length, 0) }; }"""


# MENU pressed, or Escape, and the sheet's state in the same task, so that no frame has been drawn between them: the driver's first step has no time behind it.
MENU_THEN_STATE = f"() => {{ document.querySelector('header [data-menu-toggle]').click(); return ({SHEET_STATE})(); }}"
ESCAPE_THEN_STATE = f"() => {{ document.body.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Escape', bubbles: true }})); return ({SHEET_STATE})(); }}"


def sheet_timed_by_css_problems(state, label, what):
    """Nothing runs on the sheet or its groups in CSS while it `what` (opens, closes, rests): its fade is the driver's, one value per drawn frame
    (spec 4.4), and a CSS transition or animation on it is a function of time that a late frame can find over between two frames drawn, the blink
    CI's WebKit showed. The panel fades as one: nothing on its items either."""
    problems = []
    if state["animations"]:
        problems.append(f"{label}: while the sheet {what} CSS runs {sorted((animation['kind'], animation['property']) for animation in state['animations'])} on it; "
                        f"its fade is --sheet, stepped by shell.js once per drawn frame, and nothing else (a transition or an animation is timed by a clock and can "
                        f"be over between two late frames)")
    if state["items"]:
        problems.append(f"{label}: {state['items']} animations run on the sheet's items; the panel fades as one, its items do not fade in one by one")
    return problems


def phone_sheet_problems(browser, base_url, session_key):
    """The phone's menu sheet opens and closes at Come in's pace (spec 4.4): it fades in and settles over 700 ms (ease: opacity SHEET_HELD_OPACITY
    to 1, the links 6px down into their place) and out over 700 ms (ease, the links 6px up), as ONE value, --sheet,
    that shell.js steps once per drawn frame and never by more than a quarter, with CSS reading it and timing nothing: no transition, no animation
    runs on the sheet or its groups (sheet_timed_by_css_problems). The sheet stays displayed while it fades out and takes no pointer then.
    The fade is also read frame by frame, against the driver's own model (sheet_fade_problems), where a sheet that blinks in shows no value between 0
    and 1, and with a frame of the opening held for 400 ms and with every frame held for 120 ms (sheet_slow_first_frame_problems,
    sheet_slow_frames_problems), which are the owner's blink and do not depend on the runner's speed.
    With Motion off, or a system asking for reduced motion with nothing saved, both are instant and nothing runs. The items do not
    fade in one by one. From 64rem up the column fades by the same value, in and out, and does not move. The page behind does not scroll
    while the sheet is open."""
    problems = []
    begin = page_named("/begin/")
    for name, options in (("Motion on", dict(theme="light", motion="on")), ("Motion off", dict(theme="light", motion="off")),
                          ("reduced motion", dict(theme="light", motion=None, reduced_motion="reduce"))):
        label = f"/begin/ at 375px, {name}, the menu sheet"
        context = new_context(browser, base_url, session_key, begin, width=375, height=667, **options)
        page, errors = open_page(context, base_url + begin.path)
        opening = page.evaluate(MENU_THEN_STATE)   # read in the click's own task, before a frame can be drawn
        if opening["cursor"] != "pointer":
            problems.append(f"{label}: the open sheet's cursor is {opening['cursor']}; it should be pointer, which is how iOS Safari takes a tap on its empty surface for a click")
        problems += sheet_timed_by_css_problems(opening, label, "opens")
        if opening["transform"] != "none":
            problems.append(f"{label}: the sheet itself has a transform ({opening['transform']}) as it opens; the move belongs to the groups of links inside it, "
                            f"since a transform on the sheet makes it the containing block of its fixed surface (sheet_scrolled_close_problems)")
        if name == "Motion on":
            if opening["display"] == "none" or float(opening["opacity"]) != float(SHEET_HELD_OPACITY) or opening["sheetValue"] != "0" or abs(opening["lift"] + 6) > 0.01:
                problems.append(f"{label}: right after the click the sheet is display {opening['display']}, opacity {opening['opacity']}, --sheet {opening['sheetValue']!r}, its links "
                                f"{opening['lift']}px from their place; it should be displayed at opacity {SHEET_HELD_OPACITY} with --sheet 0 and the links 6px up (the first frame "
                                f"has no time behind it, so WebKit paints the sheet before the fade begins)")
            page.wait_for_function(SHEET_IN_PLACE, timeout=5000)
            settled = page.evaluate(SHEET_STATE)
            if (settled["display"], settled["opacity"], settled["transform"], settled["lift"]) != ("flex", "1", "none", 0):
                problems.append(f"{label}: the open sheet settles as {settled['display']}, opacity {settled['opacity']}, transform {settled['transform']}, its links {settled['lift']}px "
                                f"from their place; it should be shown, fully opaque and in place")
            problems += sheet_timed_by_css_problems(settled, label, "rests open")
            closing = page.evaluate(ESCAPE_THEN_STATE)
            problems += sheet_timed_by_css_problems(closing, label, "closes")
            if closing["transform"] != "none":
                problems.append(f"{label}: the sheet itself has a transform ({closing['transform']}) as it closes; the move belongs to the groups of links inside it, "
                                f"since a transform on the sheet makes it the containing block of its fixed surface (sheet_scrolled_close_problems)")
            if closing["display"] == "none" or closing["pointer"] != "none":
                problems.append(f"{label}: as it closes the sheet is display {closing['display']}, pointer-events {closing['pointer']}; it should stay displayed "
                                f"while it fades and take no pointer")
            page.wait_for_function("() => getComputedStyle(document.querySelector('#shell-menu')).display === 'none'", timeout=5000)
            gone = page.evaluate(SHEET_STATE)
            if gone["sheetValue"] != "0":
                problems.append(f"{label}: once the sheet is gone --sheet is {gone['sheetValue']!r}; it should be 0")
        else:
            if opening["display"] == "none" or (opening["opacity"], opening["transform"], opening["lift"]) != ("1", "none", 0) or opening["sheetValue"] != "1":
                problems.append(f"{label}: the sheet should open at once and in place; it is display {opening['display']}, opacity {opening['opacity']}, "
                                f"transform {opening['transform']}, its links {opening['lift']}px from their place, --sheet {opening['sheetValue']!r}")
            page.keyboard.press("Escape")
            closing = page.evaluate(SHEET_STATE)
            if closing["display"] != "none" or closing["sheetValue"] != "0":
                problems.append(f"{label}: the sheet should close at once; it is display {closing['display']} with --sheet {closing['sheetValue']!r}")
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()

    # The fade as the frames show it, and a close or an opening that interrupts the other.
    context = new_context(browser, base_url, session_key, begin, width=375, height=667, theme="light", motion="on")
    page, errors = open_page(context, base_url + begin.path)
    problems += sheet_fade_problems(page, "/begin/ at 375px, Motion on, the menu sheet in Chromium")
    problems += [f"/begin/ at 375px, the menu sheet's fade: console error: {error}" for error in errors]
    for when, _ in SLOW_FIRST_FRAMES:
        slow, errors = open_page(context, base_url + begin.path)   # a page of its own: the probe's observer stays on the sheet it ran on
        problems += sheet_slow_first_frame_problems(slow, "/begin/ at 375px, Motion on, the menu sheet in Chromium", when)
        problems += [f"/begin/ at 375px, the menu sheet's slow first frame: console error: {error}" for error in errors]
    for mode in ("open", "close"):
        slow, errors = open_page(context, base_url + begin.path)
        problems += sheet_slow_frames_problems(slow, "/begin/ at 375px, Motion on, the menu sheet in Chromium", mode)
        problems += [f"/begin/ at 375px, the menu sheet with every frame slow: console error: {error}" for error in errors]
    context.close()

    # From 64rem up the open menu is a column: it fades in and out as one piece by the same driver as the sheet, over Come in's 0.7 s (the owner's
    # ask, 2026-10-31: it used to close at once), and does not move; Motion off and reduced motion still open and close it at once.
    for name, options in (("Motion on", dict(theme="light", motion="on")), ("Motion off", dict(theme="light", motion="off")),
                          ("reduced motion", dict(theme="light", motion=None, reduced_motion="reduce"))):
        label = f"/ at 1280px, {name}, the menu column"
        context = new_context(browser, base_url, session_key, page_named("/"), width=1280, **options)
        page, errors = open_page(context, base_url + "/")
        opening = page.evaluate(MENU_THEN_STATE)   # read in the click's own task, before a frame can be drawn
        problems += sheet_timed_by_css_problems(opening, label, "opens")
        if opening["cursor"] == "pointer":
            problems.append(f"{label}: the column has the pointer cursor; only the phone's sheet has it, since a tap on its surface closes it")
        if opening["transform"] != "none" or opening["lift"] != 0:
            problems.append(f"{label}: the column moves as it opens (transform {opening['transform']}, its links {opening['lift']}px from their place); it only fades, as Come in does")
        if name == "Motion on":
            if opening["display"] == "none" or float(opening["opacity"]) != float(SHEET_HELD_OPACITY) or opening["sheetValue"] != "0":
                problems.append(f"{label}: right after the click the column is display {opening['display']}, opacity {opening['opacity']}, --sheet {opening['sheetValue']!r}; "
                                f"it should be displayed at opacity {SHEET_HELD_OPACITY} with --sheet 0, the first frame of its fade")
            page.wait_for_function(SHEET_IN_PLACE, timeout=5000)
            settled = page.evaluate(SHEET_STATE)
            if (settled["display"], settled["opacity"]) != ("flex", "1"):
                problems.append(f"{label}: the open column settles as {settled['display']}, opacity {settled['opacity']}; it should be shown and fully opaque")
            problems += sheet_timed_by_css_problems(settled, label, "rests open")
            closing = page.evaluate(ESCAPE_THEN_STATE)
            problems += sheet_timed_by_css_problems(closing, label, "closes")
            if closing["display"] == "none" or closing["pointer"] != "none":
                problems.append(f"{label}: as it closes the column is display {closing['display']}, pointer-events {closing['pointer']}; it should stay displayed "
                                f"while it fades and take no pointer")
            if not menu_gone(page):
                problems.append(f"{label}: the column is still displayed 5 s after Escape; it fades out over 0.7 s")
            elif page.evaluate(SHEET_STATE)["sheetValue"] != "0":
                problems.append(f"{label}: once the column is gone --sheet is {page.evaluate(SHEET_STATE)['sheetValue']!r}; it should be 0")
        else:
            if opening["display"] == "none" or opening["opacity"] != "1" or opening["sheetValue"] != "1":
                problems.append(f"{label}: the column should open at once; it is display {opening['display']}, opacity {opening['opacity']}, --sheet {opening['sheetValue']!r}")
            closing = page.evaluate(ESCAPE_THEN_STATE)
            if closing["display"] != "none" or closing["sheetValue"] != "0":
                problems.append(f"{label}: the column should close at once; it is display {closing['display']} with --sheet {closing['sheetValue']!r}")
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()

    # The page does not scroll while the sheet is open (wheel and keys), and does again once it is shut.
    label = "/begin/ at 375px, the menu sheet over a scrolled page"
    context = new_context(browser, base_url, session_key, begin, width=375, height=667, theme="light", motion="off")
    page, errors = open_page(context, base_url + begin.path)
    page.evaluate("() => window.scrollTo(0, 300)")
    page.evaluate(NEXT_FRAMES)
    page.locator("header [data-menu-toggle]").click()
    page.mouse.move(100, 400)
    page.mouse.wheel(0, 400)
    page.keyboard.press("PageDown")
    page.wait_for_timeout(400)
    after_open = page.evaluate("() => scrollY")
    if after_open != 300:
        problems.append(f"{label}: the page behind scrolled from 300 to {after_open:.0f}px under the wheel and PageDown; it should stay where it was while the sheet is open")
    page.keyboard.press("Escape")
    page.mouse.wheel(0, 400)
    page.wait_for_timeout(400)
    if page.evaluate("() => scrollY") <= 300:
        problems.append(f"{label}: the page does not scroll again once the sheet is shut (still at {page.evaluate('() => scrollY'):.0f}px), so nothing was checked")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    # A tap on the sheet's visible left half closes it, as a tap on the backdrop of earendil.com's phone menu does, and a tap on a link
    # is not one. The sheet is inside the nav, so the page-wide click handler never saw its surface as outside the menu: shell.js
    # closes the menu when the tap's target is the sheet itself (its surface: the left half, the padding and the gaps between the groups of links), never a link. A
    # touchscreen's tap. The links' own navigation is cancelled, so that a tap on one cannot close the sheet by starting a change
    # in place (which closes it too): what is looked at is whether the tap alone closes it.
    label = "/begin/ at 375px, a tap on the menu sheet"
    context = new_context(browser, base_url, session_key, begin, width=375, height=667, theme="light", motion="off", touch=True)
    page, errors = open_page(context, base_url + begin.path)
    page.evaluate("() => document.addEventListener('click', (event) => { if (event.target.closest('#shell-menu a')) event.preventDefault(); }, true)")
    toggle = page.locator("header [data-menu-toggle]")
    shut = lambda: toggle.get_attribute("aria-expanded") != "true" and not page.locator("#shell-menu").is_visible()
    toggle.tap()
    link = page.evaluate(BOX_RECT, '#shell-menu a[href="/plans/"]')
    middle = page.viewport_size["height"] / 2
    if shut() or not link:
        problems.append(f"{label}: the sheet did not open, or the Plans link has no box, so nothing was checked")
    else:
        if page.evaluate("([x, y]) => document.elementFromPoint(x, y).id", [40, middle]) != "shell-menu":
            problems.append(f"{label}: the point 40px in, mid-height, is not on the sheet, so a tap there says nothing about it")
        page.touchscreen.tap(link["x"] + link["width"] / 2, link["y"] + link["height"] / 2)
        if shut():
            problems.append(f"{label}: a tap on a link closes the sheet by itself; only a tap on the sheet's own surface should")
        page.touchscreen.tap(40, middle)
        if not shut():
            problems.append(f"{label}: a tap on the left half of the sheet, 40px in at mid-height, does not close it; it should, as a tap on the backdrop of earendil.com's does")
        elif page.evaluate("() => document.querySelector('main').inert"):
            problems.append(f"{label}: the page stays inert after a tap on the sheet closed it")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    return problems


# The sheet's opacity frame by frame while MENU is clicked from inside the page, so that the clicks fall exactly where the check says. mode:
# "open" (from shut), "close" (from open), "open-then-close" and "close-then-open": the second click comes at the first frame at which the sheet
# is seen part way through its fade (opacity between .25 and .75; `before` and `after` are read just before and just after it), not at a fixed
# delay, which a slow runner can miss (its fade may not have begun, or may be over). With no such frame there is no second click and no `before`.
# Every sample is the sheet's state at a frame (below). The probe runs until the sheet is at rest, not for a fixed time: an opening takes
# its first frame and .7 s of frames, which on a runner whose frames are 100 ms or more apart is more than any fixed time one would pick. At rest
# is the menu shut and the sheet not displayed, or open and fully opaque, at two frames running; `bound` (ms) ends the probe anyway, and
# the result says so (`bounded`). stall > 0 holds one frame of the opening that many ms, which is how slow WebKit's frame can be on a phone (CI's
# frames: 533 ms, 194 ms). `when` says which frame, since the cost of drawing a full-screen sheet for the first time lands in different ones:
# "display" is the frame that displays the sheet (a ResizeObserver on the sheet runs after layout and before paint there, and busy-waits in its
# first callback with the sheet shown); "visible" is the first frame in which the sheet's computed opacity is above 0 (a requestAnimationFrame
# loop reads it each frame and busy-waits in the frame where it first leaves 0). WebKit does not paint a layer at opacity 0, so there the cost is
# paid when the opacity first rises above 0, which CI's WebKit showed: four frames 16 ms apart at opacity 0, then one of 194 ms. In that
# frame the page's script has run and the style is resolved (a transition has started, its clock with it), and the picture is then drawn; a
# wait in the rAF callback that first reads opacity above 0 lengthens that same frame, and the next frame is as late as it would have been.
# "every" holds EVERY frame of the fade for `stall` ms (a requestAnimationFrame loop that busy-waits in each): a phone drawing the cloth, and CI's WebKit
# at a pixel ratio of 3, draw frames 120 to 250 ms apart, and a fade that is a function of time can be over between two of them.
# The result is { samples, stalledEnd, bounded, waits } where stalledEnd is the time (ms since the click) the first wait ended, or null if it never
# ran, and waits how many times it ran. A sample is { at (ms since the click), t (the frame's time as the driver was given it, or the clock's at a
# click), display, opacity, mark, sheet (--sheet), main (--sheet as <main> reads it), fx (the display control's opacity), lift (how far the
# first group of links is from its place, px), anims (how many CSS transitions and animations run on the sheet and on its first group of links: a fade
# timed in CSS runs while the driver's value moves and "nothing is timed in CSS" cannot be read from the values alone), share (the shader's u_sheet,
# on a page with the cloth's probe; null otherwise) }.
FADE_PROBE = """([mode, bound, stall, when]) => new Promise((done) => {
  const toggle = document.querySelector('header [data-menu-toggle]'), sheet = document.querySelector('#shell-menu');
  const start = performance.now(), samples = [];
  let stalledEnd = null, second = !mode.includes('-then-'), still = 0, waits = 0;
  const wait = () => { const t = performance.now(); while (performance.now() - t < stall); waits += 1; if (stalledEnd === null) stalledEnd = performance.now() - start; };
  if (stall && when === 'display') new ResizeObserver(() => { if (stalledEnd === null && sheet.offsetWidth) wait(); }).observe(sheet);
  const main = document.querySelector('main'), fx = document.querySelector('.shell-corner--bottom-right'), group = sheet.querySelector('.menu__group');
  const look = (mark, time) => { const style = getComputedStyle(sheet), now = performance.now();
    samples.push({ at: now - start, t: time === undefined ? now : time, display: style.display, opacity: Number(style.opacity), mark,
      sheet: Number(style.getPropertyValue('--sheet')), main: Number(getComputedStyle(main).getPropertyValue('--sheet')),
      fx: Number(getComputedStyle(fx).opacity), lift: new DOMMatrix(getComputedStyle(group).transform).m42,
      anims: sheet.getAnimations().length + group.getAnimations().length,
      share: window.__cloth ? window.__cloth.uniforms.u_sheet : null }); };
  const frames = (time) => {
    look('frame', time);
    const seen = samples[samples.length - 1];
    if (stall && when === 'visible' && stalledEnd === null && seen.display !== 'none' && seen.opacity > 0) wait();
    if (stall && when === 'every') wait();
    if (!second && seen.display !== 'none' && seen.opacity > 0.25 && seen.opacity < 0.75) { second = true; look('before'); toggle.click(); look('after'); still = 0; }
    else still = (toggle.getAttribute('aria-expanded') === 'true' ? seen.opacity === 1 : seen.display === 'none') ? still + 1 : 0;
    if (still >= 2) done({ samples, stalledEnd, bounded: false, waits });
    else if (performance.now() - start >= bound) done({ samples, stalledEnd, bounded: true, waits });
    else requestAnimationFrame(frames);
  };
  toggle.click(); look('click');
  requestAnimationFrame(frames); })"""
# Shut or open, and at rest (an open sheet at full opacity, shell.js' value at 1; a shut one not displayed), before a probe. False if it has not come
# to rest within 3 s.
SHEET_AT_REST = """(open) => new Promise((done) => { const toggle = document.querySelector('header [data-menu-toggle]'), sheet = document.querySelector('#shell-menu');
  if ((toggle.getAttribute('aria-expanded') === 'true') !== open) toggle.click();
  const start = performance.now();
  const look = () => { const style = getComputedStyle(sheet);
    if (sheet.getAnimations().length === 0 && (style.display !== 'none') === open && (!open || style.opacity === '1')) done(true);
    else if (performance.now() - start > 3000) done(false); else requestAnimationFrame(look); };
  look(); })"""
# The opacity the displayed sheet has at --sheet 0, the first frame of an opening (menu.css: max(.01, --sheet)): above 0, because WebKit does not paint a
# layer at opacity 0 and would then draw the full-screen sheet for the first time in the fade's first frame, as CI's WebKit did (194 ms). Measured in
# Playwright's WebKit: a layer at exactly 0 is skipped (the frame that displays it is cheap, the frame where its opacity first rises pays), and one at
# 1e-5 or more is painted with the frame that displays it. A hold of .01 is far from that edge and is under three levels of an 8-bit channel, which
# no eye sees for the one frame it lasts.
SHEET_HELD_OPACITY = "0.01"
FADE_SLACK = 0.02   # opacity may differ by this between two readings of a fade that must not go back
# Frames further apart than this (their median interval) are a runner too slow to say whether a sheet has come to rest within FADE_BOUND_MS: that is
# a note, not a problem (unsettled_problem). A fade's frames are never excused by it: the driver steps by at most a quarter per drawn frame, so
# however far apart the frames are the fade shows two between 0 and 1 (fade_blink_problem has no leniency; sheet_slow_frames_problems holds it).
FADE_FRAME_LIMIT_MS = 50
FADE_BOUND_MS = 3000   # a fade probe stops at this even if the sheet is not at rest (FADE_PROBE)
SLOW_FIRST_FRAME_MS = 400   # how long the slow-first-frame probe holds the sheet's first frame
# How fast the sheet's opacity may move between two frames after an opening interrupts the close, per ms between them. The steepest part of the
# driver's curve (1 - (1 - p ** 1.48) ** 3.68, opening and closing alike, over 700 ms) is 1.95 per unit of progress, .00278 a ms, a fifth of the way
# through; a reversal goes on at that pace, not faster. The limits are just above that, plus REVERSAL_STEP_SLACK, since a limit on the step itself would
# fail a legitimate reversal as soon as one frame is longer than the others.
REVERSAL_RISE_PER_MS = 0.0029
REVERSAL_FALL_PER_MS = 0.0029
REVERSAL_STEP_SLACK = 0.04
REVERSAL_TURN_FRAMES = 1   # the click's own frame has no time behind it (the step is 0), then the sheet moves the other way
# How far the driver's curve may lie from Come in's own, CSS's ease, at any time of the fade, in opacity (measured: .0227, the same both ways), and how
# far a recorded value may lie from the driver's model of itself.
SHEET_EASE_ERROR = 0.023
SHEET_MODEL_TOLERANCE = 0.001
SHEET_SAME_VALUE = 1e-5   # two readings of one value (the shader's, the links', <main>'s): a browser prints a number to six digits, so they differ by that and no more; a step of a frame is .02 or more


def css_bezier(x1, y1, x2, y2):
    """The y of a CSS cubic-bezier(x1, y1, x2, y2) for an x (the time's share), by bisection on the curve's parameter."""
    def curve(x):
        low, high = 0.0, 1.0
        for _ in range(50):
            mid = (low + high) / 2
            if 3 * (1 - mid) ** 2 * mid * x1 + 3 * (1 - mid) * mid ** 2 * x2 + mid ** 3 < x:
                low = mid
            else:
                high = mid
        t = (low + high) / 2
        return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t ** 2 * y2 + t ** 3
    return curve


SHEET_EASE = css_bezier(0.25, 0.1, 0.25, 1)   # ease, Come in's (base.css): the sheet rises by it and falls by 1 minus it


class SheetModel:
    """The menu's driver (shell.js, setUpMenu) in Python, to replay a recorded fade against: --sheet, v, goes to `to` (1 open, 0 shut) by a share of
    the fade that is the frame's time over 700 ms, opening and closing alike, never more than a quarter; what is left to go is (1 - p ** 1.48) ** 3.68
    of the progress p, which is recovered from v each step so that a turn-around starts where v is. The first step of a fade has no time behind it.
    A step is made once for a frame's time."""

    def __init__(self, v):
        self.v, self.to, self.last = float(v), round(v), 0

    def go(self, to):
        self.to, self.last = to, 0

    def step(self, time):
        if self.v != self.to and time != self.last:
            p = (1 - abs(self.to - self.v) ** (1 / 3.68)) ** (1 / 1.48) + min(0.25, (time - (self.last or time)) / 700)
            self.v = abs(self.to - (1 - min(1, p) ** 1.48) ** 3.68)
            self.last = time
        return self.v


def frame_times(samples, count=12):
    """The first frames a fade probe saw, as (ms since the click, display, opacity): whether a fade that shows no in-between frames
    blinked, or ran between frames that came too far apart to see it."""
    return [(round(sample["at"]), sample["display"], round(sample["opacity"], 2)) for sample in samples[:count]]


def median_interval(times):
    """The median interval (ms) between consecutive times; None with fewer than two."""
    gaps = sorted(later - earlier for earlier, later in zip(times, times[1:]))
    return (gaps[len(gaps) // 2] + gaps[~(len(gaps) // 2)]) / 2 if gaps else None


def frame_gap(samples, since, ended):
    """The median interval (ms) between the frames of a fade: those from `since` (ms after the click) up to and including the first for which
    `ended(sample)` is true. None with fewer than two frames."""
    times = []
    for sample in samples:
        if sample["mark"] != "frame" or sample["at"] < since:
            continue
        times.append(sample["at"])
        if ended(sample):
            break
    return median_interval(times)


def too_slow_to_judge(samples):
    """True if the frames of a whole probe came further apart than FADE_FRAME_LIMIT_MS (their median interval): the runner is too slow to
    hold the sheet to what a fade or a turn looks like, whatever the sheet did."""
    gap = frame_gap(samples, 0.0, lambda sample: False)
    return gap is not None and gap > FADE_FRAME_LIMIT_MS


def unsettled_problem(where, text, samples, bounded):
    """`text` as a problem (with the frame times), unless it is about where the sheet ended up and the probe did not see the sheet come to rest
    (`bounded`: FADE_BOUND_MS passed) while its frames were further apart than FADE_FRAME_LIMIT_MS: then it is a note, since a runner that
    slow may simply not have finished, as fade_blink_problem does for a fade. A probe that came to rest is judged as it ended."""
    frames = f"Frames (ms, display, opacity): {frame_times(samples)}"
    if bounded and too_slow_to_judge(samples):
        REVIEW_NOTES.append(f"{where}: {text}, but the sheet had not come to rest {FADE_BOUND_MS} ms after the click and this run drew frames "
                            f"{frame_gap(samples, 0.0, lambda sample: False):.0f} ms apart (median), so it was not judged. {frames}")
        return None
    return f"{where}: {text}{f' ({FADE_BOUND_MS} ms after the click; it had not come to rest)' if bounded else ''}. {frames}"


def fade_blink_problem(where, what, between, samples, *, advice="it blinks, it should fade."):
    """The problem of a fade that shows fewer than two frames between 0 and 1, or None. No runner is too slow to excuse it: the driver steps
    --sheet by the frame's share of the fade and never by more than a quarter, so the frames of a fade, however far apart they are, show at least two
    between 0 and 1 (sheet_slow_frames_problems holds every frame 120 ms and wants them all the same). A sheet that blinks, as an iPhone's did,
    is a fade timed by something other than the frames drawn."""
    if len(between) >= 2:
        return None
    return f"{where}: the sheet shows {len(between)} frames between 0 and 1 while it {what}; {advice} Frames (ms, display, opacity): {frame_times(samples)}"


def sheet_reversal_findings(where, samples, before, after):
    """An opening that interrupts the close (the second click is read as `before`, just before it, and `after`, just after it, in the same
    instant) turns the sheet back from where it is, and without a snap or a jump (spec 4.4):
    1. the sheet's opacity is the same just after the click as just before it: no frame has been drawn, so any difference is the click moving the
       value (the driver takes the opening up from the value it has: its first step has no time behind it);
    2. no step between two frames falls faster than the closing curve can go or rises faster than the opening curve (REVERSAL_FALL_PER_MS and
       REVERSAL_RISE_PER_MS, with REVERSAL_STEP_SLACK): a snap or a jump at the reversal, whatever the runner's frame rate, since the limit
       follows the time between the frames;
    3. the lowest opacity after the click comes within REVERSAL_TURN_FRAMES frames of it (the click's own frame has no time behind it, then the sheet
       rises): a sheet that keeps falling after the click, or waits to turn, is the old two-step opening.
    The frame times and opacities from the click on are in each message."""
    path = [after] + [sample for sample in samples if sample["at"] > after["at"] and sample["display"] != "none"]
    seen = f"After the click (ms, opacity): {[(round(sample['at']), round(sample['opacity'], 2)) for sample in path[:12]]}"
    problems = []
    if abs(after["opacity"] - before["opacity"]) > FADE_SLACK:
        problems.append(f"{where}: the sheet was at opacity {before['opacity']:.2f} when the second click came and {after['opacity']:.2f} in the same instant after it, before any "
                        f"frame; the opening must go on from where the close had got to. {seen}")
    for earlier, later in zip(path, path[1:]):
        step, elapsed = later["opacity"] - earlier["opacity"], later["at"] - earlier["at"]
        if step > REVERSAL_RISE_PER_MS * elapsed + REVERSAL_STEP_SLACK:
            problems.append(f"{where}: the sheet rises by {step:.2f} in {elapsed:.0f} ms after the second click, faster than the opening's curve can go "
                            f"(at most {REVERSAL_RISE_PER_MS * elapsed + REVERSAL_STEP_SLACK:.2f}): a jump at the reversal. {seen}")
            break
        if -step > REVERSAL_FALL_PER_MS * elapsed + REVERSAL_STEP_SLACK:
            problems.append(f"{where}: the sheet falls by {-step:.2f} in {elapsed:.0f} ms after the second click, faster than the closing's curve can go "
                            f"(at most {REVERSAL_FALL_PER_MS * elapsed + REVERSAL_STEP_SLACK:.2f}): a snap at the reversal. {seen}")
            break
    lowest = min(sample["opacity"] for sample in path)
    turn = next(index for index, sample in enumerate(path) if sample["opacity"] <= lowest + FADE_SLACK)
    if turn > REVERSAL_TURN_FRAMES:
        problems.append(f"{where}: after the second click the sheet kept falling for {turn} frames, down to {lowest:.2f}, before it rose; it should turn back within "
                        f"{REVERSAL_TURN_FRAMES} (the click's own frame has no time behind it). {seen}")
    return problems


def sheet_replay_findings(where, mode, samples, *, cloth=False):
    """The recorded fade (FADE_PROBE) against shell.js' driver (SheetModel), frame by frame: the value the page shows in --sheet at every sample is the
    one the driver makes of the frames' own times (the time each frame was given, as the driver was), within SHEET_MODEL_TOLERANCE. That holds the
    lengths (700 ms opening and closing, per drawn frame and never more than a quarter at a step), the curve, the first step with no time
    behind it, a reversal starting from the value it has, and the end at exactly 1 and 0. And what follows the value, in the same frame: the sheet's
    opacity is max(.01, v) and its first group of links is 6px * (1 - v) above their place, <main> reads the same --sheet; on a page with the cloth
    the shader's share is it exactly (the cloth is handed the driver's step, so the paper is drawn with the value of its own frame and not the
    last frame's), the display control's opacity is 1 - v and <main> is masked as the sheet leaves it, and no CSS transition or animation runs on the
    sheet or its first group of links in any sample (a transition on the sheet's opacity would run while the driver's value moves, where the values
    alone cannot tell it from the driver). `mode` is the probe's: it says where the sheet
    began and where each click sent it."""
    start = 1.0 if mode in ("close", "close-then-open") else 0.0
    goals = iter({"open": (1,), "close": (0,), "open-then-close": (1, 0), "close-then-open": (0, 1)}[mode])
    model = SheetModel(start)
    problems = []
    for index, sample in enumerate(samples):
        if sample["mark"] in ("click", "after"):
            model.go(next(goals))
        elif sample["mark"] == "frame":
            model.step(sample["t"])
        elif sample["mark"] != "before":
            continue
        shown = sample["display"] != "none"
        wrong = []
        if abs(sample["sheet"] - model.v) > SHEET_MODEL_TOLERANCE:
            wrong.append(f"--sheet is {sample['sheet']:.4f} where the driver makes {model.v:.4f}")
        if shown and abs(sample["opacity"] - max(0.01, sample["sheet"])) > SHEET_MODEL_TOLERANCE:
            wrong.append(f"the sheet's opacity is {sample['opacity']:.4f}, not max(.01, --sheet) {max(0.01, sample['sheet']):.4f}")
        if shown and abs(sample["lift"] + 6 * (1 - sample["sheet"])) > 0.05:
            wrong.append(f"its links are {sample['lift']:.2f}px from their place, not {-6 * (1 - sample['sheet']):.2f}")
        if sample["anims"]:
            wrong.append(f"{sample['anims']} CSS transition(s) or animation(s) run on the sheet or its first group of links; nothing about the sheet is timed in CSS, "
                         f"its fade is the driver's value alone (a function of time can finish between two late frames)")
        if abs(sample["main"] - sample["sheet"]) > SHEET_SAME_VALUE:
            wrong.append(f"<main> reads --sheet {sample['main']:.4f}")
        if cloth and sample["share"] is not None and abs(sample["share"] - sample["sheet"]) > SHEET_SAME_VALUE:
            wrong.append(f"the shader's share is {sample['share']:.4f}, the links' --sheet {sample['sheet']:.4f}: the paper is not the value of its own frame")
        if cloth and abs(sample["fx"] - (1 - sample["sheet"])) > 0.002:
            wrong.append(f"the display control's opacity is {sample['fx']:.3f}, not {1 - sample['sheet']:.3f}")
        if wrong:
            problems.append(f"{where}, sample {index} ({sample['mark']}, {sample['at']:.0f} ms): {'; '.join(wrong)}. Frames (ms, display, opacity): {frame_times(samples)}")
            break
    return problems


def sheet_fade_findings(page, label, *, cloth=False):
    """The findings of the sheet's fade (sheet_fade_problems is the pages part's check on them; cloth_sheet_motion_problems reads them on a page with the
    cloth, `cloth=True`; helpers shared by two parts are not named ..._problems, which pages.tests_site_parts counts as checks). The sheet's value and
    opacity read at every frame (FADE_PROBE), in whatever browser `page` is, and replayed against the driver
    (sheet_replay_findings: the lengths, the curves, a reversal's start, the end). Opening and closing each show values between 0
    and 1, in one direction (a sheet that blinks in, which an iPhone showed, has none), the open starting at opacity .01 and the close
    ending with the sheet gone. No frame spacing excuses a fade that shows fewer than two frames between 0 and 1: the driver steps by at most a
    quarter per drawn frame (fade_blink_problem). A close that interrupts the opening, and an opening that interrupts the close, each when the
    sheet is seen part way through its fade, never raise the opacity (a close) or lower it by more than the click's own frame (an opening: its first
    step has no time behind it) from where it was, and the opening ends fully shown. An interruption that finds no part-way frame is a note.
    Nothing is promised about how smoothly a close that interrupts the opening goes on: only that it turns back from where it was and does not
    flash; an opening that interrupts the close, more than that, turns back without a snap or a jump (sheet_reversal_findings). Every message carries
    the frame times. Each probe runs until the sheet is at rest, bounded at FADE_BOUND_MS: where the sheet ends is judged as it ended, and a
    sheet that had not come to rest by the bound is a problem only if the frames were close together (unsettled_problem), else a note, since a
    runner that slow may not have finished."""
    problems = []
    for mode, rest in (("open", False), ("close", True), ("open-then-close", False), ("close-then-open", True)):
        if not page.evaluate(SHEET_AT_REST, rest):
            problems.append(f"{label}, {mode.replace('-', ' ')}: the sheet did not come to rest {'open' if rest else 'shut'} within 3 s (an open sheet has --sheet at 1 "
                            f"and is fully opaque), so nothing was checked")
            continue
        result = page.evaluate(FADE_PROBE, [mode, FADE_BOUND_MS, 0, "display"])
        samples, bounded = result["samples"], result["bounded"]
        shown = [sample for sample in samples if sample["display"] != "none"]
        values = [sample["opacity"] for sample in shown]
        where = f"{label}, {mode.replace('-', ' ')}"
        frames = f"Frames (ms, display, opacity): {frame_times(samples)}"
        between = [value for value in values if 0.02 < value < 0.98]
        problems += sheet_replay_findings(where, mode, samples, cloth=cloth)
        if mode == "open":
            if not values or values[0] > 0.5:
                problems.append(f"{where}: the sheet is first shown at opacity {values[0] if values else None}; it should start from nearly 0. {frames}")
            elif values[0] != float(SHEET_HELD_OPACITY):
                problems.append(f"{where}: the sheet is first shown at opacity {values[0]}; it should be held at {SHEET_HELD_OPACITY} at --sheet 0, since WebKit does not "
                                f"paint a layer at opacity 0 and would draw the sheet for the first time in the fade's first frame. {frames}")
            found = fade_blink_problem(where, "opens", between, samples, advice="it blinks in, it should fade in.")
            if found:
                problems.append(found)
            if any(later < earlier - FADE_SLACK for earlier, later in zip(values, values[1:])):
                problems.append(f"{where}: the sheet's opacity falls while it opens: {[round(value, 2) for value in values]}. {frames}")
            if not values or values[-1] != 1:
                found = unsettled_problem(where, f"the sheet ends at opacity {values[-1] if values else None}; it should be fully shown", samples, bounded)
                if found:
                    problems.append(found)
        elif mode == "close":
            found = fade_blink_problem(where, "closes", between, samples, advice="it blinks out, it should fade out.")
            if found:
                problems.append(found)
            if any(later > earlier + FADE_SLACK for earlier, later in zip(values, values[1:])):
                problems.append(f"{where}: the sheet's opacity rises while it closes: {[round(value, 2) for value in values]}. {frames}")
            if samples[-1]["display"] != "none":
                found = unsettled_problem(where, f"the sheet is still displayed {samples[-1]['at']:.0f} ms after it was closed", samples, bounded)
                if found:
                    problems.append(found)
        else:
            before = next((sample for sample in samples if sample["mark"] == "before"), None)
            if before is None:
                REVIEW_NOTES.append(f"{where}: the sheet was not seen part way through its fade (opacity between .25 and .75) at any frame, so the interruption "
                                    f"was not tried; this run's frames were too far apart or the fade was over. {frames}")
                continue
            after = next(sample for sample in samples if sample["mark"] == "after")
            later = [sample for sample in samples if sample["at"] > after["at"] and sample["display"] != "none"]
            reached = [after["opacity"], *(sample["opacity"] for sample in later[:1 if mode == "close-then-open" else len(later)])]
            if max(reached) > before["opacity"] + FADE_SLACK:
                problems.append(f"{where}: the sheet was at opacity {before['opacity']:.2f} when the second click came and shows {max(reached):.2f} just after it; "
                                f"an interruption must not flash the sheet fuller than it was. {frames}")
            if mode == "open-then-close" and samples[-1]["display"] != "none":
                found = unsettled_problem(where, f"the sheet is still displayed {samples[-1]['at']:.0f} ms after it was closed", samples, bounded)
                if found:
                    problems.append(found)
            if mode == "close-then-open":
                problems += sheet_reversal_findings(where, samples, before, after)
            if mode == "close-then-open" and (samples[-1]["display"] == "none" or samples[-1]["opacity"] != 1):
                found = unsettled_problem(where, f"the sheet ends display {samples[-1]['display']} at opacity {samples[-1]['opacity']}; it should be fully shown",
                                          samples, bounded)
                if found:
                    problems.append(found)
    return problems


def sheet_fade_problems(page, label):
    """The sheet's fade as the page checks (the pages part) read it, with no cloth: sheet_fade_findings. The cloth part reads the same fades on a page
    with the cloth (cloth_sheet_motion_problems) through the findings, not through this check, which keeps each check in one part."""
    return sheet_fade_findings(page, label)


SLOW_FIRST_FRAMES = (("display", "the frame that displays it"), ("visible", "the first frame in which its opacity is above 0"))


def sheet_slow_first_frame_problems(page, label, when):
    """The sheet still fades in when one frame of its opening is slow, which is the owner's bug and does not depend on the runner's speed.
    The probe (FADE_PROBE with a stall) busy-waits SLOW_FIRST_FRAME_MS in the frame that displays the sheet (when="display": a
    ResizeObserver on the sheet, between layout and paint) or in the first frame in which its opacity is above 0 (when="visible": a
    requestAnimationFrame loop that reads the computed opacity), as WebKit can take in drawing a full-screen sheet on a phone. The first
    is where a browser that paints a layer at opacity 0 pays (Playwright's WebKit on a Mac); the second is where one that does not pays
    (CI's WebKit: four frames 16 ms apart at opacity 0, then 194 ms). A fade whose clock starts before that frame is drawn (keyframes, a
    transition from @starting-style, a transition begun while the sheet is still at opacity 0) is then over before it is: the frames after
    the wait show opacity 1 and none in between, a blink.
    The fade must begin once that frame is drawn: after the wait the frames show at least two between 0 and 1, then 1 (the driver's first
    step has no time behind it, and the frame after the wait steps by at most a quarter). Judged whatever the frame spacing, as in
    sheet_fade_problems."""
    where = f"{label}, opening with {dict(SLOW_FIRST_FRAMES)[when]} held {SLOW_FIRST_FRAME_MS} ms"
    if not page.evaluate(SHEET_AT_REST, False):
        return [f"{where}: the sheet did not come to rest shut within 3 s, so nothing was checked"]
    result = page.evaluate(FADE_PROBE, ["open", FADE_BOUND_MS, SLOW_FIRST_FRAME_MS, when])
    samples, stalled, bounded = result["samples"], result["stalledEnd"], result["bounded"]
    frames = f"Frames (ms, display, opacity): {frame_times(samples)}"
    if stalled is None or stalled < SLOW_FIRST_FRAME_MS:
        return [f"{where}: the wait that holds the frame did not run while the sheet was displayed (it ended at {stalled}), so nothing was checked. {frames}"]
    after = [sample for sample in samples if sample["at"] >= stalled and sample["display"] != "none"]
    values = [sample["opacity"] for sample in after]
    between = [value for value in values if 0.02 < value < 0.98]
    found = fade_blink_problem(where, "opens after the held frame", between, samples,
                               advice="the fade began before the held frame was drawn and was over when it was; it should begin once the frame is.")
    problems = ([found] if found else []) + sheet_replay_findings(where, "open", samples)
    if values and values[-1] != 1:
        found = unsettled_problem(where, f"the sheet ends at opacity {values[-1]}; it should be fully shown", samples, bounded)
        if found:
            problems.append(found)
    return problems


SLOW_EVERY_FRAME_MS = 120   # how long the every-frame probe holds each frame: a model of CI's WebKit at a pixel ratio of 3 (250 ms a frame) and of a phone drawing the cloth


def sheet_slow_frames_problems(page, label, mode):
    """The sheet still fades, in and out, when every frame is slow, which a phone drawing the cloth, and CI's WebKit at a pixel ratio of 3, are
    (250 ms a frame there). The probe (FADE_PROBE with a stall) busy-waits SLOW_EVERY_FRAME_MS in every frame of the fade, a requestAnimationFrame
    loop that reads the computed opacity, so each frame is as late as the wait makes it. A fade that is a function of time (a CSS transition, a CSS
    animation, a curve timed from a clock) can finish between two such frames: the frames after the wait show 1 and none in between, a blink. A
    fade that advances by one step per drawn frame, never by more than a quarter, cannot be skipped: at least two frames between 0 and 1, whatever
    the spacing, so no frame spacing excuses this run."""
    where = f"{label}, {mode} with every frame held {SLOW_EVERY_FRAME_MS} ms"
    if not page.evaluate(SHEET_AT_REST, mode == "close"):
        return [f"{where}: the sheet did not come to rest {'open' if mode == 'close' else 'shut'} within 3 s, so nothing was checked"]
    result = page.evaluate(FADE_PROBE, [mode, FADE_BOUND_MS, SLOW_EVERY_FRAME_MS, "every"])
    samples, bounded = result["samples"], result["bounded"]
    frames = f"Frames (ms, display, opacity): {frame_times(samples)}"
    if result["waits"] < 3:
        return [f"{where}: the wait that holds each frame ran only {result['waits']} times, so nothing was checked. {frames}"]
    shown = [sample for sample in samples if sample["display"] != "none"]
    between = [sample["opacity"] for sample in shown if 0.02 < sample["opacity"] < 0.98]
    opens = mode == "open"
    found = fade_blink_problem(where, "opens" if opens else "closes", between, samples,
                               advice="the fade was over between two late frames; it should advance with each frame that is drawn, never skipping to its end.")
    problems = ([found] if found else []) + sheet_replay_findings(where, mode, samples)
    if opens and (not shown or shown[-1]["opacity"] != 1):
        found = unsettled_problem(where, f"the sheet ends at opacity {shown[-1]['opacity'] if shown else None}; it should be fully shown", samples, bounded)
        problems += [found] if found else []
    if not opens and samples[-1]["display"] != "none":
        found = unsettled_problem(where, f"the sheet is still displayed {samples[-1]['at']:.0f} ms after it was closed", samples, bounded)
        problems += [found] if found else []
    return problems


# The one face that About never loads (its heading is Cormorant Garamond italic 300, which every chapter preloads) and How, Begin, Promises and Plans set their
# headings in (italic 400, --font-heading): the first change from About to How is the one that has to find it. Its file is held until the change is waiting for it
# (hold_font, release_when_waited_for: never for a fixed time, which a slow runner's late wait ends before it begins), as a slow connection would, and a change
# must neither wait for it inside a view transition's update callback (the page's drawing is paused until the callback
# is done: in Chromium the whole page, the cloth included, froze for up to FONT_WAIT_MS) nor show the new headings before it is there (WebKit does not wait
# at all: its headings showed in a fallback face for about 270 ms).
FONT_FACE = ("Cormorant Garamond", "italic", "400")   # family, style, weight: read from the FontFace's own status (document.fonts.check says true in WebKit while a face loads)
FACE_NAME = "Cormorant Garamond italic 400"
FONT_FILES = re.compile(r".*/fonts/cormorant-garamond/italic-400-.*")
FONT_BOUND_MS = 400       # shell.js' FONT_WAIT_MS: the longest a change waits for a face that is still loading
FONT_CALLBACK_MS = 100    # the longest an update callback may take: nothing in it waits for a face; the swap and the layout are a few ms
# Whether a face (its latin file, the one whose range holds a space and the Latin text of the pages: U+0-FF in Chromium's spelling, U+0000-00FF in the stylesheet's)
# has loaded, in the page.
FACE_LOADED = """const faceLoaded = (face) => { const faces = [...document.fonts].filter((font) => font.family.replace(/["']/g, '') === face[0] && font.style === face[1]
    && font.weight === face[2] && /U\\+0{0,4}-0{0,2}FF/i.test(font.unicodeRange)); return faces.length > 0 && faces.every((font) => font.status === 'loaded'); };"""


# A change chosen from the open phone sheet (spec 4.4, 4.5), frame by frame from the tap on a link to the end, read from inside the page so that the
# frames fall exactly where the check says. The tap is the link's own click(); then every drawn frame is a sample, until the new page shows, <main>
# is at full opacity and the sheet is gone, at three frames running (`bound` ms ends the probe anyway, and the result says so). stall > 0 holds
# EVERY frame that many ms (a requestAnimationFrame loop that busy-waits in each), as a phone drawing the cloth does. want is [the new address, the new
# page's heading, a font (for document.fonts.check) or null]. A sample is { at (ms since the tap), t (the frame's time), mark, display and opacity
# (the sheet's), sheet (--sheet), main (<main>'s computed opacity), heading (the h1 in <main>), lock (the root's overflow: the scroll lock), face
# (whether the font is loaded, or null) }; a swap is what the page looked like in the task that replaced <main>'s content (a MutationObserver
# reads it as a microtask of that task): the scroll lock, whether the sheet is displayed, <main>'s opacity, the heading it holds, the font, and how many
# view transitions (VIEW_TRANSITION_CALLS) had been started by then. `end` is the state the page was left in.
VIEW_TRANSITION_CALLS = """(() => { window.__transitions = []; const start = Document.prototype.startViewTransition;
  if (start) Document.prototype.startViewTransition = function (...args) { window.__transitions.push(performance.now()); return start.apply(this, args); }; })();"""
SHEET_CHANGE_PROBE = """([href, stall, bound, want]) => new Promise((done) => {
  const root = document.documentElement, sheet = document.querySelector('#shell-menu'), main = document.querySelector('main');
  const link = sheet.querySelector(`a[href="${href}"]`), [path, heading, face] = want;
  const samples = [], swaps = [], t0 = performance.now();
  FACE_LOADED_JS
  let waits = 0, quiet = 0;
  const wait = () => { const began = performance.now(); while (performance.now() - began < stall); waits += 1; };
  const title = () => { const h = main.querySelector('h1'); return h ? h.textContent.trim() : null; };
  const look = (mark, time) => { const style = getComputedStyle(sheet), now = performance.now();
    samples.push({ at: now - t0, t: time === undefined ? now : time, mark, display: style.display, opacity: Number(style.opacity),
      sheet: Number(style.getPropertyValue('--sheet')), main: Number(getComputedStyle(main).opacity), heading: title(),
      lock: getComputedStyle(root).overflow, face: face ? faceLoaded(face) : null }); };
  new MutationObserver(() => swaps.push({ at: performance.now() - t0, lock: getComputedStyle(root).overflow, sheet: getComputedStyle(sheet).display,
    open: Boolean(document.querySelector('.menu__list.is-open')), main: Number(getComputedStyle(main).opacity), heading: title(),
    face: face ? faceLoaded(face) : null, transitions: window.__transitions.length })).observe(main, { childList: true });
  const frames = (time) => {
    look('frame', time);
    if (stall) wait();
    const seen = samples[samples.length - 1];
    quiet = location.pathname === path && seen.heading === heading && seen.main === 1 && seen.display === 'none' ? quiet + 1 : 0;
    const finish = (bounded) => done({ samples, swaps, waits, bounded, transitions: window.__transitions.map((at) => at - t0),
      end: { path: location.pathname, heading: title(), main: getComputedStyle(main).opacity, inline: main.style.opacity, transition: main.style.transition,
             focus: document.activeElement === main.querySelector('h1'), expanded: document.querySelector('header [data-menu-toggle]').getAttribute('aria-expanded'),
             marks: [...document.querySelectorAll('#shell-menu a[aria-current]')].map((a) => a.getAttribute('href')), inert: document.querySelector('main').inert } });
    if (quiet >= 3) finish(false);
    else if (performance.now() - t0 >= bound) finish(true);
    else requestAnimationFrame(frames);
  };
  link.click(); look('tap');
  requestAnimationFrame(frames); })"""
SHEET_CHANGE_PROBE = SHEET_CHANGE_PROBE.replace("FACE_LOADED_JS", FACE_LOADED)
FIRST_STEP_MS = 16   # how long after a fade's first frame (which has no time behind it) a frame must come to be judged against 1: the fade's easing (0.7 s) has moved it by .001 by 3 ms and by .01 by 16
SHEET_CHANGE_BOUND_MS = 6000   # a change from the sheet is over within this, at frames 120 ms apart as at normal speed (its two fades of 0.7 s take about 2 s then)
# The pages a change from the open sheet is tried between (the menu's links): About to How, two chapters, and Terms to Privacy, the two tallest reading
# pages (about 5,400 px at 375 wide, 16,200 device px at 3x, above the largest texture many mobile browsers take), as (from, to, from's heading, to's heading).
SHEET_CHANGES = (("/about/", "/how/", "A quiet place for what your agents make", "The life of a file"),
                 ("/terms/", "/privacy/", "Terms of Service", "Privacy Policy"))


def change_frames(samples, count=14):
    """The first frames of a change as (ms since the tap, the sheet's opacity or None once it is gone, <main>'s opacity, which page's content is in <main>
    by the first word of its heading), for the messages of sheet_change_findings."""
    return [(round(sample["at"]), None if sample["display"] == "none" else round(sample["opacity"], 2), round(sample["main"], 2),
             (sample["heading"] or "?").split()[0]) for sample in samples[:count]]


def sheet_change_findings(where, result, old, new, path, face=None, gave_up=False):
    """A change chosen from the open phone sheet fades and snaps nothing (spec 4.4, 4.5). `result` is SHEET_CHANGE_PROBE's, `old` and `new` the headings
    of the two pages. From the tap on:
    1. no view transition is started: no picture of a page (a tall one may not be drawn) and nothing for a resize to cut short;
    2. the sheet starts its normal close, and shows values between 0 and 1 in falling order, at least two (fade_blink_problem), then is gone for good;
    3. the old content fades out with it, so it never reappears at full strength: from the second frame on (the first of a fade has no time behind
       it) its opacity is below 1 and never rises, through at least two frames between 0 and 1, and it is gone when the sheet is;
    4. it is swapped for the new content in one task while <main> is invisible, the scroll lock released and the sheet gone, before the swap;
    5. the new content fades in: its first frame is nearly invisible, then at least two frames between 0 and 1 in rising order, ending at 1;
    6. what it leaves behind: the new address and heading in <main>, at full opacity with nothing of the driver's left on it, focus on the heading,
       the menu shut with the new page marked, and nothing inert.
    With a `face` (a font that the new page's heading is set in and the old page's is not): it is loaded before the content is swapped in, and so
    whenever the new content shows, never in a fallback face (item: no change waits on a font), unless the change's wait for it ran out (`gave_up`).
    Every message carries the frames. No frame spacing excuses a fade that shows fewer than two frames between 0 and 1: both fades are advanced per
    drawn frame by at most a quarter (shell.js), so frames 120 ms apart show them too."""
    samples, swaps, end = result["samples"], result["swaps"], result["end"]
    listing = f"Frames (ms, the sheet's opacity, <main>'s opacity, the content): {change_frames(samples)}"
    if result["bounded"]:
        return [f"{where}: the change had not finished {SHEET_CHANGE_BOUND_MS} ms after the tap. {listing}"]
    problems = []
    if result["transitions"]:
        problems.append(f"{where}: {len(result['transitions'])} view transition(s) started; a change from the sheet fades by its own driver, with no picture of the page "
                        f"(a tall page's may be blurred or not drawn) and no transition for a resize to cut short. {listing}")
    shown = [sample for sample in samples if sample["display"] != "none"]
    between = [sample["opacity"] for sample in shown if 0.02 < sample["opacity"] < 0.98]
    found = fade_blink_problem(where, "closes after the tap", between, samples, advice="it blinks out, it should fade out as it does when MENU is pressed.")
    if found:
        problems.append(found)
    values = [sample["opacity"] for sample in shown]
    if any(later > earlier + FADE_SLACK for earlier, later in zip(values, values[1:])):
        problems.append(f"{where}: the sheet's opacity rises while it closes: {[round(value, 2) for value in values]}. {listing}")
    gone = next((index for index, sample in enumerate(samples) if sample["display"] == "none"), None)
    if gone is None or any(sample["display"] != "none" for sample in samples[gone:]):
        problems.append(f"{where}: the sheet is not gone for good once it has closed. {listing}")
    # the old content
    # (a frame that comes less than FIRST_STEP_MS after the fade's first has had a thousandth of the fade or less behind it, and reads 1: WebKit draws a second frame
    # 3 ms after the first now and then, which is no reappearing)
    holds = [sample for sample in samples[2:] if sample["heading"] == old and sample["at"] - samples[1]["at"] >= FIRST_STEP_MS]
    full = [sample for sample in holds if sample["main"] > 0.999]
    if full:
        problems.append(f"{where}: the old content shows at full strength {len(full)} time(s) after the fade's first frame, from {full[0]['at']:.0f} ms on; it should fade out "
                        f"with the sheet, never reappear. {listing}")
    fading = [sample["main"] for sample in samples if sample["heading"] == old and 0.02 < sample["main"] < 0.98]
    if len(fading) < 2:
        problems.append(f"{where}: the old content shows {len(fading)} frames between 0 and 1 on its way out; it should fade, not snap. {listing}")
    outs = [sample["main"] for sample in samples if sample["heading"] == old]
    if any(later > earlier + FADE_SLACK for earlier, later in zip(outs, outs[1:])):
        problems.append(f"{where}: the old content's opacity rises on its way out: {[round(value, 2) for value in outs]}. {listing}")
    if gone is not None and samples[gone]["heading"] == old and samples[gone]["main"] > 0.02:
        problems.append(f"{where}: the sheet is gone but the old content is still at {samples[gone]['main']:.2f}; it goes with the sheet. {listing}")
    # the swap
    if len(swaps) != 1:
        problems.append(f"{where}: <main>'s content was replaced {len(swaps)} times; once. {listing}")
    else:
        swap = swaps[0]
        if swap["main"] > 0.001:
            problems.append(f"{where}: the content was swapped while <main> was at opacity {swap['main']:.2f}; it should be invisible. {listing}")
        if swap["lock"] == "hidden" or swap["open"] or swap["sheet"] != "none":
            problems.append(f"{where}: at the swap the scroll lock is {swap['lock']!r}, the sheet {'open' if swap['open'] else 'shut'} and {swap['sheet']!r}; it should be "
                            f"released (the root's overflow is not hidden) before the swap, when the close ends. {listing}")
        if swap["transitions"]:
            problems.append(f"{where}: a view transition had started by the swap. {listing}")
        if face and not swap["face"]:
            face_not_loaded(problems, gave_up, where, f"the new page's heading face ({FACE_NAME}) was not loaded when the content was swapped in; the faces a page uses load "
                                                       f"before any change swaps it in. {listing}")
    # the new content
    arrivals = [sample for sample in samples if sample["heading"] == new]
    if not arrivals:
        problems.append(f"{where}: the new content was never seen in <main>. {listing}")
    else:
        if arrivals[0]["main"] > 0.5:
            problems.append(f"{where}: the new content first shows at opacity {arrivals[0]['main']:.2f}; it should come from nearly 0. {listing}")
        showing = next((sample for sample in arrivals if sample["main"] > 0.02), None)
        if face and showing and not showing["face"]:
            face_not_loaded(problems, gave_up, where, f"the new content first shows ({showing['at']:.0f} ms, opacity {showing['main']:.2f}) with {FACE_NAME} still loading, in a "
                                                       f"fallback face. {listing}")
        rising = [sample["main"] for sample in arrivals if 0.02 < sample["main"] < 0.98]
        if len(rising) < 2:
            problems.append(f"{where}: the new content shows {len(rising)} frames between 0 and 1 as it comes in; it should fade in, not snap. {listing}")
        ins = [sample["main"] for sample in arrivals]
        if any(later < earlier - FADE_SLACK for earlier, later in zip(ins, ins[1:])):
            problems.append(f"{where}: the new content's opacity falls as it comes in: {[round(value, 2) for value in ins]}. {listing}")
    # what it leaves behind
    if end["path"] != path or end["heading"] != new:
        problems.append(f"{where}: the change ended on {end['path']} with the heading {end['heading']!r}, not {path} and {new!r}")
    if end["main"] != "1" or end["inline"] != "" or end["transition"] != "":
        problems.append(f"{where}: <main> is left at opacity {end['main']} with style opacity {end['inline']!r} and style transition {end['transition']!r}; the driver leaves nothing behind")
    if not end["focus"]:
        problems.append(f"{where}: focus does not move to the new heading")
    if end["marks"] != [path]:
        problems.append(f"{where}: the menu marks {end['marks']} as the current page, not {[path]}")
    if end["expanded"] != "false" or end["inert"]:
        problems.append(f"{where}: the menu is {'open' if end['expanded'] != 'false' else 'shut'} and the page {'inert' if end['inert'] else 'not inert'} after the change")
    return problems


def hold_font(page, files=FONT_FILES):
    """Hold the requests for `files` (FONT_FILES: the face How's headings are set in and About's are not), as a slow connection holds them; returns the list the
    requests are counted in and the function that lets them go. The check lets them go when the change is waiting for them (release_when_waited_for), from
    outside the page: a page that is busy, or waiting on this very font, cannot let it go itself."""
    asked = []
    held = []

    def hold(route):
        asked.append(route.request.url)
        held.append(route)

    def release():
        while held:
            held.pop().continue_()

    page.route(files, hold)
    return asked, release


def release_when_waited_for(page, release, over):
    """Let the held face go once the change is waiting for it, never after a fixed time. A change begins its wait for a face when the page has come and
    the old content has gone (from the open sheet, when its close has ended), which on a slow runner is later than any fixed hold from the tap: a face let go
    before the wait begins is loaded by then and the change waits for nothing, so a check of the wait has judged nothing. The wait is the timer of
    FONT_BOUND_MS that shell.js makes when a face is still loading and not otherwise (FONT_TIMER_PROBE, which the context must carry). `over` (a page
    expression, true when the change is done) lets it go for a change that finished without waiting, so that the check's own findings say what happened
    and the held face does not stall it for the bound. Polled on a timer, not on frames: a view transition pauses the page's drawing. Returns whether the
    change was seen waiting."""
    waited = wait_for_state(page, f"() => window.__fontTimers.length > 0 || ({over})", polling=10)
    release()
    return waited


def gave_up_on_face(page):
    """Whether a change's wait for a face ran out (the timer of FONT_BOUND_MS fired: FONT_TIMER_PROBE). It is the bound that spec 4.5 gives, not a fault: on a
    runner so slow that the released face did not arrive within it, the change goes on with the face still loading, and that is what it should do. A check of the
    face is then not judged (a line in review-by-eye.txt says so); a change that swapped with the face loading and no timer run out waited for less than the bound,
    and that is a problem."""
    return page.evaluate("() => window.__fontTimerFires.length > 0")


def face_not_loaded(problems, gave_up, where, message, name=FACE_NAME):
    """A face not loaded where it should be: a problem, unless the change's wait for it ran out (gave_up_on_face), when it is a note."""
    if gave_up:
        REVIEW_NOTES.append(f"nothing to judge: {where}: the wait for {name} ran out ({FONT_BOUND_MS} ms) before the released face arrived, as the bound says it may on a slow runner; "
                            f"not judged: {message}")
    else:
        problems.append(f"{where}: {message}")


def run_probe(page, probe, arguments, release=None, over=None, bound=SHEET_CHANGE_BOUND_MS + 4000):
    """Start a probe (a function that returns a promise) in the page without waiting for it (the probe's own clock starts at its call, which is the tap or the
    click), let the held font go when the change is waiting for it (release_when_waited_for, `over` says when the change is done), and wait for its result."""
    page.evaluate(f"(arguments) => {{ window.__probed = null; ({probe})(arguments).then((result) => {{ window.__probed = result; }}); }}", arguments)
    if release:
        release_when_waited_for(page, release, over)
    page.wait_for_function("() => window.__probed !== null", timeout=bound)
    return page.evaluate("() => window.__probed")


def sheet_change_case(context, base_url, label, source, href, old, new, stall, face=None):
    """One change from the open phone sheet in a context (any engine: Chromium or WebKit), at normal speed (stall 0) or with every frame held `stall` ms:
    open `source`, press MENU, wait for the sheet to be in place, tap the link to `href` from inside the page (SHEET_CHANGE_PROBE) and judge what the
    frames show (sheet_change_findings). With a `face`, its file is held until the change is waiting for it (hold_font, release_when_waited_for) and the findings
    add that it is loaded before the content is swapped in. Page errors are problems too. The caller made the context; its page is closed here."""
    context.add_init_script(VIEW_TRANSITION_CALLS)
    where = f"{label}, {source} to {href}{f' with every frame held {stall} ms' if stall else ''}{f', {FACE_NAME} held until the change waits for it' if face else ''}"
    if face:
        context.add_init_script(FONT_TIMER_PROBE)
    page, errors = open_page(context, base_url + source)
    asked, release = hold_font(page) if face else ([], lambda: None)
    page.locator("header [data-menu-toggle]").evaluate("(toggle) => toggle.click()")
    if not page.evaluate(SHEET_AT_REST, True):
        problems = [f"{where}: the sheet did not come to rest open within 3 s, so nothing was checked"]
    else:
        page.wait_for_timeout(200)
        result = run_probe(page, SHEET_CHANGE_PROBE, [href, stall, SHEET_CHANGE_BOUND_MS, [href, new, face]], release if face else None,
                           f"((document.querySelector('main h1') || {{}}).textContent || '').trim() === {json.dumps(new)}")
        problems = sheet_change_findings(where, result, old, new, href, face, gave_up_on_face(page) if face else False)
        if stall and result["waits"] < 3:
            problems.append(f"{where}: the wait that holds each frame ran only {result['waits']} times, so the slow frames were not tried")
        if face and not asked:
            problems.append(f"{where}: the face's file was never asked for, so there was nothing to hold")
    release()
    problems += [f"{where}: console error: {error}" for error in errors]
    page.close()
    return problems


# What a change from the open sheet leaves while its page is on its way: the sheet shut (its close has ended) and <main> faded out and held. Waited for, never
# slept for: the close takes as many frames as it takes, and on a slow runner (frames of 120 ms or more; the cloth runs on SwiftShader) that is far over a
# fixed 600 ms.
MAIN_HELD_BLANK = """() => getComputedStyle(document.querySelector('#shell-menu')).display === 'none' && getComputedStyle(document.querySelector('main')).opacity === '0'"""
# <main> at rest after a fade: full opacity and nothing of the driver's left on it (its inline opacity and transition).
MAIN_AT_REST = """() => { const main = document.querySelector('main');
  return getComputedStyle(main).opacity === '1' && main.style.opacity === '' && main.style.transition === ''; }"""
# <main> watched from inside the page while its ordinary load is held (the page cannot be asked anything then: an evaluate or a wait_for_function does not come
# back until the navigation has). Every 50 ms it reports (opacity, inline opacity, at rest) to window.reportMain, which the check exposes. It ends itself when
# `bound` ms have passed, the check clears it while the page can still be asked, and the load's own end takes it with the document, so nothing is left
# calling the check when its context closes.
MAIN_WATCH = """([bound]) => { const main = document.querySelector('main'), began = performance.now();
  window.__sampler = setInterval(() => { const rest = getComputedStyle(main).opacity === '1' && main.style.opacity === '' && main.style.transition === '';
    window.reportMain(getComputedStyle(main).opacity, main.style.opacity, rest);
    if (performance.now() - began > bound) clearInterval(window.__sampler); }, 50); }"""
# Two frames and a moment, for what a page that has been overtaken might still do after its answer came: a quiet period measured in the page's own frames.
SETTLED = "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(done, 200))))"
CHECK_WAIT_MS = 10000   # the longest a check waits for a state its page is on the way to (a bound, never a delay: it returns when the state is there)


def wait_for_state(page, state, timeout=CHECK_WAIT_MS, polling="raf"):
    """Wait, bounded, until `state` (a page function) is true. Returns whether it was, and never raises for the bound: the check says what it saw instead.
    `polling` is "raf" (every frame) or a number of ms (a timer, for a page whose drawing may be paused)."""
    try:
        page.wait_for_function(state, timeout=timeout, polling=polling)
        return True
    except PlaywrightError:
        return False


def wait_until(page, ready, timeout=CHECK_WAIT_MS):
    """Let the page's events and the check's own handlers (routes, listeners run while the check waits) go on until ready() is true, bounded. Returns ready()."""
    end = time.monotonic() + timeout / 1000
    while not ready() and time.monotonic() < end:
        page.wait_for_timeout(50)
    return ready()


# MENU pressed if it is shut, then a link of the sheet clicked, in one task: a change chosen from the open sheet, whenever the check wants it.
CHOOSE_FROM_SHEET = """(href) => { const toggle = document.querySelector('header [data-menu-toggle]');
  if (toggle.getAttribute('aria-expanded') !== 'true') toggle.click();
  document.querySelector(`#shell-menu a[href="${href}"]`).click(); }"""


# A link of the open sheet clicked, three frames later (the close has begun: the sheet is part way) MENU pressed again. Returns what the sheet was when MENU was
# pressed ({ value (--sheet), displayed }) and what MENU says after it.
CHOOSE_THEN_MENU = """(href) => new Promise((done) => {
  const toggle = document.querySelector('header [data-menu-toggle]'), sheet = document.querySelector('#shell-menu');
  if (toggle.getAttribute('aria-expanded') !== 'true') toggle.click();
  document.querySelector(`#shell-menu a[href="${href}"]`).click();
  let frames = 0;
  const frame = () => {
    if (++frames < 3) { requestAnimationFrame(frame); return; }
    const value = Number(getComputedStyle(sheet).getPropertyValue('--sheet')), displayed = getComputedStyle(sheet).display !== 'none';
    toggle.click();
    done({ value, displayed, expanded: toggle.getAttribute('aria-expanded') }); };
  requestAnimationFrame(frame); })"""
# What the page was when <main>'s content was swapped, read by a MutationObserver (a microtask, so after the swap's own synchronous part): the sheet's display and the
# root's overflow (the scroll lock), and the heading in <main>; null until it has been. A sheet that was shut before the swap reads `none` and `visible`; one that the
# swap itself begins to close (the `veilfile:swapped` that follows it) still reads `flex` and `hidden`.
SWAP_SEEN = """() => { const main = document.querySelector('main'); window.__swapSeen = null;
  new MutationObserver(() => { window.__swapSeen = window.__swapSeen || { lock: getComputedStyle(document.documentElement).overflow,
    sheet: getComputedStyle(document.querySelector('#shell-menu')).display, heading: (main.querySelector('h1') || {}).textContent }; }).observe(main, { childList: true }); }"""


def sheet_change_edge_problems(browser, base_url, session_key):
    """What a change chosen from the open phone sheet leaves as it was (spec 4.5), at 375x667 in Chromium, on the new path (no view transition):
    1. the newer change wins: How is chosen and its page held on the way, the sheet is opened again and Begin chosen, and when How's page comes at last
       it changes nothing: the page is Begin, with its own head and mark, one history entry was added, <main> is at full opacity with nothing of the
       driver's left on it, nothing reloaded and no view transition ran;
    2. a fetch that fails (aborted, a server error, a redirect away) is an ordinary page load, never a blank page: with the load held, <main> is
       not left faded out (it fades back in), and the page then loads whole;
    3. with Motion off the change is instant: no view transition, no fade (<main> at full opacity, untouched, in the task that swaps it), the sheet shut;
    4. MENU pressed again while the sheet is closing reopens it, and the change waits for it to be shut: with How's page held until the reopened sheet is at rest,
       the page comes and <main> is not swapped under the open sheet (the scroll lock is on for as long as it is open); when the visitor shuts it, the swap
       happens with the sheet shut and the lock gone, How fades in, the history gains one entry and focus is on the heading.
    The frames of a change that goes right are sheet_change_problems'."""
    problems = []
    page_of = SitePage("/about/", "sheet-edge")

    # 1. the newer change wins
    held = []
    context = new_context(browser, base_url, session_key, page_of, width=375, height=667, theme="light", motion="on")
    context.add_init_script(VIEW_TRANSITION_CALLS)
    context.route("**/how/", lambda route: held.append(route) if route.request.resource_type == "fetch" else route.continue_())
    page, errors = open_page(context, base_url + "/about/")
    page.evaluate("() => { window.stayedPut = true; }")
    entries = page.evaluate("() => history.length")
    label = "/about/ at 375px, How chosen from the sheet and Begin chosen from it again before How's page has come"
    answered = []   # How's page, once it has come
    page.on("requestfinished", lambda request: answered.append(request.url) if request.resource_type == "fetch" and urlparse(request.url).path == "/how/" else None)
    page.evaluate(CHOOSE_FROM_SHEET, "/how/")
    # The close has ended and How's page is still on its way: <main> waits blank. Waited for, not slept for (MAIN_HELD_BLANK).
    wait_for_state(page, MAIN_HELD_BLANK)
    if not wait_until(page, lambda: bool(held)):
        problems.append(f"{label}: How was never fetched, so nothing was checked")
    else:
        waiting = page.evaluate("() => [getComputedStyle(document.querySelector('main')).opacity, getComputedStyle(document.querySelector('#shell-menu')).display]")
        if waiting != ["0", "none"]:
            problems.append(f"{label}: while the page is on its way <main> and the sheet read {waiting}; <main> waits invisible and the sheet is shut")
        page.evaluate(CHOOSE_FROM_SHEET, "/begin/")
        try:
            page.wait_for_function("() => location.pathname === '/begin/' && document.querySelector('main h1').textContent.trim() === 'Begin' && "
                                   "getComputedStyle(document.querySelector('main')).opacity === '1'", timeout=CHECK_WAIT_MS)
            wait_for_state(page, MAIN_AT_REST)   # Begin has faded in, and the driver has let go of <main>
            for route in held:   # How's page comes at last
                route.continue_()
            wait_until(page, lambda: bool(answered))
            page.evaluate(SETTLED)   # and what it would change, if it changed anything, has had its frames
            state = page.evaluate("() => [location.pathname, document.querySelector('main h1').textContent.trim(), history.length, window.stayedPut === true, "
                                  "document.querySelector('main').style.opacity, document.querySelector('main').style.transition, window.__transitions.length]")
            if state[:2] != ["/begin/", "Begin"] or state[3] is not True:
                problems.append(f"{label}: the page ended on {state[0]} with {state[1]!r} (reloaded: {state[3] is not True}); the newer change, Begin, should win")
            if state[2] - entries != 1:
                problems.append(f"{label}: the history grew by {state[2] - entries} entries, not 1; the change that was overtaken should leave none")
            if state[4] != "" or state[5] != "" or state[6]:
                problems.append(f"{label}: <main> is left with style opacity {state[4]!r} and transition {state[5]!r}, and {state[6]} view transition(s) ran; none of either")
            problems += chapter_head_findings(page, label, "Begin | Veilfile", "https://veilfile.com/begin/", "Begin")
        except PlaywrightError:
            problems.append(f"{label}: Begin never came")
            for route in held:
                route.continue_()
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    # 2. a fetch that fails: an ordinary page load, and <main> is not left faded out while it is held
    allowed = {"abort": ("net::ERR_FAILED",), "500": ("status of 500",), "redirect": ()}
    for failure in ("abort", "500", "redirect"):
        loads, seen = [], []
        context = new_context(browser, base_url, session_key, page_of, width=375, height=667, theme="light", motion="on")

        def failing(route, failure=failure):
            if route.request.resource_type != "fetch":
                loads.append(route)   # the ordinary load waits until the check has looked at <main>
            elif failure == "abort":
                route.abort()
            elif failure == "500":
                route.fulfill(status=500, body="Server error")
            else:
                route.fulfill(status=302, headers={"Location": "/accounts/login/?next=/promises/"})

        context.route("**/promises/", failing)
        page, errors = open_page(context, base_url + "/about/")
        page.evaluate("() => { window.stayedPut = true; }")
        # The sampler (MAIN_WATCH): while the ordinary load is held the page cannot be asked what <main> reads.
        page.expose_function("reportMain", lambda opacity, inline, rest: seen.append((opacity, inline, rest)))
        page.evaluate(MAIN_WATCH, [CHECK_WAIT_MS])
        label = f"/about/ at 375px, Promises chosen from the sheet when the fetch fails ({failure})"
        page.evaluate(CHOOSE_FROM_SHEET, "/promises/")
        if not wait_until(page, lambda: bool(loads)):
            problems.append(f"{label}: should have fallen back to an ordinary page load")
            page.evaluate("() => clearInterval(window.__sampler)")
        else:
            held_at = len(seen)   # the load is held; what <main> reads from here on is what it was left as, and it fades back in in as many frames as it takes
            if not wait_until(page, lambda: len(seen) > held_at and seen[-1][2]):
                opacity, inline, _ = seen[-1] if seen else ("unknown", "unknown", False)
                problems.append(f"{label}: <main> is left at opacity {opacity} (inline {inline!r}) while the page loads; it fades back in")
        for route in loads:
            route.continue_()
        if loads:
            try:
                page.wait_for_url("**/promises/", timeout=8000)   # the ordinary load goes to the page itself, whatever the fetch was told
                if page.evaluate("() => window.stayedPut === true"):
                    problems.append(f"{label}: should have been an ordinary page load, but the page changed in place")
            except PlaywrightError:
                problems.append(f"{label}: the ordinary load never came")
        problems += [f"{label}: console error: {error}" for error in errors if not any(text in error for text in allowed[failure])]
        context.close()

    # 3. Motion off: instant
    context = new_context(browser, base_url, session_key, page_of, width=375, height=667, theme="light", motion="off")
    context.add_init_script(VIEW_TRANSITION_CALLS)
    page, errors = open_page(context, base_url + "/about/")
    label = "/about/ at 375px, How chosen from the sheet with Motion off"
    # What <main> was in the task that swapped its content (a MutationObserver reads it as a microtask of that task): instant means nothing of a fade, at the swap or
    # after it, however late the runner's frames are, so no time is measured.
    page.evaluate("() => { const main = document.querySelector('main'); window.__swapSeen = null; new MutationObserver(() => { window.__swapSeen = window.__swapSeen || "
                  "[getComputedStyle(main).opacity, main.style.opacity, main.style.transition, main.classList.contains('is-leaving')]; }).observe(main, { childList: true }); }")
    page.evaluate(CHOOSE_FROM_SHEET, "/how/")
    if not wait_for_state(page, "() => document.querySelector('main h1').textContent.trim() === 'The life of a file'"):
        problems.append(f"{label}: the new page never came")
    else:
        state = page.evaluate("() => [getComputedStyle(document.querySelector('main')).opacity, document.querySelector('main').style.opacity, window.__transitions.length, "
                              "getComputedStyle(document.querySelector('#shell-menu')).display, window.__swapSeen]")
        if state != ["1", "", 0, "none", ["1", "", "", False]]:
            problems.append(f"{label}: <main> opacity {state[0]!r} (style {state[1]!r}), {state[2]} view transition(s), the sheet {state[3]!r}, <main> at the swap {state[4]} "
                            "(opacity, style opacity, style transition, is-leaving); the change is instant, with none of them")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    # 4. MENU pressed again during the close: the change waits for the sheet to be shut
    held, answered = [], []
    context = new_context(browser, base_url, session_key, page_of, width=375, height=667, theme="light", motion="on")
    context.add_init_script(VIEW_TRANSITION_CALLS)
    context.route("**/how/", lambda route: held.append(route) if route.request.resource_type == "fetch" else route.continue_())
    page, errors = open_page(context, base_url + "/about/")
    page.on("requestfinished", lambda request: answered.append(request.url) if request.resource_type == "fetch" and urlparse(request.url).path == "/how/" else None)
    page.evaluate("() => { window.stayedPut = true; }")
    entries = page.evaluate("() => history.length")
    page.evaluate(SWAP_SEEN)
    label = "/about/ at 375px, How chosen from the sheet and MENU pressed again while the sheet is closing"
    page.evaluate(SHEET_AT_REST, True)   # MENU pressed and the sheet open at rest, so the close that follows is a whole one
    pressed = page.evaluate(CHOOSE_THEN_MENU, "/how/")
    if not (0 < pressed["value"] < 1 and pressed["displayed"] and pressed["expanded"] == "true"):
        problems.append(f"{label}: the sheet read {pressed['value']} (displayed {pressed['displayed']}) when MENU was pressed, and MENU says {pressed['expanded']!r} after it; "
                        "the sheet should be part way through its close and open again, so nothing was checked")
    elif not (page.evaluate(SHEET_AT_REST, True) and wait_for_state(page, "() => getComputedStyle(document.querySelector('main')).opacity === '0'")
              and wait_until(page, lambda: bool(held))):
        problems.append(f"{label}: the sheet did not come to rest open with <main> faded out and How's page asked for, so nothing was checked")
    else:
        while held:   # the page comes while the sheet is open
            held.pop().continue_()
        wait_until(page, lambda: bool(answered))
        page.evaluate(SETTLED)
        while_open = page.evaluate("() => [document.querySelector('main h1').textContent.trim(), document.querySelector('header [data-menu-toggle]').getAttribute('aria-expanded'), "
                                   "getComputedStyle(document.documentElement).overflow, window.__swapSeen, getComputedStyle(document.querySelector('main')).opacity]")
        if while_open[3] is not None:
            problems.append(f"{label}: <main> was swapped while the sheet was open again (the sheet {while_open[3]['sheet']!r}, the scroll lock {while_open[3]['lock']!r}, the heading "
                            f"{(while_open[3]['heading'] or '').strip()!r}); the swap waits for the sheet to be shut")
        elif while_open[1:3] != ["true", "hidden"]:
            problems.append(f"{label}: with the page come and the sheet open MENU says {while_open[1]!r} and the root's overflow is {while_open[2]!r}; the sheet stays open, with the scroll lock on")
        page.evaluate(SHEET_AT_REST, False)   # the visitor shuts it
        if not wait_for_state(page, "() => document.querySelector('main h1').textContent.trim() === 'The life of a file'"):
            problems.append(f"{label}: How never came once the sheet was shut again")
        else:
            wait_for_state(page, MAIN_AT_REST)
            state = page.evaluate("() => [window.__swapSeen, history.length, window.stayedPut === true, window.__transitions.length, document.querySelector('main').inert, "
                                  "document.activeElement === document.querySelector('main h1'), getComputedStyle(document.querySelector('#shell-menu')).display, "
                                  "getComputedStyle(document.documentElement).overflow]")
            seen = state[0] or {}
            if seen.get("sheet") != "none" or seen.get("lock") != "visible":
                problems.append(f"{label}: at the swap the sheet was {seen.get('sheet')!r} and the root's overflow {seen.get('lock')!r}; the swap "
                                "happens with the sheet shut and the scroll lock gone")
            if state[1] - entries != 1 or state[2] is not True or state[3]:
                problems.append(f"{label}: the history grew by {state[1] - entries} entries, never reloaded {state[2]}, {state[3]} view transition(s); one entry, in place, none")
            if state[4] or not state[5] or state[6] != "none" or state[7] != "visible":
                problems.append(f"{label}: at the end <main> inert {state[4]}, focus on the heading {state[5]}, the sheet {state[6]!r}, overflow {state[7]!r}; reachable, focused, shut and unlocked")
    while held:   # whatever ended the check, the page it asked for is let go before the context closes
        held.pop().continue_()
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    return problems


# Where a stray tap on the old page would land: the centre of the first link of <main> that changes the page in place (not a #fragment) and lies on the first
# screen with nothing over it.
OLD_LINK_POINT = """() => { const paths = (document.body.dataset.layoutPaths || '').split(' ');
  for (const link of document.querySelectorAll('main a[href]')) {
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || url.hash || !paths.includes(url.pathname)) continue;
    const box = link.getBoundingClientRect(), x = box.left + box.width / 2, y = box.top + box.height / 2;
    const hit = y > 0 && y < innerHeight && x > 0 && x < innerWidth ? document.elementFromPoint(x, y) : null;
    if (hit && link.contains(hit)) return { x, y, href: url.pathname };
  }
  return null; }"""
# Where the keyboard is and where <main> is, for the checks of what can be reached: whether focus is in <main> (and on what), <main>'s opacity and inertness.
FOCUS_AND_MAIN = """() => { const main = document.querySelector('main'), at = document.activeElement;
  return { inMain: main.contains(at) && at !== main, on: `${at.tagName} ${(at.textContent || '').trim().slice(0, 30)}`, opacity: getComputedStyle(main).opacity,
           inert: main.inert, path: location.pathname }; }"""
REACH_TABS = 14   # presses of Tab: the skip link, the mark, MENU and the corners come before and after <main>'s links, and the old page has several links


def sheet_change_reach_problems(browser, base_url, session_key):
    """The old page cannot be reached while a change chosen from the open phone sheet waits for its page (spec 4.5). Once the sheet has closed <main> is held
    at opacity 0 for as long as the fetch takes (up to 8 s) and the font wait: invisible, so it must also be out of reach, as <main> was when the sheet
    stayed open. At 375x667 on /docs/ (a page of links), with the Journal's page held on its way and the close long over:
    1. Tab never moves focus into <main>: 14 presses, and focus is never on a link of the old page;
    2. MENU opened and shut again leaves <main> out of reach (the sheet's own inertness must not give the old page back when it shuts);
    3. a tap on a link of the old page does nothing: <main> stays at opacity 0, the address stays, and no page is fetched for that link (a tap that reaches it
       starts a change that was not chosen: it snaps the old page back to full strength and cross-fades to a destination the visitor did not choose);
    4. when the page comes at last the change is the one chosen, <main> is reachable again and focus is on the new heading."""
    problems = []
    source, href = "/docs/", "/blog/"
    label = f"{source} at 375px, {href} chosen from the sheet and its page held on the way"
    held, asked = [], []
    context = new_context(browser, base_url, session_key, SitePage(source, "sheet-reach"), width=375, height=667, theme="light", motion="on")
    context.add_init_script(VIEW_TRANSITION_CALLS)
    context.route(f"**{href}", lambda route: held.append(route) if route.request.resource_type == "fetch" else route.continue_())
    page, errors = open_page(context, base_url + source)
    page.on("request", lambda request: asked.append(request.url) if request.resource_type == "fetch" else None)
    point = page.evaluate(OLD_LINK_POINT)
    if point is None:
        problems.append(f"{label}: no link of the old page lies on its first screen with nothing over it, so a stray tap says nothing about it")
    else:
        page.evaluate("() => { window.stayedPut = true; }")
        page.evaluate(CHOOSE_FROM_SHEET, href)
        wait_for_state(page, MAIN_HELD_BLANK)   # the close has ended and the page is still on its way: <main> waits invisible (waited for, not slept for)
        wait_until(page, lambda: bool(held))
        state = page.evaluate(FOCUS_AND_MAIN)
        if not held:
            problems.append(f"{label}: {href} was never fetched, so nothing was checked")
        elif state["opacity"] != "0":
            problems.append(f"{label}: <main> reads opacity {state['opacity']} while the page is on its way, not 0, so nothing was checked")
        else:
            page.evaluate("() => document.activeElement && document.activeElement.blur()")
            reached = []
            for _ in range(REACH_TABS):
                page.keyboard.press("Tab")
                now = page.evaluate(FOCUS_AND_MAIN)
                if now["inMain"]:
                    reached.append(now["on"])
            if reached:
                problems.append(f"{label}: Tab moved focus into the invisible old page {len(reached)} time(s) in {REACH_TABS} presses, onto {reached[:3]}; <main> is inert until the swap")
            if not (page.evaluate(SHEET_AT_REST, True) and page.evaluate(SHEET_AT_REST, False)):   # MENU opened, and shut again, each at rest
                problems.append(f"{label}: the sheet did not come to rest open and then shut within 3 s each, so the inertness after it was not read")
            now = page.evaluate(FOCUS_AND_MAIN)
            if not now["inert"]:
                problems.append(f"{label}: after MENU was opened and shut again the invisible old page is not inert; the sheet's shutting must not give it back")
            page.evaluate("() => window.scrollTo(0, 0)")   # Tab scrolled the page to the links it reached: the point is read again
            point = page.evaluate(OLD_LINK_POINT) or point
            before = len(asked)
            page.mouse.click(point["x"], point["y"])
            page.wait_for_timeout(500)
            now = page.evaluate(FOCUS_AND_MAIN)
            fetched = [url for url in asked[before:] if urlparse(url).path == point["href"]]
            if fetched or now["path"] != source or now["opacity"] != "0":
                problems.append(f"{label}: a tap at ({point['x']:.0f}, {point['y']:.0f}), on the old page's link to {point['href']}, reached it: the address is {now['path']}, <main> reads "
                                f"opacity {now['opacity']} (0 while the page is on its way) and {len(fetched)} page(s) were fetched for that link; the tap changes nothing")
            for route in held:
                route.continue_()
            try:
                page.wait_for_function(f"() => location.pathname === {href!r} && document.querySelector('main h1').textContent.trim() === 'Journal' && "
                                       "getComputedStyle(document.querySelector('main')).opacity === '1'", timeout=8000)
                wait_for_state(page, MAIN_AT_REST)   # the new page has faded in
                end = page.evaluate("() => [document.querySelector('main').inert, document.activeElement === document.querySelector('main h1'), window.stayedPut === true]")
                if end != [False, True, True]:
                    problems.append(f"{label}: when the page came <main> inert {end[0]}, focus on the heading {end[1]}, never reloaded {end[2]}; reachable, focused and in place")
            except PlaywrightError:
                now = page.evaluate(FOCUS_AND_MAIN)
                problems.append(f"{label}: the chosen page ({href}) never came; the page ends on {now['path']} with <main> at opacity {now['opacity']}")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    return problems


def sheet_change_problems(browser, base_url, session_key):
    """A change chosen from the open phone sheet fades and snaps nothing (spec 4.4, 4.5), in Chromium at 375x667: on About to How and on Terms to
    Privacy, the two tallest reading pages, frame by frame from the tap to the end (sheet_change_findings), at normal speed and with every frame held
    120 ms (SLOW_EVERY_FRAME_MS, a model of CI's WebKit at a pixel ratio of 3 and of a phone drawing the cloth): the sheet falls through frames in between, the
    old content never shows at full strength after the tap, the new content fades in through frames in between, no view transition starts, and the scroll
    lock is gone before the swap. webkit_sheet_problems repeats it in WebKit. The page runs without the cloth (VEIL_STUB); cloth_sheet_ink_problems reads the
    shader's share of a change from the sheet."""
    problems = []
    for source, href, old, new in SHEET_CHANGES:
        for stall in (0, SLOW_EVERY_FRAME_MS):
            context = new_context(browser, base_url, session_key, SitePage(source, "sheet-change"), width=375, height=667, theme="light", motion="on")
            problems += sheet_change_case(context, base_url, "Chromium at 375px", source, href, old, new, stall)
            context.close()
    return problems


# A change in place that cross-fades (the column menu at 1280, a link in the page), read from inside the page: every view transition's update callback is timed
# (when it began, when its promise settled), and the face and the new heading are read when the callback began and when the transition's animation could
# (ready). `want` is [the new page's heading, a font for document.fonts.check]. An engine with no view transitions is left as it is (the wrapper is not installed, or the
# page would take a function for the engine's support): the result says `supported: false` after a moment, and the change fades out and in as it does there.
FONT_TRANSITION_PROBE = """([href, want]) => new Promise((done) => {
  const [heading, face] = want, t0 = performance.now(), calls = [];
  FACE_LOADED_JS
  const start = Document.prototype.startViewTransition;
  if (start) Document.prototype.startViewTransition = function (callback) {
    const call = { at: performance.now() - t0, updated: null, settled: null, ready: null, faceAtUpdate: null, faceAtReady: null, headingAtUpdate: null };
    calls.push(call);
    const transition = start.call(this, () => {
      call.updated = performance.now() - t0; call.faceAtUpdate = faceLoaded(face);
      return Promise.resolve(callback()).finally(() => { call.settled = performance.now() - t0; }); });
    transition.ready.then(() => { call.ready = performance.now() - t0; call.faceAtReady = faceLoaded(face);
      call.headingAtReady = (document.querySelector('main h1') || {}).textContent; }, () => {});
    return transition; };
  const link = document.querySelector(`#shell-menu a[href="${href}"]`);
  link.click();
  if (!start) { setTimeout(() => done({ calls, supported: false, now: performance.now() - t0 }), 500); return; }
  const poll = setInterval(() => {
    const call = calls[0];
    if ((call && call.ready !== null && call.settled !== null) || performance.now() - t0 > 6000) { clearInterval(poll); setTimeout(() => done({ calls, supported: true, now: performance.now() - t0 }), 100); } }, 10);
})"""


FONT_TRANSITION_PROBE = FONT_TRANSITION_PROBE.replace("FACE_LOADED_JS", FACE_LOADED)


def font_transition_findings(where, result, heading, gave_up=False):
    """A change that cross-fades does not wait for a font inside the transition's update callback and does not start to show the new content before
    the face is there. The face is asked for with the click (and the menu's opening), so by the time the page has come and the transition is called the
    face is loaded, or the change has waited for it before starting, while the page was still drawing. Judged from FONT_TRANSITION_PROBE: the callback
    began with the face loaded, took no longer than FONT_CALLBACK_MS to settle, and the new heading showed with the face loaded when the animation began.
    Where the engine has no view transitions there is nothing to judge: a note in review-by-eye.txt, no problem. Where it has them and none ran, a problem."""
    if not result["calls"]:
        if not result["supported"]:   # an engine without view transitions has no update callback to time: said, not failed (CI's Linux WebKit has only ever run the sheet checks)
            REVIEW_NOTES.append(f"nothing to measure: {where}: this engine has no view transitions (document.startViewTransition is missing), so the change fades out and in and "
                                "there is no update callback to time")
            return []
        return [f"{where}: no view transition ran, so nothing was checked"]
    call = result["calls"][0]
    if call["ready"] is None or call["settled"] is None:
        return [f"{where}: the view transition never began ({call})"]
    problems = []
    waited = call["settled"] - call["updated"]
    seen = f"(click to call {call['at']:.0f} ms, the callback {call['updated']:.0f} to {call['settled']:.0f} ms, the animation began {call['ready']:.0f} ms)"
    if not call["faceAtUpdate"]:
        face_not_loaded(problems, gave_up, where, f"the face {FACE_NAME} was not loaded when the transition's update callback began; the faces a page uses load before any change "
                                                   f"swaps it in {seen}")
    if waited > FONT_CALLBACK_MS:
        problems.append(f"{where}: the update callback took {waited:.0f} ms to settle; it waited for a font while the page's drawing was paused (the cloth stands still), "
                        f"where the wait belongs before the transition begins, with the page still drawing {seen}")
    if not call["faceAtReady"]:
        face_not_loaded(problems, gave_up, where, f"the new headings began to show with {FACE_NAME} still loading, in a fallback face {seen}")
    if call.get("headingAtReady") != heading:
        problems.append(f"{where}: the new heading {call.get('headingAtReady')!r} is not {heading!r} when the animation begins {seen}")
    return problems


def font_transition_case(context, base_url, label, source, href, heading):
    """One in-place change that cross-fades (the column menu's link, at 1280: a view transition where the engine has them) with the face held: see
    font_transition_findings. The caller made the context; its page is closed here."""
    where = f"{label}, {source} to {href}, {FACE_NAME} held until the change waits for it"
    context.add_init_script(FONT_TIMER_PROBE)
    page, errors = open_page(context, base_url + source)
    asked, release = hold_font(page)
    page.locator("header [data-menu-toggle]").evaluate("(toggle) => toggle.click()")
    page.wait_for_timeout(300)
    result = run_probe(page, FONT_TRANSITION_PROBE, [href, [heading, FONT_FACE]], release,
                       f"((document.querySelector('main h1') || {{}}).textContent || '').trim() === {json.dumps(heading)}")
    problems = font_transition_findings(where, result, heading, gave_up_on_face(page))
    if not asked:
        problems.append(f"{where}: the face's file was never asked for, so there was nothing to hold")
    release()
    problems += [f"{where}: console error: {error}" for error in errors]
    page.close()
    return problems


# Every timer of FONT_BOUND_MS exactly (a script's own wait for a face still loading: shell.js' FONT_WAIT_MS; nothing else in the shell's scripts waits 400 ms), counted
# from the page's first script, as the times they were made at.
FONT_TIMER_PROBE = ("(() => { window.__fontTimers = []; window.__fontTimerFires = []; const set = window.setTimeout; window.setTimeout = function (callback, delay, ...rest) { "
                    f"if (delay === {FONT_BOUND_MS}) {{ window.__fontTimers.push(performance.now()); const wait = callback; "
                    "callback = (...args) => { window.__fontTimerFires.push(performance.now()); return typeof wait === 'function' ? wait(...args) : undefined; }; } "
                    "return set.call(this, callback, delay, ...rest); }; })();")
# Every face the layout names (<body data-faces>) asked for and waited for, as shell.js does at the menu's opening: nothing is loading after it.
LAYOUT_FACES_LOADED = """() => Promise.all((document.body.dataset.faces || '').split('|').filter(Boolean).map((face) => document.fonts.load(face)))
  .then(() => document.fonts.ready).then(() => document.fonts.status)"""


PAGE_LATE_MS = 400   # how long the destination page is held after the tap in the "late" cases: the wait for a face begins when the page has come, so a slow runner or a slow connection begins it late


def font_timer_case(context, base_url, label, source, href, heading, held, late=False):
    """The bound on a face still loading is a timer, made only when a face is still loading (spec 4.5, shell.js' `bounded`): a change that finds every face loaded makes
    none (`held` false: the layout's faces are asked for and waited for before the change), one that finds a face still loading makes exactly one (`held`: the file of
    Cormorant italic 400, which About does not load, is held until the change is waiting for it: the timer is that wait, and the face comes when it is made; `late`: the
    page is held PAGE_LATE_MS after the tap, as a slow runner's frames or a slow connection hold it, so that the wait begins late), and in neither does a second wait make a second. The link is chosen from the
    menu as the context's width has it: the open sheet below 64rem, the column above. The caller made the context (FONT_TIMER_PROBE is added here); its page is closed here."""
    context.add_init_script(FONT_TIMER_PROBE)
    where = f"{label}, {source} to {href}{', ' + FACE_NAME + ' held until the change waits for it' if held else ', every face loaded first'}{f', the page held {PAGE_LATE_MS} ms' if late else ''}"
    page, errors = open_page(context, base_url + source)
    asked, release = hold_font(page) if held else ([], lambda: None)
    pages = []
    if late:
        page.route(f"**{href}", lambda route: pages.append(route) if route.request.resource_type == "fetch" else route.continue_())
    page.locator("header [data-menu-toggle]").evaluate("(toggle) => toggle.click()")
    if not held:
        page.evaluate(LAYOUT_FACES_LOADED)
    page.wait_for_timeout(200)
    page.evaluate("(href) => document.querySelector(`#shell-menu a[href=\"${href}\"]`).click()", href)
    if late:
        page.wait_for_timeout(PAGE_LATE_MS)
        while pages:
            pages.pop().continue_()
    if held:
        release_when_waited_for(page, release, f"((document.querySelector('main h1') || {{}}).textContent || '').trim() === {json.dumps(heading)}")
    problems = []
    if not wait_for_state(page, f"() => document.querySelector('main h1') && document.querySelector('main h1').textContent.trim() === {heading!r} && "
                                "getComputedStyle(document.querySelector('main')).opacity === '1' && document.querySelector('main').style.opacity === ''"):
        problems.append(f"{where}: the new page never showed, so nothing was checked")
    else:
        page.evaluate(SETTLED)
        made = page.evaluate("() => window.__fontTimers.length")
        wanted = 1 if held else 0
        if made != wanted:
            problems.append(f"{where}: the change made {made} timer(s) of {FONT_BOUND_MS} ms (the bound on a face still loading); it makes {wanted}: "
                            + ("one for the face it waits for, and not a second for the swap's own wait" if held else "none, since no face is loading, so nothing is waited for"))
    if held and not asked:
        problems.append(f"{where}: the face's file was never asked for, so there was nothing to hold")
    release()
    problems += [f"{where}: console error: {error}" for error in errors]
    page.close()
    return problems


def font_timer_problems(new_context_for, base_url, engine):
    """font_timer_case for a change from the column at 1280 (a view transition where there is one), from the open sheet at 375 (none), each with every face loaded first
    (no timer) and, from About to How, with the one face About lacks held (one timer). `new_context_for(width, height)` makes a context in the engine under test."""
    problems = []
    for width, height, label in ((1280, 800, f"{engine} at 1280px"), (375, 667, f"{engine} at 375px from the sheet")):
        for source, href, heading, held, late in (("/how/", "/begin/", "Begin", False, False), ("/about/", "/how/", "The life of a file", True, False),
                                                  ("/about/", "/how/", "The life of a file", True, True)):
            context = new_context_for(width, height)
            problems += font_timer_case(context, base_url, label, source, href, heading, held, late)
            context.close()
    return problems


def font_change_problems(browser, base_url, session_key):
    """No change waits on a font (spec 4.5), in Chromium: from About to How, the first page that sets its headings in a face About never loads, with that face's
    file held until the change waits for it (release_when_waited_for: a runner so slow that the sheet's close ends after any fixed hold would find the face
    arrived and wait for nothing). From the open phone sheet (375x667), at normal speed and with every frame held, the face is loaded before the content is swapped in and whenever it shows
    (sheet_change_findings with a face); at 1280, where the column's link cross-fades, the transition's update callback does not wait (it takes no longer
    than FONT_CALLBACK_MS, the page's drawing is paused while it runs) and the face is loaded when the callback begins and when the new content first shows
    (font_transition_findings). webkit_sheet_problems repeats both in WebKit, where the old wait did nothing and the headings showed in a fallback face."""
    problems = []
    source, href, old, new = SHEET_CHANGES[0]
    for stall in (0, SLOW_EVERY_FRAME_MS):   # at normal speed, and with every frame held, as a slow runner's are: the wait for the face begins late there
        context = new_context(browser, base_url, session_key, SitePage(source, "font-change"), width=375, height=667, theme="light", motion="on")
        problems += sheet_change_case(context, base_url, "Chromium at 375px", source, href, old, new, stall, FONT_FACE)
        context.close()
    context = new_context(browser, base_url, session_key, SitePage(source, "font-change"), width=1280, height=800, theme="light", motion="on")
    problems += font_transition_case(context, base_url, "Chromium at 1280px", source, href, new)
    context.close()
    problems += font_timer_problems(lambda width, height: new_context(browser, base_url, session_key, SitePage(source, "font-timer"), width=width, height=height,
                                                                      theme="light", motion="on"), base_url, "Chromium")
    return problems


WEBKIT_MISSING = "WebKit is not installed for Playwright here (python -m playwright install webkit): the phone menu sheet's WebKit checks were skipped"


def webkit_sheet_problems(playwright, base_url):
    """The phone menu sheet in WebKit, the engine of every browser on an iPhone, emulating Playwright's iPhone 13 (a touchscreen, a phone
    screen, at a pixel ratio of 1): MENU opens the sheet and it fades in and out over frames, not in a blink (sheet_fade_problems, the interruptions
    as well), also when its first frame is slow and when every frame is (sheet_slow_first_frame_problems, sheet_slow_frames_problems: WebKit starts
    a CSS fade's clock at the style change and a late frame finds it over, so the fade is shell.js' one value stepped per drawn frame); a tap on the
    sheet's empty left half, which WebKit fires no click for unless the sheet has a click listener of its own, closes it; so does Escape.
    Safari's own build is not what runs here: Playwright's WebKit is the engine's source, built for Playwright. Skipped, with a line saying so, where it is not
    installed. The page runs without the cloth (VEIL_STUB), as the other page checks do."""
    try:
        browser = playwright.webkit.launch()
    except PlaywrightError as error:
        first = error.message.splitlines()[0] if error.message else repr(error)
        if "Executable doesn't exist" in error.message:
            print(WEBKIT_MISSING)
            return []
        return [f"the phone menu sheet in WebKit: WebKit would not start: {first}"]
    problems = []
    started = time.perf_counter()
    begin = page_named("/begin/")
    # The iPhone 13's viewport, touch and user agent, at a pixel ratio of 1 and not its 3: what is under test (a fade, a tap, a key) does not
    # depend on the density, and a full-screen masked layer at 3x in software compositing is what made CI's frames 100 to 150 ms apart.
    device = {**playwright.devices["iPhone 13"], "device_scale_factor": 1}
    for motion, what in (("on", "Motion on"), ("off", "Motion off")):
        label = f"/begin/ in WebKit as an iPhone 13, {what}"
        context = browser.new_context(**device, reduced_motion="no-preference")
        context.add_init_script(seed_storage("light", motion))
        context.route(VEIL_SCRIPT, lambda route: route.fulfill(status=200, content_type="text/javascript", body=VEIL_STUB))
        shut_after_routes(context)
        page, errors = open_page(context, base_url + begin.path)
        if motion == "on":
            problems += sheet_fade_problems(page, label)
            for when, _ in SLOW_FIRST_FRAMES:
                slow = open_page(context, base_url + begin.path)   # a page of its own: the probe's observer stays on the sheet it ran on
                problems += sheet_slow_first_frame_problems(slow[0], label, when)
                problems += [f"{label}: console error: {error}" for error in slow[1]]
            for mode in ("open", "close"):
                slow = open_page(context, base_url + begin.path)
                problems += sheet_slow_frames_problems(slow[0], label, mode)
                problems += [f"{label}: console error: {error}" for error in slow[1]]
        else:
            toggle = page.locator("header [data-menu-toggle]")
            shut = lambda: toggle.get_attribute("aria-expanded") != "true" and not page.locator("#shell-menu").is_visible()
            middle = page.viewport_size["height"] / 2
            page.evaluate("() => { window.__clicks = []; document.addEventListener('click', (event) => window.__clicks.push(event.target.id || event.target.tagName), true); }")
            toggle.tap()
            if shut():
                problems.append(f"{label}: a tap on MENU did not open the sheet, so nothing was checked")
            else:
                if page.evaluate("([x, y]) => document.elementFromPoint(x, y).id", [40, middle]) != "shell-menu":
                    problems.append(f"{label}: the point 40px in, mid-height, is not on the sheet, so a tap there says nothing about it")
                page.touchscreen.tap(40, middle)
                page.wait_for_timeout(200)
                if not shut():
                    problems.append(f"{label}: a tap on the left half of the open sheet, 40px in at mid-height, does not close it (the clicks the page heard: "
                                    f"{page.evaluate('() => window.__clicks')}); WebKit fires none for an element with no click listener of its own")
                toggle.tap()
                page.keyboard.press("Escape")
                page.wait_for_timeout(200)
                if not shut():
                    problems.append(f"{label}: Escape does not close the open sheet")
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()
    print(f"phone: webkit_sheet_problems: the sheet's fade, a tap and Escape took {time.perf_counter() - started:.0f} s")
    started = time.perf_counter()
    for source, href, old, new in SHEET_CHANGES:   # a change chosen from the open sheet, as sheet_change_problems has it in Chromium
        for stall in (0, SLOW_EVERY_FRAME_MS):
            context = browser.new_context(**device, reduced_motion="no-preference")
            context.add_init_script(seed_storage("light", "on"))
            context.route(VEIL_SCRIPT, lambda route: route.fulfill(status=200, content_type="text/javascript", body=VEIL_STUB))
            shut_after_routes(context)
            problems += sheet_change_case(context, base_url, "WebKit as an iPhone 13", source, href, old, new, stall)
            context.close()
    print(f"phone: webkit_sheet_problems: the changes from the sheet took {time.perf_counter() - started:.0f} s")
    started = time.perf_counter()
    source, href, old, new = SHEET_CHANGES[0]   # the face that About does not load, held: the sheet's change, and a cross-fade at a desktop's width
    for stall in (0, SLOW_EVERY_FRAME_MS):
        context = browser.new_context(**device, reduced_motion="no-preference")
        context.add_init_script(seed_storage("light", "on"))
        context.route(VEIL_SCRIPT, lambda route: route.fulfill(status=200, content_type="text/javascript", body=VEIL_STUB))
        shut_after_routes(context)
        problems += sheet_change_case(context, base_url, "WebKit as an iPhone 13", source, href, old, new, stall, FONT_FACE)
        context.close()
    context = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="no-preference")
    context.add_init_script(seed_storage("light", "on"))
    context.route(VEIL_SCRIPT, lambda route: route.fulfill(status=200, content_type="text/javascript", body=VEIL_STUB))
    shut_after_routes(context)
    problems += font_transition_case(context, base_url, "WebKit at 1280px", source, href, new)
    context.close()

    def webkit_context(width, height):   # a desktop's viewport, or the iPhone 13 (390 wide) for the sheet
        context = browser.new_context(**device, reduced_motion="no-preference") if width < 1000 else \
            browser.new_context(viewport={"width": width, "height": height}, reduced_motion="no-preference")
        context.add_init_script(seed_storage("light", "on"))
        context.route(VEIL_SCRIPT, lambda route: route.fulfill(status=200, content_type="text/javascript", body=VEIL_STUB))
        shut_after_routes(context)
        return context

    problems += font_timer_problems(webkit_context, base_url, "WebKit")
    print(f"phone: webkit_sheet_problems: the faces took {time.perf_counter() - started:.0f} s")
    browser.close()
    return problems


def sheet_scrolled_close_problems(browser, base_url, session_key):
    """The sheet's surface stays where it is while a scrolled sheet fades out (spec 4.4). On a phone turned sideways (667x375) the links
    reach past the sheet's bottom, so it scrolls; the 6px its links rise as it closes (0.7 s) must not be a transform of the sheet itself,
    which would make it the containing block of its position: fixed surface, take the surface off the screen and scroll it away with the links for
    the fade, leaving the page bare behind them. With the sheet scrolled to its end and its --sheet pinned half way on the sheet (as a close half
    through has it: the driver's value is the root's, the sheet's own declaration holds it still, which a fade timed by CSS could be held by pausing
    its animations), a black layer under it stands for what lies behind: the right half, solid, shows the paper at the sheet's own opacity over
    that black, at the top, the middle and the bottom, and the first group of links is 3px up. A surface that has scrolled away leaves the black
    bare where it was."""
    problems = []
    begin = page_named("/begin/")
    label = "/begin/ at 667x375, the menu sheet scrolled to its end and half closed"
    context = new_context(browser, base_url, session_key, begin, width=667, height=375, theme="light", motion="on")
    page, errors = open_page(context, base_url + begin.path)
    page.locator("header [data-menu-toggle]").click()
    page.wait_for_function(SHEET_IN_PLACE, timeout=5000)
    scrolled = page.evaluate("() => { const sheet = document.querySelector('#shell-menu'); sheet.scrollTop = sheet.scrollHeight; return sheet.scrollTop; }")
    if scrolled < 40:
        problems.append(f"{label}: the sheet scrolls only {scrolled:.0f}px, so a surface that scrolls away would not show; nothing was checked")
    else:
        paper = gamma_luma(page.evaluate(TOKEN, "--paper"))
        page.add_style_tag(content="#shell-menu a { visibility: hidden !important; }")  # only the surface is looked at
        page.evaluate("""() => { const behind = document.createElement('div');
            behind.style.cssText = 'position: fixed; inset: 0; z-index: 1; background: #000; pointer-events: none;'; document.body.append(behind); }""")
        held = page.evaluate("""() => { const sheet = document.querySelector('#shell-menu'); [sheet, ...sheet.children].forEach((reader) => reader.style.setProperty('--sheet', '0.5'));
            return { opacity: Number(getComputedStyle(sheet).opacity), scrolled: sheet.scrollTop, transform: getComputedStyle(sheet).transform,
                     lift: new DOMMatrix(getComputedStyle(sheet.querySelector('.menu__group')).transform).m42 }; }""")
        if abs(held["opacity"] - 0.5) > 0.01 or held["scrolled"] < 40 or abs(held["lift"] + 3) > 0.01:
            problems.append(f"{label}: the close was not held half way (opacity {held['opacity']}, links {held['lift']}px from their place, "
                            f"scrolled {held['scrolled']:.0f}px), so nothing was checked")
        else:
            want = held["opacity"] * paper
            for where, y in (("top", 70), ("middle", 170), ("bottom", 330)):
                have, _ = png_luma(page.screenshot(clip={"x": 667 * 0.6, "y": y, "width": 667 * 0.3, "height": 20}))
                if abs(have - want) > 0.05:
                    problems.append(f"{label}: the right half of the sheet at the {where} shows {have:.3f} of luma where its surface over the black behind it should show {want:.3f} "
                                    f"(the paper {paper:.3f} at the sheet's opacity {held['opacity']:.2f}); the surface has scrolled away with the sheet's {held['scrolled']:.0f}px "
                                    f"(the sheet's transform is {held['transform']})")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    return problems


# Each code block of the page (components/code_block.html): its text as a reader sees it, the button's name and word, and where the button
# lies against the code and the block (a button over the code, or out of its block, is a problem). `scrolls`: the code is wider than its box.
CODE_BLOCKS = """() => [...document.querySelectorAll('.code-block')].map((block) => {
  const pre = block.querySelector('pre'), button = block.querySelector('[data-copy]'), status = block.querySelector('[data-copy-status]');
  const box = block.getBoundingClientRect(), code = pre.getBoundingClientRect(), copy = button ? button.getBoundingClientRect() : null;
  const within = (inner, outer) => inner.left >= outer.left - 0.5 && inner.right <= outer.right + 0.5 && inner.top >= outer.top - 0.5 && inner.bottom <= outer.bottom + 0.5;
  return { id: pre.id, text: pre.innerText.trim(), tab: pre.tabIndex, scrolls: pre.scrollWidth > pre.clientWidth, status: status ? status.textContent : null,
           name: button && button.getAttribute('aria-label'), word: button && button.textContent.trim(),
           shown: Boolean(button && button.getClientRects().length), copy: copy && { x: copy.x, y: copy.y, width: copy.width, height: copy.height },
           code: { x: code.x, y: code.y, width: code.width, height: code.height },
           over: Boolean(copy && code.left < copy.right - 0.5 && copy.left < code.right - 0.5 && code.top < copy.bottom - 0.5 && copy.top < code.bottom - 0.5),
           buttonInBlock: Boolean(copy && within(copy, box)), codeInBlock: within(code, box), inViewport: Boolean(copy && copy.left >= 0 && copy.right <= innerWidth) }; })"""


def code_block_problems(browser, base_url, session_key):
    """The code blocks on Begin (five, components/code_block.html): each is a focusable scroll box with a Copy button that never lies over its
    code or leaves its block, and the page does not scroll sideways, at 320, 375 and 1280 wide (the longest line scrolls inside its box).
    Copy puts exactly its block's text on the clipboard (as a reader sees it, line breaks and indentation included), says Copied, and tells
    screen readers in its own block's status and no other's (spoken once), the box and the button staying where they were; with the clipboard
    refused it selects the code and says Selected. Without JavaScript Copy is not shown and the block is its code alone. axe passes with
    Copied showing."""
    problems = []
    begin = page_named("/begin/")
    for width in (320, 375, 1280):
        context = new_context(browser, base_url, session_key, begin, width=width, theme="light", motion="off")
        page, errors = open_page(context, base_url + begin.path)
        label = f"/begin/ at {width}px, the code blocks"
        blocks = page.evaluate(CODE_BLOCKS)
        if len(blocks) != 5:
            problems.append(f"{label}: Begin has {len(blocks)} code blocks; it should have its five snippets, so nothing was checked")
            context.close()
            continue
        for block in blocks:
            where = f"{label}, {block['id']}"
            if not block["shown"] or block["word"] != "Copy" or not (block["name"] or "").startswith("Copy "):
                problems.append(f"{where}: the Copy button is shown: {block['shown']}, says {block['word']!r}, is named {block['name']!r}; it should say Copy and be named Copy followed by what it copies")
            if block["tab"] != 0:
                problems.append(f"{where}: the code is not a keyboard-focusable scroll box (tabindex {block['tab']})")
            if block["over"]:
                problems.append(f"{where}: the Copy button lies over the code ({block['copy']} against {block['code']})")
            if not block["buttonInBlock"] or not block["codeInBlock"] or not block["inViewport"]:
                problems.append(f"{where}: the Copy button or the code leaves its block or the screen ({block['copy']}, {block['code']})")
        if width == 320 and not any(block["scrolls"] for block in blocks):
            problems.append(f"{label}: no snippet is wider than its box at 320px, so the sideways scroll of a long line was not tried")
        problems += sideways_problems(page, label, " with the Copy buttons")
        if width == 1280:
            problems += code_block_copy_problems(page, label, blocks)
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()

    label = "/begin/ with the clipboard refused"
    context = new_context(browser, base_url, session_key, begin, width=1280, theme="light", motion="off")
    context.add_init_script(CLIPBOARD_REFUSED)
    page, errors = open_page(context, base_url + begin.path)
    blocks = page.evaluate(CODE_BLOCKS)
    if len(blocks) == 5:
        cursor = blocks[2]  # the longest, over several lines
        page.locator(f'[data-copy="{cursor["id"]}"]').click()
        page.wait_for_timeout(300)
        if page.locator(f'[data-copy="{cursor["id"]}"]').text_content().strip() != "Selected":
            problems.append(f"{label}: Copy does not say it selected the code")
        if page.evaluate("() => String(window.getSelection())").strip() != cursor["text"]:
            problems.append(f"{label}: Copy selects {page.evaluate('() => String(window.getSelection())').strip()!r}, not the block's code")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    label = "/begin/ without JavaScript, the code blocks"
    context = new_context(browser, base_url, session_key, begin, width=375, theme=None, motion=None, javascript=False)
    page, errors = open_page(context, base_url + begin.path)
    for block in page.evaluate("""() => [...document.querySelectorAll('.code-block')].map((block) => ({ id: block.querySelector('pre').id,
        shown: Boolean(block.querySelector('[data-copy]').getClientRects().length), box: block.getBoundingClientRect().height,
        code: block.querySelector('pre').getBoundingClientRect().height }))"""):
        if block["shown"]:
            problems.append(f"{label}: the Copy button of {block['id']} shows, and cannot work")
        if abs(block["box"] - block["code"] - 2) > 1.5:  # the box's hairline top and bottom, and nothing else
            problems.append(f"{label}: {block['id']} is {block['box']:.0f}px tall around code {block['code']:.0f}px tall; the box should hold the code alone")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    return problems


def code_block_copy_problems(page, label, blocks):
    """Copy on each block of Begin, one after the other (see code_block_problems)."""
    problems = []
    status = lambda: page.evaluate("() => [...document.querySelectorAll('.code-block [data-copy-status]')].map((node) => node.textContent)")
    for index, block in enumerate(blocks):
        where = f"{label}, {block['id']}"
        button = page.locator(f'[data-copy="{block["id"]}"]')
        button.scroll_into_view_if_needed()
        before = page.evaluate(CODE_BLOCKS)[index]
        button.click()
        page.wait_for_timeout(300)
        copied = page.evaluate("() => navigator.clipboard.readText()")
        if copied != block["text"]:
            problems.append(f"{where}: Copy put {copied!r} on the clipboard; the block's text is {block['text']!r}")
        after = page.evaluate(CODE_BLOCKS)[index]
        if after["word"] != "Copied":
            problems.append(f"{where}: Copy says {after['word']!r}, not Copied")
        if status() != ["Copied" if other == index else "" for other in range(len(blocks))]:
            problems.append(f"{where}: after Copy the blocks' statuses are {status()}; only this block's should say Copied (spoken once, in its own block)")
        for part in ("copy", "code"):
            if abs(after[part]["x"] - before[part]["x"]) > 1 or abs(after[part]["width"] - before[part]["width"]) > 1:
                problems.append(f"{where}: the {part} box moves or changes width when Copy says Copied "
                                f"(x {before[part]['x']:.0f} to {after[part]['x']:.0f}, width {before[part]['width']:.0f} to {after[part]['width']:.0f})")
        if index == 0:
            problems += axe_problems(page, f"{where} with Copied showing")
        page.wait_for_timeout(1700)
        if page.evaluate(CODE_BLOCKS)[index]["word"] != "Copy" or any(status()):
            problems.append(f"{where}: Copy does not go back to Copy, and its status to empty, after 1.8 s")
    if len({block["text"] for block in blocks}) != len(blocks):
        problems.append(f"{label}: two blocks hold the same text, so a Copy that took the wrong block's would not show")
    focus = page.locator(f'[data-copy="{blocks[1]["id"]}"]')
    focus.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    if page.evaluate("() => navigator.clipboard.readText()") != blocks[1]["text"]:
        problems.append(f"{label}: Enter on the focused Copy button of {blocks[1]['id']} does not copy its text")
    return problems


def come_in_motion_problems(browser, base_url, session_key, site_page):
    """Come in follows the motion rule of base.css, which come-in.js shares: instant when Motion is off, and when
    the system asks for reduced motion with nothing saved (the boot script then turns Motion off); the normal
    fade when the system asks for reduced motion and the visitor saved Motion on, because they chose it.
    Instant means 150 ms after the click the install line is showing at full opacity with no opacity transition
    running: being visible says nothing about a fade, which shows at opacity 0.2 long before it is done.
    The fade means the line and COME IN are still showing 150 ms after the click with an opacity transition
    running, the install line then fades in (a transition running on it), and 1.8 s after the click the install
    line is at full opacity with the line gone."""
    problems = []
    for words, motion, reduced, instant in (("Motion off", "off", "no-preference", True),
                                            ("reduced motion", None, "reduce", True),
                                            ("reduced motion and Motion saved on", "on", "reduce", False)):
        context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=motion,
                              reduced_motion=reduced)
        page, errors = open_page(context, base_url + site_page.path)
        label = f"{site_page.path} with {words}"
        line, install = page.locator("[data-threshold-line]").first, page.locator("[data-install]")
        page.locator("[data-come-in]").click()
        page.wait_for_timeout(150)
        if instant:
            if not install.is_visible():
                problems.append(f"{label}: Come in is not instant (the install line is not showing)")
            else:
                state = install.evaluate(OPACITY_FADE)
                if state["opacity"] != "1" or state["fading"]:
                    problems.append(f"{label}: Come in is not instant (the install line is fading in: "
                                    f"opacity {state['opacity']}, {state['fading']} opacity transition(s) running)")
        else:
            if not line.is_visible():
                problems.append(f"{label}: Come in is instant; the line should fade out (it is already gone)")
            elif not line.evaluate(OPACITY_FADE)["fading"]:
                problems.append(f"{label}: the line is not fading out (no opacity transition is running on it)")
            if install.is_visible():
                problems.append(f"{label}: the install line shows while the line is still fading out")
            try:
                page.wait_for_function("() => !document.querySelector('[data-install]').hidden", timeout=3000)
                if not install.evaluate(OPACITY_FADE)["fading"]:
                    problems.append(f"{label}: the install line does not fade in (no opacity transition is running on it)")
                page.wait_for_timeout(900)  # the rest of the fade in
                state = install.evaluate(OPACITY_FADE)
                if not install.is_visible() or state["opacity"] != "1" or state["fading"] or line.is_visible():
                    problems.append(f"{label}: 1.8 s after the click the install line is not fully showing with the line gone "
                                    f"(opacity {state['opacity']}, {state['fading']} opacity transition(s) running, "
                                    f"line {'showing' if line.is_visible() else 'gone'})")
            except PlaywrightError:
                problems.append(f"{label}: the install line never appeared")
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()
    return problems


def come_in_twice_problems(browser, base_url, session_key, site_page):
    """Come in asked for twice before the first has finished: the install line once, and still on this page.
    A double click shows the page stays put (the first click puts pointer-events: none on COME IN, so the
    second never arrives). Enter pressed twice does reach come-in.js, so it exercises its `entering` guard: the
    second Enter must be cancelled like the first, not followed to /begin/, and must not start the fade again."""
    problems, url = [], base_url + site_page.path
    context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
    page, errors = open_page(context, url)
    page.locator("[data-come-in]").dblclick()
    page.wait_for_timeout(1800)
    if page.evaluate("() => location.pathname") != "/" or not page.locator("[data-install]").is_visible():
        problems.append(f"{site_page.path}: a double click on Come in leaves the threshold or loses the install line")
    problems += [f"{site_page.path} after a double click on Come in: console error: {error}" for error in errors]
    context.close()

    context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
    page, errors = open_page(context, url)
    label = f"{site_page.path} after Enter pressed twice on Come in"
    page.evaluate("""() => { window.fades = 0;
        const line = document.querySelector('[data-install]');
        line.addEventListener('transitionrun', (event) => {
            if (event.target === line && event.propertyName === 'opacity') window.fades += 1; }); }""")
    page.locator("[data-come-in]").focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)  # inside the first fade out
    page.keyboard.press("Enter")
    page.wait_for_timeout(2200)  # the fade out, the fade in, and a second run's if there were one
    state = page.evaluate("""() => { const line = document.querySelector('[data-install]');
        return { path: location.pathname, shown: Boolean(line) && line.getClientRects().length > 0,
                 opacity: line ? getComputedStyle(line).opacity : null, fades: window.fades }; }""")
    if state["path"] != "/":
        problems.append(f"{label}: left the threshold for {state['path']}")
    elif not state["shown"] or state["opacity"] != "1":
        problems.append(f"{label}: the install line is not fully showing (shown {state['shown']}, opacity {state['opacity']})")
    elif state["fades"] != 1:
        problems.append(f"{label}: the install line faded in {state['fades']} times; a second Enter should do nothing")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    return problems


def clipboard_refused_problems(browser, base_url, session_key, site_page):
    """A browser that refuses the clipboard: Copy selects the command and says so (Review Focus 3), and Selected,
    the longest word Copy shows, still leaves the box, the command and the button where they were."""
    context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
    context.add_init_script(CLIPBOARD_REFUSED)
    page, errors = open_page(context, base_url + site_page.path)
    label = f"{site_page.path} with the clipboard refused"
    problems = []
    page.locator("[data-come-in]").click()
    page.wait_for_timeout(1700)
    panel = page.locator('[data-install] [role="tabpanel"]:not([hidden])')
    before = install_part_boxes(panel)
    copy = panel.locator("[data-copy]")
    copy.click()
    page.wait_for_timeout(300)
    if (copy.text_content() or "").strip() != "Selected":
        problems.append(f"{label}: Copy does not say it selected the command")
    problems += install_part_problems(panel, before, label, "Selected")
    if page.evaluate("() => String(window.getSelection())").strip() != "https://veilfile.com/mcp":
        problems.append(f"{label}: Copy does not select the command")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    return problems


# A second <footer>, in <main>, as an article of a page on the shell (Docs, Journal) will have its own.
ARTICLE_FOOTER = "() => { const footer = document.createElement('footer'); footer.dataset.siteChecks = ''; footer.textContent = 'An article footer'; document.querySelector('main').append(footer); }"
REMOVE_ARTICLE_FOOTER = "() => document.querySelectorAll('main > footer[data-site-checks]').forEach((footer) => footer.remove())"


def no_switch_problems(page, label):
    """Without JavaScript neither switch can work, so neither shows, and the footer, which holds nothing else, is not shown
    as an empty strip of padding. The shell's footer is the page's own (body > footer): a second <footer> in the content,
    which is put there first, must neither be taken for it nor make the locator ambiguous (strict mode). It is taken out
    again as soon as what the check needs has been read, so it is not in the page for the checks and the screenshots after."""
    page.evaluate(ARTICLE_FOOTER)
    try:
        problems = [f"{label}: the {name} shows but cannot work" for name, selector in
                    (("appearance switch", "body > footer [data-appearance-toggle]"), ("Motion switch", "body > footer [data-motion-toggle]"))
                    if page.locator(selector).is_visible()]
        if page.locator("body > footer").is_visible():
            problems.append(f"{label}: the footer shows with nothing in it")
    finally:
        page.evaluate(REMOVE_ARTICLE_FOOTER)
    return problems


def menu_rest_problems(browser, base_url, session_key, site_page):
    """The menu starts closed on every page at every width and stays closed until MENU is pressed (spec 4.4; as on earendil.com,
    where the links start hidden): on the threshold, on a chapter and on Begin, at 1280 and 375. MENU opens it, and on a chapter
    the current page's link is marked, by the same inset fill as hover and focus (on ::before), never by a fill on the link
    itself; nothing shows under the open column. A click elsewhere closes it at 1280, where the page is not under a sheet
    (the phone sheet's own taps are phone_sheet_problems')."""
    problems, url = [], base_url + site_page.path
    for width in WIDTHS:
        context = new_context(browser, base_url, session_key, site_page, width=width, theme=None, motion=None)
        page, errors = open_page(context, url)
        label = f"{site_page.path} at {width}px, the menu at rest"
        toggle, menu = page.locator("header [data-menu-toggle]"), page.locator("#shell-menu")
        if toggle.get_attribute("aria-expanded") != "false" or menu.is_visible():
            problems.append(f"{label}: the menu starts open; it should start closed on every page at every width")
        toggle.click()
        if toggle.get_attribute("aria-expanded") != "true" or not menu.is_visible():
            problems.append(f"{label}: MENU does not open the menu")
        else:
            if site_page.chapter:
                marked = page.evaluate("() => [...document.querySelectorAll('#shell-menu a[aria-current]')].map((a) => a.getAttribute('href'))")
                if marked != [site_page.path]:
                    problems.append(f"{label}: the open menu marks {marked}; it should mark {site_page.path}")
                fills = page.locator('#shell-menu a[aria-current="page"]').evaluate(
                    "(a) => [getComputedStyle(a).backgroundColor, getComputedStyle(a, '::before').backgroundColor]")
                if fills[0] != "rgba(0, 0, 0, 0)" or fills[1] == "rgba(0, 0, 0, 0)":
                    problems.append(f"{label}: the current link's fill is {fills[0]} on the link and {fills[1]} on its inset mark")
            if width >= 1024:  # the column; below it the sheet covers the page
                page.wait_for_function(SHEET_IN_PLACE, timeout=5000)  # the column has finished fading in (0.7 s)
                problems += menu_sideways_problems(page, label)
                box = menu.bounding_box()
                if box and content_shows_under(page, box):
                    problems.append(f"{label}: page content shows under the open menu")
                page.mouse.click(4, 320)  # the left margin, where nothing answers the click
                if toggle.get_attribute("aria-expanded") != "false" or not menu_gone(page):
                    problems.append(f"{label}: a click elsewhere on the page leaves the menu open")
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()
    return problems


# Whether the first menu link takes a tap: what is painted at its centre is the link or something inside it (a rule that leaves the list
# taking no pointer, such as the closing sheet's `pointer-events: none`, is hit through, and the page behind gets the tap). The link is brought
# into the viewport first, since a point outside it is hit-tested to nothing.
FIRST_MENU_LINK_HIT = """() => { const link = document.querySelector('#shell-menu a');
  link.scrollIntoView({ block: 'center' });
  const box = link.getBoundingClientRect(), hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
  return { href: link.getAttribute('href'), hit: hit ? hit.tagName.toLowerCase() + (hit.id ? '#' + hit.id : '') + (hit.className ? '.' + String(hit.className).split(' ').join('.') : '') : null,
           takes: Boolean(hit) && link.contains(hit) }; }"""


def menu_link_hit_problems(page, label):
    """Where the menu's links are on the page without the sheet's script (no JavaScript, shell.js failing to load or throwing), the first one takes a
    tap: a rule written for the sheet that the script's `.js` does not scope would leave every link ignoring taps, with all of them still visible."""
    if page.locator("#shell-menu a").count() == 0:
        return []
    found = page.evaluate(FIRST_MENU_LINK_HIT)
    if not found["takes"]:
        return [f"{label}: a tap at the centre of the first menu link ({found['href']}) reaches {found['hit']}, not the link; its menu should take taps without the script"]
    return []


def page_edge_problems(browser, base_url, session_key, site_page, out):
    """The edge cases of any page on the shell, the pages part's on each page of PAGES and the catalog part's on the catalog: what a visitor can bring (no
    JavaScript, shell.js failing to load or throwing or late, blocked storage, saved values it does not know, reduced motion), the corners wherever the page
    is scrolled and at both sides of 64rem, and narrow and short screens. The threshold's and the chapters' own (Come in, the in-place changes, the fades) are
    edge_case_problems', which runs this first."""
    problems = []
    url = base_url + site_page.path

    # No JavaScript: every menu link is visible and clear of the text, the switches that need
    # JavaScript are hidden.
    for width in WIDTHS:
        context = new_context(browser, base_url, session_key, site_page, width=width, theme=None, motion=None, javascript=False)
        page, errors = open_page(context, url)
        label = f"{site_page.path} at {width}px without JavaScript"
        if page.locator("#shell-menu a").count() == 0 or not all(link.is_visible() for link in page.locator("#shell-menu a").all()):
            problems.append(f"{label}: some menu links are hidden")
        problems += [f"{label}: the menu covers the text {text!r}" for text in page.evaluate(TEXT_UNDER_MENU)]
        if width < 1024:  # the phone sheet's rules
            problems += menu_link_hit_problems(page, label)
        problems += no_switch_problems(page, label)
        problems += sideways_problems(page, label)
        if site_page.threshold and page.locator("[data-come-in]").get_attribute("href") != "/begin/":
            problems.append(f"{label}: without JavaScript Come in should be a link to /begin/")
        full_page_screenshot(page, out / f"{site_page.name}-{width}-no-javascript.png")
        problems += [f"{label}: {error}" for error in errors]
        context.close()

    # shell.js fails to load (a flaky network, a blocked script): the page falls back to the
    # no-JavaScript layout instead of a MENU button that does nothing.
    context = new_context(browser, base_url, session_key, site_page, width=375, theme=None, motion=None)
    context.route(SHELL_SCRIPT, lambda route: route.abort())
    page, _ = open_page(context, url)  # the failed script is itself a console error; expected here
    label = f"{site_page.path} when shell.js cannot load"
    if page.locator("#shell-menu a").count() == 0 or not all(link.is_visible() for link in page.locator("#shell-menu a").all()):
        problems.append(f"{label}: the menu links are hidden behind a MENU button that does nothing")
    problems += menu_link_hit_problems(page, label)
    problems += no_switch_problems(page, label)
    context.close()

    problems += menu_rest_problems(browser, base_url, session_key, site_page)

    # shell.js throws while setting up: the page falls back to the no-JavaScript layout, as when it
    # cannot load, instead of a dead MENU and hidden switches.
    def broken_shell(route):
        response = route.fetch()
        body = response.text().replace('document.querySelectorAll("[data-menu]").forEach(setUpMenu);',
                                       'throw new Error("site checks: simulated failure in shell.js");', 1)
        route.fulfill(response=response, body=body)

    context = new_context(browser, base_url, session_key, site_page, width=375, theme=None, motion=None)
    context.route(SHELL_SCRIPT, broken_shell)
    page, _ = open_page(context, url)  # the simulated error is itself a page error; expected here
    label = f"{site_page.path} when shell.js throws"
    if page.locator("#shell-menu a").count() == 0 or not all(link.is_visible() for link in page.locator("#shell-menu a").all()):
        problems.append(f"{label}: the menu links are hidden behind a MENU button that does nothing")
    problems += menu_link_hit_problems(page, label)
    problems += no_switch_problems(page, label)
    context.close()

    # Until shell.js runs, the switches cannot show the saved choice, so they stay hidden rather
    # than show the wrong word.
    context = new_context(browser, base_url, session_key, site_page, width=1280, theme="dark", motion="off")
    held = []
    context.route(SHELL_SCRIPT, lambda route: held.append(route))
    page = context.new_page()
    page.goto(url, wait_until="commit")
    page.wait_for_function("() => { const c = document.querySelector('footer .controls');"
                           " return Boolean(c) && getComputedStyle(c).display === 'flex'; }")
    label = f"{site_page.path} before shell.js runs"
    switch = page.locator("footer [data-appearance-toggle]")
    if switch.is_visible():
        problems.append(f"{label}: the switches show {(switch.text_content() or '').strip()!r} before shell.js has read the saved Ink")
    for route in held:
        route.continue_()
    page.wait_for_load_state("networkidle")
    if not switch.is_visible() or (switch.text_content() or "").strip() != "Ink":
        problems.append(f"{label}: after shell.js runs the appearance switch does not show the saved Ink")
    context.close()

    # Wherever the page is scrolled, nothing is painted over the corners: their switches take clicks.
    for width, height in ((1280, 800), (375, 760)):
        context = new_context(browser, base_url, session_key, site_page, width=width, height=height, theme=None, motion=None)
        page, errors = open_page(context, url)
        label = f"{site_page.path} scrolled at {width}x{height}"
        covered = {}
        bottom = page.evaluate("() => document.documentElement.scrollHeight - innerHeight")
        for y in range(0, bottom + 1, height // 3):
            page.evaluate(f"() => window.scrollTo(0, {y})")
            for name in page.evaluate(COVERED, "header [data-menu-toggle], footer [data-appearance-toggle], footer [data-motion-toggle]"):
                covered.setdefault(name, y)
        problems += [f"{label}: at {y}px down, something on the page covers {name!r}" for name, y in covered.items()]
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()

    # Wide screens (64rem and up): the four corners stay put and the page runs between them. Narrow screens: the
    # bottom corners wait at the end of the page instead of floating over it, and the page fades out along the top
    # (cloth_top_fade_problems). Both are checked at the edge of the switch, and nothing may show under a corner
    # either way.
    for width, height, wide in ((1024, 768, True), (1023, 768, False), (375, 667, False)):
        context = new_context(browser, base_url, session_key, site_page, width=width, height=height, theme=None, motion=None)
        page, errors = open_page(context, url)
        label = f"{site_page.path} at {width}x{height}"
        before = page.locator(".shell-corner--bottom-left").bounding_box()
        page.evaluate("() => window.scrollTo(0, 120)")
        after = page.locator(".shell-corner--bottom-left").bounding_box()
        moved = before and after and abs(before["y"] - after["y"]) > 1
        if wide and moved:
            problems.append(f"{label}: the bottom corners scroll away on a wide screen")
        # A short page does not scroll, so the corners cannot be seen to move: ask for their position.
        scrolls = page.evaluate("() => document.documentElement.scrollHeight > innerHeight")
        floats = not moved if scrolls else page.evaluate(
            "() => getComputedStyle(document.querySelector('.shell-corner--bottom-left')).position === 'fixed'")
        if not wide and floats:
            problems.append(f"{label}: the bottom corners float over the page on a narrow screen; they should wait at its end")
        page.evaluate("() => window.scrollTo(0, 0)")
        problems += under_corner_problems(page, label)
        if wide:  # the column runs down from MENU and is at its tightest here, at 1024: nothing may show under a corner with it open either
            set_menu(page, True)
            problems += under_corner_problems(page, f"{label} with the menu open")
            set_menu(page, False)
        set_menu(page, True)  # the column from 64rem up, the sheet below it
        problems += menu_sideways_problems(page, f"{label} with the menu open")
        set_menu(page, False)
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()

    # Narrow and short viewports: the corners never overlap, and every menu link can be reached.
    for width, height in SMALL_VIEWPORTS:
        context = new_context(browser, base_url, session_key, site_page, width=width, height=height, theme=None, motion=None)
        page, errors = open_page(context, url)
        label = f"{site_page.path} at {width}x{height}"
        problems += sideways_problems(page, label)
        problems += [f"{label}: the {pair} corners overlap" for pair in page.evaluate(CORNER_OVERLAPS)]
        problems += menu_reach_problems(page, label)
        page.screenshot(path=str(out / f"{site_page.name}-{width}x{height}.png"))
        line = page.locator("[data-tabs]").first
        if line.count() and line.is_visible():
            problems += install_line_height_problems(line, label)
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()

    # Blocked storage: the page still renders in Auto and the switch still changes this page.
    context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None, block_storage=True)
    page, errors = open_page(context, url)
    label = f"{site_page.path} with storage blocked"
    if page.evaluate(ROOT_ATTRIBUTE, "data-theme") != "system":
        problems.append(f"{label}: the page did not start in Auto")
    page.locator("footer [data-appearance-toggle]").click()
    if page.evaluate(ROOT_ATTRIBUTE, "data-theme") != "light":
        problems.append(f"{label}: the appearance switch does nothing")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    # Saved values this version does not know fall back to Auto and the system's motion setting.
    context = new_context(browser, base_url, session_key, site_page, width=1280, theme="paper", motion="maybe")
    page, errors = open_page(context, url)
    label = f"{site_page.path} with unknown saved values"
    shown = (page.evaluate(ROOT_ATTRIBUTE, "data-theme"), page.evaluate(ROOT_ATTRIBUTE, "data-motion"))
    if shown != ("system", "on"):
        problems.append(f"{label}: started as {shown}, expected ('system', 'on')")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()

    # A system that asks for reduced motion: off with nothing saved, on when the visitor saved on.
    for saved, expected in ((None, "off"), ("on", "on")):
        context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=saved, reduced_motion="reduce")
        page, errors = open_page(context, url)
        label = f"{site_page.path} with reduced motion and {saved or 'nothing'} saved"
        if page.evaluate(ROOT_ATTRIBUTE, "data-motion") != expected:
            problems.append(f"{label}: Motion is not {expected}")
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()
    problems += chapter_style_problems(browser, base_url, session_key, site_page)
    return problems


def edge_case_problems(browser, base_url, session_key, site_page, out):
    """The pages part's edge cases of a page of PAGES: page_edge_problems, and for the threshold and the chapters what only they have."""
    problems = page_edge_problems(browser, base_url, session_key, site_page, out)
    url = base_url + site_page.path
    if site_page.threshold:
        for width, motion in ((1280, "on"), (1024, "on"), (375, "on"), (320, "on")):
            context = new_context(browser, base_url, session_key, site_page, width=width, theme=None, motion=motion)
            page, errors = open_page(context, url)
            label = f"{site_page.path} at {width}px, Come in"
            if width >= 900 and page.evaluate(LINES, "[data-threshold-line]") != 2:
                problems.append(f"{label}: on a wide screen the line should set in two lines, breaking after \"moment,\"")
            problems += come_in_problems(page, label)
            problems += sideways_problems(page, label, " with the install line showing")  # Review Focus 5
            page.locator(".shell-corner--top-left .lockup").click()  # the lockup returns to the line
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1000)
            if not page.locator("[data-threshold-line]").is_visible():
                problems.append(f"{label}: the lockup does not return to the line")
            problems += [f"{label}: console error: {error}" for error in errors]
            context.close()

        problems += come_in_motion_problems(browser, base_url, session_key, site_page)
        problems += come_in_twice_problems(browser, base_url, session_key, site_page)
        problems += clipboard_refused_problems(browser, base_url, session_key, site_page)
    if site_page.path == "/about/":  # the shell's in-place changes, tried once
        problems += chapter_change_problems(browser, base_url, session_key, site_page)
        problems += chapter_transition_problems(browser, base_url, session_key, site_page)
        problems += history_scroll_problems(browser, base_url, session_key, site_page)
        problems += crossfade_scroll_problems(browser, base_url, session_key, site_page)
        problems += fragment_link_problems(browser, base_url, session_key, site_page)
    if site_page.path == "/about/" or site_page.threshold:  # the two scripts that wait out a fade, tried once each
        problems += fade_length_problems(browser, base_url, session_key, site_page)
    if site_page.threshold:  # a chapter reached from the threshold loads the reading font on demand
        problems += font_wait_problems(browser, base_url, session_key, site_page)
    return problems


# The reading pages (the Journal, the Docs and the legal documents) are checked by the `reading` part, in a loop of their own
# (run_reading). Each is opened at 375 and 1280 in Paper and Ink with Motion on (Motion changes only how a change fades, which the
# change checks watch); the corners and the menu are looked at in the states the pages part looks at them; and in place of the
# pages part's 20 or so edge cases a page they get the few a long column calls for: long content that must not widen the page, the
# section list and where its links land, Copy on a code block, no JavaScript, and the changes among the pages. A check with nothing to measure says so:
# as a problem where the page must have the thing, as a note (REVIEW_NOTES) where it may rightly lack it.
READING_STOPS = 8   # the scroll positions the corner check looks at on a reading page, spread from its top to its end
READING_WIDTHS = (1280, 375, 320)   # nothing scrolls sideways at any of them; the widest first, since the long content is added at the first
READING_RUN = {"pages": ()}   # the reading pages of this run, for the line the run ends with
READING_CSS = "the .reading rules in backend/static/css, found with: grep -rn reading backend/static/css"
CODE_PAGES = ("/docs/quickstart/",)   # the reading pages that must have a code block, so that Copy on one is tried (reading_code_problems)
LEGAL_WORDS = {"/terms/": "Terms", "/privacy/": "Privacy", "/report-abuse/": "Report abuse"}   # the menu's word for each legal document
ARTICLE_OF = {"post": "BlogPosting", "docs-page": "TechArticle"}   # the Article structured data each kind of reading page carries (spec 6.6)
# The Journal's posts, newest first, as the Journal lists them: the paths of the links in <main> that go to a post. A post path has a
# slug between its slashes, so the page's other links are not posts: /blog/ itself (a post's "Journal" link back, which the Journal's
# own page does not carry) and /blog/feed.xml.
POST_PATHS = """() => { const paths = [];
  for (const link of document.querySelectorAll('main a[href]')) {
    const path = new URL(link.href).pathname;
    if (/^\\/blog\\/[-a-zA-Z0-9_]+\\/$/.test(path) && !paths.includes(path)) paths.push(path);
  }
  return paths; }"""
# The reading column's width, its measure (a probe of 65ch inside the column, so in the column's own face) and the room <main> leaves it.
READING_MEASURE = """() => {
  const column = document.querySelector('.reading');
  if (!column) return null;
  const probe = document.createElement('div');
  probe.style.cssText = 'position:absolute;visibility:hidden;width:65ch;height:0;margin:0;padding:0;border:0';
  column.append(probe);
  const measure = probe.getBoundingClientRect().width;
  probe.remove();
  const main = document.querySelector('main'), style = getComputedStyle(main);
  return { width: column.getBoundingClientRect().width, measure,
           room: main.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) };
}"""
# What a reading column must hold without the page scrolling sideways (Review Focus 1): a heading with a long word, a paragraph with a
# long unbroken URL, a long code line in a code block and a wide table (as the reading pages draw them: the code block by
# components/code_block.html, the table in render_markdown's .table-scroll). Added at the end of the column, in a div that REMOVE_LONG
# takes out again. False when the page has no column.
LONG_CONTENT = """() => {
  const column = document.querySelector('.reading');
  if (!column) return false;
  const holder = document.createElement('div');
  holder.dataset.siteChecks = 'long';
  const url = 'https://example.com/' + 'unbroken'.repeat(24);
  const cells = (tag) => Array.from({ length: 8 }, (_, index) => `<${tag}>Column ${index + 1} heading</${tag}>`).join('');
  holder.innerHTML = `<h2 id="check-long-heading">${'Unbroken'.repeat(12)}</h2>`
    + `<p id="check-long-url">A link: <a href="${url}">${url}</a> and a word, ${'wordy'.repeat(30)}.</p>`
    + `<div class="code-block" data-code-block><button class="code-block__copy label interactive" type="button" data-copy="check-long-code" aria-label="Copy the long code line">Copy</button><pre id="check-long-code" tabindex="0"><code>${'long_code_line_'.repeat(20)}</code></pre><p class="visually-hidden" role="status" data-copy-status></p></div>`
    + `<div id="check-wide-table" class="table-scroll" tabindex="0" role="region" aria-label="Scrollable table"><table style="white-space: nowrap"><thead><tr>${cells('th')}</tr></thead><tbody><tr>${cells('td')}</tr></tbody></table></div>`;
  column.append(holder);
  return true;
}"""
REMOVE_LONG = "() => document.querySelectorAll('[data-site-checks=\"long\"]').forEach((holder) => holder.remove())"
# Where each piece of LONG_CONTENT lies against the column, and whether it overflows its own box.
LONG_FIT = """() => {
  const column = document.querySelector('.reading').getBoundingClientRect();
  const measure = (id) => { const element = document.getElementById(id), rect = element.getBoundingClientRect();
    return { right: rect.right, scroll: element.scrollWidth, client: element.clientWidth, height: rect.height,
             line: parseFloat(getComputedStyle(element).lineHeight) || 0 }; };
  return { column: { right: column.right }, heading: measure('check-long-heading'), paragraph: measure('check-long-url'),
           code: measure('check-long-code'), table: measure('check-wide-table') }; }"""
# The headings a section list should list: the page's own ## headings (h2 in the column, outside any nav), with their ids and plain text.
READING_HEADINGS = """() => [...document.querySelectorAll('.reading h2')].filter((heading) => !heading.closest('nav'))
    .map((heading) => ({ id: heading.id, text: heading.textContent.replace(/\\s+/g, ' ').trim() }))"""
READING_SECTION_LINKS = """(nav) => [...nav.querySelectorAll('a[href]')]
    .map((link) => ({ href: link.getAttribute('href'), text: link.textContent.replace(/\\s+/g, ' ').trim() }))"""
# Counts the swaps of <main>'s content and the fetches the page makes from here on: a link that only scrolls the page makes neither.
JUMP_WATCH = """() => { window.swaps = 0; window.fetches = 0;
  new MutationObserver(() => { window.swaps += 1; }).observe(document.querySelector('main'), { childList: true });
  const fetched = window.fetch; window.fetch = (...args) => { window.fetches += 1; return fetched(...args); }; }"""
# Where a section link left its heading: the heading's top in the window, the heading's own scroll-margin-top (margin), the top
# margin the browser keeps for it (scroll-padding-top), how far the page can still scroll, the address, and what JUMP_WATCH counted.
JUMP_LANDING = """(id) => ({ top: document.getElementById(id).getBoundingClientRect().top,
    margin: parseFloat(getComputedStyle(document.getElementById(id)).scrollMarginTop) || 0,
    pad: parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop) || 0,
    room: document.documentElement.scrollHeight - innerHeight - window.scrollY,
    hash: location.hash, path: location.pathname, swaps: window.swaps, fetches: window.fetches })"""
# Resolves when the page has stopped scrolling (the same position for three frames), or after 2 s.
SCROLL_SETTLED = """() => new Promise((done) => { let last = -1, same = 0;
  const frame = () => { same = window.scrollY === last ? same + 1 : 0; last = window.scrollY;
    if (same >= 3) done(true); else requestAnimationFrame(frame); };
  requestAnimationFrame(frame); setTimeout(() => done(false), 2000); })"""
MENU_MARKS = "() => [...document.querySelectorAll('#shell-menu a[aria-current]')].map((a) => [a.textContent.trim(), a.getAttribute('aria-current')])"
# What a reading page shows of itself, with what a direct load of the same address gets: the title and every [data-page-meta] element
# of the head (description, canonical address, Open Graph tags, structured data), as the page fetched now has them.
ARRIVAL = """async (path) => {
  const asked = await fetch(path, { headers: { Accept: 'text/html' }, credentials: 'same-origin' });
  const loaded = new DOMParser().parseFromString(await asked.text(), 'text/html');
  const meta = (root) => [...root.head.querySelectorAll('[data-page-meta]')].map((element) => element.outerHTML).sort();
  const data = [...document.head.querySelectorAll('script[type="application/ld+json"][data-page-meta]')].map((script) => {
    try { return JSON.parse(script.textContent); } catch (error) { return { '@type': 'unreadable' }; } });
  const heading = document.querySelector('main h1'), main = document.querySelector('main');
  return { title: [document.title, loaded.title], meta: [meta(document), meta(loaded)],
    canonical: [...document.querySelectorAll('link[rel="canonical"]')].map((link) => link.href),
    organizations: data.filter((item) => item['@type'] === 'Organization').length,
    articles: data.filter((item) => item['@type'] !== 'Organization').map((item) => [item['@type'], item.headline || '']),
    heading: heading ? heading.textContent.replace(/\\s+/g, ' ').trim() : '',
    marks: [...document.querySelectorAll('#shell-menu a[aria-current]')].map((a) => [a.textContent.trim(), a.getAttribute('aria-current')]),
    layout: [document.body.dataset.layout, document.body.classList.contains('layout-reading')],
    opacity: getComputedStyle(main).opacity, leaving: main.classList.contains('is-leaving') }; }"""
# The links of a pager nav, with the path each goes to and the rel it carries: components/pager.html gives the previous link
# rel="prev" and the next link rel="next" (the word Previous or Next is a label beside the link, not in it).
PAGER_LINKS = """(nav) => [...nav.querySelectorAll('a[href]')]
    .map((link) => ({ path: new URL(link.href).pathname, rel: link.getAttribute('rel') || '' }))"""
# Before a click or a Back: remember that the page is the same one (window.stayedPut, which a whole-page load clears) and mark its
# heading, so that a page whose heading has not got the mark is another page's <main>.
MARK_HEADING = "() => { window.stayedPut = true; const heading = document.querySelector('main h1'); if (heading) heading.dataset.before = ''; }"
SWAPPED_TO = "(path) => location.pathname === path && !document.querySelector('main h1[data-before]')"
STAYED = "() => window.stayedPut === true"
FOCUSED = "() => document.activeElement === document.querySelector('main h1')"


def reading_kind(path):
    """What a reading page is, from its address: the Journal, a post, the Docs index, a Docs page or a legal document."""
    if path == "/blog/":
        return "journal"
    if path.startswith("/blog/"):
        return "post"
    if path == "/docs/":
        return "docs"
    return "docs-page" if path.startswith("/docs/") else "legal"


def reading_mark(path):
    """How the menu marks a reading page, as [word, aria-current]: its own link says "page"; a post or a Docs page marks its
    section's link "true" (registry.SECTIONS)."""
    kind = reading_kind(path)
    if kind == "legal":
        return [LEGAL_WORDS[path], "page"]
    return {"journal": ["Journal", "page"], "post": ["Journal", "true"], "docs": ["Docs", "page"], "docs-page": ["Docs", "true"]}[kind]


def reading_pages(browser, base_url, session_key):
    """The reading pages of this run in the order they are visited, and the Journal's posts newest first, as the Journal lists them.
    The newest post is not written down here: it is the Journal's first entry now, so a post added tomorrow is the one checked. A
    Journal with no post has no post page to check, and the run says so."""
    journal = READING_PAGES[0]
    context = new_context(browser, base_url, session_key, journal, width=1280, theme=None, motion=None)
    page, _ = open_page(context, base_url + journal.path)
    posts = page.evaluate(POST_PATHS)
    context.close()
    pages = list(READING_PAGES)
    if posts:
        pages.insert(1, SitePage(posts[0], "journal-post"))
    else:
        REVIEW_NOTES.append(f"nothing to measure: {journal.path} lists no post, so no post page was checked and the change from "
                            f"the Journal to a post was not tried")
    return tuple(pages), posts


def reading_menu_problems(page, label, path):
    """The menu starts closed on every page and never pins open (shell.js; spec 4.4; Review Focus 2), so a reading
    page's menu is closed when the page arrives at 1280px and does not open and close as the family changes in place (at 375px the
    pages part's phone_menu_problems, which run_reading also calls, checks that it starts closed); opened, it marks the page:
    [word, aria-current]."""
    problems = []
    toggle = page.locator("header [data-menu-toggle]")
    if toggle.get_attribute("aria-expanded") != "false" or page.locator("#shell-menu").is_visible():
        problems.append(f"{label}: the menu starts open on a reading page; the menu starts closed on every page and never pins open "
                        f"(shell.js, spec 4.4)")
    set_menu(page, True)
    marks = page.evaluate(MENU_MARKS)
    if marks != [reading_mark(path)]:
        problems.append(f"{label}: the opened menu marks {marks}; it should mark only {reading_mark(path)} (pages.registry.menu_groups)")
    set_menu(page, False)
    return problems


def reading_measure_problems(page, label):
    """The reading column is no wider than its measure, 65 characters of its own face (spec 4.6, --measure in tokens.css), at
    1280px, where the window leaves room for about that much, and at 1920px, where there is more room than the measure and only
    the measure keeps the column from filling it; and it is not much narrower than the room it has. The window goes back to 1280px."""
    problems = []
    for width in (1280, 1920):
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate(NEXT_FRAMES)
        found = page.evaluate(READING_MEASURE)
        if found is None:
            problems.append(f'{label}: the page has no .reading column, so its measure was not checked; layouts/reading.html '
                            f'wraps a reading page\'s content in <div class="reading">')
            break
        column, measure, room = found["width"], found["measure"], found["room"]
        where = f"{label} in a {width}px window"
        if column > measure * 1.02 + 1:
            problems.append(f"{where}: the reading column is {column:.0f}px wide, wider than its measure of 65 characters "
                            f"({measure:.0f}px); give it max-width: var(--measure) in {READING_CSS}")
        elif column < min(measure, room) * 0.8:
            problems.append(f"{where}: the reading column is {column:.0f}px wide, though there is room for {min(measure, room):.0f}px; "
                            f"it should fill the room up to its measure (width: 100% in {READING_CSS})")
    page.set_viewport_size({"width": 1280, "height": 900})
    return problems


def reading_sideways_problems(browser, base_url, session_key, site_page):
    """No reading column scrolls the page sideways at 1280, 375 or 320px (spec 4.6; Review Focus 1), whatever it holds: a long
    word in a heading and a long unbroken URL wrap, a long code line and a wide table scroll inside their own boxes. The page's
    own words may have none of these, so the long content is added to the column (LONG_CONTENT) for this one run, and taken
    out again."""
    problems, path = [], site_page.path
    context = new_context(browser, base_url, session_key, site_page, width=READING_WIDTHS[0], theme=None, motion=None)
    page, errors = open_page(context, base_url + path)
    if not page.evaluate(LONG_CONTENT):
        context.close()
        return [f'{path}: the page has no .reading column to add long content to, so nothing was measured; layouts/reading.html '
                f'wraps a reading page\'s content in <div class="reading">']
    for width in READING_WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate(NEXT_FRAMES)
        label = f"{path} at {width}px with a long word in a heading, a long URL, a long code line and a wide table added"
        problems += sideways_problems(page, label)
        fit = page.evaluate(LONG_FIT)
        edge = fit["column"]["right"] + 1
        for name, box in (("long word in a heading", fit["heading"]), ("paragraph with a long URL", fit["paragraph"])):
            if box["scroll"] > box["client"] + 1 or box["right"] > edge:
                problems.append(f"{label}: the {name} does not wrap; it is {box['scroll']}px wide in a box {box['client']}px wide "
                                f"(overflow-wrap: anywhere in {READING_CSS})")
        paragraph = fit["paragraph"]
        if paragraph["line"] and paragraph["height"] < 2 * paragraph["line"]:
            problems.append(f"{label}: the paragraph with a long URL is {paragraph['height']:.0f}px tall, under two lines of "
                            f"{paragraph['line']:.0f}px, so the URL did not wrap")
        for name, box, fix in (("code block", fit["code"], "the block's grid column minmax(0, 1fr) and overflow-x: auto on .code-block pre, both in static/css/components/code_block.css"),
                               ("table", fit["table"], f"max-width: 100% and overflow-x: auto in {READING_CSS}")):
            if box["right"] > edge:
                problems.append(f"{label}: the {name} reaches {box['right']:.0f}px, past the column's edge; it should stay in the "
                                f"column ({fix})")
            elif box["scroll"] <= box["client"] + 1:
                problems.append(f"{label}: the {name} does not scroll inside its own box (scrollWidth {box['scroll']}, "
                                f"clientWidth {box['client']}); code and tables scroll sideways in their own boxes ({fix})")
    page.evaluate(REMOVE_LONG)
    problems += [f"{path} with long content added: console error: {error}" for error in errors]
    context.close()
    return problems


def reading_jump_problems(page, label, path, width, nav, headings):
    """A section list lists the page's ## headings, in order, as links to their ids, and each link is an ordinary jump (nothing
    fetched or swapped) that lands its heading at the top of the window, one scroll-margin-top (the heading's own) down: at 375px
    that is below the top fade (scroll-padding-top in base.css), at 1280px at the top. A heading near the end of the page, which
    cannot be scrolled that far, may stand lower."""
    problems = []
    links = nav.evaluate(READING_SECTION_LINKS)
    ids = [heading["id"] for heading in headings]
    if not all(ids) or len(set(ids)) != len(ids):
        problems.append(f"{label}: the ## headings' ids are {ids}; each needs an id of its own (render_reading in backend/blog/render.py)")
        return problems
    if [link["href"] for link in links] != [f"#{name}" for name in ids]:
        problems.append(f"{label}: the list links {[link['href'] for link in links]}, but the page's ## headings have the ids {ids}")
    elif [link["text"] for link in links] != [heading["text"] for heading in headings]:
        problems.append(f"{label}: the list reads {[link['text'] for link in links]}, but the headings read "
                        f"{[heading['text'] for heading in headings]}")
    page.evaluate(JUMP_WATCH)
    for index, heading in enumerate(headings):
        where = f"{label}: the link to {heading['text']!r}"
        page.evaluate("() => window.scrollTo(0, 0)")
        page.evaluate(NEXT_FRAMES)
        try:
            nav.locator("a").nth(index).click(timeout=10000)
        except PlaywrightError as error:
            problems.append(f"{where} cannot be clicked: {error.message.splitlines()[0]}")
            continue
        page.evaluate(SCROLL_SETTLED)
        landed = page.evaluate(JUMP_LANDING, heading["id"])
        if landed["path"] != path or landed["hash"] != f"#{heading['id']}":
            problems.append(f"{where} goes to {landed['path']}{landed['hash']}; it should only add #{heading['id']} to {path}")
        if landed["swaps"] or landed["fetches"]:
            problems.append(f"{where} changed the page in place ({landed['swaps']} swap(s), {landed['fetches']} fetch(es)); a link "
                            f"with a #fragment is the browser's jump, which shell.js leaves alone")
        if width < 1024:
            if landed["pad"] <= 0:
                problems.append(f"{where}: scroll-padding-top is {landed['pad']}px at {width}px; base.css gives .js.shell-ready "
                                f"the height of the top fade below 64rem, so a heading lands below it")
            elif landed["top"] < landed["pad"] - 1:
                problems.append(f"{where} lands the heading {landed['top']:.0f}px down, under the top fade, which ends "
                                f"{landed['pad']:.0f}px down (scroll-padding-top in base.css)")
            elif landed["top"] > landed["pad"] + landed["margin"] + 24 and landed["room"] > 1:
                problems.append(f"{where} lands the heading {landed['top']:.0f}px down, not at the top margin of "
                                f"{landed['pad'] + landed['margin']:.0f}px although the page can scroll {landed['room']:.0f}px further")
        elif landed["top"] < -1 or (landed["top"] > landed["margin"] + 2 and landed["room"] > 1):
            problems.append(f"{where} lands the heading {landed['top']:.0f}px down, not at the top of the window (its scroll margin is "
                            f"{landed['margin']:.0f}px) although the page can scroll {landed['room']:.0f}px further")
    return problems


def reading_sections_problems(browser, base_url, session_key, site_page):
    """The section list (spec 4.6; Review Focus 2 and 3): a nav labelled Contents on a Docs page or a legal document with two or
    more ## headings, and on no other page (a Journal post, the two indexes and a short document have none); the links of
    reading_jump_problems, at 1280px and, for a page that has the list, at 375px."""
    problems, kind = [], reading_kind(site_page.path)
    for width in ((1280, 375) if kind in ("docs-page", "legal") else (1280,)):
        context = new_context(browser, base_url, session_key, site_page, width=width, theme=None, motion=None)
        page, errors = open_page(context, base_url + site_page.path)
        label = f"{site_page.path} at {width}px, the section list"
        headings = page.evaluate(READING_HEADINGS)
        nav = page.get_by_role("navigation", name="Contents", exact=True)
        wanted = kind in ("docs-page", "legal") and len(headings) >= 2
        if nav.count() and not wanted:
            problems.append(f"{label}: the page has a section list, but only a Docs page or a legal document with two or more ## "
                            f"headings has one (it has {len(headings)}; components/sections.html shows nothing for fewer)")
        elif wanted and not nav.count():
            problems.append(f"{label}: the page has {len(headings)} ## headings but no section list (a nav labelled Contents, "
                            f"components/sections.html)")
        elif wanted:
            problems += reading_jump_problems(page, label, site_page.path, width, nav.first, headings)
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()
    return problems


def reading_no_script_problems(browser, base_url, session_key, site_page, out):
    """Without JavaScript a reading page is its words and plain links (spec 9): every menu link shows and clears the text, the two
    switches, which need JavaScript, and the footer around them do not show, and nothing scrolls sideways."""
    problems = []
    for width in (375, 1280):
        context = new_context(browser, base_url, session_key, site_page, width=width, theme=None, motion=None, javascript=False)
        page, errors = open_page(context, base_url + site_page.path)
        label = f"{site_page.path} at {width}px without JavaScript"
        links = page.locator("#shell-menu a")
        if links.count() == 0 or not all(link.is_visible() for link in links.all()):
            problems.append(f"{label}: some menu links are hidden")
        problems += [f"{label}: the menu covers the text {text!r}" for text in page.evaluate(TEXT_UNDER_MENU)]
        problems += no_switch_problems(page, label)
        problems += sideways_problems(page, label)
        full_page_screenshot(page, out / f"{site_page.name}-{width}-no-javascript.png")
        problems += [f"{label}: {error}" for error in errors]
        context.close()
    return problems


def reading_tabs_problems(page, label):
    """The quickstart's shell tabs and Copy buttons (tabs.js and copy.js, which listen on the document, so they work on a page
    swapped in by a change in place as on one loaded whole): a tablist whose click and arrow keys choose one tab and show only
    its panel, and Copy, which puts the command shown on the clipboard, says Copied and tells screen readers. The page has other
    [data-tabs] groups (the upload command's has no tablist), so the group looked at is the first that has a tab. This is not the
    install-line check, which looks for the threshold's install line and stays in the pages part."""
    line = page.locator('main [data-tabs]:has([role="tab"])').first
    tabs = line.locator('[role="tab"]')
    if not line.count() or tabs.count() < 2:
        return [f"{label}: no tablist with two or more tabs ([data-tabs] in <main>), so the shell tabs and Copy were not tried; "
                f"the quickstart's shell switcher is built on tabs.js and copy.js (backend/docs/pages/quickstart.md)"]
    problems = tab_state_problems(line, label, 0, "the page arrived", focused=False)
    problems += install_tab_keys_problems(page, line, label)
    tabs.nth(1).click()
    problems += tab_state_problems(line, label, 1, "a click on the second tab")
    controls = tabs.nth(1).get_attribute("aria-controls") or ""
    button = page.locator(f'[id="{controls}"] [data-copy]').first
    if not button.count():
        return problems + [f"{label}: the second tab's panel has no Copy button ([data-copy]); each command on the quickstart can be copied"]
    wanted = button.evaluate("""(button) => { const source = document.getElementById(button.dataset.copy);
        return source ? (source.querySelector('[data-copy-text]') || source).textContent.trim() : null; }""")
    button.click()
    page.wait_for_timeout(300)
    if (button.text_content() or "").strip() != "Copied":
        problems.append(f"{label}: Copy does not say Copied")
    if wanted is None or page.evaluate("() => navigator.clipboard.readText()") != wanted:
        problems.append(f"{label}: Copy did not put the command shown ({wanted!r}) on the clipboard")
    status = button.evaluate("""(button) => { const status = (button.closest('[data-tabs]') || document).querySelector('[data-copy-status]');
        return status ? status.textContent.trim() : null; }""")
    if status != "Copied":
        problems.append(f"{label}: Copy is not announced to screen readers (its [data-copy-status] reads {status!r})")
    return problems


def reading_code_problems(page, label, required=False):
    """Copy on the code blocks of the reading column (components/code_block.html, which render_reading draws every fenced block with).
    Each block's Copy button says Copy and is named Copy and what it copies, its code is a focusable scroll box, and the ids are
    each its own on the page. Copy, tried on the first block, the middle one and the last (a post may have many, and each Copy
    waits out its 1.8 s), puts exactly the code as a reader sees it on the clipboard (line breaks, indentation and any < or & as
    shown), says Copied and tells screen readers in its own block's status and no other's. Begin's blocks are code_block_problems'
    and the quickstart's shell tabs reading_tabs_problems'. A page may rightly have no code (the Journal, Terms), so a page with
    none is a problem only where `required` says it must have one (the quickstart's DELETE example, in CODE_PAGES)."""
    blocks = page.evaluate(CODE_BLOCKS)
    if not blocks:
        return [f"{label}: the page has no code block (.code-block), so Copy on a code block was not tried; the quickstart's fenced example "
                f"(backend/docs/pages/quickstart.md) is drawn through components/code_block.html by render_reading; if it was taken out on "
                f"purpose, name a Docs page that has code in CODE_PAGES"] if required else []
    problems, ids = [], [block["id"] for block in blocks]
    if not all(ids) or len(set(ids)) != len(ids):
        problems.append(f"{label}: the code blocks' ids are {ids}; each needs an id of its own on the page, which its Copy button names "
                        f"in data-copy (render_reading in backend/blog/render.py)")
    for index, block in enumerate(blocks):
        where = f"{label}, code block {index + 1}"
        if not block["shown"] or block["word"] != "Copy" or not (block["name"] or "").startswith("Copy "):
            problems.append(f"{where}: the Copy button is shown: {block['shown']}, says {block['word']!r} and is named {block['name']!r}; it "
                            f"should say Copy and be named Copy followed by what it copies (components/code_block.html)")
        if block["tab"] != 0:
            problems.append(f"{where}: the code is not a keyboard-focusable scroll box (tabindex {block['tab']})")
    status = lambda: page.evaluate("() => [...document.querySelectorAll('.code-block [data-copy-status]')].map((node) => node.textContent)")
    for index in sorted({0, len(blocks) // 2, len(blocks) - 1}):
        where = f"{label}, code block {index + 1}"
        button = page.locator(".code-block").nth(index).locator("[data-copy]")
        try:
            button.click(timeout=10000)
        except PlaywrightError as error:
            problems.append(f"{where}: could not click Copy: {error.message.splitlines()[0]}")
            continue
        page.wait_for_timeout(300)
        copied = page.evaluate("() => navigator.clipboard.readText()")
        if copied != blocks[index]["text"]:
            problems.append(f"{where}: Copy put {copied!r} on the clipboard; the block's text is {blocks[index]['text']!r}")
        if (button.text_content() or "").strip() != "Copied":
            problems.append(f"{where}: Copy does not say Copied")
        if status() != ["Copied" if other == index else "" for other in range(len(blocks))]:
            problems.append(f"{where}: after Copy the blocks' statuses are {status()}; only this block's should say Copied (spoken once, in its own block)")
        try:  # Copy goes back to its word after 1.8 s: the next look at the page (the corners) should not catch it half way
            page.wait_for_function("() => [...document.querySelectorAll('.code-block [data-copy]')].every((button) => button.textContent.trim() === 'Copy')", timeout=4000)
        except PlaywrightError:
            problems.append(f"{where}: Copy does not go back to Copy after it said Copied (copy.js)")
    return problems


def pager_of(page):
    """The page's previous and next links as {"previous": path or None, "next": path or None}, or None when it has no pager (a nav
    labelled More to read, components/pager.html). A link is the previous or the next by its rel (prev or next), never by its text,
    which is the neighbour's title."""
    nav = page.get_by_role("navigation", name="More to read", exact=True)
    if not nav.count():
        return None
    pager = {"previous": None, "next": None}
    for link in nav.first.evaluate(PAGER_LINKS):
        side = {"prev": "previous", "next": "next"}.get(link["rel"])
        if side:
            pager[side] = link["path"]
    return pager


def pager_link(page, path):
    """The pager's link to path."""
    return page.get_by_role("navigation", name="More to read", exact=True).locator(f'a[href="{path}"]')


def reading_pager_problems(page, label, previous, following):
    """A post's pager links the posts either side of it by date ("previous" is the older), a Docs page's the pages either side
    of it by order, and a page with no neighbour has no pager (Review Focus 3). previous and following are the addresses the page
    should link, None for a side it should not have."""
    found = {side: path for side, path in (pager_of(page) or {}).items() if path}
    wanted = {side: path for side, path in (("previous", previous), ("next", following)) if path}
    if found == wanted:
        return []
    return [f"{label}: the pager links {found or 'nothing'}; it should link {wanted or 'nothing'} "
            f"(blog.loader.neighbours, components/pager.html)"]


def reading_arrival_problems(page, label, path):
    """What a reading page shows once it is there, however it came (a direct load, a change in place, Back): the head a direct load
    of it gets (its title and every [data-page-meta] element: description, canonical address, Open Graph tags and structured
    data), one absolute canonical address, one Organization and its own Article structured data (a post's BlogPosting, a Docs
    page's TechArticle, none elsewhere) with the page's own headline, the menu closed and marking the page, the reading layout, and
    <main> showing."""
    found, kind, problems = page.evaluate(ARRIVAL, path), reading_kind(path), []
    if found["title"][0] != found["title"][1]:
        problems.append(f"{label}: the title reads {found['title'][0]!r}, but a direct load of {path} reads {found['title'][1]!r}; "
                        f"shell.js swaps the title with the page")
    now, loaded = found["meta"]
    if now != loaded:
        extra, missing = [text[:160] for text in now if text not in loaded], [text[:160] for text in loaded if text not in now]
        problems.append(f"{label}: the head's [data-page-meta] elements are not those a direct load of {path} gets (only in this "
                        f"page: {extra}; missing from it: {missing}); every head element that changes with the page carries "
                        f"data-page-meta in shell.html, and shell.js swaps those")
    canonical = found["canonical"]
    if len(canonical) != 1 or not canonical[0].startswith("https://") or not canonical[0].endswith(path):
        problems.append(f"{label}: the canonical addresses read {canonical}; there should be one, absolute, ending {path}")
    if found["organizations"] != 1:
        problems.append(f"{label}: {found['organizations']} Organization structured data scripts; there should be one")
    wanted = [ARTICLE_OF[kind]] if kind in ARTICLE_OF else []
    if [article[0] for article in found["articles"]] != wanted:
        problems.append(f"{label}: the structured data besides Organization is {[article[0] for article in found['articles']]}; "
                        f"{path} should carry {wanted or 'none'} (registry entries, spec 6.6)")
    for article_type, headline in found["articles"]:
        if headline.lower() not in (found["heading"].lower(), found["title"][0].lower()):
            problems.append(f"{label}: the {article_type} headline is {headline!r}, but the page's heading is "
                            f"{found['heading']!r} and its title {found['title'][0]!r}; the structured data should be the page's own")
    if found["marks"] != [reading_mark(path)]:
        problems.append(f"{label}: the menu marks {found['marks']}; it should mark only {reading_mark(path)} (pages.registry.menu_groups)")
    if found["layout"] != ["reading", True]:
        problems.append(f'{label}: the body reads data-layout {found["layout"][0]!r} and layout-reading {found["layout"][1]}; a reading '
                        f'page is data-layout="reading" with the class layout-reading (layouts/reading.html, registry layout)')
    if found["opacity"] != "1" or found["leaving"]:
        problems.append(f"{label}: <main> is not showing (opacity {found['opacity']})")
    if page.locator("header [data-menu-toggle]").get_attribute("aria-expanded") != "false" or not menu_gone(page):
        problems.append(f"{label}: the menu is open on a reading page at 1280px; the menu starts closed on every page and never pins "
                        f"open, so it should be closed after a change in place too (shell.js, spec 4.4)")
    return problems


def land_on(problems, page, path, label, *, in_place):
    """Wait for the page at path to show (the heading MARK_HEADING marked is gone) and, for a change in place, for shell.js to move
    focus to the new heading, the last thing it does, and for the cross-fade to end. None, with a problem, if it never comes; else
    whether the page changed in place (window.stayedPut survived, which a whole-page load does not)."""
    try:
        page.wait_for_function(SWAPPED_TO, arg=path, timeout=8000)
    except PlaywrightError:
        problems.append(f"{label}: never arrived at {path}")
        return None
    stayed = page.evaluate(STAYED)
    if in_place and stayed:
        try:
            page.wait_for_function(FOCUSED, timeout=3000)
        except PlaywrightError:
            problems.append(f"{label}: focus does not move to the new heading")
        page.wait_for_timeout(600)  # the cross-fade
    else:
        page.wait_for_load_state("networkidle")
    return stayed


def follow(problems, page, target, path, label, *, in_place=True):
    """Click the link (a locator) and wait for the page at path. A reading page changes in place to another reading page (the
    whole page must not reload) and loads whole to any other. True when the page came."""
    page.evaluate(MARK_HEADING)
    try:
        target.first.click(timeout=10000)
    except PlaywrightError as error:
        problems.append(f"{label}: could not click the link: {error.message.splitlines()[0]}")
        return False
    stayed = land_on(problems, page, path, label, in_place=in_place)
    if stayed is None:
        return False
    if in_place and not stayed:
        problems.append(f'{label}: the whole page reloaded; the shell should change to {path} in place (is it in the reading '
                        f'family, data-layout-paths in shell.html, and does it answer with data-layout="reading"?)')
    elif stayed and not in_place:
        problems.append(f"{label}: should be an ordinary page load, but the page changed in place")
    return True


def move(problems, page, direction, path, label, *, in_place=True):
    """Back or Forward, then wait for the page at path (land_on). None if it never came."""
    page.evaluate(MARK_HEADING)
    if not try_history(problems, page, direction, label):
        return None
    return land_on(problems, page, path, label, in_place=in_place)


def reading_change_problems(browser, base_url, session_key, site_page, posts):
    """The reading pages change in place among themselves and in no other way (spec 4.5, 4.6; Review Focus 2 and 5), tried once, in
    one page at 1280px: the Journal to its newest post and, if it has neighbours, to the post before it and back; to the Docs, the
    quickstart (whose tabs and Copy still work) and the Docs page after it, Back and Forward there (the quickstart comes back
    where it was scrolled), Previous to the quickstart, and Terms. Each is a change in place that leaves the menu closed, swaps the
    head as a direct load has it, moves focus to the heading and starts at the top. Then Terms to About, a chapter, is an ordinary
    page load, never fetched first, and Back from it brings Terms back."""
    problems = []
    context = new_context(browser, base_url, session_key, site_page, width=1280, height=800, theme=None, motion="on")
    page, errors = open_page(context, base_url + "/blog/")
    states = {}  # PAGE_STATE of each page when it first came, for Back and Forward

    def arrived(path, label):
        problems.extend(reading_arrival_problems(page, label, path))
        states[path] = try_state(problems, page, label)

    def restored(path, label):
        """The page showing is the one that first came at path, whole and showing."""
        now, was = try_state(problems, page, label), states.get(path)
        shown = ("path", "title", "canonical", "marks", "heading")
        if now and was and any(now[key] != was[key] for key in shown):
            problems.append(f"{label}: reads { {key: now[key] for key in shown} }, but {path} first read { {key: was[key] for key in shown} }")
        problems.extend(reading_arrival_problems(page, label, path))

    arrived("/blog/", "/blog/ loaded directly")
    if posts:
        newest = posts[0]
        label = "the Journal to its newest post in place"
        # On the Journal's page only the entries link a post, so this is the entry (a post's own "Journal" link goes to /blog/).
        if follow(problems, page, page.locator(f'main a[href="{newest}"]'), newest, label):
            arrived(newest, label)
            problems += reading_pager_problems(page, label, posts[1] if len(posts) > 1 else None, None)
            if len(posts) > 1:
                older = posts[1]
                label = "the newest post to the post before it in place"
                if follow(problems, page, pager_link(page, older), older, label):
                    arrived(older, label)
                    problems += reading_pager_problems(page, label, posts[2] if len(posts) > 2 else None, newest)
                    label = "the older post back to the newest by Next, in place"
                    if follow(problems, page, pager_link(page, newest), newest, label):
                        arrived(newest, label)
            else:
                REVIEW_NOTES.append(f"nothing to measure: the Journal has one post ({newest}), so previous and next between posts "
                                    f"and the change from one post to another were not tried")

    label = "the Docs by the menu, in place"
    set_menu(page, True)
    if follow(problems, page, page.locator('#shell-menu a[href="/docs/"]'), "/docs/", label):
        arrived("/docs/", label)

    label = "the Docs to the quickstart in place"
    # On the Docs index, the links to the quickstart are the index's own words and its entry; a Docs page's "Docs" link goes to /docs/.
    if follow(problems, page, page.locator('main a[href="/docs/quickstart/"]'), "/docs/quickstart/", label):
        arrived("/docs/quickstart/", label)
        problems += reading_tabs_problems(page, label)
        pager = pager_of(page)
        following = pager and pager["next"]
        if not following or not re.fullmatch(r"/docs/[-\w]+/", following):
            problems.append(f"{label}: the quickstart's pager has no Next link to a Docs page (it links {pager}); Docs pages end "
                            f"with previous and next by order (docs.loader.neighbours, components/pager.html)")
        else:
            link = pager_link(page, following)
            link.first.scroll_into_view_if_needed()
            page.evaluate(NEXT_FRAMES)
            left_at = page.evaluate("() => scrollY")
            label = f"the quickstart to {following} by Next, in place"
            if follow(problems, page, link, following, label):
                arrived(following, label)
                if page.evaluate("() => scrollY") > 2:
                    problems.append(f"{label}: the page does not start at the top (scrolled {page.evaluate('() => scrollY')}px)")
                pager = pager_of(page)
                if not pager or pager["previous"] != "/docs/quickstart/":
                    problems.append(f"{label}: its Previous link goes to {pager and pager['previous']}, not to the quickstart it "
                                    f"followed (docs.loader.neighbours)")
                label = f"{following} back to the quickstart by Back, in place"
                if move(problems, page, "back", "/docs/quickstart/", label) is not None:
                    restored("/docs/quickstart/", label)
                    now = page.evaluate("() => scrollY")
                    if abs(now - left_at) > 3:
                        problems.append(f"{label}: the quickstart comes back scrolled {now}px, not the {left_at}px where it was left")
                    label = f"the quickstart forward to {following} by Forward, in place"
                    if move(problems, page, "forward", following, label) is not None:
                        restored(following, label)
                label = f"{following} to the quickstart by Previous, in place"
                if follow(problems, page, pager_link(page, "/docs/quickstart/"), "/docs/quickstart/", label):
                    arrived("/docs/quickstart/", label)

    label = "Terms by the menu, in place"
    came_from = page.evaluate("() => location.pathname")
    set_menu(page, True)
    if follow(problems, page, page.locator('#shell-menu a[href="/terms/"]'), "/terms/", label):
        arrived("/terms/", label)
        if came_from in states:
            label = f"Terms back to {came_from} by Back, in place"
            if move(problems, page, "back", came_from, label) is not None:
                restored(came_from, label)
                label = "Terms forward again by Forward, in place"
                if move(problems, page, "forward", "/terms/", label) is not None:
                    restored("/terms/", label)

    label = "Terms to About, a chapter"
    fetched = []

    def note_fetch(request):
        if request.resource_type == "fetch":
            fetched.append(request.url)

    page.on("request", note_fetch)
    set_menu(page, True)
    if follow(problems, page, page.locator('#shell-menu a[href="/about/"]'), "/about/", label, in_place=False):
        if fetched:
            problems.append(f"{label}: should be an ordinary page load, never fetched first (fetched {fetched})")
        label = "About back to Terms by Back"
        if move(problems, page, "back", "/terms/", label, in_place=False) is not None:
            restored("/terms/", label)
    page.remove_listener("request", note_fetch)
    problems += [f"the reading pages changing in place: console error: {error}" for error in errors]
    context.close()
    return problems


def run_reading(browser, base_url, session_key, out):
    """The reading checks (part `reading`): each reading page at 375 and 1280 in Paper and Ink, with Motion on (at 1280 in Paper also
    the menu, the measure, the quickstart's tabs and Copy on the code blocks), then what a long column calls for
    (reading_sideways_problems, reading_sections_problems, reading_no_script_problems), and last the changes among the pages
    (reading_change_problems)."""
    pages, posts = reading_pages(browser, base_url, session_key)
    READING_RUN["pages"] = pages
    problems = []
    for site_page in pages:
        started = time.perf_counter()
        for width in WIDTHS:
            for appearance in APPEARANCES:
                label = f"{site_page.path} at {width}px, {appearance}"
                context = new_context(browser, base_url, session_key, site_page, width=width, theme=appearance, motion="on")
                page, errors = open_page(context, base_url + site_page.path)
                full_page_screenshot(page, out / f"{site_page.name}-{width}-{appearance}-motion-on.png")
                problems += page_problems(page, label)
                if appearance == "light":
                    problems += bottom_row_problems(page, label)
                    if width == 1280:
                        problems += reading_menu_problems(page, label, site_page.path) + reading_measure_problems(page, label)
                        if site_page.path == "/docs/quickstart/":
                            problems += reading_tabs_problems(page, label)
                        problems += reading_code_problems(page, label, required=site_page.path in CODE_PAGES)
                        problems += menu_problems(page, label)
                    else:
                        problems += phone_menu_problems(page, label)
                    set_menu(page, False)
                    problems += corner_problems(page, label, READING_STOPS)
                problems += [f"{label}: console error: {error}" for error in errors]
                context.close()
        problems += reading_sideways_problems(browser, base_url, session_key, site_page)
        problems += reading_sections_problems(browser, base_url, session_key, site_page)
        problems += reading_no_script_problems(browser, base_url, session_key, site_page, out)
        print(f"reading: {site_page.path} took {time.perf_counter() - started:.0f} s")
    started = time.perf_counter()
    problems += reading_change_problems(browser, base_url, session_key, pages[0], posts)
    print(f"reading: the changes among the pages took {time.perf_counter() - started:.0f} s")
    return problems


# What each part runs, in order: `all` is every part, one after the other, so a stage cannot be left out of every job
# (pages.tests_site_parts holds the split to this and to what `run` reaches).
# The order of `all` is the order of the jobs in ci.yml. The parts that draw the cloth (cloth, ink) come straight after the pages part, as before the
# later parts were added; the reading, phone, forms, catalog and dashboard parts stub the cloth out, so they come after them, each one added at the end of the line.
PART_STAGES = {"pages": ("pages",), "cloth": ("cloth",), "ink": ("ink",), "reading": ("reading",), "phone": ("phone",), "forms": ("forms",), "catalog": ("catalog",), "dashboard": ("dashboard",),
               "all": ("pages", "cloth", "ink", "reading", "phone", "forms", "catalog", "dashboard")}
PARTS = tuple(PART_STAGES)
# The checks that the pages part runs on its pages and the reading, forms and catalog parts on their own: each is one check of one page in one state, so
# CI running both is not running one check twice. The catalog part runs the pages part's own checks of a page (page_states, page_edge_problems) on the
# component catalog, which is why these are named: the checks they call are the pages part's and the catalog part's. Any other check is in one part
# only (pages.tests_site_parts reads this list).
SHARED_PAGE_CHECKS = (page_problems, sideways_problems, bottom_row_problems, menu_problems, phone_menu_problems, corner_problems,
                      no_switch_problems, tab_state_problems, install_tab_keys_problems,
                      grain_problems, labelled_link_problems, install_line_problems, motion_problems, appearance_problems, page_edge_problems)


def timed(part, check, *arguments):
    """A check of a part, timed, so that a slow run shows where its time went (the CI log has only the step's start and end otherwise): the check's problems."""
    started = time.perf_counter()
    found = check(*arguments)
    print(f"{part}: {check.__name__} took {time.perf_counter() - started:.0f} s")
    return found


def page_states(browser, base_url, session_key, out, device, first, site_page, part):
    """One page in every width, appearance and Motion: the pages part's on each page of PAGES and the catalog part's on the catalog, so that both look at a
    page in the same states. It is not named ..._problems, which pages.tests_site_parts counts as a check and allows in one part only (the checks it calls are
    in SHARED_PAGE_CHECKS). `first` is {} until the first page of the run has told what its own context shows of the pointer (webgl_precondition read the
    plain context's, `device`); the problems found."""
    problems = []
    started = time.perf_counter()
    for width in WIDTHS:
        for appearance in APPEARANCES:
            for motion in MOTIONS:
                label = f"{site_page.path} at {width}px, {appearance}, motion {motion}"
                context = new_context(browser, base_url, session_key, site_page, width=width, theme=appearance, motion=motion)
                page, errors = open_page(context, base_url + site_page.path)
                if not first:  # the same read, from inside the first check's own context: the fills that follow answer to this
                    first.update(page.evaluate(POINTER_INFO))
                    note = "" if first == device else f" (the plain context on / showed {device['pointer']}, hover: {device['hover']})"
                    print(f"Pointer in the first page check ({label}): {first['pointer']}; hover: {first['hover']}{note}")
                    pointer_stop(first, f"in the first page check ({label})")
                full_page_screenshot(page, out / f"{site_page.name}-{width}-{appearance}-motion-{motion}.png")
                problems += grain_problems(page, label, appearance)
                problems += page_problems(page, label, display_text=site_page.chapter or site_page.threshold)
                if (width, appearance, motion) in ((1280, "light", "on"), (375, "light", "on")):
                    problems += bottom_row_problems(page, label)
                if (width, appearance, motion) == (1280, "light", "on"):
                    if site_page.chapter:
                        problems += labelled_link_problems(page, label)
                    line = page.locator("[data-tabs]").first
                    if line.count() and line.is_visible():
                        problems += install_line_problems(page, line, label)
                    problems += menu_problems(page, label) + motion_problems(page, label) + appearance_problems(page, label)
                if (width, appearance, motion) == (375, "light", "on"):
                    problems += phone_menu_problems(page, label) + motion_problems(page, label) + appearance_problems(page, label)
                if (appearance, motion) == ("light", "on"):
                    if site_page.chapter and width == 1280:  # look at the corners with the menu open too
                        set_menu(page, True)
                        problems += corner_problems(page, f"{label} with the menu open")
                    set_menu(page, False)
                    problems += corner_problems(page, label)
                problems += [f"{label}: console error: {error}" for error in errors]
                context.close()
    print(f"{part}: {site_page.path} in every width, appearance and motion took {time.perf_counter() - started:.0f} s")
    return problems


def run_pages(browser, base_url, session_key, out, device):
    """The page checks (part `pages`): every page in every state, then each page's edge cases and, on the chapters, the in-place
    changes. device is what the plain context's page showed of the pointer (webgl_precondition). The phone menu sheet's own checks, the changes from it
    and the checks that run in another engine (WebKit) are the phone part's (run_phone); the pages part runs the phone menu only as each page has it
    (phone_menu_problems, the corners, the in-place changes of the chapters). The component catalog is the catalog part's (run_catalog)."""
    problems = []
    first = {}  # what the first page check's own context shows of the pointer
    for site_page in PAGES:
        problems += page_states(browser, base_url, session_key, out, device, first, site_page, "pages")
        problems += timed("pages", edge_case_problems, browser, base_url, session_key, site_page, out)
    problems += timed("pages", grain_scale_problems, browser, base_url, session_key)
    problems += timed("pages", code_block_problems, browser, base_url, session_key)
    problems += timed("pages", corner_type_problems, browser, base_url, session_key)
    problems += timed("pages", ordinary_fade_problems, browser, base_url, session_key)
    return problems


def run_catalog(browser, base_url, session_key, out, device):
    """The component catalog's page checks (part `catalog`): what the pages part does for each page of PAGES that is not the threshold's or a chapter's, on the
    catalog alone: the page in every width, appearance and Motion (page_states), then its edge cases (page_edge_problems). It is a part of its own for time: the
    catalog shows every component in its states, and its two steps took 159 s and 371 s on CI (73 s and 158 s before the form components), which brought
    the pages part to 19.8 of its 20 minutes."""
    problems = []
    first = {}
    for site_page in CATALOG_PAGES:
        problems += page_states(browser, base_url, session_key, out, device, first, site_page, "catalog")
        problems += timed("catalog", page_edge_problems, browser, base_url, session_key, site_page, out)
    return problems


def run_phone(browser, base_url, session_key, playwright):
    """The phone menu's checks (part `phone`), each timed: the sheet's own opening and closing in Chromium (phone_sheet_problems, sheet_scrolled_close_problems),
    the changes chosen from the open sheet (sheet_change_problems, sheet_change_edge_problems, sheet_change_reach_problems), the faces a change waits for or does
    not (font_change_problems), and the sheet in WebKit, the engine of every browser on an iPhone (webkit_sheet_problems: the sheet's fade, a tap and Escape, the
    changes from it and the faces again). The sheet's own checks are here, not in the pages part, to keep the sheet's two engines and its changes in the job that
    is re-read when the sheet is touched, and because the pages part is the longer job without them (estimated for CI from the 17.7 minutes the pages step took with
    everything in it: about 10 to 11 minutes against 7 to 9). playwright starts WebKit."""
    problems = []
    problems += timed("phone", phone_sheet_problems, browser, base_url, session_key)
    problems += timed("phone", sheet_scrolled_close_problems, browser, base_url, session_key)
    problems += timed("phone", sheet_change_problems, browser, base_url, session_key)
    problems += timed("phone", sheet_change_edge_problems, browser, base_url, session_key)
    problems += timed("phone", sheet_change_reach_problems, browser, base_url, session_key)
    problems += timed("phone", font_change_problems, browser, base_url, session_key)
    problems += timed("phone", webkit_sheet_problems, playwright, base_url)
    return problems


# The forms part (CI's job `site-forms`): every form and error page in each state it has. The dev server (DEBUG on) answers the generic 404 and the 500 with Django's
# technical pages, so make_form_fixtures.py draws those two with DEBUG off and drive_baked serves them.
FORM_SCREENS = ((1280, 900), (375, 667), (320, 640))
KEYBOARD_SCREENS = ((375, 380), (320, 300))   # a phone with its on-screen keyboard up
ROOM_TO_SCROLL = "body::after { content: ''; display: block; height: 100vh; }"   # a short page can then be scrolled far enough to put a field under the top fade
UNDER_THE_FADE = 20   # px: a field is put with its top this far above where the top fade ends, so that it is part under it and part clear
FORM_NAV_MS = 15000   # the longest a submission is waited for: a bound, never a delay
FORM_TRIES = 14   # the most requests it takes to reach a throttle
STALE = ("an earlier run of this part may have used up the server's throttles (the sign-in, code and reset limits live in its memory) or the fixtures' device codes: "
         "restart the server and run make_form_fixtures.py again")
TEXT_FIELDS = "main input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button]), main select, main textarea"
AUTOCOMPLETE = {"username": "username", "email": "email", "password": "current-password", "password1": "new-password", "password2": "new-password",
                "new_password1": "new-password", "new_password2": "new-password", "user_code": "off"}
FRAME_STALL = ("(() => { const hold = () => { const began = performance.now(); while (performance.now() - began < " + str(SLOW_EVERY_FRAME_MS)
               + "); requestAnimationFrame(hold); }; requestAnimationFrame(hold); })();")   # every frame held, as the phone part's probe holds them
NOVALIDATE = "() => document.querySelector('main form[method=post]').setAttribute('novalidate', '')"   # the browser's own check of required fields, switched off
FORM_READY = "() => !document.documentElement.classList.contains('js') || document.documentElement.classList.contains('shell-ready')"   # shell.js has run, or there is none
FORM_PAGE = """() => {
  const words = (e) => e.textContent.replace(/\\s+/g, ' ').trim(), main = document.querySelector('main') || document;
  const fields = [...main.querySelectorAll('input, select, textarea')].filter((f) => !['hidden', 'submit', 'button', 'image', 'reset'].includes(f.type));
  return { alerts: [...main.querySelectorAll('[role=alert]')].map(words),
    headings: [...document.querySelectorAll('h1')].map((h) => ({ inMain: Boolean(h.closest('main')), words: words(h) })),
    buttons: [...main.querySelectorAll('button, a.action, input[type=submit], input[type=button]')].map((b) => ({ words: words(b) || b.value || b.name, interactive: b.classList.contains('interactive') })),
    errorBoxes: [...main.querySelectorAll('[id$=_error]')].map((e) => e.id),
    fields: fields.map((f) => ({ name: f.name || f.id, shown: f.getClientRects().length > 0, size: parseFloat(getComputedStyle(f).fontSize),
      textual: !['checkbox', 'radio'].includes(f.type), invalid: f.getAttribute('aria-invalid') === 'true', autocomplete: f.getAttribute('autocomplete') || '',
      labelled: [...f.labels].some((l) => words(l) && l.getClientRects().length > 0),
      described: (f.getAttribute('aria-describedby') || '').split(/\\s+/).filter(Boolean).map((id) => {
        const m = document.querySelectorAll('[id=' + JSON.stringify(id) + ']'); return m.length === 1 && words(m[0]) ? id : '!' + id; }) })) }; }"""
FORM_BARE = """() => ({ title: document.title.trim(), lang: document.documentElement.lang, viewport: Boolean(document.querySelector('meta[name="viewport"]')),
  h1: document.querySelectorAll('h1').length, files: document.querySelectorAll('script[src], link[rel~="stylesheet"]').length, styles: document.querySelectorAll('style').length })"""
# Field `index` of TEXT_FIELDS put `at` px down the window (null: not moved; otherwise whatever has focus is blurred first, because focus() on the field that already has it scrolls
# nothing), and where it lies in a window `view` high.
FIELD_PLACE = """([selector, index, at]) => { const f = document.querySelectorAll(selector)[index];
  if (at !== null) { document.activeElement.blur(); window.scrollTo(0, Math.max(0, f.getBoundingClientRect().top + window.scrollY - at)); }
  const b = f.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, view: innerHeight }; }"""
# The top fade's height as the tokens set it.
FADE_HEIGHT = """() => { const probe = document.createElement('div'); probe.style.cssText = 'position:absolute;visibility:hidden;height:calc(var(--fade-top) + var(--fade-band))';
  document.body.append(probe); const height = probe.getBoundingClientRect().height; probe.remove(); return height; }"""
WATCH_SUBMIT = """(selector) => { window.__submitted = null; document.querySelector(selector).form.addEventListener('submit', (event) => { event.preventDefault();
  const by = event.submitter; window.__submitted = { by: by ? by.outerHTML.slice(0, 100) : null, ok: Boolean(by) && by.classList.contains('action') && !by.hasAttribute('formnovalidate') }; }, { once: true }); }"""
# The page's rules on the browser's autofill, and whether each holds the field's look: a transition of 1000 s or more, or an inset shadow.
AUTOFILL_RULES = """() => { const found = []; const walk = (rules) => { for (const r of rules) { if (r.cssRules && !r.selectorText) walk(r.cssRules);
    else if (r.selectorText && /:(-webkit-)?autofill/.test(r.selectorText)) found.push({ selector: r.selectorText, fill: r.style.getPropertyValue('-webkit-text-fill-color'),
      held: [...(r.style.getPropertyValue('transition') + ' ' + r.style.getPropertyValue('transition-duration')).matchAll(/(\\d+(?:\\.\\d+)?)s\\b/g)].some((m) => Number(m[1]) >= 1000),
      inset: r.style.getPropertyValue('box-shadow').includes('inset') }); } };
  for (const sheet of document.styleSheets) { try { walk(sheet.cssRules); } catch (error) { /* another origin */ } } return found; }"""


# The element that has focus, the first field marked aria-invalid (in the order of the page) and the page's error summary; `focused` names the focused element as an id (the summary has none: "the error
# summary"), and is empty when focus is on the page's top. `ring` is how the focused element is drawn: whether :focus-visible matches it, its outline and its box (`grow`: how far the outline
# reaches beyond the box), with the window's size.
FOCUS_AND_FIRST_INVALID = """() => { const focus = document.activeElement, invalid = document.querySelector('main [aria-invalid=true]'), summary = document.querySelector('main [role=alert]'),
  on = focus && focus !== document.body ? focus : null, box = on ? on.getBoundingClientRect() : null, look = on ? getComputedStyle(on) : null;
  return { focused: !on ? '' : on === summary ? 'the error summary' : on.id || on.name || on.tagName.toLowerCase(), first: invalid ? (invalid.id || invalid.name) : '', summary: Boolean(summary),
    ring: on ? { match: on.matches(':focus-visible'), style: look.outlineStyle, width: parseFloat(look.outlineWidth), grow: parseFloat(look.outlineOffset) + parseFloat(look.outlineWidth),
      left: box.left, right: box.right, top: box.top, bottom: box.bottom, view: { width: innerWidth, height: innerHeight } } : null }; }"""
# What may set one secondary link off from the next ("Log in · Forgot password?"), and each such mark of every `.form-links` paragraph with the characters on its line, read from
# the line box of each character (Range.getClientRects): a wrap is not a node of the page. `last` says that no other character follows the mark on its line.
LINK_SEPARATORS = "\u00b7\u2022|"
FORM_LINK_LINES = """(separators) => [...document.querySelectorAll('main .form-links')].flatMap((links) => {
  const marks = [], walker = document.createTreeWalker(links, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    for (let at = 0; at < node.data.length; at++) {
      if (!node.data[at].trim()) continue;
      const range = document.createRange(); range.setStart(node, at); range.setEnd(node, at + 1);
      const box = range.getClientRects()[0];
      if (box) marks.push({ ch: node.data[at], left: box.left, right: box.right, top: box.top, bottom: box.bottom, separator: separators.includes(node.data[at]) });
    }
  }
  const sameLine = (a, b) => { const middle = (b.top + b.bottom) / 2; return middle > a.top && middle < a.bottom; };
  return marks.filter((mark) => mark.separator).map((mark) => {
    const line = marks.filter((other) => sameLine(mark, other)).sort((a, b) => a.left - b.left);
    return { ch: mark.ch, last: !line.some((other) => !other.separator && other.left >= mark.right - 1), line: line.map((other) => other.ch).join('') };
  });
})"""


def load_form_fixtures(path):
    """What make_form_fixtures.py wrote, or a stop that says how to make it."""
    if not path.is_file():
        raise SystemExit(f"{path} is missing: the forms part needs the fixtures that site-tests/make_form_fixtures.py writes beside the session file (AGENTS.md, Site checks).")
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class FormState:
    """One state of one page: name (the screenshots' too), path ({device[consent]} and the like come from the fixtures), who ("", "staff" or a fixtures session), how (a driver of
    FORM_DRIVERS), status, expect ("blank", "field", "alert" or "any"), enter (a field whose form Enter submits: the keyboard checks run on it), bare (the 500),
    separated (its secondary links hold a separator, which forms_links_problems must find), mark (its page draws the approved login's check mark in CSS, which
    forms_mark_problems must find drawn), theme ("light", the default, is Paper; "dark" is Ink) and spends (uses a throttle up: comes last)."""
    name: str
    path: str = ""
    who: str = ""
    how: str = "open"
    status: int = 200
    expect: str = "any"
    enter: str = ""
    bare: bool = False
    separated: bool = False
    mark: bool = False
    theme: str = "light"
    spends: bool = False


def form_session(who, session_key, fixtures):
    return "" if not who else session_key if who == "staff" else fixtures["sessions"][who]


def form_context(browser, base_url, key, path, name, theme="light", **options):
    """Paper (theme "light") or Ink ("dark"), Motion on, the session cookie `key` names (none when empty), a bound on every wait."""
    context = new_context(browser, base_url, key, SitePage(path or "/", name, signed_in=bool(key)), theme=theme, motion="on", **options)
    context.set_default_timeout(FORM_NAV_MS)
    return context


def fresh_page(context):
    """A page that collects the console's errors and every file it asked for and could not have."""
    page, errors = context.new_page(), []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" and "Failed to load resource" not in message.text else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("response", lambda response: errors.append(f"{response.url} answered {response.status}")
            if response.status >= 400 and response.request.resource_type != "document" and not response.url.endswith("/favicon.ico") else None)
    return page, errors


def set_screen(page, width, height):
    """Resize the window; wait for the page to lay itself out."""
    page.set_viewport_size({"width": width, "height": height})
    page.wait_for_function("(width) => innerWidth === width", arg=width)
    page.evaluate(NEXT_FRAMES)
    wait_for_state(page, FORM_READY)


def submitted(page, press=None, click=None):
    """Submit as a visitor does (Enter in a field, or a click); the response of the page that comes."""
    with page.expect_navigation(timeout=FORM_NAV_MS) as navigation:
        if click is not None:
            click.click()
        else:
            press.press("Enter")
    page.wait_for_load_state("networkidle")
    return navigation.value


def fill_fields(form):
    """Every text field of the form gets a value its type takes; a required box is ticked."""
    for field in form.locator(TEXT_FIELDS.replace("main ", "")).all():
        if field.is_visible():
            kind, name = field.get_attribute("type") or "text", field.get_attribute("name")
            field.fill("someone@example.com" if kind == "email" else "pw-site-checks-1" if kind == "password" else "ABCD-EFGH" if name == "user_code" else "site checks")
    for box in form.locator("input[type=checkbox][required]").all():
        box.check()


def drive_open(page, base_url, fixtures, state, problems):
    return page.goto(base_url + state.path.format(**fixtures), wait_until="networkidle")


def drive_baked(page, base_url, fixtures, state, problems):
    """The 404 or the 500 as make_form_fixtures.py drew it, served at its address."""
    baked = fixtures["errors"][state.path]
    page.context.route(base_url + baked["path"], lambda route: route.fulfill(status=baked["status"], headers=baked["headers"], body=baked["body"]))
    return page.goto(base_url + baked["path"], wait_until="networkidle")


def filler(values, press, clear_cookie=False):
    """A driver: open the page, type `values` ({run} comes from the fixtures), press Enter in the field `press`."""
    def drive(page, base_url, fixtures, state, problems):
        drive_open(page, base_url, fixtures, state, problems)
        if clear_cookie:
            page.context.clear_cookies(name="csrftoken")   # a form left open past its cookie
        for name, value in values.items():
            page.fill(f"main input[name={name}]", value.format(**fixtures))
        for box in page.locator("main input[type=checkbox][required]").all():
            box.check()
        return submitted(page, press=page.locator(f"main input[name={press}]"))
    return drive


def approving(tick):
    """A driver for the approval page: tick the confirmation, or not (the browser's own check is then switched off), press Approve."""
    def drive(page, base_url, fixtures, state, problems):
        drive_open(page, base_url, fixtures, state, problems)
        if tick:
            page.locator("main input[name=confirm]").check()
        else:
            page.evaluate(NOVALIDATE)
        return submitted(page, click=page.locator("main button.action").first)   # Approve: the first of the form's actions
    return drive


def denying(page, base_url, fixtures, state, problems):
    """The approval page's Deny: the quiet second action, which posts to its own address and skips the browser's check of the confirmation."""
    drive_open(page, base_url, fixtures, state, problems)
    return submitted(page, click=page.locator("main button.action", has_text="Deny"))


def throttler(post):
    """A driver that uses a throttle up with requests of the page's own cookies and token until a 429 comes, then asks once in the page."""
    def drive(page, base_url, fixtures, state, problems):
        target = base_url + state.path.format(**fixtures)
        page.goto(target, wait_until="networkidle")
        token = page.locator("main input[name=csrfmiddlewaretoken]").first.input_value() if post else ""
        form = {key: value.format(**fixtures) for key, value in (post or {}).items()}
        for _ in range(FORM_TRIES):
            reply = page.request.post(target, form={"csrfmiddlewaretoken": token, **form}) if post else page.request.get(target)
            if reply.status == 429:
                break
        else:
            problems.append(f"{state.name}: the throttle never answered 429 in {FORM_TRIES} requests")
            return None
        if not post:
            return page.goto(target, wait_until="networkidle")
        for name, value in form.items():
            page.fill(f"main input[name={name}]", value)
        page.evaluate(NOVALIDATE)   # the sign-up page has more required fields than the throttle's request names
        return submitted(page, press=page.locator(f"main input[name={list(form)[-1]}]"))
    return drive


def drive_verify_resend(page, base_url, fixtures, state, problems):
    drive_open(page, base_url, fixtures, state, problems)
    return submitted(page, click=page.locator("main form:has(input[value=resend]) button"))


FORM_DRIVERS = {
    "open": drive_open, "baked": drive_baked, "verify_resend": drive_verify_resend,   # a resend by a user whose last link went just now is too soon: no mail is sent
    "login_failed": filler({"username": "site-nobody-{run}", "password": "not-the-password-1"}, "password"),
    "signup_errors": filler({"username": "site-signup-{run}", "email": "a@b", "password1": "pw1", "password2": "pw2"}, "password2"),   # a@b: a browser takes it, Django does not
    "reset_error": filler({"email": "a@b"}, "email"), "reset_done": filler({"email": "forms-{run}@example.com"}, "email"),   # no account has it: nothing is mailed
    "reset_confirm_errors": filler({"new_password1": "pw1", "new_password2": "pw2"}, "new_password2"),
    "verify_errors": filler({"email": "a@b", "password": "not-the-password-1"}, "password"), "csrf": filler({"username": "x", "password": "y"}, "password", clear_cookie=True),
    "approve": approving(True), "approve_without": approving(False), "deny": denying,
    "login_throttled": throttler({"username": "site-throttle-{run}", "password": "not-the-password-1"}), "signup_throttled": throttler({"username": "site-throttle-{run}"}),
    "reset_throttled": throttler({"email": "throttle-{run}@example.com"}), "device_throttled": throttler(None),
}

# Every page in every state it has, in the order they are looked at (the states that spend a throttle's budget come last, whatever their place here).
FORM_STATES = (
    FormState("login", "/accounts/login/", expect="blank", enter="main input[name=password]"),
    FormState("login-failed", "/accounts/login/", how="login_failed", expect="alert"),
    FormState("login-failed-ink", "/accounts/login/", how="login_failed", expect="alert", theme="dark"),   # the stored appearance is Ink: Paper's states are the others
    FormState("signup", "/accounts/signup/", expect="blank", enter="main input[name=password2]"),
    FormState("signup-errors", "/accounts/signup/", how="signup_errors", expect="field", enter="main input[name=password2]"),
    FormState("reset", "/accounts/password_reset/", expect="blank", enter="main input[name=email]"),
    FormState("reset-error", "/accounts/password_reset/", how="reset_error", expect="field"),
    FormState("reset-done", "/accounts/password_reset/", how="reset_done"),
    FormState("reset-confirm", "{reset[link]}", expect="blank", enter="main input[name=new_password2]"),
    FormState("reset-confirm-errors", "{reset[link]}", how="reset_confirm_errors", expect="field"),
    FormState("verify-done", "{verify[link]}"),
    FormState("verify-email-invalid", "/accounts/verify-email/site-checks-not-a-token/"),   # a link that does not parse: the page says nothing of why
    FormState("reset-confirm-dead", "/accounts/reset/MQ/site-checks-0000000000000000000/"),   # a link whose token is no token: "invalid or has expired"
    FormState("reset-complete", "/accounts/reset/done/"),
    FormState("reset-complete-signed-in", "/accounts/reset/done/", who="verified"),
    FormState("link-missing", "/a/site-checks-missing-9f2c", status=404),
    FormState("not-found", "not_found", how="baked", status=404),
    FormState("not-found-ink", "not_found", how="baked", status=404, theme="dark"),
    FormState("server-error", "server_error", how="baked", status=500, bare=True),
    FormState("csrf-failure", "/accounts/login/", how="csrf", status=403),
    FormState("verify-email", "/accounts/verify-email/", who="staff", expect="blank", enter="main input[name=password]"),   # the staff login: no record
    FormState("device-approve-unverified", "/device?user_code={device[consent]}", who="staff", how="approve", status=403),
    FormState("oauth-unverified", "{oauth[authorize]}", who="staff", status=403),
    FormState("forbidden", "/admin/auth/user/", who="staff", status=403),
    FormState("verify-pending", "/accounts/verify-email/?sent=1", who="pending", expect="blank"),   # asked for a link a moment ago; the longest address
    FormState("verify-errors", "/accounts/verify-email/", who="pending", how="verify_errors", expect="field"),
    FormState("checkout", "/accounts/signup/?plan=team", who="verified"),
    FormState("device", "/device", who="verified", expect="blank", enter="main input[name=user_code]"),
    FormState("device-wrong", "/device?user_code={device[unknown]}", who="verified", expect="field"),
    FormState("device-expired", "/device?user_code={device[expired]}", who="verified", status=410),
    FormState("device-consent", "/device?user_code={device[consent]}", who="verified"),
    FormState("device-consent-ink", "/device?user_code={device[consent]}", who="verified", theme="dark"),
    FormState("device-refused", "/device?user_code={device[consent]}", who="verified", how="approve_without", status=400, expect="alert"),
    FormState("device-approved", "/device?user_code={device[approve]}", who="verified", how="approve", mark=True),   # uses its own code up
    FormState("device-denied", "/device?user_code={device[deny]}", who="verified", how="deny"),
    FormState("oauth-consent", "{oauth[authorize]}", who="verified"),
    FormState("oauth-unknown-client", "{oauth[unknown]}", who="verified", status=400),
    FormState("device-consent-long", "/device?user_code={device[long]}", who="long"),   # the longest username, address, key name and user agent
    FormState("oauth-consent-long", "{oauth[long]}", who="long"),
    FormState("login-throttled", "/accounts/login/", how="login_throttled", status=429, separated=True, spends=True),
    FormState("signup-throttled", "/accounts/signup/", how="signup_throttled", status=429, separated=True, spends=True),
    FormState("reset-throttled", "/accounts/password_reset/", how="reset_throttled", status=429, separated=True, spends=True),
    FormState("device-throttled", "/device?user_code={device[unknown]}", who="verified", how="device_throttled", status=429, spends=True),
    FormState("verify-throttled", "/accounts/verify-email/", who="pending", how="verify_resend", status=429, spends=True),
)


def forms_page_problems(page, label, state, phone=False):
    """One h1 in <main>, a visible label and the old autocomplete token on every field, every error tied to its field and listed after the help, the ink wash on every button, a blank
    page saying nothing is wrong; (phone) 16 px text."""
    found, problems = page.evaluate(FORM_PAGE), []
    if phone:
        return [f"{label}: the {f['name']} field's text is {f['size']:g}px; under 16px a phone zooms in when it takes focus (static/css/components/field.css)"
                for f in found["fields"] if f["shown"] and f["textual"] and f["size"] < 16]
    headings = found["headings"]
    if len(headings) != 1 or not headings[0]["inMain"] or not headings[0]["words"]:
        problems.append(f"{label}: the page should have one non-empty <h1>, inside <main> (spec 9); it has {headings} (the page's template, once, in {{% block form_page %}})")
    problems += [f"{label}: the {b['words']!r} button should be .interactive, or it answers neither the cursor nor a press with the ink wash (class=\"action interactive\" or "
                f"\"label interactive\" in the page's template)" for b in found["buttons"] if not b["interactive"]]
    referred = {name for f in found["fields"] for name in f["described"]}
    for f in (f for f in found["fields"] if f["shown"]):
        wanted = AUTOCOMPLETE.get(f["name"])
        kinds = [d.rsplit("_", 1)[-1] for d in f["described"]]   # the ids end _help or _error: the help is named first
        for bad, why in ((not f["labelled"], "has no visible label"), (wanted and f["autocomplete"] != wanted, f"has autocomplete {f['autocomplete']!r}, not {wanted!r}"),
                         (sorted(kinds, key=lambda kind: kind == "error") != kinds, "lists its error before its help in aria-describedby"),
                         (any(d.startswith("!") for d in f["described"]), "is described by an id that is missing, repeated or empty"),
                         (f["invalid"] and not f["described"], "is aria-invalid and nothing describes it")):
            problems += [f"{label}: the {f['name']} field {why} (components/field.html, the page's template)"] if bad else []
    if [box for box in found["errorBoxes"] if box not in referred]:
        problems.append(f"{label}: an error sentence belongs to no field's aria-describedby, so a screen reader never reads it with the field")
    marked = [f["name"] for f in found["fields"] if f["invalid"]]
    if state.expect == "blank" and (marked or any(found["alerts"]) or found["errorBoxes"]):
        problems.append(f"{label}: a page nobody has filled in says something is wrong: {marked or found['alerts'] or found['errorBoxes']}")
    if state.expect == "field" and not (marked and found["errorBoxes"]):
        problems.append(f"{label}: the page should mark a field aria-invalid and say what is wrong under it; it marks {marked}")
    if state.expect == "alert" and not any(found["alerts"]):
        problems.append(f"{label}: the page should say what is wrong in a role=\"alert\" sentence (components/form_errors.html)")
    return problems


def forms_bare_problems(page, label, response, requests):
    """The 500 asks for no file: it must render when nothing else can."""
    found = page.evaluate(FORM_BARE)
    others = [url for url in requests if url != response.url and not url.endswith("/favicon.ico")]
    bad = others or found["files"] or not found["styles"] or found["h1"] != 1 or not (found["title"] and found["lang"] and found["viewport"])
    return [f"{label}: the 500 asked for {others[:3]} and has {found}; it should ask for no file and have inline CSS, a title, lang, viewport and one h1 (templates/500.html)"] if bad else []


def forms_focus_problems(page, label):
    """With the on-screen keyboard up (a window as high as what is left) a field put partly under the top fade and focused lands clear of it, above the keyboard. The input
    is what is measured: Chromium's focus() scrolls only when the input is not wholly below scroll-padding-top, and then leaves scroll-margin-top clear above it, so a label above
    an input that was already in view may stay in the fade."""
    fields, problems, tried = page.locator(TEXT_FIELDS), [], 0
    page.add_style_tag(content=ROOM_TO_SCROLL)
    for width, height in KEYBOARD_SCREENS if fields.count() else ():
        set_screen(page, width, height)
        here, fade = f"{label} at {width}x{height}px, the keyboard up", page.evaluate(FADE_HEIGHT)
        if fade <= 0:
            problems.append(f"{here}: --fade-top and --fade-band (tokens.css) give no top fade")
            continue
        for index in range(fields.count()):
            if fields.nth(index).is_visible():
                tried += page.evaluate(FIELD_PLACE, [TEXT_FIELDS, index, fade - UNDER_THE_FADE])["top"] < fade
                page.evaluate(NEXT_FRAMES)
                fields.nth(index).focus()
                page.evaluate(SCROLL_SETTLED)
                place = page.evaluate(FIELD_PLACE, [TEXT_FIELDS, index, None])
                if place["top"] < fade - 1 or place["bottom"] > place["view"] + 1:
                    problems.append(f"{here}: field {index + 1} took focus with its top at {place['top']:.0f}px and its bottom at {place['bottom']:.0f}px: under the top fade (ends {fade:.0f}px; "
                                    f"scroll-padding-top in base.css) or below the keyboard ({place['view']}px)")
    if fields.count() and not tried:
        REVIEW_NOTES.append(f"nothing to measure: no field of {label} could be put under the top fade")
    return problems


def forms_enter_problems(page, label, selector):
    """Enter in the field submits the form by its main action (a quiet Deny first would take it). The submit is stopped."""
    fill_fields(page.locator(selector).first.locator("xpath=ancestor::form[1]"))
    page.evaluate(WATCH_SUBMIT, selector)
    page.locator(selector).first.press("Enter")
    if not wait_for_state(page, "() => window.__submitted !== null", timeout=5000):
        return [f"{label}: Enter in {selector} did not submit the form (a required field is empty, or there is no submit button)"]
    seen = page.evaluate("() => window.__submitted")
    return [] if seen["ok"] else [f"{label}: Enter submitted the form by {seen['by']}; the form's first submit button should be its main action (.action), never one that skips the browser's checks"]


def forms_autofill_problems(page, label):
    """The browser's autofill colours are !important, so the page holds the field's look: a rule on each of :autofill and :-webkit-autofill keeps the text --ink under a
    transition that never ends (or an inset shadow), and one paints the field paper with Motion off. Playwright cannot make Chromium autofill a field: the rules are read."""
    rules, problems = page.evaluate(AUTOFILL_RULES), []
    for spelling in (":autofill", ":-webkit-autofill"):
        mine = [r for r in rules if "data-motion" not in r["selector"] and re.search(spelling.replace("-", r"\-") + r"(?![\w-])", r["selector"])]
        if not mine or any(not r["fill"] or not (r["held"] or r["inset"]) for r in mine):
            problems.append(f"{label}: no rule on {spelling} keeps the text --ink and holds the field's look (a long transition or an inset shadow): the browser paints its colour (static/css/components/field.css)")
    if not any(r["inset"] for r in rules if "data-motion" in r["selector"]):
        problems.append(f"{label}: no rule paints an autofilled field paper with Motion off, where every transition is off (static/css/components/field.css)")
    return problems


def forms_error_focus_problems(page, label):
    """After a submit that came back with errors, focus is where the server put it: on the first field marked aria-invalid (the server renders autofocus on it, replacing the page's own), not at the top
    of the page and not on another field, so that a visitor who cannot see the page hears the field and its error where the problem is; and, when no field is in error and the page has only the
    form's own sentence (a failed sign-in, a refused approval), on the error summary (components/form_errors.html: tabindex -1 and autofocus, passed by the page), because an alert that is on
    the page as it loads is announced inconsistently. The focused summary shows the site's focus outline (`:focus-visible`, base.css) and lies in the window, clear of the top fade. Read at each
    width of the state, with nothing having moved focus."""
    seen = page.evaluate(FOCUS_AND_FIRST_INVALID)
    if seen["first"]:
        if seen["focused"] != seen["first"]:
            return [f"{label}: after the submit focus is on {seen['focused'] or 'the top of the page'}, not on the first field in error, {seen['first']} (the page's template: components/field.html `focus`, "
                    f"templatetags/shell.py focus_field)"]
        return []
    if not seen["summary"]:
        return [f"{label}: the page marks no field aria-invalid and has no role=alert summary, so there is nothing in error to focus"]
    if seen["focused"] != "the error summary":
        return [f"{label}: after the submit focus is on {seen['focused'] or 'the top of the page'}, not on the error summary, the only thing in error (the page's template passes focus=... to "
                f"components/form_errors.html, which prints tabindex=\"-1\" and autofocus; templatetags/shell.py focus_summary)"]
    ring, problems = seen["ring"], []
    fade = page.evaluate(FADE_HEIGHT)
    if not ring["match"] or ring["style"] == "none" or ring["width"] < 1:
        problems.append(f"{label}: the error summary has focus and shows none (:focus-visible matches {ring['match']}, outline {ring['style']} {ring['width']:g}px): a sighted keyboard visitor cannot see "
                        f"where focus went (static/css/base.css `:focus-visible`, static/css/components/form_errors.css)")
    if ring["left"] - ring["grow"] < 0 or ring["right"] + ring["grow"] > ring["view"]["width"]:
        problems.append(f"{label}: the focused error summary's outline reaches {ring['left'] - ring['grow']:.0f} to {ring['right'] + ring['grow']:.0f}px of a {ring['view']['width']}px window")
    if ring["top"] < fade or ring["bottom"] > ring["view"]["height"]:
        problems.append(f"{label}: the focused error summary lies from {ring['top']:.0f} to {ring['bottom']:.0f}px of a {ring['view']['height']}px window, with the top fade ending at {fade:.0f}px: "
                        "it is under the fade or below the fold, so focus is not where it can be seen")
    return problems


MARK_BOX = """() => { const mark = document.querySelector('.device-mark'); if (!mark) return null; const style = getComputedStyle(mark), box = mark.getBoundingClientRect();
  return { count: document.querySelectorAll('.device-mark').length, hidden: mark.getAttribute('aria-hidden') === 'true', left: box.left, right: box.right, width: box.width, height: box.height,
    right_edge: [style.borderRightStyle, parseFloat(style.borderRightWidth)], bottom_edge: [style.borderBottomStyle, parseFloat(style.borderBottomWidth)] }; }"""
MARK_SPREAD = 0.1   # the least the mark's box may vary in luma: two 2px ink lines on paper in its box read 0.22; an empty box (a border that did not draw) 0.01


def forms_theme_problems(page, state):
    """The page is drawn in the appearance its state names: an Ink state that opened in Paper would pass every check of the wrong page."""
    shown = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
    return [] if shown == state.theme else [f"{state.name}: the state is drawn in {'Ink' if state.theme == 'dark' else 'Paper'} (data-theme {state.theme!r}) and the page says {shown!r} "
                                             "(the boot script in templates/shell.html reads the stored choice)"]


def forms_mark_problems(page, label):
    """The approved page's check mark is drawn in CSS (static/css/pages/device.css: two borders turned an eighth of the way round, since the character is in none of the site's fonts):
    one, aria-hidden, with its two borders solid and in the viewport, and ink in the box a screenshot of it shows."""
    found = page.evaluate(MARK_BOX)
    if found is None or found["count"] != 1:
        return [f"{label}: the page should draw the approved login's one check mark, a .device-mark (templates/device/device_success.html, static/css/pages/device.css)"]
    problems = [] if found["hidden"] else [f"{label}: the check mark is decoration, so it is aria-hidden (the heading says the same in words)"]
    if found["right_edge"][0] != "solid" or found["bottom_edge"][0] != "solid" or min(found["right_edge"][1], found["bottom_edge"][1]) < 1:
        problems.append(f"{label}: the check mark's right and bottom borders are {found['right_edge']} and {found['bottom_edge']}; they are what draws it (static/css/pages/device.css)")
    if found["left"] < 0 or found["right"] > page.viewport_size["width"]:
        problems.append(f"{label}: the check mark lies from {found['left']:.0f} to {found['right']:.0f}px, outside the {page.viewport_size['width']}px window")
    mean, spread = png_luma(page.locator(".device-mark").screenshot())
    if spread < MARK_SPREAD:
        problems.append(f"{label}: the check mark's box shows no ink (spread of luma {spread:.3f}, at least {MARK_SPREAD} when it is drawn): its borders do not paint (static/css/pages/device.css)")
    return problems


def forms_links_problems(page, label, separated=False):
    """No line of a `.form-links` paragraph ends with its separator: a dot left at the end of a line belongs to the link after it, which went down. (separated: the page must have one,
    or the check measured nothing.)"""
    marks = page.evaluate(FORM_LINK_LINES, LINK_SEPARATORS)
    problems = [f"{label}: a line of the secondary links ends with the separator {mark['ch']!r} (the line is {mark['line']!r}): keep it with the link after it, in one `.form-links__item`, or "
                f"leave the separator out where the links stack (static/css/base.css, the page's template)" for mark in marks if mark["last"]]
    if separated and not marks:
        problems.append(f"{label}: the secondary links should have a separator ({LINK_SEPARATORS[0]!r}) and the page has none, so nothing was measured (the throttled page's sign-in links, templates/registration/throttled.html)")
    return problems


def forms_sign_out_problems(page, label):
    """A form page or an error page is never a page of the dashboard, whoever is signed in: its menu has no Sign out button and no form. The button and its token belong to the
    dashboard's sections and the pages that hang off them (spec 4.4); the throttled pages, the 403s and the 404 of a signed-in visitor reach here."""
    found = page.evaluate("""() => ({ parts: document.querySelectorAll('#shell-menu form, #shell-menu button').length,
        words: Boolean(document.querySelector('#shell-menu')) && document.querySelector('#shell-menu').textContent.includes('Sign out') })""")
    if found["parts"] or found["words"]:
        return [f"{label}: the page's menu has {found['parts']} forms or buttons and {'says' if found['words'] else 'does not say'} Sign out; only a page of the dashboard has the button and its token, "
                f"and a form or error page draws the ordinary menu (pages/errors.py and drop_core/throttle.py pass plain_menu)"]
    return []


def forms_state_problems(browser, base_url, session_key, fixtures, state, out):
    """One state at 1280, 375 and 320 px: the page's checks, nothing sideways, axe (not at 320); with fields, the keyboard checks."""
    problems, requests = [], []
    context = form_context(browser, base_url, form_session(state.who, session_key, fixtures), state.path, state.name, state.theme, width=1280, height=900)
    page, errors = fresh_page(context)
    page.on("request", lambda request: requests.append(request.url))
    try:
        response = FORM_DRIVERS[state.how](page, base_url, fixtures, state, problems)
        if response is None:
            return problems
        if response.status != state.status:
            return problems + [f"{state.name}: the page answered {response.status}, not {state.status}" + (f"; {STALE}" if response.status == 429 else "")]
        wait_for_state(page, FORM_READY)
        problems += forms_theme_problems(page, state) + forms_sign_out_problems(page, state.name)
        if state.expect in ("field", "alert"):
            for width, height in FORM_SCREENS:   # first, before a screenshot, axe or a key has touched the page: focus is where the server put it
                set_screen(page, width, height)
                problems += forms_error_focus_problems(page, f"{state.name} at {width}px")
        for width, height in FORM_SCREENS:
            set_screen(page, width, height)
            here = f"{state.name} at {width}px"
            full_page_screenshot(page, out / f"form-{state.name}-{width}.png")
            if state.bare:
                problems += forms_bare_problems(page, here, response, requests) if width == 1280 else []
            else:
                problems += forms_page_problems(page, here, state, phone=width != 1280)
            if width == 320:
                problems += sideways_problems(page, here)
            else:
                problems += (axe_problems(page, here) + sideways_problems(page, here)) if state.bare else page_problems(page, here)
            problems += forms_links_problems(page, here, state.separated)
            if state.mark:
                problems += forms_mark_problems(page, here)
        if state.enter:
            problems += forms_focus_problems(page, state.name) + forms_autofill_problems(page, state.name) + forms_enter_problems(page, state.name, state.enter)
    except PlaywrightError as error:
        problems.append(f"{state.name}: could not finish looking at the page: {error.message.splitlines()[0]}; if it is not the page the state means, {STALE}")
    finally:
        context.close()
    return problems + [f"{state.name}: console error: {error}" for error in errors]


def forms_round_trip_problems(browser, base_url, session_key, fixtures):
    """?next= makes the trip: the device page and OAuth consent, signed out, go to sign in and come back with their query."""
    problems, account, device = [], fixtures["accounts"]["verified"], fixtures["device"]["consent"]
    for tag, target, words in (("device", f"/device?user_code={device}", device), ("oauth", fixtures["oauth"]["authorize"], fixtures["oauth"]["app"])):
        label = f"{tag}, signed out, through sign in"
        context = form_context(browser, base_url, "", "/", "round-trip", width=1280, height=900)
        page, errors = fresh_page(context)
        try:
            page.goto(base_url + target, wait_until="networkidle")
            if urlsplit(page.url).path != "/accounts/login/" or parse_qs(urlsplit(page.url).query).get("next") != [target]:
                problems.append(f"{label}: went to {page.url}, not to sign in with next={target}")
            else:
                page.fill("main input[name=username]", account["username"])
                page.fill("main input[name=password]", account["password"])
                reply, here = submitted(page, press=page.locator("main input[name=password]")), urlsplit(page.url)
                if reply.status != 200 or here.path != urlsplit(target).path or parse_qs(here.query) != parse_qs(urlsplit(target).query):
                    problems.append(f"{label}: came back to {page.url} with status {reply.status}, not to {target} with 200" + (f"; {STALE}" if reply.status == 429 else ""))
                elif words not in page.locator("main").inner_text():
                    problems.append(f"{label}: came back to the address, but its page does not show {words!r}")
        except PlaywrightError as error:
            problems.append(f"{label}: {error.message.splitlines()[0]}")
        finally:
            context.close()
        problems += [f"{label}: console error: {error}" for error in errors]
    return problems


def forms_slow_frames_problems(browser, base_url, session_key, fixtures):
    """The keyboard checks again with every frame held SLOW_EVERY_FRAME_MS: a check that waited a fixed time would find the page unfinished."""
    problems = []
    for path, who, enter in (("/accounts/login/", "", "main input[name=password]"), ("/device", "verified", "main input[name=user_code]")):
        here = f"{path}, every frame held {SLOW_EVERY_FRAME_MS} ms"
        context = form_context(browser, base_url, form_session(who, session_key, fixtures), path, "slow-frames", width=1280, height=900)
        context.add_init_script(FRAME_STALL)
        page, errors = fresh_page(context)
        try:
            page.goto(base_url + path, wait_until="networkidle")
            wait_for_state(page, FORM_READY)
            problems += forms_focus_problems(page, here) + forms_enter_problems(page, here, enter)
        except PlaywrightError as error:
            problems.append(f"{here}: {error.message.splitlines()[0]}")
        finally:
            context.close()
        problems += [f"{here}: console error: {error}" for error in errors]
    return problems


def run_forms(browser, base_url, session_key, out, fixtures):
    """The forms part: every state (the throttled ones last), the ?next= round trips, the keyboard with frames held."""
    fixtures["run"] = f"{int(time.time()):x}"   # in the names of what a run makes, so a second run meets none of the first's
    problems = []

    def looked(states):
        nonlocal problems
        for state in states:
            started = time.perf_counter()
            problems += forms_state_problems(browser, base_url, session_key, fixtures, state, out)
            print(f"forms: {state.name} took {time.perf_counter() - started:.0f} s")

    looked([state for state in FORM_STATES if not state.spends])
    problems += timed("forms", forms_round_trip_problems, browser, base_url, session_key, fixtures)
    problems += timed("forms", forms_slow_frames_problems, browser, base_url, session_key, fixtures)
    looked([state for state in FORM_STATES if state.spends])
    return problems


# The dashboard part (CI's job `site-dashboard`): the signed-in dashboard, /app/ and the pages that hang off it, as the users of site-tests/make_dashboard_fixtures.py see
# it. The five sections are checked like any page on the shell (page_states, page_edge_problems), signed in as the owner fixture; then what only the dashboard has: its
# sections changing in place, the Sign out button of its menu, a new key shown once, a revoke, rows that stack, times in the visitor's zone, and Billing with no Stripe.
DASHBOARD_PAGES = (
    SitePage("/app/", "dashboard-overview", signed_in=True),
    SitePage("/app/files/", "dashboard-files", signed_in=True),
    SitePage("/app/keys/", "dashboard-keys", signed_in=True),
    SitePage("/app/apps/", "dashboard-apps", signed_in=True),
    SitePage("/app/billing/", "dashboard-billing", signed_in=True),
)
DASHBOARD_HEADINGS = {"/app/": "Overview", "/app/files/": "Files", "/app/keys/": "Keys", "/app/apps/": "Connected apps", "/app/billing/": "Billing"}   # the h1, the menu's word, the title's first part
FILES_PER_PAGE = 50   # dashboard/views.py: the Files list's page
SIGN_OUT = "#shell-menu form button"
MENU_ORDER = ["/app/", "/app/files/", "/app/keys/", "/app/apps/", "/app/billing/", "/docs/", "/blog/", "/"]   # what Tab meets in the open menu before Sign out (pages.registry)
FIXTURES_STALE = "run site-tests/make_dashboard_fixtures.py again (it resets what an earlier run changed) and restart the server"
# What a visitor can see of the dashboard page showing: the address, the head, the menu's mark, the heading and whether focus is on it, <main>'s state, the rows and the notices.
DASH_STATE = """() => { const text = (e) => e.textContent.replace(/\\s+/g, ' ').trim(), main = document.querySelector('main'), toggle = document.querySelector('header [data-menu-toggle]');
  return { path: location.pathname, search: location.search, title: document.title, h1s: document.querySelectorAll('h1').length,
    heading: main.querySelector('h1') ? text(main.querySelector('h1')) : '', focused: document.activeElement === main.querySelector('h1'),
    marks: [...document.querySelectorAll('#shell-menu a[aria-current]')].map((a) => [text(a), a.getAttribute('aria-current')]),
    layout: document.body.dataset.layout || '', layoutClass: document.body.classList.contains('layout-app'),
    robots: [...document.querySelectorAll('meta[name="robots"]')].map((meta) => meta.content),
    public: document.querySelectorAll('link[rel="canonical"], link[rel="alternate"], meta[property^="og:"], meta[name="twitter:card"], script[type="application/ld+json"]').length,
    meta: [...document.head.querySelectorAll('[data-page-meta]')].map((e) => e.outerHTML).sort(), menuShut: toggle.getAttribute('aria-expanded') === 'false',
    opacity: getComputedStyle(main).opacity, leaving: main.classList.contains('is-leaving'), rows: main.querySelectorAll('table.rows tbody tr').length,
    notices: [...main.querySelectorAll('.notice')].map(text) }; }"""
# The same page fetched whole, as a direct load gets it: what an in-place change must have left behind.
DASH_DIRECT = """async (address) => { const loaded = new DOMParser().parseFromString(await (await fetch(address, { headers: { Accept: 'text/html' }, credentials: 'same-origin' })).text(), 'text/html');
  const text = (e) => e.textContent.replace(/\\s+/g, ' ').trim(), main = loaded.querySelector('main');
  return { title: loaded.title, heading: main && main.querySelector('h1') ? text(main.querySelector('h1')) : '', bodyClass: loaded.body.className,
    marks: [...loaded.querySelectorAll('#shell-menu a[aria-current]')].map((a) => [text(a), a.getAttribute('aria-current')]),
    meta: [...loaded.head.querySelectorAll('[data-page-meta]')].map((e) => e.outerHTML).sort(), rows: main ? main.querySelectorAll('table.rows tbody tr').length : -1 }; }"""
# A change in place has landed: the address, the new <h1> (the old one was marked), focus on it (the last thing shell.js does) and no cross-fade still running. A whole load has: the address and readyState.
DASH_ARRIVED = """([path, search]) => location.pathname === path && location.search === search && !document.querySelector('main h1[data-before]')
  && document.activeElement === document.querySelector('main h1') && !document.getAnimations().some((a) => a.playState === 'running' || a.playState === 'pending')"""
# A whole load has come once its <main> is at full strength: a page reached by an ordinary load fades in (spec 4.5), and axe, a screenshot or a read of the page must not meet it half way.
DASH_LOADED = """([path, search]) => location.pathname === path && (search === null || location.search === search) && !document.querySelector('main h1[data-before]') && document.readyState === 'complete'
  && (() => { const main = document.querySelector('main'); return !main || (!main.classList.contains('is-leaving') && !document.documentElement.hasAttribute('data-arriving') && getComputedStyle(main).opacity === '1'); })()"""
# The Sign out form of the menu, and the words of the menu's links and button as they are set.
SIGN_OUT_FORM = """() => { const form = document.querySelector('#shell-menu form'), button = form && form.querySelector('button'), token = form && form.querySelector('input[name=csrfmiddlewaretoken]');
  const set = (e) => { const s = getComputedStyle(e); return [s.fontFamily, s.fontSize, s.letterSpacing, s.textTransform]; };
  return { forms: document.querySelectorAll('#shell-menu form').length, buttons: document.querySelectorAll('#shell-menu button').length, method: form ? form.method : null,
    action: form ? new URL(form.action).pathname : null, token: token ? token.value.length : 0, word: button ? button.textContent.trim() : null, kind: button ? button.type : null,
    interactive: button ? button.classList.contains('interactive') : false, group: button ? [...document.querySelectorAll('#shell-menu .menu__group')].indexOf(button.closest('.menu__group')) : -1,
    last: Boolean(button) && !button.closest('li').nextElementSibling, set: button ? set(button) : null, linkSet: set(document.querySelector('#shell-menu a[href="/"]')) }; }"""
SIGN_OUT_HIT = """() => { const button = document.querySelector('#shell-menu form button'), box = button.getBoundingClientRect(),
    hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
  return { takes: Boolean(hit) && button.contains(hit), hit: hit ? hit.tagName.toLowerCase() + (hit.id ? '#' + hit.id : '') : null }; }"""
SIGN_OUT_UNDER_TEXT = TEXT_UNDER_MENU.replace("#shell-menu a", "#shell-menu form button")
# The element focus is on, in words, for the Tab order.
FOCUS_NOW = """() => { const e = document.activeElement; return e.matches('a[href]') ? e.getAttribute('href') : e.matches('button') ? e.textContent.trim() : e.tagName.toLowerCase(); }"""
# Every table of rows on the page as it is laid out now: stacked (display block, no header row, each value led by its label) or a table.
ROWS_STATE = """() => [...document.querySelectorAll('main table.rows')].map((table) => { const head = table.querySelector('thead'), words = (e) => e.textContent.replace(/\\s+/g, ' ').trim();
  return { display: getComputedStyle(table).display, caption: table.querySelector('caption') ? words(table.querySelector('caption')) : '',
    headHidden: !head || getComputedStyle(head).display === 'none' || head.getBoundingClientRect().height <= 1.5, headings: [...table.querySelectorAll('thead th')].map(words),
    cells: [...table.querySelectorAll('tbody td')].map((td) => ({ label: td.dataset.label || '', empty: !td.textContent.trim() && !td.children.length, shown: td.getClientRects().length > 0,
      before: getComputedStyle(td, '::before').content, right: td.getBoundingClientRect().right, left: td.getBoundingClientRect().left, clipped: td.scrollWidth > td.clientWidth + 1 })) }; })"""
# The words of the status regions Copy speaks in (spoken once, in the block's own).
COPY_STATUS = "() => [...document.querySelectorAll('main [data-copy-status]')].map((node) => node.textContent.trim())"
# What lies past the window's right edge in <main> (not the visually hidden text, nor a header row that is clipped away).
BEYOND = """() => [...document.querySelectorAll('main *')].filter((e) => e.getClientRects().length && !e.closest('.visually-hidden') && !(e.closest('thead') && e.closest('thead').getBoundingClientRect().height <= 1.5)
    && e.getBoundingClientRect().right > innerWidth + 1)
  .slice(0, 4).map((e) => e.tagName.toLowerCase() + (e.className ? '.' + String(e.className).split(' ')[0] : '') + ' to ' + Math.round(e.getBoundingClientRect().right))"""
# The times of the page: the datetime the server wrote and the text now showing.
TIMES = """() => [...document.querySelectorAll('main time[data-local]')].map((t) => ({ iso: t.getAttribute('datetime'), text: t.textContent.replace(/\\s+/g, ' ').trim() }))"""
LOCAL_ZONES = (("America/Chicago", None), ("Asia/Kolkata", ("IST", "GMT+5:30")), ("UTC", ("UTC",)))   # the zone, and the short names an engine gives it (None: the tz database's own)
SERVER_TIME = re.compile(r"\d{1,2} [A-Z][a-z]{2} \d{4}, \d\d:\d\d UTC")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def load_dashboard_fixtures(path):
    """What make_dashboard_fixtures.py wrote, or a stop that says how to make it."""
    if not path.is_file():
        raise SystemExit(f"{path} is missing: the dashboard part needs the fixtures that site-tests/make_dashboard_fixtures.py writes beside the session file (AGENTS.md, Site checks).")
    return json.loads(path.read_text(encoding="utf-8"))


def dash_context(browser, base_url, key, **options):
    """Paper, Motion on, the session `key` names, 1280 by 900 unless said, a bound on every wait: a context for one flow."""
    return form_context(browser, base_url, key, "/app/", "dashboard", **{"width": 1280, "height": 900, **options})


def dash_open(page, base_url, path):
    """The page at path, once shell.js has run (or there is none)."""
    response = page.goto(base_url + path, wait_until="networkidle")
    wait_for_state(page, FORM_READY)
    return response


def dash_state(problems, page, label):
    """DASH_STATE of the page now showing; a Playwright error is recorded as a problem and gives None."""
    try:
        return page.evaluate(DASH_STATE)
    except PlaywrightError as error:
        problems.append(f"{label}: could not read the page: {error.message.splitlines()[0]}")
        return None


def dash_land(problems, page, path, search, label, *, in_place=True):
    """Wait for the page at path and search to show: for a change in place its new heading with focus on it and the cross-fade over, for a whole load the loaded page. False, with a problem, if it
    never comes. The window.stayedPut that MARK_HEADING set says whether the page was kept (a change in place) or replaced (a load)."""
    try:
        page.wait_for_function(DASH_ARRIVED if in_place else DASH_LOADED, arg=[path, search], timeout=CHECK_WAIT_MS)
    except PlaywrightError:
        now = dash_state(problems, page, label) or {}
        problems.append(f"{label}: never arrived at {path}{search or ''}"
                        + (f" (the address is {now.get('path')}{now.get('search')}, focus is {'on' if now.get('focused') else 'not on'} the heading): a change in place ends with focus on the new heading" if in_place else ""))
        return False
    stayed = page.evaluate(STAYED)
    if in_place and not stayed:
        problems.append(f"{label}: the whole page reloaded; the dashboard's sections change in place (layout app, data-layout-paths in shell.html, registry.layout_paths)")
    elif stayed and not in_place:
        problems.append(f"{label}: should be an ordinary page load, but the page changed in place")
    return True


def dash_follow(problems, page, target, path, label, *, search="", in_place=True):
    """Click the link (a locator) and wait for the page at path and search (dash_land). False if it never came."""
    page.evaluate(MARK_HEADING)
    try:
        target.first.click(timeout=10000)
    except PlaywrightError as error:
        problems.append(f"{label}: could not click the link: {error.message.splitlines()[0]}")
        return False
    return dash_land(problems, page, path, search, label, in_place=in_place)


def dash_move(problems, page, direction, path, search, label):
    """Back or Forward, then wait for the page at path and search to have changed in place."""
    page.evaluate(MARK_HEADING)
    if not try_history(problems, page, direction, label):
        return False
    return dash_land(problems, page, path, search, label)


def dash_menu_link(page, href):
    """The menu's link to href, with the menu opened first if it is shut (an in-place change shuts it)."""
    set_menu(page, True)
    return page.locator(f'#shell-menu a[href="{href}"]')


def dash_arrival_findings(page, label, path, search=""):
    """What a dashboard section shows once it is there, however it came (a direct load, a change in place, Back): the title and head a direct load of it gets, its own heading
    and the menu's mark on it, the layout, the noindex tag and none of the public head (canonical address, Open Graph, structured data, feeds), the menu shut and <main> showing.
    A helper of the change checks and not named ..._problems, which pages.tests_site_parts counts as a check that runs in one part only."""
    problems, heading = [], DASHBOARD_HEADINGS.get(path, "")
    now, direct = dash_state(problems, page, label), page.evaluate(DASH_DIRECT, path + search)
    if now is None:
        return problems
    for ok, what in ((now["title"] == direct["title"] == f"{heading} | Veilfile", f"the title reads {now['title']!r} and a direct load {direct['title']!r}; it is '{heading} | Veilfile' (pages/context_processors.py, shell.js swaps it)"),
                     (now["meta"] == direct["meta"], "the head's [data-page-meta] elements are not those a direct load gets (shell.js swaps the elements that carry data-page-meta)"),
                     (now["heading"] == direct["heading"] == heading and now["h1s"] == 1, f"the page has {now['h1s']} h1 reading {now['heading']!r}; one, {heading!r}"),
                     (now["marks"] == direct["marks"] == [[heading, "page"]], f"the menu marks {now['marks']} (a direct load {direct['marks']}); it marks only {heading!r} as the page (pages.registry.menu_groups)"),
                     (now["layout"] == "app" and now["layoutClass"], f"the body reads data-layout {now['layout']!r}; a section is data-layout=\"app\" with the class layout-app (layouts/app.html)"),
                     (now["robots"] == ["noindex, nofollow"], f"the robots tags read {now['robots']}; there is one, noindex, nofollow (layouts/app.html)"),
                     (now["public"] == 0, f"the head holds {now['public']} public tags (canonical address, Open Graph, structured data, feed links); a page with no canonical address prints none (shell.html)"),
                     (now["menuShut"], "the menu is open; it starts closed on every page and an in-place change closes it (shell.js, spec 4.4)"),
                     (now["opacity"] == "1" and not now["leaving"], f"<main> is not showing (opacity {now['opacity']})"),
                     (now["rows"] == direct["rows"], f"the page shows {now['rows']} rows and a direct load {direct['rows']}")):
        if not ok:
            problems.append(f"{label}: {what}")
    return problems


def dashboard_section_problems(browser, base_url, session_key, fixtures):
    """Each section loaded directly, as the owner fixture sees it (and the Overview of the downgraded user): the status, the noindex header, the title and head (dash_arrival_findings), one
    h1, the menu with its five sections and its Sign out button, and what the fixtures put on it: the meters, 50 files of the 53 with the expired and the held one, the keys with the
    longest name and a revoked one, the apps with the longest name; then the downgraded user's Overview, with its notice of what it is over, its meters over 100 % and axe. A section that shows nothing of this would pass every check below it, so these say what to look for."""
    problems, owner = [], fixtures["owner"]
    context = dash_context(browser, base_url, fixtures["sessions"]["owner"])
    page, errors = fresh_page(context)
    try:
        for site_page in DASHBOARD_PAGES:
            path, heading = site_page.path, DASHBOARD_HEADINGS[site_page.path]
            label = f"{path} loaded directly"
            reply = dash_open(page, base_url, path)
            if reply.status != 200 or "noindex" not in (reply.headers.get("x-robots-tag") or "") or "nofollow" not in (reply.headers.get("x-robots-tag") or ""):
                problems.append(f"{label}: answers {reply.status} with X-Robots-Tag {reply.headers.get('x-robots-tag')!r}; a section is 200 with noindex, nofollow (dashboard.views.dashboard_view)")
            problems += dash_arrival_findings(page, label, path)
            found = page.evaluate(SIGN_OUT_FORM)
            if found["buttons"] != 1:
                problems.append(f"{label}: the menu has {found['buttons']} buttons; the dashboard's menu has one, Sign out (components/menu.html)")
            if path == "/app/":
                meters = page.evaluate("() => [document.querySelectorAll('main .meter').length, document.querySelectorAll('main [role=meter]').length, document.querySelectorAll('main .meter--over').length]")
                if meters != [4, 3, 0]:
                    problems.append(f"{label}: {meters[0]} meters, {meters[1]} with a bar and {meters[2]} over; the owner has four (uploads, storage, keys, link lifetime), three with a bar and none over (components/meter.html)")
            elif path == "/app/files/":
                shown = page.evaluate("""() => { const rows = [...document.querySelectorAll('main table.rows tbody tr')], cell = (row, label) => (row.querySelector(`td[data-label="${label}"]`) || { textContent: '' }).textContent.trim();
                  return { rows: rows.length, names: rows.map((row) => cell(row, 'File')), states: rows.map((row) => row.dataset.state), flags: rows.map((row) => cell(row, 'Secret flags')),
                           sizes: rows.map((row) => cell(row, 'Size')), older: Boolean(document.querySelector('main a[href*="cursor="]')), copies: document.querySelectorAll('main button[data-copy]').length }; }""")
                if shown["rows"] != FILES_PER_PAGE or not shown["older"]:
                    problems.append(f"{label}: {shown['rows']} rows and {'an' if shown['older'] else 'no'} Older files link; the list has {FILES_PER_PAGE} of the owner's {owner['files']} and a way to the rest ({FIXTURES_STALE})")
                for name, what in ((owner["long_file"], "the file with the longest name"), (owner["expired_file"], "the expired file"), (owner["held_file"], "the held file"), (owner["flags_file"], "the file with flags")):
                    if name not in shown["names"]:
                        problems.append(f"{label}: {what} is not in the list's first page ({FIXTURES_STALE})")
                if "expired" not in shown["states"] or "held" not in shown["states"] or shown["copies"] < 1:
                    problems.append(f"{label}: the rows' states are {sorted(set(shown['states']))} and {shown['copies']} Copy buttons; there are live, expired and held rows, and Copy on the live ones (components/file_list.html)")
                if not any("Stripe" in words for words in shown["flags"]):
                    problems.append(f"{label}: no row names a secret flag in words (the file {owner['flags_file']} has two)")
                if "2.0 GB" not in shown["sizes"]:
                    problems.append(f"{label}: no row shows 2.0 GB; the held file is 2,150,000,000 bytes (the bytes filter goes up to gigabytes)")
            elif path == "/app/keys/":
                shown = page.evaluate("""() => ({ names: [...document.querySelectorAll('main table.rows tbody tr td[data-label="Name"]')].map((td) => td.textContent.trim()),
                  states: [...document.querySelectorAll('main table.rows tbody tr td[data-label="State"]')].map((td) => td.textContent.trim()),
                  form: Boolean(document.querySelector('main form input[name=token]')) && Boolean(document.querySelector('main input[name=name]')) })""")
                if len(shown["names"]) != owner["keys"] or owner["long_key"] not in shown["names"] or shown["states"].count("Revoked") != 1 or not shown["form"]:
                    problems.append(f"{label}: the keys are {len(shown['names'])} (the owner has {owner['keys']}), the longest name {'is' if owner['long_key'] in shown['names'] else 'is not'} there, {shown['states'].count('Revoked')} revoked "
                                    f"(one), and the form {'has' if shown['form'] else 'lacks'} its name field and one-time token ({FIXTURES_STALE})")
            elif path == "/app/apps/":
                shown = page.evaluate("() => [...document.querySelectorAll('main table.rows tbody tr td[data-label=\"App\"]')].map((td) => td.textContent.trim())")
                if sorted(shown) != sorted(owner["apps"].values()):
                    problems.append(f"{label}: the apps are {[name[:30] for name in shown]}; the owner has connected {len(owner['apps'])}, one with the longest name ({FIXTURES_STALE})")
    except PlaywrightError as error:
        problems.append(f"the dashboard's sections: {error.message.splitlines()[0]}")
    finally:
        context.close()
    problems += [f"the dashboard's sections: console error: {error}" for error in errors]
    context = dash_context(browser, base_url, fixtures["sessions"]["over"])
    page, errors = fresh_page(context)
    try:
        dash_open(page, base_url, "/app/")
        lines = page.evaluate("""() => ({ notice: (document.querySelector('main .notice strong') || { textContent: '' }).textContent, items: document.querySelectorAll('main .notice li').length,
          over: document.querySelectorAll('main .meter--over').length, figures: [...document.querySelectorAll('main .meter__figures')].map((p) => p.textContent.replace(/\\s+/g, ' ').trim()),
          values: [...document.querySelectorAll('main [role=meter]')].map((m) => Number(m.getAttribute('aria-valuenow')) <= Number(m.getAttribute('aria-valuemax'))) })""")
        if "over the Free plan" not in lines["notice"] or lines["items"] != 4 or lines["over"] != 3 or not all(lines["values"]):
            problems.append(f"/app/ of the downgraded user: the over-limit notice reads {lines['notice']!r} with {lines['items']} lines (keys, storage, link lifetime, uploads: four), {lines['over']} meters over their limit (three) "
                            f"and the bars' values {'within' if all(lines['values']) else 'past'} their ends (templates/dashboard/over_limits.html, components/meter.html; {FIXTURES_STALE})")
        if not any("2.0 GB of 1.0 GB" in figures for figures in lines["figures"]):
            problems.append(f"/app/ of the downgraded user: no meter reads 2.0 GB of 1.0 GB; storage is {lines['figures']}")
        problems += page_problems(page, "/app/ of the downgraded user at 1280px")
    except PlaywrightError as error:
        problems.append(f"/app/ of the downgraded user: {error.message.splitlines()[0]}")
    finally:
        context.close()
    return problems + [f"/app/ of the downgraded user: console error: {error}" for error in errors]


def dashboard_walk_findings(browser, base_url, fixtures, *, slow):
    """The sections changing in place, tried once at 1280px: Overview to Files by the menu; Files to its older files by `?cursor=` (the rows change, the page stays); Back and Forward
    there; Keys, Connected apps, Billing and Overview by the menu, then Back and Forward across them. Each is a change in place (the page is kept, not reloaded) that leaves the menu shut,
    swaps the head as a direct load has it, marks its section in the menu and puts focus on the new heading. A confirmation page (Revoke on Keys) is an ordinary load, and Keep it
    comes back to Keys; then a session that has ended (the cookie gone) makes the next change an ordinary load that reaches sign in with ?next=. With `slow` every frame is held
    SLOW_EVERY_FRAME_MS and only the first half is tried: the waits are for states, so a runner's slow frames change no answer."""
    problems = []
    context = dash_context(browser, base_url, fixtures["sessions"]["owner"], height=800)
    if slow:
        context.add_init_script(FRAME_STALL)
    page, errors = fresh_page(context)
    where = f", every frame held {SLOW_EVERY_FRAME_MS} ms" if slow else ""
    states = {}   # DASH_STATE of a page when it first came, for Back and Forward

    def arrived(path, search, label):
        problems.extend(dash_arrival_findings(page, label, path, search))
        states[(path, search)] = dash_state(problems, page, label)

    def restored(path, search, label):
        now, was = dash_state(problems, page, label), states.get((path, search))
        if now and was and (now["rows"], now["title"]) != (was["rows"], was["title"]):
            problems.append(f"{label}: shows {now['rows']} rows titled {now['title']!r}, but {path}{search} first showed {was['rows']} rows titled {was['title']!r}")
        problems.extend(dash_arrival_findings(page, label, path, search))

    try:
        dash_open(page, base_url, "/app/")
        arrived("/app/", "", "/app/ loaded directly" + where)
        label = "Files by the menu, in place" + where
        if dash_follow(problems, page, dash_menu_link(page, "/app/files/"), "/app/files/", label):
            arrived("/app/files/", "", label)
            older = page.locator("main a.files__older")
            first = page.evaluate(DASH_STATE)["rows"]
            label = "Files to its older files by ?cursor=, in place" + where
            if older.count() != 1:
                problems.append(f"{label}: the list has {older.count()} Older files links; it has one when there are more than {FILES_PER_PAGE} files (components/file_list.html; {FIXTURES_STALE})")
            else:
                href = older.first.get_attribute("href")
                query = "?" + urlsplit(href).query
                if urlsplit(href).path != "/app/files/" or not query.startswith("?cursor="):
                    problems.append(f"{label}: Older files links {href}; it is /app/files/?cursor=...")
                elif dash_follow(problems, page, older, "/app/files/", label, search=query):
                    arrived("/app/files/", query, label)
                    second = states.get(("/app/files/", query))
                    rest = second["rows"] if second else None
                    if first != FILES_PER_PAGE or rest != fixtures["owner"]["files"] - FILES_PER_PAGE:
                        problems.append(f"{label}: the first page had {first} rows and the second has {rest}; the owner's {fixtures['owner']['files']} files are {FILES_PER_PAGE} and then {fixtures['owner']['files'] - FILES_PER_PAGE}")
                    label = "the older files back to Files by Back, in place" + where
                    if dash_move(problems, page, "back", "/app/files/", "", label):
                        restored("/app/files/", "", label)
                        label = "Files forward to its older files by Forward, in place" + where
                        if dash_move(problems, page, "forward", "/app/files/", query, label):
                            restored("/app/files/", query, label)
        label = "Keys by the menu, from the older files, in place" + where
        if dash_follow(problems, page, dash_menu_link(page, "/app/keys/"), "/app/keys/", label):
            arrived("/app/keys/", "", label)
        if slow:
            return problems
        for path in ("/app/apps/", "/app/billing/", "/app/"):
            label = f"{DASHBOARD_HEADINGS[path]} by the menu, in place"
            if dash_follow(problems, page, dash_menu_link(page, path), path, label):
                arrived(path, "", label)
        for direction, path in (("back", "/app/billing/"), ("back", "/app/apps/"), ("forward", "/app/billing/")):
            label = f"{DASHBOARD_HEADINGS[path]} by {direction.title()}, in place"
            if dash_move(problems, page, direction, path, "", label):
                restored(path, "", label)
        if dash_follow(problems, page, dash_menu_link(page, "/app/keys/"), "/app/keys/", "Keys by the menu, in place"):
            link = page.locator('main a[href$="/revoke/"]').first
            href = link.get_attribute("href") if link.count() else None
            label = "Revoke on Keys, a confirmation page"
            if href is None:
                problems.append(f"{label}: Keys has no Revoke link ({FIXTURES_STALE})")
            elif dash_follow(problems, page, link, urlsplit(href).path, label, in_place=False):
                shown = page.evaluate(DASH_STATE)
                if shown["marks"] != [["Keys", "true"]] or shown["layout"] != "":
                    problems.append(f"{label}: the menu marks {shown['marks']} and the body reads data-layout {shown['layout']!r}; a confirmation page marks Keys as the section it is in and is not part of the family "
                                    f"(registry.DASHBOARD_SECTIONS, templates/dashboard/key_revoke.html)")
                label = "Keep this key, back to Keys"
                dash_follow(problems, page, page.locator("main a", has_text="Keep this key"), "/app/keys/", label, in_place=False)
        label = "Files by the menu after the session ended"
        context.clear_cookies(name="sessionid")
        if dash_follow(problems, page, dash_menu_link(page, "/app/files/"), "/accounts/login/", label, search=None, in_place=False):
            if parse_qs(urlsplit(page.url).query).get("next") != ["/app/files/"]:
                problems.append(f"{label}: reached {page.url}; sign in with next=/app/files/ (the page asked for)")
            if page.locator("#shell-menu form").count():
                problems.append(f"{label}: the sign-in page has a form in its menu; Sign out is the dashboard's")
    except PlaywrightError as error:
        problems.append(f"the dashboard's changes in place{where}: {error.message.splitlines()[0]}")
    finally:
        context.close()
    return problems + [f"the dashboard's changes in place{where}: console error: {error}" for error in errors]


def dashboard_change_problems(browser, base_url, session_key, fixtures):
    """The sections change in place among themselves and a confirmation page does not (dashboard_walk_findings), the pattern of reading_change_problems on the dashboard's family."""
    return dashboard_walk_findings(browser, base_url, fixtures, slow=False)


def dashboard_slow_frames_problems(browser, base_url, session_key, fixtures):
    """The changes in place again with every frame held SLOW_EVERY_FRAME_MS, and Sign out by the keyboard: a check that waited a fixed time would find the page unfinished."""
    return dashboard_walk_findings(browser, base_url, fixtures, slow=True) + dashboard_sign_out_keyboard_findings(browser, base_url, fixtures["sessions"]["sign_out"][13], slow=True)


def dash_sign_out_markup_findings(page, label):
    """The menu's Sign out: one form, a POST to /accounts/logout/ with a token, holding one button that says Sign out, ends the second group (Docs, Journal, Site), is a `label interactive`
    like the links and is set in their type."""
    found, problems = page.evaluate(SIGN_OUT_FORM), []
    for ok, what in ((found["forms"] == 1 and found["buttons"] == 1, f"the menu has {found['forms']} forms and {found['buttons']} buttons; one of each"),
                     (found["method"] == "post" and found["action"] == "/accounts/logout/", f"the form is {found['method']} to {found['action']}; a POST to /accounts/logout/ (a GET signs nobody out)"),
                     (found["token"] >= 20, "the form carries no CSRF token"),
                     (found["word"] == "Sign out" and found["kind"] == "submit", f"the button says {found['word']!r} and is type {found['kind']}; it says Sign out and submits"),
                     (found["interactive"], "the button is not .interactive, so it answers neither the cursor nor a press with the ink wash"),
                     (found["group"] == 1 and found["last"], f"the button is in group {found['group']} and {'is' if found['last'] else 'is not'} its last item; it ends the second group (pages.registry.menu_groups, components/menu.html)"),
                     (found["set"] == found["linkSet"], f"the button is set in {found['set']} and the menu's links in {found['linkSet']}; the menu reads as one list (static/css/components/menu.css: style the button as the links)")):
        if not ok:
            problems.append(f"{label}: {what}")
    return problems


def dash_sign_out_reach_findings(page, label):
    """With the menu open the button can be brought into view, lies clear of the display controls, takes a click at its centre and covers no text of the page."""
    problems = []
    if page.locator(SIGN_OUT).count() != 1:
        return [f"{label}: the menu has {page.locator(SIGN_OUT).count()} Sign out buttons; one, so its place was not looked at"]
    page.wait_for_function(SHEET_IN_PLACE)   # a sheet fading in is up to 6px above its place
    controls = page.locator(".shell-corner--bottom-right").bounding_box()
    if page.evaluate(UNDER_SHEET, "footer [data-motion-toggle]"):   # the open sheet itself is painted over them
        controls = None
    button = page.locator(SIGN_OUT)
    try:
        button.scroll_into_view_if_needed(timeout=2000)
    except PlaywrightError:
        pass
    box, height = button.bounding_box(), page.viewport_size["height"]
    if box is None or box["y"] < -REACH_SLACK or box["y"] + box["height"] > height + REACH_SLACK:
        problems.append(f"{label}: the Sign out button cannot be brought into view")
    elif controls and overlaps(box, controls):
        problems.append(f"{label}: the Sign out button sits on the display controls")
    elif not page.evaluate(SIGN_OUT_HIT)["takes"]:
        problems.append(f"{label}: a click at the centre of the Sign out button reaches {page.evaluate(SIGN_OUT_HIT)['hit']}, not the button")
    problems += [f"{label}: the Sign out button covers the text {text!r}" for text in page.evaluate(SIGN_OUT_UNDER_TEXT)]
    return problems


def dash_signed_out_findings(page, base_url, reply, label):
    """After Sign out: sign in, answered 200, and the dashboard is out of reach (a section goes to sign in with ?next=)."""
    problems = []
    if reply.status != 200 or urlsplit(page.url).path != "/accounts/login/":
        return [f"{label}: Sign out ended at {page.url} with status {reply.status}; it ends at /accounts/login/ with 200 (settings.LOGOUT_REDIRECT_URL)"]
    page.goto(base_url + "/app/", wait_until="networkidle")
    if urlsplit(page.url).path != "/accounts/login/" or parse_qs(urlsplit(page.url).query).get("next") != ["/app/"]:
        problems.append(f"{label}: after Sign out /app/ went to {page.url}, not to sign in with next=/app/")
    return problems


def dashboard_sign_out_keyboard_findings(browser, base_url, key, *, slow=False):
    """Sign out by the keyboard: Enter on MENU opens the menu, Tab meets the five sections, Docs, Journal and Site and then the Sign out button, which shows the focus ring, and Enter on it signs out."""
    problems, where = [], "Sign out by the keyboard" + (f", every frame held {SLOW_EVERY_FRAME_MS} ms" if slow else "")
    context = dash_context(browser, base_url, key)
    if slow:
        context.add_init_script(FRAME_STALL)
    page, errors = fresh_page(context)
    try:
        dash_open(page, base_url, "/app/")
        page.locator("header [data-menu-toggle]").focus()
        page.keyboard.press("Enter")
        page.wait_for_function(SHEET_IN_PLACE)
        met = []
        for _ in range(len(MENU_ORDER) + 3):
            page.keyboard.press("Tab")
            met.append(page.evaluate(FOCUS_NOW))
            if met[-1] == "Sign out":
                break
        if met != MENU_ORDER + ["Sign out"]:
            problems.append(f"{where}: Tab meets {met}; it meets {MENU_ORDER + ['Sign out']} (the button is the last of the second group, after Site)")
        else:
            ring = page.evaluate("() => { const e = document.activeElement; return [e.matches(':focus-visible'), getComputedStyle(e).outlineStyle, getComputedStyle(e).outlineWidth]; }")
            if not ring[0] or ring[1] == "none" or ring[2] == "0px":
                problems.append(f"{where}: the focused Sign out button shows no focus ring (:focus-visible {ring[0]}, outline {ring[1]} {ring[2]}; static/css/base.css)")
            problems += dash_signed_out_findings(page, base_url, submitted(page, press=page.locator(SIGN_OUT)), where)
    except PlaywrightError as error:
        problems.append(f"{where}: {error.message.splitlines()[0]}")
    finally:
        context.close()
    return problems + [f"{where}: console error: {error}" for error in errors]


def dashboard_sign_out_problems(browser, base_url, session_key, fixtures):
    """Sign out, the button that ends the dashboard's menu (spec 4.4, 4.8). Its markup and its place in the open menu at 1280, 375, 320 and 667x375 (clear of the display controls, taking a
    click, covering no text); then, from every section, a click on it signs out and lands on sign in with 200 and the dashboard out of reach, with scripts at 1280, in the phone's sheet,
    and without scripts at 375 (where the menu is open on the page); by the keyboard (dashboard_sign_out_keyboard_findings); and a GET of /accounts/logout/ signs nobody out. Each sign-out
    ends a session, so each uses one of the spare sessions of the fixtures."""
    problems, spare = [], iter(fixtures["sessions"]["sign_out"])
    for width, height in ((1280, 800), (375, 667), (320, 640), (667, 375)):
        label = f"/app/ at {width}x{height}, the menu open"
        context = dash_context(browser, base_url, fixtures["sessions"]["owner"], width=width, height=height)
        page, errors = fresh_page(context)
        try:
            dash_open(page, base_url, "/app/")
            set_menu(page, True)
            if width == 1280 or width == 375:
                problems += dash_sign_out_markup_findings(page, label)
            problems += dash_sign_out_reach_findings(page, label)
        except PlaywrightError as error:
            problems.append(f"{label}: {error.message.splitlines()[0]}")
        finally:
            context.close()
        problems += [f"{label}: console error: {error}" for error in errors]
    for scripts, width, height in ((True, 1280, 800), (False, 375, 667)):
        for site_page in DASHBOARD_PAGES:
            label = f"Sign out from {site_page.path} {'with' if scripts else 'without'} scripts at {width}px"
            context = dash_context(browser, base_url, next(spare), width=width, height=height, javascript=scripts)
            page, errors = fresh_page(context)
            try:
                dash_open(page, base_url, site_page.path)
                if scripts:
                    set_menu(page, True)
                    page.wait_for_function(SHEET_IN_PLACE)
                else:
                    problems += dash_sign_out_markup_findings(page, label)
                    problems += [f"{label}: the Sign out button covers the text {text!r}" for text in page.evaluate(SIGN_OUT_UNDER_TEXT)]
                    if not page.locator(SIGN_OUT).is_visible():
                        problems.append(f"{label}: the Sign out button is hidden without scripts, where the menu is open on the page")
                if page.locator(SIGN_OUT).count() == 1:
                    problems += dash_signed_out_findings(page, base_url, submitted(page, click=page.locator(SIGN_OUT)), label)
            except PlaywrightError as error:
                problems.append(f"{label}: {error.message.splitlines()[0]}")
            finally:
                context.close()
            problems += [f"{label}: console error: {error}" for error in errors]
    label = "Sign out from the phone's sheet at 375px"
    context = dash_context(browser, base_url, next(spare), width=375, height=667, touch=True)
    page, errors = fresh_page(context)
    try:
        dash_open(page, base_url, "/app/keys/")
        set_menu(page, True)
        page.wait_for_function(SHEET_IN_PLACE)
        problems += dash_signed_out_findings(page, base_url, submitted(page, click=page.locator(SIGN_OUT)), label)
    except PlaywrightError as error:
        problems.append(f"{label}: {error.message.splitlines()[0]}")
    finally:
        context.close()
    problems += [f"{label}: console error: {error}" for error in errors]
    problems += dashboard_sign_out_keyboard_findings(browser, base_url, next(spare))
    context = dash_context(browser, base_url, next(spare))
    try:
        reply = context.request.get(base_url + "/accounts/logout/", max_redirects=0)
        after = context.request.get(base_url + "/app/", max_redirects=0)
        if reply.status != 405 or after.status != 200:
            problems.append(f"a GET of /accounts/logout/ answers {reply.status} and /app/ then {after.status}; a GET signs nobody out: 405, and the dashboard still opens (django.contrib.auth.views.LogoutView)")
    except PlaywrightError as error:
        problems.append(f"a GET of /accounts/logout/: {error.message.splitlines()[0]}")
    finally:
        context.close()
    return problems


def dashboard_error_page_problems(browser, base_url, session_key, fixtures):
    """The error pages of a signed-in visitor carry nothing of the dashboard's menu: an address under /app/ that is no page, a key and a file of nobody's, the link-miss page and the
    page that a POST without its token gets have the ordinary menu (Dashboard, not the five sections), no Sign out button and no CSRF token (AGENTS.md, Error pages): a token
    on a page that is cached or shared would be the visitor's. The ones under /app/ say noindex too. Read from the page as sent, with the visitor's cookies."""
    problems = []
    context = dash_context(browser, base_url, fixtures["sessions"]["owner"])
    try:
        opener = context.new_page()
        opener.goto(base_url + "/app/", wait_until="networkidle")   # the cookies the visitor has: the session and the CSRF cookie
        # Only the pages that a view returns: the dev server runs with DEBUG on, which answers an address no route matches, and an Http404 that a view raises, with Django's technical
        # page and never calls the site's handlers (the Django tests of pages/errors.py have those).
        for method, path, status in (("GET", "/app/keys/999999999/revoke/", 404), ("GET", "/app/apps/999999999/disconnect/", 404), ("GET", "/app/billing/switch/nothing/", 404),
                                     ("GET", "/a/site-checks-missing-9f2c", 404), ("POST", "/app/keys/", 403)):
            label = f"{method} {path}, signed in"
            reply = context.request.fetch(base_url + path, method=method, max_redirects=0)
            body = reply.text()
            if reply.status != status:
                problems.append(f"{label}: answers {reply.status}, not {status}")
                continue
            if "Sign out" in body or "csrfmiddlewaretoken" in body or "csrf-token" in body:
                problems.append(f"{label}: the page holds the dashboard's Sign out button or a CSRF token; an error page draws the ordinary menu and no token (pages/errors.py plain_menu, drop_core/throttle.py)")
            if path.startswith("/app/") and "noindex" not in (reply.headers.get("x-robots-tag") or ""):
                problems.append(f"{label}: no X-Robots-Tag: noindex; everything under /app/ carries it, the error pages too (pages/errors.py)")
            if 'href="/app/files/"' in body:
                problems.append(f"{label}: the page's menu lists the dashboard's sections; an error page has the ordinary menu (pages/errors.py plain_menu)")
    except PlaywrightError as error:
        problems.append(f"the error pages of a signed-in visitor: {error.message.splitlines()[0]}")
    finally:
        context.close()
    return problems


KEY_SHOWN = re.compile(r"vlf_[A-Za-z0-9_-]{20,}")   # a raw API key, wherever it is written
KEY_WARNING = "Copy this key now. You won't see it again."


def dash_key_count(page, base_url):
    """The rows of the Keys list, the page loaded whole."""
    dash_open(page, base_url, "/app/keys/")
    return page.locator("main table.rows tbody tr").count()


def dash_key_page_findings(page, context, reply, label):
    """The answer to the POST that made a key: 200, never cached, at /app/keys/, the key written once in the page and nowhere else the browser keeps (the address, storage, a cookie),
    the warning with focus (and its ring, after a key press), Copy named for it. The key the page shows, or None if it shows none."""
    problems = []
    shown = page.evaluate("""() => ({ heading: (document.querySelector('main h1') || { textContent: '' }).textContent.trim(), key: (document.querySelector('#new-key') || { textContent: '' }).textContent.trim(),
      warning: (document.querySelector('#key-warning') || { textContent: '' }).textContent.trim(), focus: document.activeElement.id || document.activeElement.tagName.toLowerCase(),
      ring: document.activeElement.matches(':focus-visible'), copies: document.querySelectorAll('main [data-copy="new-key"]').length })""")
    key = shown["key"] if KEY_SHOWN.fullmatch(shown["key"]) else None
    for ok, what in ((reply.status == 200, f"answers {reply.status}; the POST that made a key answers 200"),
                     ("no-store" in (reply.headers.get("cache-control") or ""), f"Cache-Control is {reply.headers.get('cache-control')!r}; the page that shows a key is no-store (dashboard/keys.py)"),
                     (urlsplit(page.url).path == "/app/keys/" and not urlsplit(page.url).query, f"the address is {page.url}; the answer is at /app/keys/ with nothing in the address"),
                     (shown["heading"] == "Your new API key", f"the heading reads {shown['heading']!r}, not 'Your new API key'"),
                     (key is not None, f"#new-key holds {shown['key'][:12]!r}, not a key (vlf_ and its characters; templates/dashboard/key_made.html)"),
                     (shown["warning"] == KEY_WARNING, f"the warning reads {shown['warning']!r}, not {KEY_WARNING!r}"),
                     (shown["focus"] == "key-warning", f"focus is on {shown['focus']!r}; the warning takes it on load (autofocus on #key-warning), so a screen reader reads it before the key"),
                     (shown["copies"] == 1, f"{shown['copies']} Copy buttons for #new-key; one")):
        if not ok:
            problems.append(f"{label}: {what}")
    if key is not None:
        if page.content().count(key) != 1:
            problems.append(f"{label}: the key is written {page.content().count(key)} times in the page; once, in #new-key")
        kept = page.evaluate("(key) => [location.href, JSON.stringify(localStorage), JSON.stringify(sessionStorage), document.cookie].filter((text) => text.includes(key)).length", key)
        cookies = [cookie["name"] for cookie in context.cookies() if key in cookie["value"]]
        if kept or cookies:
            problems.append(f"{label}: the key is kept in the address, storage or cookie {cookies or ''}; it is in the page only (invariant 1, AGENTS.md)")
    return problems, key


def dash_key_flow_findings(page, context, label, name):
    """Fill the new-key form with `name`, send it with Enter (a key press, so the focus ring is the keyboard's) and read what comes back. (problems, the key or None)"""
    field = page.locator("main input[name=name]")
    field.fill(name)
    reply = submitted(page, press=field)
    wait_for_state(page, FORM_READY)
    problems, key = dash_key_page_findings(page, context, reply, label)
    if key and not page.evaluate("() => document.activeElement.matches(':focus-visible')"):
        problems.append(f"{label}: the focused warning shows no focus ring after a key press (:focus-visible does not match; static/css/base.css)")
    return problems, key


def dashboard_key_problems(browser, base_url, session_key, fixtures):
    """The key flow (Review Focus 1, invariant 1), as the owner: a new key is made by the form and shown in the answer, focused, once, with Copy putting exactly the key on the clipboard;
    a reload (the browser sends the POST again) shows no key, says no key was made and makes no second one; a second key is made the same way, and Back from the Keys page after it shows
    no key and makes none. The keys are counted before and after. The owner has room under the Team plan's five for these two."""
    problems, stamp = [], fixtures["run"]
    context = dash_context(browser, base_url, fixtures["sessions"]["owner"])
    page, errors = fresh_page(context)
    try:
        before = dash_key_count(page, base_url)
        if before != fixtures["owner"]["keys"]:
            return [f"/app/keys/ lists {before} keys before the key flow, not the owner's {fixtures['owner']['keys']}: {FIXTURES_STALE}"]
        found, key = dash_key_flow_findings(page, context, "a new key", f"site-key-{stamp}")
        problems += found
        if key:
            label, button = "Copy on the new key", page.locator('[data-copy="new-key"]')
            if page.get_by_role("button", name="Copy the new API key").count() != 1:
                problems.append(f"{label}: no button is named 'Copy the new API key' (templates/dashboard/key_made.html)")
            button.click()
            if not wait_for_state(page, "() => document.querySelector('[data-copy=\"new-key\"]').textContent.trim() === 'Copied'"):
                problems.append(f"{label}: the button never says Copied")
            elif page.evaluate("() => navigator.clipboard.readText()") != key:
                problems.append(f"{label}: Copy put {page.evaluate('() => navigator.clipboard.readText()')!r} on the clipboard; the key is the block's text")
            if not wait_for_state(page, "() => (" + COPY_STATUS + ")().join('|') === 'Copied'"):   # one region, in the key's own block
                problems.append(f"{label}: the status regions read {page.evaluate(COPY_STATUS)}; one, in the key's own block, says Copied")
            problems += page_problems(page, "the new key's page at 1280px")
            if not wait_for_state(page, "() => document.querySelector('[data-copy=\"new-key\"]').textContent.trim() === 'Copy'"):
                problems.append(f"{label}: the button does not go back to Copy")
            set_screen(page, 375, 667)
            problems += sideways_problems(page, "the new key's page at 375px")
            set_screen(page, 1280, 900)
            label = "a new key, the page reloaded"
            try:
                again = page.reload(wait_until="networkidle")
            except PlaywrightError as error:
                again = None
                problems.append(f"{label}: the reload failed: {error.message.splitlines()[0]}")
            if again is not None:
                text, words = page.content(), page.locator("main").inner_text()
                if KEY_SHOWN.search(text):
                    problems.append(f"{label}: the page shows a key; a reload shows none (the one-time token is spent: dashboard/forms.py)")
                if again.status != 409 or "no key was made" not in words:
                    problems.append(f"{label}: answers {again.status} and says {words[:80]!r}; a reload is a 409 that says no key was made (dashboard/keys.py SPENT)")
            counted = dash_key_count(page, base_url)
            if counted != before + 1:
                problems.append(f"{label}: the owner has {counted} keys after the reload, not {before + 1}; the reload made another")
        dash_open(page, base_url, "/app/keys/")
        found, key = dash_key_flow_findings(page, context, "a second new key", f"site-key-{stamp}-b")
        problems += found
        if key:
            label = "a second new key, Done and then Back"
            dash_follow(problems, page, page.locator("main a", has_text="Done"), "/app/keys/", label, in_place=False)
            try:
                page.go_back(timeout=10000, wait_until="load")
            except PlaywrightError as error:
                print(f"dashboard: Back to the key's page: {error.message.splitlines()[0]} (no page, so no key)")
            if KEY_SHOWN.search(page.content()):
                problems.append(f"{label}: Back shows a key; the answer that made it is no-store and shows none again (dashboard/keys.py)")
            counted = dash_key_count(page, base_url)
            if counted != before + 2:
                problems.append(f"{label}: the owner has {counted} keys after Back, not {before + 2}; Back sent the POST again and made another")
    except PlaywrightError as error:
        problems.append(f"the key flow: {error.message.splitlines()[0]}")
    finally:
        context.close()
    return problems + [f"the key flow: console error: {error}" for error in errors]


REVOKES = (   # what, the section, the column of the name, the confirmation's link ending, its heading, Keep, its button, the line after
    ("key", "/app/keys/", "Name", "/revoke/", "Do you want to revoke this API key?", "Keep this key", "Revoke key", "The key is revoked."),
    ("file", "/app/files/", "File", "/revoke/", "Do you want to revoke this file?", "Keep it", "Revoke file", "The file is revoked."),
    ("app", "/app/apps/", "App", "/disconnect/", "Do you want to disconnect {name}?", "Keep connected", "Disconnect", "{name} is disconnected."),
)


def dash_row(page, label, name):
    """The row of the page's rows table whose cell in column `label` holds exactly `name`."""
    cell = page.locator(f'td[data-label="{label}"]', has_text=re.compile(rf"^\s*{re.escape(name)}\s*$"))
    return page.locator("main table.rows tbody tr").filter(has=cell)


def dashboard_revoke_problems(browser, base_url, session_key, fixtures):
    """A revoke, end to end, for a key, a file and a connected app of the owner: the row's link opens a confirmation page (an ordinary load that changes nothing: Keep goes back with the row
    as it was), whose button revokes and goes back with the one line "The key is revoked." (read once: a reload has it gone) and the row revoked, gone or disconnected. The confirmation page is a
    form page: one h1 with the name, a POST form with a token, the section marked in the menu, Sign out, axe and no sideways scroll at 1280, 375 and 320."""
    problems, owner = [], fixtures["owner"]
    names = {"key": owner["revoke_key"], "file": owner["revoke_file"], "app": owner["apps"]["gone"]}
    context = dash_context(browser, base_url, fixtures["sessions"]["owner"])
    page, errors = fresh_page(context)
    try:
        for what, section, column, ending, heading, keep, button, done in REVOKES:
            name, label = names[what], f"Revoke a {what}" if what != "app" else "Disconnect an app"
            heading, done = heading.format(name=name), done.format(name=name)
            dash_open(page, base_url, section)
            row = dash_row(page, column, name)
            if row.count() != 1:
                problems.append(f"{label}: {row.count()} rows hold {name!r} in {section}; one ({FIXTURES_STALE})")
                continue
            link = row.locator(f'a[href$="{ending}"]')
            href = link.first.get_attribute("href") if link.count() else None
            if href is None:
                problems.append(f"{label}: the row has no link to {ending}")
                continue
            before = row.locator('td[data-label="State"]').inner_text().strip() if what != "app" else ""
            if not dash_follow(problems, page, link, urlsplit(href).path, f"{label}, the confirmation page", in_place=False):
                continue
            here = f"{label}, the confirmation page"
            found = page.evaluate("""() => ({ headings: [...document.querySelectorAll('h1')].map((h) => h.textContent.trim()), text: document.querySelector('main').innerText,
              forms: document.querySelectorAll('main form[method=post] input[name=csrfmiddlewaretoken]').length, layout: document.body.dataset.layout || '',
              marks: [...document.querySelectorAll('#shell-menu a[aria-current]')].map((a) => [a.textContent.trim(), a.getAttribute('aria-current')]),
              signOut: document.querySelectorAll('#shell-menu form button').length })""")
            for ok, why in ((found["headings"] == [heading], f"the headings are {found['headings']}; one, {heading!r}"),
                            (name in found["text"], "the page does not name what it acts on"),
                            (found["forms"] == 1, f"{found['forms']} POST forms with a token; one"),
                            (found["layout"] == "", f"the body reads data-layout {found['layout']!r}; a confirmation page is not in the family"),
                            (found["marks"] == [[DASHBOARD_HEADINGS[section], "true"]], f"the menu marks {found['marks']}; the section it is in, {DASHBOARD_HEADINGS[section]!r}, as true (registry.DASHBOARD_SECTIONS)"),
                            (found["signOut"] == 1, f"{found['signOut']} Sign out buttons in the menu; one")):
                if not ok:
                    problems.append(f"{here}: {why}")
            problems += page_problems(page, f"{here} at 1280px")
            for width, height in ((375, 667), (320, 640)):
                set_screen(page, width, height)
                problems += sideways_problems(page, f"{here} at {width}px")
            set_screen(page, 1280, 900)
            if not dash_follow(problems, page, page.locator("main a", has_text=keep), section, f"{label}, Keep", in_place=False):
                continue
            kept = dash_row(page, column, name)
            if kept.count() != 1 or (what != "app" and kept.locator('td[data-label="State"]').inner_text().strip() != before):
                problems.append(f"{label}: after Keep the row is {kept.count()} rows in state {before!r} no more; opening the confirmation page changed something (a GET must change nothing)")
            page.goto(base_url + urlsplit(href).path, wait_until="networkidle")
            reply = submitted(page, click=page.locator("main form button.action", has_text=button))
            wait_for_state(page, FORM_READY)
            notices = page.locator("main .notice").all_inner_texts()
            if reply.status != 200 or urlsplit(page.url).path != section or done not in [text.strip() for text in notices]:
                problems.append(f"{label}: the button ended at {page.url} with {reply.status} and the lines {notices}; it goes back to {section} with {done!r}")
            after = dash_row(page, column, name)
            if what == "key" and (after.count() != 1 or after.locator('td[data-label="State"]').inner_text().strip() != "Revoked" or after.locator(f'a[href$="{ending}"]').count()):
                problems.append(f"{label}: the key's row should read Revoked with no Revoke link ({after.count()} rows)")
            if what != "key" and after.count():
                problems.append(f"{label}: the row of {name!r} is still in the list")
            page.reload(wait_until="networkidle")
            if page.locator("main .notice").count():
                problems.append(f"{label}: the line {done!r} shows again after a reload; a message is read once")
            gone = page.goto(base_url + urlsplit(href).path, wait_until="networkidle")
            if gone.status >= 500:
                problems.append(f"{label}: the confirmation page of what is gone answers {gone.status}; a sentence or the shell's 404, never a 500")
    except PlaywrightError as error:
        problems.append(f"the revoke flows: {error.message.splitlines()[0]}")
    finally:
        context.close()
    return problems + [f"the revoke flows: console error: {error}" for error in errors]


def dash_rows_findings(page, label, stacked):
    """The tables of rows on the page as they lie now. Stacked (a column under 40rem: a phone, and the browser checks' 1280): each table a block with no header row, every value that shows led by
    its column's label, a value with nothing in it not drawn, nothing clipped or past the window. A table (about 1300px and up): a table, nothing past the window."""
    problems = []
    for table in page.evaluate(ROWS_STATE):
        words = table["caption"] or "a table of rows"
        if not table["caption"]:
            problems.append(f"{label}: a table of rows has no caption (spec 9; the template's <caption>)")
        if stacked:
            if table["display"] != "block" or not table["headHidden"]:
                problems.append(f"{label}: {words} is display {table['display']} with its header row {'hidden' if table['headHidden'] else 'showing'}; stacked rows are blocks and the header row is clipped (static/css/base.css, the rows pattern)")
            wanted = {heading or "Actions" for heading in table["headings"]}
            for cell in table["cells"]:
                if cell["shown"] and cell["before"].strip('"') != cell["label"]:
                    problems.append(f"{label}: in {words} a value labelled {cell['label']!r} is led by {cell['before']} (td::before shows its data-label)")
                if cell["empty"] and cell["shown"]:
                    problems.append(f"{label}: in {words} the empty value of {cell['label']!r} is drawn; <td data-label=...></td> with nothing in it is not")
                if cell["label"] not in wanted:
                    problems.append(f"{label}: in {words} a value is labelled {cell['label']!r}, which is not a column ({sorted(wanted)})")
        elif table["display"] != "table":
            problems.append(f"{label}: {words} is display {table['display']} where the column is wide; a table (static/css/base.css, @container app)")
        for cell in table["cells"]:
            if cell["shown"] and (cell["right"] > page.viewport_size["width"] + 1 or cell["left"] < -1 or cell["clipped"]):
                problems.append(f"{label}: in {words} the value of {cell['label']!r} lies from {cell['left']:.0f} to {cell['right']:.0f}px in a {page.viewport_size['width']}px window or is clipped; long values wrap (overflow-wrap: anywhere)")
    beyond = page.evaluate(BEYOND)
    if beyond:
        problems.append(f"{label}: these lie past the right edge of the window: {beyond}")
    return problems + sideways_problems(page, label)


def dashboard_rows_problems(browser, base_url, session_key, fixtures):
    """Rows that stack, and the longest values (Review Focus 5, spec 4.10): the owner's Keys (a 255-character name), Files (a 512-character name, a size in gigabytes) and Connected apps
    (a 255-character name) at 375, 320 and the 1280 of the other checks (all stacked: the column is 624px) and at 1440 as a table; the downgraded user's Overview (meters over 100 %,
    four limit lines, a 2.0 GB figure) and the subscriber's Billing (a warning, a payment waiting and its buttons) at 375 and 320. Nothing scrolls sideways and no value is clipped."""
    problems = []
    cases = (("owner", "/app/keys/", True), ("owner", "/app/files/", True), ("owner", "/app/apps/", True), ("over", "/app/", False), ("sub", "/app/billing/", False))
    for who, path, tabled in cases:
        context = dash_context(browser, base_url, fixtures["sessions"][who], width=375, height=667)
        page, errors = fresh_page(context)
        try:
            dash_open(page, base_url, path)
            for width, height, stacked in ((375, 667, True), (320, 640, True), (1280, 800, True)) + (((1440, 900, False),) if tabled else ()):
                set_screen(page, width, height)
                here = f"{path} of the {who} fixture at {width}px"
                problems += dash_rows_findings(page, here, stacked)
                if width == 320 and path == "/app/files/":
                    wide = page.evaluate("() => Math.max(...[...document.querySelectorAll('main table.rows tbody td[data-label=\"File\"]')].map((td) => td.getBoundingClientRect().height))")
                    if wide < 40:
                        problems.append(f"{here}: the tallest file name is {wide:.0f}px; the 512-character name wraps over many lines at 320px, so nothing of it was measured")
        except PlaywrightError as error:
            problems.append(f"{path} of the {who} fixture, rows: {error.message.splitlines()[0]}")
        finally:
            context.close()
        problems += [f"{path} of the {who} fixture, rows: console error: {error}" for error in errors]
    return problems


def expected_local(iso, zone):
    """The time `iso` (a datetime attribute: 2026-10-22T13:58Z) as a visitor in `zone` reads it in English: (month, day, year, hour 1-12, minute, AM or PM, the tz database's short zone name)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    when = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo(zone))
    return MONTHS[when.month - 1], when.day, when.year, when.hour % 12 or 12, when.minute, "AM" if when.hour < 12 else "PM", when.tzname()


def dash_time_findings(page, label, zone, names, *, local):
    """Every <time data-local> on the page: rewritten into `zone` with the zone's short name (the month, day, year, hour and minute of expected_local, an AM or PM, and the zone name last), or, with no
    script (local False), the server's UTC text for the datetime it carries. Some time must be there to look at."""
    from datetime import datetime
    problems, times = [], page.evaluate(TIMES)
    if not times:
        return [f"{label}: the page has no <time data-local>, so no time was looked at (templates/dashboard, the utc_time filter)"]
    for found in times:
        text = found["text"].replace(" ", " ").replace("\xa0", " ")
        if not local:
            when = datetime.fromisoformat(found["iso"].replace("Z", "+00:00"))
            server = f"{when.day} {MONTHS[when.month - 1]} {when.year}, {when:%H:%M} UTC"
            if found["text"] != server or not SERVER_TIME.fullmatch(found["text"]):
                problems.append(f"{label}: without scripts {found['iso']} reads {found['text']!r}, not {server!r} (dashboard/templatetags/dashboard.py utc_time)")
            continue
        try:
            month, day, year, hour, minute, half, short = expected_local(found["iso"], zone)
        except KeyError as error:   # zoneinfo.ZoneInfoNotFoundError: no tz database on this machine
            return problems + [f"{label}: this machine has no time zone database for {zone} ({error}); install tzdata (apt install tzdata) so the expected times can be worked out"]
        accepted = names or (short,)
        if not re.search(rf"{month} {day}, {year}(,| at) {hour}:{minute:02d} {half}", text) or not any(text.endswith(f" {name}") for name in accepted):
            problems.append(f"{label}: {found['iso']} reads {text!r}; in {zone} it is {month} {day}, {year}, {hour}:{minute:02d} {half} {' or '.join(accepted)} (WebKit joins the date and the time with ' at') "
                            f"(static/js/behaviors/local-time.js: a date and a time in the visitor's zone, with the zone's short name)")
    return problems[:2] + ([f"{label}: and {len(problems) - 2} more times of the page read wrong"] if len(problems) > 2 else [])


def dashboard_time_problems(browser, base_url, session_key, fixtures):
    """Times are exact and in the visitor's own zone (the owner's ruling, 2026-10-22): the server writes <time datetime="...Z" data-local> in UTC; with scripts the text is the time in the zone the
    browser reports, named: America/Chicago (CDT or CST by the date), Asia/Kolkata and UTC, on Files, and again after a change in place to Keys (the behaviour runs on veilfile:swapped);
    with scripts off it stays the UTC the server wrote, in its own format."""
    problems = []
    for zone, names in LOCAL_ZONES:
        label = f"/app/files/ in {zone}"
        context = dash_context(browser, base_url, fixtures["sessions"]["owner"], timezone_id=zone)
        page, errors = fresh_page(context)
        try:
            dash_open(page, base_url, "/app/files/")
            problems += dash_time_findings(page, label, zone, names, local=True)
            if zone == "America/Chicago":
                label = f"/app/keys/ in {zone} after a change in place"
                if dash_follow(problems, page, dash_menu_link(page, "/app/keys/"), "/app/keys/", label):
                    problems += dash_time_findings(page, label, zone, names, local=True)
        except PlaywrightError as error:
            problems.append(f"{label}: {error.message.splitlines()[0]}")
        finally:
            context.close()
        problems += [f"{label}: console error: {error}" for error in errors]
    for path in ("/app/files/", "/app/keys/"):
        label = f"{path} without scripts"
        context = dash_context(browser, base_url, fixtures["sessions"]["owner"], timezone_id="America/Chicago", javascript=False)
        page, errors = fresh_page(context)
        try:
            dash_open(page, base_url, path)
            problems += dash_time_findings(page, label, "UTC", None, local=False)
        except PlaywrightError as error:
            problems.append(f"{label}: {error.message.splitlines()[0]}")
        finally:
            context.close()
        problems += [f"{label}: console error: {error}" for error in errors]
    return problems


def refuse_foreign(context, base_url, found):
    """Any request to a host other than the app's is refused and recorded in `found`: the billing buttons must reach Stripe never (the fixtures refuse a server that has a key, and this
    refuses a page that was told to go there). The VEIL stub and the other routes of the context still see the local requests."""
    host = urlsplit(base_url).netloc

    def guard(route):
        if urlsplit(route.request.url).netloc == host:
            route.fallback()
        else:
            found.append(route.request.url)
            route.abort()

    context.route(re.compile(r"^https?://"), guard)


BILLING_DOWN_WORDS = "Billing isn't available right now. Try again later. Reference: XXXXXX"   # dashboard/billing_page.py: what a refusal says when billing is not set up (CI has no Stripe settings)
BILLING_DOWN = re.compile(r"Billing isn.t available right now\. Try again later\. Reference: [0-9A-F]{6}$")
BILLING_OPERATOR_WORDS = ("stripe:", "instance", "configured", "billing_not_configured", "stripe_error")   # ruling R1: none of these on a customer's page


def dashboard_billing_problems(browser, base_url, session_key, fixtures):
    """Billing with no Stripe (CI has none, and the fixtures refuse a server that does): the downgraded user's page shows the unpaid warning, the limits it is over and Upgrade; pressing
    Upgrade or Manage billing comes back to Billing with the customer sentence for "billing is not set up" and a reference (`Billing isn't available right now. Try again later. Reference: XXXXXX`, matched with `Reference: [0-9A-F]{6}$`), a redirect and not a Stripe page, with none of the operator's words on it. The subscriber's page shows the failed payment, the
    switch waiting for payment with Complete payment (a link to Stripe's invoice, opening in a new tab: it is not followed) and Keep Team, and Switch to Scale opens its confirmation page,
    an ordinary load whose button comes back to Billing with the same kind of sentence. Both Billing pages and the confirmation page go through axe and the sideways check. Every request to another host is refused and counted."""
    problems, foreign = [], []

    def configured(page, label, reply):
        notices = [" ".join(text.split()) for text in page.locator("main .notice").all_inner_texts()]   # the page's other notices (a stored status, a limit) stay beside the refusal
        refusal = [text for text in notices if text.startswith("Billing ") or "Reference" in text]
        if reply.status != 200 or urlsplit(page.url).path != "/app/billing/" or not any(BILLING_DOWN.fullmatch(text) for text in refusal):
            problems.append(f"{label}: ended at {page.url} with {reply.status} and the notices {[text[:90] for text in notices]}; with no Stripe it comes back to Billing with one notice that reads {BILLING_DOWN_WORDS!r} (dashboard/billing_page.py)")
        for text in refusal:
            for word in BILLING_OPERATOR_WORDS:
                if word in text.lower():
                    problems.append(f"{label}: the notice {text[:160]!r} holds {word!r}; the dashboard speaks to customers and never shows Stripe's words, a code or the operator's (dashboard/billing_page.py, refusal)")

    context = dash_context(browser, base_url, fixtures["sessions"]["over"])
    refuse_foreign(context, base_url, foreign)
    page, errors = fresh_page(context)
    try:
        dash_open(page, base_url, "/app/billing/")
        words = page.locator("main").inner_text()
        for ok, what in (("Current plan: Free" in words, "the plan line does not read 'Current plan: Free'"),
                         ("unpaid invoice" in words, "the stored unpaid status shows no warning (the Subscription row of the fixture)"),
                         ("over the Free plan" in words, "the over-limit lines are missing"),
                         (page.get_by_role("button", name=re.compile(r"^Upgrade to ")).count() >= 1, "no Upgrade button"),
                         (page.get_by_role("button", name="Manage billing").count() == 1, "no Manage billing button (the fixture has a Stripe customer id)")):
            if not ok:
                problems.append(f"/app/billing/ of the downgraded user: {what}")
        problems += page_problems(page, "/app/billing/ of the downgraded user at 1280px")
        configured(page, "Upgrade, with no Stripe", submitted(page, click=page.get_by_role("button", name=re.compile(r"^Upgrade to ")).first))
        configured(page, "Manage billing, with no Stripe", submitted(page, click=page.get_by_role("button", name="Manage billing")))
    except PlaywrightError as error:
        problems.append(f"Billing of the downgraded user: {error.message.splitlines()[0]}")
    finally:
        context.close()
    problems += [f"Billing of the downgraded user: console error: {error}" for error in errors]
    context = dash_context(browser, base_url, fixtures["sessions"]["sub"])
    refuse_foreign(context, base_url, foreign)
    page, errors = fresh_page(context)
    try:
        dash_open(page, base_url, "/app/billing/")
        words = page.locator("main").inner_text()
        invoice = page.locator('main a[href^="https://invoice.stripe.com/"]')
        # The Switch to Scale names are matched with "." where the label has "/" ("$12/mo"): Playwright 1.49 writes a pattern into its selector
        # as /.../ without escaping a slash in it, and a "/" there ends the pattern (InvalidSelectorError, PR 14's first run).
        for ok, what in (("Your last payment failed" in words, "the stored past_due status shows no warning"),
                         ("waiting for payment" in words, "the switch waiting for payment is not shown (the session hint of the fixture)"),
                         (invoice.count() == 1 and invoice.get_attribute("target") == "_blank" and "noopener" in (invoice.get_attribute("rel") or ""), "Complete payment is not one link to Stripe's invoice opening in a new tab with rel=noopener"),
                         (page.get_by_role("button", name=re.compile(r"^Keep ")).count() == 1, "no Keep button for the plan held"),
                         (page.get_by_role("link", name=re.compile(r"^Switch to Scale \(\$\d+.mo\)")).count() == 1, "no monthly Switch to Scale link"),
                         (page.get_by_role("link", name=re.compile(r"^Switch to Scale \(\$\d+.yr\)")).count() == 1, "no yearly Switch to Scale link")):
            if not ok:
                problems.append(f"/app/billing/ of the subscriber: {what}")
        problems += page_problems(page, "/app/billing/ of the subscriber at 1280px")
        page.get_by_role("link", name=re.compile(r"^Switch to Scale \(\$\d+.mo\)")).click()
        label = "the switch confirmation page"
        dash_land(problems, page, "/app/billing/switch/scale/", None, label, in_place=False)
        found = page.evaluate("() => ({ heading: (document.querySelector('main h1') || { textContent: '' }).textContent.trim(), layout: document.body.dataset.layout || '', forms: document.querySelectorAll('main form[method=post] input[name=csrfmiddlewaretoken]').length })")
        if urlsplit(page.url).path != "/app/billing/switch/scale/" or not found["heading"].startswith("Do you want to switch to Scale") or found["layout"] != "" or found["forms"] != 1:
            problems.append(f"{label}: at {page.url} reads {found['heading']!r}, data-layout {found['layout']!r} and {found['forms']} POST forms; an ordinary page that asks 'Do you want to switch to Scale ($12/mo)?' with one form")
        problems += page_problems(page, f"{label} at 1280px")
        set_screen(page, 375, 667)
        problems += sideways_problems(page, f"{label} at 375px")
        set_screen(page, 1280, 900)
        configured(page, "Switch to Scale, with no Stripe", submitted(page, click=page.locator("main form button.action")))
    except PlaywrightError as error:
        problems.append(f"Billing of the subscriber: {error.message.splitlines()[0]}")
    finally:
        context.close()
    problems += [f"Billing of the subscriber: console error: {error}" for error in errors]
    if foreign:
        problems.append(f"Billing asked another host for {foreign[:3]}; no billing button leaves the app with no Stripe key (dashboard/billing_page.py https_only, drop_billing/account.py)")
    return problems


def dashboard_webkit_problems(playwright, base_url, fixtures):
    """The two things of the dashboard that depend on the engine, in WebKit as an iPhone 13 (the engine of every browser on an iPhone): times rewritten into the visitor's zone (America/Chicago; the
    script parses the server's `2026-10-22T13:58Z`, which has no seconds, and names the zone with Intl), and the Sign out button in the open phone sheet, in the window and taking a tap.
    Skipped, with a line saying so, where WebKit is not installed (the CI job installs it, and pages.tests_site_parts holds that)."""
    try:
        browser = playwright.webkit.launch()
    except PlaywrightError as error:
        if "Executable doesn't exist" in error.message:
            print(WEBKIT_MISSING.replace("the phone menu sheet's WebKit checks were skipped", "the dashboard's WebKit checks were skipped"))
            return []
        return [f"the dashboard in WebKit: WebKit would not start: {error.message.splitlines()[0] if error.message else repr(error)}"]
    problems, label = [], "/app/files/ in WebKit as an iPhone 13"
    device = {**playwright.devices["iPhone 13"], "device_scale_factor": 1}
    context = browser.new_context(**device, timezone_id="America/Chicago", reduced_motion="no-preference")
    context.add_cookies([{"name": "sessionid", "value": fixtures["sessions"]["owner"], "url": base_url}])
    context.add_init_script(seed_storage("light", "on"))
    context.route(VEIL_SCRIPT, lambda route: route.fulfill(status=200, content_type="text/javascript", body=VEIL_STUB))
    shut_after_routes(context)
    page, errors = open_page(context, base_url + "/app/files/")
    try:
        wait_for_state(page, FORM_READY)
        problems += dash_time_findings(page, label, "America/Chicago", None, local=True)
        page.locator("header [data-menu-toggle]").tap()
        page.wait_for_function(SHEET_IN_PLACE)
        problems += dash_sign_out_reach_findings(page, f"{label}, the open sheet")
        problems += dash_sign_out_markup_findings(page, f"{label}, the open sheet")
    except PlaywrightError as error:
        problems.append(f"{label}: {error.message.splitlines()[0]}")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    browser.close()
    return problems


def run_dashboard(browser, base_url, session_key, out, device, fixtures, playwright):
    """The dashboard part: the five sections as the pages part checks a page (the owner fixture's session, not the staff login), then what only the dashboard has, each timed. The checks that
    use up sessions or change what the fixtures made (Sign out, the key flow, the revokes) come last, the revokes after every check that counts rows and keys (the slow-frames walk counts the
    owner's 53 files, so it comes before the revoke of one)."""
    fixtures["run"] = f"{int(time.time()):x}"   # in the names of what a run makes, so a second run meets none of the first's
    problems, first = [], {}
    for site_page in DASHBOARD_PAGES:
        problems += page_states(browser, base_url, fixtures["sessions"]["owner"], out, device, first, site_page, "dashboard")
        problems += timed("dashboard", page_edge_problems, browser, base_url, fixtures["sessions"]["owner"], site_page, out)
    for check in (dashboard_section_problems, dashboard_change_problems, dashboard_rows_problems, dashboard_time_problems, dashboard_billing_problems, dashboard_error_page_problems,
                  dashboard_sign_out_problems, dashboard_slow_frames_problems, dashboard_key_problems, dashboard_revoke_problems):
        problems += timed("dashboard", check, browser, base_url, session_key, fixtures)
    problems += timed("dashboard", dashboard_webkit_problems, playwright, base_url, fixtures)
    return problems


# What Playwright's own tasks fail with when nothing awaits them ("Task exception was never retrieved") is printed to stderr as a notice, not
# raised, so a run could pass with one in its log, or lose it: each is recorded instead and reported as a problem of the run, with the frames
# of this file that started it where Playwright kept them (a route's action keeps the stack of the handler that made it). Only that notice is
# taken: every other thing the loop reports ("Task was destroyed but it is pending", "Exception in callback") goes to the loop's default
# handler and is logged to stderr as it always was.
UNRETRIEVED = []


def note_unretrieved(loop, context):
    if "never retrieved" not in str(context.get("message", "")):
        loop.default_exception_handler(context)
        return
    exception, task = context.get("exception"), context.get("future")
    frames = [f"line {frame.lineno} in {frame.function}" for frame in getattr(task, "__pw_stack__", ()) if frame.filename.endswith("run_site_checks.py")]
    if not frames and type(exception).__name__ == "TargetClosedError":
        # Playwright's own task (its route-pattern update after a route handler ends) racing a page or context we
        # closed: nothing of ours started it or can await it, so it goes to stderr as before, not into the problems.
        loop.default_exception_handler(context)
        return
    UNRETRIEVED.append(f"{context.get('message')}: {exception!r}" + (f", started from {' <- '.join(frames)}" if frames else ", started from no frame of this file"))


def run(base_url, session_key, out, part="all", fixtures=None, dashboard=None):
    """Run a part of the checks (PARTS): `pages`, `cloth`, `ink`, `reading`, `phone`, `forms`, `catalog` and `dashboard` are what CI runs as eight jobs (fixtures: what the forms part needs; dashboard: what the
    dashboard part needs), and `all` is every one, one after the other. Every part starts with the WebGL look, which also prints the pointer and hover media the hover fills answer to (a part that
    follows another in `all` looks again, since the earlier one may have taken WebGL away; the reading, phone, forms, catalog and dashboard parts, which run last and
    load no cloth, need no look)."""
    out.mkdir(parents=True, exist_ok=True)
    EVIDENCE["dir"] = out
    problems = []
    with sync_playwright() as playwright:
        loop = getattr(playwright, "_loop", None)   # the loop Playwright's tasks run in: private, so a release without it skips the hook
        if loop is None:
            print("playwright has no _loop in this version: tasks nobody awaits are not collected as problems of the run")
        else:
            loop.set_exception_handler(note_unretrieved)
        browser = playwright.chromium.launch(args=GL_ARGS)
        device = webgl_precondition(browser, base_url)
        previous = None
        for stage in PART_STAGES[part]:
            if stage == "pages":
                problems += run_pages(browser, base_url, session_key, out, device)
            elif stage == "cloth":
                problems += cloth_problems(browser, base_url, session_key, previous)
            elif stage == "ink":
                problems += ink_part_problems(browser, base_url, session_key, previous)
            elif stage == "reading":
                problems += run_reading(browser, base_url, session_key, out)
            elif stage == "phone":
                problems += run_phone(browser, base_url, session_key, playwright)
            elif stage == "catalog":
                problems += run_catalog(browser, base_url, session_key, out, device)
            elif stage == "dashboard":
                problems += run_dashboard(browser, base_url, session_key, out, device, dashboard, playwright)
            else:
                problems += run_forms(browser, base_url, session_key, out, fixtures)
            previous = stage
        browser.close()
    gc.collect()  # a task nobody awaited reports when it is collected
    problems += [f"playwright: {note}" for note in UNRETRIEVED]
    for note in REVIEW_NOTES:
        print(f"review by eye: {note}")
    (out / "review-by-eye.txt").write_text("".join(f"{note}\n" for note in REVIEW_NOTES), encoding="utf-8")
    for problem in problems:
        print(problem)
    scope = {"pages": f"across {len(PAGES)} page(s)", "cloth": f"in the {len(CLOTH_FAMILIES)} cloth families", "ink": f"in the {len(INK_FAMILIES)} ink families",
             "reading": f"across {len(READING_RUN['pages'])} reading page(s)", "phone": "in the phone menu's checks",
             "forms": f"across {len(FORM_STATES)} form state(s)", "catalog": f"across {len(CATALOG_PAGES)} catalog page(s)", "dashboard": f"across {len(DASHBOARD_PAGES)} dashboard page(s) and their flows",
             "all": f"across {len(PAGES)} page(s), {len(CATALOG_PAGES)} catalog page(s) and {len(READING_RUN['pages'])} reading page(s), in {len(CLOTH_FAMILIES)} cloth and {len(INK_FAMILIES)} ink families "
                    f"and in the phone menu's checks, across {len(FORM_STATES)} form state(s) and across {len(DASHBOARD_PAGES)} dashboard page(s) and their flows"}[part]
    print(f"{len(problems)} problem(s) {scope}; {len(REVIEW_NOTES)} note(s) to review by eye; "
          f"screenshots and review-by-eye.txt in {out}")
    return 1 if problems else 0


def main():
    parser = argparse.ArgumentParser(description="Browser checks for pages on the new shell.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--session-file", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--part", choices=PARTS, default="all",
                        help="pages: every page and the in-place changes; cloth: the WebGL cloth's families; ink: the ink washes, "
                             "the phone menu's sheet included; reading: the Journal, the Docs and the legal documents; "
                             "phone: the phone menu's sheet, the changes chosen from it and the faces they wait for, in Chromium and WebKit; "
                             "forms: the forms and the error pages; catalog: the component catalog's page checks; "
                             "dashboard: the signed-in dashboard, its sections and the flows that start from them; all (the default): every part, one after the other")
    args = parser.parse_args()
    if not AXE_SCRIPT.is_file():
        sys.exit(f"{AXE_SCRIPT} is missing: run `npm ci --prefix site-tests` first.")
    stages = PART_STAGES[args.part]
    sys.exit(run(args.base_url.rstrip("/"), args.session_file.read_text(encoding="utf-8").strip(), args.out, args.part,
             load_form_fixtures(args.session_file.with_name("form-fixtures.json")) if "forms" in stages else None,
             load_dashboard_fixtures(args.session_file.with_name("dashboard-fixtures.json")) if "dashboard" in stages else None))


if __name__ == "__main__":
    main()
