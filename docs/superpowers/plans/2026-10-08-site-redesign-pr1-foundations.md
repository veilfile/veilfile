# Site redesign PR 1: Foundations Implementation Plan

**Goal:** Build the new site's foundations alongside the current pages: design tokens, self-hosted fonts, the "Let go" mark and favicons, a base `shell.html` with the corner menu and display controls, a registry of public pages that drives the menu and `sitemap.xml`, a staff-only component catalog, content checks for Muse, and a browser-check CI job named `site`.

**Architecture:** A new Django app `backend/pages/` owns the registry, the sitemap, the catalog and the content checks. The shell is a Django template (`backend/templates/shell.html`) that later pages extend. It uses plain CSS and one ES module from `backend/static/`, with no build step. Nothing a visitor sees changes in this PR. The current pages keep their own templates until PR 2 onward moves them onto the shell, so the existing tests that pin today's markup keep passing. The only new page is the staff-only catalog at `/app/components/`.

**Tech Stack:** Python 3.12, Django 6.1.1, WhiteNoise 6.12.0 (`CompressedManifestStaticFilesStorage`), plain CSS and JavaScript modules, Playwright for Python 1.49.1 and axe-core 4.10.2 in CI only, fontTools 4.55.3 for a one-off font tool only.

**Spec:** `docs/superpowers/specs/2026-10-08-site-redesign-design.md`. Read sections 3, 4.1, 4.4, 6, 7, 10 and 11 before starting. This plan implements delivery step 1 of section 11.

**Branch:** execute on `design/site-redesign`, which carries the spec and this plan. A worktree made from `main` has neither.

**Verified:** a full dry run of this plan (every task, the full suite, production `collectstatic`, the deploy check and the browser checks) passed once the fixes from its review were folded in. A second run of the revised code then gave 986 tests OK, `collectstatic` clean and 0 browser-check problems. Its 20 "review by eye" lines are the fixed bottom corners over page content, where axe cannot measure contrast. `tests_tokens.py` checks the palette's contrast instead.

## Global Constraints

- **Leave existing pages alone.** Do not edit any existing template under `backend/templates/` except `sitemap.xml`, and do not edit anything under `frontend/`, `backend/legal/documents/` or the old includes. Many tests pin today's markup (`drop_core/tests_ambient.py`, `tests_menu.py`, `tests_site_pages.py`, `tests.py` AccessibilityTests). They must all still pass.
- **No new runtime dependencies.** Nothing is added to `backend/requirements.txt`, because that needs owner approval (AGENTS.md). Playwright and axe-core live only in `site-tests/`. fontTools and brotli live only in `tools/requirements-fonts.txt`, installed in a throwaway virtualenv.
- **Colors** live only in `backend/static/css/tokens.css`, with exactly these values (spec 3.2):
  - Paper: `--paper #ebe6de`, `--ink #2c2925`, `--muted #5d574f`, `--hair rgba(44, 41, 37, .2)`, `--fill rgba(44, 41, 37, .12)`, `--box rgba(240, 236, 229, .35)`.
  - Ink: `--paper #1b1917`, `--ink #e6e0d6`, `--muted #a0998e`, `--hair rgba(230, 224, 214, .2)`, `--fill rgba(230, 224, 214, .14)`, `--box rgba(30, 28, 26, .35)`.
- **Fonts** (spec 3.5): Cormorant Garamond italic 300 and 400; Newsreader 400 to 500 roman and italic, with optical sizing; IBM Plex Mono 400. All self-hosted as WOFF2, split by Unicode range, never subset to the characters on the site today, and shipped with their SIL Open Font License files.
- **Labels:** IBM Plex Mono, uppercase, `letter-spacing: .2em`, `font-size: clamp(10px, 1.05vw, 13px)`. Corner labels sit 5.5% from the top or bottom and 5% from the side (spec 4.1).
- **The mark** is the rows `####`, `###.`, `##.#`, `#.#.` on 140-unit cells in a 560 viewBox, with path `M0 0H560V140H420V280H280V420H140V560H0Z M420 280H560V420H420Z M280 420H420V560H280Z` (spec 3.6).
- **Preferences** keep today's storage so the old pages and the shell never fight:
  - localStorage key `veilfile-theme` holds `light`, `dark` or `system` (default `system`).
  - localStorage key `veilfile-motion` holds `on` or `off`. With no stored value, the default comes from `prefers-reduced-motion`.
  - The interface calls these Paper (`light`), Ink (`dark`) and Auto (`system`). The switch cycles Ink → Auto → Paper → Ink. Task 13 amends spec 6.4 to match.
- **Template rules** (spec 6.2, 7.2):
  - no `style=` attributes;
  - components are included only as `{% include "components/<name>.html" with … only %}`;
  - every component starts with two one-line comments, `{# Component: … #}` and `{# Inputs: … #}`;
  - every Django comment fits on one line, because a multi-line `{# #}` leaks into the page in this project.
- **Copy:** plain, calm, specific, American spelling. No em dashes in new copy.
- **Tests:**
  - New tests go in `backend/pages/tests_<topic>.py`.
  - Run a module with `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_<topic> -v 2`.
  - The full suite takes about 7 minutes; run it at the end of each task that touches shared files. It currently has 896 tests; never hardcode the count.
  - Never point a test or the site checks at https://veilfile.com.
- **Commits:** every commit uses `git -c user.name=Veilfile -c user.email=support@veilfile.com commit` with no attribution lines.

- **The `site` CI job** is named exactly `site` and is not a required check yet. Spec step 7 makes it required with the owner's go-ahead.

## Review Focus

These are the conditions a visitor or Muse can bring that no task's main tests exercise. Each has a test in the task that owns the code.

1. **Storage that throws,** as in Safari private browsing or with blocked site data. The page renders in Auto with no console errors, and the switches still change the page. Covered by the site checks' blocked-storage run (Task 9), made to pass in Task 10.
2. **A stored value this version doesn't know,** such as `veilfile-theme = paper` or garbage. The boot script falls back to `system`, and to the system's motion setting. Covered by the boot script test (Task 7) and the site checks' unknown-values run (Task 9).
3. **A system that asks for reduced motion.** With nothing saved, Motion is off. With `on` saved, Motion is on. Covered by the site checks' reduced-motion runs (Task 9).
4. **No JavaScript.** Every menu link is visible and reachable, the switches that need JavaScript are hidden, and the page doesn't scroll sideways. Covered by the CSS gating test (Task 7) and the site checks' no-JavaScript run (Task 9).
5. **A content file with a byte-order mark or invalid UTF-8, or Windows line endings.** The blog loader skips a post with a byte-order mark or invalid UTF-8 and only logs a warning, so the post quietly never appears. Windows line endings load fine, but the house rule is LF. The content checks name the file and the fix for all three (Task 11).

---

## File structure

```
backend/pages/                      new Django app: everything about public pages
  __init__.py, apps.py
  registry.py                       every public page (title, description) and the menu
  views.py                          sitemap.xml and the component catalog
  catalog.py                        the catalog's entries: one per component, with sample inputs
  fonts.py                          what the self-hosted fonts can draw (reads ranges.json)
  content_checks.py                 checks for posts, Docs and legal pages; Problems name file, line and fix
  templatetags/__init__.py, shell.py   {% shell_menu %}: the menu groups for this request
  tests_static.py, tests_fonts.py, tests_tokens.py, tests_images.py, tests_registry.py,
  tests_sitemap.py, tests_shell.py, tests_catalog.py, tests_content.py, tests_site_rules.py
backend/templates/
  shell.html                        base template: head, boot script, corners, <main>
  components/mark.html, lockup.html, menu.html, controls.html
  pages/catalog.html                the staff-only component catalog
  sitemap.xml                       rewritten to loop over entries (modified)
backend/static/                     served by WhiteNoise at /static/; no build step
  css/fonts.css                     generated by tools/vendor_fonts.py
  css/tokens.css, css/base.css
  css/components/mark.css, lockup.css, menu.css, controls.css
  css/pages/catalog.css
  js/shell.js                       menu and display preferences
  fonts/<family>/*.woff2, OFL.txt   generated by tools/vendor_fonts.py
  fonts/ranges.json                 generated: what each face can draw
  img/mark.svg, favicon.svg, favicon-16.png, favicon-32.png, apple-touch-icon.png, grain.png   generated by tools/make_images.py
tools/                              one-off generators, run by hand, never shipped
  vendor_fonts.py, requirements-fonts.txt, make_images.py
site-tests/                         browser checks (CI job `site`), never shipped
  requirements.txt, package.json, package-lock.json
  make_staff_session.py, run_site_checks.py
Modified: backend/config/settings.py, backend/config/urls.py, backend/drop_core/views.py (old sitemap view removed),
          backend/drop_core/tests_fix_docs.py (scan registry.py for prices), docs/llms.txt, .github/workflows/ci.yml,
          .gitignore, AGENTS.md, README.md, the spec
```

---

### Task 1: The `pages` app and static file settings

**Files:**
- Create: `backend/pages/__init__.py`, `backend/pages/apps.py`, `backend/static/.gitkeep`, `backend/pages/tests_static.py`
- Modify: `backend/config/settings.py` (INSTALLED_APPS at about line 83; static settings at about lines 234-238)
- Modify: `backend/config/test_runner.py` (after `_enable_stub_templates`, about line 82; the runner's setup and teardown)
- Modify: `.github/workflows/ci.yml` (backend job, after the "Backend tests" step)

**Interfaces:**
- Consumes: nothing.
- Produces: the app label `pages`. `settings.STATICFILES_DIRS[0] == BASE_DIR / "static"`. Production static storage is `whitenoise.storage.CompressedManifestStaticFilesStorage`. Django's test runner sets `DEBUG=False` during tests, and the manifest storage would then raise for every file `collectstatic` has not hashed. So the test runner swaps in plain `StaticFilesStorage`, and in tests `{% static %}` returns plain `/static/<path>` URLs.

- [ ] **Step 1: Write the failing test**

Create `backend/pages/__init__.py` (empty) and `backend/pages/tests_static.py`:

```python
"""Static files for the new shell (spec 6.3): served from backend/static, hashed and compressed in production."""
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.templatetags.static import static
from django.test import SimpleTestCase


class StaticSettingsTests(SimpleTestCase):
    def test_pages_app_is_installed(self):
        self.assertTrue(apps.is_installed("pages"))

    def test_backend_static_is_the_first_static_dir(self):
        static_dir = Path(settings.BASE_DIR) / "static"
        self.assertEqual(Path(settings.STATICFILES_DIRS[0]), static_dir)
        self.assertTrue(static_dir.is_dir())

    def test_production_storage_hashes_and_compresses(self):
        # The test runner swaps in plain storage (config/test_runner.py), so read the settings module itself.
        from config import settings as project_settings

        self.assertEqual(
            project_settings.STORAGES["staticfiles"]["BACKEND"],
            "whitenoise.storage.CompressedManifestStaticFilesStorage",
        )

    def test_static_urls_stay_plain_in_tests(self):
        # Tests never run collectstatic, so the runner serves plain storage: no manifest needed.
        self.assertEqual(static("css/tokens.css"), "/static/css/tokens.css")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_static -v 2`
Expected: FAIL and ERROR. `test_pages_app_is_installed` fails with `AssertionError`. `test_backend_static_is_the_first_static_dir` errors with `IndexError` (STATICFILES_DIRS is empty locally). `test_production_storage_hashes_and_compresses` errors with `AttributeError: module 'config.settings' has no attribute 'STORAGES'`.

- [ ] **Step 3: Add the app, the static dir and the storage**

Create `backend/pages/apps.py`:

```python
from django.apps import AppConfig


class PagesConfig(AppConfig):
    """Public pages on the new shell: the registry, sitemap, component catalog and content checks."""

    name = "pages"
    label = "pages"
```

Create an empty `backend/static/.gitkeep`. The directory must exist, or `check --deploy` warns `staticfiles.W004` and CI fails.

In `backend/config/settings.py`, add `"pages.apps.PagesConfig",` to `INSTALLED_APPS`, directly after `"drop_mcp",`. Then replace the line

```python
STATICFILES_DIRS = [FRONTEND_DIST] if FRONTEND_DIST.exists() else []
```

with

```python
# backend/static holds the new shell's CSS, JS, fonts and images. frontend/dist stays until
# the React app is removed (site redesign PR 6).
STATICFILES_DIRS = [BASE_DIR / "static"] + ([FRONTEND_DIST] if FRONTEND_DIST.exists() else [])

# Hashed, compressed static files in production (long-cacheable). Local runs with DEBUG on get
# plain names; the test runner swaps in plain storage (config/test_runner.py).
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
```

In `backend/config/test_runner.py`, add these two functions directly after `_enable_stub_templates`:

```python
def _enable_plain_static_files():
    """Tests run with DEBUG=False, where the manifest storage raises for any file
    collectstatic has not hashed. Serve plain /static/ URLs in tests instead."""
    storages = {**settings.STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    override = override_settings(STORAGES=storages)
    override.enable()
    return override


def _enable_test_overrides():
    return _enable_stub_templates(), _enable_plain_static_files()
```

In the same file, make three more changes:

- In `VeilfileParallelTestSuite`, change `process_setup = _enable_stub_templates` to `process_setup = _enable_test_overrides`, so `--parallel` workers get both overrides.
- In `VeilfileTestRunner.setup_test_environment`, change `self._templates = _enable_stub_templates()` to `self._templates, self._static = _enable_test_overrides()`.
- In `teardown_test_environment`, add `self._static.disable()` directly before `self._templates.disable()`.

- [ ] **Step 4: Run the test and watch it pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_static -v 2`
Expected: 4 tests, OK.

- [ ] **Step 5: Prove production collectstatic works, and keep proving it in CI**

Run, from the repo root:
`cd backend && DJANGO_DEBUG=False DJANGO_SECRET_KEY=local-check-not-a-secret DJANGO_ALLOW_SQLITE=True ../venv/bin/python manage.py collectstatic --noinput --verbosity 1 && rm -rf staticfiles`
Expected: a line like `N static files copied to '.../backend/staticfiles', M post-processed.` and no traceback. These are the same settings the Dockerfile uses.

In `.github/workflows/ci.yml`, in the `backend` job, add this step directly after the "Backend tests" step. The frontend is already built at that point, so the React files get collected too:

```yaml
      - name: Static files collect with production storage
        working-directory: backend
        env:
          DJANGO_DEBUG: "False"
          DJANGO_SECRET_KEY: ci-only-not-a-secret
          DJANGO_ALLOW_SQLITE: "True"
        run: python manage.py collectstatic --noinput --verbosity 0
```

- [ ] **Step 6: Run the full suite**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: OK, with 4 more tests than before. Without the runner change, four existing admin tests in `drop_core/tests_fix_auth.py` would fail too, because the admin's templates use `{% static %}`. Also run `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py makemigrations --check --dry-run`. Expected: `No changes detected`.

- [ ] **Step 7: Commit**

```bash
git add backend/pages/__init__.py backend/pages/apps.py backend/pages/tests_static.py backend/static/.gitkeep backend/config/settings.py backend/config/test_runner.py .github/workflows/ci.yml
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Add the pages app and hashed static files for the new shell

backend/static is the first static dir; production storage is WhiteNoise's
CompressedManifestStaticFilesStorage, and the test runner keeps plain
storage because tests run with DEBUG off and never collect static files.
CI now runs collectstatic with production settings so a broken asset
fails the PR, not the Docker build.

EOF
```

---

### Task 2: Self-hosted fonts

**Files:**
- Create: `tools/requirements-fonts.txt`, `tools/vendor_fonts.py`, `backend/pages/fonts.py`, `backend/pages/tests_fonts.py`
- Generated by the tool and committed: `backend/static/fonts/**`, `backend/static/css/fonts.css`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - **`pages.fonts.FontRanges`**: `FontRanges.load()`, `.covers(role: str, char: str) -> bool` for the roles `"display"`, `"reading"` and `"mono"`, plus `.roles` (role to face key) and `.faces` (face key to a list of `(first, last)` code-point pairs).
  - **`pages.fonts.FAMILY_NAMES`:** `{"display": "Cormorant Garamond", "reading": "Newsreader", "mono": "IBM Plex Mono"}`.
  - **`pages.fonts.parse_ranges(text) -> list[tuple[int, int]]`** and **`pages.fonts.merge(spans) -> list[tuple[int, int]]`.**
  - **Static files:** `css/fonts.css` declares the families `"Cormorant Garamond"`, `"Newsreader"` and `"IBM Plex Mono"`, plus the fallbacks `"Cormorant Garamond Fallback"`, `"Newsreader Fallback"` and `"IBM Plex Mono Fallback"`. The preload files are `fonts/cormorant-garamond/italic-300-latin.woff2` and `fonts/newsreader/normal-400-500-latin.woff2`.

- [ ] **Step 1: Write the failing tests**

Create `backend/pages/tests_fonts.py`:

```python
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
PRELOADS = ("fonts/cormorant-garamond/italic-300-latin.woff2", "fonts/newsreader/normal-400-500-latin.woff2")


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
    def test_ranges_json_matches_the_unicode_ranges_in_fonts_css(self):
        data = json.loads(RANGES.read_text(encoding="utf-8"))
        spans = {}
        for face in faces():
            if "unicode-range" in face:
                spans.setdefault(face_key(face), []).extend(parse_ranges(face["unicode-range"]))
        self.assertEqual(set(spans), set(data["faces"]))
        for key, found in spans.items():
            with self.subTest(face=key):
                self.assertEqual(merge(found), [tuple(span) for span in data["faces"][key]])

    def test_roles_name_shipped_faces(self):
        ranges = FontRanges.load()
        self.assertEqual(set(ranges.roles), {"display", "reading", "mono"})
        for role, key in ranges.roles.items():
            with self.subTest(role=role):
                self.assertIn(key, ranges.faces)

    def test_the_faces_draw_the_characters_the_site_uses_today(self):
        ranges = FontRanges.load()
        for role in ("display", "reading", "mono"):
            for char in "Aa1·—…é":
                with self.subTest(role=role, char=char):
                    self.assertTrue(ranges.covers(role, char))

    def test_parse_and_merge(self):
        self.assertEqual(parse_ranges("U+0000-00FF, U+0131, u+0152-0153"), [(0, 255), (305, 305), (338, 339)])
        self.assertEqual(merge([(5, 9), (0, 4), (20, 30), (25, 40)]), [(0, 9), (20, 40)])
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_fonts -v 2`
Expected: ERROR, `ModuleNotFoundError: No module named 'pages.fonts'`.

- [ ] **Step 3: Write `pages/fonts.py`**

Create `backend/pages/fonts.py`:

```python
"""What the self-hosted fonts can draw (spec 3.5), read from static/fonts/ranges.json.

tools/vendor_fonts.py writes that file; the content checks read it to catch characters
that no font on the site can draw.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

RANGES_FILE = Path(settings.BASE_DIR) / "static" / "fonts" / "ranges.json"
FAMILY_NAMES = {"display": "Cormorant Garamond", "reading": "Newsreader", "mono": "IBM Plex Mono"}


def parse_ranges(text):
    """'U+0000-00FF, U+0131' -> [(0, 255), (305, 305)]"""
    spans = []
    for part in text.split(","):
        part = part.strip().upper()
        if part.startswith("U+"):
            part = part[2:]
        first, _, last = part.partition("-")
        spans.append((int(first, 16), int(last or first, 16)))
    return spans


def merge(spans):
    """Sort and join touching or overlapping (first, last) spans."""
    merged = []
    for first, last in sorted(spans):
        if merged and first <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return merged


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
```

- [ ] **Step 4: Write the vendoring tool**

Create `tools/requirements-fonts.txt`:

```
# Only for tools/vendor_fonts.py, run by hand in a throwaway virtualenv. Never installed in the app.
fonttools==4.55.3
brotli==1.1.0
```

Create `tools/vendor_fonts.py`:

```python
#!/usr/bin/env python3
"""Vendor the site's three fonts from Google Fonts into backend/static (spec 3.5).

Run by hand when the fonts change; it needs network access and CI never runs it.
It writes, replacing what was there:

  backend/static/fonts/<family>/<style>-<weight>-<subset>.woff2   one file per Unicode range
  backend/static/fonts/<family>/OFL.txt                           the SIL Open Font License
  backend/static/fonts/ranges.json                                what each face can draw
  backend/static/css/fonts.css                                    @font-face rules and fallbacks

Usage, from the repo root, in a throwaway virtualenv (fontTools measures the fallbacks;
it is never installed in the app):

  python3 -m venv /tmp/vf-fonts
  /tmp/vf-fonts/bin/pip install -r tools/requirements-fonts.txt
  /tmp/vf-fonts/bin/python tools/vendor_fonts.py

The fallback faces are tuned against Georgia and Courier New. The default paths are
macOS's; pass --georgia, --georgia-italic and --courier to point elsewhere.
"""
import argparse
import json
import re
import shutil
import urllib.request
from pathlib import Path

from fontTools.ttLib import TTFont

REPO = Path(__file__).resolve().parent.parent
FONTS_DIR = REPO / "backend" / "static" / "fonts"
FONTS_CSS = REPO / "backend" / "static" / "css" / "fonts.css"
CSS2_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Cormorant+Garamond:ital,wght@1,300;1,400"
    "&family=Newsreader:ital,opsz,wght@0,6..72,400..500;1,6..72,400..500"
    "&family=IBM+Plex+Mono:wght@400"
    "&display=swap"
)
# Google serves WOFF2 split by Unicode range only to browsers it recognises.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
LICENCES = {
    "cormorant-garamond": "https://raw.githubusercontent.com/google/fonts/main/ofl/cormorantgaramond/OFL.txt",
    "newsreader": "https://raw.githubusercontent.com/google/fonts/main/ofl/newsreader/OFL.txt",
    "ibm-plex-mono": "https://raw.githubusercontent.com/google/fonts/main/ofl/ibmplexmono/OFL.txt",
}
# Which face each role in the design uses (read by the content checks through ranges.json).
ROLES = {
    "display": "cormorant-garamond/italic-300",
    "reading": "newsreader/normal-400-500",
    "mono": "ibm-plex-mono/normal-400",
}
# Fallback faces, measured so text does not jump when the web fonts arrive:
# face key -> (fallback family, font-style, local() sources, which system font to measure)
FALLBACKS = {
    "cormorant-garamond/italic-300": ("Cormorant Garamond Fallback", "italic", ('local("Georgia Italic")', 'local("Georgia-Italic")'), "georgia_italic"),
    "newsreader/normal-400-500": ("Newsreader Fallback", "normal", ('local("Georgia")',), "georgia"),
    "newsreader/italic-400-500": ("Newsreader Fallback", "italic", ('local("Georgia Italic")', 'local("Georgia-Italic")'), "georgia_italic"),
    "ibm-plex-mono/normal-400": ("IBM Plex Mono Fallback", "normal", ('local("Courier New")', 'local("CourierNewPSMT")'), "courier"),
}
SAMPLE = "The quick brown fox jumps over the lazy dog, then rests. 0123456789"
BLOCK = re.compile(r"/\*\s*(?P<subset>[\w-]+)\s*\*/\s*@font-face\s*\{(?P<body>[^}]*)\}")


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def slugify(family):
    return family.lower().replace(" ", "-")


