# Site redesign PR 2: the threshold and the chapters — implementation plan

**Goal:** Move `/` and the five chapters (About, How it works, Begin, Promises, Plans) onto the new shell as server-rendered Django pages, with Come in and the install line, chapters that change in place, and every page's `<head>` taken from the registry. This is the still page only: no cloth (that is PR 3).

**Architecture:** One layout, `layouts/chapter.html`, serves the threshold and the chapters; each chapter is a template in `templates/chapters/` rendered by a view in `backend/pages/views.py`. A context processor hands `shell.html` the registry entry for the page being rendered, so the title, description, canonical address, Open Graph tags and JSON-LD all come from `pages/registry.py`. Small ES modules in `static/js/behaviors/` (tabs, Copy, Come in) attach by data attribute through event delegation, and `shell.js` gains in-place chapter changes: it fetches the next page, swaps the children of `<main>` and the marked `<head>` elements, and records history, falling back to an ordinary page load on anything unexpected.

**Tech stack:** Django 6.1 templates, plain CSS with tokens, plain ES modules with no build step, Playwright for Python and axe-core for the browser checks.

**Spec:** `docs/superpowers/specs/2026-10-08-site-redesign-design.md` (binding). Sections used: 2, 3.4, 3.5, 3.6, 4.1 to 4.5, 4.10, 5, 6.2 to 6.6, 7, 8, 9, 10, and 11 step 2.

## Global Constraints

Every task's requirements include these.

- **Branch:** `design/site-redesign-pr2`, which already holds this plan's commit, cut from `origin/main`. Start with `git switch design/site-redesign-pr2 && git fetch origin && git rebase origin/main` (or, for a worktree, `git worktree add .claude/worktrees/pr2 design/site-redesign-pr2` and the same fetch and rebase there). Muse's teams push to `main` too, so before the final review run `git fetch origin && git rebase origin/main` and rerun the full suite. Never push, merge or open a PR without the owner's go-ahead.
- **Commits:** `git -c user.name=Veilfile -c user.email=support@veilfile.com commit`, with the message the commit step gives and nothing added: no `Co-Authored-By` or `Claude-Session` trailer and no mention of Claude or of any model, whatever a session's attribution reminder says (the owner's rule).
- **Tests:** `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test <labels>`; the whole suite is the same command with no labels (about 3.5 minutes; about 1020 tests at the end). A fresh worktree prints `UserWarning: No directory at: .../backend/staticfiles/` on every run; ignore it.
- **Browser checks:** with the dev server running (`cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py runserver 127.0.0.1:8777 --noreload`) and a staff session made once per database (`cd backend && SITE_SESSION_FILE=../site-tests/session.txt DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py shell -c "exec(open('../site-tests/make_staff_session.py').read())"`), run from the repo root `site-tests/.venv/bin/python site-tests/run_site_checks.py --base-url http://127.0.0.1:8777 --session-file site-tests/session.txt --out site-tests/artifacts`. Expected at the end of every task that touches pages: `0 problem(s)`; the run also prints `N note(s) to review by eye` (axe colour-contrast 'incomplete' items on the current menu link, the MENU toggle and `pre > code`): they are not problems, and N grows as pages are added. A fresh worktree has no database: before making the staff session run `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py migrate --noinput`. Templates are cached when the server runs with `--noreload`: restart it after changing any template.
- **A fresh worktree** has none of the checkout's environments: link them from the main checkout (`ln -s <main checkout>/venv venv`, and the same for `site-tests/.venv` and `site-tests/node_modules`). The links show as untracked files; the commit steps name their files, so never `git add -A`.
- **Words changed on main in a page this PR deletes:** before Tasks 2, 4, 5 and 8 (each deletes an old page), run `git fetch origin && git log --oneline HEAD..origin/main -- backend/templates/about.html backend/templates/trust.html backend/templates/plans.html frontend/src/Landing.jsx`. If it lists a commit, read its diff and carry the changed wording into the chapter you are writing, and record a ruling.
- **Never** point a test or a check at veilfile.com, contact production, Stripe or Render, add a runtime dependency to `backend/requirements.txt`, or change package versions.
- **Owner-only:** legal documents (`backend/legal/documents/*`) and the privacy policy do not change. `robots.txt` does not change.
- **The funnel on `/` is unchanged** (spec 6.6): the landing view stays named `landing`, keeps `@funnel.sets_funnel_cookie`, and calls `funnel.record(request, funnel.LANDING_VISIT, once_per_session=True)` before rendering. It must not touch the session or render `{% csrf_token %}`.
- **React stays for `/app/*`** until PR 6. In this PR the only frontend changes are: remove the `/` route and `Landing.jsx`, and turn the two Dashboard wordmark `<Link to="/">` elements into `<a href="/">`. Do not touch `frontend/index.html` (the dashboard is served from it, and the price test reads its JSON-LD).
- **Components** (spec 6.2): each new component has its template in `templates/components/` whose first two lines are `{# Component: ... #}` and `{# Inputs: ... #}`, exactly one stylesheet `static/css/components/<name>.css` linked from `shell.html` as `{% static 'css/components/<name>.css' %}`, an `Entry` in `pages/catalog.py`, a rendering test, and is included only as `{% include "components/<name>.html" with ... only %}`. A component whose markup carries ids takes an `id_prefix` input.
- **Templates:** no `style=` attributes; every `{# #}` comment fits on one line (a multi-line one prints into the page); every character must be drawable by all three faces (the content checks test this); no colour values outside `static/css/tokens.css` (a mask's opaque stop is `var(--mask-solid)`).
- **Layering:** nothing inside `<main>` may use a `z-index` above 1 (the corners sit at 3, the narrow-screen top veil at 2).
- **Corners** (spec 4.1, as amended 2026-10-09): from 64rem up the four corners stay fixed and the page runs between them (`.shell-main` is padded by `--corner-reach`); below 64rem the bottom corners wait at the end of the page and a veil fades the page out along the top. Every layout fills `.shell-main`'s content box and never sets its own inline padding.
- **Motion:** cross-fades take `--fade` (0.7 s) and are only ever opacity: nothing slides. When `<html data-motion="off">` or the system asks for reduced motion, every change is instant, and no script may wait for a `transitionend` (transitions are switched off then, so it never fires).
- **Voice** (spec 5): plain, calm, specific; no em dash (U+2014), no exclamation marks, none of `genuinely`, `utilize`, `innovative`, `innovation`, `passionate about`, `excited to share`, `powerful force`, `leverage`, `great `. Typographic apostrophes are `’` (U+2019).
- **Product facts** (spec 5): the chapters state only these, worded as in this plan: the 256-bit token in the address is the only key; there is no public list of files and nothing is indexed; uploads are checked for anything shaped like a secret and flagged; when its days run out the link stops working at once and a sweep deletes the file from storage, usually within a day; file contents are never logged. Never claim that every MCP client signs in: clients that support MCP sign-in (OAuth) ask to sign in and need a verified email; the others take an API key.
- **Plans copy comes from `backend/drop_core/plans.py`.** No chapter template contains a price, an upload count, a link life, a storage size or a key count as literal text.
- **Registry:** titles at most 60 characters, descriptions 70 to 160.
- **JavaScript budget** (spec 8): `shell.js` plus `js/behaviors/*.js` at most 10 KB gzipped, enforced by a test from Task 6.

## Review Focus

These are the conditions a visitor can bring that no task's main tests exercise. Each has a test in the task that owns the code.

1. **A second click while a change is still running** (a menu link clicked during a cross-fade, Come in clicked twice). The last click wins; the page never ends half-swapped or with `<main>` stuck faded out. Tests: Task 9's rapid-clicks check, Task 8's double-click check.
2. **Back and forward through in-place changes**, including back to the threshold after Come in and back across a full load to a page of another layout. The address, title, content and the menu's current mark always agree. Test: Task 9's history check.
3. **A browser that won't let the page write to the clipboard** (an insecure origin, a denied permission, an older browser). Copy selects the command and says so, shows the follow-up line, and logs no error. Test: Task 8's clipboard-denied run.
4. **An in-place fetch that fails or returns something else** (a network error, a server error, a redirect to sign in, a page in another layout). The visitor gets an ordinary page load, never an error, a blank page or a faded-out `<main>`. A link out of the chapters (the dashboard, sign-up, the Docs) is never fetched first, so no page is requested twice. Tests: Task 9's failure checks (an aborted fetch, a 500 and a redirect) and its dashboard check.
5. **The narrowest phones (320px) with the long MCP address and Begin's long commands.** Commands scroll inside their own boxes; the page itself never scrolls sideways. Tests: Task 3 adds the sideways-scroll check to the small-viewport runs, which every page in `PAGES` goes through; the threshold's install line is hidden at load, so Task 8 repeats that check at 320px with the install line showing, and Task 6's `install_line_problems` fails when the install box is wider than the space it sits in. A command box that can scroll (a `<pre>`, the install line's `<code>`) carries `tabindex="0"`, or axe fails with `scrollable-region-focusable`.

## Rulings made while planning

Each is a decision the spec left open; the executor follows them.

- **Head metadata:** a context processor, `pages.context_processors.shell_page`, looks the request's URL name up in the registry; `shell.html` renders the title, description, canonical address, Open Graph tags and JSON-LD from it. Everything that changes from page to page in `<head>` carries `data-page-meta`, so in-place changes can replace exactly that set.
- **Layout family:** `Page.layout` in the registry (`"chapter"` for the threshold and the chapters) is written to `<body data-layout>`. In-place changes happen only between two pages whose `data-layout` values are equal and not empty. A page with a layout lists every registry page of that layout in `<body data-layout-paths>` (Task 9), and `shell.js` changes in place only to those addresses: any other link is an ordinary page load and is never fetched first.
- **Structured data:** `Organization` on every registry page; `Product` with one `Offer` per plan on Plans, built from `PLANS`. The landing page's old `SoftwareApplication` JSON-LD stays only in `frontend/index.html`, which now serves `/app/*` alone.
- **Open Graph image:** `img/og.png`, 1200 by 630, the mark centred on paper, drawn by `tools/make_images.py`.
- **Behaviours load on every shell page** as module scripts and work by event delegation on `document`, so content swapped in later needs no re-attaching.
- **Words in chapters** sit between two single-line comments, `{# Words: edit the text between here and the end marker; keep every tag, class and template tag as it is. #}` and `{# End of words. #}`; a test requires both in every chapter template. This is what spec 7.1's "marked blocks" means.
- **Copy refinements for accuracy** (spec 4.3 drafts, checked against the code): "nothing is listed" becomes "there is no public list of files"; "the file is deleted" gains "from storage, usually within a day"; the Promises line "We look for secrets, not at your files" becomes "We look only for the shapes of secrets", because the scanner does read the bytes.
- **Begin's client snippets** are the ones already in `backend/docs/pages/agent.md`, copied exactly, and a test keeps them equal. The Claude apps get one sentence ("add a custom connector with the address above, and sign in when it asks"); the repo has no verified snippet for them, so the owner confirms it in the voice pass (PR 7).
- **Old pages:** `about.html`, `plans.html`, `trust.html`, the old landing route and `Landing.jsx` are deleted, with the tests that asserted their markup. Requirements those tests carried that still apply (voice rules, About under 300 words and naming no person, the three sign-up links, "The price you see is the price you pay.") move into `backend/pages/tests_chapters.py`. The legacy includes (`includes/ambient.html` and the rest) stay: other pages still use them until PRs 4 to 6.
- **`/trust/`** answers a permanent (301) redirect to `/promises/`, from a view outside the registry.
- **The cloth's parts are PR 3:** the clearing that opens behind the install box after Come in, the cloth slowing, and the clearings following a chapter change. This PR builds the still page they will sit on.

---

## File structure

```
backend/
  config/settings.py                  add the shell_page context processor
  config/urls.py                      chapter routes, /trust/ redirect, landing from pages
  drop_core/views.py                  remove landing, plans, trust, about
  pages/
    context_processors.py             NEW: shell_page
    install.py                        NEW: the install line's three tabs
    registry.py                       Page.layout; how, begin, promises; MENU[0]
    views.py                          landing, chapter, plans
    templatetags/shell.py             structured_data tag
    catalog.py                        install_line and plan entries
    tests_head.py                     NEW
    tests_chapters.py                 NEW
    tests_install_line.py             NEW
  templates/
    shell.html                        head from the registry, data-layout, preload_reading, behaviours
    layouts/chapter.html              NEW
    chapters/threshold.html about.html how.html begin.html promises.html plans.html   NEW
    components/install_line.html plan.html                                           NEW
    about.html plans.html trust.html  DELETED
  static/
    css/tokens.css base.css           chapter tokens and rules
    css/components/install_line.css plan.css                                         NEW
    js/shell.js                       in-place chapter changes
    js/behaviors/tabs.js copy.js come-in.js                                          NEW
    img/og.png                        NEW (generated)
frontend/src/App.jsx Dashboard.jsx    the / route goes; wordmark links become <a href="/">
frontend/src/Landing.jsx              DELETED
site-tests/run_site_checks.py         chapter pages, Come in, Copy, in-place changes
tools/make_images.py                  og.png
docs/llms.txt AGENTS.md README.md     pages for people, recipes, repo map
```

---
### Task 1: Every registry page's head from the registry, the link-preview image, and the layout attribute

**Files:**
- Modify: `backend/pages/registry.py` (the `Page` dataclass)
- Create: `backend/pages/context_processors.py`
- Modify: `backend/config/settings.py` (`TEMPLATES[0]["OPTIONS"]["context_processors"]`)
- Modify: `backend/pages/templatetags/shell.py` (add `structured_data`)
- Modify: `backend/templates/shell.html` (title, head metadata, `preload_reading` block, `data-layout`)
- Modify: `tools/make_images.py`; generate `backend/static/img/og.png`
- Create: `backend/pages/tests_head.py`
- Modify: `backend/pages/tests_images.py`

**Interfaces:**
- Consumes: `registry.page(url_name)`, `registry.canonical_url(url_name)`, `registry.SITE_ORIGIN` (PR 1).
- Produces:
  - `Page.layout: str = ""`; the value `"chapter"` marks the threshold and the chapters.
  - Template context variable `shell_page`: `None`, or a dict with `title`, `description`, `canonical` (absolute), `layout`, `image` (absolute URL of `img/og.png`).
  - Template tag `{% structured_data %}` (library `shell`): one `<script type="application/ld+json" data-page-meta>` per item, `Organization` first, then each dict in the context's optional `structured_data` list.
  - In `shell.html`: every per-page `<head>` element carries `data-page-meta`; `<body>` carries `data-layout="<layout>"` when the page's layout is not empty; `{% block preload_reading %}` holds the Newsreader preload.
  - `static/img/og.png`, 1200 by 630.

- [ ] **Step 1: Write the failing tests**

Create `backend/pages/tests_head.py`:

```python
"""Every registry page's <head> comes from the registry (spec 6.5, 6.6)."""
import json
import re

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve
from django.utils.html import escape

from pages import registry

LD_JSON = re.compile(r'<script type="application/ld\+json" data-page-meta>(.*?)</script>', re.DOTALL)


def render_shell_as(path, context=None):
    """shell.html as it renders for path: the request carries the URL name the registry is keyed by."""
    request = RequestFactory().get(path)
    request.user = AnonymousUser()
    request.resolver_match = resolve(path)
    return render_to_string("shell.html", context or {}, request=request)


class HeadFromRegistryTests(SimpleTestCase):
    def test_a_registry_page_gets_its_title_description_and_address(self):
        html = render_shell_as("/about/")
        page = registry.page("about")
        canonical = registry.canonical_url("about")
        self.assertIn(f"<title>{escape(page.title)}</title>", html)
        for tag in (
            f'<meta name="description" content="{escape(page.description)}" data-page-meta>',
            f'<link rel="canonical" href="{canonical}" data-page-meta>',
            '<meta property="og:type" content="website" data-page-meta>',
            '<meta property="og:site_name" content="Veilfile" data-page-meta>',
            f'<meta property="og:title" content="{escape(page.title)}" data-page-meta>',
            f'<meta property="og:description" content="{escape(page.description)}" data-page-meta>',
            f'<meta property="og:url" content="{canonical}" data-page-meta>',
            '<meta property="og:image" content="https://veilfile.com/static/img/og.png" data-page-meta>',
            '<meta name="twitter:card" content="summary_large_image" data-page-meta>',
        ):
            with self.subTest(tag=tag):
                self.assertIn(tag, html)

    def test_every_registry_page_says_who_it_belongs_to(self):
        items = [json.loads(block) for block in LD_JSON.findall(render_shell_as("/about/"))]
        self.assertEqual(items[0]["@type"], "Organization")
        self.assertEqual(items[0]["url"], "https://veilfile.com/")
        self.assertEqual(items[0]["logo"], "https://veilfile.com/static/img/apple-touch-icon.png")

    def test_a_view_can_add_structured_data(self):
        html = render_shell_as("/about/", {"structured_data": [{"@type": "Product", "name": "Veilfile"}]})
        self.assertEqual([json.loads(block)["@type"] for block in LD_JSON.findall(html)], ["Organization", "Product"])

    def test_structured_data_cannot_close_its_script(self):
        html = render_shell_as("/about/", {"structured_data": [{"name": "</script><b>"}]})
        self.assertNotIn("</script><b>", html)
        self.assertIn("\\u003c/script\\u003e\\u003cb\\u003e", html)

    def test_a_page_outside_the_registry_gets_a_bare_head(self):
        html = render_shell_as("/app/components/")
        self.assertIn("<title>Veilfile</title>", html)
        self.assertNotIn("data-page-meta", html)
        self.assertNotIn("data-layout", html)

    def test_the_reading_face_preload_sits_in_a_block_pages_can_empty(self):
        shell = (settings.BASE_DIR / "templates" / "shell.html").read_text(encoding="utf-8")
        self.assertIn("{% block preload_reading %}<link rel=\"preload\" href=\"{% static 'fonts/newsreader/normal-400-500-latin.woff2' %}\"", shell)
```

