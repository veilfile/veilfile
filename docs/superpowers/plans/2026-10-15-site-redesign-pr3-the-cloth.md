# Site redesign PR 3: the cloth — implementation plan

**Goal:** Draw the cloth behind every page on the new shell: the voile from the prototype's shader on one fixed canvas, clearings that keep text readable over it, the ink wash on what the pointer or keyboard is on, Paper and Ink in the shader, and an adaptive renderer that drops to the still page on a device that cannot keep up, with a still grain that matches the shader's.

**Architecture:** `static/js/veil.js` is one ES module holding the shader (the prototype's, as a string) and everything that drives it; it exports `start({ canvas, gl, off, ended })`. `shell.js` gains a small loader: once the page has loaded, and whenever Motion comes back on, it checks that Motion is on and that a WebGL context can be had, then imports `veil.js` from the hashed URL that `shell.html` puts on `<body data-veil-src>` and hands it the canvas and context. `veil.js` reads everything else from the DOM each frame: clearings from `[data-clearing]` elements, ink targets from `.interactive` and prose links, the appearance from `<html data-theme>`, Motion from `<html data-motion>`. Anything it cannot do turns Motion off for the visit through `off(reason)`, which sets `data-motion="off"` and `data-veil-off` but never touches storage.

**Tech stack:** Django 6.1 templates, plain CSS with tokens, plain ES modules with no build step, WebGL 1 (GLSL ES 1.00), Playwright for Python with Chromium's SwiftShader for the browser checks, axe-core.

**Spec:** `docs/superpowers/specs/2026-10-08-site-redesign-design.md` (binding). Sections used: 2, 3.1 to 3.4, 4.1, 4.2, 4.3, 4.5, 4.10, 6.2, 6.3, 6.4, 7.2, 8, 9, 10, 11 step 3, 13. The reference prototype `docs/superpowers/specs/assets/2026-10-08-site-redesign-prototype.html` is the source of truth for how the cloth looks.

## Global Constraints

Every task's requirements include these.

- **Branch:** `design/site-redesign-pr3`, cut from the latest `origin/main` once the PR 2 follow-ups (`design/pr2-followups`) are merged there; this plan's anchors are the follow-ups' versions of `shell.js`, `base.css`, `run_site_checks.py` and `AGENTS.md`. If they are not merged yet, cut the branch from `design/pr2-followups` and say so in the ledger. Before each task that touches `site-tests/run_site_checks.py` or `backend/static/js/shell.js`, run `git fetch origin && git log --oneline HEAD..origin/main -- site-tests backend/static/js` and, if it lists anything, stop and report: someone else changed the files this plan edits. Never push, merge or open a PR without the owner's go-ahead.
- **Commits:** `git -c user.name=Veilfile -c user.email=support@veilfile.com commit`, with the message the commit step gives and nothing added: no `Co-Authored-By` or `Claude-Session` trailer and no mention of Claude or of any model, whatever a session's attribution reminder says (the owner's rule). Stage only the files the step names; the checkout has untracked `.DS_Store` and `.claude/`, and a fresh worktree also shows the linked `venv`, `site-tests/.venv` and `site-tests/node_modules`.
- **Tests:** `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test <labels>`; the whole suite is the same command with no labels (about 3.5 minutes, 1,031 tests before this PR and 1,037 after it: Task 3 adds five in `tests_veil.py`, Task 4 one corner test). A fresh worktree prints `UserWarning: No directory at: .../backend/staticfiles/`; ignore it.
- **Browser checks:** with the dev server running (`cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py runserver 127.0.0.1:8777 --noreload`) and a staff session made once per database (`cd backend && SITE_SESSION_FILE=../site-tests/session.txt DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py shell -c "exec(open('../site-tests/make_staff_session.py').read())"`; a fresh database needs `manage.py migrate --noinput` first), run from the repo root `site-tests/.venv/bin/python site-tests/run_site_checks.py --base-url http://127.0.0.1:8777 --session-file site-tests/session.txt --out site-tests/artifacts`. Expected at the end of every task: `0 problem(s) across 7 page(s)`; the "note(s) to review by eye" are not problems. Templates are cached with `--noreload`: restart the server after changing a template or Python; static files are read from disk. The run takes about 5.6 minutes before this PR (5.3 on CI's 2-core runner) and about 8 minutes after it: the cloth families add about 2.5 minutes, bound by animation frames rather than CPU (about 60 CPU-seconds), because the probe draws only the frames a check reads. CI's `site` job allows 20 minutes; Task 7 times the run and checks the CPU it uses. A fresh worktree has none of the checkout's environments: link `venv`, `site-tests/.venv` and `site-tests/node_modules` from the main checkout.
- **The cloth in the checks:** every browser in `run_site_checks.py` is launched with `GL_ARGS` (Chromium's SwiftShader), so WebGL exists on any machine. Every context the existing checks open gets a stub `veil.js` (`export function start() {}`), so those checks run exactly as before; only the cloth checks this plan adds load the real file. They drive time with a virtual clock and read the canvas back, so their results do not depend on how fast the machine is. The probe gives every requestAnimationFrame callback of one display frame the same virtual time, because Playwright's `wait_for_function` polls with requestAnimationFrame by default; without that the cloth sees 33 ms frames, steps its resolution down during a run and sits 1 ms from its 'slow' stop, and `run_frames(page, n)` is n display frames. WebGL is lost for the rest of a browser's life by any full-page screenshot taller than 8,192 px: use `full_page_screenshot()` for full-page shots. The probe skips the draw call on every frame no check reads (software WebGL spent about 950 CPU-seconds drawing frames nobody looked at); `cloth_screenshot()` draws for the moment of a screenshot.
- **Never** point a test or a check at veilfile.com, contact production, Stripe or Render, add a runtime dependency to `backend/requirements.txt`, change package versions, add a build step, bundler or third-party request (spec 2, 3.5, 6.3), or touch legal documents, `robots.txt`, `backend/drop_core/plans.py`, the funnel on `/`, or `frontend/`.
- **Budgets** (spec 8): `shell.js` plus `js/behaviors/*.js` at most 10 KB gzipped (7.6 KB before this PR; the loader adds about 0.5 KB); `veil.js` at most 15 KB gzipped (about 8.2 KB as written here). Tests enforce both.
- **Motion** (spec 3.3 as amended): data-motion "on" means the cloth runs; a saved "on" wins over the system's reduced motion. With nothing saved, reduced motion loads the Off state and `veil.js` is never fetched. "Off" fades the cloth out over about a second and then stops everything: no loop, no GPU work, no canvas. Automatic Off (no WebGL, a device too slow, a lost context, an error) lasts for that visit only: never written to storage.
- **Colours:** in CSS only through tokens in `static/css/tokens.css` (the existing test). The shader's five colours per appearance live in `veil.js` (spec 3.2) and a test keeps its ground equal to `--paper` in both appearances.
- **Layering:** the canvas is `position: fixed; z-index: -1` at the start of `<body>`, under everything; nothing inside `<main>` may use a `z-index` above 1 (corners 3, top veil 2, phone sheet 4 to 6, skip link 10).
- **Accessibility** (spec 9): the canvas is `aria-hidden="true"`, takes no pointer events and no focus. Small text keeps 4.5:1 over the moving cloth, guaranteed by the clearings: 0.88 strength behind a content block (spec), 0.92 behind a label (this plan, Rulings). Muted text measured against the darkest and lightest pixel the shader draws under it, across a whole cycle of the cloth, at 1280x800, 1024x640 and 375x667 in both appearances (measured while planning): content 4.67:1 worst, labels 4.80:1 worst. The threshold's line is large display text: 3:1.
- **Muse keeps this site** (spec 1.4, 7): every new convention has a test whose failure names the file and the fix, and `AGENTS.md` stays true (Task 7).

## Review Focus

These are the conditions a visitor can bring that no task's main tests fully exercise. Each has a test in the task that owns the code.

1. **A device that cannot keep up** (a low-end phone, software WebGL, a tab that comes back after a long time hidden). The cloth lowers its resolution, then turns to the still page for the visit; it never freezes half-drawn, never leaves a blank or dark page, and never writes the automatic Off to storage. A single long frame (a tab coming back) changes nothing. Tests: Task 6's adaptive checks.
2. **Motion switched rapidly, or while the cloth is still loading or fading** (ten clicks in a row; Motion on during the fade-out; on under reduced motion). There is never more than one canvas, no WebGL context is leaked (no "Too many active WebGL contexts" warning), and the Motion button always says what `data-motion` says. Tests: Task 3's lifecycle checks.
3. **Small text over the moving cloth** at every moment once its clearing has eased in (about a second after the text appears; an in-place chapter change hands the old block's clearing to the new one, so there is nothing to ease), in both appearances, at phone and desktop widths, scrolled, after an in-place chapter change and after Come in: 4.5:1 for labels and content, 3:1 for the threshold's line. axe cannot see a canvas, so the canvas is read back and measured. Tests: Task 4's clearing checks.
4. **WebGL missing, refused, failing to compile, or lost mid-visit.** The still page, Motion off for the visit, the reason in `data-veil-off`, no console error beyond the one failed request, and the page fully usable. Tests: Task 3 (missing, refused, import failing, compile failing) and Task 6 (lost).
5. **In-place chapter changes, Come in, the menu opening, scrolling and resizing while the cloth runs.** The clearings follow the text, the canvas and its context persist across the change ("the cloth continues uninterrupted", spec 4.5), and the cloth never pops. Tests: Task 3 (canvas persists), Task 4 (clearings follow, and the cloth behind the block does not jump when the chapter changes).

## Rulings made while planning

Each is a decision the spec left open or a place where spec, prototype and the built code disagree; the executor follows them. Measurements are from the planning session's harness (the plan's `veil.js` on a page shaped like the shell, Chromium 131, Apple GPU and SwiftShader, which gave identical numbers under the virtual clock).

- **Who fetches `veil.js`:** `shell.js`'s loader, from `<body data-veil-src="{% static 'js/veil.js' %}">` (the hashed URL in production; a relative `import()` from a hashed `shell.js` would not be hashed). It runs on `load` (spec 8: "veil.js loads after the page is ready") and whenever `data-motion` becomes "on". "WebGL works" means `getContext("webgl", { failIfMajorPerformanceCaveat: true, alpha: false, antialias: false, depth: false, powerPreference: "low-power" })` returns a context; that context and its canvas are handed to `veil.js`, so only one is ever made per start.
- **The canvas slot** (spec 6.2) is the start of `<body>`, filled by `veil.js`: there is no canvas in the markup, so none exists without JavaScript or with Motion off, and "removed, not paused" (spec 2) is literal. The canvas is fixed to the viewport, so "the canvas is off-screen" (spec 8) cannot happen; only a hidden tab pauses it. Spec 6.2 and 8 are amended to say so (Task 7).
- **Observable state:** `<html data-veil="on">` while the cloth draws; `<html data-veil-off="no-webgl|error|slow|lost">` after an automatic Off. The Motion button follows `data-motion` through a MutationObserver, so it says "· off" after an automatic Off too. Clicking Motion on again clears `data-veil-off` and tries once more: the visitor asked.
- **First reveal:** the cloth fades in from plain paper over about a second (the same easing as Motion on), so it never pops over the still page.
- **Framing at other aspect ratios:** the prototype's composition was made for a 16:10 frame centred on (0.8, 0.5) in cloth space; every canvas keeps that centre and the prototype's zoom, so a phone shows the middle of the composition and a wide screen more of its sides.
- **GLSL:** two constructs GLSL ES 1.00 leaves undefined in the prototype's `wash()` are rewritten without changing the look: `smoothstep(1.0, 0.88, r)` becomes `1.0 - smoothstep(0.88, 1.0, r)`, and `pow(x, 2.0)` of a possibly negative `x` becomes `x * x`. A device with fewer than 32 fragment uniform vectors cannot run the cloth: `start()` throws and the visit gets the still page with `data-veil-off="error"` (`veil.js` needs about 18).
- **Clearings:** `data-clearing="content"` or `data-clearing="label"` on an element gives it a clearing while it is shown (not `hidden`, laid out, and visible by `checkVisibility`). At most one content block and five labels at once. The content block keeps the prototype's soft edge (from 3% of the window height inside to 24% outside) at strength 0.88. A label's rectangle is its box padded by 12 CSS px, full strength inside and fading out over 28 CSS px, at strength 0.92 (0.88 left labels at 4.5:1 with no margin over the brightest Ink folds). Weights ease in and out over about a second (95% after 1 s, faster than the prototype's 1.3 per second, so text is never long over a part-cleared cloth); on an in-place change the new chapter's block takes over the old block's weight and the old element eases out where it was, so the cloth behind the text does not jump (spec 4.5); rectangles are re-read every frame, so they follow scrolling, resizing, fonts arriving, the menu opening and in-place changes without any event. The four corners carry `data-clearing="label"` (the top-right corner's box includes the open menu column, so that clearing covers the menu); chapters' `.chapter` carries `data-clearing="content"` through the layout; the threshold's `.chapter` does not (spec 4.2: the line sits on the bare cloth), and its install line carries `data-clearing="content"`, so the clearing opens behind it on Come in.
- **"Keeping 20% of its variation"** (spec 3.1): the prototype's formula, the source of truth for the look, keeps about 30% at strength 0.88 (`1 - 0.8 x strength`). The formula is kept and the spec's number amended (Task 7).
- **Pace:** 0.75 on the threshold, 0.6 on chapters (spec), and 0.65 after Come in ("slows slightly", spec 4.2), eased at the prototype's rate.
- **Ink:** a wash grows under any `.interactive` element or prose link (`.chapter :is(p, dd) a:not(.interactive, .label)`) when the pointer is over it or it has keyboard focus (`:focus-visible`), from 64rem up only, and never inside a catalog frame. Below 64rem the top corners sit on the opaque top veil and the open menu is an opaque sheet, so the flat fill stays the answer there; phones are touch devices, where hover barely exists. While an element is inked it carries `is-inked`, and CSS turns its flat fill off; so the fill is still there before the cloth loads, after it fails, with Motion off, and in the existing checks (which stub `veil.js`). At most four washes, one of which can be the current chapter's held wash while the menu is open. Every time `veil.js` uses comes from the animation frame's timestamp, so the checks' virtual clock drives inks, easing and the adaptive measurement alike.
- **Adaptive rendering:** scale 0.8 of CSS pixels on screens wider than 40rem, 0.5 at 40rem and below ("phones"; the same breakpoint as the 17.5px reading size), whatever the device pixel ratio. Frames are measured in windows of 60 after 30 ignored ones; a window whose median frame is longer than 24 ms steps the scale down (0.8, 0.6, 0.45, 0.35; phones 0.5, 0.375, 0.28), never up; at the lowest step, a median above 40 ms (slower than 25 frames a second) turns to the still page for the visit (`data-veil-off="slow"`); 30 Hz, where iOS Low Power Mode caps animation frames, keeps the cloth at its lowest step. A frame's time step is capped at 0.1 s, so a hidden tab's gap neither jumps the cloth nor reads as slow, and a hidden tab draws nothing.
- **Context loss:** the still page for the visit (`data-veil-off="lost"`); no attempt to restore.
- **Still grain** (spec 3.3, "must match the shader's grain closely"): the shader's grain is zero-mean, uniform ±0.025 (3.7 levels of 255 standard deviation); the PR 1 tile (`grain.png`, grey at alpha 0 to 18) only darkens Paper and only lightens Ink, by a mean of about 3.8 levels, at about 60% of the strength. It is replaced by one tile per appearance (`grain-paper.png`, `grain-ink.png`), each zero-mean over its own ground with the shader's spread, chosen through a `--grain` token. The tile's cells are 1 CSS px against the shader's 1.25; that difference is accepted.
- **The whole-scene fade** (spec 3.2, "a switch fades the whole scene in about 1.5 seconds"): the shader eases its appearance at the prototype's rate, and `html` transitions its ground and text colour over `--scene-fade: 1.4s`; with Motion off both are instant.
- **The flat fill under Motion off appears at once**, not in 0.15 s: PR 1's kill switch (`[data-motion="off"] *`) stops every transition, and Motion off is a choice for stillness. Spec 3.4 is amended (Task 7).
- **The catalog** (`/app/components/`) runs the cloth behind its frames like any shell page; the frames are opaque and carry their own `data-theme`/`data-motion`, so inside them the components keep the flat fill.
- **A real mid-range Android phone** (spec 13) cannot be measured by an agent: the PR notes record it as the owner's to do.

---

## File structure

```
backend/
  templates/shell.html                 data-veil-src on <body>; data-clearing="label" on the four corners
  templates/layouts/chapter.html       {% block chapter_clearing %} on .chapter
  templates/chapters/threshold.html    no clearing on .chapter; data-clearing="content" on the install line
  static/js/veil.js                    NEW: the shader and everything that drives it
  static/js/shell.js                   the loader; the Motion button follows data-motion
  static/css/tokens.css                --grain (per appearance), --scene-fade
  static/css/base.css                  the canvas; the scene fade; var(--grain); the ink replaces the fill
  static/img/grain-paper.png grain-ink.png   NEW (generated); grain.png DELETED
  pages/tests_veil.py                  NEW: veil.js's budget, colours, shader and loader contract
  pages/tests_images.py                the new grain tiles' statistics
  pages/tests_shell.py tests_chapters.py   the markup this PR changes (tests_static.py is run, not changed)
tools/make_images.py                   the two grain tiles
site-tests/run_site_checks.py          GL_ARGS, the veil.js stub, the WebGL precondition, the cloth checks
AGENTS.md                              repo map, Site checks, recipes
docs/superpowers/specs/2026-10-08-site-redesign-design.md   amendments (Task 7)
```

---

### Task 1: Browser checks that can see WebGL, and a stub `veil.js` for every existing check

**Files:**
- Modify: `site-tests/run_site_checks.py`

**Interfaces:**
- Consumes: `new_context`, `run`, `TOKENS_STYLESHEET` (PR 1 and PR 2).
- Produces:
  - `GL_ARGS` (Chromium launch arguments for SwiftShader), `VEIL_SCRIPT` (a route pattern for `/static/js/veil.js`, hashed or not), `VEIL_STUB` (an empty `start`).
  - `new_context(..., cloth=False)`: unless `cloth=True`, the context answers every request for `veil.js` with `VEIL_STUB`, so the existing checks never run the cloth.
  - `WEBGL_INFO` and `webgl_precondition(browser, after=None)`: stops the run with a plain reason when the browser has no WebGL, or only WebGL that Chromium calls a major performance caveat (which `shell.js` refuses); otherwise prints the renderer. With `after` (Task 3 calls it again before the cloth checks) it prints nothing when all is well and names what came before when WebGL has gone.
  - `run()` launches Chromium with `GL_ARGS` and calls `webgl_precondition` first.

- [ ] **Step 1: Add the launch arguments, the stub and the precondition**

First, before changing anything, start the server and run the browser checks once with `time` in front on the unchanged tree; note the total. It is the baseline for Step 3.

In `site-tests/run_site_checks.py`, after the line `TOKENS_STYLESHEET = re.compile(r".*/css/tokens(\.[0-9a-f]+)?\.css$")`, add:

```python
VEIL_SCRIPT = re.compile(r".*/js/veil(\.[0-9a-f]+)?\.js$")
# One software rasteriser everywhere (SwiftShader, bundled with Chromium), so WebGL exists on any
# machine and CI matches a laptop. Without the second switch Chromium logs a deprecation warning.
GL_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
# What the existing checks get instead of veil.js: the page stays as it is without the cloth (the
# flat fills, data-motion as stored), and no GPU work slows the run. The cloth checks load the real one.
VEIL_STUB = "export function start() {}"
```

Replace the whole `new_context` function with:

```python
def new_context(browser, base_url, session_key, site_page, *, width, theme, motion, height=900,
                javascript=True, reduced_motion="no-preference", block_storage=False, cloth=False):
    context = browser.new_context(viewport={"width": width, "height": height},
                                  java_script_enabled=javascript, reduced_motion=reduced_motion)
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base_url)
    if site_page.signed_in:
        context.add_cookies([{"name": "sessionid", "value": session_key, "url": base_url}])
    context.add_init_script(BLOCK_STORAGE if block_storage else seed_storage(theme, motion))
    if not cloth:
        context.route(VEIL_SCRIPT, lambda route: route.fulfill(status=200, content_type="text/javascript", body=VEIL_STUB))
    return context
```

After the `open_page` function, add:

```python
WEBGL_INFO = """(strict) => {
  const gl = document.createElement("canvas").getContext("webgl", strict ? { failIfMajorPerformanceCaveat: true } : {});
  if (!gl) return null;
  const debug = gl.getExtension("WEBGL_debug_renderer_info");
  return { renderer: gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : gl.RENDERER),
           uniforms: gl.getParameter(gl.MAX_FRAGMENT_UNIFORM_VECTORS), cores: navigator.hardwareConcurrency };
}"""


def webgl_precondition(browser, after=None):
    """Stop at once if this Chromium has no WebGL (the cloth checks would all fail for that reason
    alone), and print what renders it, so every log records the machine's GPU path. With after, it is a
    look in the middle of the run: something took WebGL away (a full-page screenshot taller than 8,192 px
    does), and one plain message beats a page of unrelated failures."""
    page = browser.new_page()
    info = page.evaluate(WEBGL_INFO, True)
    # shell.js asks for failIfMajorPerformanceCaveat: true, so a context that only exists without it is no
    # WebGL for the cloth: ask again without the option, to say which of the two it is.
    anything = info or page.evaluate(WEBGL_INFO, False)
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
```

In `run()`, replace

```python
        browser = playwright.chromium.launch()
```

with

```python
        browser = playwright.chromium.launch(args=GL_ARGS)
        webgl_precondition(browser)
```

- [ ] **Step 2: Watch the precondition refuse a browser without WebGL**

Run from the repo root:

```bash
site-tests/.venv/bin/python -c "
import sys; sys.path.insert(0, 'site-tests')
import run_site_checks as checks
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    for args in (['--disable-gpu'], checks.GL_ARGS):
        browser = p.chromium.launch(args=args)
        try:
            checks.webgl_precondition(browser)
        except SystemExit as stop:
            print('stopped:', stop)
        browser.close()
"
```

Expected: first `stopped: This Chromium has no WebGL at all (launched with --use-angle=swiftshader --enable-unsafe-swiftshader); the cloth checks need it.` (the message names `GL_ARGS`, the arguments the run uses), then `WebGL: ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device ...` with a fragment-uniform count of 1024 or more (4096 on Chromium with SwiftShader today).

- [ ] **Step 3: Run the browser checks**

Start the server and run the browser checks (Global Constraints). Expected: the first line is `WebGL: ANGLE (Google, Vulkan ... SwiftShader ...`, and the last is `0 problem(s) across 7 page(s)`. Note how long the run took (`time` in front of the command) against the baseline run you took before Step 1 (5:37 on the planning laptop): the existing checks now share the browser with SwiftShader but draw no cloth, so the two should be within about 10%. If the new run is more than 25% slower, report it as DONE_WITH_CONCERNS with both times.

- [ ] **Step 4: Commit**

```bash
git add site-tests/run_site_checks.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Browser checks that can see WebGL, with a stub cloth for the existing ones

Chromium launches with SwiftShader so WebGL exists on any machine, the run
stops at once if it does not, and every existing check gets an empty veil.js
so it runs exactly as before; the cloth checks will load the real one.
EOF
```

---

### Task 2: A still grain that matches the shader's

**Files:**
- Modify: `tools/make_images.py`; generate `backend/static/img/grain-paper.png` and `backend/static/img/grain-ink.png`; delete `backend/static/img/grain.png`
- Modify: `backend/static/css/tokens.css`, `backend/static/css/base.css`
- Modify (tests): `backend/pages/tests_images.py`, `site-tests/run_site_checks.py`

**Interfaces:**
- Consumes: `png(width, height, pixel)` in `tools/make_images.py`; `read_png` in `tests_images.py`.
- Produces: token `--grain` (Paper: `url("../img/grain-paper.png")`, Ink: `url("../img/grain-ink.png")`), used by `html` and the top veil; `grain_problems(page, label, appearance)` in the site checks.

- [ ] **Step 1: Write the failing test**

In `backend/pages/tests_images.py`, replace the whole method `test_grain_is_a_subtle_neutral_tile` with:

```python
    def test_still_grain_matches_the_shaders_on_each_ground(self):
        # The shader adds a zero-mean grain, uniform within +-0.025 (+-6.4 levels of 255, a standard
        # deviation of 3.7 levels); the still page's tile must match it closely (spec 3.3) on its own
        # ground: pure white or black specks whose alpha lightens or darkens the ground by that much.
        for name, ground in (("grain-paper.png", (0xEB, 0xE6, 0xDE)), ("grain-ink.png", (0x1B, 0x19, 0x17))):
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
                self.assertTrue(3.1 <= spread <= 4.3, f"{name}: the grain's spread is {spread:.2f} levels; the shader's is 3.7")
                self.assertLessEqual(max(abs(d) for d in deltas), 7.5, f"{name}: a speck moves its ground more than the shader's grain can")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_images -v 2`
Expected: ERROR for `test_still_grain_matches_the_shaders_on_each_ground` (both subtests) with `FileNotFoundError` for `grain-paper.png`.

- [ ] **Step 2: Draw the two tiles**

In `tools/make_images.py`, replace the whole `grain` function with:

```python
def grain(ground, size=128, seed=7):
    """The still grain over one appearance's ground: pure white or black specks whose alpha moves
    the ground by a uniform amount within +-6.4 levels of 255, the shader's film grain (spec 3.3)."""
    rng = random.Random(seed)
    level = 0.2126 * ground[0] + 0.7152 * ground[1] + 0.0722 * ground[2]
    specks = []
    for _ in range(size * size):
        delta = rng.uniform(-6.4, 6.4)
        if delta >= 0:
            specks.append((255, 255, 255, round(delta / (255 - level) * 255)))
        else:
            specks.append((0, 0, 0, round(-delta / level * 255)))
    return lambda x, y: specks[y * size + x]
```

In `main()`, replace the line

```python
    (IMG / "grain.png").write_bytes(png(128, 128, grain()))
```

with

```python
    (IMG / "grain-paper.png").write_bytes(png(128, 128, grain((0xEB, 0xE6, 0xDE))))
    (IMG / "grain-ink.png").write_bytes(png(128, 128, grain((0x1B, 0x19, 0x17))))
```

and change the count in the final `print` (the line that says how many images were written) to one more than it says now. Then run, from the repo root: `python3 tools/make_images.py && git rm -q backend/static/img/grain.png && git status --short backend/static/img`.
Expected: the print line; `git status` shows `grain-paper.png` and `grain-ink.png` new and `grain.png` deleted, and no other image changed (the generator is deterministic).

- [ ] **Step 3: Use them through a token**

In `backend/static/css/tokens.css`, in the first block (`:root, [data-theme="light"], [data-theme="system"]`), after `--box: rgba(240, 236, 229, .35);` add:

```css
  --grain: url("../img/grain-paper.png"); /* the still grain over this ground (spec 3.3) */
```

In the `[data-theme="dark"]` block and in the `@media (prefers-color-scheme: dark)` block, after each `--box: rgba(30, 28, 26, .35);` (indented four spaces inside the `@media` block) add:

```css
  --grain: url("../img/grain-ink.png");
```

(Both files live in `static/css/`, so the relative URL means the same thing wherever `var(--grain)` is used; `collectstatic` rewrites it to the hashed name in production.)

In `backend/static/css/base.css`, replace `  background-image: url("../img/grain.png");` with `  background-image: var(--grain);`, and replace `    background: url("../img/grain.png") fixed, var(--paper);` with `    background: var(--grain) fixed, var(--paper);`.

- [ ] **Step 4: Check the tile follows the appearance in the browser**

In `site-tests/run_site_checks.py`, after `webgl_precondition`, add:

```python
def grain_problems(page, label, appearance):
    """The still grain is the tile made for this appearance's ground (spec 3.3)."""
    tile = "grain-ink" if appearance == "dark" else "grain-paper"
    found = page.evaluate("() => getComputedStyle(document.documentElement).backgroundImage")
    problems = [] if tile in found else [f"{label}: the page's grain is {found}; it should be {tile}.png"]
    if page.evaluate("() => innerWidth < 1024"):  # the top veil carries the same grain below 64rem (base.css)
        veil = page.evaluate("() => { const veil = document.querySelector('.edge-veil'); return veil && getComputedStyle(veil).backgroundImage; }")
        if veil is not None and tile not in veil:
            problems.append(f"{label}: the top veil's grain is {veil}; it should be {tile}.png")
    return problems
```

In `run()`, immediately before the line `problems += page_problems(page, label, display_text=site_page.chapter or site_page.threshold)` (after the screenshot; `axe_problems`, which `page_problems` ends with, sets the page's background-image to none, so the grain must be read first), add:

```python
                        problems += grain_problems(page, label, appearance)
```

- [ ] **Step 5: Run the tests and the browser checks**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages -v 1`. Expected: OK.
Run the full suite. Expected: OK.
Run `cd backend && DJANGO_DEBUG=False DJANGO_SECRET_KEY=local-check-not-a-secret DJANGO_ALLOW_SQLITE=True ../venv/bin/python manage.py collectstatic --noinput --verbosity 1 && grep -o 'grain-[a-z]*\.[0-9a-f]*\.png' staticfiles/css/tokens.*.css | sort -u && rm -rf staticfiles`. Expected: both hashed names, once each after `sort -u` (`grain-ink` is in two blocks of the token stylesheet; the manifest rewrote the token's URLs).
Run the browser checks. Expected: `0 problem(s) across 7 page(s)`. Look at `about-1280-light-motion-off.png` and `about-1280-dark-motion-off.png`: the grain has the same fine cells as before. Compared with the old screenshots, Paper is about 3 levels lighter and Ink about 4 levels darker overall (the old tile pulled each off its ground; each now averages to its --paper) and the grain is about 1.7 times stronger (spread 2.2 to 3.7 levels), as intended.

- [ ] **Step 6: Commit**

```bash
git add tools/make_images.py backend/static/img/grain-paper.png backend/static/img/grain-ink.png \
  backend/static/css/tokens.css backend/static/css/base.css backend/pages/tests_images.py site-tests/run_site_checks.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
A still grain that matches the shader's, one tile per appearance

The old tile only darkened Paper and lightened Ink, at about 60% of the
shader's strength. Each appearance now has its own tile, zero-mean over its
ground with the shader's spread, chosen through a --grain token, so the
still page and the cloth's grain look alike when one gives way to the other.
EOF
```

(`grain.png` is already staged as deleted by Step 2's `git rm`.)

---

### Task 3: The cloth: `veil.js`, its loader, and when it runs

**Files:**
- Create: `backend/static/js/veil.js`, `backend/pages/tests_veil.py`
- Modify: `backend/static/js/shell.js` (the loader; the Motion button follows `data-motion`), `backend/templates/shell.html` (`data-veil-src` on `<body>`), `backend/static/css/base.css` (the canvas; the scene fade), `backend/static/css/tokens.css` (`--scene-fade`)
- Modify (tests): `site-tests/run_site_checks.py`

**Interfaces:**
- Consumes: `GL_ARGS`, `VEIL_SCRIPT`, `new_context(..., cloth=)` (Task 1); `--grain` (Task 2); `save`, `root`, `MOTION_KEY` in `shell.js`.
- Produces:
  - `veil.js` exports `start({ canvas, gl, off, ended })`. It puts `canvas` (class `veil`, `aria-hidden="true"`) first in `<body>`, sets `<html data-veil="on">` after its first frame, reads clearings from `[data-clearing]` (Task 4) and ink targets (Task 5) each frame, and on any failure calls `off(reason)` with `"error"`, `"slow"` or `"lost"`. When it has gone (Motion off, or a failure) it removes the canvas and `data-veil` and calls `ended()`.
  - `shell.js`: `veilOff(reason)` sets `data-veil-off` and `data-motion="off"` (never storage); `startVeil()` and `setUpVeil()`; the Motion button follows `data-motion` through a MutationObserver, and clicking it on clears `data-veil-off`.
  - `<body data-veil-src="{% static 'js/veil.js' %}">` on every shell page.
  - Site checks: `CLOTH_PROBE`, `NO_WEBGL`, `SHADER_FAILS`, `FREEZE` (cloth time held still from the first frame), `VEIL_STATE`, `cloth_context()`, `cloth_running()`, `run_frames()`, `capture()`, `canvas_stats()`, `cloth_screenshot()`, `full_page_screenshot()`, `page_named()`, `cloth_problems()` (Tasks 4 to 6 add families to its list), and `open_page(..., warnings=True)` (WebGL warnings count as errors).

- [ ] **Step 1: Write the failing Django tests**

Create `backend/pages/tests_veil.py`:

```python
"""The cloth (spec 3.1 to 3.3, 6.3, 8): veil.js's budget and colours, and how the shell loads it."""
import gzip
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from pages.tests_shell import render_shell

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
        self.assertIn("export function start({ canvas, gl, off, ended })", VEIL.read_text(encoding="utf-8"))

    def test_the_shell_hands_veil_js_its_url_and_never_loads_it_as_a_script(self):
        html = render_shell()
        self.assertIn('data-veil-src="/static/js/veil.js"', html)
        self.assertNotRegex(html, r"<script[^>]*veil\.js",
                            "shell.html: veil.js is imported by shell.js only when Motion is on and WebGL works (spec 6.3)")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_veil -v 2`
Expected: ERROR or FAIL for all five (`FileNotFoundError` for `static/js/veil.js`; no `data-veil-src` in the shell).

- [ ] **Step 2: Write `veil.js`**

Create `backend/static/js/veil.js` with exactly this content (the shader is the prototype's; the changes are marked in its comments and listed in the plan's Rulings):

```js
// The cloth (spec 3.1 to 3.4, 8): a fragment shader drawn on one fixed canvas behind the page, with
// clearings behind the text and an ink wash on whatever the pointer or the keyboard is on. shell.js
// imports this module only when Motion is on and WebGL works, and calls start() with a canvas and
// its context; everything after that is decided here. The shader is the prototype's
// (docs/superpowers/specs/assets/), with the spec's additions marked where they differ.

const ZOOM = 0.6;
const MAX_INKS = 4;
const PACE = { threshold: 0.75, entered: 0.65, chapter: 0.6 };   // spec 3.1, 4.2: the cloth slows after Come in
const CONTENT = 0.88;   // clearing strength behind a content block (spec 3.1)
const LABEL = 0.92;     // behind a corner label or the open menu: enough for 4.5:1 over the darkest fold (spec 9)
const LABEL_PAD = 12;   // CSS px added around a label's box
const LABEL_EDGE = 28;  // CSS px over which a label's clearing fades out
const SCALES = { desktop: [0.8, 0.6, 0.45, 0.35], phone: [0.5, 0.375, 0.28] };   // canvas px per CSS px
const SLOW_MS = 24;     // a window whose median frame is longer than this lowers the resolution
const STILL_MS = 40;    // at the lowest resolution, longer than this switches to the still page (30 Hz, where iOS Low Power Mode caps frames, keeps the cloth)
const WINDOW = 60;      // frames per measurement
const WARMUP = 30;      // frames ignored after a start, a resume or a resolution change
const PHONE = window.matchMedia("(max-width: 40rem)");
const WIDE = window.matchMedia("(min-width: 64rem)");
const DARK = window.matchMedia("(prefers-color-scheme: dark)");
const INK_TARGETS = ".interactive, .chapter :is(p, dd) a:not(.interactive, .label)";

const VERT = "attribute vec2 a; void main(){ gl_Position = vec4(a, 0.0, 1.0); }";
const FRAG = `precision highp float;
uniform vec2 u_res; uniform float u_time; uniform float u_theme; uniform float u_cloth; uniform float u_scale;
uniform vec4 u_clear[6];   // centre x, y and half size (canvas px): 0 is the content block, 1 to 5 are labels
uniform vec4 u_clearW[2];  // how far each clearing has eased in: [0].x is clearing 0, [0].y clearing 1, ...
uniform vec4 u_inkP[${MAX_INKS}];   // centre x, y (canvas px), radius x, y
uniform vec4 u_inkQ[${MAX_INKS}];   // growth 0..1, strength 0..1, age (s), seed

float hash(vec2 p){ p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
float noise(vec2 p){ vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y); }
float fbm(vec2 p){ float v = 0.0, a = 0.5; mat2 m = mat2(1.6, 1.2, -1.2, 1.6);
  for (int i = 0; i < 4; i++) { v += a * noise(p); p = m * p; a *= 0.5; } return v; }

const vec3 L = vec3(-0.451, 0.551, 0.702);      // key light from the upper left
const vec3 H = vec3(-0.2445, 0.2986, 0.9225);   // halfway between the light and the eye
// The five colours of each appearance (spec 3.2), blended so a switch fades across.
vec3 PAPER, LIT, SHADE, SHEEN, WASH;
void appearance(float k){
  PAPER = mix(vec3(0.922, 0.902, 0.871), vec3(0.104, 0.098, 0.091), k);   // paper | warm slate
  LIT   = mix(vec3(0.992, 0.952, 0.880), vec3(0.300, 0.302, 0.312), k);   // vanilla | faint cool light
  SHADE = mix(vec3(0.735, 0.728, 0.712), vec3(0.052, 0.049, 0.046), k);   // cool grey | near black
  SHEEN = mix(vec3(0.080, 0.078, 0.074), vec3(0.125, 0.135, 0.150), k);   // the sheen matters more at night
  WASH  = mix(vec3(0.235, 0.210, 0.185), vec3(0.880, 0.850, 0.800), k);   // dark ink | pale wash
}

// A slow swell of air moving under the cloth; every layer rides on it.
float swell(vec2 p, float t){
  vec2 c = vec2(0.8 + 0.5 * sin(t * 0.071), 0.5 + 0.28 * sin(t * 0.053 + 1.0));
  return 0.09 * exp(-dot(p - c, p - c) / 0.2);
}

vec3 cloth(vec3 n){
  float diff = clamp(dot(n, L) / L.z, 0.0, 1.5);
  vec3 c = mix(SHADE, LIT, smoothstep(0.2, 1.15, diff));
  return c + SHEEN * pow(max(dot(n, H), 0.0), 28.0);   // soft sheen on the crests
}

// The sheet underneath everything: broad diagonal folds.
float baseH(vec2 p, float t){
  vec2 d = vec2(0.83, 0.56);
  float u = dot(p, d), v = dot(p, vec2(-d.y, d.x));
  float w = fbm(p * 0.9 + vec2(0.0, t * 0.02));
  return 0.10 * sin(u * 4.2 + 2.2 * w + t * 0.11) + 0.04 * sin(u * 7.5 - v * 1.6 + t * 0.17 + 1.3 * w) + swell(p, t);
}

// One turned-over band of cloth. x: distance inside its nearest edge, y: along it, z: across it.
vec4 band(vec2 p, float t, float i){
  vec2 a; float ang, W;
  if (i < 0.5) { a = vec2(0.50, 0.80); ang = -0.38; W = 0.20; }
  else if (i < 1.5) { a = vec2(1.20, 0.36); ang = 0.62; W = 0.24; }
  else { a = vec2(0.30, 0.18); ang = 0.22; W = 0.17; }
  vec2 dir = vec2(cos(ang), sin(ang)), nn = vec2(-dir.y, dir.x);
  float along = dot(p - a, dir);
  float s = dot(p - a, nn) - 0.08 * sin(along * 1.9 + t * 0.16 + i * 2.3) - 0.028 * sin(along * 4.1 - t * 0.23 + i);
  float hw = W * (0.55 + 0.45 * sin(along * 1.5 + t * 0.19 + i * 2.1)) + 0.02;   // the band twists and narrows
  return vec4(hw - abs(s), along, s, hw);
}
float bandH(vec4 r, vec2 p, float t, float i){ return 0.012 * sin(r.z * 34.0 + r.y * 1.2 + t * 0.3 + i) + swell(p, t); }

vec3 layBand(vec3 col, vec2 p, float t, float i){
  col *= 1.0 - 0.12 * smoothstep(-0.03, 0.05, band(p + L.xy * 0.04, t, i).x);   // its soft shadow on the cloth below
  float e = 0.003;
  vec4 r = band(p, t, i);
  if (r.x < -0.004) return col;
  vec4 rx = band(p + vec2(e, 0.0), t, i), ry = band(p + vec2(0.0, e), t, i);
  float h = bandH(r, p, t, i);
  vec3 nf = normalize(vec3(-(bandH(rx, p + vec2(e, 0.0), t, i) - h) / e, -(bandH(ry, p + vec2(0.0, e), t, i) - h) / e, 1.0));
  // where the cloth rolls over the edge it is seen edge-on: dense, and lit or shaded by which way it faces
  vec2 outward = -normalize(vec2(rx.x - r.x, ry.x - r.x) + 1e-6);
  float phi = asin(1.0 - clamp(r.x / min(0.085, r.w), 0.0, 1.0));
  vec3 n = normalize(vec3(outward * sin(phi) + nf.xy, cos(phi) * nf.z));
  float a = (1.0 - exp(-0.32 / max(n.z, 0.1))) * smoothstep(-0.0015, 0.0015, r.x);
  vec3 c = cloth(n) + (noise(vec2(r.y * 9.0, r.z * 150.0)) - 0.5) * 0.07;       // weave lines running with the band
  return mix(col, c, a);
}

// A drop of ink on paper: a pale granulating wash with a darker edge where the pigment dries.
// (Rewritten from the prototype without a reversed smoothstep or a pow of a negative number, which
// GLSL ES 1.00 leaves undefined.)
float wash(vec4 P, vec4 Q){
  if (Q.y < 0.002) return 0.0;
  vec2 d = (gl_FragCoord.xy - P.xy) / (P.zw * (0.4 + 0.6 * Q.x) * (1.0 + 0.02 * Q.z));
  float r = length(d) + (fbm(d * 1.4 + Q.w + Q.z * 0.03) - 0.5) * 0.5
          + (noise(gl_FragCoord.xy * 0.2 + Q.w) - 0.5) * 0.07;                    // ragged, fibrous edge
  float body = (1.0 - smoothstep(0.88, 1.0, r)) * (0.7 + 0.6 * noise(gl_FragCoord.xy * 0.4 + Q.w * 9.0));
  float x = (r - 0.93) / 0.055;
  float rim = exp(-x * x);
  return (body * 0.10 + rim * 0.16) * Q.y;
}

// Signed distance from this pixel to a box (negative inside).
float boxDist(vec4 R){ vec2 q = abs(gl_FragCoord.xy - R.xy) - R.zw; return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0); }
// A content block's clearing has the prototype's soft edge, sized by the window; a label's is narrower.
float contentClear(vec4 R, float w){ return (1.0 - smoothstep(-0.03 * u_res.y, 0.24 * u_res.y, boxDist(R))) * w * ${CONTENT.toFixed(3)}; }
float labelClear(vec4 R, float w){ return (1.0 - smoothstep(0.0, ${LABEL_EDGE.toFixed(1)} * u_scale, boxDist(R))) * w * ${LABEL.toFixed(3)}; }

void main(){
  appearance(u_theme);
  vec2 uv = gl_FragCoord.xy / u_res;
  float aspect = u_res.x / u_res.y, t = u_time;
  // The composition was made for a 16:10 frame centred on (0.8, 0.5); every window keeps that centre.
  vec2 p = (vec2(uv.x * aspect, uv.y) - vec2(aspect * 0.5, 0.5)) * ${ZOOM.toFixed(2)} + vec2(0.8, 0.5);

  // base sheet over the paper
  float e = 0.004, h = baseH(p, t);
  vec3 n = normalize(vec3(-(baseH(p + vec2(e, 0.0), t) - h) / e, -(baseH(p + vec2(0.0, e), t) - h) / e, 1.0));
  vec3 col = PAPER * 0.965;
  vec2 bd = vec2(0.83, 0.56);
  vec3 c = cloth(n) + (noise(vec2(dot(p, vec2(-bd.y, bd.x)) * 9.0, dot(p, bd) * 150.0)) - 0.5) * 0.06;
  col = mix(col, c, 1.0 - exp(-0.5 / max(n.z, 0.1)));

  // three bands turned over on top, each casting a soft shadow on what lies beneath
  col = layBand(col, p, t, 0.0);
  col = layBand(col, p, t, 1.0);
  col = layBand(col, p, t, 2.0);

  // Clearings behind the words and the labels: the cloth keeps moving around them, and faintly within.
  float k = contentClear(u_clear[0], u_clearW[0].x);
  k = max(k, labelClear(u_clear[1], u_clearW[0].y));
  k = max(k, labelClear(u_clear[2], u_clearW[0].z));
  k = max(k, labelClear(u_clear[3], u_clearW[0].w));
  k = max(k, labelClear(u_clear[4], u_clearW[1].x));
  k = max(k, labelClear(u_clear[5], u_clearW[1].y));
  col = mix(col, PAPER * 1.012 + (col - PAPER) * 0.2, k);
  col = mix(PAPER, col, u_cloth);

  for (int i = 0; i < ${MAX_INKS}; i++) col = mix(col, WASH, clamp(wash(u_inkP[i], u_inkQ[i]), 0.0, 0.6));

  col += (hash(gl_FragCoord.xy + fract(t * 7.13) * vec2(97.3, 41.9)) - 0.5) * 0.05;   // fine film grain
  gl_FragColor = vec4(col, 1.0);
}`;

// Starts the cloth on canvas (with its WebGL context gl). off(reason) is shell.js's way to turn Motion
// off for this visit; ended() tells it the cloth has gone, so turning Motion on again starts afresh.
export function start({ canvas, gl, off, ended }) {
  const root = document.documentElement;
  const program = build(gl);
  const u = (name) => gl.getUniformLocation(program, name);
  const at = { res: u("u_res"), time: u("u_time"), theme: u("u_theme"), cloth: u("u_cloth"), scale: u("u_scale"),
    clear: u("u_clear"), clearW: u("u_clearW"), inkP: u("u_inkP"), inkQ: u("u_inkQ") };
  const clearR = new Float32Array(24), clearW = new Float32Array(8);
  const inkP = new Float32Array(4 * MAX_INKS), inkQ = new Float32Array(4 * MAX_INKS);
  const ladder = PHONE.matches ? SCALES.phone : SCALES.desktop;
  let step = 0, scale = ladder[0];
  let now = performance.now(), last = null, raf = 0, t = 8, frames = 0, sample = [], stopped = false;
  let cloth = 0, clothTo = 1, theme = themeWanted(), pace = paceWanted(), leaving = false;
  const weights = new Map();   // element -> eased clearing weight, kept while an element eases out
  const rects = new Map();     // element -> its last rectangle, so a vanished one can ease out in place
  let inks = [], held = null;

  canvas.className = "veil";
  canvas.setAttribute("aria-hidden", "true");
  document.body.prepend(canvas);
  size();

  function themeWanted() {
    const chosen = root.getAttribute("data-theme");
    return chosen === "dark" || (chosen !== "light" && DARK.matches) ? 1 : 0;
  }
  function paceWanted() {
    if (!document.querySelector("main [data-threshold]")) return PACE.chapter;
    return document.querySelector("main [data-install]:not([hidden])") ? PACE.entered : PACE.threshold;
  }
  function size() {
    canvas.width = Math.max(1, Math.round(window.innerWidth * scale));
    canvas.height = Math.max(1, Math.round(window.innerHeight * scale));
  }
  function shown(element) {
    if (element.closest("[hidden]") || !element.getClientRects().length) return false;
    return element.checkVisibility ? element.checkVisibility({ opacityProperty: true, visibilityProperty: true }) : true;
  }
  // A rectangle in canvas pixels, y counted up from the bottom, as gl_FragCoord does.
  function box(element, pad) {
    const b = element.getBoundingClientRect();
    return [(b.left + b.width / 2) * scale, (window.innerHeight - (b.top + b.height / 2)) * scale,
      (b.width / 2 + pad) * scale, (b.height / 2 + pad) * scale];
  }

  // Clearings follow [data-clearing] elements: one content block, up to five labels.
  function clearings(dt) {
    const live = new Set();
    let content = null;
    const labels = [];
    for (const element of document.querySelectorAll("[data-clearing]")) {
      if (!shown(element)) continue;
      live.add(element);
      if (element.dataset.clearing === "content") { if (!content) content = element; }
      else if (labels.length < 5) labels.push(element);
    }
    for (const element of live) rects.set(element, box(element, element.dataset.clearing === "content" ? 0 : LABEL_PAD));
    const ease = Math.min(1, dt * 3);   // clearings ease in over about a second (95% after 1 s, spec 3.1)
    // An in-place change swaps the content block's element for the next chapter's: the new block takes over
    // the old one's weight and place, so the cloth behind the text neither jumps back nor starts calming
    // again from nothing (spec 4.5: the cloth continues uninterrupted).
    if (content && !weights.has(content)) {
      const gone = [...weights.keys()].find((element) => !live.has(element) && !element.isConnected && element.dataset.clearing === "content");
      if (gone) { weights.set(content, weights.get(gone)); weights.delete(gone); rects.delete(gone); }
    }
    for (const element of new Set([...weights.keys(), ...live])) {
      const w = weights.get(element) || 0, to = live.has(element) ? 1 : 0, next = w + (to - w) * ease;
      if (!live.has(element) && next < 0.01) { weights.delete(element); rects.delete(element); }   // a detached element eases out in place
      else weights.set(element, next);
    }
    clearR.fill(0); clearW.fill(0);
    const fading = [...weights.keys()].filter((element) => !live.has(element));
    const slots = [content || fading.find((element) => element.dataset.clearing === "content"),
      ...labels, ...fading.filter((element) => element.dataset.clearing !== "content")].slice(0, 6);
    slots.forEach((element, i) => {
      if (!element) return;
      clearR.set(rects.get(element), i * 4);
      clearW[i] = weights.get(element) || 0;
    });
  }

  // Ink: a wash grows under what the pointer or the keyboard is on, from 64rem up (below that the
  // corners sit on the top veil and the menu is an opaque sheet, so the flat fill stays).
  function inkable(element) {
    return element && WIDE.matches && !element.closest(".catalog__frame");
  }
  function addInk(element, keep = false) {
    if (inks.some((ink) => ink.element === element && !ink.dead)) return;
    inks.push({ element, born: now, dead: null, keep, seed: Math.random() * 40 });
    while (inks.length > MAX_INKS) {
      const i = inks.findIndex((ink) => !ink.keep);
      const [gone] = inks.splice(i < 0 ? 0 : i, 1);
      gone.element.classList.remove("is-inked");
    }
    element.classList.add("is-inked");
    kick();
  }
  function endInk(element) {
    for (const ink of inks) if (ink.element === element && !ink.dead) { ink.dead = now; element.classList.remove("is-inked"); }
  }
  function inkUniforms() {
    inks = inks.filter((ink) => (!ink.dead || now - ink.dead < 1800) && ink.element.isConnected);
    inkP.fill(0); inkQ.fill(0);
    inks.forEach((ink, i) => {
      const age = (now - ink.born) / 1000, b = ink.element.getBoundingClientRect();
      const grow = 1 - Math.pow(1 - Math.min(1, age / 1.4), 3);   // 1.4 s, cubic ease-out (spec 3.4)
      const fade = ink.dead === null ? 1 : Math.max(0, 1 - (now - ink.dead) / 1800);   // 1.8 s
      inkP.set([(b.left + b.width / 2) * scale, (window.innerHeight - (b.top + b.height / 2)) * scale,
        (b.width * 0.48 + 6) * scale, (b.height * 0.8 + 4) * scale], i * 4);
      inkQ.set([grow, fade, age, ink.seed], i * 4);
    });
  }
  // The current chapter holds its wash while the menu is open (spec 3.4).
  function holdCurrent() {
    const current = document.querySelector("#shell-menu.is-open a[aria-current='page']");
    const want = inkable(current) && shown(current) ? current : null;
    if (want === held) return;
    if (held) { endInk(held); inks = inks.filter((ink) => ink.element !== held || ink.dead); }
    held = want;
    if (held) addInk(held, true);
  }

  const target = (event) => event.target instanceof Element ? event.target.closest(INK_TARGETS) : null;
  const listeners = {
    pointerover: (event) => { const element = target(event); if (inkable(element) && element !== held) addInk(element); },
    pointerout: (event) => {
      const element = target(event);
      if (element && element !== held && !(event.relatedTarget instanceof Node && element.contains(event.relatedTarget))) endInk(element);
    },
    focusin: (event) => { const element = target(event); if (inkable(element) && element !== held && element.matches(":focus-visible")) addInk(element); },
    focusout: (event) => { const element = target(event); if (element && element !== held) endInk(element); },
    click: (event) => { const element = target(event); if (element && element !== held) endInk(element); },
  };
  for (const [name, listener] of Object.entries(listeners)) document.addEventListener(name, listener, true);

  // Motion, appearance and the system's colour scheme, as they change.
  const watch = new MutationObserver(() => {
    if (root.getAttribute("data-motion") !== "on") leave(); else if (leaving) { leaving = false; clothTo = 1; }
    kick();
  });
  watch.observe(root, { attributes: true, attributeFilter: ["data-motion", "data-theme"] });
  const onScheme = () => kick();
  DARK.addEventListener("change", onScheme);
  const onResize = () => { size(); kick(); };
  window.addEventListener("resize", onResize);
  const onVisible = () => { if (!document.hidden) { last = null; sample = []; frames = 0; kick(); } };
  document.addEventListener("visibilitychange", onVisible);
  const onLost = (event) => { event.preventDefault(); stop("lost"); };
  canvas.addEventListener("webglcontextlost", onLost);

  // Motion went off: the cloth fades out over about a second, then everything stops (spec 3.3).
  function leave() {
    if (leaving) return;
    leaving = true; clothTo = 0;
    for (const ink of inks) ink.element.classList.remove("is-inked");
    inks = []; held = null;
  }
  function stop(reason) {
    if (stopped) return;
    stopped = true;
    cancelAnimationFrame(raf);
    for (const [name, listener] of Object.entries(listeners)) document.removeEventListener(name, listener, true);
    watch.disconnect();
    DARK.removeEventListener("change", onScheme);
    window.removeEventListener("resize", onResize);
    document.removeEventListener("visibilitychange", onVisible);
    canvas.removeEventListener("webglcontextlost", onLost);
    for (const ink of inks) ink.element.classList.remove("is-inked");
    delete root.dataset.veil;
    canvas.remove();
    const lose = gl.getExtension("WEBGL_lose_context");
    if (lose && reason !== "lost") lose.loseContext();
    if (reason) off(reason);
    ended();
  }

  // Adaptive rendering (spec 8): lower the resolution while frames run long; at the floor, the still page.
  function measure(ms) {
    if (document.hidden) return;
    frames += 1;
    if (frames <= WARMUP) return;
    sample.push(ms);
    if (sample.length < WINDOW) return;
    const median = sample.sort((a, b) => a - b)[WINDOW >> 1];
    sample = [];
    if (median <= SLOW_MS) return;
    if (step < ladder.length - 1) { step += 1; scale = ladder[step]; frames = 0; size(); }
    else if (median > STILL_MS) stop("slow");
  }

  function frame(time) {
    raf = 0;
    if (stopped || document.hidden) return;
    try {
      draw(time);
    } catch (error) {
      stop("error");   // a cloth that cannot draw gives way to the still page, not a broken one
    }
  }
  function draw(time) {
    const ms = last === null ? 0 : time - last;
    const dt = Math.min(0.1, ms / 1000);   // a long gap (a hidden tab) neither jumps the cloth nor reads as slow
    if (last !== null) measure(ms);
    last = time; now = time;
    if (stopped) return;
    cloth += (clothTo - cloth) * Math.min(1, dt * 1.8);              // about a second (spec 3.3)
    theme += (themeWanted() - theme) * Math.min(1, dt * 2.2);        // about 1.5 s (spec 3.2)
    pace += (paceWanted() - pace) * Math.min(1, dt * 0.8);
    t += dt * pace;
    clearings(dt);
    holdCurrent();
    inkUniforms();
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.uniform2f(at.res, canvas.width, canvas.height);
    gl.uniform1f(at.time, t);
    gl.uniform1f(at.theme, theme);
    gl.uniform1f(at.cloth, cloth);
    gl.uniform1f(at.scale, scale);
    gl.uniform4fv(at.clear, clearR);
    gl.uniform4fv(at.clearW, clearW);
    gl.uniform4fv(at.inkP, inkP);
    gl.uniform4fv(at.inkQ, inkQ);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    if (!root.dataset.veil) root.dataset.veil = "on";
    if (leaving && cloth < 0.01) { stop(null); return; }
    kick();
  }
  function kick() { if (!raf && !stopped) raf = requestAnimationFrame(frame); }

  // The first frames fade the cloth in from plain paper, so it never pops over the still page.
  kick();
}

// Compiles and links the shader; a failure throws, and shell.js turns Motion off for the visit.
function build(gl) {
  if (gl.getParameter(gl.MAX_FRAGMENT_UNIFORM_VECTORS) < 32) throw new Error("veil.js: too few fragment uniforms");
  const shader = (type, source) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, source);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(`veil.js: ${gl.getShaderInfoLog(s)}`);
    return s;
  };
  const program = gl.createProgram();
  gl.attachShader(program, shader(gl.VERTEX_SHADER, VERT));
  gl.attachShader(program, shader(gl.FRAGMENT_SHADER, FRAG));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(`veil.js: ${gl.getProgramInfoLog(program)}`);
  gl.useProgram(program);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const a = gl.getAttribLocation(program, "a");
  gl.enableVertexAttribArray(a);
  gl.vertexAttribPointer(a, 2, gl.FLOAT, false, 0, 0);
  return program;
}
```

Run: `gzip -9 -c backend/static/js/veil.js | wc -c`. Expected: about 7,900 (the budget is 15,360).

- [ ] **Step 3: Load it from the shell**

In `backend/templates/shell.html`, replace

```html
<body class="{% block body_class %}{% endblock %}"{% if shell_page.layout %}
```

with

```html
<body class="{% block body_class %}{% endblock %}" data-veil-src="{% static 'js/veil.js' %}"{% if shell_page.layout %}
```

(the rest of that line stays as it is).

In `backend/static/js/shell.js`, change the first line to:

```js
// The shell's behavior: the corner menu, the display preferences, in-place chapter changes and the cloth's loader (spec 4.4, 4.5, 6.3, 6.4).
```

Replace the whole `setUpMotion` function with:

```js
function setUpMotion(button) {
  // The name stays "Motion"; aria-pressed carries the state and the visible word is hidden from the name.
  const state = button.querySelector("[data-motion-state]");
  const show = () => {
    const on = root.getAttribute("data-motion") !== "off";
    button.setAttribute("aria-pressed", String(on));
    if (state) state.textContent = on ? "· on" : "· off";
  };
  button.addEventListener("click", () => {
    const next = root.getAttribute("data-motion") === "off" ? "on" : "off";
    if (next === "on") delete root.dataset.veilOff; // asked for again: the cloth may try once more
    root.setAttribute("data-motion", next);
    save(MOTION_KEY, next);
  });
  // Follow data-motion however it changes, so an automatic Off (the cloth's loader) shows here too.
  new MutationObserver(show).observe(root, { attributes: true, attributeFilter: ["data-motion"] });
  show();
}

// The cloth (spec 3.3, 6.3, 8): veil.js is fetched only when Motion is on and WebGL works, once the
// page has loaded, and again whenever Motion comes back on. If it cannot run, Motion goes off for this
// visit only: data-motion changes and data-veil-off says why; storage keeps the visitor's choice.
let veil = null; // set while a cloth is starting or running
function veilOff(reason) {
  root.dataset.veilOff = reason;
  root.setAttribute("data-motion", "off");
}
async function startVeil() {
  const src = document.body.dataset.veilSrc;
  if (veil || !src || root.getAttribute("data-motion") !== "on" || root.dataset.veilOff) return;
  const canvas = document.createElement("canvas");
  const gl = canvas.getContext("webgl", { alpha: false, antialias: false, depth: false, failIfMajorPerformanceCaveat: true, powerPreference: "low-power" });
  if (!gl) {
    veilOff("no-webgl");
    return;
  }
  veil = true;
  try {
    (await import(src)).start({ canvas, gl, off: veilOff, ended: () => { veil = null; } });
  } catch (error) {
    veil = null;
    veilOff("error");
  }
}
function setUpVeil() {
  if (document.readyState === "complete") startVeil();
  else window.addEventListener("load", startVeil, { once: true });
  new MutationObserver(startVeil).observe(root, { attributes: true, attributeFilter: ["data-motion"] });
}
```

In the final `try` block, after the line `  setUpChapterChanges();`, add:

```js
  setUpVeil();
```

Leave the line `  document.querySelectorAll("[data-menu]").forEach(setUpMenu);` exactly as it is (the site checks' "shell.js throws" run rewrites that text).

In `backend/static/css/tokens.css`, after the line that defines `--fade`, add:

```css
  --scene-fade: 1.4s; /* a switch of appearance: the page's ground and text fade with the cloth (spec 3.2) */
```

In `backend/static/css/base.css`, before the comment `/* Chapters (spec 4.3) and the threshold (4.2): one block, centred both ways in the space the page`, add:

```css
/* The cloth (veil.js, spec 3.1): one canvas, fixed behind everything; veil.js adds and removes it. */
.veil {
  position: fixed;
  inset: 0;
  width: 100%;
  height: 100%;
  z-index: -1;
  pointer-events: none;
}

/* A switch of appearance fades the whole scene (spec 3.2): the ground and the text here, the cloth in
   veil.js. With Motion off it is instant, like every other change. */
html {
  transition: background-color var(--scene-fade) ease, color var(--scene-fade) ease;
}

html[data-motion="off"] {
  transition: none;
}

@media (prefers-reduced-motion: reduce) {
  html:not([data-motion]) {
    transition: none;
  }
}
```

- [ ] **Step 4: Run the Django tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_veil pages.tests_static pages.tests_shell pages.tests_site_rules -v 1`
Expected: OK. (`tests_static` still holds `shell.js` and the behaviours under 10 KB: about 8.1 KB now. `tests_site_rules` finds the new `{% static 'js/veil.js' %}` path.)

- [ ] **Step 5: Write the cloth's browser checks**

In `site-tests/run_site_checks.py`:

In the imports at the top of `site-tests/run_site_checks.py`, add `import time` between `import sys` and `import zlib` (`cloth_problems` times its families with it).

Then replace the whole `open_page` function with:

```python
# WebGL's own complaints (an invalid call, a leaked context) arrive as console warnings, not errors.
WEBGL_WARNING = re.compile(r"WebGL|Too many active WebGL contexts|CONTEXT_LOST|Automatic fallback to software WebGL")


def open_page(context, url, warnings=False):
    """Open url; stop the whole run with a plain reason if the page itself cannot be had. With
    warnings, WebGL's console warnings are kept among the errors (all but the one about read-back,
    which the cloth checks cause themselves)."""
    page = context.new_page()
    errors = []

    def heard(message):
        if message.type == "error" or (warnings and message.type == "warning" and WEBGL_WARNING.search(message.text)
                                       and "GPU stall due to ReadPixels" not in message.text):
            errors.append(message.text)

    page.on("console", heard)
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        response = page.goto(url, wait_until="networkidle")
    except PlaywrightError as error:
        raise SystemExit(f"Could not open {url}: {error.message.splitlines()[0]}. Is the app running at --base-url?")
    status = response.status if response else "nothing"
    if status != 200:
        hint = " Is the session file current? Run make_staff_session.py again (AGENTS.md, Site checks)." if status in (302, 403, 404) else ""
        raise SystemExit(f"{url} answered {status}; every check needs the page itself.{hint}")
    return page, errors
```

After the `webgl_precondition` function (and before `grain_problems`), add:

```python
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
```

In `edge_case_problems`, replace `page.screenshot(path=str(out / f"{site_page.name}-{width}-no-javascript.png"), full_page=True)` with `full_page_screenshot(page, out / f"{site_page.name}-{width}-no-javascript.png")`; in `run()`, replace `page.screenshot(path=str(out / f"{site_page.name}-{width}-{appearance}-motion-{motion}.png"), full_page=True)` with `full_page_screenshot(page, out / f"{site_page.name}-{width}-{appearance}-motion-{motion}.png")`.

Then, after the `grain_problems` function, add:

```python
# The cloth checks load the real veil.js and drive it with a virtual clock: every display frame is
# 1/60 s of cloth time (or the step a check sets), however long the machine takes to draw it, and time
# can be held still. They read the canvas back after a draw, so they measure exactly what the shader
# painted, which axe cannot see. Nothing they assert depends on the machine's speed. Only the frames a
# check reads are drawn, so the checks cost little CPU even on a software GPU.
CLOTH_PROBE = """(() => {
  const cloth = window.__cloth = { draws: 0, frames: 0, contexts: 0, gl: null, last: null, want: false, wait: null, drawAll: false };
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
  // The rasteriser is what costs time on a software GPU, and the cloth's state (time, clearings, inks, the
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
  cloth.capture = () => new Promise((done) => { cloth.wait = done; cloth.want = true; });
  const linear = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  // A viewport rectangle (CSS px) of the last captured frame: relative luminance (for contrast) and
  // gamma luma (for what a person sees change).
  cloth.stats = ({ x, y, width, height }) => {
    const { w, h, px, cssW, cssH } = cloth.last, sx = w / cssW, sy = h / cssH;
    const x0 = Math.max(0, Math.floor(x * sx)), x1 = Math.min(w, Math.ceil((x + width) * sx));
    const y0 = Math.max(0, Math.floor(h - (y + height) * sy)), y1 = Math.min(h, Math.ceil(h - y * sy));
    let n = 0, sum = 0, sum2 = 0, lo = 1, hi = 0, luma = 0;
    for (let j = y0; j < y1; j++) for (let i = x0; i < x1; i++) {
      const k = (j * w + i) * 4, v = 0.2126 * linear(px[k]) + 0.7152 * linear(px[k + 1]) + 0.0722 * linear(px[k + 2]);
      n += 1; sum += v; sum2 += v * v; lo = Math.min(lo, v); hi = Math.max(hi, v);
      luma += (0.2126 * px[k] + 0.7152 * px[k + 1] + 0.0722 * px[k + 2]) / 255;
    }
    const mean = sum / n;
    return { n, mean, std: Math.sqrt(Math.max(0, sum2 / n - mean * mean)), min: lo, max: hi, luma: luma / n };
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
           z: style.zIndex, first: document.body.firstElementChild === canvas, scale: canvas.width / innerWidth };
}"""


def page_named(path):
    return next(site_page for site_page in PAGES if site_page.path == path)


def cloth_context(browser, base_url, session_key, site_page, *, width=1024, height=640, theme="light", motion="on",
                  reduced_motion="no-preference", scripts=()):
    """A context that runs the real veil.js under CLOTH_PROBE, and the list of its requests for veil.js.
    1024x640 is the smallest wide layout: the cheapest canvas that has the corners and the open menu."""
    context = new_context(browser, base_url, session_key, site_page, width=width, height=height, theme=theme,
                          motion=motion, reduced_motion=reduced_motion, cloth=True)
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


def capture(page):
    """Read back the next frame the cloth draws."""
    page.evaluate("() => window.__cloth.capture()")


def canvas_stats(page, rect):
    return page.evaluate("(rect) => window.__cloth.stats(rect)", rect)


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
    in <body>, behind everything, out of the pointer's way and the accessibility tree (spec 9), at 0.8
    of the CSS resolution, or 0.5 on a phone (spec 3.1). With Motion off, or reduced motion and nothing
    saved, veil.js is never fetched and nothing is drawn."""
    problems = []
    about = page_named("/about/")
    cases = (
        ("Motion saved on", about, 1024, 640, "on", "no-preference", True),
        ("nothing saved", about, 1024, 640, None, "no-preference", True),
        ("Motion saved off", about, 1024, 640, "off", "no-preference", False),
        ("reduced motion, nothing saved", about, 1024, 640, None, "reduce", False),
        ("reduced motion, Motion saved on", about, 1024, 640, "on", "reduce", True),
        ("a phone", about, 375, 667, "on", "no-preference", True),
        ("the threshold", page_named("/"), 1024, 640, "on", "no-preference", True),
        ("the component catalog", page_named("/app/components/"), 1024, 640, "on", "no-preference", True),
    )
    for name, site_page, width, height, motion, reduced, runs in cases:
        label = f"{site_page.path} cloth, {name}"
        context, requests = cloth_context(browser, base_url, session_key, site_page, width=width, height=height,
                                          motion=motion, reduced_motion=reduced)
        page, errors = open_page(context, base_url + site_page.path, warnings=True)
        if runs:
            if not cloth_running(page):
                problems.append(f"{label}: the cloth never started ({page.evaluate(VEIL_STATE)})")
            else:
                shape, state = page.evaluate(CANVAS_SHAPE), page.evaluate(VEIL_STATE)
                scale = 0.5 if width <= 640 else 0.8
                if state["canvases"] != 1 or not shape or (shape["hidden"], shape["position"], shape["pointer"], shape["z"], shape["first"]) != (
                        "true", "fixed", "none", "-1", True):
                    problems.append(f"{label}: expected one canvas first in <body>, aria-hidden, fixed, no pointer events, "
                                    f"z-index -1; found {shape} with {state['canvases']} canvas(es)")
                elif abs(shape["scale"] - scale) > 0.02:
                    problems.append(f"{label}: the cloth renders at {shape['scale']:.2f} of the CSS width; it should be {scale}")
                if len(requests) != 1:
                    problems.append(f"{label}: veil.js was fetched {len(requests)} times")
        else:
            page.wait_for_timeout(1500)
            state = page.evaluate(VEIL_STATE)
            if state["canvases"] or state["veil"] or state["draws"] or requests:
                problems.append(f"{label}: nothing should run and veil.js should never be fetched ({state}; requests {requests})")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()
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
        # The paper under the cloth has a spread of its own, about 0.4 of the settled cloth's, which is what "no
        # cloth yet" reads as. 0.3 s in, a fade of about a second stands at 0.6 (the clearings ease in at the same
        # time); a fade twice as fast reads 0.8, twice as slow 0.48, and a cloth that appears at once 1.1.
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
    follows the system as it changes; and the page's own ground and text fade with it unless Motion is
    off (--scene-fade)."""
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
    fade = page.evaluate("() => getComputedStyle(document.documentElement).transitionDuration")
    if "1.4s" not in fade:
        problems.append(f"{label}: the page's ground and text should fade over --scene-fade (1.4s); transition-duration is {fade}")
    problems += [f"{label}: console: {error}" for error in errors]
    context.close()
    return problems


def cloth_change_problems(browser, base_url, session_key):
    """An in-place chapter change and Come in keep the same canvas and the same WebGL context: the cloth
    continues uninterrupted (spec 4.5)."""
    problems = []
    for start, act, done, label in (
            ("/about/", lambda page: page.locator('#shell-menu a[href="/plans/"]').click(),
             "() => location.pathname === '/plans/' && !document.querySelector('main').classList.contains('is-leaving')",
             "/about/ cloth, to Plans in place"),
            ("/", lambda page: page.locator("[data-come-in]").click(),
             "() => { const box = document.querySelector('[data-install]'); return Boolean(box) && !box.hidden; }",
             "/ cloth, Come in")):
        site_page = page_named(start)
        context, _ = cloth_context(browser, base_url, session_key, site_page)
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
        after = page.evaluate(VEIL_STATE)
        kept = page.evaluate("() => { const canvas = document.querySelector('canvas.veil'); return canvas && canvas.dataset.mark; }")
        if kept != "kept" or after["contexts"] != before["contexts"] or after["veil"] != "on" or after["canvases"] != 1:
            problems.append(f"{label}: the same canvas and context should carry on ({before} then {after}, mark {kept})")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()
    return problems


# Every family of cloth checks, in the order they run; new ones go at the end.
CLOTH_FAMILIES = [cloth_presence_problems, cloth_failure_problems, cloth_lifecycle_problems, cloth_input_problems,
                  cloth_appearance_problems, cloth_change_problems]


def cloth_problems(browser, base_url, session_key):
    """Every cloth family, each timed, so a slow run shows where its time went. If WebGL is gone, or the
    cloth does not start in a family, the others would only wait out the same 30 s timeouts: say so once."""
    try:
        webgl_precondition(browser, after="the page checks (failures above that mention Motion probably follow from it)")
    except SystemExit as stop:
        return [str(stop)]
    problems = []
    for family in CLOTH_FAMILIES:
        started = time.perf_counter()
        found = family(browser, base_url, session_key)
        problems += found
        print(f"cloth: {family.__name__} took {time.perf_counter() - started:.0f} s")
        if any("the cloth never started" in problem for problem in found):
            problems.append("cloth: the cloth did not start in the family above; the remaining families were not run")
            break
    return problems
```

In `run()`, replace

```python
            problems += edge_case_problems(browser, base_url, session_key, site_page, out)
        browser.close()
```

with

```python
            problems += edge_case_problems(browser, base_url, session_key, site_page, out)
        problems += cloth_problems(browser, base_url, session_key)
        browser.close()
```

Run, with the server restarted (templates changed), only the cloth checks first, from the repo root:

```bash
site-tests/.venv/bin/python -c "
import sys; sys.path.insert(0, 'site-tests')
import run_site_checks as checks
from playwright.sync_api import sync_playwright
session = open('site-tests/session.txt').read().strip()
with sync_playwright() as p:
    browser = p.chromium.launch(args=checks.GL_ARGS)
    problems = checks.cloth_problems(browser, 'http://127.0.0.1:8777', session)
    print('\n'.join(problems)); print(len(problems), 'problem(s)')
"
```

Expected: `0 problem(s)`, with each family's time printed. Then check that the checks can fail: temporarily change `veil.js`'s `start` to `export function start() {}` (the existing checks' stub) and run `checks.cloth_lifecycle_problems` and `checks.cloth_change_problems` with the same one-off command (the whole list takes about 6 minutes broken, because every cloth that never starts waits 30 s). Expect both to report that the cloth never started (the full list also reports the presence, input and appearance families, and one problem in the failure family for the shader that never compiles). Undo the change. Then prove the fades are measured: in `veil.js` change `Math.min(1, dt * 1.8)` in the line `cloth += (clothTo - cloth) * Math.min(1, dt * 1.8);` to `1` and run `checks.cloth_lifecycle_problems` alone: expect "the cloth should fade in over about a second when it starts, not appear at once" (the dry run read 1.09 of the settled spread against the 0.75 bound) and "the cloth was gone 0.3 s after the click" (the "ten times" line may fail too, as a side effect). Undo that change too: `git status --short` must show `veil.js` only as untracked, and `gzip -9 -c backend/static/js/veil.js | wc -c` must print what it printed in Step 2.

- [ ] **Step 6: Run everything**

Run the full suite. Expected: OK.
Run the full browser checks. Expected: `0 problem(s) across 7 page(s)`, the cloth families' times printed. Look at the cloth once as a person would: open `http://127.0.0.1:8777/about/` in a browser on the machine (or take a Playwright screenshot at 1280x800 after 3 s from a plain context launched with `GL_ARGS`, not a `cloth_context`, whose probe draws only the frames a check reads; on SwiftShader the adaptive renderer will already have stepped down to 0.6, which is expected), and look at `cloth-1024-paper.png` and `cloth-1024-ink.png` in `site-tests/artifacts`; confirm the cloth is there, behind the words and the corners, moving slowly.

- [ ] **Step 7: Commit**

```bash
git add backend/static/js/veil.js backend/static/js/shell.js backend/templates/shell.html backend/static/css/base.css \
  backend/static/css/tokens.css backend/pages/tests_veil.py site-tests/run_site_checks.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
The cloth: veil.js, its loader, and when it runs

veil.js draws the prototype's voile on one fixed canvas behind the page,
fading in from plain paper. shell.js imports it once the page has loaded,
only when Motion is on and WebGL works, and again when Motion comes back on;
if it cannot run, Motion goes off for the visit and data-veil-off says why.
The page's ground and text now fade with the cloth on a switch of
appearance. The browser checks load the real veil.js under a virtual clock.
EOF
```

---

### Task 4: Clearings behind the words and the labels

**Files:**
- Modify: `backend/templates/shell.html` (the four corners), `backend/templates/layouts/chapter.html`, `backend/templates/chapters/threshold.html`
- Modify (tests): `backend/pages/tests_shell.py`, `backend/pages/tests_chapters.py`, `site-tests/run_site_checks.py`

**Interfaces:**
- Consumes: `veil.js`'s clearing rule (an element with `data-clearing="content"` or `"label"` gets a clearing while it is shown; Task 3); `cloth_context`, `run_frames`, `capture`, `canvas_stats`, `FREEZE`, `CLOTH_FAMILIES` (Task 3); `CORNERS` (PR 1).
- Produces:
  - `data-clearing="label"` on the four `.shell-corner` elements; `data-clearing="content"` on a chapter's `.chapter` (through `{% block chapter_clearing %}` in the layout) and on the threshold's `[data-install]`; none on the threshold's `.chapter`.
  - Site checks: `NO_CLEARINGS`, `TOKEN`, `CLEARING_RECT`, `relative_luminance()`, `worst_contrast()`, `clearing_contrast_problems()`, `clearing_problems()` (added to `CLOTH_FAMILIES`).

- [ ] **Step 1: Write the failing tests**

In `backend/pages/tests_shell.py`, in the class that holds `test_bottom_corners`, add after that method:

```python
    def test_each_corner_asks_for_a_label_clearing(self):
        # veil.js clears the cloth behind every [data-clearing] element while it is shown (spec 3.1); the
        # top-right corner's box takes in the open menu column, so its clearing covers the menu too.
        for corner in ("top-left", "top-right", "bottom-left", "bottom-right"):
            with self.subTest(corner=corner):
                self.assertIn(f'<div class="shell-corner shell-corner--{corner}" data-clearing="label">', self.html)
```

In `backend/pages/tests_chapters.py`, in `test_every_chapter_renders_on_the_shell_in_the_chapter_layout`, replace the line

```python
                self.assertIn('<div class="chapter">', html)
```

with

```python
                # A chapter's block has a clearing behind it (spec 3.1); the threshold's line sits on the bare
                # cloth until Come in opens one behind the install line (spec 4.2).
                block = '<div class="chapter">' if page.url_name == "landing" else '<div class="chapter" data-clearing="content">'
                self.assertIn(block, html)
```

and in `test_the_install_line_waits_behind_come_in`, replace `'<div class="threshold__install" data-install hidden>'` with `'<div class="threshold__install" data-install data-clearing="content" hidden>'`.

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_shell pages.tests_chapters -v 2`
Expected: FAIL for `test_each_corner_asks_for_a_label_clearing` (four subtests), for the chapters (every chapter but the threshold), and for `test_the_install_line_waits_behind_come_in`.

- [ ] **Step 2: Mark the clearings**

In `backend/templates/shell.html`, add ` data-clearing="label"` to each of the four corner `<div>`s, right after their `class` attribute:
- `<div class="shell-corner shell-corner--top-left">` becomes `<div class="shell-corner shell-corner--top-left" data-clearing="label">`, and the same for `--top-right`, `--bottom-left` and `--bottom-right`.

In `backend/templates/layouts/chapter.html`, replace

```html
{% block main %}<div class="chapter">{% block chapter %}{% endblock %}</div>{% endblock %}
```

with

```html
{% block main %}<div class="chapter"{% block chapter_clearing %} data-clearing="content"{% endblock %}>{% block chapter %}{% endblock %}</div>{% endblock %}
```

In `backend/templates/chapters/threshold.html`, after the line `{% block preload_reading %}{% endblock %}`, add:

```html
{# The line sits on the bare cloth; the clearing opens behind the install line, when Come in shows it (spec 4.2). #}
{% block chapter_clearing %}{% endblock %}
```

and replace `<div class="threshold__install" data-install hidden>` with `<div class="threshold__install" data-install data-clearing="content" hidden>`.

Run the command from Step 1. Expected: OK.

- [ ] **Step 3: Write the clearing checks**

In `site-tests/run_site_checks.py`, before the comment line just above `CLOTH_FAMILIES` (`# Every family of cloth checks, in the order they run; new ones go at the end.`), add:

```python
NO_CLEARINGS = """document.addEventListener("DOMContentLoaded", () => {
  for (const element of document.querySelectorAll("[data-clearing]")) element.removeAttribute("data-clearing"); });"""
TOKEN = "(name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()"
CLEARING_RECT = """(selector) => {
  const element = document.querySelector(selector);
  if (!element || !element.getClientRects().length) return null;
  const b = element.getBoundingClientRect(), x = Math.max(0, b.left), y = Math.max(0, b.top);
  const width = Math.min(innerWidth, b.right) - x, height = Math.min(innerHeight, b.bottom) - y;
  return width > 4 && height > 4 ? { x, y, width, height } : null;
}"""


WORD_RECTS = """(selector) => [...document.querySelectorAll(selector)].flatMap((element) => {
  const range = document.createRange(); range.selectNodeContents(element);
  const owner = element.closest("[data-clearing]");
  const words = range.getBoundingClientRect(), shown = owner ? owner.getBoundingClientRect() : { left: 0, top: 0, right: innerWidth, bottom: innerHeight };
  const x = Math.max(0, words.left, shown.left), y = Math.max(0, words.top, shown.top);
  const width = Math.min(innerWidth, words.right, shown.right) - x, height = Math.min(innerHeight, words.bottom, shown.bottom) - y;
  return width > 4 && height > 4 ? [{ x, y, width, height }] : [];
})"""


def relative_luminance(colour):
    """WCAG relative luminance of a #rrggbb colour."""
    channels = [int(colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def worst_contrast(text, stats):
    """The lowest contrast between text (a relative luminance) and any pixel the cloth drew under it."""
    return min((max(text, value) + 0.05) / (min(text, value) + 0.05) for value in (stats["min"], stats["max"]))


def clearing_contrast_problems(page, label, selectors, token="--muted", floor=4.5, words=False, required=()):
    """In the frame just captured, text in token's colour over each selector's box keeps floor:1 or better
    against every pixel the cloth drew there (spec 9). With words, every match is measured over its own
    words only, and only where its clearing's box shows it (the open menu scrolls inside its corner), so a
    neighbour's ink wash at a box's edge is not counted. A selector with nothing shown is skipped, unless it
    is in required: a check that measured nothing proves nothing, so that is a problem."""
    problems = []
    text = relative_luminance(page.evaluate(TOKEN, token))
    for selector in selectors:
        if words:
            rects = page.evaluate(WORD_RECTS, selector)
        else:
            rect = page.evaluate(CLEARING_RECT, selector)
            rects = [rect] if rect else []
        if not rects:
            if selector in required:
                problems.append(f"{label}: nothing of {selector} is shown, so its contrast could not be measured")
            continue
        ratio = min(worst_contrast(text, canvas_stats(page, rect)) for rect in rects)
        if ratio < floor:
            problems.append(f"{label}: {token} text over {selector} could fall to {ratio:.2f}:1 over the cloth; "
                            f"it must stay at {floor}:1 or better (spec 9)")
    return problems


def clearing_problems(browser, base_url, session_key):
    """Text over the moving cloth keeps its contrast (spec 3.1, 9). The clearings behind a chapter's
    block, the corner labels and the open menu hold muted text at 4.5:1 or better over every pixel the
    shader draws, in both appearances, at desktop and phone widths, at two moments, scrolled, after an
    in-place change and after Come in; the threshold's line, display text with no clearing, holds 3:1.
    And a clearing really calms the cloth: under it the cloth varies far less than in the same frame
    without clearings."""
    problems = []
    about, begin, threshold = page_named("/about/"), page_named("/begin/"), page_named("/")
    corners = [f".shell-corner--{corner}" for corner in CORNERS]
    # The open menu holds the current chapter's ink wash (spec 3.4), and the words on it are --ink, not --muted:
    # the top-right corner is measured by its words, MENU and the other links in --muted, the current chapter's in --ink.
    wide_labels = [corners[0], *corners[2:]]
    menu_muted, menu_current = "header [data-menu-toggle], #shell-menu a:not([aria-current])", "#shell-menu a[aria-current]"

    def opened(site_page, label, **options):
        context, _ = cloth_context(browser, base_url, session_key, site_page, scripts=(FREEZE,), **options)
        page, errors = open_page(context, base_url + site_page.path, warnings=True)
        if not cloth_running(page):
            problems.append(f"{label}: the cloth never started")
            context.close()
            return None, None, None
        return context, page, errors

    # Both appearances; wide (every corner, the menu open) and a phone (the top corners sit on the veil there).
    for theme in ("light", "dark"):
        for width, height, labels, moments, menu in ((1024, 640, wide_labels, (200, 240), True), (375, 667, corners[2:], (200,), False)):
            label = f"/about/ cloth at {width}px, {theme}"
            context, page, errors = opened(about, label, width=width, height=height, theme=theme)
            if not page:
                continue
            for count in moments:  # eased in, then four seconds of cloth later
                run_frames(page, count)
                capture(page)
                problems += clearing_contrast_problems(page, label, [".chapter", *labels], required=(".chapter",))
                if menu:
                    problems += clearing_contrast_problems(page, label, [menu_muted], words=True, required=(menu_muted,))
                    problems += clearing_contrast_problems(page, label, [menu_current], token="--ink", words=True, required=(menu_current,))
            problems += [f"{label}: console: {error}" for error in errors]
            context.close()

    # The clearing calms the cloth: the same frame with and without clearings.
    spread = {}
    for clearings in (True, False):
        context, _ = cloth_context(browser, base_url, session_key, about, scripts=(FREEZE,) if clearings else (FREEZE, NO_CLEARINGS))
        page, _ = open_page(context, base_url + about.path, warnings=True)
        if cloth_running(page):
            run_frames(page, 200)
            capture(page)
            spread[clearings] = canvas_stats(page, page.evaluate(CLEARING_RECT, ".chapter"))["std"]
        context.close()
    if len(spread) == 2:
        if spread[False] < 0.02:
            REVIEW_NOTES.append(f"/about/ cloth: the bare cloth behind the block hardly varies at this moment ({spread[False]:.3f}); the calm check proves little")
        elif spread[True] / spread[False] > 0.6:
            problems.append(f"/about/ cloth: the clearing hardly calms the cloth behind the block "
                            f"(spread {spread[True]:.3f} with it, {spread[False]:.3f} without)")

    # Scrolled: the block's clearing follows it.
    label = "/begin/ cloth, scrolled"
    context, page, errors = opened(begin, label)
    if page:
        run_frames(page, 200)
        page.evaluate("() => window.scrollTo(0, 400)")
        run_frames(page, 30)
        capture(page)
        problems += clearing_contrast_problems(page, label, [".chapter"])
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    # After an in-place change: the new chapter's block takes over the old one's clearing (the cloth behind
    # the text does not jump, spec 4.5), and has its clearing once it has eased in.
    label = "/about/ cloth, to Plans in place"
    context, page, errors = opened(about, label)
    if page:
        run_frames(page, 200)
        capture(page)
        block = page.evaluate(CLEARING_RECT, ".chapter")
        middle = {"x": block["x"] + block["width"] / 4, "y": block["y"] + block["height"] / 4,
                  "width": block["width"] / 2, "height": block["height"] / 2}  # inside Plans' larger block too
        calm = canvas_stats(page, middle)["std"]
        page.locator('#shell-menu a[href="/plans/"]').click()
        page.wait_for_function("() => location.pathname === '/plans/'", timeout=20000)
        page.wait_for_function("() => !document.querySelector('main').classList.contains('is-leaving')", timeout=20000)
        run_frames(page, 3)  # time is held still between runs: a clearing that started again from nothing shows now
        capture(page)
        jumped = canvas_stats(page, middle)["std"]
        if jumped > calm * 1.3 + 0.005:  # measured: 0.037 with the hand-over; 0.074 (the bare cloth) without it
            problems.append(f"{label}: the cloth behind the block jumped when the chapter changed (spread {calm:.3f} before, "
                            f"{jumped:.3f} after); the new block's clearing must carry on from the old one's")
        run_frames(page, 200)
        capture(page)
        problems += clearing_contrast_problems(page, label, [".chapter"], required=(".chapter",))
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    # The threshold: its line holds 3:1 on the bare cloth; after Come in the install line has its clearing.
    for theme in ("light", "dark"):
        label = f"/ cloth, {theme}"
        context, page, errors = opened(threshold, label, theme=theme)
        if not page:
            continue
        run_frames(page, 200)
        capture(page)
        problems += clearing_contrast_problems(page, label, ["[data-threshold-line]"], token="--ink", floor=3.0,
                                               required=("[data-threshold-line]",))
        page.locator("[data-come-in]").click()
        page.wait_for_function("() => { const box = document.querySelector('[data-install]'); return Boolean(box) && !box.hidden; }",
                               timeout=20000)  # Come in's fade-out is a real-time timer
        run_frames(page, 200)
        capture(page)
        problems += clearing_contrast_problems(page, f"{label}, after Come in", ["[data-install]"], required=("[data-install]",))
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()
    return problems
```

and change the `CLOTH_FAMILIES` list (two lines) so it ends with `clearing_problems` (keep the other names as they are).

- [ ] **Step 4: Watch the clearing checks fail without the markup, then pass**

With the server restarted, run only the clearing family, from the repo root:

```bash
site-tests/.venv/bin/python -c "
import sys; sys.path.insert(0, 'site-tests')
import run_site_checks as checks
from playwright.sync_api import sync_playwright
session = open('site-tests/session.txt').read().strip()
with sync_playwright() as p:
    browser = p.chromium.launch(args=checks.GL_ARGS)
    problems = checks.clearing_problems(browser, 'http://127.0.0.1:8777', session)
    print('\n'.join(problems)); print(len(problems), 'problem(s)')
"
```

Expected: `0 problem(s)`. Then prove the checks can fail: temporarily remove ` data-clearing="content"` from `layouts/chapter.html` and ` data-clearing="label"` from the four corners, and ` data-clearing="content"` from the install line in `chapters/threshold.html`, restart the server, run the command again (if no `after Come in` problem about `[data-install]` appears, its check proves nothing: measure it in a colour that does fail, or say in the ledger why not): expect contrast problems such as `--muted text over .chapter could fall to 3.0:1` in both appearances and the calm check's "hardly calms" line. Restore the markup (`git diff` must show only Step 2's changes), restart the server.

- [ ] **Step 5: Run everything**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages -v 1`. Expected: OK.
Run the full suite. Expected: OK.
Run the full browser checks. Expected: `0 problem(s) across 7 page(s)`.

- [ ] **Step 6: Commit**

```bash
git add backend/templates/shell.html backend/templates/layouts/chapter.html backend/templates/chapters/threshold.html \
  backend/pages/tests_shell.py backend/pages/tests_chapters.py site-tests/run_site_checks.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Clearings behind the words and the labels

The corners, a chapter's block and the threshold's install line ask for a
clearing with data-clearing, and veil.js calms the cloth behind them while
they are shown, so muted text keeps 4.5:1 over the moving cloth. The
browser checks read the canvas back and measure it, in both appearances, at
desktop and phone widths, scrolled, after an in-place change and after
Come in, which axe cannot see.
EOF
```

---

### Task 5: The ink wash replaces the flat fill

**Files:**
- Modify: `backend/static/css/base.css`
- Modify (tests): `site-tests/run_site_checks.py`

**Interfaces:**
- Consumes: `veil.js`'s ink (it adds `is-inked` to an element while its wash lives, from 64rem up, outside catalog frames; Task 3); `CLEARING_RECT` (Task 4); `FREEZE` and the cloth helpers (Task 3).
- Produces: CSS that turns the flat fill off on an inked element; `ink_problems()` (added to `CLOTH_FAMILIES`).

- [ ] **Step 1: Write the failing check**

In `site-tests/run_site_checks.py`, before the comment line just above `CLOTH_FAMILIES` (`# Every family of cloth checks, in the order they run; new ones go at the end.`), add:

```python
INK_PATCH = """(selector) => { const b = document.querySelector(selector).getBoundingClientRect();
  const rx = b.width * 0.48 + 6, ry = b.height * 0.8 + 4;   // the wash's radii (spec 3.4)
  return { x: b.left + b.width / 2 - rx * 0.7, y: b.top + b.height / 2 - ry * 0.7, width: rx * 1.4, height: ry * 1.4 }; }"""
INKED = "(selector) => document.querySelector(selector).classList.contains('is-inked')"
OWN_FILL = "(selector) => getComputedStyle(document.querySelector(selector), '::before').backgroundColor"


def ink_problems(browser, base_url, session_key):
    """With the cloth running, the pointer or keyboard focus grows an ink wash under a control from 64rem
    up, and its flat fill steps aside; the wash fades when the pointer leaves; the current chapter holds
    its wash while the menu is open; a prose link's fill steps aside too (spec 3.4). Below 64rem, and with
    Motion off, the flat fill is still the answer."""
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
            page.mouse.move(4, 320)
            run_frames(page, 150)  # the wash fades over 1.8 s
            capture(page)
            seen[hovered] = (grown, canvas_stats(page, page.evaluate(INK_PATCH, switch))["luma"])
            problems += [f"{label}: console: {error}" for error in errors]
            context.close()
        if len(seen) == 2:
            grew, faded = seen[True][0] - seen[False][0], seen[True][1] - seen[False][1]
            if abs(grew) < 0.03 or (grew > 0) != (theme == "dark"):
                problems.append(f"/about/ cloth, {theme}: hovering the switch should lay a wash under it "
                                f"({'lighter' if theme == 'dark' else 'darker'} by 0.03 of luma or more); it changed {grew:+.3f}")
            if abs(faded) > 0.01:
                problems.append(f"/about/ cloth, {theme}: the wash should have faded 2.5 s after the pointer left; {faded:+.3f} remains")

    label = "/about/ cloth, ink"
    context, _ = cloth_context(browser, base_url, session_key, about)
    page, errors = open_page(context, base_url + about.path, warnings=True)
    if cloth_running(page):
        run_frames(page, 60)
        current = '#shell-menu a[aria-current="page"]'
        if not page.evaluate(INKED, current) or page.evaluate(OWN_FILL, current) != "rgba(0, 0, 0, 0)":
            problems.append(f"{label}: the current chapter should hold its wash in the open menu, without its flat fill")
        for _ in range(6):  # keyboard focus on MENU grows a wash too
            page.keyboard.press("Tab")
            if page.evaluate("() => document.activeElement.matches('[data-menu-toggle]')"):
                break
        run_frames(page, 10)
        if not page.evaluate(INKED, "header [data-menu-toggle]"):
            problems.append(f"{label}: keyboard focus on MENU should grow a wash")
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

    # A phone, and Motion off: no wash, the flat fill.
    for label, width, height, motion in (("/about/ cloth on a phone", 375, 667, "on"), ("/about/ with Motion off", 1024, 640, "off")):
        context, _ = cloth_context(browser, base_url, session_key, about, width=width, height=height, motion=motion)
        page, errors = open_page(context, base_url + about.path, warnings=True)
        if motion == "on" and not cloth_running(page):
            problems.append(f"{label}: the cloth never started")
        page.locator(switch).scroll_into_view_if_needed()
        page.hover(switch)
        page.wait_for_timeout(300)
        if page.evaluate(INKED, switch) or page.evaluate(OWN_FILL, switch) == "rgba(0, 0, 0, 0)":
            problems.append(f"{label}: hovering the switch should give the flat fill, not a wash")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()
    return problems
```

and add `ink_problems` to the end of the `CLOTH_FAMILIES` list.

With the server running, run only this family (the same one-off command as in Task 4 Step 4, calling `checks.ink_problems`). Expected: problems saying the switch, the current chapter and the prose link keep their flat fill while inked (`fill rgba(...)` not transparent): `veil.js` inks them, but no CSS yet lets the wash replace the fill. The wash itself already shows (no "should lay a wash" line).

- [ ] **Step 2: Let the wash replace the fill**

In `backend/static/css/base.css`, change the comment above `.interactive {` from

```css
/* Every interactive element answers the cursor (spec 3.4). Until the cloth arrives the answer
   is the flat fill; the ink wash replaces it when Motion is on. */
```

to

```css
/* Every interactive element answers the cursor (spec 3.4) with a flat fill. While the cloth (veil.js)
   lays an ink wash under an element, the element carries is-inked and the fill steps aside, below. */
```

After the rule that ends the `.interactive[aria-selected="true"]::before` selector list (`background: var(--fill);` then `}`), add:

```css
.interactive.is-inked::before {
  background: transparent;
}
```

After the rule `.chapter :is(p, dd) a:not(.interactive, .label):is(:hover, :focus-visible) { ... }`, add:

```css
.chapter :is(p, dd) a:not(.interactive, .label).is-inked {
  background-color: transparent;
}
```

(Both new rules have the same specificity as the fill rules they follow, and win by coming after them.)

- [ ] **Step 3: Run the checks and the tests**

Run the ink family again. Expected: `0 problem(s)`.
Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages -v 1`. Expected: OK.
Run the full browser checks. Expected: `0 problem(s) across 7 page(s)` (the existing fill checks run with the stub `veil.js`, where nothing is ever inked).

- [ ] **Step 4: Commit**

```bash
git add backend/static/css/base.css site-tests/run_site_checks.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
The ink wash replaces the flat fill where the cloth runs

While veil.js lays a wash under an element it marks it is-inked, and the
element's flat fill steps aside; the fill stays the answer below 64rem,
with Motion off, before the cloth loads and if it fails. The browser checks
compare the same frames with and without the pointer on a control.
EOF
```

---

### Task 6: A device that cannot keep up, a hidden tab, a lost context

**Files:**
- Modify (tests): `site-tests/run_site_checks.py`

**Interfaces:**
- Consumes: `veil.js`'s adaptive rendering, hidden-tab pause and context-loss handling (Task 3: `SCALES`, `SLOW_MS`, `STILL_MS`, `WINDOW`, `WARMUP`; `stop("slow")`, `stop("lost")`); the cloth helpers (Task 3).
- Produces: `adaptive_problems()` (added to `CLOTH_FAMILIES`). This task adds checks only: the behaviour was written in Task 3, and these are the tests that pin it (Review Focus 1 and 4).

- [ ] **Step 1: Write the checks**

In `site-tests/run_site_checks.py`, before the comment line just above `CLOTH_FAMILIES` (`# Every family of cloth checks, in the order they run; new ones go at the end.`), add:

```python
SCALE = "() => { const canvas = document.querySelector('canvas.veil'); return canvas ? canvas.width / innerWidth : null; }"
HIDE_TAB = """(hidden) => {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => (hidden ? "hidden" : "visible") });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}"""


def adaptive_problems(browser, base_url, session_key):
    """The adaptive cloth (spec 8), on a phone, where frames are cheap and the scales are 0.5, 0.375
    and 0.28. A healthy device keeps 0.5. Frames that run long (50 ms of cloth time each) lower the
    resolution step by step and then give the still page for the visit: data-veil-off "slow", Motion and
    its button off, storage untouched. One long gap (a tab coming back) changes nothing. A hidden tab
    draws nothing and picks up where it was. A lost WebGL context gives the still page."""
    problems = []
    about = page_named("/about/")

    def opened(label):
        context, _ = cloth_context(browser, base_url, session_key, about, width=375, height=667, scripts=(FREEZE,))
        page, errors = open_page(context, base_url + about.path, warnings=True)
        if not cloth_running(page):
            problems.append(f"{label}: the cloth never started")
            context.close()
            return None, None, None
        return context, page, errors

    label = "/about/ cloth on a phone, healthy"
    context, page, errors = opened(label)
    if page:
        run_frames(page, 300)
        if abs(page.evaluate(SCALE) - 0.5) > 0.02:
            problems.append(f"{label}: a healthy device should keep 0.5 of the CSS resolution; it has {page.evaluate(SCALE):.3f}")
        run_frames(page, 1, step=5000)  # one five-second gap, as when a tab comes back
        run_frames(page, 200)
        state = page.evaluate(VEIL_STATE)
        if abs(page.evaluate(SCALE) - 0.5) > 0.02 or state["veil"] != "on":
            problems.append(f"{label}: one long gap should change nothing ({state}, scale {page.evaluate(SCALE)})")
        page.evaluate(HIDE_TAB, True)
        draws = page.evaluate(VEIL_STATE)["draws"]
        page.wait_for_timeout(600)
        if page.evaluate(VEIL_STATE)["draws"] != draws:
            problems.append(f"{label}: a hidden tab should draw nothing")
        page.evaluate(HIDE_TAB, False)
        page.wait_for_timeout(600)
        if page.evaluate(VEIL_STATE)["draws"] == draws or abs(page.evaluate(SCALE) - 0.5) > 0.02:
            problems.append(f"{label}: the cloth should pick up where it was when the tab shows again")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    label = "/about/ cloth on a phone that cannot keep up"
    context, page, errors = opened(label)
    if page:
        scales = []
        for _ in range(6):  # 90 frames at a time: a 30-frame settle and a 60-frame measure
            run_frames(page, 90, step=50)
            scales.append(page.evaluate(SCALE))
            if scales[-1] is None:
                break
        state = page.evaluate(VEIL_STATE)
        stepped = [round(s, 3) for s in scales if s is not None]
        reached = lambda scale: any(abs(s - scale) < 0.005 for s in stepped)  # a 375 px canvas is whole pixels: 0.375 comes out as 0.376
        if stepped != sorted(stepped, reverse=True) or not reached(0.375) or not reached(0.28):
            problems.append(f"{label}: the resolution should step down 0.5, 0.375, 0.28 and never up; it went {stepped}")
        expected = {"off": "slow", "motion": "off", "pressed": "false", "saved": "on", "canvases": 0, "veil": None}
        if {key: state[key] for key in expected} != expected:
            problems.append(f"{label}: at the lowest step it should give the still page for the visit, storage untouched ({state})")
        problems += [f"{label}: console: {error}" for error in errors]
        context.close()

    label = "/about/ cloth on a phone, the WebGL context lost"
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
        problems += [f"{label}: console: {error}" for error in errors if "CONTEXT_LOST" not in error]  # the loss itself is announced
        context.close()
    return problems
```

and add `adaptive_problems` to the end of the `CLOTH_FAMILIES` list.

- [ ] **Step 2: Run the checks, and prove they can fail**

With the server running, run only `checks.adaptive_problems` (the one-off command from Task 4 Step 4). Expected: `0 problem(s)`.
Prove the slow-device check can fail: temporarily change `const SLOW_MS = 24;` in `backend/static/js/veil.js` to `const SLOW_MS = 1e9;` and `const STILL_MS = 40;` to `const STILL_MS = 1e9;`, run the family again: expect "the resolution should step down" and "at the lowest step it should give the still page". Undo the change (`git diff backend/static/js/veil.js` must be empty).

- [ ] **Step 3: Run the full browser checks**

Expected: `0 problem(s) across 7 page(s)`.

- [ ] **Step 4: Commit**

```bash
git add site-tests/run_site_checks.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Check the adaptive cloth, a hidden tab and a lost context

On a phone at 50 ms a frame the cloth steps its resolution down and then
gives the still page for the visit, storage untouched; a healthy device and
a single long gap keep it as it is; a hidden tab draws nothing; a lost
WebGL context gives the still page.
EOF
```

---

### Task 7: Recipes, the spec where the build refined it, and the last checks

**Files:**
- Modify: `AGENTS.md`, `docs/superpowers/specs/2026-10-08-site-redesign-design.md`
- Not modified by this task: `.github/workflows/ci.yml` (Step 3 may recommend a change; the controller decides)

**Interfaces:**
- Consumes: everything above.
- Produces: what Muse's teams need to keep the cloth true (spec 7): where it lives, how a component gets a clearing, where the shader's colours are, and what the site checks do with it.

- [ ] **Step 1: Keep AGENTS.md true**

In `AGENTS.md`:

1. In the repo map, replace `backend/static/        the new shell's CSS, JS (js/behaviors/: tabs, Copy, Come in), fonts` with `backend/static/        the new shell's CSS, JS (js/veil.js: the cloth; js/behaviors/: tabs, Copy, Come in), fonts` and re-wrap that entry so no line passes 80 columns.
2. In "Site checks", after the sentence that ends `and Copy with the clipboard denied.`, add (wrapped at 80 columns, in the file's voice):
   "The cloth has checks of its own. Chromium runs with SwiftShader (`GL_ARGS`), so WebGL exists on any machine, and the run stops at once if it does not. Every other check gets an empty `veil.js`, so it runs as without the cloth. The cloth checks load the real one, drive its time with a virtual clock and read the canvas back: they check when the cloth runs and when it must not, the still page when WebGL is missing or fails, Motion switched on and off, the clearings (muted text at 4.5:1 over every pixel the cloth draws, which axe cannot see), the ink wash, a device that cannot keep up, a hidden tab and a lost context. Each family prints how long it took."
3. In "Change or add a component", after step 7, add a new step 8 and renumber the old step 8 as 9:
   "8. If the component shows text on the cloth outside a chapter's block (a new corner, a panel of its own), give its outer element `data-clearing="label"`, or `data-clearing="content"` for a block of reading text: `veil.js` calms the cloth behind any such element while it is shown, so the text keeps its contrast. A chapter's block has one already (`layouts/chapter.html`). The browser checks measure the contrast in `clearing_problems` (`site-tests/run_site_checks.py`): add the component's selector to the list in its `clearing_contrast_problems(page, label, [".chapter", *labels], ...)` call, and to `required=` too if the component is always shown there, so a selector that matches nothing fails instead of passing. Text that sits on an ink wash (a held current link) is measured by its words: give its selector its own call with `words=True` and the token its words use, as the open menu's links have."
4. Where `AGENTS.md` says colours live only in `tokens.css` (the component recipe's step 2), add one sentence: "The cloth's own colours, five per appearance, are in `appearance()` in `backend/static/js/veil.js` (spec 3.2); `pages.tests_veil` keeps its ground equal to `--paper`, so change them together."
5. Where step 7 of the component recipe says `shell.js` and the behaviours stay within 10 KB gzipped, add: "`veil.js` has its own budget, 15 KB gzipped (`pages.tests_veil`)."
6. In "Site checks", after the sentences about the cloth, add (wrapped at 80 columns): "Take full-page screenshots with `full_page_screenshot()`: in Chromium with SwiftShader a screenshot taller than 8,192 px takes WebGL away from the rest of the run, so taller pages are cut at 8,000 px. The cloth checks count display frames: `run_frames()` lets that many frames pass, and anything else that waits on animation frames shares the frame's time. They draw only the frames they read back; take a picture of the cloth with `cloth_screenshot()`, which draws for that moment."

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_fix_docs pages -v 1`. Expected: OK (the docs-hygiene tests read AGENTS.md).

- [ ] **Step 2: Amend the spec where the build refined it**

In `docs/superpowers/specs/2026-10-08-site-redesign-design.md` (each replacement must match exactly once; if one does not, the spec changed since this plan: make the smallest equivalent edit and record a ruling):

- §3.1: replace `keeping 20% of its variation so it still faintly moves. Strength is 0.88 for content and enough for labels to meet §9's contrast floor.` with `keeping about 30% of its variation so it still faintly moves (the prototype's formula keeps 1 - 0.8 × the strength). Strength is 0.88 for content and 0.92 for labels, measured to keep muted text at 4.5:1 over the darkest and lightest folds in both appearances. A content block's clearing has the prototype's soft edge; a label's box is padded by 12 CSS px and fades out over 28.`
- §3.1: replace `The shader renders at 0.8 of CSS resolution, or 0.5 on phones, and the canvas is scaled up.` with `The shader renders at 0.8 of CSS resolution, or 0.5 at 40rem and narrower (phones), whatever the device pixel ratio, and the canvas is scaled up.`
- §3.3: replace `It must match the shader's grain closely.` with `It must match the shader's grain closely: one tile per appearance, zero-mean over its own ground, with the shader's spread.`
- §3.4: replace `**Flat fill (Motion off):** a `--fill` block behind the label, inset a little inside the hit area, appearing in 0.15 s.` with `**Flat fill (Motion off, and wherever the wash is not drawn):** a `--fill` block behind the label, inset a little inside the hit area, appearing in 0.15 s when Motion is on (at once when Motion is off, when nothing moves). It is also what a control shows before the cloth has loaded and after it has failed.`
- §3.4: replace `  - **Inline links** have a dotted underline that gives way to the wash.` with that line followed by `  - **Where:** from 64rem up. Below that the top corners sit on the veil and the open menu is an opaque sheet, so the flat fill is the answer there.`
- §6.2: replace `corner labels, menu, canvas slot, <main>` with `corner labels, menu, <main>; the cloth's canvas is added first in <body> by veil.js`
- §8: replace `pause when the tab is hidden or the canvas is off-screen;` with `pause when the tab is hidden (the canvas is fixed to the window, so it is never off-screen);`
- §6.3: replace `  img/grain.png           tiling still-grain texture` with `  img/grain-paper.png, grain-ink.png   tiling still-grain textures, one per appearance` (Task 2 deleted `grain.png`).

- [ ] **Step 3: Time the browser checks against CI**

Run the full browser checks with `/usr/bin/time -p` in front. Record the wall time (real), the user and system CPU seconds, and each `cloth:` family's time from the log. CI's `site` job allows 20 minutes: setup takes under a minute and the existing checks about 5.3 minutes on CI's 2-core runner (about 121 CPU-seconds, mostly waiting; 5.6 minutes on the planning laptop). The dry run, with the probe drawing only the frames a check reads, measured the whole run at 8:05 wall and 340 CPU-seconds (232 user, 107 system) on an 8-core laptop, the cloth families at about 143 s wall and 60 CPU-seconds; before that lever the cloth families alone took about 950 CPU-seconds, which a 2-core runner could not have finished in time. Report DONE_WITH_CONCERNS, with all these numbers, if the run's user plus system seconds exceed 600 or its wall time exceeds 10 minutes: something is drawing frames nobody reads again (check `cloth.drawAll` is back to false after every `cloth_screenshot`, and that no check calls `capture` in a loop). Do not change `ci.yml`. The local numbers are an estimate: the PR notes ask the owner for one CI run of the branch, to read the real `site` job's duration.

- [ ] **Step 4: Bring the branch up to date and run everything**

```bash
git fetch origin && git rebase origin/main
```

Resolve a conflict in `AGENTS.md`, the spec or the templates by keeping both sides' changes (Muse's teams push to `main`) and record a ruling. A conflict in `site-tests/` or `backend/static/js/` means someone else changed the files this plan edits: stop and report (Global Constraints). Then run, from the repo root:

- the full suite. Expected: OK.
- `cd backend && DJANGO_DEBUG=False DJANGO_SECRET_KEY=local-check-not-a-secret DJANGO_ALLOW_SQLITE=True ../venv/bin/python manage.py collectstatic --noinput --verbosity 1 && ls staticfiles/js/veil.*.js staticfiles/img/grain-*.png && rm -rf staticfiles`. Expected: a `static files copied` line, a hashed `veil.js`, and the two hashed grain tiles.
- `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py makemigrations --check --dry-run`. Expected: `No changes detected`.
- the full browser checks. Expected: `0 problem(s) across 7 page(s)`.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md docs/superpowers/specs/2026-10-08-site-redesign-design.md
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Recipes for the cloth, and the spec where the build refined it

AGENTS.md says where the cloth lives, how a component asks for a clearing,
where the shader's colours are, and what the site checks do with the
cloth. The spec records the measured clearing strengths, what a phone is,
the matched still grain, where the ink shows, and the canvas's place.
EOF
```

The PR description (when the owner asks for one) says three things an agent cannot do. The cloth is still to be measured on a real mid-range Android phone (spec 13): open the site on one with Motion on, watch that the cloth moves smoothly and that the still page does not take over within a few seconds, and do the same on an iPhone in Low Power Mode (it caps animation at 30 frames a second, which should keep the cloth at its lowest resolution). The `site` job's real duration on CI is to be read from the first CI run of the branch (the local estimate is about 8 minutes of the 20 allowed). And the first CI run is also the first on Linux: if the WebGL precondition stops it, the message says whether Chromium there has no WebGL at all or calls SwiftShader a major performance caveat, and `GL_ARGS` may need `--use-gl=angle`.

---