def declaration(body, name):
    match = re.search(rf"{name}\s*:\s*([^;]+);", body)
    if not match:
        raise SystemExit(f"Google's CSS has no {name} in a block: {body.strip()[:120]}")
    return match.group(1).strip()


def parse_ranges(text):
    spans = []
    for part in text.split(","):
        part = part.strip().upper()
        if part.startswith("U+"):
            part = part[2:]
        first, _, last = part.partition("-")
        spans.append((int(first, 16), int(last or first, 16)))
    return spans


def merge(spans):
    merged = []
    for first, last in sorted(spans):
        if merged and first <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return merged


def average_width(font):
    cmap, metrics = font.getBestCmap(), font["hmtx"]
    widths = [metrics[cmap[ord(char)]][0] for char in SAMPLE if ord(char) in cmap]
    return sum(widths) / len(widths)


def metric_overrides(web, fallback):
    """CSS descriptors that make `fallback` take the space `web` will (the method Next.js and Capsize use)."""
    web_em, fallback_em = web["head"].unitsPerEm, fallback["head"].unitsPerEm
    size_adjust = (average_width(web) / web_em) / (average_width(fallback) / fallback_em)
    hhea = web["hhea"]

    def percent(value):
        return f"{value / (web_em * size_adjust) * 100:.2f}%"

    return {
        "size-adjust": f"{size_adjust * 100:.2f}%",
        "ascent-override": percent(hhea.ascent),
        "descent-override": percent(abs(hhea.descent)),
        "line-gap-override": percent(hhea.lineGap),
    }