Add to `backend/pages/tests_images.py`, inside `class MarkTests`, after `test_png_favicons_draw_the_grid_on_paper`:

```python
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_head pages.tests_images -v 2`
Expected: FAIL for every `tests_head` test except `test_a_page_outside_the_registry_gets_a_bare_head` (it passes); `test_every_registry_page_says_who_it_belongs_to` is an ERROR, not a FAIL (`IndexError: list index out of range`, since no JSON-LD is rendered yet), and the other `tests_head` tests fail because no description, canonical or Open Graph tag is rendered and the preload is not in a block. Also ERROR for `test_link_preview_image_is_the_mark_on_paper` with `FileNotFoundError` (`og.png` does not exist).

- [ ] **Step 3: Add the layout field, the context processor and the tag**

In `backend/pages/registry.py`, replace the `Page` dataclass with:

```python
@dataclass(frozen=True)
class Page:
    url_name: str
    title: str
    description: str
    renders_metadata: bool = True  # False only while a page's <head> lives outside Django templates
    in_sitemap: bool = True
    layout: str = ""  # "chapter" for the threshold and the chapters; in-place changes stay within one layout
```

Create `backend/pages/context_processors.py`:

```python
"""The registry entry for the page being rendered, for the <head> in shell.html (spec 6.5, 6.6)."""
from django.templatetags.static import static

from pages import registry


def shell_page(request):
    """{"shell_page": None} for a page outside the registry, else its title, description, canonical
    address, layout and link-preview image, every address absolute."""
    match = getattr(request, "resolver_match", None)
    name = getattr(match, "url_name", None)
    try:
        page = registry.page(name) if name else None
    except KeyError:
        page = None
    if page is None:
        return {"shell_page": None}
    return {"shell_page": {
        "title": page.title,
        "description": page.description,
        "canonical": registry.canonical_url(page.url_name),
        "layout": page.layout,
        "image": registry.SITE_ORIGIN + static("img/og.png"),
    }}
```

In `backend/config/settings.py`, add `"pages.context_processors.shell_page",` as the last item of the `context_processors` list (after `"django.contrib.messages.context_processors.messages",`).

In `backend/pages/templatetags/shell.py`, replace the imports at the top with:

```python
"""Template tags for pages on the new shell."""
import json

from django import template
from django.templatetags.static import static
from django.utils.safestring import mark_safe

from pages import registry

register = template.Library()

# JSON-LD sits inside <script>: these three must never appear raw in it.
_SCRIPT_SAFE = {ord("<"): "\\u003c", ord(">"): "\\u003e", ord("&"): "\\u0026"}
```

and add at the end of the file:

```python
@register.simple_tag(takes_context=True)
def structured_data(context):
    """The page's JSON-LD (spec 6.6): Organization on every registry page, then whatever the view
    passes as structured_data (a list of dicts). Each is its own script, marked for in-place changes."""
    organization = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Veilfile",
        "url": registry.SITE_ORIGIN + "/",
        "logo": registry.SITE_ORIGIN + static("img/apple-touch-icon.png"),
    }
    items = [organization, *(context.get("structured_data") or ())]
    return mark_safe("".join(
        '<script type="application/ld+json" data-page-meta>'
        + json.dumps(item, ensure_ascii=False).translate(_SCRIPT_SAFE)
        + "</script>\n"
        for item in items
    ))
```

- [ ] **Step 4: Render the head and the layout from the registry**

In `backend/templates/shell.html`, replace

```html
<title>{% block title %}Veilfile{% endblock %}</title>
{% block meta %}{% endblock %}
```

with

```html
<title>{% block title %}{{ shell_page.title|default:"Veilfile" }}{% endblock %}</title>
{% if shell_page %}<meta name="description" content="{{ shell_page.description }}" data-page-meta>
<link rel="canonical" href="{{ shell_page.canonical }}" data-page-meta>
<meta property="og:type" content="website" data-page-meta>
<meta property="og:site_name" content="Veilfile" data-page-meta>
<meta property="og:title" content="{{ shell_page.title }}" data-page-meta>
<meta property="og:description" content="{{ shell_page.description }}" data-page-meta>
<meta property="og:url" content="{{ shell_page.canonical }}" data-page-meta>
<meta property="og:image" content="{{ shell_page.image }}" data-page-meta>
<meta name="twitter:card" content="summary_large_image" data-page-meta>
{% structured_data %}<link rel="alternate" type="application/rss+xml" title="Veilfile Journal" href="{% url 'blog-feed' %}">
<link rel="alternate" type="text/plain" title="Veilfile agent docs (llms.txt)" href="{% url 'llms-txt' %}">
{% endif %}{% block meta %}{% endblock %}
```

Replace

```html
<link rel="preload" href="{% static 'fonts/newsreader/normal-400-500-latin.woff2' %}" as="font" type="font/woff2" crossorigin>
```

with

```html
{# Pages with no reading text (the threshold) empty this block and skip the download. #}
{% block preload_reading %}<link rel="preload" href="{% static 'fonts/newsreader/normal-400-500-latin.woff2' %}" as="font" type="font/woff2" crossorigin>{% endblock %}
```

Replace

```html
<body class="{% block body_class %}{% endblock %}">
```

with

```html
<body class="{% block body_class %}{% endblock %}"{% if shell_page.layout %} data-layout="{{ shell_page.layout }}"{% endif %}>
```

- [ ] **Step 5: Draw the link-preview image**

In `tools/make_images.py`, add after `mark_tile`:

```python
def mark_card(width, height, mark):
    """The mark, `mark` px square, centred in ink on a paper card (the link-preview image)."""
    cell, left, top = mark // 4, (width - mark) // 2, (height - mark) // 2

    def pixel(x, y):
        if 0 <= x - left < mark and 0 <= y - top < mark and MARK_ROWS[(y - top) // cell][(x - left) // cell] == "#":
            return (*INK, 255)
        return (*PAPER, 255)

    return pixel
```

In `main()`, add before the `grain.png` line:

```python
    (IMG / "og.png").write_bytes(png(1200, 630, mark_card(1200, 630, 280)))
```

and change the final print to `print(f"Wrote seven images to {IMG}")`. Then run `python3 tools/make_images.py` from the repo root.
Expected: `Wrote seven images to .../backend/static/img`, and `git status` shows `backend/static/img/og.png` new and no other image changed (if another PNG shows as modified, your zlib differs: `git checkout` those files, as the tool's docstring says).

- [ ] **Step 6: Run the tests and watch them pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages -v 1`
Expected: OK. (`tests_shell` still passes: the bare shell keeps the Newsreader preload, and `RegistryMatchesRenderedPagesTests` is unchanged because no registry page renders `shell.html` yet.)

- [ ] **Step 7: Run the browser checks**

Restart the server (`shell.html` changed) and run the browser checks. Expected: `0 problem(s) across 1 page(s)` (`PAGES` holds only `/app/components/` until Task 3).

- [ ] **Step 8: Commit**

```bash
git add backend/pages/registry.py backend/pages/context_processors.py backend/config/settings.py \
  backend/pages/templatetags/shell.py backend/templates/shell.html tools/make_images.py \
  backend/static/img/og.png backend/pages/tests_head.py backend/pages/tests_images.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Take every shell page's head from the registry

A context processor hands shell.html the registry entry for the page being
rendered: title, description, canonical address, Open Graph tags with a new
link-preview image, and Organization JSON-LD, each marked data-page-meta so
in-place chapter changes can swap them. Registry pages gain a layout, written
to <body data-layout>.
EOF
```

---

### Task 2: The chapter layout and the About chapter

**Files:**
- Create: `backend/templates/layouts/chapter.html`, `backend/templates/chapters/about.html`, `backend/pages/tests_chapters.py`
- Modify: `backend/static/css/tokens.css`, `backend/static/css/base.css`
- Modify: `backend/pages/views.py` (add `chapter`), `backend/config/urls.py` (the `about/` route), `backend/pages/registry.py` (the `about` entry)
- Delete: `backend/templates/about.html`; the `about` view in `backend/drop_core/views.py`
- Modify (retire tests of the old page): `backend/drop_core/tests_site_pages.py`, `backend/drop_core/tests_menu.py`, `backend/drop_core/tests_ambient.py`

**Interfaces:**
- Consumes: `shell_page`, `data-layout`, `{% block preload_reading %}` (Task 1).
- Produces:
  - `layouts/chapter.html` with `{% block chapter %}`, inside `<div class="chapter">`; it preloads the display face and sets `body_class` to `layout-chapter`.
  - `pages.views.chapter(request, name)` renders `chapters/<name>.html`; routes pass `{"name": ...}`.
  - CSS classes `chapter`, `chapter__title`, `chapter__heading`, `chapter__label`, `chapter__steps`, `chapter__more`; tokens `--display-size`, `--title-size`, `--chapter-measure`, `--fade`.
  - The two words markers (see the Rulings) and `backend/pages/tests_chapters.py` with `chapter_pages()` and `visible_text(html)` helpers that later tasks extend.

- [ ] **Step 1: Write the failing tests**

Create `backend/pages/tests_chapters.py`:

```python
"""The chapters (spec 4.3): real pages on the shell, their words in templates/chapters/, in the site's voice."""
import re
from html import unescape
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from pages import registry

CHAPTERS = Path(settings.BASE_DIR) / "templates" / "chapters"
WORDS_START = "{# Words: edit the text between here and the end marker; keep every tag, class and template tag as it is. #}"
WORDS_END = "{# End of words. #}"
BANNED = ("genuinely", "utilize", "innovative", "innovation", "passionate about", "excited to share",
          "powerful force", "leverage", "great ")


def chapter_pages():
    """The registry pages in the chapter layout: the threshold and every chapter."""
    return [page for page in registry.PAGES if page.layout == "chapter"]


def visible_text(html):
    """The words a visitor reads in <main>, tags removed and spaces collapsed."""
    main = html[html.index("<main"):html.index("</main>")]
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", main))).strip()


class ChapterPagesTests(TestCase):
    def test_every_chapter_renders_on_the_shell_in_the_chapter_layout(self):
        self.assertTrue(chapter_pages(), "no registry page has layout='chapter'")
        for page in chapter_pages():
            with self.subTest(page=page.url_name):
                response = self.client.get(reverse(page.url_name))
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                self.assertIn('data-layout="chapter"', html)
                self.assertIn('<div class="chapter">', html)
                self.assertRegex(html[html.index("<main"):], r"<h1[ >]")

    def test_chapter_templates_mark_their_words_and_keep_the_voice(self):
        templates = sorted(CHAPTERS.glob("*.html"))
        self.assertTrue(templates, "no templates in templates/chapters/")
        for path in templates:
            text = path.read_text(encoding="utf-8")
            where = f"backend/templates/chapters/{path.name}"
            with self.subTest(template=path.name):
                self.assertIn(WORDS_START, text, f"{where}: put the page's words after {WORDS_START}")
                self.assertIn(WORDS_END, text, f"{where}: end the page's words with {WORDS_END}")
                self.assertNotIn("—", text, f"{where}: no em dashes; use a colon, a comma or two sentences")
                self.assertNotIn("!", text, f"{where}: no exclamation marks")
                for word in BANNED:
                    self.assertNotIn(word, text.lower(), f"{where}: avoid {word.strip()!r} (AGENTS.md, voice)")


class AboutTests(TestCase):
    def test_about_is_short_and_says_what_veilfile_is(self):
        text = visible_text(self.client.get(reverse("about")).content.decode())
        self.assertIn("A quiet place for what your agents make.", text)
        self.assertLess(len(text.split()), 300)
```
The names check that this class once held is now CI's names job, which reads the names from a repository secret.

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_chapters -v 2`
Expected: FAIL for all three. `test_every_chapter_renders_on_the_shell_in_the_chapter_layout` fails with `no registry page has layout='chapter'`; `test_chapter_templates_mark_their_words_and_keep_the_voice` with `no templates in templates/chapters/`; the About test with an `AssertionError` (the old page says something else).

- [ ] **Step 3: Add the chapter tokens and rules**

In `backend/static/css/tokens.css`, in the `:root` block, replace

```css
  --fill-fade: .15s;
```

with

```css
  --fill-fade: .15s;
  --display-size: clamp(26px, 3.6vw, 52px); /* the threshold line */
  --title-size: clamp(28px, 2.9vw, 44px); /* a chapter's title */
  --chapter-measure: 40rem; /* a chapter's column */
  --fade: .7s; /* cross-fades: Come in and chapter changes (shell.js and come-in.js wait this long) */
```

Append to the end of `backend/static/css/base.css`:

```css
/* Chapters (spec 4.3) and the threshold (4.2): one block, centred both ways in the space the page
   has (between the corners from 64rem up); a long chapter grows and the page scrolls. */
.layout-chapter .shell-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  place-items: center;
}

.chapter {
  width: 100%;
  max-width: var(--chapter-measure);
  font-variant-numeric: lining-nums;
}

.chapter__title {
  margin: 0 0 var(--space-4);
  font-family: var(--font-display);
  font-style: italic;
  font-weight: 300;
  font-size: var(--title-size);
  line-height: 1.15;
  text-wrap: balance;
}

.chapter > p {
  margin: 0 0 var(--space-3);
}

.chapter__heading {
  margin: var(--space-5) 0 var(--space-3);
  font-family: var(--font-display);
  font-style: italic;
  font-weight: 400;
  font-size: 1.45em;
  line-height: 1.2;
}

.chapter__label {
  margin: var(--space-4) 0 var(--space-2);
  font-weight: 400;
}

.chapter__steps {
  margin: 0;
}

.chapter__steps dt {
  font-family: var(--font-display);
  font-style: italic;
  font-weight: 400;
  font-size: 1.25em;
}

.chapter__steps dd {
  margin: 0 0 var(--space-4);
}

/* Inline links: a dotted underline that gives way to the flat fill (spec 3.4). */
.chapter :is(p, dd) a {
  text-decoration: underline dotted var(--muted);
  text-decoration-thickness: 1px;
  text-underline-offset: .22em;
  transition: background-color var(--fill-fade) ease, text-decoration-color var(--fill-fade) ease;
}

.chapter :is(p, dd) a:is(:hover, :focus-visible) {
  background-color: var(--fill);
  text-decoration-color: transparent;
}

.chapter code {
  font-family: var(--font-mono);
  font-size: .8em;
}

.chapter pre {
  margin: 0 0 var(--space-3);
  padding: var(--space-3);
  overflow-x: auto;
  border: 1px solid var(--hair);
  background: var(--box);
}

.chapter pre code {
  font-size: .75em;
  line-height: 1.7;
}
```

- [ ] **Step 4: Add the layout and the About chapter**

Create `backend/templates/layouts/chapter.html`:

```html
{% extends "shell.html" %}
{% load static %}
{# Layout: chapter. The threshold and the chapters: one block, centred in the page (spec 6.2). #}
{% block preload_display %}<link rel="preload" href="{% static 'fonts/cormorant-garamond/italic-300-latin.woff2' %}" as="font" type="font/woff2" crossorigin>{% endblock %}
{% block body_class %}layout-chapter{% endblock %}
{% block main %}<div class="chapter">{% block chapter %}{% endblock %}</div>{% endblock %}
```

Create `backend/templates/chapters/about.html`:

```html
{% extends "layouts/chapter.html" %}
{% block chapter %}
{# Words: edit the text between here and the end marker; keep every tag, class and template tag as it is. #}
<h1 class="chapter__title">A quiet place for what your agents make.</h1>
<p>Agents make things all day: a screenshot from a failing test, a bundle of logs, a report written in the middle of the night. Most of it needs to be seen by one or two people, for a little while.</p>
<p>Veilfile gives each file a private link, and lets it go when its time is up. No public repository, no bucket to lock down, no paste that lives forever.</p>
{# End of words. #}
{% endblock %}
```

- [ ] **Step 5: Route About to the new view and take its head from the registry**

Append to `backend/pages/views.py`:

```python
def chapter(request, name):
    """A chapter (spec 4.3): templates/chapters/<name>.html; its <head> comes from the registry."""
    return render(request, f"chapters/{name}.html")
```

In `backend/config/urls.py`, replace

```python
    path('about/', core_views.about, name='about'),
```

with

```python
    path("about/", pages_views.chapter, {"name": "about"}, name="about"),
```

In `backend/pages/registry.py`, replace the `about` entry

```python
    Page("about", "About | Veilfile",
         "Veilfile is private, expiring artifact hosting for AI agents: one upload, one unguessable link, "
         "dead on schedule. Built and run by a one-person company."),
```

with

```python
    Page("about", "About | Veilfile",
         "Veilfile gives each file your agents make a private link, and lets it go when its time is up. "
         "No public repository, no bucket, no paste that lives forever.",
         layout="chapter"),
```

In `backend/drop_core/views.py`, delete the `about` view (the three lines from `def about(request):` to `return render(request, "about.html")` and the two blank lines after it). Delete `backend/templates/about.html` (`git rm backend/templates/about.html`).

- [ ] **Step 6: Retire the tests of the old About page**

- `backend/drop_core/tests_site_pages.py`: delete the class `AboutPageTests`, from the line `class AboutPageTests(TestCase):` up to (not including) `class MenuJourneyTests(TestCase):`. Its requirements that still apply (under 300 words, names no person, the voice rules) are now in `pages/tests_chapters.py`.
- `backend/drop_core/tests_menu.py`: in both `MENU_PAGES` and `FOOTER_PAGES`, delete the line `    "/about/",`. Leave `MENU_LINKS` alone: the old pages' menu still links to `/about/`, which now answers with the chapter. Also in `backend/drop_core/tests_menu.py`, replace both `self.client.get("/about/")` calls (in `SharedMenuTests.test_menu_keyboard_behavior` and `test_menu_uses_menuitem_roles`, which assert the old menu's `"Escape"` handler and `role="menuitem"`) with `self.client.get("/terms/")`; without that Step 7 fails with two failures.
- `backend/drop_core/tests_ambient.py`: in `FIELD_PAGES` delete the line `    ("/about/", False),`, and replace every `self.client.get("/about/")` with `self.client.get("/terms/")` (`/terms/` is still an old-style page and carries everything those tests assert).