def main():
    parser = argparse.ArgumentParser(description="Vendor the site's fonts into backend/static.")
    parser.add_argument("--georgia", default="/System/Library/Fonts/Supplemental/Georgia.ttf")
    parser.add_argument("--georgia-italic", default="/System/Library/Fonts/Supplemental/Georgia Italic.ttf")
    parser.add_argument("--courier", default="/System/Library/Fonts/Supplemental/Courier New.ttf")
    args = parser.parse_args()
    system_fonts = {"georgia": args.georgia, "georgia_italic": args.georgia_italic, "courier": args.courier}
    for name, path in system_fonts.items():
        if not Path(path).is_file():
            raise SystemExit(f"Cannot measure the fallback: {path} does not exist. Pass --{name.replace('_', '-')}.")

    css = fetch(CSS2_URL).decode("utf-8")
    shutil.rmtree(FONTS_DIR, ignore_errors=True)
    faces = {}
    for match in BLOCK.finditer(css):
        body, subset = match.group("body"), match.group("subset")
        family = declaration(body, "font-family").strip("'\"")
        style = declaration(body, "font-style")
        weight = declaration(body, "font-weight")
        url = re.search(r"url\((\S+?)\)", body).group(1)
        key = f"{slugify(family)}/{style}-{weight.replace(' ', '-')}"
        relative = f"{key}-{subset}.woff2"
        target = FONTS_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fetch(url))
        face = faces.setdefault(key, {"family": family, "style": style, "weight": weight, "files": []})
        face["files"].append({"subset": subset, "path": relative, "ranges": declaration(body, "unicode-range")})
    missing = [key for key in [*ROLES.values(), *FALLBACKS] if key not in faces]
    if missing:
        raise SystemExit(f"Google returned no files for {missing}; check CSS2_URL.")

    for family_slug, url in LICENCES.items():
        (FONTS_DIR / family_slug / "OFL.txt").write_bytes(fetch(url))

    blocks = ["/* Generated by tools/vendor_fonts.py: rerun it rather than editing this file. */"]
    for key in sorted(faces):
        face = faces[key]
        for file in face["files"]:
            blocks.append(
                "@font-face {\n"
                f'  font-family: "{face["family"]}";\n'
                f"  font-style: {face['style']};\n"
                f"  font-weight: {face['weight']};\n"
                "  font-display: swap;\n"
                f'  src: url("../fonts/{file["path"]}") format("woff2");\n'
                f"  unicode-range: {file['ranges']};\n"
                "}"
            )
    for key, (fallback_family, style, sources, system_font) in FALLBACKS.items():
        latin = next(file for file in faces[key]["files"] if file["subset"] == "latin")
        overrides = metric_overrides(TTFont(FONTS_DIR / latin["path"]), TTFont(system_fonts[system_font]))
        descriptors = "".join(f"  {name}: {value};\n" for name, value in overrides.items())
        blocks.append(
            "@font-face {\n"
            f'  font-family: "{fallback_family}";\n'
            f"  font-style: {style};\n"
            f"  src: {', '.join(sources)};\n"
            f"{descriptors}"
            "}"
        )
    FONTS_CSS.parent.mkdir(parents=True, exist_ok=True)
    FONTS_CSS.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")

    ranges = {
        "generated_by": "tools/vendor_fonts.py",
        "roles": ROLES,
        "faces": {
            key: merge([span for file in face["files"] for span in parse_ranges(file["ranges"])])
            for key, face in sorted(faces.items())
        },
    }
    (FONTS_DIR / "ranges.json").write_text(json.dumps(ranges, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {sum(len(face['files']) for face in faces.values())} font files for {len(faces)} faces.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tool**

Run, from the repo root:

```bash
python3 -m venv /tmp/vf-fonts
/tmp/vf-fonts/bin/pip install -r tools/requirements-fonts.txt
/tmp/vf-fonts/bin/python tools/vendor_fonts.py
```

Expected: `Wrote N font files for 5 faces.`, where N is about 20 to 25. Check the results:

- `ls backend/static/fonts/*` shows `cormorant-garamond/`, `newsreader/` and `ibm-plex-mono/`, each with several `*.woff2` files and an `OFL.txt`.
- `backend/static/css/fonts.css` ends with three fallback blocks, each with four percentage descriptors.

If Google's response has no `/* latin */` block for a face, the tool stops with a message. Do not hand-edit the outputs. Fix the URL or the tool and rerun.

- [ ] **Step 6: Run the tests and watch them pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_fonts -v 2`
Expected: 10 tests, OK. If `test_the_faces_draw_the_characters_the_site_uses_today` fails for one role and character, look at that face's ranges in `ranges.json`. If a face genuinely lacks a character the site uses today, stop and report it rather than changing the test.

- [ ] **Step 7: Commit**

```bash
git add tools/requirements-fonts.txt tools/vendor_fonts.py backend/pages/fonts.py backend/pages/tests_fonts.py backend/static/fonts backend/static/css/fonts.css
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Self-host the site's fonts, split by Unicode range

Cormorant Garamond, Newsreader and IBM Plex Mono as WOFF2, one file per
Unicode range, with their OFL licences and metric-matched fallbacks.
tools/vendor_fonts.py regenerates them; ranges.json records what each
face can draw, for the content checks.

EOF
```

---

### Task 3: Design tokens and base styles

**Files:**
- Create: `backend/static/css/tokens.css`, `backend/static/css/base.css`, `backend/pages/tests_tokens.py`

**Interfaces:**
- Consumes: the font family names from Task 2, and `img/grain.png` from Task 4. The `url()` in base.css resolves once Task 4 lands; until then the browser shows plain paper.
- Produces:
  - **Custom properties:** `--paper`, `--ink`, `--muted`, `--hair`, `--fill`, `--box`, `--font-display`, `--font-reading`, `--font-mono`, `--label-size`, `--label-tracking`, `--reading-size`, `--reading-leading`, `--measure`, `--edge-block`, `--edge-inline`, `--space-1` to `--space-6`, `--fill-fade`.
  - **Classes:** `.label`, `.interactive`, `.skip-link`, `.shell-corner` with the modifiers `--top-left`, `--top-right`, `--bottom-left` and `--bottom-right`, and `.shell-main`.
  - **Theme scoping:** any element with `data-theme="light"` or `"dark"` gets that appearance's colors. The catalog relies on this.

- [ ] **Step 1: Write the failing test**

Create `backend/pages/tests_tokens.py`:

```python
"""Design tokens (spec 3.2): the colors are exactly the spec's, for Paper and for Ink, and live only in tokens.css."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

CSS = Path(settings.BASE_DIR) / "static" / "css"
PAPER = {
    "--paper": "#ebe6de", "--ink": "#2c2925", "--muted": "#5d574f",
    "--hair": "rgba(44, 41, 37, .2)", "--fill": "rgba(44, 41, 37, .12)", "--box": "rgba(240, 236, 229, .35)",
}
INK = {
    "--paper": "#1b1917", "--ink": "#e6e0d6", "--muted": "#a0998e",
    "--hair": "rgba(230, 224, 214, .2)", "--fill": "rgba(230, 224, 214, .14)", "--box": "rgba(30, 28, 26, .35)",
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

    def test_label_size_and_corners_follow_the_spec(self):
        self.assertIn("--label-size: clamp(10px, 1.05vw, 13px);", self.css)
        self.assertIn("--label-tracking: .2em;", self.css)
        self.assertIn("--edge-block: 5.5%;", self.css)
        self.assertIn("--edge-inline: 5%;", self.css)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_tokens -v 2`
Expected: ERROR in `setUp`, `FileNotFoundError: ... tokens.css`.

- [ ] **Step 3: Write `tokens.css`**

Create `backend/static/css/tokens.css`:

```css
/* Design tokens for pages on the new shell: every color, size and space lives here (spec 3.2).
   Paper is the default appearance. Ink applies to any element with data-theme="dark", and to
   data-theme="system" (or no data-theme) when the system is dark. The stored values stay
   light | dark | system because pages not yet on the shell read the same storage. */

:root,
[data-theme="light"],
[data-theme="system"] {
  --paper: #ebe6de;
  --ink: #2c2925;
  --muted: #5d574f;
  --hair: rgba(44, 41, 37, .2);
  --fill: rgba(44, 41, 37, .12);
  --box: rgba(240, 236, 229, .35);
  color-scheme: light;
}

[data-theme="dark"] {
  --paper: #1b1917;
  --ink: #e6e0d6;
  --muted: #a0998e;
  --hair: rgba(230, 224, 214, .2);
  --fill: rgba(230, 224, 214, .14);
  --box: rgba(30, 28, 26, .35);
  color-scheme: dark;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]):not([data-theme="dark"]),
  [data-theme="system"] {
    --paper: #1b1917;
    --ink: #e6e0d6;
    --muted: #a0998e;
    --hair: rgba(230, 224, 214, .2);
    --fill: rgba(230, 224, 214, .14);
    --box: rgba(30, 28, 26, .35);
    color-scheme: dark;
  }
}

:root {
  --font-display: "Cormorant Garamond", "Cormorant Garamond Fallback", Georgia, serif;
  --font-reading: "Newsreader", "Newsreader Fallback", Georgia, serif;
  --font-mono: "IBM Plex Mono", "IBM Plex Mono Fallback", Menlo, Consolas, monospace;
  --label-size: clamp(10px, 1.05vw, 13px);
  --label-tracking: .2em;
  --reading-size: 18.5px;
  --reading-leading: 1.62;
  --measure: 65ch;
  --edge-block: 5.5%;
  --edge-inline: 5%;
  --space-1: .25rem;
  --space-2: .5rem;
  --space-3: 1rem;
  --space-4: 1.5rem;
  --space-5: 2.5rem;
  --space-6: 4rem;
  --fill-fade: .15s;
}

@media (max-width: 40rem) {
  :root {
    --reading-size: 17.5px;
  }
}
```

- [ ] **Step 4: Write `base.css`**

Create `backend/static/css/base.css`:

```css
/* Base styles for pages on the new shell: reset, type, the corners and the still grain.
   Colors come only from tokens.css. */

*, *::before, *::after {
  box-sizing: border-box;
}

html {
  background-color: var(--paper);
  background-image: url("../img/grain.png");
  color: var(--ink);
  -webkit-text-size-adjust: 100%;
}

body {
  margin: 0;
  min-height: 100vh;
  font-family: var(--font-reading);
  font-size: var(--reading-size);
  line-height: var(--reading-leading);
  font-optical-sizing: auto;
}

img, svg {
  display: block;
  max-width: 100%;
}

a {
  color: inherit;
}

.label {
  font-family: var(--font-mono);
  font-size: var(--label-size);
  letter-spacing: var(--label-tracking);
  line-height: 1.4;
  text-transform: uppercase;
  text-decoration: none;
  color: var(--muted);
}

button.label {
  background: none;
  border: 0;
  cursor: pointer;
}

/* Every interactive element answers the cursor (spec 3.4). Until the cloth arrives the answer
   is the flat fill; the ink wash replaces it when Motion is on. */
.interactive {
  position: relative;
  isolation: isolate;
  transition: color var(--fill-fade) ease;
}

.label.interactive {
  padding: .6em .8em;
  margin: -.6em -.8em;
}

.interactive::before {
  content: "";
  position: absolute;
  inset: .3em .45em;
  z-index: -1;
  background: transparent;
  transition: background-color var(--fill-fade) ease;
}

.interactive:hover,
.interactive:focus-visible,
.interactive[aria-current="page"] {
  color: var(--ink);
}

.interactive:hover::before,
.interactive:focus-visible::before,
.interactive[aria-current="page"]::before {
  background: var(--fill);
}

:focus-visible {
  outline: 1px solid var(--muted);
  outline-offset: 3px;
}

.skip-link {
  position: fixed;
  top: var(--space-3);
  left: var(--space-3);
  z-index: 10;
  padding: var(--space-2) var(--space-3);
  background: var(--paper);
  transform: translateY(-200%);
}

.skip-link:focus-visible {
  transform: none;
}

.shell-corner {
  position: fixed;
  z-index: 3;
}

.shell-corner--top-left { top: var(--edge-block); left: var(--edge-inline); }
.shell-corner--top-right { top: var(--edge-block); right: var(--edge-inline); }
.shell-corner--bottom-left { bottom: var(--edge-block); left: var(--edge-inline); }
.shell-corner--bottom-right { bottom: var(--edge-block); right: var(--edge-inline); }

.shell-main {
  min-height: 100vh;
  padding: calc(var(--edge-block) + 3.5rem) var(--edge-inline);
}

/* Motion off (the switch, or a system asking for reduced motion with nothing saved) stops every
   transition and animation. Without JavaScript the boot script never sets data-motion, so the
   system setting decides. */
[data-motion="off"] *,
[data-motion="off"] *::before,
[data-motion="off"] *::after {
  transition: none !important;
  animation: none !important;
}

@media (prefers-reduced-motion: reduce) {
  :root:not([data-motion]) *,
  :root:not([data-motion]) *::before,
  :root:not([data-motion]) *::after {
    transition: none !important;
    animation: none !important;
  }
}
```

- [ ] **Step 5: Run the test and watch it pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_tokens -v 2`
Expected: 6 tests, OK.

- [ ] **Step 6: Commit**

```bash
git add backend/static/css/tokens.css backend/static/css/base.css backend/pages/tests_tokens.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Add the shell's design tokens and base styles

Paper and Ink exactly as the spec's table, scoped by data-theme so any
element can show either appearance; type, spacing and corner tokens;
base styles for labels, the flat-fill hover, focus and the corners.

EOF
```

---

### Task 4: The mark, favicons and still grain

**Files:**
- Create: `tools/make_images.py`, `backend/pages/tests_images.py`
- Generated by the tool and committed: `backend/static/img/mark.svg`, `favicon.svg`, `favicon-16.png`, `favicon-32.png`, `apple-touch-icon.png`, `grain.png`

**Interfaces:**
- Consumes: nothing.
- Produces: the static images above, at `img/<name>`.

- [ ] **Step 1: Write the failing test**

Create `backend/pages/tests_images.py`:

```python
"""The "Let go" mark, favicons and still grain (spec 3.3, 3.6), read back from the files."""
import struct
import zlib
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

IMG = Path(settings.BASE_DIR) / "static" / "img"
MARK_ROWS = ("####", "###.", "##.#", "#.#.")
MARK_PATH = "M0 0H560V140H420V280H280V420H140V560H0Z M420 280H560V420H420Z M280 420H420V560H280Z"
PAPER, INK = (0xEB, 0xE6, 0xDE, 255), (0x2C, 0x29, 0x25, 255)


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

    def test_grain_is_a_subtle_neutral_tile(self):
        width, height, pixel = read_png(IMG / "grain.png")
        self.assertEqual((width, height), (128, 128))
        alphas = set()
        for y in range(height):
            for x in range(width):
                red, green, blue, alpha = pixel(x, y)
                self.assertEqual((red, green, blue), (128, 128, 128))
                alphas.add(alpha)
        self.assertLessEqual(max(alphas), 24)
        self.assertGreater(len(alphas), 2)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_images -v 2`
Expected: ERROR, `FileNotFoundError: ... mark.svg`, and the same for each image.

- [ ] **Step 3: Write the image tool**

Create `tools/make_images.py`:

```python
#!/usr/bin/env python3
"""Draw the mark, the favicons and the still grain into backend/static/img (spec 3.3, 3.6).

Standard library only. Rerun it after changing MARK_ROWS. From the repo root:
  python3 tools/make_images.py
"""
import random
import struct
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IMG = REPO / "backend" / "static" / "img"
MARK_ROWS = ("####", "###.", "##.#", "#.#.")  # "Let go": # is a filled cell, on a 4x4 grid
MARK_PATH = "M0 0H560V140H420V280H280V420H140V560H0Z M420 280H560V420H420Z M280 420H420V560H280Z"
PAPER = (0xEB, 0xE6, 0xDE)
INK = (0x2C, 0x29, 0x25)


def png(width, height, pixel):
    """An 8-bit RGBA PNG; pixel(x, y) returns (r, g, b, a). Every row uses filter 0."""
    rows = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(width)) for y in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


def mark_tile(size, padding):
    """The mark in ink on a paper square, with `padding` px of paper around it."""
    cell = (size - 2 * padding) // 4

    def pixel(x, y):
        column, row = (x - padding) // cell, (y - padding) // cell
        if 0 <= column < 4 and 0 <= row < 4 and MARK_ROWS[row][column] == "#":
            return (*INK, 255)
        return (*PAPER, 255)

    return pixel


def grain(size=128, seed=7):
    """Neutral grey speckles at very low opacity: they darken Paper and lighten Ink alike."""
    rng = random.Random(seed)
    alphas = [rng.randint(0, 18) for _ in range(size * size)]
    return lambda x, y: (128, 128, 128, alphas[y * size + x])


def main():
    IMG.mkdir(parents=True, exist_ok=True)
    svg_open = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 560 560" shape-rendering="crispEdges">'
    (IMG / "mark.svg").write_text(f'{svg_open}<path fill="#2c2925" d="{MARK_PATH}"/></svg>\n', encoding="utf-8")
    (IMG / "favicon.svg").write_text(
        f"{svg_open}<style>path{{fill:#2c2925}}@media (prefers-color-scheme:dark){{path{{fill:#e6e0d6}}}}</style>"
        f'<path d="{MARK_PATH}"/></svg>\n',
        encoding="utf-8",
    )
    for name, size, padding in (("favicon-16.png", 16, 0), ("favicon-32.png", 32, 0), ("apple-touch-icon.png", 180, 30)):
        (IMG / name).write_bytes(png(size, size, mark_tile(size, padding)))
    (IMG / "grain.png").write_bytes(png(128, 128, grain()))
    print(f"Wrote six images to {IMG}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tool and the test**

Run: `python3 tools/make_images.py`
Expected: `Wrote six images to .../backend/static/img`. Open `backend/static/img/apple-touch-icon.png` and look at it: the "Let go" glyph in charcoal on warm paper, solid in the top left and breaking into a checkerboard toward the lower right.

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_images -v 2`
Expected: 4 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add tools/make_images.py backend/pages/tests_images.py backend/static/img
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Draw the Let go mark, favicons and still grain

tools/make_images.py (standard library only) writes the mark, an SVG
favicon that follows the system appearance, 16/32/180 px PNGs on paper,
and the neutral grain tile the still page uses.

EOF
```

---

### Task 5: The page registry

**Files:**
- Create: `backend/pages/registry.py`, `backend/pages/tests_registry.py`
- Modify: `backend/drop_core/tests_fix_docs.py`. Add `"backend/pages/registry.py",` to `PLAN_COPY_GLOBS`, directly after the `"backend/drop_core/views.py",` entry, so the price facts in the registry are checked against `plans.py`.

**Interfaces:**
- Consumes: the existing URL names `landing`, `about`, `plans`, `trust`, `docs-index`, `blog-index`, `terms`, `privacy`, `report-abuse`, `login`, `app` and `llms-txt`.
- Produces:
  - `registry.SITE_ORIGIN = "https://veilfile.com"`.
  - `Page(url_name, title, description, renders_metadata=True, in_sitemap=True)` and `registry.PAGES`, a tuple of `Page`.
  - `MenuLink(label, url_name, audience="everyone")`, where `audience` is `"everyone"`, `"signed-in"` or `"signed-out"`, and `registry.MENU`, a tuple of three tuples of `MenuLink`. `MENU[0]` holds the chapters.
  - `registry.page(url_name) -> Page`.
  - `registry.canonical_url(url_name) -> str`.
  - `registry.menu_groups(*, signed_in: bool, current: str | None = None) -> list[list[dict]]`, where each dict is `{"label": str, "href": str, "current": bool}`.

- [ ] **Step 1: Write the failing tests**

Create `backend/pages/tests_registry.py`:

```python
"""The registry of public pages (spec 6.5): one place for titles, descriptions and the menu."""
from html.parser import HTMLParser

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from pages import registry


class _Head(HTMLParser):
    """Reads <title>, the meta description and the canonical link (unescaping entities)."""

    def __init__(self):
        super().__init__()
        self.title, self.description, self.canonical, self._in_title = "", None, None, False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta" and attributes.get("name") == "description":
            self.description = attributes.get("content")
        elif tag == "link" and attributes.get("rel") == "canonical":
            self.canonical = attributes.get("href")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


class RegistryTests(SimpleTestCase):
    def test_every_page_reverses_once(self):
        names = [page.url_name for page in registry.PAGES]
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            with self.subTest(page=name):
                reverse(name)

    def test_titles_and_descriptions_fit_search_results(self):
        for page in registry.PAGES:
            with self.subTest(page=page.url_name):
                self.assertLessEqual(len(page.title), 60)
                self.assertGreaterEqual(len(page.description), 70)
                self.assertLessEqual(len(page.description), 160)

    def test_canonical_urls_use_the_public_origin(self):
        self.assertEqual(registry.canonical_url("about"), "https://veilfile.com/about/")
        self.assertEqual(registry.canonical_url("landing"), "https://veilfile.com/")

    def test_menu_links_reverse_and_name_a_known_audience(self):
        for group in registry.MENU:
            for link in group:
                with self.subTest(link=link.label):
                    reverse(link.url_name)
                    self.assertIn(link.audience, {"everyone", "signed-in", "signed-out"})

    def test_signed_out_menu(self):
        labels = [[item["label"] for item in group] for group in registry.menu_groups(signed_in=False)]
        self.assertEqual(labels, [
            ["About", "Plans"],
            ["Docs", "Journal", "Sign in"],
            ["Terms", "Privacy", "Report abuse", "llms.txt"],
        ])

    def test_signed_in_menu_offers_the_dashboard(self):
        group = registry.menu_groups(signed_in=True)[1]
        self.assertEqual([item["label"] for item in group], ["Docs", "Journal", "Dashboard"])
        self.assertEqual(group[2]["href"], "/app/")

    def test_the_current_page_is_marked(self):
        groups = registry.menu_groups(signed_in=False, current="plans")
        marked = [item["label"] for group in groups for item in group if item["current"]]
        self.assertEqual(marked, ["Plans"])

    def test_page_lookup(self):
        self.assertEqual(registry.page("plans").title, "Plans | Veilfile")
        with self.assertRaises(KeyError):
            registry.page("nope")


class RegistryMatchesRenderedPagesTests(TestCase):
    def test_each_page_renders_its_registry_metadata(self):
        for page in registry.PAGES:
            if not page.renders_metadata:
                continue
            with self.subTest(page=page.url_name):
                response = self.client.get(reverse(page.url_name))
                self.assertEqual(response.status_code, 200)
                head = _Head()
                head.feed(response.content.decode())
                self.assertEqual(head.title.strip(), page.title)
                self.assertEqual(head.description, page.description)
                self.assertEqual(head.canonical, registry.canonical_url(page.url_name))
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_registry -v 2`
Expected: ERROR, `ImportError: cannot import name 'registry' from 'pages'`.

- [ ] **Step 3: Write the registry**

Create `backend/pages/registry.py`:

```python
"""Every public page, and the menu, in one place (spec 6.5).

The menu, sitemap.xml and the tests read from here; from PR 2 each page's <head> does too.
To add a public page: add a Page below with a title of 60 characters or fewer and a
description of 70 to 160 characters, then run the tests. Until a page moves onto the
shell, its title and description must equal what its template renders (a test compares them).
"""
from dataclasses import dataclass

from django.urls import reverse

SITE_ORIGIN = "https://veilfile.com"


@dataclass(frozen=True)
class Page:
    url_name: str
    title: str
    description: str
    renders_metadata: bool = True  # False only while a page's <head> lives outside Django templates
    in_sitemap: bool = True


@dataclass(frozen=True)
class MenuLink:
    label: str
    url_name: str
    audience: str = "everyone"  # "everyone", "signed-in" or "signed-out"


PAGES = (
    # The landing page's <head> is in frontend/index.html until the threshold moves here (PR 2).
    Page("landing", "Veilfile | Private ephemeral artifact hosting for AI agents",
         "Private file hosting for AI agents. Upload screenshots, logs, and bundles via REST or MCP. "
         "Unguessable links that expire on schedule. Free to $12/mo.",
         renders_metadata=False),
    Page("about", "About | Veilfile",
         "Veilfile is private, expiring artifact hosting for AI agents: one upload, one unguessable link, "
         "dead on schedule. Built and run by a one-person company."),
    Page("plans", "Plans | Veilfile",
         "Veilfile plans: Free ($0), Team ($4/month), Scale ($12/month). Tax included. "
         "The price you see is the price you pay."),
    Page("trust", "Why not a gist? | Veilfile",
         "A gist is public by default and lives forever. Veilfile is the boring, correct option: "
         "one upload, one unguessable link, dead on schedule."),
    Page("docs-index", "Veilfile docs | REST API, MCP, and agent setup",
         "Start here: five-minute quickstart, agent key setup, how uploads and expiry work, "
         "and the full REST and MCP API reference."),
    Page("blog-index", "Veilfile blog | launches and build notes",
         "Notes from building Veilfile: launches, changes, pricing, and the reasoning behind each decision. "
         "Updated twice a month. RSS available."),
    Page("terms", "Terms of service | Veilfile",
         "Veilfile's terms: acceptable use, accounts and API keys, data retention, billing and refunds, "
         "liability limits, and how to contact us."),
    Page("privacy", "Privacy policy | Veilfile",
         "What Veilfile collects, why, how long files and metadata are kept, subprocessors, "
         "and how to request access or deletion."),
    Page("report-abuse", "Report abuse | Veilfile",
         "Report a file on Veilfile that violates our terms. Email abuse@veilfile.com with the link "
         "and a description. CSAM reports go to NCMEC first."),
)

# The menu's three groups (spec 4.4). MENU[0] is the chapters; PR 2 adds How it works, Begin and Promises.
MENU = (
    (MenuLink("About", "about"), MenuLink("Plans", "plans")),
    (MenuLink("Docs", "docs-index"), MenuLink("Journal", "blog-index"),
     MenuLink("Sign in", "login", audience="signed-out"), MenuLink("Dashboard", "app", audience="signed-in")),
    (MenuLink("Terms", "terms"), MenuLink("Privacy", "privacy"),
     MenuLink("Report abuse", "report-abuse"), MenuLink("llms.txt", "llms-txt")),
)


def page(url_name):
    for candidate in PAGES:
        if candidate.url_name == url_name:
            return candidate
    raise KeyError(f"no public page named {url_name!r} in pages/registry.py")


def canonical_url(url_name):
    return SITE_ORIGIN + reverse(url_name)


def menu_groups(*, signed_in, current=None):
    """The menu as the menu component takes it: groups of {"label", "href", "current"}."""
    hidden = "signed-out" if signed_in else "signed-in"
    return [
        [
            {"label": link.label, "href": reverse(link.url_name), "current": link.url_name == current}
            for link in group
            if link.audience != hidden
        ]
        for group in MENU
    ]
```

In `backend/drop_core/tests_fix_docs.py`, add `"backend/pages/registry.py",` to `PLAN_COPY_GLOBS`, directly after `"backend/drop_core/views.py",`.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_registry drop_core.tests_fix_docs -v 2`
Expected: OK. If `test_each_page_renders_its_registry_metadata` fails, the failure shows the two strings. Copy the template's text into the registry exactly; do not edit the old template. If `drop_core.tests_fix_docs` fails on `registry.py`, a price or cap in a description disagrees with `plans.py`. Make the registry text match what the page's template says.

- [ ] **Step 5: Commit**

```bash
git add backend/pages/registry.py backend/pages/tests_registry.py backend/drop_core/tests_fix_docs.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Add the registry of public pages and the menu

One place for every public page's title and description and the menu's
three groups; a test keeps the registry equal to what each page renders,
and the plan-copy test now checks the registry's prices too.

EOF
```

---

### Task 6: sitemap.xml from the registry

**Files:**
- Create: `backend/pages/views.py`, `backend/pages/tests_sitemap.py`
- Modify: `backend/templates/sitemap.xml` (replace it completely), `backend/config/urls.py` (the `sitemap.xml` route), `backend/drop_core/views.py` (delete the `sitemap` function at about lines 1252-1269)

**Interfaces:**
- Consumes: `registry.PAGES`, `registry.canonical_url`, `registry.SITE_ORIGIN`, `blog.loader.load_posts()` (items with `.slug` and `.date`), and `docs.loader.load_pages()` (items with `.slug`).
- Produces: `pages.views.sitemap(request)`, routed at `/sitemap.xml` with the name `sitemap`.

- [ ] **Step 1: Write the failing test**

Create `backend/pages/tests_sitemap.py`:

```python
"""sitemap.xml (spec 6.5): built from the registry, the Docs pages and the published Journal posts."""
from django.test import TestCase

from blog import loader as blog_loader
from docs import loader as docs_loader
from pages import registry
from pages.views import sitemap


class SitemapTests(TestCase):
    def setUp(self):
        self.response = self.client.get("/sitemap.xml")
        self.xml = self.response.content.decode()

    def test_is_served_by_the_pages_app_as_xml(self):
        self.assertEqual(self.response.status_code, 200)
        self.assertEqual(self.response["Content-Type"], "application/xml")
        self.assertIs(self.response.resolver_match.func, sitemap)

    def test_lists_every_registry_page(self):
        for page in registry.PAGES:
            if page.in_sitemap:
                with self.subTest(page=page.url_name):
                    self.assertIn(f"<loc>{registry.canonical_url(page.url_name)}</loc>", self.xml)

    def test_lists_every_docs_page(self):
        for doc in docs_loader.load_pages():
            if doc.slug != "index":
                with self.subTest(doc=doc.slug):
                    self.assertIn(f"<loc>https://veilfile.com/docs/{doc.slug}/</loc>", self.xml)

    def test_lists_published_posts_with_their_dates(self):
        posts = blog_loader.load_posts()
        self.assertTrue(posts)
        for post in posts:
            with self.subTest(post=post.slug):
                self.assertIn(
                    f"<loc>https://veilfile.com/blog/{post.slug}/</loc><lastmod>{post.date:%Y-%m-%d}</lastmod>",
                    self.xml,
                )

    def test_each_address_appears_once(self):
        locations = [line for line in self.xml.splitlines() if "<loc>" in line]
        self.assertEqual(len(locations), len(set(locations)))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_sitemap -v 2`
Expected: ERROR, `ModuleNotFoundError: No module named 'pages.views'`.

- [ ] **Step 3: Write the view, the template and the route**

Create `backend/pages/views.py`:

```python
"""Views for public pages on the new shell."""
from django.shortcuts import render
from django.urls import reverse

from blog import loader as blog_loader
from docs import loader as docs_loader
from pages import registry


def sitemap(request):
    """sitemap.xml: every registry page, every Docs page and every published Journal post.

    Leaves out /app/*, /a/<token>, /llms.txt and the API, as robots.txt does.
    """
    entries = [
        {"loc": registry.canonical_url(page.url_name), "lastmod": None}
        for page in registry.PAGES
        if page.in_sitemap
    ]
    entries += [
        {"loc": registry.SITE_ORIGIN + reverse("docs-page", args=[doc.slug]), "lastmod": None}
        for doc in docs_loader.load_pages()
        if doc.slug != "index"
    ]
    entries += [
        {"loc": registry.SITE_ORIGIN + reverse("blog-post", args=[post.slug]), "lastmod": post.date.isoformat()}
        for post in blog_loader.load_posts()
    ]
    return render(request, "sitemap.xml", {"entries": entries}, content_type="application/xml")
```

Replace the whole of `backend/templates/sitemap.xml` with:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{% for entry in entries %}  <url><loc>{{ entry.loc }}</loc>{% if entry.lastmod %}<lastmod>{{ entry.lastmod }}</lastmod>{% endif %}</url>
{% endfor %}</urlset>
```

In `backend/config/urls.py`, add `from pages import views as pages_views` beside the other view imports. Then change

```python
    path("sitemap.xml", core_views.sitemap, name="sitemap"),
```

to

```python
    path("sitemap.xml", pages_views.sitemap, name="sitemap"),
```

In `backend/drop_core/views.py`, delete the whole `def sitemap(request):` function (about lines 1252-1269). Then run `grep -rn "core_views.sitemap\|views.sitemap" backend --include='*.py'` from the repo root. Expected: only `pages_views.sitemap` in `config/urls.py`. If `load_posts` is now unused in `drop_core/views.py`, remove its import too.

- [ ] **Step 4: Run the new and the existing sitemap tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_sitemap drop_core.tests_site_pages drop_core.tests_fix_web -v 2`
Expected: OK. The existing tests check that `/plans/`, `/trust/` and `/about/` are listed, and that the sitemap survives a broken post.

- [ ] **Step 5: Commit**

```bash
git add backend/pages/views.py backend/pages/tests_sitemap.py backend/templates/sitemap.xml backend/config/urls.py backend/drop_core/views.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Build sitemap.xml from the registry and the content loaders

Docs pages now come from the docs loader instead of a hand-kept list,
so a new Docs page is listed without editing the sitemap. Hand-typed
lastmod dates are gone; Journal posts keep their publish dates.

EOF
```

---

### Task 7: The shell template and its components

**Files:**
- Create: `backend/templates/shell.html`
- Create: `backend/templates/components/mark.html`, `lockup.html`, `menu.html`, `controls.html`
- Create: `backend/static/css/components/mark.css`, `lockup.css`, `menu.css`, `controls.css`
- Create: `backend/pages/templatetags/__init__.py` (empty), `backend/pages/templatetags/shell.py`
- Create: `backend/pages/tests_shell.py`

**Interfaces:**
- Consumes: `registry.menu_groups`, the CSS from Task 3, the fonts from Task 2, and the images from Task 4.
- Produces:
  - **`shell.html` blocks:** `title`, `meta`, `styles`, `body_class` and `main`.
  - **`{% load shell %}{% shell_menu as groups %}`.**
  - **Component inputs:** `mark` (none), `lockup` (`home_url`), `menu` (`groups`, `list_id`) and `controls` (none).
  - **DOM hooks for `shell.js`:** `[data-menu]`, `[data-menu-toggle]`, `[data-menu-list]` and the `.is-open` class; `[data-appearance-toggle]`; `[data-motion-toggle]`; and on `<html>`, `data-theme`, `data-motion` and the class `js`.

- [ ] **Step 1: Write the failing tests**

Create `backend/pages/tests_shell.py`:

```python
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
            'if (theme !== "light" && theme !== "dark") theme = "system";',
            "(prefers-reduced-motion: reduce)",
            'root.setAttribute("data-theme", theme);',
            'root.setAttribute("data-motion", motion);',
            'root.classList.add("js");',
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

    def test_preloads_the_display_and_reading_fonts(self):
        for path in ("fonts/cormorant-garamond/italic-300-latin.woff2", "fonts/newsreader/normal-400-500-latin.woff2"):
            with self.subTest(path=path):
                self.assertIn(f'<link rel="preload" href="/static/{path}" as="font" type="font/woff2" crossorigin>', self.html)

    def test_declares_the_favicons(self):
        for snippet in ('href="/static/img/favicon.svg" type="image/svg+xml"', 'href="/static/img/favicon-32.png"',
                        'href="/static/img/favicon-16.png"', 'rel="apple-touch-icon" href="/static/img/apple-touch-icon.png"'):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, self.html)

    def test_loads_shell_js_as_a_module(self):
        self.assertIn('<script type="module" src="/static/js/shell.js"></script>', self.html)


class ShellBodyTests(SimpleTestCase):
    def setUp(self):
        self.html = render_shell()

    def test_skip_link_comes_before_the_corners_and_targets_main(self):
        self.assertLess(self.html.index('class="skip-link label"'), self.html.index("<header>"))
        self.assertIn('<main id="main" class="shell-main">', self.html)

    def test_lockup_links_home_with_the_mark(self):
        self.assertIn('<a class="lockup label interactive" href="/">', self.html)
        self.assertIn(f'd="{MARK_PATH}"', self.html)

    def test_menu_lists_the_signed_out_groups_in_order(self):
        nav = self.html[self.html.index("<nav"):self.html.index("</nav>")]
        labels = re.findall(r'<a class="label interactive" href="[^"]+"[^>]*>([^<]+)</a>', nav)
        self.assertEqual(labels, ["About", "Plans", "Docs", "Journal", "Sign in", "Terms", "Privacy", "Report abuse", "llms.txt"])
        self.assertIn('aria-controls="shell-menu"', nav)
        self.assertIn('id="shell-menu"', nav)

    def test_signed_in_visitors_see_the_dashboard(self):
        html = render_shell(user=get_user_model()(username="reader"))
        self.assertIn('href="/app/">Dashboard</a>', html)
        self.assertNotIn(">Sign in</a>", html)

    def test_bottom_corners(self):
        self.assertIn('<span class="label">Private · Ephemeral</span>', self.html)
        self.assertIn("data-appearance-toggle", self.html)
        # Motion's accessible name stays "Motion"; aria-pressed carries the state, the visible word is hidden from it.
        self.assertIn('aria-pressed="true" data-motion-toggle>Motion <span aria-hidden="true" data-motion-state>· on</span>', self.html)


class NoJavaScriptTests(SimpleTestCase):
    """Without JavaScript the menu's links stay visible and the switches that need it stay hidden."""

    def test_css_hides_only_behind_the_js_class(self):
        menu = (STATIC / "css/components/menu.css").read_text(encoding="utf-8")
        controls = (STATIC / "css/components/controls.css").read_text(encoding="utf-8")
        self.assertIn(".js .menu__list:not(.is-open)", menu)
        self.assertIn(".js .menu__toggle", menu)
        self.assertRegex(controls, r"\.controls\s*\{[^}]*display:\s*none")
        self.assertIn(".js .controls", controls)


class MotionCssTests(SimpleTestCase):
    def test_motion_off_stops_animations(self):
        base = (STATIC / "css/base.css").read_text(encoding="utf-8")
        menu = (STATIC / "css/components/menu.css").read_text(encoding="utf-8")
        self.assertIn('[data-motion="off"] *,', base)
        self.assertIn(':root:not([data-motion="off"]) .menu__list.is-open li', menu)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_shell -v 2`
Expected: ERROR, `django.template.exceptions.TemplateDoesNotExist: shell.html`.

- [ ] **Step 3: Write the template tag**

Create `backend/pages/templatetags/__init__.py` (empty) and `backend/pages/templatetags/shell.py`:

```python
"""Template tags for pages on the new shell."""
from django import template

from pages import registry

register = template.Library()


@register.simple_tag(takes_context=True)
def shell_menu(context):
    """The menu's groups for this request: Sign in or Dashboard, and the current page marked."""
    request = context.get("request")
    user = getattr(request, "user", None)
    match = getattr(request, "resolver_match", None)
    return registry.menu_groups(
        signed_in=bool(user is not None and user.is_authenticated),
        current=getattr(match, "url_name", None),
    )
```

- [ ] **Step 4: Write the components and their styles**

Create `backend/templates/components/mark.html`:

```django
{# Component: mark. The "Let go" mark (spec 3.6), drawn in the current text color. #}
{# Inputs: none. Size it with CSS; it is 1em square by default. #}
<svg class="mark" viewBox="0 0 560 560" aria-hidden="true" focusable="false" shape-rendering="crispEdges"><path fill="currentColor" d="M0 0H560V140H420V280H280V420H140V560H0Z M420 280H560V420H420Z M280 420H420V560H280Z"/></svg>
```

Create `backend/templates/components/lockup.html`:

```django
{# Component: lockup. The mark and VEILFILE, linking to the threshold (spec 3.6). #}
{# Inputs: home_url, the threshold's address. #}
<a class="lockup label interactive" href="{{ home_url }}">{% include "components/mark.html" only %}<span>Veilfile</span></a>
```

Create `backend/templates/components/menu.html`:

```django
{# Component: menu. MENU and the column of links it unfolds, in groups (spec 4.4). #}
{# Inputs: groups, a list of groups, each a list of items with label, href and current; list_id, a unique id for the column. #}
<nav class="menu" aria-label="Site" data-menu>
<button class="menu__toggle label interactive" type="button" aria-expanded="false" aria-controls="{{ list_id }}" data-menu-toggle>Menu</button>
<div class="menu__list" id="{{ list_id }}" data-menu-list>
{% for group in groups %}<ul class="menu__group">
{% for item in group %}<li><a class="label interactive" href="{{ item.href }}"{% if item.current %} aria-current="page"{% endif %}>{{ item.label }}</a></li>
{% endfor %}</ul>
{% endfor %}</div>
</nav>
```

Create `backend/templates/components/controls.html`:

```django
{# Component: controls. The appearance switch (Ink, Auto, Paper) and Motion, whose name stays Motion while aria-pressed carries on or off; shell.js keeps them in step with the saved choice. #}
{# Inputs: none. #}
<div class="controls" role="group" aria-label="Display">
<button class="label interactive" type="button" aria-label="Appearance: Auto" data-appearance-toggle>Auto</button>
<button class="label interactive" type="button" aria-pressed="true" data-motion-toggle>Motion <span aria-hidden="true" data-motion-state>· on</span></button>
</div>
```

Create `backend/static/css/components/mark.css`:

```css
/* The "Let go" mark: 1em square, in the current text color. */
.mark {
  width: 1em;
  height: 1em;
  flex: none;
}
```

Create `backend/static/css/components/lockup.css`:

```css
/* The lockup: the mark at cap height, a 0.75em gap, then VEILFILE (spec 3.6). */
.lockup {
  display: inline-flex;
  align-items: center;
  gap: .75em;
}

.lockup .mark {
  width: .95em;
  height: .95em;
}
```

Create `backend/static/css/components/menu.css`:

```css
/* The corner menu (spec 4.4). Without JavaScript the column is always shown and MENU is hidden;
   with it (html.js), MENU opens and closes the column. On phones the open column is a full sheet. */
.menu {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.menu__toggle {
  display: none;
}

.js .menu__toggle {
  display: inline-block;
}

.menu__list {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 1.1em;
  margin-top: 1.1em;
}

.js .menu__list:not(.is-open) {
  display: none;
}

.menu__group {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: .95em;
}

@keyframes menu-item-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

:root:not([data-motion="off"]) .menu__list.is-open li {
  animation: menu-item-in .5s ease both;
}

.menu__list.is-open .menu__group:nth-child(2) li { animation-delay: .08s; }
.menu__list.is-open .menu__group:nth-child(3) li { animation-delay: .16s; }

@media (max-width: 40rem) {
  .menu__toggle {
    position: relative;
    z-index: 5;
  }

  .menu__list.is-open {
    position: fixed;
    inset: 0;
    z-index: 4;
    margin: 0;
    padding: calc(var(--edge-block) + 3rem) var(--edge-inline) var(--edge-block);
    overflow-y: auto;
    gap: 1.6em;
    background: var(--paper);
  }
}
```

Create `backend/static/css/components/controls.css`:

```css
/* The display controls, bottom right (spec 4.1). They only work with JavaScript, so they show only with it. */
.controls {
  display: none;
  gap: 2.6em;
}

.js .controls {
  display: flex;
}
```

- [ ] **Step 5: Write `shell.html`**

Create `backend/templates/shell.html`:

```django
{% load static shell %}<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<script id="veilfile-shell-boot">
(function () {
  var root = document.documentElement, theme = null, motion = null;
  try { theme = localStorage.getItem("veilfile-theme"); motion = localStorage.getItem("veilfile-motion"); } catch (error) {}
  if (theme !== "light" && theme !== "dark") theme = "system";
  if (motion !== "on" && motion !== "off") motion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "off" : "on";
  root.setAttribute("data-theme", theme);
  root.setAttribute("data-motion", motion);
  root.classList.add("js");
})();
</script>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}Veilfile{% endblock %}</title>
{% block meta %}{% endblock %}
<link rel="icon" href="{% static 'img/favicon.svg' %}" type="image/svg+xml">
<link rel="icon" href="{% static 'img/favicon-32.png' %}" sizes="32x32" type="image/png">
<link rel="icon" href="{% static 'img/favicon-16.png' %}" sizes="16x16" type="image/png">
<link rel="apple-touch-icon" href="{% static 'img/apple-touch-icon.png' %}">
<link rel="preload" href="{% static 'fonts/cormorant-garamond/italic-300-latin.woff2' %}" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="{% static 'fonts/newsreader/normal-400-500-latin.woff2' %}" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{% static 'css/fonts.css' %}">
<link rel="stylesheet" href="{% static 'css/tokens.css' %}">
<link rel="stylesheet" href="{% static 'css/base.css' %}">
<link rel="stylesheet" href="{% static 'css/components/mark.css' %}">
<link rel="stylesheet" href="{% static 'css/components/lockup.css' %}">
<link rel="stylesheet" href="{% static 'css/components/menu.css' %}">
<link rel="stylesheet" href="{% static 'css/components/controls.css' %}">
{% block styles %}{% endblock %}
<script type="module" src="{% static 'js/shell.js' %}"></script>
</head>
<body class="{% block body_class %}{% endblock %}">
<a class="skip-link label" href="#main">Skip to content</a>
<header>
<div class="shell-corner shell-corner--top-left">{% url 'landing' as home_url %}{% include "components/lockup.html" with home_url=home_url only %}</div>
<div class="shell-corner shell-corner--top-right">{% shell_menu as menu_groups %}{% include "components/menu.html" with groups=menu_groups list_id="shell-menu" only %}</div>
</header>
<main id="main" class="shell-main">{% block main %}{% endblock %}</main>
<footer>
<div class="shell-corner shell-corner--bottom-left"><span class="label">Private · Ephemeral</span></div>
<div class="shell-corner shell-corner--bottom-right">{% include "components/controls.html" only %}</div>
</footer>
</body>
</html>
```

- [ ] **Step 6: Run the tests and watch them pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_shell -v 2`
Expected: 13 tests, OK. `test_loads_shell_js_as_a_module` passes on the tag alone; the file arrives in Task 10.

- [ ] **Step 7: Run the old chrome tests to prove nothing leaked**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_ambient drop_core.tests_menu drop_core.tests_site_pages -v 1`
Expected: OK. This includes the scan of every template for a visible "Trust" label.

- [ ] **Step 8: Commit**

```bash
git add backend/templates/shell.html backend/templates/components backend/static/css/components backend/pages/templatetags backend/pages/tests_shell.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Add the shell template and its first components

shell.html carries the head, an inline boot script that applies the
saved appearance and motion before first paint, and the four corners:
the lockup, the menu from the registry, PRIVATE · EPHEMERAL and the
display controls. Without JavaScript the menu's links stay visible.

EOF
```

---

### Task 8: The component catalog

**Files:**
- Create: `backend/pages/catalog.py`, `backend/templates/pages/catalog.html`, `backend/static/css/pages/catalog.css`, `backend/pages/tests_catalog.py`
- Modify: `backend/pages/views.py` (add `component_catalog`), `backend/config/urls.py` (add a route directly before `re_path(r'^app/.*$', …)`)

**Interfaces:**
- Consumes: the components and `shell.html` from Task 7, and `registry.menu_groups`.
- Produces:
  - `catalog.Entry(name, note, context)` and `catalog.entries() -> tuple[Entry, ...]`.
  - `pages.views.component_catalog(request)` at `/app/components/`, named `component-catalog`. It answers 404 to anyone who isn't staff.

- [ ] **Step 1: Write the failing tests**

Create `backend/pages/tests_catalog.py`:

```python
"""The component catalog (spec 4.8): staff-only, every component in Paper and Ink, Motion on and off."""
import re
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from pages import catalog

URL = "/app/components/"
COMPONENTS = Path(settings.BASE_DIR) / "templates" / "components"


class CatalogAccessTests(TestCase):
    def test_anonymous_visitors_get_not_found(self):
        self.assertEqual(self.client.get(URL).status_code, 404)

    def test_signed_in_visitors_who_are_not_staff_get_not_found(self):
        user = get_user_model().objects.create_user("reader", "reader@example.com", "a-test-only-password")
        self.client.force_login(user)
        self.assertEqual(self.client.get(URL).status_code, 404)


class CatalogPageTests(TestCase):
    def setUp(self):
        staff = get_user_model().objects.create_user("staff", "staff@example.com", "a-test-only-password", is_staff=True)
        self.client.force_login(staff)
        self.response = self.client.get(URL)
        self.html = self.response.content.decode()

    def test_staff_see_every_component_in_four_frames(self):
        self.assertEqual(self.response.status_code, 200)
        for entry in catalog.entries():
            with self.subTest(component=entry.name):
                self.assertIn(f'id="component-{entry.name}"', self.html)
        self.assertEqual(self.html.count('class="catalog__frame"'), 4 * len(catalog.entries()))
        for theme, motion in (("light", "on"), ("dark", "on"), ("light", "off"), ("dark", "off")):
            self.assertIn(f'data-theme="{theme}" data-motion="{motion}"', self.html)

    def test_is_kept_out_of_search(self):
        self.assertEqual(self.response["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn('<meta name="robots" content="noindex, nofollow">', self.html)

    def test_ids_are_unique(self):
        repeated = [name for name, count in Counter(re.findall(r'\sid="([^"]+)"', self.html)).items() if count > 1]
        self.assertEqual(repeated, [])

    def test_uses_the_shell(self):
        self.assertIn('<script id="veilfile-shell-boot">', self.html)


class CatalogCoverageTests(TestCase):
    def test_every_component_has_an_entry(self):
        on_disk = {path.stem for path in COMPONENTS.glob("*.html")}
        self.assertEqual(on_disk, {entry.name for entry in catalog.entries()},
                         "add an Entry to pages/catalog.py for each component in templates/components/")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_catalog -v 2`
Expected: ERROR, `ImportError: cannot import name 'catalog' from 'pages'`.

- [ ] **Step 3: Write the catalog, the view, the page and the route**

Create `backend/pages/catalog.py`:

```python
"""The component catalog's entries: every component in templates/components/, with sample inputs.

A test fails when a component has no entry here. Add one when you add a component.
"""
from dataclasses import dataclass, field

from pages import registry


@dataclass(frozen=True)
class Entry:
    name: str  # the file name in templates/components/, without .html
    note: str  # one line: what it is and where it appears
    context: dict = field(default_factory=dict)


def entries():
    return (
        Entry("mark", "The Let go mark on its own, in the current text color."),
        Entry("lockup", "Top left on every page: the mark and VEILFILE, linking to the threshold.", {"home_url": "/"}),
        Entry("menu", "Top right on every page: MENU and its three groups, here signed out with About current.",
              {"groups": registry.menu_groups(signed_in=False, current="about"), "list_id": "catalog-menu"}),
        Entry("controls", "Bottom right on every page: the appearance switch and Motion."),
    )
```

Add to `backend/pages/views.py`. Put the new imports beside the existing ones:

```python
from django.http import Http404
from django.template.loader import render_to_string

from pages import catalog

FRAMES = (
    ("light", "on", "Paper, motion on"),
    ("dark", "on", "Ink, motion on"),
    ("light", "off", "Paper, motion off"),
    ("dark", "off", "Ink, motion off"),
)


def component_catalog(request):
    """Every component in Paper and Ink, Motion on and off. Staff only; everyone else gets a 404."""
    if not (request.user.is_authenticated and request.user.is_staff):
        raise Http404("No such page.")
    components = []
    for entry in catalog.entries():
        frames = []
        for index, (theme, motion, caption) in enumerate(FRAMES):
            context = dict(entry.context)
            if "list_id" in context:
                context["list_id"] = f"{context['list_id']}-{index}"
            frames.append({"theme": theme, "motion": motion, "caption": caption,
                           "html": render_to_string(f"components/{entry.name}.html", context)})
        components.append({"entry": entry, "frames": frames})
    response = render(request, "pages/catalog.html", {"components": components})
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
```

Create `backend/templates/pages/catalog.html`:

```django
{% extends "shell.html" %}
{% load static %}
{% block title %}Components | Veilfile{% endblock %}
{% block meta %}<meta name="robots" content="noindex, nofollow">{% endblock %}
{% block styles %}<link rel="stylesheet" href="{% static 'css/pages/catalog.css' %}">{% endblock %}
{% block main %}
<h1 class="catalog__title">Components</h1>
<p class="catalog__lede">Every component in Paper and Ink, with Motion on and off. Staff only.</p>
{% for item in components %}
<section class="catalog__entry" aria-labelledby="component-{{ item.entry.name }}">
<h2 class="catalog__name label" id="component-{{ item.entry.name }}">{{ item.entry.name }}</h2>
<p class="catalog__note">{{ item.entry.note }}</p>
<div class="catalog__frames">
{% for frame in item.frames %}<figure class="catalog__frame" data-theme="{{ frame.theme }}" data-motion="{{ frame.motion }}">
<div class="catalog__sample">{{ frame.html }}</div>
<figcaption class="label">{{ frame.caption }}</figcaption>
</figure>
{% endfor %}</div>
</section>
{% endfor %}
{% endblock %}
```

Create `backend/static/css/pages/catalog.css`:

```css
/* The staff-only component catalog. */
.catalog__title {
  margin: 0 0 var(--space-3);
  font-family: var(--font-display);
  font-style: italic;
  font-weight: 300;
  font-size: clamp(2rem, 4vw, 3.2rem);
  line-height: 1.1;
}

.catalog__lede {
  max-width: var(--measure);
  margin: 0 0 var(--space-6);
  color: var(--muted);
}

.catalog__entry {
  margin-bottom: var(--space-6);
}

.catalog__name {
  display: block;
  margin: 0 0 var(--space-1);
}

.catalog__note {
  max-width: var(--measure);
  margin: 0 0 var(--space-4);
}

.catalog__frames {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 16rem), 1fr));
  gap: var(--space-3);
}

/* Frames sit above the fixed corners (z-index 3), so a corner label never lies on a frame of the other appearance. */
.catalog__frame {
  position: relative;
  z-index: 4;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
  margin: 0;
  padding: var(--space-5) var(--space-4) var(--space-3);
  border: 1px solid var(--hair);
  background: var(--paper);
  color: var(--ink);
}

.catalog__sample {
  display: flex;
  justify-content: center;
  align-items: flex-start;
  min-height: 4rem;
}

.catalog__sample > .mark {
  width: 4rem;
  height: 4rem;
}
```

In `backend/config/urls.py`, add this line directly before `re_path(r'^app/.*$', …)`:

```python
    path("app/components/", pages_views.component_catalog, name="component-catalog"),
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_catalog -v 2`
Expected: 7 tests, OK.

- [ ] **Step 5: Look at it**

Run `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py createsuperuser`, then `../venv/bin/python manage.py runserver` with the same environment. Sign in at http://127.0.0.1:8000/accounts/login/ and open http://127.0.0.1:8000/app/components/. Expected: warm paper with grain, VEILFILE and the mark top left, MENU top right (it does nothing until Task 10), PRIVATE · EPHEMERAL bottom left, and four frames per component, two of them dark.

- [ ] **Step 6: Commit**

```bash
git add backend/pages/catalog.py backend/pages/views.py backend/templates/pages backend/static/css/pages backend/pages/tests_catalog.py backend/config/urls.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Add the staff-only component catalog at /app/components/

Every component rendered four times, Paper and Ink with Motion on and
off, from sample inputs in pages/catalog.py. Everyone but staff gets a
404, and the page is noindex. A test fails if a component lacks an entry.

EOF
```

---

### Task 9: Browser checks and the `site` CI job

**Files:**
- Create: `site-tests/requirements.txt`, `site-tests/package.json`, `site-tests/package-lock.json` (generated by npm), `site-tests/make_staff_session.py`, `site-tests/run_site_checks.py`
- Modify: `.github/workflows/ci.yml` (a new `site` job), `.gitignore`

**Interfaces:**
- Consumes: the catalog page and the DOM hooks from Task 7.
- Produces: `python site-tests/run_site_checks.py --base-url URL --session-file PATH --out DIR`, which exits 0 when there are no problems and 1 otherwise, and prints one line per problem. Screenshots are written to `DIR/<page>-<width>-<appearance>-motion-<on|off>.png`, plus `DIR/<page>-no-javascript.png`. The `PAGES` tuple in `run_site_checks.py` is where later PRs add each page that moves onto the shell.

- [ ] **Step 1: Pin the tools**

Create `site-tests/requirements.txt`:

```
# Browser checks for the site (CI job `site`). Never installed in the app's image.
playwright==1.49.1
```

Create `site-tests/package.json`:

```json
{
  "name": "veilfile-site-tests",
  "private": true,
  "description": "axe-core for the site's browser checks (CI job `site`).",
  "dependencies": {
    "axe-core": "4.10.2"
  }
}
```

Run: `npm install --prefix site-tests`
Expected: `site-tests/package-lock.json` is created, and `site-tests/node_modules/axe-core/axe.min.js` exists.

Add to `.gitignore` under `# Node`:

```
site-tests/.venv/
site-tests/artifacts/
site-tests/session.txt
```

- [ ] **Step 2: Write the session helper**

Create `site-tests/make_staff_session.py`:

```python
"""Create a staff user and a signed-in session for the site checks; CI and local runs only.

Run from backend/ inside Django, with SITE_SESSION_FILE naming where to write the session key:
  SITE_SESSION_FILE=../site-tests/session.txt python manage.py shell \
      -c "exec(open('../site-tests/make_staff_session.py').read())"
"""
import os
from pathlib import Path

from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore

user, _ = get_user_model().objects.get_or_create(username="site-checks", defaults={"email": "site-checks@example.com"})
user.is_staff = True
user.set_unusable_password()
user.save()
session = SessionStore()
session[SESSION_KEY] = str(user.pk)
session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
session[HASH_SESSION_KEY] = user.get_session_auth_hash()
session.create()
Path(os.environ["SITE_SESSION_FILE"]).write_text(session.session_key, encoding="utf-8")
```

- [ ] **Step 3: Write the checks**

Create `site-tests/run_site_checks.py`:

```python
#!/usr/bin/env python3
"""Browser checks for every page on the new shell (spec 10; CI job `site`).

Each page opens at phone and desktop widths, in Paper and Ink, with Motion on and off. A page
fails on sideways scrolling, console errors, a font family that never loads, or accessibility
violations (axe-core, WCAG 2.1 A and AA). The menu and the display controls are exercised, and
the conditions a visitor can bring (no JavaScript, blocked storage, a saved value this version
does not know, a system asking for reduced motion) each get a run of their own. Screenshots of
every page and state go to --out, for reviewing a pull request by eye.

Usage, with the app running (see AGENTS.md, "Site checks"):
  python site-tests/run_site_checks.py --base-url http://127.0.0.1:8000 \
      --session-file site-tests/session.txt --out site-tests/artifacts
"""
import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
AXE_SCRIPT = HERE / "node_modules" / "axe-core" / "axe.min.js"
FONT_FAMILIES = ("Cormorant Garamond", "Newsreader", "IBM Plex Mono")
WIDTHS = (375, 1280)
APPEARANCES = ("light", "dark")  # the stored values for Paper and Ink
MOTIONS = ("on", "off")
OVERFLOW = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
ROOT_ATTRIBUTE = "(name) => document.documentElement.getAttribute(name)"
BLOCK_STORAGE = (
    'Object.defineProperty(window, "localStorage", { configurable: true, '
    'get() { throw new DOMException("Storage is blocked.", "SecurityError"); } });'
)


@dataclass(frozen=True)
class SitePage:
    path: str
    name: str  # used in screenshot file names
    signed_in: bool = False


# Every page that uses the new shell. Add each page here as it moves onto the shell.
PAGES = (
    SitePage("/app/components/", "component-catalog", signed_in=True),
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


def new_context(browser, base_url, session_key, site_page, *, width, theme, motion,
                javascript=True, reduced_motion="no-preference", block_storage=False):
    context = browser.new_context(viewport={"width": width, "height": 900},
                                  java_script_enabled=javascript, reduced_motion=reduced_motion)
    if site_page.signed_in:
        context.add_cookies([{"name": "sessionid", "value": session_key, "url": base_url}])
    context.add_init_script(BLOCK_STORAGE if block_storage else seed_storage(theme, motion))
    return context


def open_page(context, url):
    page = context.new_page()
    errors = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    response = page.goto(url, wait_until="networkidle")
    if response is None or response.status != 200:
        errors.append(f"{url} answered {response.status if response else 'nothing'}")
    return page, errors


def page_problems(page, label):
    problems = []
    overflow = page.evaluate(OVERFLOW)
    if overflow > 0:
        problems.append(f"{label}: the page scrolls sideways by {overflow}px")
    page.evaluate("() => document.fonts.ready.then(() => true)")
    unloaded = page.evaluate(
        """(families) => families.filter((family) => ![...document.fonts].some(
            (face) => face.family.replace(/["']/g, "") === family && face.status === "loaded"))""",
        list(FONT_FAMILIES),
    )
    problems += [f"{label}: the font {family} never loaded" for family in unloaded]
    # axe-core cannot measure contrast through a background image, so take the grain away first.
    page.add_style_tag(content="html { background-image: none !important; }")
    page.add_script_tag(path=str(AXE_SCRIPT))
    violations = page.evaluate(
        """async () => (await axe.run(document, { runOnly: { type: "tag",
              values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } })).violations.map(
              (v) => `${v.id} (${v.impact}): ${v.help} at ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)"""
    )
    problems += [f"{label}: accessibility: {violation}" for violation in violations]
    unmeasured = page.evaluate(
        """async () => (await axe.run(document, { runOnly: { type: "rule", values: ["color-contrast"] } }))
              .incomplete.flatMap((v) => v.nodes.map((n) => n.target.join(" ")))"""
    )
    for target in unmeasured:
        print(f"review by eye: {label}: axe could not measure the contrast of {target}")
    return problems


def menu_problems(page, label):
    problems = []
    toggle = page.locator("header [data-menu-toggle]")
    toggle.click()
    if toggle.get_attribute("aria-expanded") != "true" or not page.locator("#shell-menu").is_visible():
        problems.append(f"{label}: MENU does not open the menu")
    page.keyboard.press("Escape")
    if toggle.get_attribute("aria-expanded") != "false" or page.locator("#shell-menu").is_visible():
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
    return problems


def phone_menu_problems(page, label):
    """On a phone the open menu is a full sheet: what lies under it is inert, so Tab stays in the menu."""
    problems = []
    covered = "() => document.querySelector('main').inert && document.querySelector('.shell-corner--top-left').inert"
    if page.locator("#shell-menu").is_visible():
        problems.append(f"{label}: the menu starts open on a phone")
    page.locator("header [data-menu-toggle]").click()
    if not page.evaluate(covered):
        problems.append(f"{label}: the page under the open menu sheet is not inert")
    page.keyboard.press("Escape")
    if page.evaluate("() => document.querySelector('main').inert"):
        problems.append(f"{label}: the page stays inert after the menu closes")
    return problems


def edge_case_problems(browser, base_url, session_key, site_page, out):
    problems = []
    url = base_url + site_page.path

    # No JavaScript: every menu link is visible, the switches that need JavaScript are hidden.
    context = new_context(browser, base_url, session_key, site_page, width=375, theme=None, motion=None, javascript=False)
    page, errors = open_page(context, url)
    label = f"{site_page.path} without JavaScript"
    if page.locator("#shell-menu a").count() == 0 or not all(link.is_visible() for link in page.locator("#shell-menu a").all()):
        problems.append(f"{label}: some menu links are hidden")
    if page.locator("footer [data-appearance-toggle]").is_visible():
        problems.append(f"{label}: the appearance switch shows but cannot work")
    if page.evaluate(OVERFLOW) > 0:
        problems.append(f"{label}: the page scrolls sideways")
    page.screenshot(path=str(out / f"{site_page.name}-no-javascript.png"), full_page=True)
    problems += [f"{label}: {error}" for error in errors]
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
    return problems


def run(base_url, session_key, out):
    out.mkdir(parents=True, exist_ok=True)
    problems = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for site_page in PAGES:
            for width in WIDTHS:
                for appearance in APPEARANCES:
                    for motion in MOTIONS:
                        label = f"{site_page.path} at {width}px, {appearance}, motion {motion}"
                        context = new_context(browser, base_url, session_key, site_page, width=width, theme=appearance, motion=motion)
                        page, errors = open_page(context, base_url + site_page.path)
                        page.screenshot(path=str(out / f"{site_page.name}-{width}-{appearance}-motion-{motion}.png"), full_page=True)
                        problems += page_problems(page, label)
                        if (width, appearance, motion) == (1280, "light", "on"):
                            problems += menu_problems(page, label) + motion_problems(page, label) + appearance_problems(page, label)
                        if (width, appearance, motion) == (375, "light", "on"):
                            problems += phone_menu_problems(page, label)
                        problems += [f"{label}: console error: {error}" for error in errors]
                        context.close()
            problems += edge_case_problems(browser, base_url, session_key, site_page, out)
        browser.close()
    for problem in problems:
        print(problem)
    print(f"{len(problems)} problem(s) across {len(PAGES)} page(s); screenshots in {out}")
    return 1 if problems else 0


def main():
    parser = argparse.ArgumentParser(description="Browser checks for pages on the new shell.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--session-file", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if not AXE_SCRIPT.is_file():
        sys.exit(f"{AXE_SCRIPT} is missing: run `npm ci --prefix site-tests` first.")
    sys.exit(run(args.base_url.rstrip("/"), args.session_file.read_text(encoding="utf-8").strip(), args.out))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the checks locally and watch them fail**

Set up once:

```bash
python3 -m venv site-tests/.venv
site-tests/.venv/bin/pip install -r site-tests/requirements.txt
site-tests/.venv/bin/python -m playwright install chromium
npm ci --prefix site-tests
```

Start the app in a second terminal, from `backend/`:
`DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py migrate --noinput && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py runserver 127.0.0.1:8000 --noreload`

Then, from `backend/`:

```bash
SITE_SESSION_FILE=../site-tests/session.txt DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py shell -c "exec(open('../site-tests/make_staff_session.py').read())"
cd .. && site-tests/.venv/bin/python site-tests/run_site_checks.py --base-url http://127.0.0.1:8000 --session-file site-tests/session.txt --out site-tests/artifacts
```

Expected: exit 1. The problems include `console error: Failed to load resource: the server responded with a status of 404 (Not Found)` (because `shell.js` does not exist yet), `MENU does not open the menu`, `the appearance switch showed ...`, Motion problems and the phone-sheet problems. There must be no `scrolls sideways`, `never loaded` or `accessibility:` lines. If there are, fix them in the shell's CSS or templates before moving on. Lines starting `review by eye:` are not failures: look at those elements in the screenshots. Look through `site-tests/artifacts/*.png`. If port 8000 is busy, run the server on another port and pass that address as `--base-url`.

- [ ] **Step 5: Add the `site` job to CI**

In `.github/workflows/ci.yml`, add this job after the `python-cli` job, at the same indentation as `backend:`:

```yaml
  site:
    name: site
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4.2.2
      - uses: actions/setup-node@v4.4.0
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: site-tests/package-lock.json
      - uses: actions/setup-python@v5.6.0
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: |
            backend/requirements.txt
            site-tests/requirements.txt
      - name: Install dependencies
        run: |
          pip install -r backend/requirements.txt -r site-tests/requirements.txt
          python -m playwright install --with-deps chromium
          npm ci --prefix site-tests
      - name: Start the app
        working-directory: backend
        env:
          DJANGO_DEBUG: "True"
          DJANGO_SECRET_KEY: ci-site-not-a-secret
          SITE_SESSION_FILE: ../site-tests/session.txt
        run: |
          python manage.py migrate --noinput
          python manage.py shell -c "exec(open('../site-tests/make_staff_session.py').read())"
          nohup python manage.py runserver 127.0.0.1:8000 --noreload > ../site-tests/server.log 2>&1 &
          for attempt in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/healthz >/dev/null && break; sleep 1; done
          curl -fsS http://127.0.0.1:8000/healthz >/dev/null || { cat ../site-tests/server.log; exit 1; }
      - name: Browser checks
        run: python site-tests/run_site_checks.py --base-url http://127.0.0.1:8000 --session-file site-tests/session.txt --out site-tests/artifacts
      - name: Keep the screenshots and the server log
        if: always()
        uses: actions/upload-artifact@v4.4.3
        with:
          name: site-screenshots
          path: |
            site-tests/artifacts/
            site-tests/server.log
          retention-days: 14
```

Do not touch the required-checks comment at the top of `ci.yml`. `site` becomes required only in spec step 7, with the owner's go-ahead.

- [ ] **Step 6: Commit**

```bash
git add site-tests/requirements.txt site-tests/package.json site-tests/package-lock.json site-tests/make_staff_session.py site-tests/run_site_checks.py .github/workflows/ci.yml .gitignore
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Add browser checks for the new shell and the site CI job

Every page on the shell at 375 and 1280 px, Paper and Ink, Motion on and
off: sideways scroll, console errors, fonts, axe-core WCAG 2.1 AA, the
menu and the display controls, plus no-JavaScript, blocked-storage,
unknown-value and reduced-motion runs. Screenshots are kept as a CI
artifact. The job is not a required check yet.

EOF
```

---

### Task 10: `shell.js`: the menu and display preferences

**Files:**
- Create: `backend/static/js/shell.js`

**Interfaces:**
- Consumes: the DOM hooks from Task 7, and the attributes the boot script sets on `<html>`.
- Produces: the menu opens on MENU, closes on Escape (returning focus to MENU) or on a click outside, and starts open on wider screens when the shell's own menu has a link with `aria-current="page"`. On phones the open menu is a full sheet and everything under it is inert. The appearance switch cycles dark → system → light and saves `veilfile-theme`. Motion flips `on`/`off` and saves `veilfile-motion`. Every storage write is guarded.

- [ ] **Step 1: Confirm the failing checks**

The failing run from Task 9 Step 4 is this task's failing test. Rerun it if the server restarted. Expected: the same `MENU does not open the menu` and appearance and Motion problems.

- [ ] **Step 2: Write `shell.js`**

Create `backend/static/js/shell.js`:

```js
// The shell's behavior: the corner menu and the display preferences (spec 4.4, 6.4).
// The inline boot script in shell.html has already set data-theme and data-motion on <html>.
// Stored values stay light | dark | system and on | off, because pages not yet on the shell
// read the same keys.

const THEME_KEY = "veilfile-theme";
const MOTION_KEY = "veilfile-motion";
const APPEARANCE_ORDER = ["dark", "system", "light"]; // Ink, Auto, Paper
const APPEARANCE_WORD = { dark: "Ink", system: "Auto", light: "Paper" };
const PHONE = window.matchMedia("(max-width: 40rem)"); // where the open menu becomes a full sheet
const root = document.documentElement;

function save(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (error) {
    // Storage is blocked (private browsing, site data off): the choice lasts for this page only.
  }
}

function setUpMenu(nav) {
  const toggle = nav.querySelector("[data-menu-toggle]");
  const list = nav.querySelector("[data-menu-list]");
  if (!toggle || !list) return;
  const isShellMenu = Boolean(nav.closest("header"));
  const isOpen = () => toggle.getAttribute("aria-expanded") === "true";
  // While the phone sheet covers the page, everything under it is inert, so Tab stays in the menu.
  const covered = isShellMenu
    ? [...document.querySelectorAll(".skip-link, main, .shell-corner")].filter((part) => !part.contains(nav))
    : [];
  const updateInert = () => {
    const sheet = isOpen() && PHONE.matches;
    covered.forEach((part) => { part.inert = sheet; });
  };
  const setOpen = (open, returnFocus) => {
    toggle.setAttribute("aria-expanded", String(open));
    list.classList.toggle("is-open", open);
    updateInert();
    if (!open && returnFocus) toggle.focus();
  };
  toggle.addEventListener("click", () => setOpen(!isOpen(), false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isOpen()) setOpen(false, true);
  });
  document.addEventListener("click", (event) => {
    if (isOpen() && !nav.contains(event.target)) setOpen(false, false);
  });
  PHONE.addEventListener("change", updateInert);
  // On a chapter page the shell's menu starts open with the current chapter marked (spec 4.4),
  // except on a phone, where the open menu would cover the page.
  if (isShellMenu && !PHONE.matches && list.querySelector('[aria-current="page"]')) setOpen(true, false);
}

function setUpAppearance(button) {
  const show = () => {
    const word = APPEARANCE_WORD[root.getAttribute("data-theme")] || "Auto";
    button.textContent = word;
    button.setAttribute("aria-label", `Appearance: ${word}`);
  };
  button.addEventListener("click", () => {
    const current = APPEARANCE_ORDER.indexOf(root.getAttribute("data-theme"));
    const next = APPEARANCE_ORDER[(current + 1) % APPEARANCE_ORDER.length];
    root.setAttribute("data-theme", next);
    save(THEME_KEY, next);
    show();
  });
  show();
}

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
    root.setAttribute("data-motion", next);
    save(MOTION_KEY, next);
    show();
  });
  show();
}

document.querySelectorAll("[data-menu]").forEach(setUpMenu);
document.querySelectorAll("[data-appearance-toggle]").forEach(setUpAppearance);
document.querySelectorAll("[data-motion-toggle]").forEach(setUpMotion);
```

- [ ] **Step 3: Run the browser checks and watch them pass**

With the server from Task 9 still running (restart it if needed), run from the repo root:
`site-tests/.venv/bin/python site-tests/run_site_checks.py --base-url http://127.0.0.1:8000 --session-file site-tests/session.txt --out site-tests/artifacts`
Expected: `0 problem(s) across 1 page(s); ...` and exit 0. Open the 375px screenshots. With the menu closed, the phone layout shows only MENU top right.

- [ ] **Step 4: Run the Django suite for the pages app**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages -v 1`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add backend/static/js/shell.js
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Add shell.js: the corner menu and the display preferences

MENU opens and closes the column (Escape returns focus, a click outside
closes it), and on phones the page under the open sheet is inert; the
appearance switch cycles Ink, Auto, Paper and Motion flips on and off,
saving to the storage the older pages already use. Blocked storage
leaves the choice working for the page.

EOF
```

---

### Task 11: Content checks for posts, Docs and legal pages

**Files:**
- Create: `backend/pages/content_checks.py`, `backend/pages/tests_content.py`

**Interfaces:**
- Consumes: `pages.fonts.FontRanges` and `pages.fonts.FAMILY_NAMES`.
- Produces:
  - **`Problem(path, line, problem, fix)`,** whose `str()` is `"<path>:<line>: <problem>. Fix: <fix>"`.
  - **`read_file(path: Path, repo: Path) -> tuple[str, list[Problem]]`.**
  - **The checks,** each returning `list[Problem]`:
    - `check_post(path, text, fonts, static_dir)`
    - `check_doc_page(path, text, fonts)`
    - `check_legal_document(path, text, fonts)`
    - `check_template_characters(path, text, fonts)`
  - **`RAW_HTML_ALLOWED: dict[str, str]`**, mapping each allowed file to the reason.

- [ ] **Step 1: Write the failing tests**

Create `backend/pages/tests_content.py`:

```python
"""Content checks (spec 7.2): every failure names the file, the line, the problem and the fix."""
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from pages.content_checks import (
    check_doc_page, check_legal_document, check_post, check_template_characters, read_file,
)
from pages.fonts import FontRanges

REPO = Path(settings.BASE_DIR).parent
STATIC = Path(settings.BASE_DIR) / "static"
FONTS = FontRanges.load()
GOOD_POST = """---
title: Where should an agent put a screenshot?
date: 2026-11-03
slug: where-agents-put-screenshots
description: Agents make files that one or two people need to see for a while. Here is why a private link fits.
---

Your agent has just found the bug.

## What the file needs

- An address only the right people can find.
"""
POST_PATH = "backend/blog/posts/where-agents-put-screenshots.md"


def post(**changes):
    """GOOD_POST with front matter lines replaced; a value of None removes the key."""
    lines = GOOD_POST.split("\n")
    for key, value in changes.items():
        lines = [line for line in lines if not line.startswith(f"{key}:")] if value is None else [
            f"{key}: {value}" if line.startswith(f"{key}:") else line for line in lines]
    return "\n".join(lines)


def messages(problems):
    return [str(problem) for problem in problems]


class PostCheckTests(SimpleTestCase):
    def check(self, text, path=POST_PATH):
        return messages(check_post(path, text, FONTS, STATIC))

    def test_a_good_post_passes(self):
        self.assertEqual(self.check(GOOD_POST), [])

    def test_missing_key_names_the_fix(self):
        problems = self.check(post(description=None))
        self.assertEqual(len(problems), 1)
        self.assertIn(f"{POST_PATH}:1: the front matter has no 'description'", problems[0])
        self.assertIn("Fix: add a line 'description: ...'", problems[0])

    def test_unknown_key_is_flagged_on_its_line(self):
        text = GOOD_POST.replace("slug:", "descripton: oops\nslug:")
        self.assertIn(f"{POST_PATH}:4: 'descripton' is not a post key", "\n".join(self.check(text)))

    def test_title_longer_than_60_characters(self):
        self.assertIn(f"{POST_PATH}:2: the title is 61 characters", "\n".join(self.check(post(title="x" * 61))))

    def test_description_length(self):
        self.assertIn("the description is 12 characters", "\n".join(self.check(post(description="Too short ok"))))
        self.assertIn("the description is 161 characters", "\n".join(self.check(post(description="y" * 161))))

    def test_slug_must_match_the_file_name(self):
        problems = "\n".join(self.check(GOOD_POST, path="backend/blog/posts/other-name.md"))
        self.assertIn("the slug 'where-agents-put-screenshots' does not match the file name 'other-name.md'", problems)

    def test_slug_shape(self):
        self.assertIn("the slug 'Bad_Slug' is not lowercase words joined by hyphens",
                      "\n".join(self.check(post(slug="Bad_Slug"), path="backend/blog/posts/Bad_Slug.md")))

    def test_date_must_be_a_real_yyyy_mm_dd(self):
        self.assertIn("the date '20261103' is not YYYY-MM-DD", "\n".join(self.check(post(date="20261103"))))
        self.assertIn("the date '2026-02-30' is not a real date", "\n".join(self.check(post(date="2026-02-30"))))

    def test_raw_html_outside_code(self):
        text = GOOD_POST + "\n<div class=\"note\">Hi</div>\n\nUse `<key>` in a header.\n\n```\n<pre>fine in a fence</pre>\n```\n"
        problems = [p for p in self.check(text) if "raw HTML" in p]
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("raw HTML <div>", problems[0])

    def test_autolinks_are_not_raw_html(self):
        self.assertEqual(self.check(GOOD_POST + "\nSee <https://veilfile.com/docs/>.\n"), [])

    def test_first_heading_repeating_the_title(self):
        text = GOOD_POST.replace("## What the file needs", "# Where should an agent put a screenshot?")
        self.assertIn("the first heading repeats the title", "\n".join(self.check(text)))

    def test_images_need_alt_text_and_a_real_file(self):
        problems = "\n".join(self.check(GOOD_POST + "\n![](/static/img/mark.svg)\n\n![The mark](/static/img/missing.png)\n"))
        self.assertIn("an image has no alt text", problems)
        self.assertIn("/static/img/missing.png does not exist", problems)

    def test_front_matter_image_needs_alt_and_a_file(self):
        text = GOOD_POST.replace("---\n\n", "image: img/missing.png\n---\n\n", 1)
        problems = "\n".join(self.check(text))
        self.assertIn("'image' needs 'image_alt'", problems)
        self.assertIn("img/missing.png does not exist under backend/static", problems)

    def test_characters_no_font_can_draw(self):
        problems = "\n".join(self.check(GOOD_POST + "\nPress ⌘ to continue.\n"))
        self.assertIn("'⌘' (U+2318) is not in the Newsreader font", problems)


class FileEncodingTests(SimpleTestCase):
    """Review focus 5: the loader skips a post with a BOM or invalid UTF-8 with only a log warning; the checks say why. LF endings are a house rule."""

    def write(self, name, data):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = directory / name
        path.write_bytes(data)
        return path, directory

    def test_windows_line_endings(self):
        path, repo = self.write("crlf.md", GOOD_POST.replace("\n", "\r\n").encode())
        text, problems = read_file(path, repo)
        self.assertIn("crlf.md:1: the file has Windows line endings", messages(problems)[0])
        self.assertNotIn("\r", text)

    def test_byte_order_mark(self):
        path, repo = self.write("bom.md", b"\xef\xbb\xbf" + GOOD_POST.encode())
        text, problems = read_file(path, repo)
        self.assertIn("starts with a byte-order mark", messages(problems)[0])
        self.assertTrue(text.startswith("---"))

    def test_not_utf8(self):
        path, repo = self.write("latin1.md", "café".encode("latin-1"))
        text, problems = read_file(path, repo)
        self.assertIn("is not UTF-8", messages(problems)[0])
        self.assertEqual(text, "")


class DocAndLegalCheckTests(SimpleTestCase):
    GOOD_DOC = """---