- [ ] **Step 7: Run the tests and watch them pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages drop_core.tests_site_pages drop_core.tests_menu drop_core.tests_ambient -v 1`
Expected: OK. `pages.tests_registry.RegistryMatchesRenderedPagesTests` now checks About's head as rendered from the registry, and `pages.tests_site_rules` checks `layouts/` and `chapters/` (no inline styles, drawable characters).
Then run the full suite. Expected: OK.

- [ ] **Step 8: Commit**

```bash
git add backend/static/css/tokens.css backend/static/css/base.css backend/templates/layouts/chapter.html \
  backend/templates/chapters/about.html backend/pages/views.py backend/config/urls.py backend/pages/registry.py \
  backend/drop_core/views.py backend/pages/tests_chapters.py backend/drop_core/tests_site_pages.py \
  backend/drop_core/tests_menu.py backend/drop_core/tests_ambient.py
# backend/templates/about.html is already staged as deleted by Step 5's git rm
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Move About onto the shell as the first chapter

A chapter layout centres one block in the page; About is its first chapter,
with its words between two marker comments and its head from the registry.
The old about.html and the tests of its markup go; the requirements they
carried (short, names no person, the voice rules) move to tests_chapters.
EOF
```

---

### Task 3: Browser checks that know chapter pages

**Files:**
- Modify: `site-tests/run_site_checks.py`

**Interfaces:**
- Consumes: the About chapter (Task 2); `shell.js` pins the menu open on a page whose menu has an `aria-current="page"` link, from 64rem up (PR 1).
- Produces:
  - `SitePage(path, name, signed_in=False, chapter=False)`; `chapter=True` means the menu should start open on wide screens.
  - The font check covers only the families a page sets visible text in, so a page with no display text passes.
  - The menu checks close a pinned menu before testing MENU, and open the menu only when it is closed.
  - The real-chapter check replaces the PR 1 run that faked a current link: on a chapter page the menu starts open at 1280px, a click on the page leaves it open, the current link's fill is the inset mark, and no page content shows under the open column; on any other page the menu starts closed.
  - The small-viewport runs (320x640, 667x375, 900x500) also fail on sideways scrolling (Review Focus 5).

- [ ] **Step 1: Add About to the pages and watch the old assumptions fail**

In `site-tests/run_site_checks.py`, replace the `SitePage` dataclass and `PAGES` with:

```python
@dataclass(frozen=True)
class SitePage:
    path: str
    name: str  # used in screenshot file names
    signed_in: bool = False
    chapter: bool = False  # a chapter: from 64rem up its menu starts open, marking it


# Every page that uses the new shell. Add each page here as it moves onto the shell.
PAGES = (
    SitePage("/app/components/", "component-catalog", signed_in=True),
    SitePage("/about/", "about", chapter=True),
)
```

Run the browser checks (Global Constraints; restart the server first, since Task 2 changed templates).
Expected: exit 1 as a Python traceback ending `TypeError: 'NoneType' object is not subscriptable` in `content_shows_under`, called from `menu_problems` (the menu was already open, so the MENU click closed it and `#shell-menu` has no bounding box); no problem list is printed. `/app/components/` ran without trouble before the crash.

- [ ] **Step 2: Teach the checks about chapter pages**

Add after the `CORNER_ROWS` constant:

```python
# The font families a page sets visible text in (the first family of each element with text).
USED_FAMILIES = """() => { const used = new Set();
  for (const el of document.querySelectorAll("body *")) {
    if (!el.getClientRects().length || getComputedStyle(el).visibility === "hidden") continue;
    if (![...el.childNodes].some((node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim())) continue;
    used.add(getComputedStyle(el).fontFamily.split(",")[0].replace(/["']/g, "").trim());
  }
  return [...used]; }"""
```

In `page_problems`, replace

```python
    page.evaluate("() => document.fonts.ready.then(() => true)")
    unloaded = page.evaluate(
        """(families) => families.filter((family) => ![...document.fonts].some(
            (face) => face.family.replace(/["']/g, "") === family && face.status === "loaded"))""",
        list(FONT_FAMILIES),
    )
```

with

```python
    page.evaluate("() => document.fonts.ready.then(() => true)")
    used = [family for family in page.evaluate(USED_FAMILIES) if family in FONT_FAMILIES]
    unloaded = page.evaluate(
        """(families) => families.filter((family) => ![...document.fonts].some(
            (face) => face.family.replace(/["']/g, "") === family && face.status === "loaded"))""",
        used,
    )
```

In `menu_problems`, replace

```python
    toggle = page.locator("header [data-menu-toggle]")
    toggle.click()
```

with

```python
    toggle = page.locator("header [data-menu-toggle]")
    if toggle.get_attribute("aria-expanded") == "true":  # a chapter page's menu starts open
        page.keyboard.press("Escape")
    toggle.click()
```

In `menu_reach_problems`, replace

```python
    page.locator("header [data-menu-toggle]").click()
```

with

```python
    toggle = page.locator("header [data-menu-toggle]")
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
```

In `edge_case_problems`, replace the whole block from the comment `# On a chapter page (a current link in the menu) the menu starts open on wider screens and` down to and including the `context.close()` that follows the `fills` check, with:

```python
    # On a chapter page the menu starts open on wide screens, marks the chapter, and stays open
    # while the visitor reads and clicks the page (spec 4.4); on any other page it starts closed.
    context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
    page, errors = open_page(context, url)
    label = f"{site_page.path} at 1280px, the menu at rest"
    menu_open = page.locator("#shell-menu").is_visible()
    if not site_page.chapter:
        if menu_open:
            problems.append(f"{label}: the menu starts open on a page that is not a chapter")
    elif not menu_open:
        problems.append(f"{label}: the menu does not start open on a chapter page")
    else:
        page.locator("main h1").click()
        if not page.locator("#shell-menu").is_visible():
            problems.append(f"{label}: a click on the page closes the menu that should stay open")
        # The current link is marked by the same inset fill as hover and focus (on ::before), never
        # by a fill on the link itself.
        fills = page.locator('#shell-menu a[aria-current="page"]').evaluate(
            "(a) => [getComputedStyle(a).backgroundColor, getComputedStyle(a, '::before').backgroundColor]")
        if fills[0] != "rgba(0, 0, 0, 0)" or fills[1] == "rgba(0, 0, 0, 0)":
            problems.append(f"{label}: the current link's fill is {fills[0]} on the link and {fills[1]} on its inset mark")
        if content_shows_under(page, page.locator("#shell-menu").bounding_box()):
            problems.append(f"{label}: page content shows under the open menu")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
```

In the small-viewport loop, after `label = f"{site_page.path} at {width}x{height}"`, add:

```python
        overflow = page.evaluate(OVERFLOW)
        if overflow > 0:
            problems.append(f"{label}: the page scrolls sideways by {overflow}px")
```

A chapter page is short (it does not scroll at 1023x768, 375x667 or 900x500), which the catalog page never is, and two checks assume a page that scrolls; without the next two edits Step 3 ends with `3 problem(s)`.

In `menu_reach_problems`, after the line `    controls = page.locator(".shell-corner--bottom-right").bounding_box()`, add:

```python
    if page.evaluate(COVERED, "footer [data-motion-toggle]"):  # on a short page the open sheet is painted over them
        controls = None
```

In `edge_case_problems`, in the loop over the edge of the 64rem switch, replace

```python
        if not wide and not moved:
            problems.append(f"{label}: the bottom corners float over the page on a narrow screen; they should wait at its end")
```

with

```python
        # A short page does not scroll, so the corners cannot be seen to move: ask for their position.
        scrolls = page.evaluate("() => document.documentElement.scrollHeight > innerHeight")
        floats = not moved if scrolls else page.evaluate(
            "() => getComputedStyle(document.querySelector('.shell-corner--bottom-left')).position === 'fixed'")
        if not wide and floats:
            problems.append(f"{label}: the bottom corners float over the page on a narrow screen; they should wait at its end")
```

- [ ] **Step 3: Run the checks and watch them pass**

Run the browser checks.
Expected: `0 problem(s) across 2 page(s)`. Look at `site-tests/artifacts/about-1280-light-motion-on.png` and `about-375-light-motion-on.png`: the title and text centred in the column, the menu open beside it at 1280, closed at 375.

- [ ] **Step 4: Commit**

```bash
git add site-tests/run_site_checks.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Check chapter pages in the browser, starting with About

The checks learn that a chapter's menu starts open on wide screens, test
that against the real About page instead of a faked current link, check only
the fonts a page uses, and fail on sideways scrolling at 320px too.
EOF
```

---

### Task 4: How it works, Promises, and the move from /trust/

**Files:**
- Create: `backend/templates/chapters/how.html`, `backend/templates/chapters/promises.html`
- Modify: `backend/pages/registry.py` (add `how`, replace `trust` with `promises`, `MENU[0]`), `backend/config/urls.py`, `docs/llms.txt`
- Delete: `backend/templates/trust.html`; the `trust` view in `backend/drop_core/views.py`
- Modify (tests): `backend/pages/tests_chapters.py`, `backend/pages/tests_registry.py`, `backend/pages/tests_shell.py`, `backend/drop_core/tests_site_pages.py`, `backend/drop_core/tests_menu.py`, `backend/drop_core/tests_ambient.py`, `backend/drop_core/tests.py`
- Modify: `site-tests/run_site_checks.py` (`PAGES`)

**Interfaces:**
- Consumes: `layouts/chapter.html`, `pages.views.chapter`, `tests_chapters.visible_text` (Task 2); `SitePage(chapter=True)` (Task 3).
- Produces: URL names `how` (`/how/`) and `promises` (`/promises/`); `/trust/` answers 301 to `/promises/` (URL name `trust-redirect`, not in the registry); `MENU[0]` is About, How it works, Promises, Plans (Task 7 inserts Begin after How it works).

- [ ] **Step 1: Write the failing tests**

Append to `backend/pages/tests_chapters.py`:

```python
class HowAndPromisesTests(TestCase):
    def test_how_it_works_tells_the_life_of_a_file(self):
        text = visible_text(self.client.get(reverse("how")).content.decode())
        for step in ("Made", "Veiled", "Seen", "Let go"):
            self.assertIn(step, text)
        self.assertIn("The 256-bit token in the address is the only key", text)
        self.assertIn("deleted from storage soon after", text)

    def test_promises_say_what_we_keep_and_answer_why_not_a_gist(self):
        text = visible_text(self.client.get(reverse("promises")).content.decode())
        self.assertIn("The link is the only way in.", text)
        self.assertIn("File contents are never logged.", text)
        self.assertIn("Why not a gist?", text)

    def test_the_old_trust_address_moves_permanently_to_promises(self):
        response = self.client.get("/trust/")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/promises/")
```

In `backend/pages/tests_registry.py`, in `test_signed_out_menu`, change the expected first group from `["About", "Plans"]` to `["About", "How it works", "Promises", "Plans"]`.