title: Agent setup
slug: agent
seo_title: Agent setup | Veilfile docs
description: Connect an agent to Veilfile over MCP or the API, with a key or OAuth, in a few minutes, step by step.
order: 3
---

## MCP
"""

    def test_a_good_docs_page_passes(self):
        self.assertEqual(messages(check_doc_page("backend/docs/pages/agent.md", self.GOOD_DOC, FONTS)), [])

    def test_docs_order_must_be_a_whole_number(self):
        problems = messages(check_doc_page("backend/docs/pages/agent.md", self.GOOD_DOC.replace("order: 3", "order: three"), FONTS))
        self.assertIn("the order 'three' is not a whole number", "\n".join(problems))

    def test_legal_pages_are_checked_for_characters_only(self):
        self.assertEqual(messages(check_legal_document("backend/legal/documents/x.md", "# Terms\n\nPlain text.\n", FONTS)), [])
        self.assertIn("'⌘' (U+2318)", "\n".join(messages(check_legal_document("backend/legal/documents/x.md", "# T\n\n⌘\n", FONTS))))

    def test_templates_need_characters_every_face_draws(self):
        self.assertIn("'⌘' (U+2318)", "\n".join(messages(check_template_characters("backend/templates/x.html", "<p>⌘</p>", FONTS))))


class RealContentTests(SimpleTestCase):
    """Every content file in the repo passes. Failures list every problem at once."""

    def assert_clean(self, problems):
        self.assertEqual(problems, [], "\n" + "\n".join(messages(problems)))

    def run_check(self, pattern, check, *extra):
        problems = []
        for path in sorted(REPO.glob(pattern)):
            text, file_problems = read_file(path, REPO)
            problems += file_problems + (check(str(path.relative_to(REPO)), text, FONTS, *extra) if text else [])
        return problems

    def test_every_journal_post(self):
        self.assert_clean(self.run_check("backend/blog/posts/*.md", check_post, STATIC))

    def test_every_docs_page(self):
        self.assert_clean(self.run_check("backend/docs/pages/*.md", check_doc_page))

    def test_every_legal_document(self):
        self.assert_clean(self.run_check("backend/legal/documents/*.md", check_legal_document))

    def test_every_shell_template(self):
        problems = self.run_check("backend/templates/shell.html", check_template_characters)
        for directory in ("components", "layouts", "chapters", "pages"):
            problems += self.run_check(f"backend/templates/{directory}/*.html", check_template_characters)
        self.assert_clean(problems)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_content -v 2`
Expected: ERROR, `ModuleNotFoundError: No module named 'pages.content_checks'`.

- [ ] **Step 3: Write the checks**

Create `backend/pages/content_checks.py`:

```python
"""Checks for the site's written content: Journal posts, Docs pages, legal pages and shell templates.

Every check returns Problems that name the file, the line, what is wrong and how to fix it,
so an agent can act on a failure without reading this module. tests_content.py runs them over
every content file; AGENTS.md ("Site recipes") points writers at that test.
"""
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pages.fonts import FAMILY_NAMES

POST_REQUIRED = ("title", "date", "slug", "description")
POST_OPTIONAL = ("image", "image_alt")
DOC_REQUIRED = ("title", "slug", "seo_title", "description", "order")
TITLE_MAX = 60
DESCRIPTION_MIN, DESCRIPTION_MAX = 70, 160
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FENCE = re.compile(r"^\s*(```|~~~)")
CODE_SPAN = re.compile(r"(`+)(.+?)\1")
RAW_TAG = re.compile(r"<(?!https?://|mailto:)(/?[A-Za-z][A-Za-z0-9-]*|!--)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)")
# Docs pages that may still carry raw HTML, and why. Remove an entry when its markup becomes a component.
RAW_HTML_ALLOWED = {
    "backend/docs/pages/quickstart.md": "the shell switcher and copy boxes, which become components when Docs move onto the shell (PR 4)",
}


@dataclass(frozen=True)
class Problem:
    path: str
    line: int
    problem: str
    fix: str

    def __str__(self):
        return f"{self.path}:{self.line}: {self.problem}. Fix: {self.fix}"


@dataclass(frozen=True)
class Field:
    value: str
    line: int


def read_file(path, repo):
    """The file's text with line endings normalised, plus problems with the file itself."""
    name = str(Path(path).relative_to(repo))
    data = Path(path).read_bytes()
    problems = []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "", [Problem(name, 1, "the file is not UTF-8", "save it as UTF-8")]
    if text.startswith("﻿"):
        problems.append(Problem(name, 1, "the file starts with a byte-order mark", "save it as UTF-8 without a BOM"))
        text = text[1:]
    if "\r" in text:
        line = text[:text.index("\r")].count("\n") + 1
        problems.append(Problem(name, line, "the file has Windows line endings", "save it with LF line endings"))
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text, problems