In `backend/pages/tests_shell.py`, in `test_menu_lists_the_signed_out_groups_in_order`, change the expected list to `["About", "How it works", "Promises", "Plans", "Docs", "Journal", "Sign in", "Terms", "Privacy", "Report abuse", "llms.txt"]`.

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_chapters pages.tests_registry pages.tests_shell -v 1`
Expected: errors for the two chapter tests (`NoReverseMatch` for `how` and `promises`), a failure for the redirect (`/trust/` answers 200), and failures for the two menu tests (the menu still lists About and Plans only).

- [ ] **Step 3: Add the two chapters**

Create `backend/templates/chapters/how.html`:

```html
{% extends "layouts/chapter.html" %}
{% block chapter %}
{# Words: edit the text between here and the end marker; keep every tag, class and template tag as it is. #}
<h1 class="chapter__title">The life of a file</h1>
<dl class="chapter__steps">
<dt>Made</dt>
<dd>Your agent pushes a file through the command line, the API, or MCP.</dd>
<dt>Veiled</dt>
<dd>It gets back an unguessable link. The 256-bit token in the address is the only key. There is no public list of files, and nothing is indexed.</dd>
<dt>Seen</dt>
<dd>Share the link with whoever should see it. Uploads are checked for anything shaped like a secret, and flagged.</dd>
<dt>Let go</dt>
<dd>When its days run out, the link stops working at once, and the file is deleted from storage soon after.</dd>
</dl>
<p class="chapter__more">The technical version is in the Docs: <a href="{% url 'docs-page' 'how-it-works' %}">how it works, in full</a>.</p>
{# End of words. #}
{% endblock %}
```

Create `backend/templates/chapters/promises.html`:

```html
{% extends "layouts/chapter.html" %}
{% block chapter %}
{# Words: edit the text between here and the end marker; keep every tag, class and template tag as it is. #}
<h1 class="chapter__title">What we promise</h1>
<dl class="chapter__steps">
<dt>The link is the only way in.</dt>
<dd>No public list, no search, no addresses anyone could guess.</dd>
<dt>Gone means deleted.</dt>
<dd>When a file expires its link stops working, and a sweep removes the file from storage, usually within a day. It is not hidden; it is gone.</dd>
<dt>We look only for the shapes of secrets.</dt>
<dd>Uploads are scanned for anything shaped like a key or a token, and a match is flagged by name. File contents are never logged.</dd>
<dt>Why not a gist?</dt>
<dd>A gist is public by default and lives forever. Fine for a snippet; wrong for a client’s failing screenshot. See <a href="{% url 'how' %}">how a file lives here</a>.</dd>
</dl>
{# End of words. #}
{% endblock %}
```

- [ ] **Step 4: Register them, route them, and move /trust/**

In `backend/pages/registry.py`, insert after the `about` entry:

```python
    Page("how", "How it works | Veilfile",
         "The life of a file on Veilfile: your agent pushes it, gets back an unguessable link, shares it, "
         "and the file is deleted when its days run out.",
         layout="chapter"),
```

and replace the `trust` entry

```python
    Page("trust", "Why not a gist? | Veilfile",
         "A gist is public by default and lives forever. Veilfile is the boring, correct option: "
         "one upload, one unguessable link, dead on schedule."),
```

with

```python
    Page("promises", "What we promise | Veilfile",
         "What Veilfile promises: the link is the only way in, expired files are deleted from storage, "
         "and uploads are scanned for secrets without logging contents.",
         layout="chapter"),
```

Change `MENU[0]` and the comment above `MENU` to:

```python
# The menu's three groups (spec 4.4). MENU[0] is the chapters, in the order the menu shows them.
MENU = (
    (MenuLink("About", "about"), MenuLink("How it works", "how"), MenuLink("Promises", "promises"),
     MenuLink("Plans", "plans")),
```

(the other two groups stay as they are).

In `backend/config/urls.py`, replace

```python
    path('trust/', core_views.trust, name='trust'),
```

with

```python
    path("how/", pages_views.chapter, {"name": "how"}, name="how"),
    path("promises/", pages_views.chapter, {"name": "promises"}, name="promises"),
    # Old address of Promises (spec 4.3): permanent, so search engines move it over.
    path("trust/", RedirectView.as_view(url="/promises/", permanent=True), name="trust-redirect"),
```

In `backend/drop_core/views.py`, delete the `trust` view (from `def trust(request):` to `return render(request, "trust.html")` and the two blank lines after it), and `git rm backend/templates/trust.html`.

In `docs/llms.txt`, in `## Pages for people`, replace

```
- About: https://veilfile.com/about/
- Plans and limits: https://veilfile.com/plans/
```

with

```
- About: https://veilfile.com/about/
- How it works: https://veilfile.com/how/
- Promises: https://veilfile.com/promises/
- Plans and limits: https://veilfile.com/plans/
```

The old pages' menu (`templates/includes/menu.html`) still links "why not a gist" to `/trust/`; leave it, the redirect covers it, and `drop_core/tests_menu.py` pins that menu until those pages move.

- [ ] **Step 5: Retire the tests of the old Trust page**

- `backend/drop_core/tests_site_pages.py`: delete the class `TrustPageTests` (from `class TrustPageTests(TestCase):` up to, not including, `class DocsSetupFlowTests(TestCase):`), and delete the class `MenuJourneyTests` (from `class MenuJourneyTests(TestCase):` to the end of the file): it tested the old React menu and the old `/trust/` page, and the registry tests now cover the menu. In `LandingRedesignTests.test_sitemap_includes_new_pages`, change `"https://veilfile.com/trust/"` to `"https://veilfile.com/promises/"`.
- `backend/drop_core/tests_menu.py`: in both `MENU_PAGES` and `FOOTER_PAGES`, delete the line `    "/trust/",`.
- `backend/drop_core/tests_ambient.py`: in `FIELD_PAGES`, delete the line `    ("/trust/", True),`.
- `backend/drop_core/tests.py`: in `test_skip_link_and_main_id_on_django_pages`, delete the line `            "/trust/",` from `urls`.

- [ ] **Step 6: Run the tests and watch them pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages drop_core.tests_site_pages drop_core.tests_menu drop_core.tests_ambient drop_core.tests drop_core.tests_fix_docs -v 1`
Expected: OK (`pages.tests_site_rules.LlmsTxtTests` finds the new chapters in `llms.txt`; `pages.tests_sitemap` lists `/how/` and `/promises/` and not `/trust/`).
Then run the full suite. Expected: OK.

- [ ] **Step 7: Check them in the browser**

In `site-tests/run_site_checks.py`, add to `PAGES` after the About entry:

```python
    SitePage("/how/", "how", chapter=True),
    SitePage("/promises/", "promises", chapter=True),
```

Restart the server and run the browser checks. Expected: `0 problem(s) across 4 page(s)`.

- [ ] **Step 8: Commit**

```bash
git add backend/templates/chapters/how.html backend/templates/chapters/promises.html backend/pages/registry.py \
  backend/config/urls.py backend/drop_core/views.py docs/llms.txt backend/pages/tests_chapters.py \
  backend/pages/tests_registry.py backend/pages/tests_shell.py backend/drop_core/tests_site_pages.py \
  backend/drop_core/tests_menu.py backend/drop_core/tests_ambient.py backend/drop_core/tests.py site-tests/run_site_checks.py
# backend/templates/trust.html is already staged as deleted by Step 4's git rm
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Add the How it works and Promises chapters; /trust/ moves to /promises/

Both chapters state only what the product does, worded against the code: no
public list of files, deletion from storage soon after expiry, and only the
names of secret-shaped matches kept. /trust/ answers a permanent redirect.
EOF
```

---

### Task 5: The plan component and the Plans chapter, every figure from plans.py

**Files:**
- Create: `backend/pages/plan_cards.py`, `backend/templates/components/plan.html`, `backend/static/css/components/plan.css`, `backend/templates/chapters/plans.html`
- Modify: `backend/pages/views.py` (add `plans`), `backend/pages/catalog.py`, `backend/templates/shell.html` (the stylesheet link), `backend/static/css/base.css` (the `.plans` grid), `backend/pages/registry.py`, `backend/config/urls.py`
- Delete: `backend/templates/plans.html`; the `plans` view in `backend/drop_core/views.py`
- Modify (tests and docs): `backend/pages/tests_chapters.py`, `backend/drop_core/tests_fix_docs.py`, `backend/drop_core/tests_site_pages.py`, `backend/drop_core/tests_menu.py`, `backend/drop_core/tests_ambient.py`, `backend/drop_core/tests.py`, `AGENTS.md`, `site-tests/run_site_checks.py`

**Interfaces:**
- Consumes: `PLANS` from `backend/drop_core/plans.py` (keys `uploads`, `ttl_days`, `keys` with `None` meaning unlimited, `price_usd`, `storage_gb`); the `signup` URL name, which reads `?plan=free|team|scale`; `{% structured_data %}` (Task 1).
- Produces:
  - `pages.plan_cards.plan_cards()` returns, in the order free, team, scale, dicts with `slug`, `name`, `price`, `limits` (four short phrases), `signup_url` and `action`.
  - Component `plan`, input `plan` (one such dict).
  - `pages.views.plans` passes `plans` and `structured_data` (one `Product` with an `Offer` per plan).

- [ ] **Step 1: Write the failing tests**

Add to the imports of `backend/pages/tests_chapters.py`:

```python
import json

from django.template.loader import render_to_string

from drop_core.plans import PLANS
```

and append:

```python
class PlansTests(TestCase):
    def setUp(self):
        self.html = self.client.get(reverse("plans")).content.decode()
        self.text = visible_text(self.html)

    def test_every_figure_comes_from_plans_py(self):
        for slug, caps in PLANS.items():
            with self.subTest(plan=slug):
                self.assertIn(f"{caps['uploads']:,} uploads a month", self.text)
                self.assertIn(f"Links last up to {caps['ttl_days']} days", self.text)
                self.assertIn(f"{caps['storage_gb']} GB of storage", self.text)
                self.assertIn("$0" if caps["price_usd"] == 0 else f"${caps['price_usd']} a month", self.text)
        self.assertIn("Unlimited keys", self.text)

    def test_each_plan_links_into_sign_up_with_it_chosen(self):
        for slug in PLANS:
            with self.subTest(plan=slug):
                self.assertIn(f'href="/accounts/signup/?plan={slug}"', self.html)

    def test_says_the_price_includes_tax(self):
        self.assertIn("Tax included. The price you see is the price you pay.", self.text)

    def test_structured_data_offers_match_plans_py(self):
        blocks = [json.loads(block) for block in re.findall(
            r'<script type="application/ld\+json" data-page-meta>(.*?)</script>', self.html, re.DOTALL)]
        product = next(block for block in blocks if block["@type"] == "Product")
        self.assertEqual({offer["name"]: offer["price"] for offer in product["offers"]},
                         {slug.title(): str(caps["price_usd"]) for slug, caps in PLANS.items()})

    def test_the_template_states_no_figures(self):
        text = (CHAPTERS / "plans.html").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"\$\d|\b\d[\d,]*\s*(GB|uploads|days|keys?)\b",
                            "backend/templates/chapters/plans.html: figures come from drop_core/plans.py, through the view")

    def test_the_plan_component_renders_one_card(self):
        from pages.plan_cards import plan_cards

        team = next(card for card in plan_cards() if card["slug"] == "team")
        html = render_to_string("components/plan.html", {"plan": team})
        self.assertIn('<h2 class="plan__name">Team</h2>', html)
        self.assertIn("$4 a month", html)
        self.assertIn('href="/accounts/signup/?plan=team"', html)
        self.assertIn(">Choose Team</a>", html)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_chapters.PlansTests -v 2`
Expected: FAIL or ERROR for four of the six (`test_every_figure_comes_from_plans_py`, `test_structured_data_offers_match_plans_py`, `test_the_plan_component_renders_one_card`, `test_the_template_states_no_figures`): the old Plans page says "/mo" and "link TTL", has no `data-page-meta` JSON-LD, and `chapters/plans.html`, `components/plan.html` and `pages.plan_cards` do not exist. `test_each_plan_links_into_sign_up_with_it_chosen` and `test_says_the_price_includes_tax` already pass against the old page, which is fine.

- [ ] **Step 3: Build the cards from plans.py**

Create `backend/pages/plan_cards.py`:

```python
"""The three plans as the plan component takes them (spec 4.3). Every figure comes from
drop_core/plans.py; to change a price or a limit, follow AGENTS.md "Pricing changes"."""
from django.urls import reverse

from drop_core.plans import PLANS

PLAN_ORDER = ("free", "team", "scale")


def plan_cards():
    cards = []
    for slug in PLAN_ORDER:
        caps, name = PLANS[slug], slug.title()
        if caps["keys"] is None:
            keys = "Unlimited keys"
        else:
            keys = "1 key" if caps["keys"] == 1 else f"{caps['keys']} keys"
        cards.append({
            "slug": slug,
            "name": name,
            "price": "$0" if caps["price_usd"] == 0 else f"${caps['price_usd']} a month",
            "limits": [
                f"{caps['uploads']:,} uploads a month",
                f"Links last up to {caps['ttl_days']} days",
                f"{caps['storage_gb']} GB of storage",
                keys,
            ],
            "signup_url": f"{reverse('signup')}?plan={slug}",
            "action": "Start free" if caps["price_usd"] == 0 else f"Choose {name}",
        })
    return cards
```

- [ ] **Step 4: Add the plan component**

Create `backend/templates/components/plan.html`:

```html
{# Component: plan. One plan: its name, price, limits, and a link into sign-up with it chosen (spec 4.3). #}
{# Inputs: plan, a dict with name, price, limits (a list of short phrases), signup_url and action. #}
<article class="plan">
<h2 class="plan__name">{{ plan.name }}</h2>
<p class="plan__price label">{{ plan.price }}</p>
<ul class="plan__limits">{% for limit in plan.limits %}<li>{{ limit }}</li>{% endfor %}</ul>
<p class="plan__act"><a class="label interactive" href="{{ plan.signup_url }}">{{ plan.action }}</a></p>
</article>
```

Create `backend/static/css/components/plan.css`:

```css
/* A plan (spec 4.3): its name in the display face, its price as a label, its limits, and a link
   into sign-up with it chosen. */
.plan {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.plan__name {
  margin: 0;
  font-family: var(--font-display);
  font-style: italic;
  font-weight: 400;
  font-size: 1.45em;
  line-height: 1.2;
}

.plan__price {
  margin: 0;
}

.plan__limits {
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: .92em;
  line-height: 1.55;
}

.plan__act {
  margin: var(--space-3) 0 0;
}
```

In `backend/templates/shell.html`, add after the `controls.css` link:

```html
<link rel="stylesheet" href="{% static 'css/components/plan.css' %}">
```

In `backend/pages/catalog.py`, add `from pages.plan_cards import plan_cards` to the imports and, as the last entry of `entries()`:

```python
        Entry("plan", "One plan on the Plans chapter, here Team: name, price, limits and a link into sign-up.",
              {"plan": plan_cards()[1]}),
```

Append to `backend/static/css/base.css`:

```css
.plans {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 10.5rem), 1fr));
  gap: var(--space-5);
  margin: var(--space-4) 0 var(--space-5);
}
```

- [ ] **Step 5: Add the Plans chapter and route it**

Create `backend/templates/chapters/plans.html`:

```html
{% extends "layouts/chapter.html" %}
{% block chapter %}
{# Words: edit the text between here and the end marker; keep every tag, class and template tag as it is. #}
<h1 class="chapter__title">Plans</h1>
<div class="plans">{% for plan in plans %}{% include "components/plan.html" with plan=plan only %}{% endfor %}</div>
<p class="chapter__more">Tax included. The price you see is the price you pay.</p>
<p class="chapter__more">Already subscribed? Switch between Team and Scale in the billing section of <a href="/app/">your dashboard</a>; the change is prorated.</p>
{# End of words. #}
{% endblock %}
```

In `backend/pages/views.py`, add `from pages.plan_cards import plan_cards` and `from drop_core.plans import PLANS` to the imports, and append:

```python
def plans(request):
    """Plans (spec 4.3): the three plans from drop_core/plans.py, each a link into sign-up with it chosen."""
    cards = plan_cards()
    product = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Veilfile",
        "description": registry.page("plans").description,
        "offers": [
            {"@type": "Offer", "name": card["name"], "price": str(PLANS[card["slug"]]["price_usd"]),
             "priceCurrency": "USD", "url": registry.SITE_ORIGIN + card["signup_url"]}
            for card in cards
        ],
    }
    return render(request, "chapters/plans.html", {"plans": cards, "structured_data": [product]})
```

In `backend/config/urls.py`, replace `path('plans/', core_views.plans, name='plans'),` with `path("plans/", pages_views.plans, name="plans"),`. In `backend/pages/registry.py`, add `layout="chapter"` to the `plans` entry (its title and description stay). In `backend/drop_core/views.py`, delete the `plans` view (from `def plans(request):` to `return render(request, "plans.html")` and the two blank lines after it), and `git rm backend/templates/plans.html`.

- [ ] **Step 6: Retire the old Plans page's tests and update the price checklist**

- `backend/drop_core/tests_fix_docs.py`: in `test_every_core_copy_file_states_something`, delete the line `            "backend/templates/plans.html",` (the Plans chapter states no figures of its own now; `pages.tests_chapters.PlansTests` checks it renders every figure from `plans.py`). The repo still yields 42 price facts and 105 cap facts, above the test's floors of 30 and 60.
- `AGENTS.md`, "Pricing changes": replace `` `backend/templates/**/*.html` (`plans.html`) `` with `` `backend/templates/**/*.html` (the sign-up and checkout pages; the Plans
chapter takes every figure from `plans.py`) ``.
- `backend/drop_core/tests_site_pages.py`: delete the class `PlansPageTests` (from `class PlansPageTests(TestCase):` up to, not including, `class DocsSetupFlowTests(TestCase):`).
- `backend/drop_core/tests_menu.py`: in both `MENU_PAGES` and `FOOTER_PAGES`, delete the line `    "/plans/",`.
- `backend/drop_core/tests_ambient.py`: in `FIELD_PAGES`, delete `    ("/plans/", False),`, and replace `self.client.get("/plans/")` with `self.client.get("/terms/")`.
- `backend/drop_core/tests.py`: in `test_skip_link_and_main_id_on_django_pages`, delete the line `            "/plans/",`.

- [ ] **Step 7: Run the tests and watch them pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages drop_core -v 1`
Expected: OK. (`pages.tests_site_rules` requires the new component's stylesheet link and catalog entry; `pages.tests_catalog` renders it in four frames.)
Then run the full suite. Expected: OK.

- [ ] **Step 8: Check it in the browser**

Add `SitePage("/plans/", "plans", chapter=True),` to `PAGES` after Promises. Restart the server and run the browser checks.
Expected: `0 problem(s) across 5 page(s)`. Look at `plans-1280-light-motion-on.png` and `plans-375-light-motion-on.png`: three plans side by side or two and one at 1280, stacked at 375, and the sign-up links answering the cursor with the flat fill.

- [ ] **Step 9: Commit**

```bash
git add backend/pages/plan_cards.py backend/templates/components/plan.html backend/static/css/components/plan.css \
  backend/templates/chapters/plans.html backend/pages/views.py backend/pages/catalog.py backend/templates/shell.html \
  backend/static/css/base.css backend/pages/registry.py backend/config/urls.py backend/drop_core/views.py \
  backend/pages/tests_chapters.py backend/drop_core/tests_fix_docs.py AGENTS.md backend/drop_core/tests_site_pages.py \
  backend/drop_core/tests_menu.py backend/drop_core/tests_ambient.py backend/drop_core/tests.py site-tests/run_site_checks.py
# backend/templates/plans.html is already staged as deleted by Step 5's git rm
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Add the Plans chapter, every figure from plans.py

A plan component shows each plan's name, price and limits with a link into
sign-up with that plan chosen; the cards and the Product JSON-LD are built
from drop_core/plans.py, so a price change touches one file for this page.
The old plans.html and the tests of its markup go.
EOF
```

---

### Task 6: The install line, with tabs and Copy

**Files:**
- Create: `backend/pages/install.py`, `backend/templates/components/install_line.html`, `backend/static/css/components/install_line.css`, `backend/static/js/behaviors/tabs.js`, `backend/static/js/behaviors/copy.js`, `backend/pages/tests_install_line.py`
- Modify: `backend/templates/shell.html` (stylesheet link, behaviour scripts), `backend/pages/catalog.py`, `backend/pages/views.py` (`component_catalog` id suffixing), `backend/pages/tests_shell.py`, `backend/pages/tests_static.py`, `site-tests/run_site_checks.py`

**Interfaces:**
- Consumes: `registry.SITE_ORIGIN`; `.visually-hidden` (PR 1 follow-up); the component rules.
- Produces:
  - `pages.install.INSTALL_TABS`: three dicts (`mcp`, `npm`, `pip`, in that order) with `key`, `label`, `prompt`, `command`, `then`, `then_code`.
  - Component `install_line`, inputs `tabs` and `id_prefix`. Ids: `<prefix>-tab-<key>`, `<prefix>-panel-<key>`, `<prefix>-command-<key>`. Markers: `[data-tabs]` on the root, `[data-copy="<command id>"]` on Copy, `[data-copy-text]` around the command without its prompt, `[data-copy-then]` on the follow-up line, `[data-copy-status]` on the screen-reader status.
  - `js/behaviors/tabs.js` and `js/behaviors/copy.js`, loaded on every shell page, working by event delegation (content swapped in later needs nothing re-attached).
  - Site checks: `install_line_problems(page, line, label)` (Task 8 reuses it), and every browser context is granted clipboard read and write.

- [ ] **Step 1: Write the failing tests**

Create `backend/pages/tests_install_line.py`:

```python
"""The install line (spec 4.2, 9): a proper tablist over one box with Copy, and each tab's line after Copy."""
import re

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from pages.install import INSTALL_TABS


class InstallLineTests(SimpleTestCase):
    def setUp(self):
        self.html = render_to_string("components/install_line.html", {"tabs": INSTALL_TABS, "id_prefix": "t"})

    def test_mcp_comes_first_then_npm_and_pip(self):
        self.assertEqual(re.findall(r'role="tab"[^>]*>([^<]+)</button>', self.html), ["MCP", "npm", "pip"])

    def test_the_tabs_are_a_proper_tablist(self):
        self.assertIn('role="tablist" aria-label="Connect with"', self.html)
        for tab in INSTALL_TABS:
            key = tab["key"]
            with self.subTest(tab=key):
                self.assertIn(f'id="t-tab-{key}" aria-controls="t-panel-{key}"', self.html)
                self.assertIn(f'role="tabpanel" id="t-panel-{key}" aria-labelledby="t-tab-{key}"', self.html)
        self.assertIn('aria-selected="true" tabindex="0">MCP</button>', self.html)
        self.assertEqual(self.html.count('aria-selected="false" tabindex="-1"'), 2)
        self.assertEqual(self.html.count(" hidden>"), 2)  # npm and pip start hidden

    def test_copy_takes_the_command_without_its_prompt(self):
        self.assertIn('data-copy="t-command-npm"', self.html)
        self.assertIn('<span class="install-line__prompt" aria-hidden="true">$</span><span data-copy-text>npm install -g veilfile</span>',
                      self.html)
        self.assertIn("<span data-copy-text>https://veilfile.com/mcp</span>", self.html)

    def test_every_tab_has_a_line_after_copy_and_copy_is_announced(self):
        self.assertIn("Add it to your agent as a remote MCP server; sign in when it asks.", self.html)
        self.assertEqual(self.html.count("then <code>veilfile login</code>"), 2)
        self.assertIn('role="status" data-copy-status', self.html)
```

Add to `backend/pages/tests_shell.py`, inside `class ShellHeadTests`:

```python
    def test_loads_every_behaviour_as_a_module(self):
        names = sorted(path.stem for path in (STATIC / "js" / "behaviors").glob("*.js"))
        self.assertTrue(names, "no behaviours in static/js/behaviors/")
        for name in names:
            with self.subTest(behaviour=name):
                self.assertIn(f'<script type="module" src="/static/js/behaviors/{name}.js"></script>', self.html)
```

Add to `backend/pages/tests_static.py` (add `import gzip` to its imports):

```python
class ScriptBudgetTests(SimpleTestCase):
    def test_shell_and_behaviours_stay_under_10_kb_gzipped(self):
        js = Path(settings.BASE_DIR) / "static" / "js"
        files = [js / "shell.js", *sorted((js / "behaviors").glob("*.js"))]
        total = sum(len(gzip.compress(path.read_bytes(), 9)) for path in files)
        self.assertLessEqual(total, 10 * 1024,
                             f"shell.js and js/behaviors/*.js are {total} bytes gzipped; the budget is 10 KB (spec 8)")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_install_line pages.tests_shell pages.tests_static -v 2`
Expected: ERROR for `tests_install_line` (`ModuleNotFoundError: No module named 'pages.install'`), FAIL for `test_loads_every_behaviour_as_a_module` (`no behaviours in static/js/behaviors/`); the budget test passes (shell.js alone is about 1.8 KB), and must keep passing.

- [ ] **Step 3: Add the tabs' words and the component**

Create `backend/pages/install.py`:

```python
"""The install line's three tabs (spec 4.2): what each shows, what Copy copies, and the line after Copy.
These are words a visitor reads; edit them here, keeping each command exactly as it runs."""
from pages.registry import SITE_ORIGIN

INSTALL_TABS = (
    {"key": "mcp", "label": "MCP", "prompt": "", "command": f"{SITE_ORIGIN}/mcp",
     "then": "Add it to your agent as a remote MCP server; sign in when it asks.", "then_code": ""},
    {"key": "npm", "label": "npm", "prompt": "$", "command": "npm install -g veilfile",
     "then": "then", "then_code": "veilfile login"},
    {"key": "pip", "label": "pip", "prompt": "$", "command": "pip install veilfile",
     "then": "then", "then_code": "veilfile login"},
)
```

Create `backend/templates/components/install_line.html`:

```html
{# Component: install_line. MCP, npm and pip tabs over one box with Copy, and each tab's line after Copy (spec 4.2). #}
{# Inputs: tabs, the dicts in pages/install.py (key, label, prompt, command, then, then_code); id_prefix, unique on the page. #}
<div class="install-line" data-tabs>
<div class="install-line__tabs" role="tablist" aria-label="Connect with">
{% for tab in tabs %}<button class="install-line__tab label interactive" type="button" role="tab" id="{{ id_prefix }}-tab-{{ tab.key }}" aria-controls="{{ id_prefix }}-panel-{{ tab.key }}" aria-selected="{% if forloop.first %}true{% else %}false{% endif %}" tabindex="{% if forloop.first %}0{% else %}-1{% endif %}">{{ tab.label }}</button>
{% endfor %}</div>
{% for tab in tabs %}<div class="install-line__panel" role="tabpanel" id="{{ id_prefix }}-panel-{{ tab.key }}" aria-labelledby="{{ id_prefix }}-tab-{{ tab.key }}"{% if not forloop.first %} hidden{% endif %}>
<div class="install-line__box"><code id="{{ id_prefix }}-command-{{ tab.key }}" tabindex="0">{% if tab.prompt %}<span class="install-line__prompt" aria-hidden="true">{{ tab.prompt }}</span>{% endif %}<span data-copy-text>{{ tab.command }}</span></code><button class="install-line__copy label interactive" type="button" data-copy="{{ id_prefix }}-command-{{ tab.key }}">Copy</button></div>
<p class="install-line__then" data-copy-then>{{ tab.then }}{% if tab.then_code %} <code>{{ tab.then_code }}</code>{% endif %}</p>
</div>
{% endfor %}<p class="visually-hidden" role="status" data-copy-status></p>
</div>
```

Create `backend/static/css/components/install_line.css`:

```css
/* The install line (spec 4.2): tabs over one hairline box with Copy, and the line each tab reveals
   after Copy, its space held from the start so nothing moves. The command scrolls inside its box. */
.install-line,
.install-line__panel {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  justify-items: center;
  gap: var(--space-3);
  max-width: 100%;
}

.install-line__panel[hidden] {
  display: none;
}

.install-line__tabs {
  display: flex;
  gap: 1.6em;
}

.install-line__tab[aria-selected="true"] {
  color: var(--ink);
}

.install-line__box {
  display: flex;
  align-items: center;
  gap: 2.2em;
  max-width: 100%;
  padding: .95em 1.2em .95em 1.3em;
  border: 1px solid var(--hair);
  background: var(--box);
  -webkit-backdrop-filter: blur(2px);
  backdrop-filter: blur(2px);
  font-family: var(--font-mono);
  font-size: clamp(11px, 1.15vw, 14px);
  color: var(--ink);
}

.install-line__box code {
  min-width: 0;
  overflow-x: auto;
  white-space: nowrap;
  font: inherit;
}

.install-line__prompt {
  margin-right: .7em;
  color: var(--muted);
}

.install-line__then {
  min-height: 1.5em;
  max-width: 34ch;
  margin: 0;
  font-family: var(--font-mono);
  font-size: clamp(11px, 1.1vw, 13px);
  line-height: 1.5;
  text-align: center;
  color: var(--muted);
  opacity: 0;
  transition: opacity var(--fade) ease;
}

.install-line__then.is-shown {
  opacity: 1;
}

.install-line__then code {
  font: inherit;
  color: var(--ink);
}
```

In `backend/templates/shell.html`, add after the `plan.css` link:

```html
<link rel="stylesheet" href="{% static 'css/components/install_line.css' %}">
```

and after the `shell.js` script line:

```html
<script type="module" src="{% static 'js/behaviors/tabs.js' %}"></script>
<script type="module" src="{% static 'js/behaviors/copy.js' %}"></script>
```

- [ ] **Step 4: Add the two behaviours**

Create `backend/static/js/behaviors/tabs.js`:

```js
// Tabs (spec 9: a proper tablist): a click or the arrow, Home and End keys choose a tab and show its
// panel. Delegated from the document, so tabs swapped in by an in-place change work too.
function choose(tab) {
  for (const other of tab.closest('[role="tablist"]').querySelectorAll('[role="tab"]')) {
    const selected = other === tab;
    other.setAttribute("aria-selected", String(selected));
    other.tabIndex = selected ? 0 : -1;
    const panel = document.getElementById(other.getAttribute("aria-controls"));
    if (panel) panel.hidden = !selected;
  }
}

document.addEventListener("click", (event) => {
  const tab = event.target.closest('[data-tabs] [role="tab"]');
  if (tab) choose(tab);
});

document.addEventListener("keydown", (event) => {
  const tab = event.target.closest('[data-tabs] [role="tab"]');
  if (!tab) return;
  const tabs = [...tab.closest('[role="tablist"]').querySelectorAll('[role="tab"]')];
  const index = tabs.indexOf(tab);
  const next = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: tabs.length - 1 }[event.key];
  if (next === undefined) return;
  event.preventDefault();
  const target = tabs[(next + tabs.length) % tabs.length];
  choose(target);
  target.focus();
});
```

Create `backend/static/js/behaviors/copy.js`:

```js
// Copy (spec 4.2): copies the command without its prompt, says "Copied" for 1.8 s and tells screen
// readers, then shows the tab's line after Copy. When the page may not write to the clipboard (an
// insecure origin, a refused permission), it selects the command and says so instead.
const SHOWN_MS = 1800;

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const source = document.getElementById(button.dataset.copy);
  if (!source) return;
  const command = source.querySelector("[data-copy-text]") || source;
  const scope = button.closest("[data-tabs]") || document;
  const status = scope.querySelector("[data-copy-status]");
  let copied = false;
  try {
    await navigator.clipboard.writeText(command.textContent.trim());
    copied = true;
  } catch (error) {
    const range = document.createRange();
    range.selectNodeContents(command);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }
  button.textContent = copied ? "Copied" : "Selected";
  if (status) status.textContent = copied ? "Copied" : "Command selected; copy it with your keyboard";
  const then = button.closest('[role="tabpanel"]')?.querySelector("[data-copy-then]");
  if (then) then.classList.add("is-shown");
  clearTimeout(button.copyReset);
  button.copyReset = setTimeout(() => {
    button.textContent = "Copy";
    if (status) status.textContent = "";
  }, SHOWN_MS);
});
```

- [ ] **Step 5: Show it in the catalog with unique ids**

In `backend/pages/catalog.py`, add `from pages.install import INSTALL_TABS` to the imports and the entry:

```python
        Entry("install_line", "The threshold after Come in: MCP, npm and pip, Copy, and the line after Copy.",
              {"tabs": INSTALL_TABS, "id_prefix": "catalog-install"}),
```

In `backend/pages/views.py`, in `component_catalog`, replace

```python
            if "list_id" in context:
                context["list_id"] = f"{context['list_id']}-{index}"
```

with

```python
            for key in ("list_id", "id_prefix"):  # ids must stay unique across the four frames
                if key in context:
                    context[key] = f"{context[key]}-{index}"
```

- [ ] **Step 6: Run the tests and watch them pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages -v 1`
Expected: OK (`pages.tests_catalog` checks every id on the catalog page is unique; `pages.tests_content` checks the component's characters).

- [ ] **Step 7: Check tabs and Copy in the browser**

In `site-tests/run_site_checks.py`:

In `new_context`, after the `context = browser.new_context(...)` line, add:

```python
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base_url)
```

Add after `corner_row_problems`:

```python
def install_line_problems(page, line, label):
    """The install line (spec 4.2): tabs by click and arrow keys; Copy says Copied, tells screen
    readers and copies the command without its prompt; the tab's line appears after Copy."""
    problems = []
    if line.evaluate("(line) => line.querySelector('.install-line__box').getBoundingClientRect().right"
                     " > line.getBoundingClientRect().right + 1"):
        problems.append(f"{label}: the install box is wider than the space it sits in; the command should scroll inside it")
    tabs = line.locator('[role="tab"]')
    tabs.nth(1).click()
    shown = line.locator('[role="tabpanel"]:not([hidden]) [data-copy-text]')
    if (shown.text_content() or "").strip() != "npm install -g veilfile":
        problems.append(f"{label}: choosing npm does not show the npm command")
    page.keyboard.press("ArrowRight")
    if tabs.nth(2).get_attribute("aria-selected") != "true" or tabs.nth(2).evaluate("(tab) => tab !== document.activeElement"):
        problems.append(f"{label}: the right arrow does not move to the pip tab")
    tabs.nth(0).click()
    panel = line.locator('[role="tabpanel"]:not([hidden])')
    panel.locator("[data-copy]").click()
    page.wait_for_timeout(300)
    if (panel.locator("[data-copy]").text_content() or "").strip() != "Copied":
        problems.append(f"{label}: Copy does not say Copied")
    if (line.locator("[data-copy-status]").text_content() or "").strip() != "Copied":
        problems.append(f"{label}: Copy is not announced to screen readers")
    if page.evaluate("() => navigator.clipboard.readText()") != "https://veilfile.com/mcp":
        problems.append(f"{label}: Copy did not put the MCP address on the clipboard")
    page.wait_for_timeout(1200)
    if panel.locator("[data-copy-then]").evaluate("(line) => getComputedStyle(line).opacity") != "1":
        problems.append(f"{label}: the line after Copy does not appear")
    return problems
```

In `run()`, inside the `(1280, "light", "on")` branch, add before `problems += menu_problems(...)`:

```python
                            line = page.locator("[data-tabs]").first
                            if line.count() and line.is_visible():
                                problems += install_line_problems(page, line, label)
```

Restart the server and run the browser checks. Expected: `0 problem(s) across 5 page(s)` (the catalog's install line is checked; the chapters have none). Also look at `component-catalog-1280-light-motion-on.png`: in every install-line frame the box sits inside its frame and a long address is clipped by the box, not spilling out of the frame (page-level checks cannot see this, because the frames clip it).

- [ ] **Step 8: Commit**

```bash
git add backend/pages/install.py backend/templates/components/install_line.html \
  backend/static/css/components/install_line.css backend/static/js/behaviors/tabs.js \
  backend/static/js/behaviors/copy.js backend/templates/shell.html backend/pages/catalog.py backend/pages/views.py \
  backend/pages/tests_install_line.py backend/pages/tests_shell.py backend/pages/tests_static.py site-tests/run_site_checks.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Add the install line, with tabs and Copy

MCP, npm and pip tabs over one box with Copy, and each tab's line after Copy,
its space held from the start. Tabs and Copy are small delegated modules that
load on every shell page; Copy falls back to selecting the command when the
page may not write to the clipboard. A test holds shell.js and the
behaviours to 10 KB gzipped.
EOF
```

---

### Task 7: Begin, the setup chapter

**Files:**
- Create: `backend/templates/chapters/begin.html`
- Modify: `backend/pages/registry.py` (add `begin`, `MENU[0]`), `backend/config/urls.py`, `docs/llms.txt`
- Modify (tests): `backend/pages/tests_chapters.py`, `backend/pages/tests_registry.py`, `backend/pages/tests_shell.py`, `site-tests/run_site_checks.py`

**Interfaces:**
- Consumes: `layouts/chapter.html`, `pages.views.chapter`, `visible_text` (Task 2); the snippets in `backend/docs/pages/agent.md` under `### Claude Code`, `### Codex` and `### Cursor`.
- Produces: URL name `begin` (`/begin/`), which Come in links to without JavaScript (Task 8); `MENU[0]` is About, How it works, Begin, Promises, Plans.

- [ ] **Step 1: Write the failing tests**

Append to `backend/pages/tests_chapters.py` (it already imports `Path`, `re`, `settings` and `unescape`):

```python
AGENT_SETUP = Path(settings.BASE_DIR) / "docs" / "pages" / "agent.md"


def fenced_after(markdown, heading):
    """The first fenced code block after the line '### <heading>' in a Markdown file."""
    start = markdown.index(f"### {heading}\n")
    return re.search(r"```[a-z]*\n(.*?)\n```", markdown[start:], re.DOTALL).group(1)


class BeginTests(TestCase):
    def setUp(self):
        self.html = self.client.get(reverse("begin")).content.decode()
        self.text = visible_text(self.html)
        self.blocks = [unescape(block) for block in re.findall(r"<pre[^>]*><code>(.*?)</code></pre>", self.html, re.DOTALL)]

    def test_mcp_comes_first_with_the_docs_snippet_for_each_client(self):
        markdown = AGENT_SETUP.read_text(encoding="utf-8")
        for client in ("Claude Code", "Codex", "Cursor"):
            with self.subTest(client=client):
                self.assertIn(fenced_after(markdown, client), self.blocks,
                              f"backend/templates/chapters/begin.html: copy the {client} snippet from "
                              f"backend/docs/pages/agent.md exactly")
        self.assertLess(self.text.index("Connect your agent"), self.text.index("Or use the command line"))
        self.assertLess(self.text.index("Or use the command line"), self.text.index("Or plain HTTP"))

    def test_says_who_signs_in_and_who_takes_a_key(self):
        self.assertIn("Clients that support MCP sign-in ask you to sign in the first time", self.text)
        self.assertIn("your account needs a verified email", self.text)
        self.assertIn("VEILFILE_API_KEY", self.text)

    def test_the_commands_are_in_the_page_without_javascript(self):
        for command in ("npm install -g veilfile", "pip install veilfile", "veilfile login", "veilfile push",
                        "curl -X POST https://veilfile.com/api/v1/artifacts"):
            with self.subTest(command=command):
                self.assertIn(command, self.text)

    def test_points_agents_at_llms_txt(self):
        self.assertIn('href="/llms.txt"', self.html)
```

In `backend/pages/tests_registry.py`, `test_signed_out_menu`: the first group becomes `["About", "How it works", "Begin", "Promises", "Plans"]`. In `backend/pages/tests_shell.py`, `test_menu_lists_the_signed_out_groups_in_order`: the list becomes `["About", "How it works", "Begin", "Promises", "Plans", "Docs", "Journal", "Sign in", "Terms", "Privacy", "Report abuse", "llms.txt"]`.

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_chapters.BeginTests pages.tests_registry pages.tests_shell -v 1`
Expected: ERROR for the four `BeginTests` (`NoReverseMatch: Reverse for 'begin' not found`) and FAIL for the two menu tests.

- [ ] **Step 3: Add the chapter**

Create `backend/templates/chapters/begin.html`:

```html
{% extends "layouts/chapter.html" %}
{% block chapter %}
{# Words: edit the text between here and the end marker; keep every tag, class and template tag as it is. #}
<h1 class="chapter__title">Begin</h1>
<h2 class="chapter__heading">Connect your agent</h2>
<p>The MCP server is at <code>https://veilfile.com/mcp</code>. Clients that support MCP sign-in ask you to sign in the first time; your account needs a verified email. Clients that don’t take an API key instead: create one in <a href="/app/">your dashboard</a> and export it as <code>VEILFILE_API_KEY</code>.</p>
<h3 class="chapter__label label">Claude Code</h3>
<pre tabindex="0"><code>claude mcp add --transport http veilfile https://veilfile.com/mcp \
  --header "Authorization: Bearer $VEILFILE_API_KEY"</code></pre>
<h3 class="chapter__label label">Codex</h3>
<pre tabindex="0"><code>codex mcp add veilfile --url https://veilfile.com/mcp --bearer-token-env-var VEILFILE_API_KEY</code></pre>
<h3 class="chapter__label label">Cursor</h3>
<p>In <code>~/.cursor/mcp.json</code>:</p>
<pre tabindex="0"><code>{
  "mcpServers": {
    "veilfile": {
      "url": "https://veilfile.com/mcp",
      "headers": { "Authorization": "Bearer ${env:VEILFILE_API_KEY}" }
    }
  }
}</code></pre>
<h3 class="chapter__label label">The Claude apps</h3>
<p>Add a custom connector with the address above, and sign in when it asks.</p>
<p>Every other client, and each option in full: <a href="{% url 'docs-page' 'agent' %}">agent setup, in the Docs</a>.</p>
<h2 class="chapter__heading">Or use the command line</h2>
<pre tabindex="0"><code>npm install -g veilfile     # or: pip install veilfile
veilfile login              # approve in your browser
veilfile push report.pdf    # prints a private link</code></pre>
<h2 class="chapter__heading">Or plain HTTP</h2>
<pre tabindex="0"><code>curl -X POST https://veilfile.com/api/v1/artifacts \
  -H "Authorization: Bearer $VEILFILE_API_KEY" \
  -F "file=@screenshot.png" -F "ttl_days=7"</code></pre>
<p>Agents can read everything they need in one file: <a href="/llms.txt">llms.txt</a>.</p>
{# End of words. #}
{% endblock %}
```

- [ ] **Step 4: Register and route it**

In `backend/pages/registry.py`, insert after the `how` entry:

```python
    Page("begin", "Begin | Veilfile",
         "Set up Veilfile: connect your agent to the MCP server at veilfile.com/mcp, install the command line "
         "with npm or pip, or upload over plain HTTP.",
         layout="chapter"),
```

and make `MENU[0]`:

```python
    (MenuLink("About", "about"), MenuLink("How it works", "how"), MenuLink("Begin", "begin"),
     MenuLink("Promises", "promises"), MenuLink("Plans", "plans")),
```

In `backend/config/urls.py`, after the `how/` route, add:

```python
    path("begin/", pages_views.chapter, {"name": "begin"}, name="begin"),
```

In `docs/llms.txt`, in `## Pages for people`, add after the How it works line:

```
- Begin (setting up, for people): https://veilfile.com/begin/
```

- [ ] **Step 5: Run the tests and watch them pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages drop_core.tests_fix_docs -v 1`
Expected: OK (`tests_site_rules` follows Begin's internal links; `tests_fix_docs` scans `llms.txt` and the new template and finds nothing that contradicts `plans.py`).
Then run the full suite. Expected: OK.

- [ ] **Step 6: Check it in the browser**

Add `SitePage("/begin/", "begin", chapter=True),` to `PAGES` after How it works. Restart the server and run the browser checks.
Expected: `0 problem(s) across 6 page(s)`; the 320x640 run shows the long snippets scrolling inside their boxes (`begin-320x640.png`) and no sideways page scroll.

- [ ] **Step 7: Commit**

```bash
git add backend/templates/chapters/begin.html backend/pages/registry.py backend/config/urls.py docs/llms.txt \
  backend/pages/tests_chapters.py backend/pages/tests_registry.py backend/pages/tests_shell.py site-tests/run_site_checks.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Add Begin, the setup chapter

MCP first, with the Docs' own snippets for Claude Code, Codex and Cursor (a
test keeps them equal) and a plain word on who signs in and who takes a key;
then the command line, then curl, then a pointer to llms.txt.
EOF
```

---

### Task 8: The threshold and Come in

**Files:**
- Create: `backend/templates/chapters/threshold.html`, `backend/static/js/behaviors/come-in.js`
- Modify: `backend/pages/views.py` (add `landing`), `backend/config/urls.py`, `backend/pages/registry.py` (the `landing` entry), `backend/static/css/base.css` (threshold rules), `backend/templates/shell.html` (the behaviour script)
- Delete: the `landing` view in `backend/drop_core/views.py`; `frontend/src/Landing.jsx`
- Modify: `frontend/src/App.jsx`, `frontend/src/Dashboard.jsx`
- Modify (tests): `backend/pages/tests_chapters.py`, `backend/pages/tests_registry.py`, `backend/drop_core/tests_site_pages.py`, `backend/drop_core/tests.py`, `site-tests/run_site_checks.py`

**Interfaces:**
- Consumes: `INSTALL_TABS`, the `install_line` component, `tabs.js`, `copy.js`, `install_line_problems` (Task 6); `begin` (Task 7); `drop_core.funnel` (`sets_funnel_cookie`, `record`, `LANDING_VISIT`).
- Produces:
  - `pages.views.landing`, URL name `landing` at `/`, still wrapped by `@funnel.sets_funnel_cookie` and recording `LANDING_VISIT` once per session.
  - Markers on the threshold: `[data-threshold]` (the block), `[data-threshold-line]` (the line), `[data-come-in]` (Come in, a link to `/begin/`), `[data-install]` (the install line's wrapper, `hidden` until Come in).
  - `js/behaviors/come-in.js`, loaded on every shell page.
  - `SitePage(..., threshold=True)` and `come_in_problems(page, label)` in the site checks.

- [ ] **Step 1: Write the failing tests**

Append to `backend/pages/tests_chapters.py`:

```python
class ThresholdTests(TestCase):
    def setUp(self):
        self.response = self.client.get("/")
        self.html = self.response.content.decode()

    def test_the_threshold_is_the_line_and_come_in(self):
        self.assertEqual(self.response.status_code, 200)
        self.assertIn('data-layout="chapter"', self.html)
        self.assertIn("Some files are meant to be seen for a moment,", self.html)
        self.assertIn("by the right eyes, and then let go.", self.html)
        self.assertIn('<a class="threshold__enter label interactive" href="/begin/" data-come-in>Come in</a>', self.html)

    def test_the_install_line_waits_behind_come_in(self):
        self.assertIn('<div class="threshold__install" data-install hidden>', self.html)
        self.assertIn('id="threshold-tab-mcp"', self.html)

    def test_its_head_comes_from_the_registry(self):
        self.assertIn('<link rel="canonical" href="https://veilfile.com/" data-page-meta>', self.html)
        self.assertNotIn("fonts/newsreader/normal-400-500-latin.woff2", self.html)  # no reading text here

    def test_the_dashboard_goes_home_with_a_full_page_load(self):
        dashboard = (Path(settings.BASE_DIR).parent / "frontend" / "src" / "Dashboard.jsx").read_text(encoding="utf-8")
        self.assertNotIn('<Link to="/"', dashboard, "frontend/src/Dashboard.jsx: link home with <a href=\"/\">; / is not a React route")
        self.assertEqual(dashboard.count('<a href="/" className="wordmark">'), 2)
```

In `backend/pages/tests_registry.py`, delete the class `LandingMetadataTests` (the landing page's head no longer lives in `frontend/index.html`; `RegistryMatchesRenderedPagesTests` now checks it as rendered), then remove the imports it leaves unused: `re`, `from pathlib import Path` and `from django.conf import settings`.

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_chapters.ThresholdTests -v 2`
Expected: FAIL for all four (`/` still renders the React shell, or its test stub `config/test_templates/index.html`; `Dashboard.jsx` still has `<Link to="/"`).

- [ ] **Step 3: Add the threshold**

Create `backend/templates/chapters/threshold.html`:

```html
{% extends "layouts/chapter.html" %}
{% block preload_reading %}{% endblock %}
{% block chapter %}
<div class="threshold" data-threshold>
{# Words: edit the text between here and the end marker; keep every tag, class and template tag as it is. #}
<h1 class="threshold__line" data-threshold-line>Some files are meant to be seen for a moment,<br class="threshold__break"> by the right eyes, and then let go.</h1>
<a class="threshold__enter label interactive" href="{% url 'begin' %}" data-come-in>Come in</a>
{# End of words. #}
<div class="threshold__install" data-install hidden>{% include "components/install_line.html" with tabs=install_tabs id_prefix="threshold" only %}</div>
</div>
{% endblock %}
```

Append to `backend/static/css/base.css`:

```css
/* The threshold (spec 4.2): the line and COME IN, then the install line alone in their place. Changes
   are fades only, made while the content is invisible, so nothing ever moves in view. */
.threshold {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  justify-items: center;
  gap: var(--space-5);
  text-align: center;
}

.threshold > * {
  transition: opacity var(--fade) ease;
}

.threshold .is-faded {
  opacity: 0;
  pointer-events: none;
}

.threshold__line {
  max-width: 30ch;
  margin: 0;
  font-family: var(--font-display);
  font-style: italic;
  font-weight: 300;
  font-size: var(--display-size);
  line-height: 1.22;
  text-wrap: balance;
}

.threshold__break {
  display: none;
}

/* On wide screens the line breaks after "moment," (spec 3.6). That first line is about 16em long, so
   there the threshold takes the page's whole width and the type shrinks before the line would wrap. */
@media (min-width: 56.25rem) {
  .chapter:has(> .threshold) {
    max-width: none;
    container-type: inline-size;
  }

  .threshold__line {
    max-width: none;
    font-size: min(var(--display-size), 100cqi / 16.5);
  }

  .threshold__break {
    display: inline;
  }
}

.label.threshold__enter {
  padding: 1em 1.6em;
  margin: 0;
  letter-spacing: .28em;
}

.threshold__install {
  max-width: 100%;
}
```

Create `backend/static/js/behaviors/come-in.js`:

```js
// Come in (spec 4.2): the line and COME IN fade out, then the install line fades in alone, already in
// its centred place; nothing moves while it can be seen, and focus moves to Copy. Without this script
// Come in is an ordinary link to /begin/.
const FADE_MS = 700; // --fade in tokens.css
const REDUCE = window.matchMedia("(prefers-reduced-motion: reduce)");

function instant() {
  return document.documentElement.getAttribute("data-motion") === "off" || REDUCE.matches;
}

document.addEventListener("click", async (event) => {
  const enter = event.target.closest("[data-come-in]");
  if (!enter || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const threshold = enter.closest("[data-threshold]");
  const install = threshold && threshold.querySelector("[data-install]");
  if (!install) return;
  event.preventDefault();
  if (threshold.dataset.entering) return; // a second click while the first is still fading
  threshold.dataset.entering = "true";
  const leaving = [...threshold.querySelectorAll("[data-threshold-line]"), enter];
  if (!instant()) {
    leaving.forEach((part) => part.classList.add("is-faded"));
    await new Promise((resolve) => setTimeout(resolve, FADE_MS));
  }
  leaving.forEach((part) => { part.hidden = true; });
  install.classList.add("is-faded");
  install.hidden = false;
  install.getBoundingClientRect(); // lay it out while invisible, then let it fade in
  install.classList.remove("is-faded");
  const copy = install.querySelector('[role="tabpanel"]:not([hidden]) [data-copy]');
  if (copy) copy.focus({ preventScroll: true });
});
```

In `backend/templates/shell.html`, add after the `copy.js` script line:

```html
<script type="module" src="{% static 'js/behaviors/come-in.js' %}"></script>
```

- [ ] **Step 4: Serve / from the pages app, with its funnel unchanged**

In `backend/pages/views.py`, add to the imports `from drop_core import funnel` and `from pages.install import INSTALL_TABS`, and append:

```python
@funnel.sets_funnel_cookie
def landing(request):
    """The threshold (spec 4.2). Records one funnel event per browser session, as the old landing page
    did (cookie-deduped; no database session; bots and HEAD not counted)."""
    funnel.record(request, funnel.LANDING_VISIT, once_per_session=True)
    return render(request, "chapters/threshold.html", {"install_tabs": INSTALL_TABS})
```

In `backend/config/urls.py`, replace `    path('', core_views.landing, name='landing'),  # PUBLIC landing` with `    path("", pages_views.landing, name="landing"),  # the threshold`. In `backend/drop_core/views.py`, delete the `landing` view: the line `@funnel.sets_funnel_cookie` above it, the function, and the two blank lines after it. (If `funnel` is now unused in `drop_core/views.py`, leave the import: `signup` and the upload views still use it.)

In `backend/pages/registry.py`, replace

```python
    # The landing page's <head> is in frontend/index.html until the threshold moves here (PR 2).
    Page("landing", "Veilfile | Private ephemeral artifact hosting for AI agents",
         "Private file hosting for AI agents. Upload screenshots, logs, and bundles via REST or MCP. "
         "Unguessable links that expire on schedule. Free to $12/mo.",
         renders_metadata=False),
```

with

```python
    Page("landing", "Veilfile | Private ephemeral artifact hosting for AI agents",
         "Private file hosting for AI agents. Upload screenshots, logs, and bundles via REST or MCP. "
         "Unguessable links that expire on schedule. Free to $12/mo.",
         layout="chapter"),
```

and in the module docstring of `backend/pages/registry.py` (the sentence is wrapped over two lines) replace the two lines `description of 70 to 160 characters, then run the tests. Until a page moves onto the` / `shell, its title and description must equal what its template renders (a test compares them).` with `description of 70 to 160 characters, then run the tests. A page not yet on the shell must` / `render the same title and description itself (a test compares them).`

- [ ] **Step 5: Take / out of the React app**

- `git rm frontend/src/Landing.jsx`.
- `frontend/src/App.jsx`: delete the line `import Landing from './Landing.jsx'` and the line `      <Route path="/" element={<Landing />} />`.
- `frontend/src/Dashboard.jsx`: replace both

  ```jsx
          <Link to="/" className="wordmark">
  ```
  and the matching `</Link>` (two places, at about lines 957 and 977) with `<a href="/" className="wordmark">` and `</a>`, keeping the indentation; then change the first line `import { Link, useSearchParams } from 'react-router-dom'` to `import { useSearchParams } from 'react-router-dom'` (no other `Link` remains; check with `grep -n "Link" frontend/src/Dashboard.jsx`).
- Prove the build still works: `cd frontend && npm ci && npm run build` (Expected: Vite prints the built files and exits 0), then `rm -rf frontend/dist` so the backend tests go back to their stub template. A fresh worktree has no `frontend/node_modules`, so `npm ci` installs one (gitignored; leave it).
- `backend/templates/includes/menu.html`, second line of its leading comment: replace `{# audited MenuButton in frontend/src/Landing.jsx: icon button, top right, #}` with `{# audited MenuButton of the old React landing page: icon button, top right, #}` (the file it names is deleted).

- [ ] **Step 6: Retire the tests of the old landing page**

- `backend/drop_core/tests_site_pages.py`: delete the class `LandingRedesignTests` (from `class LandingRedesignTests(TestCase):` up to, not including, the next class or the end of the file). The file keeps `DocsSetupFlowTests`; replace its module docstring (it still describes the old Plans, Trust and landing pages) with `"""The Docs quickstart's shell-setup flow (approved redesign, 2026-10-06)."""`.
- `backend/drop_core/tests.py`: delete the method `test_landing_menu_links_docs` (it read `Landing.jsx`). In `test_copy_buttons_named_and_live`, delete the five lines that read and assert on `Landing.jsx` (the `landing = ...` line, `self.assertIn("aria-label={`Copy ${describe}`}", landing)`, the two-line comment and `self.assertEqual(landing.count('aria-live="polite"'), 1)`), keeping the Dashboard and quickstart assertions.

- [ ] **Step 7: Run the tests and watch them pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages drop_core.tests_fix_web drop_core.tests drop_core.tests_site_pages config -v 1`
Expected: OK. This includes the funnel's own tests on `/` (`FunnelSessionlessTests`: one event per session, no database session, bots and HEAD not counted, a recording failure still answers 200), `RegistryMatchesRenderedPagesTests` now checking the threshold's head, and `config.tests_fix_platform.test_landing_renders_without_a_frontend_build`.
Then run the full suite. Expected: OK.

- [ ] **Step 8: Check Come in, Copy and a refused clipboard in the browser**

In `site-tests/run_site_checks.py`, add `threshold: bool = False  # the threshold: Come in gives way to the install line` as the last field of `SitePage`, and `SitePage("/", "threshold", threshold=True),` as the second entry of `PAGES` (after the catalog).

Add after the line that defines `OVERFLOW`:

```python
LINES = """(selector) => { const range = document.createRange();
    range.selectNodeContents(document.querySelector(selector));
    return new Set([...range.getClientRects()].map((rect) => Math.round(rect.top))).size; }"""
```

Add after `install_line_problems`:

```python
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
```

In `edge_case_problems`, inside the no-JavaScript loop after the sideways-scroll check, add:

```python
        if site_page.threshold and page.locator("[data-come-in]").get_attribute("href") != "/begin/":
            problems.append(f"{label}: without JavaScript Come in should be a link to /begin/")
```

and add, before the comment `# Wide screens (64rem and up): the four corners stay put`:

```python
    if site_page.threshold:
        for width, motion in ((1280, "on"), (1024, "on"), (375, "on"), (320, "on")):
            context = new_context(browser, base_url, session_key, site_page, width=width, theme=None, motion=motion)
            page, errors = open_page(context, url)
            label = f"{site_page.path} at {width}px, Come in"
            if width >= 900 and page.evaluate(LINES, "[data-threshold-line]") != 2:
                problems.append(f"{label}: on a wide screen the line should set in two lines, breaking after \"moment,\"")
            problems += come_in_problems(page, label)
            if page.evaluate(OVERFLOW) > 0:  # the install line is showing now (Review Focus 5)
                problems.append(f"{label}: the page scrolls sideways with the install line showing")
            page.locator(".shell-corner--top-left .lockup").click()  # the lockup returns to the line
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1000)
            if not page.locator("[data-threshold-line]").is_visible():
                problems.append(f"{label}: the lockup does not return to the line")
            problems += [f"{label}: console error: {error}" for error in errors]
            context.close()

        # Motion off: Come in is instant.
        context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion="off")
        page, errors = open_page(context, url)
        page.locator("[data-come-in]").click()
        page.wait_for_timeout(150)
        if not page.locator("[data-install]").is_visible():
            problems.append(f"{site_page.path} with Motion off: Come in is not instant")
        context.close()

        # A double click on Come in: the install line, once, and still on this page. (The first click puts
        # pointer-events: none on COME IN, so this shows the page stays put, not the `entering` guard.)
        context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
        page, errors = open_page(context, url)
        page.locator("[data-come-in]").dblclick()
        page.wait_for_timeout(1800)
        if page.evaluate("() => location.pathname") != "/" or not page.locator("[data-install]").is_visible():
            problems.append(f"{site_page.path}: a double click on Come in leaves the threshold or loses the install line")
        problems += [f"{site_page.path} after a double click on Come in: console error: {error}" for error in errors]
        context.close()

        # A browser that refuses the clipboard: Copy selects the command and says so (Review Focus 3).
        context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
        context.add_init_script("""Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
            writeText: () => Promise.reject(new DOMException("Refused.", "NotAllowedError")),
            readText: () => Promise.reject(new DOMException("Refused.", "NotAllowedError")) } });""")
        page, errors = open_page(context, url)
        label = f"{site_page.path} with the clipboard refused"
        page.locator("[data-come-in]").click()
        page.wait_for_timeout(1700)
        copy = page.locator('[data-install] [role="tabpanel"]:not([hidden]) [data-copy]')
        copy.click()
        page.wait_for_timeout(300)
        if (copy.text_content() or "").strip() != "Selected":
            problems.append(f"{label}: Copy does not say it selected the command")
        if page.evaluate("() => String(window.getSelection())").strip() != "https://veilfile.com/mcp":
            problems.append(f"{label}: Copy does not select the command")
        problems += [f"{label}: console error: {error}" for error in errors]
        context.close()
```

Restart the server and run the browser checks.
Expected: `0 problem(s) across 7 page(s)`. Look at `threshold-1280-dark-motion-on.png` and `threshold-375-light-motion-on.png`: the line centred both ways, COME IN below it, the mark and MENU in the top corners, and at 1280 PRIVATE · EPHEMERAL and the switches in the bottom corners. From 900px up the line sets in two lines, breaking after "moment," (spec 3.6); at 1024 its type is smaller than at 1280, because there the corners take more of the width. Below 900px it sets in three balanced lines.

- [ ] **Step 9: Commit**

```bash
git add backend/templates/chapters/threshold.html backend/static/js/behaviors/come-in.js backend/static/css/base.css \
  backend/templates/shell.html backend/pages/views.py backend/config/urls.py backend/pages/registry.py \
  backend/drop_core/views.py frontend/src/App.jsx frontend/src/Dashboard.jsx backend/pages/tests_chapters.py \
  backend/pages/tests_registry.py backend/drop_core/tests_site_pages.py backend/drop_core/tests.py site-tests/run_site_checks.py
# frontend/src/Landing.jsx is already staged as deleted by Step 5's git rm
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Move / onto the shell: the threshold and Come in

The threshold is the line and COME IN; Come in fades them out and the install
line in, alone and centred, with focus on Copy, and is a plain link to
/begin/ without JavaScript. The landing view moves to the pages app with its
funnel tracking unchanged. / leaves the React app, whose dashboard now links
home with a full page load.
EOF
```

---

### Task 9: Chapters that change in place

**Files:**
- Modify: `backend/static/js/shell.js`, `backend/static/css/base.css`, `backend/pages/context_processors.py`, `backend/templates/shell.html`, `site-tests/run_site_checks.py`
- Modify (tests): `backend/pages/tests_chapters.py`

**Interfaces:**
- Consumes: `<body data-layout>` and the `data-page-meta` head elements (Task 1); every chapter and the threshold (Tasks 2 to 8); the menu's `aria-current` marks, rendered by the server.
- Produces:
  - In `shell.js`, `setUpChapterChanges()`: a click on a link to one of the addresses in `<body data-layout-paths>` (no modifier keys, no `target`, no `download`, not `[data-come-in]`, not a jump within the page) fetches the target; any other link is an ordinary page load and is never fetched. When the answer is a 200 `text/html` page at that same address with the same `data-layout`, it fades `<main>` out (`.is-leaving`, `--fade`), swaps in the new `<main>` children, the `<title>` and every `[data-page-meta]` element, the body class and the menu's `aria-current` marks, records the address in history, scrolls to the top, fires `veilfile:swapped` on `document` (on a phone this closes the menu sheet and ends the page's inertness) and only then focuses the new `<h1>`. A history move that changes only the `#fragment` (the skip link) is left to the browser. Anything else is `location.assign(url)`. Back and forward swap the same way.
  - The latest click wins: a change that finishes after a newer one started is dropped.
  - `shell_page["layout_paths"]`: the space-separated paths of every registry page in the page's layout (empty when it has none), which `shell.html` writes to `<body data-layout-paths>`.
  - `setUpMenu` listens for `veilfile:swapped`: on a chapter, from 64rem up, the menu opens and pins; otherwise it closes.

- [ ] **Step 1: Write the failing test and browser checks**

Add to the class `ChapterPagesTests` in `backend/pages/tests_chapters.py`:

```python
    def test_a_chapter_names_the_pages_it_changes_to_in_place(self):
        """shell.js changes in place only to these addresses; a link anywhere else is an ordinary load."""
        found = re.search(r'data-layout-paths="([^"]*)"', self.client.get("/about/").content.decode())
        self.assertIsNotNone(found, "a chapter's <body> should carry data-layout-paths")
        self.assertEqual(set(found.group(1).split()), {"/", "/about/", "/how/", "/begin/", "/promises/", "/plans/"})
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_chapters.ChapterPagesTests -v 2`
Expected: FAIL: `a chapter's <body> should carry data-layout-paths`.

In `site-tests/run_site_checks.py`, add `import json` to the imports if it is not there, and add after `come_in_problems`:

```python
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
            page.wait_for_timeout(900)  # the fade back in
            return True
        except PlaywrightError:
            problems.append(f"{label}: never arrived at {path}")
            return False

    context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)
    page, errors = open_page(context, url)
    page.evaluate("() => { window.stayedPut = true; }")
    label = f"{site_page.path} to Plans in place"
    page.locator('#shell-menu a[href="/plans/"]').click()
    if arrive(page, "/plans/", "Plans", label):
        if not page.evaluate(stayed):
            problems.append(f"{label}: the whole page reloaded")
        head = page.evaluate("""() => [document.title, document.querySelectorAll('link[rel="canonical"]').length,
            document.querySelector('link[rel="canonical"]').href]""")
        if head != ["Plans | Veilfile", 1, "https://veilfile.com/plans/"]:
            problems.append(f"{label}: the title and canonical address read {head}")
        marks = page.evaluate("""() => [...document.querySelectorAll('#shell-menu a[aria-current]')]
            .map((a) => [a.textContent.trim(), a.getAttribute('aria-current')])""")
        if marks != [["Plans", "page"]]:
            problems.append(f"{label}: the menu marks {marks}")
        if not page.evaluate("() => document.activeElement === document.querySelector('main h1')"):
            problems.append(f"{label}: focus does not move to the new heading")
    page.go_back()
    if arrive(page, "/about/", "A quiet place for what your agents make.", f"{label}, then back") and \
            (not page.evaluate(stayed) or page.title() != "About | Veilfile"):
        problems.append(f"{label}, then back: About did not come back in place with its title")
    page.go_forward()
    arrive(page, "/plans/", "Plans", f"{label}, then forward")

    label = f"{site_page.path} with two chapters clicked at once"
    page.locator('#shell-menu a[href="/how/"]').click()
    page.locator('#shell-menu a[href="/begin/"]').click()
    if arrive(page, "/begin/", "Begin", label):
        page.wait_for_timeout(1500)
        if page.evaluate("() => location.pathname") != "/begin/" or page.evaluate(
                "() => document.querySelector('main').classList.contains('is-leaving')"):
            problems.append(f"{label}: the first click overrode the second, or the page stayed faded")

    label = f"{site_page.path} to the threshold by the mark"
    page.locator(".shell-corner--top-left .lockup").click()
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
    page.locator('main a[href="/app/"]').click()  # signed out, /app/ redirects to sign-in
    try:
        page.wait_for_url("**/accounts/login/**", timeout=5000)
        if page.evaluate(stayed) or fetched:
            problems.append(f"{label}: should be an ordinary page load, never fetched first (fetched {fetched})")
    except PlaywrightError:
        problems.append(f"{label}: never arrived at sign-in")

    label = "back to the threshold after Come in"
    page.goto(base_url + "/", wait_until="networkidle")
    page.evaluate("() => { window.stayedPut = true; }")
    page.locator("[data-come-in]").click()
    page.wait_for_timeout(1700)
    page.locator("header [data-menu-toggle]").click()
    page.locator('#shell-menu a[href="/begin/"]').click()
    if arrive(page, "/begin/", "Begin", label):
        page.go_back()
        try:
            page.wait_for_function("() => location.pathname === '/' && document.querySelector('[data-threshold-line]')"
                                   " && !document.querySelector('[data-threshold-line]').hidden", timeout=5000)
            if not page.evaluate(stayed) or page.locator("[data-install]").is_visible():
                problems.append(f"{label}: the threshold should come back in place, with the line and Come in")
        except PlaywrightError:
            problems.append(f"{label}: going back did not bring the line back")

    label = f"{site_page.path} to the Docs, another layout"
    page.locator("header [data-menu-toggle]").click()  # the threshold's menu starts closed
    page.locator('#shell-menu a[href="/docs/"]').click()
    try:
        page.wait_for_url("**/docs/", timeout=5000)
        if page.evaluate(stayed):
            problems.append(f"{label}: should load the Docs as an ordinary page")
    except PlaywrightError:
        problems.append(f"{label}: never arrived at the Docs")
    problems += [f"{site_page.path}, changing chapters: console error: {error}" for error in errors]
    context.close()

    # A fetch that fails, answers with an error or redirects away: an ordinary page load, never a blank page.
    for failure in ("abort", "500", "redirect"):
        context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion=None)

        def fail(route, failure=failure):
            if route.request.resource_type != "fetch":
                return route.continue_()
            if failure == "abort":
                return route.abort()
            if failure == "500":
                return route.fulfill(status=500, body="Server error")
            return route.fulfill(status=302, headers={"Location": "/accounts/login/?next=/promises/"})

        context.route("**/promises/", fail)
        page, errors = open_page(context, url)
        page.evaluate("() => { window.stayedPut = true; }")
        label = f"{site_page.path} to Promises when the fetch fails ({failure})"
        page.locator('#shell-menu a[href="/promises/"]').click()
        try:
            page.wait_for_url("**/promises/", timeout=5000)
            heading = (page.locator("main h1").text_content() or "").strip()
            if page.evaluate(stayed) or heading != "What we promise":
                problems.append(f"{label}: should have fallen back to an ordinary page load")
        except PlaywrightError:
            problems.append(f"{label}: never arrived at Promises")
        context.close()

    # Motion off: the change is instant.
    context = new_context(browser, base_url, session_key, site_page, width=1280, theme=None, motion="off")
    page, errors = open_page(context, url)
    page.locator('#shell-menu a[href="/plans/"]').click()
    page.wait_for_timeout(400)
    if (page.locator("main h1").text_content() or "").strip() != "Plans":
        problems.append(f"{site_page.path} with Motion off: the chapter change is not instant")
    context.close()

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
    page, errors = open_page(context, url)
    label = f"{site_page.path} to Plans in place on a phone"
    page.locator("header [data-menu-toggle]").click()
    page.locator('#shell-menu a[href="/plans/"]').click()
    if arrive(page, "/plans/", "Plans", label):
        if page.locator("#shell-menu").is_visible() or page.evaluate("() => document.querySelector('main').inert"):
            problems.append(f"{label}: the menu sheet stays open, or the page stays inert")
        if not page.evaluate("() => document.activeElement === document.querySelector('main h1')"):
            problems.append(f"{label}: focus does not move to the new heading")
    problems += [f"{label}: console error: {error}" for error in errors]
    context.close()
    return problems
```

In `edge_case_problems`, add at the end, before `return problems`:

```python
    if site_page.path == "/about/":  # the shell's in-place changes, tried once
        problems += chapter_change_problems(browser, base_url, session_key, site_page)
```

Run the browser checks.
Expected: exit 1, with `/about/ to Plans in place: the whole page reloaded` and similar lines (links are ordinary page loads today); the fallback, dashboard and Docs checks already pass (an ordinary load reaches the page, and nothing is fetched yet). With the phone check added below, `... to Plans in place on a phone: focus does not move to the new heading` appears too; the skip-link check passes for now (nothing swaps yet) and guards Step 3.

- [ ] **Step 2: Fade <main> during a change**

Append to `backend/static/css/base.css`:

```css
/* In-place chapter changes (shell.js, spec 4.5): the content fades out and back in; nothing slides. */
.shell-main {
  transition: opacity var(--fade) ease;
}

.shell-main.is-leaving {
  opacity: 0;
}
```

- [ ] **Step 3: Change chapters in place**

In `backend/pages/context_processors.py`, add `from django.urls import reverse` after the line `from django.templatetags.static import static`, and in `shell_page` add after the line `        "layout": page.layout,`:

```python
        # The only addresses shell.js changes to in place: every registry page in the same layout.
        "layout_paths": " ".join(reverse(other.url_name) for other in registry.PAGES
                                 if page.layout and other.layout == page.layout),
```

In `backend/templates/shell.html`, replace `{% if shell_page.layout %} data-layout="{{ shell_page.layout }}"{% endif %}>` with `{% if shell_page.layout %} data-layout="{{ shell_page.layout }}" data-layout-paths="{{ shell_page.layout_paths }}"{% endif %}>`.

In `backend/static/js/shell.js`:

Change the header comment's first line to `// The shell's behavior: the corner menu, the display preferences and in-place chapter changes (spec 4.4, 4.5, 6.4).`

After the line `const root = document.documentElement;`, add:

```js
const REDUCE = window.matchMedia("(prefers-reduced-motion: reduce)");
const FADE_MS = 700; // --fade in tokens.css
```

In `setUpMenu`, after the line `PHONE.addEventListener("change", updateInert);`, add:

```js
  // After an in-place change, the menu follows the new page: open and pinned on a chapter from 64rem
  // up, as on a fresh load; closed anywhere else (which also takes the phone sheet away).
  document.addEventListener("veilfile:swapped", () => {
    if (!isShellMenu) return;
    const chapter = !PHONE.matches && Boolean(list.querySelector('[aria-current="page"]'));
    setOpen(chapter, false);
    pinned = chapter;
  });
```

After the `setUpMotion` function, add:

```js
// In-place chapter changes (spec 4.5): a link to another page of the same layout swaps in that page's
// <main> and its marked <head> elements, so the shell stays put. Anything unexpected (an error, a
// redirect, another layout) becomes an ordinary page load. The latest click wins.
let changes = 0;
let shown = window.location.pathname + window.location.search; // the page now in <main>

function instant() {
  return root.getAttribute("data-motion") === "off" || REDUCE.matches;
}

async function changeTo(url, push) {
  const change = ++changes;
  const layout = document.body.dataset.layout;
  const main = document.querySelector("main");
  let next;
  try {
    const response = await fetch(url, { headers: { Accept: "text/html" }, credentials: "same-origin" });
    const type = response.headers.get("Content-Type") || "";
    if (!response.ok || !type.includes("text/html") || new URL(response.url).pathname !== new URL(url).pathname) {
      throw new Error("not this page");
    }
    next = new DOMParser().parseFromString(await response.text(), "text/html");
    if (!layout || next.body.dataset.layout !== layout || !next.querySelector("main")) throw new Error("another layout");
  } catch (error) {
    if (change === changes) window.location.assign(url);
    return;
  }
  if (change !== changes) return;
  if (!instant()) {
    main.classList.add("is-leaving");
    await new Promise((resolve) => setTimeout(resolve, FADE_MS));
    if (change !== changes) return;
  }
  main.replaceChildren(...next.querySelector("main").childNodes);
  document.title = next.title;
  document.head.querySelectorAll("[data-page-meta]").forEach((element) => element.remove());
  const meta = document.createDocumentFragment();
  next.head.querySelectorAll("[data-page-meta]").forEach((element) => meta.append(element));
  document.head.querySelector("title").after(meta);
  document.body.className = next.body.className;
  for (const link of document.querySelectorAll("#shell-menu a")) {
    const twin = next.querySelector(`#shell-menu a[href="${link.getAttribute("href")}"]`);
    const mark = twin && twin.getAttribute("aria-current");
    if (mark) link.setAttribute("aria-current", mark);
    else link.removeAttribute("aria-current");
  }
  if (push) history.pushState({ inPlace: true }, "", url);
  shown = new URL(url).pathname + new URL(url).search;
  window.scrollTo(0, 0);
  main.classList.remove("is-leaving");
  // First let the menu follow the page (on a phone this closes the sheet and ends the page's inertness),
  // then move focus: an inert heading cannot take it.
  document.dispatchEvent(new CustomEvent("veilfile:swapped"));
  const heading = main.querySelector("h1");
  if (heading) {
    heading.tabIndex = -1;
    heading.focus({ preventScroll: true });
  }
}

function setUpChapterChanges() {
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]");
    if (!link || event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if ((link.target && link.target !== "_self") || link.hasAttribute("download") || link.hasAttribute("data-come-in")) return;
    if (!document.body.dataset.layout) return;
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin) return;
    if (url.pathname === window.location.pathname && url.hash) return; // a jump within the page
    // Only a page of this layout can change in place; any other link is an ordinary page load, unfetched.
    if (!(document.body.dataset.layoutPaths || "").split(" ").includes(url.pathname)) return;
    event.preventDefault();
    changeTo(url.href, url.href !== window.location.href);
  });
  window.addEventListener("popstate", () => {
    // A jump within the page (the skip link, a #fragment) fires popstate too: the browser has done it.
    if (window.location.pathname + window.location.search === shown) return;
    if (document.body.dataset.layout) changeTo(window.location.href, false);
  });
}
```

In the final `try` block, add after the line `document.querySelectorAll("[data-motion-toggle]").forEach(setUpMotion);`:

```js
  setUpChapterChanges();
```

Leave the line `document.querySelectorAll("[data-menu]").forEach(setUpMenu);` exactly as it is: the site checks' "shell.js throws" run replaces that text.

- [ ] **Step 4: Run the checks and the tests**

Run the browser checks (the server serves `shell.js` straight from disk; no restart needed). Expected: `0 problem(s) across 7 page(s)`.
Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_static pages.tests_shell pages.tests_chapters -v 1`. Expected: OK, including the new `test_a_chapter_names_the_pages_it_changes_to_in_place` and the 10 KB budget (shell.js and the three behaviours come to about 5 KB gzipped).

- [ ] **Step 5: Commit**

```bash
git add backend/static/js/shell.js backend/static/css/base.css backend/pages/context_processors.py \
  backend/templates/shell.html backend/pages/tests_chapters.py site-tests/run_site_checks.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Change chapters in place

A link to another page of the same layout (the server lists them on
<body>) fetches it and swaps in its <main>, title and marked head
elements behind a cross-fade, records the address, and moves focus to the
new heading; the menu follows. The latest click wins, back and forward
work, Motion off makes it instant, and anything unexpected is an ordinary
page load. A link out of the chapters is never fetched.
EOF
```

---

### Task 10: Recipes, repo map, spec amendments, and the last checks

**Files:**
- Modify: `AGENTS.md`, `README.md`, `docs/superpowers/specs/2026-10-08-site-redesign-design.md`
- Modify (only if Step 4 says so): `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: everything above.
- Produces: the recipes Muse's teams follow for chapter words and components (spec 7.1, items 2 and 5).

- [ ] **Step 1: Add the two recipes**

In `AGENTS.md`, in `## Site recipes`, add these two recipes after the last step of "Write a Journal post" and before `## Docs and tests hygiene`:

````markdown
### Edit a chapter's words

Who: usually the CMO's team. The chapters are the threshold (`/`), About,
How it works, Begin, Promises and Plans.

1. Open `backend/templates/chapters/<name>.html`: `threshold`, `about`, `how`,
   `begin`, `promises` or `plans`. The install line's words (MCP, npm, pip and
   the line after Copy) are in `backend/pages/install.py`.
2. Change only the text between the line
   `{# Words: edit the text between here and the end marker; keep every tag, class and template tag as it is. #}`
   and the line `{# End of words. #}`. Keep every tag (`<h1 ...>`, `<p>`,
   `<dt>`, `<pre tabindex="0">`), every `class="..."` and every `{% ... %}`
   exactly as it is. A code block is `<pre tabindex="0"><code>...</code></pre>`:
   the tabindex lets keyboard users scroll a long command, and the browser
   checks fail without it.
3. Write plain, calm sentences: no em dashes, no exclamation marks, and none of
   genuinely, utilize, innovative, innovation, passionate about, excited to
   share, powerful force, leverage, great. Use `’` for apostrophes.
4. State only what the product does (`docs/API.md` is the reference). Never
   type a price or a limit: Plans takes every figure from
   `backend/drop_core/plans.py`.
5. A page's title and its summary in search results live in
   `backend/pages/registry.py`, not in the chapter: title at most 60
   characters, description 70 to 160.
6. Check it:
   `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages`.
   Every failure names the file and the fix.
7. Open a pull request.

### Change or add a component

Who: usually the CTO's team. A component is one piece of the shell, such as
the menu, a plan or the install line; `/app/components/` (staff only) shows
every one in Paper and Ink, Motion on and off.

1. Its template is `backend/templates/components/<name>.html`. Line 1 is
   `{# Component: <name>. What it is (spec section). #}`; line 2 is
   `{# Inputs: each input and what it holds. #}`.
2. Its one stylesheet is `backend/static/css/components/<name>.css`. Colours
   and sizes only as `var(--token)` from `backend/static/css/tokens.css`.
3. Link the stylesheet in `backend/templates/shell.html`, next to the others:
   `<link rel="stylesheet" href="{% static 'css/components/<name>.css' %}">`.
4. Add an `Entry` for it to `backend/pages/catalog.py`, with sample inputs. If
   its markup has ids, give it an `id_prefix` input.
5. Include it only as `{% include "components/<name>.html" with ... only %}`.
6. Add a rendering test, like `backend/pages/tests_install_line.py`.
7. Check it: the `pages` tests above, then the browser checks ("Site checks").
````

- [ ] **Step 2: Update the repo map and the README**

In `AGENTS.md`, `## Repo map`, replace

```
backend/templates/     Django templates: shell.html and components/ for the new shell;
                       older pages (plans, device flow, OAuth consent) until they move onto it
backend/pages/         public-page registry, sitemap, component catalog, content checks
backend/static/        the new shell's CSS, JS, fonts and images (no build step)
```

with

```
backend/templates/     Django templates: shell.html, layouts/, chapters/ (the threshold and
                       the chapters) and components/ for the new shell; older pages (Docs,
                       Journal, sign-in, device flow, OAuth consent) until they move onto it
backend/pages/         public-page registry and views (threshold, chapters), sitemap,
                       component catalog, content checks
backend/static/        the new shell's CSS, JS (js/behaviors/: tabs, Copy, Come in), fonts
                       and images (no build step)
```

and replace `frontend/              React + Vite dashboard and landing page (built into frontend/dist)` with `frontend/              React + Vite dashboard at /app/ (built into frontend/dist), until PR 6`.

In `README.md`, replace `  templates/       shell.html and components/, plus login/signup/plans/device pages` with `  templates/       shell.html, layouts/, chapters/ and components/, plus the older sign-in and device pages`, and replace `frontend/          React + Vite (base: '/static/'); / landing, /app/ dashboard` with `frontend/          React + Vite (base: '/static/'); the /app/ dashboard`.

- [ ] **Step 3: Amend the spec where the build refined it**

In `docs/superpowers/specs/2026-10-08-site-redesign-design.md`:

- §4.3, How it works: replace `The 256-bit token in the address is the only key, and nothing is listed or indexed.` with `The 256-bit token in the address is the only key; there is no public list of files, and nothing is indexed.`, and `*Let go:* when its days run out, the link stops working and the file is deleted.` with `*Let go:* when its days run out, the link stops working at once, and the file is deleted from storage soon after.`
- §4.3, Promises: replace `*We look for secrets, not at your files:* uploads are scanned for the shapes of keys and tokens, and their contents are never logged.` with `*We look only for the shapes of secrets:* uploads are scanned for anything shaped like a key or a token, a match is flagged by name, and file contents are never logged. (The scanner reads the bytes, so "not at your files" would overstate it.)`
- §4.3, Begin: after `**MCP first,** with exact snippets for the main clients: Claude Code, the Claude apps, Cursor and Codex.` add ` The snippets are the Docs' own (backend/docs/pages/agent.md), kept equal by a test; the Claude apps get one sentence until the owner confirms their steps in the voice pass.`
- §6.5: after the sentence ending `whether it belongs in the sitemap.` add ` The menu's groups and their order are MENU in the same file; each Page holds its layout, which in-place chapter changes compare.`
- §6.6: replace `Open Graph tags and an image.` with `Open Graph tags and an image (img/og.png: the mark on paper, 1200 by 630).`
- §4.2, Agent-friendly addresses: replace everything from `the MCP address works in every MCP client` through `A user connects without ever pasting a key.` with the text below (the spec's own claim is wrong: only clients that support MCP sign-in sign in, which is what Begin and the Global Constraints say):

  ```text
  the MCP address is the same for every client. A client that supports MCP sign-in (OAuth) finds the server's sign-in through OAuth discovery and dynamic client registration (`docs/API.md`, "OAuth 2.0 authorization server"), asks the user to sign in and needs a verified email, so the user connects without pasting a key; a client that does not takes an API key, as Begin shows.
  ```

Each replacement must match exactly once; if one does not (the spec changed since this plan), make the smallest equivalent edit and record a ruling.

- [ ] **Step 4: Time the browser checks against CI's limit**

Run the browser checks with `time` in front. Expected: `0 problem(s) across 7 page(s)`, finishing in well under the `site` job's `timeout-minutes: 20` (the job also spends about 2 minutes installing). If the run takes more than 12 minutes locally, raise `timeout-minutes` for the `site` job in `.github/workflows/ci.yml` to 30 and record a ruling; otherwise leave CI alone.

- [ ] **Step 5: Bring the branch up to date and run everything**

```bash
git fetch origin && git rebase origin/main
```

Resolve any conflict in favour of keeping both changes (Muse's teams push to `main`; nothing they change should be dropped). A modify/delete conflict on `about.html`, `plans.html`, `trust.html` or `Landing.jsx` means someone edited words in a page this PR deletes: keep the deletion and carry their wording into the matching chapter template between its words markers (for Plans, into `plans.py` or `chapters/plans.html` as fits), and record a ruling. Then run, from the repo root:

- the full suite (Global Constraints). Expected: OK.
- `cd backend && DJANGO_DEBUG=False DJANGO_SECRET_KEY=local-check-not-a-secret DJANGO_ALLOW_SQLITE=True ../venv/bin/python manage.py collectstatic --noinput --verbosity 1 && rm -rf staticfiles`. Expected: a `static files copied` line and no traceback.
- `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py makemigrations --check --dry-run`. Expected: `No changes detected`.
- the browser checks, after restarting the server. Expected: `0 problem(s) across 7 page(s)`.

- [ ] **Step 6: Commit**

```bash
git add AGENTS.md README.md docs/superpowers/specs/2026-10-08-site-redesign-design.md
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -F - <<'EOF'
Recipes for chapter words and components; repo map and spec follow the build

Two step-by-step recipes for Muse's teams (the words in a chapter, and a
component's five parts), the repo map and README updated for layouts,
chapters and behaviours, and the spec amended where the chapters' copy was
worded against the code.
EOF
```