def _front_matter(path, lines):
    """({key: Field}, index of the first body line, problems)."""
    if not lines or lines[0] != "---":
        return {}, 0, [Problem(path, 1, "the file does not start with a front matter block",
                               "start it with a line '---', the key: value lines, then another '---'")]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, 0, [Problem(path, 1, "the front matter block is never closed", "add a line '---' after the last key: value line")]
    fields, problems = {}, []
    for index in range(1, end):
        raw = lines[index]
        if not raw.strip():
            continue
        key, colon, value = raw.partition(":")
        if not colon:
            problems.append(Problem(path, index + 1, f"the front matter line {raw!r} has no colon", "write it as key: value"))
            continue
        key = key.strip()
        if key in fields:
            problems.append(Problem(path, index + 1, f"'{key}' appears twice in the front matter", "keep one"))
        fields[key] = Field(value.strip(), index + 1)
    return fields, end + 1, problems


def _body(lines, start):
    """[(line number, text, in_code_fence)] for the body."""
    rows, fenced = [], False
    for index in range(start, len(lines)):
        if FENCE.match(lines[index]):
            fenced = not fenced
            rows.append((index + 1, lines[index], True))
            continue
        rows.append((index + 1, lines[index], fenced))
    return rows


def _keys(path, fields, required, optional, kind):
    problems = [Problem(path, 1, f"the front matter has no '{key}'", f"add a line '{key}: ...' between the '---' lines")
                for key in required if key not in fields]
    allowed = (*required, *optional)
    problems += [Problem(path, field.line, f"'{key}' is not a {kind} key", f"use one of: {', '.join(allowed)}")
                 for key, field in fields.items() if key not in allowed]
    return problems


def _length(path, fields, key, low, high, what):
    if key not in fields:
        return []
    size = len(fields[key].value)
    if size < low or size > high:
        span = f"{high} or fewer" if low == 0 else f"between {low} and {high}"
        return [Problem(path, fields[key].line, f"the {what} is {size} characters",
                        f"keep it {span} so search results show all of it")]
    return []


def _slug(path, fields):
    if "slug" not in fields:
        return []
    slug, line, file_name = fields["slug"].value, fields["slug"].line, Path(path).name
    problems = []
    if not SLUG.match(slug):
        problems.append(Problem(path, line, f"the slug '{slug}' is not lowercase words joined by hyphens",
                                "use only a-z, 0-9 and single hyphens, like where-agents-put-screenshots"))
    if f"{slug}.md" != file_name:
        problems.append(Problem(path, line, f"the slug '{slug}' does not match the file name '{file_name}'",
                                f"rename the file to {slug}.md, or change the slug to {Path(path).stem}"))
    return problems


def _date(path, fields):
    if "date" not in fields:
        return []
    value, line = fields["date"].value, fields["date"].line
    if not DATE.match(value):
        return [Problem(path, line, f"the date '{value}' is not YYYY-MM-DD", "write it like 2026-11-03")]
    try:
        date.fromisoformat(value)
    except ValueError:
        return [Problem(path, line, f"the date '{value}' is not a real date", "check the month and day")]
    return []


def _order(path, fields):
    if "order" in fields and not fields["order"].value.isdigit():
        return [Problem(path, fields["order"].line, f"the order '{fields['order'].value}' is not a whole number",
                        "use a whole number; pages are listed from the smallest")]
    return []


def _front_matter_image(path, fields, static_dir):
    problems = []
    if "image" in fields:
        if not fields.get("image_alt", Field("", 0)).value:
            problems.append(Problem(path, fields["image"].line, "'image' needs 'image_alt'",
                                    "add a line 'image_alt: ...' with one sentence describing the image"))
        if not (Path(static_dir) / fields["image"].value).is_file():
            problems.append(Problem(path, fields["image"].line, f"{fields['image'].value} does not exist under backend/static",
                                    "put the image in backend/static/img/journal/ and use that path, like img/journal/x.png"))
    elif "image_alt" in fields:
        problems.append(Problem(path, fields["image_alt"].line, "'image_alt' has no 'image'", "add 'image' or remove 'image_alt'"))
    return problems


def _raw_html(path, rows):
    if path in RAW_HTML_ALLOWED:
        return []
    problems = []
    for number, text, fenced in rows:
        if fenced:
            continue
        match = RAW_TAG.search(CODE_SPAN.sub("", text))
        if match:
            problems.append(Problem(path, number, f"raw HTML <{match.group(1)}> in the body",
                                    "write it in Markdown; the template decides how everything looks"))
    return problems


def _first_heading(path, fields, rows):
    if "title" not in fields:
        return []
    for number, text, fenced in rows:
        heading = None if fenced else HEADING.match(text)
        if heading:
            def plain(value):
                return value.casefold().rstrip(".:!?").strip()
            if plain(heading.group(1)) == plain(fields["title"].value):
                return [Problem(path, number, "the first heading repeats the title", "delete the heading; the page shows the title already")]
            return []
    return []


def _body_images(path, rows, static_dir):
    problems = []
    for number, text, fenced in rows:
        if fenced:
            continue
        for alt, source in IMAGE.findall(CODE_SPAN.sub("", text)):
            if not alt.strip():
                problems.append(Problem(path, number, "an image has no alt text",
                                        "write a sentence describing it between the brackets: ![A failing checkout page](...)"))
            if source.startswith("/static/") and not (Path(static_dir) / source[len("/static/"):]).is_file():
                problems.append(Problem(path, number, f"{source} does not exist",
                                        "put the file under backend/static/ or fix the path"))
    return problems


def _missing(path, line, char, family):
    return Problem(path, line, f"'{char}' (U+{ord(char):04X}) is not in the {family} font",
                   "replace it with a character the font has, or ask the owner to ship a font range that covers it")


def _characters(path, fields, rows, fonts, title_keys):
    problems, seen = [], set()

    def check(text, role, line):
        for char in text:
            if char in "\t" or ord(char) < 32 or (line, char, role) in seen:
                continue
            if not fonts.covers(role, char):
                seen.add((line, char, role))
                problems.append(_missing(path, line, char, FAMILY_NAMES[role]))

    for key, field in fields.items():
        check(field.value, "display" if key in title_keys else "reading", field.line)
    for number, text, fenced in rows:
        if fenced:
            check(text, "mono", number)
            continue
        for match in CODE_SPAN.finditer(text):
            check(match.group(2), "mono", number)
        check(CODE_SPAN.sub("", text), "reading", number)
    return problems


def check_post(path, text, fonts, static_dir):
    lines = text.split("\n")
    fields, start, problems = _front_matter(path, lines)
    rows = _body(lines, start)
    problems += _keys(path, fields, POST_REQUIRED, POST_OPTIONAL, "post")
    problems += _length(path, fields, "title", 0, TITLE_MAX, "title")
    problems += _length(path, fields, "description", DESCRIPTION_MIN, DESCRIPTION_MAX, "description")
    problems += _slug(path, fields) + _date(path, fields) + _front_matter_image(path, fields, static_dir)
    problems += _raw_html(path, rows) + _first_heading(path, fields, rows) + _body_images(path, rows, static_dir)
    problems += _characters(path, fields, rows, fonts, title_keys=("title",))
    return problems


def check_doc_page(path, text, fonts):
    lines = text.split("\n")
    fields, start, problems = _front_matter(path, lines)
    rows = _body(lines, start)
    problems += _keys(path, fields, DOC_REQUIRED, (), "Docs page")
    problems += _length(path, fields, "title", 0, TITLE_MAX, "title")
    problems += _length(path, fields, "seo_title", 0, TITLE_MAX, "seo_title")
    problems += _length(path, fields, "description", DESCRIPTION_MIN, DESCRIPTION_MAX, "description")
    problems += _slug(path, fields) + _order(path, fields)
    problems += _raw_html(path, rows) + _first_heading(path, fields, rows)
    problems += _characters(path, fields, rows, fonts, title_keys=("title",))
    return problems


def check_legal_document(path, text, fonts):
    """Legal text is the owner's (AGENTS.md invariant 8): check only that the fonts can draw it."""
    return _characters(path, {}, _body(text.split("\n"), 0), fonts, title_keys=())


def check_template_characters(path, text, fonts):
    """Shell templates mix all three faces, so every character must be in every face."""
    problems = []
    for number, line in enumerate(text.split("\n"), start=1):
        for char in sorted(set(line)):
            if ord(char) < 128:
                continue
            for role in ("display", "reading", "mono"):
                if not fonts.covers(role, char):
                    problems.append(_missing(path, number, char, FAMILY_NAMES[role]))
                    break
    return problems
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_content -v 2`
Expected: OK. If `RealContentTests` fails, read the listed problems.

- A problem in a post, a Docs page or a template you wrote in this PR: fix the content.
- A problem in a legal document: do not edit it (AGENTS.md invariant 8). Stop and report it to the owner.

The mapping run found the current content clean, apart from the allowlisted `quickstart.md` HTML.

- [ ] **Step 5: Commit**

```bash
git add backend/pages/content_checks.py backend/pages/tests_content.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Add content checks for posts, Docs pages, legal pages and shell templates

Front matter keys and limits, slug equals file name, real dates, no raw
HTML outside code, no title repeated as a heading, images with alt text,
every character drawable by the self-hosted fonts, and files saved as
UTF-8 with LF endings. Each failure names the file, line, problem and fix.

EOF
```

---

### Task 12: Site rules: links, llms.txt and the template and CSS rules

**Files:**
- Create: `backend/pages/tests_site_rules.py`
- Modify: `docs/llms.txt` (add a short section near the end, before the final Docs links)

**Interfaces:**
- Consumes: `registry.PAGES`, `registry.MENU` and `registry.canonical_url`, `catalog.entries()`, and the loaders.
- Produces: tests only.

- [ ] **Step 1: Write the failing tests**

Create `backend/pages/tests_site_rules.py`:

```python
"""Site rules (spec 7.2): internal links resolve, llms.txt names the chapters, templates and CSS keep the conventions."""
import re
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from blog import loader as blog_loader
from docs import loader as docs_loader
from pages import catalog, registry

BACKEND = Path(settings.BASE_DIR)
REPO = BACKEND.parent
TEMPLATES = BACKEND / "templates"
CSS = BACKEND / "static" / "css"
# Paths that are linked but are not pages a GET can open, and why.
NOT_PAGES = {"/mcp": "the MCP endpoint answers POST from MCP clients"}


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href")
        if tag == "a" and href:
            self.hrefs.append(href)


def shell_templates():
    paths = [TEMPLATES / "shell.html"]
    for directory in ("components", "layouts", "chapters", "pages"):
        paths += sorted((TEMPLATES / directory).glob("*.html"))
    return [path for path in paths if path.is_file()]


class InternalLinksTests(TestCase):
    def test_every_internal_link_on_every_public_page_resolves(self):
        sources = [reverse(page.url_name) for page in registry.PAGES]
        sources += [reverse("docs-page", args=[doc.slug]) for doc in docs_loader.load_pages() if doc.slug != "index"]
        sources += [reverse("blog-post", args=[post.slug]) for post in blog_loader.load_posts()]
        broken = []
        for source in sources:
            response = self.client.get(source)
            self.assertEqual(response.status_code, 200, source)
            parser = _Links()
            parser.feed(response.content.decode())
            for href in parser.hrefs:
                path = href.split("#")[0]
                if not path.startswith("/") or path.startswith("//") or path.startswith("/static/") or path in NOT_PAGES:
                    continue
                status = self.client.get(path, follow=True).status_code
                if status != 200:
                    broken.append(f"{source} links to {href}, which answers {status}")
        self.assertEqual(broken, [], "\n" + "\n".join(broken))


class LlmsTxtTests(SimpleTestCase):
    def test_names_every_chapter_and_the_mcp_address(self):
        text = (REPO / "docs" / "llms.txt").read_text(encoding="utf-8")
        for link in registry.MENU[0]:
            with self.subTest(chapter=link.label):
                self.assertIn(registry.canonical_url(link.url_name), text)
        self.assertIn("https://YOUR-DEPLOY/mcp", text)


class TemplateRulesTests(SimpleTestCase):
    def test_no_inline_styles(self):
        for path in shell_templates():
            with self.subTest(template=path.name):
                self.assertNotRegex(path.read_text(encoding="utf-8"), r"\sstyle\s*=",
                                    f"{path.relative_to(REPO)}: move the style into the component's CSS file")

    def test_components_are_included_with_only(self):
        include = re.compile(r"{%\s*include\s+[\"']components/([\w-]+)\.html[\"'](.*?)%}")
        for path in shell_templates():
            for name, rest in include.findall(path.read_text(encoding="utf-8")):
                with self.subTest(template=path.name, component=name):
                    self.assertRegex(rest, r"\bonly\s*$", f"{path.relative_to(REPO)}: include components/{name}.html with ... only")

    def test_components_start_with_their_two_comments(self):
        for path in sorted((TEMPLATES / "components").glob("*.html")):
            lines = path.read_text(encoding="utf-8").split("\n")
            with self.subTest(component=path.stem):
                self.assertTrue(lines[0].startswith("{# Component: ") and lines[0].endswith("#}"), f"{path.name} line 1")
                self.assertTrue(lines[1].startswith("{# Inputs: ") and lines[1].endswith("#}"), f"{path.name} line 2")

    def test_every_component_has_a_stylesheet_and_a_catalog_entry(self):
        names = {path.stem for path in (TEMPLATES / "components").glob("*.html")}
        self.assertEqual(names, {entry.name for entry in catalog.entries()})
        for name in names:
            with self.subTest(component=name):
                self.assertTrue((CSS / "components" / f"{name}.css").is_file(), f"add backend/static/css/components/{name}.css")

    def test_colors_live_only_in_tokens_css(self):
        color = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch|lab|lch)\(")
        for path in sorted(CSS.rglob("*.css")):
            if path.name == "tokens.css":
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
                with self.subTest(file=path.name, line=number):
                    self.assertIsNone(color.search(line), f"{path.relative_to(REPO)}:{number}: use a token from tokens.css")
```

- [ ] **Step 2: Run them and watch the llms.txt test fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules -v 2`
Expected: two subtests of `test_names_every_chapter_and_the_mcp_address` fail (About and Plans), because their addresses are not in llms.txt yet. Everything else passes. If the link test fails, read each listed source and link. A link to an endpoint that is not a page, such as `/api/v1/...` (which answers 401 without a key), belongs in `NOT_PAGES` with its reason. Any other failure is a broken link in an existing page: report it to the owner rather than editing old templates.

- [ ] **Step 3: Name the chapters in llms.txt**

In `docs/llms.txt`, directly before the final lines that link to `https://veilfile.com/docs/api/` and `https://veilfile.com/docs/`, add:

```
## Pages for people

- About: https://veilfile.com/about/
- Plans and limits: https://veilfile.com/plans/
```

Do not write plan names followed by numbers here; the plan-copy test scans this file. The MCP address `https://YOUR-DEPLOY/mcp` is already in the file.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules drop_core.tests_fix_docs -v 2`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add backend/pages/tests_site_rules.py docs/llms.txt
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Add site rules: internal links, llms.txt chapters, template and CSS rules

Every internal link on every public page resolves; llms.txt names each
chapter and the MCP address; shell templates have no inline styles,
include components with only, and start each component with its two
comments; colors appear only in tokens.css.

EOF
```

---

### Task 13: Docs, spec amendments and the full run

**Files:**
- Modify: `AGENTS.md`, `README.md`, `docs/superpowers/specs/2026-10-08-site-redesign-design.md`

**Interfaces:**
- Consumes: everything above.
- Produces: the recipes and commands Muse follows.

- [ ] **Step 1: Update AGENTS.md**

In `## Repo map`, replace the line

```
backend/templates/     Django templates (plans page, device flow, OAuth consent)
```

with

```
backend/templates/     Django templates: shell.html and components/ for the new shell;
                       older pages (plans, device flow, OAuth consent) until they move onto it
backend/pages/         public-page registry, sitemap, component catalog, content checks
backend/static/        the new shell's CSS, JS, fonts and images (no build step)
site-tests/            browser checks for pages on the new shell (CI job `site`)
tools/                 one-off generators run by hand: vendor_fonts.py, make_images.py
```

In `## Run the tests`, after the paragraph that ends "Warnings other than the accepted list in that file fail the build.", add:

````markdown
CI also runs `collectstatic` with production settings, so a missing or
broken static file fails the pull request instead of the Docker build.

## Site checks

The `site` CI job opens every page on the new shell in Chromium at phone
and desktop widths, in Paper and Ink, with Motion on and off. It fails on
sideways scrolling, console errors, fonts that never load or accessibility
violations, and it keeps screenshots of every state as a build artifact
named `site-screenshots`. Pages are listed in `PAGES` in
`site-tests/run_site_checks.py`; add a page there when it moves onto the
shell. To run it locally:

```bash
python3 -m venv site-tests/.venv && site-tests/.venv/bin/pip install -r site-tests/requirements.txt
site-tests/.venv/bin/python -m playwright install chromium
npm ci --prefix site-tests
# terminal 1, from backend/:
DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py migrate --noinput
DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py runserver 127.0.0.1:8000 --noreload
# terminal 2, from backend/, then the repo root:
SITE_SESSION_FILE=../site-tests/session.txt DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py shell -c "exec(open('../site-tests/make_staff_session.py').read())"
cd .. && site-tests/.venv/bin/python site-tests/run_site_checks.py --base-url http://127.0.0.1:8000 --session-file site-tests/session.txt --out site-tests/artifacts
```
````

After the whole `## Pricing changes` section, and before `## Docs and tests hygiene`, add:

````markdown
## Site recipes

### Write a Journal post

1. Create `backend/blog/posts/<slug>.md`. The file name is the slug:
   lowercase words joined by hyphens, like `where-agents-put-screenshots.md`.
   Do not put a date in the file name.
2. Start the file with this header, then a blank line, then the post:

   ```
   ---
   title: Where should an agent put a screenshot?
   date: 2026-11-03
   slug: where-agents-put-screenshots
   description: Agents make files that one or two people need to see for a while. Here is why a private link fits.
   ---
   ```

   - `title`: 60 characters or fewer. Do not repeat it as a heading in the body.
   - `date`: YYYY-MM-DD. A post dated in the future stays hidden until that day.
   - `description`: 70 to 160 characters, plain sentences. It is the search-result snippet.
   - Optional `image`: a path under `backend/static/`, like `img/journal/checkout.png`.
     It needs `image_alt`, one sentence describing the image.
3. Write the body in Markdown only: `##` headings, paragraphs, lists, links,
   `code`, fenced code blocks, and images with alt text, like
   `![A failing checkout page](/static/img/journal/checkout.png)`. No HTML
   and no styling: the template decides how everything looks.
4. Check it:
   `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_content`.
   Every failure names the file, the line, the problem and the fix.
5. Open a pull request. Plan prices and limits in the post must match
   `plans.py` (the docs tests check them).
````

- [ ] **Step 2: Update README.md**

In `## Tests`, replace

```
CI (`.github/workflows/ci.yml`) runs all of the above plus
`manage.py check --deploy` on every push and pull request to `main`.
```

with

```
CI (`.github/workflows/ci.yml`) runs all of the above plus
`manage.py check --deploy`, a production-settings `collectstatic`, and the
browser checks in `site-tests/` (job `site`; see AGENTS.md, "Site checks")
on every push and pull request to `main`.
```

In `## Project layout`, replace the line

```
  templates/       login/signup/plans/device pages
```

with

```
  pages/           public-page registry, sitemap, component catalog, content checks
  static/          the new shell's CSS, JS, fonts and images
  templates/       shell.html and components/, plus login/signup/plans/device pages
```

and add these two lines after the `docs/ ...` entry (both lines of it):

```
site-tests/        browser checks for the new shell (CI job `site`)
tools/             one-off font and image generators, run by hand
```

- [ ] **Step 3: Amend the spec to match what was built**

In `docs/superpowers/specs/2026-10-08-site-redesign-design.md`:

**Section 6.4.** Replace the "Storage" bullet with:

```markdown
- **Storage:** Motion and appearance are stored per browser in `localStorage`, with every read and write guarded. The keys and values are the ones the current pages already use, so pages not yet on the shell and pages on it never disagree: `veilfile-theme` is `light`, `dark` or `system` (shown as Paper, Ink and Auto), and `veilfile-motion` is `on` or `off`. With no stored motion value, the default comes from `prefers-reduced-motion`.
```

**Section 7.1.** In recipe 1, directly after the line `     - optionally `image` with a required `image_alt``, add this bullet, indented three spaces like its neighbours:

```markdown
   - The file name is the slug, with no date prefix.
```

**Section 7.2.** In the opening paragraph, inside the existing backticks, replace the text `blog/posts/2026-11-03-foo.md` with `blog/posts/where-agents-put-screenshots.md`. At the end of the "Templates and CSS" bullet, add:

```markdown
  These template and CSS rules apply to the new shell's files (`shell.html`, `components/`, `layouts/`, `chapters/`, `pages/` and `backend/static/css/`). Older templates are checked as they move onto the shell. Raw HTML in Docs is allowed only for the files listed in `RAW_HTML_ALLOWED` in `pages/content_checks.py`, each with its reason. Legal documents are checked for characters only.
```

**Section 4.4.** Replace the bullet `- The menu stays open on chapter pages and marks the current chapter.` with:

```markdown
- The menu stays open on chapter pages and marks the current chapter, on wider screens. On phones it starts closed, because the open menu is a full-screen sheet; while the sheet is open, the page beneath it is inert.
```

**Section 9.** In the "Keyboard" bullet, change `the menu closes on Escape and returns focus;` to `the menu closes on Escape and returns focus, and while the phone sheet is open the page beneath it is inert;`.

**Section 6.3.** Directly after the bullet that starts "Static storage switches to WhiteNoise's" (it ends "so they don't need `collectstatic`."), add:

```markdown
- WhiteNoise writes gzip copies only: brotli is not installed, and adding it is a new dependency. Cloudflare compresses at the edge either way.
```

- [ ] **Step 4: Run everything**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: OK. The count is higher than the 896 before this PR.

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`.

Run the deploy check exactly as CI does. Copy the env block from the "Deploy checks (production-like env)" step in `.github/workflows/ci.yml` and run `manage.py check --deploy` with it. Expected: only the accepted warning IDs (`oauth2_provider.W004`-`W008`, `security.W005`, `security.W021`).

Run the browser checks once more (Task 9 Step 4 commands). Expected: `0 problem(s)`.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md README.md docs/superpowers/specs/2026-10-08-site-redesign-design.md
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Document the new shell's foundations and the Journal recipe

AGENTS.md gains the repo map entries, the site checks and the first site
recipe (writing a Journal post); README notes the new CI steps and
folders; the spec now records the shared preference storage, the slug
file-name rule and the scope of the template and content checks.

EOF
```

- [ ] **Step 6: Open the pull request (with the owner's go-ahead)**

Push the branch and open a PR against `main`, but only once the owner says to. The description lists what changed for visitors (nothing), the new staff-only page, the new `site` job (not yet required) and where its screenshots are.
