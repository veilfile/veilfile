# Site redesign PR 4: the reading pages (Journal, Docs, legal) — Implementation Plan

**Goal:** Move the Journal, Docs and the legal documents onto the new shell as one reading layout (a calm Newsreader column over the cloth) with section lists, previous and next links, head metadata and structured data on every page, a full-text RSS feed and browser checks, without changing a word of the legal documents.

**Architecture:** A new `layouts/reading.html` (body class `layout-reading`) extends `shell.html`, as `layouts/chapter.html` does for chapters. The registry gains a `reading` layout family: the five reading registry pages, every published Journal post and every Docs page. The context processor builds head entries for posts and Docs pages from their loaders, so every reading page has a title, description, canonical address, Article structured data where it is an article, `data-layout="reading"` and the family's paths. In-place changes then run within the family with the `shell.js` that exists, unchanged. A new `render_reading()` adds heading ids and a section list to the shared Markdown pipeline. Reading typography (headings, lists, tables, code, blockquotes, emphasis, long words) is scoped to `.reading`.

**Tech Stack:** Django 6.1 templates, `markdown==3.11` (its bundled `toc` extension; no new dependency), plain CSS tokens, the existing ES-module behaviours (`tabs.js`, `copy.js`), Django's syndication framework, Playwright for Python 1.49.1 site checks, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-08-site-redesign-design.md` (as amended through PR #15), sections 4.4 (the menu), 4.5 (in-place changes), 4.6 (reading pages), 6.2 (templates and component rules), 6.5 (registry), 6.6 (search: Article structured data), 7.1 (Muse's recipes), 9 (accessibility), 10 (testing and CI) and 11 step 4.

## Global Constraints

- **Branch:** `design/site-redesign-pr4`, cut from `origin/main` at or after 7d94ea2 (PR #15 merged: the "like Earendil" changes, the phone menu's per-frame driver, `code_block`, and the `site-ink` job). Before starting, `git fetch origin && git log --oneline HEAD..origin/main -- backend site-tests .github` must print nothing.
- **Commits:** `git -c user.name=Veilfile -c user.email=support@veilfile.com commit`, with the message the commit step gives and nothing added: no `Co-Authored-By`, no session links, no mention of any AI or model. The checkout has untracked `.DS_Store` and `.claude/`; a fresh worktree also shows the linked `venv`, `site-tests/.venv` and `site-tests/node_modules`. Stage files by name, never `git add -A`.
- **Tests:** `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test <labels>`; the whole suite without labels takes about 3.5 minutes. Every task ends with the whole suite green.
- **Browser checks:** dev server `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py runserver 127.0.0.1:8777 --noreload`; checks `site-tests/.venv/bin/python site-tests/run_site_checks.py --base-url http://127.0.0.1:8777 --session-file site-tests/session.txt --out site-tests/artifacts [--part pages|cloth|ink|reading|all]`. Restart the server after template or Python changes. Before the first browser run in a fresh worktree, make the database and the staff session, from the repo root: `(cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py migrate --noinput && SITE_SESSION_FILE=../site-tests/session.txt DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py shell -c "exec(open('../site-tests/make_staff_session.py').read())")` (AGENTS.md, "Site checks"). A worktree's `backend/db.sqlite3` starts empty and a `site-tests/session.txt` made for another database names no session, so without this the catalog answers 500 (`no such table: django_session` in the server log) and the run stops at its first check. Before starting the server make sure nothing already listens on its port (`lsof -ti tcp:8777` prints nothing): the health check cannot tell your server from an old one, and the checks would run against the old one. On a laptop put `caffeinate -ims` in front of every browser run: an idle sleep in the middle of a part freezes the process while the wall clock runs, and the part then fails with a Playwright `TimeoutError` (`Page.wait_for_function`, 240000 ms, in `cloth_top_fade_problems`) or reports times that are not its cost. CI runs each part in its own job with a 20-minute limit: `site` (pages, with a WebKit pass for the phone menu sheet), `site-cloth`, `site-ink`, and the `site-reading` this PR adds. CI's Linux Chromium reports `pointer: none` and `hover: none`, so hover CSS is gated on `not all and (pointer: coarse)`, never on `(hover: hover)`. Never attach extra CDP sessions to the checks' pages.
- **Legal text is the owner's** (AGENTS.md invariant 8): never edit `backend/legal/documents/*`. Each document's rendered text must stay word-for-word the same; a test in Task 4 compares it with the Markdown source rendered the old way.
- **No new dependencies:** `markdown==3.11` stays, and its `toc` extension ships with it.
- **Budgets:** `shell.js` plus `behaviors/*.js` at most 10,240 B gzipped, and 10,218 B are used after PR #15: this PR adds nothing to them, and must not. `veil.js` at most 15 KB gzipped.
- **Addresses do not change:** `/blog/`, `/blog/<slug>/`, `/blog/feed.xml`, `/docs/`, `/docs/<slug>/`, `/terms/`, `/privacy/`, `/report-abuse/`, and the retired-slug redirects.
- **Voice:** no em dashes, no exclamation marks, and none of the banned words in `pages/tests_chapters.py` in any copy this PR adds (labels, the meta line, link text).
- **Accessibility (spec 9):** small text in `--muted`; one `h1` per page, inside `<main>`; the section list and previous/next are each a `<nav>` with its own label; never a `<footer>` or `<header>` element inside `<main>` (base.css styles the bare elements: `:root:not(.js) footer { display: none }`); focus visible; tables that scroll are focusable regions (the renderer marks them), and code blocks are the `code_block` component's focusable `pre` with its Copy button (Task 1).
- **Components (spec 6.2):** each starts with a comment listing its inputs, is included only with `{% include "components/<name>.html" with … only %}`, has exactly one stylesheet `static/css/components/<name>.css`, a catalog entry and a rendering test.
- **Muse keeps this site** (spec 1.4, 7): every new convention has a test whose failure message names the file and the fix; AGENTS.md stays true after every task.

## Review Focus

1. **Long unbreakable content on a 320 px phone** (a long URL in a paragraph, a wide table, a long code line, a long word in a heading): the page never scrolls sideways; a code block or table scrolls inside its own box, and a long word or URL in running text wraps. Tests: Task 3 (CSS rendering test) and Task 7 (sideways checks on every reading page at 320, 375 and 1280).
2. **Moving between reading pages and out of them:** Journal, a post, the next post, Docs, a Docs page and Terms change in place; a reading page to a chapter, or back, is an ordinary load; Back and Forward restore; a section-list link (`#id`) is an ordinary jump to its heading, landing below the phone's top fade; the menu starts closed on reading pages, as on every page (the menu no longer pins open anywhere, as on earendil.com: that change lands with `fix/phone-scale`). Tests: Task 2 (layout paths), Task 7 (in-place, section-link and menu checks).
3. **Pages with less than the templates expect:** a page with fewer than two `##` headings shows no section list; the newest and oldest posts have one neighbour; a future-dated post is neither listed, linked as a neighbour, nor in the family's paths; an unknown slug is a plain 404 with no head entry and no error from the context processor. Tests: Tasks 2, 5 and 6.
4. **The legal documents' words and emphasis:** text content identical to before; `**bold**` still reads as bold although Newsreader has no bold face (weight 500 is the heaviest); the "Last updated" line stays a plain paragraph. Tests: Task 4.
5. **Feeds and search:** the feed keeps its address and its items, carries each post's full text, and every link in it is absolute on the same origin as the canonical addresses; each post has BlogPosting JSON-LD and each Docs page TechArticle, swapped with the page during in-place changes; canonicals are absolute and unique. Tests: Task 2 (head), Task 6 (feed).

## Rulings made while planning (the spec leaves these open)

- **One reading family** for Journal, Docs and legal: in-place changes run across all of them, never into chapters.
- **The menu** starts closed on every page at every width and never pins open, as on earendil.com (the owner's call, 2026-10-16). That change, with its spec 4.4 amendment and its checks, landed in PR #14, so this PR changes nothing in the menu.
- **Section lists** (spec: Docs) appear on Docs pages and the legal documents, which are long. They list `##` headings only, and are left out when a page has fewer than two. Journal posts have none. On a Docs page the list follows the title; on a legal document it sits just before the first section, so the "Last updated" line stays under the title.
- **A way back:** a Docs page's meta line above its title links to the Docs index ("DOCS"), and a post's meta line links its first word, "Journal", to the Journal. They replace the old pages' list of every Docs page.
- **Legal links live in the menu only,** as on earendil.com (the owner's call, 2026-10-16). The "terms · privacy · report abuse" line at the foot of the old Journal, Docs and legal pages goes with those templates.
- **Docs pages end with previous and next** by `order`, as Journal posts do by date ("previous" is the older post).
- **Long words and URLs in running text wrap** (`overflow-wrap: anywhere`); code blocks and tables scroll sideways in their own boxes (spec 4.6).
- **Bold in the reading column is weight 500**, the heaviest Newsreader face, with synthetic bold turned off, so `**bold**` is drawn in Newsreader and never in a fallback face.
- **Copy on every code block in the reading column** (the owner, 2026-10-16: "Begin now, Docs and Journal in PR 4"): `render_reading` draws each fenced block with the `code_block` component from PR #15, its button named from the fence's language and the heading above it; `render_markdown` (the feed, the legal text comparison) stays as it was, so the feed carries no Copy buttons.
- **Test failures Muse can read:** the new test classes set `longMessage = False`, so a failure prints the message that names the file and the fix, not the page's HTML first.
- **The RSS feed** is titled "Veilfile Journal" (the menu's word, and the shell's alternate link's title). Each item keeps the post's description as `<description>` and carries the full post HTML as `<content:encoded>`. Every link in it is absolute from `registry.SITE_ORIGIN`, the origin the canonical addresses use.
- **Heading ids** come from the `toc` extension's slugify; no visible permalink marks.
- **Structured data:** `BlogPosting` for posts and `TechArticle` for Docs pages (both are kinds of `Article`, spec 6.6), beside the site-wide `Organization`. Index and legal pages carry `Organization` only.

## File structure

```
backend/blog/render.py                      render_reading(): heading ids, sections and Copy on code blocks (Task 1)
backend/pages/registry.py                    layout "reading"; layout_paths(); dynamic entries (Task 2)
backend/pages/context_processors.py          shell_page for blog-post and docs-page (Task 2)
backend/pages/templatetags/…                 Article JSON-LD in the structured_data tag (Task 2)
backend/templates/shell.html                 og:type article and published time for articles (Task 2)
backend/templates/layouts/reading.html       the reading layout (Task 3)
backend/templates/components/{sections,pager,entries}.html  + static/css/components/*.css (Task 3)
backend/static/css/base.css (or a layout stylesheet)  .reading typography; prose links in li/td/blockquote (Task 3)
backend/static/js/veil.js                    INK_TARGETS include the reading column's prose links (Task 3)
backend/legal/views.py, templates/legal/document.html        legal on the reading layout (Task 4)
backend/docs/{views,loader}.py, templates/docs/{index,page}.html, docs/pages/quickstart.md (raw HTML only)  Docs (Task 5)
backend/blog/{views,loader,feeds}.py, templates/blog/{index,post}.html  the Journal and its feed (Task 6)
site-tests/run_site_checks.py, .github/workflows/ci.yml, backend/pages/tests_site_parts.py  reading checks and the site-reading job (Task 7)
AGENTS.md, README.md, the spec, templates/includes/*        recipes, amendments, old includes only the reading pages used (Task 8)
```

## Tasks

1. Heading ids, section lists and Copy buttons in the Markdown pipeline
2. The reading family, and head entries for posts and Docs pages
3. The reading layout, its components, typography and ink
4. Legal on the reading layout
5. Docs on the reading layout
6. The Journal on the reading layout, and the feed
7. Browser checks for the reading pages, and the `site-reading` CI job
8. Recipes, the spec, cleanup and the last checks

Each task leaves the whole Django suite green and is committed on its own. Tasks 4 to 6 each move one section's pages and update the old-markup tests for those pages only; the task that moves the last of them removes what nothing tests any more.

---

### Task 1: Heading ids, section lists and Copy buttons in the Markdown pipeline

**Files:**
- Modify: `backend/blog/render.py`
- Modify (tests): `backend/drop_core/tests_fix_web.py` (a new class `RenderReadingTests` beside `ContentGuardTests`, which already renders every Journal, Docs and legal file; `render_markdown`'s other test, `test_render_markdown_marks_scroll_regions`, is in `backend/drop_core/tests.py` and does not change)
- Read, not changed: `backend/templates/components/code_block.html` (on main since the phone fixes, PR #15), its stylesheet `backend/static/css/components/code_block.css` (linked by `shell.html` on every shell page), and `backend/static/js/behaviors/copy.js` (Copy)

**Interfaces:**
- Consumes:
  - `markdown==3.11` and its bundled `toc` extension (no new dependency).
  - The component `components/code_block.html`, rendered with `render_to_string` and three plain strings: `id` (unique on the page; Copy reads the code by it), `code` (the snippet's text; the template escapes it) and `label` (the button's accessible name; the visible word stays Copy). It draws `<div class="code-block" data-code-block>` holding a Copy button (`data-copy` is the id), `<pre tabindex="0" id="…"><code>…</code></pre>` and a status paragraph of its own. `copy.js` finds that status with `closest("[data-tabs], [data-code-block]")`, so each block on a page speaks in its own. `pages.tests_code_block` pins the component, and `pages/catalog.py` already describes it as used "on Begin, in the Docs and in the Journal". This task adds no component, stylesheet, script or catalog entry.
- Produces, all in `backend/blog/render.py`:
  - `Section`, a frozen dataclass: `id: str` (the heading's `id` attribute), `title: str` (the heading's plain text, markup removed, not HTML-escaped).
  - `Reading`, a frozen dataclass: `html: str` (a plain `str` like `render_markdown`'s, trusted HTML: templates print it with `|safe`), `sections: tuple[Section, ...]` (every `##` heading in order, `()` when there are none).
  - `render_reading(text: str) -> Reading`: the HTML `render_markdown(text)` makes, with two differences and no other: an `id` on its headings, and each code block (a fence or an indented block) drawn by `components/code_block.html` instead of a bare `<pre>` in a scroll region. Tables, and everything else, come out as `render_markdown` writes them.
    - A block's id is `code-block-<n>`, `n` counting from 1 in page order and skipping any number whose id the page already has (a heading "Code block 1" has the id `code-block-1`; raw HTML has ids of its own), so ids are unique on the page.
    - A block's `code` is its text exactly as the author typed it.
    - A block's `label` is "Copy the", the fence's language if it names one, "code", and "under" with the plain text of the heading above if there is one: "Copy the bash code under Upload", "Copy the code under MCP", "Copy the code" before any heading. A label the page already has gets " (2)", " (3)" after it. Why: the heading says which block of many (the agent page has nine, five of them in the same language, `json`), the language tells apart two blocks under one heading (Upload has a `bash` and a `json`), and the number is only the last resort, so a screen reader's list of buttons never holds two of one name.
  - `render_markdown(text)` keeps its signature and its output, byte for byte: no Copy button, no `data-code-block`. The feed (Task 6) and the legal text comparison (Task 4) use it, and a Copy button has no place in RSS.
  - What other tasks' tests will see in a reading page: a code block is no longer `<pre tabindex="0" role="region" aria-label="Scrollable code block">` but the component's `<pre tabindex="0" id="code-block-<n>">` (as on Begin), and the page's text gains the word "Copy" and an empty status paragraph for each block. A test that compares a page's words with its Markdown must leave `[data-code-block]` out or count those words. The legal documents have no code blocks.

**What the `toc` extension does here** (checked against Markdown 3.11 in the project's venv, so the code below is not guesswork):
- It puts an `id` on every heading, `h1` to `h6`, whatever `toc_depth` says (that setting only limits its token list). So `h1` and `h4` to `h6` get ids as well as `h2` and `h3`. That is harmless: the page's `h1` is unique, and an id on it is never linked to.
- The id is the heading's text with tags removed, then: Unicode-normalised to ASCII (`Café` becomes `cafe`; a curly apostrophe is dropped, so `Don’t` becomes `dont`), everything except letters, digits, `_`, space and `-` removed, lower-cased, runs of spaces turned into one hyphen. Inline code, emphasis and links in a heading change nothing: `` Using `veilfile` with *care* `` has the id `using-veilfile-with-care`. A heading with no ASCII letters or digits at all (`日本語`, `!!!`) gets `_1`, then `_2`; none of our headings does.
- Duplicates are made unique across all heading levels of one page by appending `_1`, `_2`: `Notes`, `Notes`, `Notes` become `notes`, `notes_1`, `notes_2`. It does not know the `id`s inside raw HTML (the quickstart's), so a test below guards against a clash with those.
- Numbered headings (the legal documents, the quickstart: `## 3. Upload`) give ids that start with a digit (`3-upload`) and titles that keep the number. Both are valid, but see the notes for the browser checks and the section list.
- Trap 1: by default it replaces a paragraph that is only `[TOC]` with a generated contents list. `marker=""` turns that off.
- Trap 2: the token list nests: with the default depth every `##` sits inside the `#` before it (the legal documents start with `#`), and a bare number as `toc_depth` still counts from `#`. `toc_depth="2-2"` gives a flat list of the `##` headings only.
- A token's `name` is the text with tags removed but still HTML-escaped (`A & B` is `A &amp; B`, `` `a<b>` `` is `a&lt;b&gt;`). Templates escape what they print, so `Section.title` is `html.unescape(name)`.
- It sees only headings the Markdown parser makes: not `##` lines inside code fences, and not raw-HTML headings (there are none in the content; AGENTS.md says posts are Markdown only).

**What the parser writes for a code block, and what is done with it** (checked against Markdown 3.11):
- A fence and an indented block both come out as `<pre><code>TEXT\n</code></pre>`; a fence with a language is `<code class="language-bash">`. TEXT is escaped (`&`, `<`, `>` and `"`; an apostrophe stays as typed), so it holds no `<`, and it ends in one newline the author did not type (an empty fence has none). A fence with `{ }` attributes (`` ``` { .py #name } ``) is `<pre id="name">`, and a raw HTML `<pre>` with tags inside it is left as it is: neither is a plain code block, and a test below fails a content file that has one.
- The `fenced_code` extension hands its blocks to the parser as ready-made HTML, so no tree step sees them: the blocks are found in the finished HTML, in one pass that also notes each heading (the text of the heading above is the label's). That happens after `md.convert` (the `toc` extension has written every heading id by then, so a block's id can skip them) and before the scroll regions are marked (the component's `<pre>` is not the bare `<pre>` that step looks for; tables still get their region).
- The code is passed as the decoded text, less that one newline, and the template escapes it again: what a reader sees and what Copy puts on the clipboard (`copy.js` takes the `<pre>`'s `textContent`, trimmed) is what the author typed.

- [ ] **Step 1: Give the test module what the new tests need**

In `backend/drop_core/tests_fix_web.py`, make four edits.

Replace this exact text (the end of the module docstring):

```python
dashboard CSRF cookie (web-11), and optional artifact-list pagination
(web-10)."""
```

with:

```python
dashboard CSRF cookie (web-11), optional artifact-list pagination
(web-10), and heading ids, code blocks and section lists for the
reading pages."""
```

Replace this exact text:

```python
import os
import shutil
```

with:

```python
import os
import re
import shutil
```

Replace this exact text:

```python
from html.parser import HTMLParser
```

with:

```python
from html import unescape
from html.parser import HTMLParser
```

Replace this exact text:

```python
from django.test import Client, TestCase
```

with:

```python
from django.test import Client, SimpleTestCase, TestCase
```

- [ ] **Step 2: Write the failing tests**

In the same file, insert the following block after the last test of `ContentGuardTests` (`test_guard_allows_the_docs_shell_switcher_markup`) and before the line `class AppShellCsrfCookieTests(TestCase):`, with two blank lines before and two after it. (`_TagCollector`, `Path`, `HTMLParser` and the other helpers it uses are already in the module. The block imports `blog.render` inside the tests, as the module's other tests do, so a missing function fails the tests and not the whole module. `longMessage = False` makes a failure print only the message, which names the file and the fix, and not the page-sized HTML that was compared.)

````python
_HEADING_ID_RE = re.compile(r'(<h[1-6]) id="[^"]*">')
# render_markdown's code block (after its scroll region is marked) and the code_block
# component's: both give the escaped text of the block, as group 1.
_MARKDOWN_CODE_RE = re.compile(
    r'<pre tabindex="0" role="region" aria-label="Scrollable code block">'
    r'<code(?: class="[^"]*")?>(.*?)</code></pre>',
    re.DOTALL,
)
_COMPONENT_CODE_RE = re.compile(
    r'<div class="code-block" data-code-block>.*?<code>(.*?)</code>.*?</div>',
    re.DOTALL,
)


def _headings(html):
    """The (tag, id) of every heading, h1 to h6, in ``html``, in document order."""
    parser = _TagCollector()
    parser.feed(html)
    parser.close()
    return [
        (tag, dict(attrs).get("id"))
        for tag, attrs in parser.tags
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6")
    ]


def _all_ids(html):
    """Every id attribute in ``html``, in document order, repeats included."""
    parser = _TagCollector()
    parser.feed(html)
    parser.close()
    return [value for _tag, attrs in parser.tags for name, value in attrs if name == "id"]


def _copy_buttons(html):
    """The (data-copy, aria-label) of every Copy button in ``html``, in document order."""
    parser = _TagCollector()
    parser.feed(html)
    parser.close()
    return [
        (dict(attrs).get("data-copy"), dict(attrs).get("aria-label"))
        for tag, attrs in parser.tags
        if tag == "button" and dict(attrs).get("data-copy")
    ]


class _PreText(HTMLParser):
    """The text of every <pre> in the HTML fed to it, tags removed and character
    references decoded: what a reader sees, and what Copy takes."""

    def __init__(self):
        super().__init__()
        self.texts = []
        self._inside = False

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self.texts.append("")
            self._inside = True

    def handle_endtag(self, tag):
        if tag == "pre":
            self._inside = False

    def handle_data(self, data):
        if self._inside:
            self.texts[-1] += data


def _pre_texts(html):
    parser = _PreText()
    parser.feed(html)
    parser.close()
    return parser.texts


def _same_blocks(pattern, html):
    """``html`` with each code block (found by ``pattern``) replaced by its text, so two pages
    can be compared however each draws a block. The text is decoded and loses the one newline
    the Markdown parser ends a block with."""

    def text_of(match):
        text = unescape(match[1]).removesuffix("\n")
        return f"<<code {text!r}>>"

    return pattern.sub(text_of, html)


def _reading_sources():
    """(file, Markdown text) for every Journal post, Docs page and legal document: the text
    the views hand to the renderer (front matter already taken off)."""
    from blog import loader as blog_loader
    from docs import loader as docs_loader

    backend = Path(__file__).resolve().parent.parent
    sources = []
    for path in sorted((backend / "blog" / "posts").glob("*.md")):
        _meta, body = blog_loader._parse_frontmatter(path.read_text(encoding="utf-8"))
        sources.append((f"backend/blog/posts/{path.name}", body))
    for path in sorted((backend / "docs" / "pages").glob("*.md")):
        _meta, body = docs_loader._parse_frontmatter(path.read_text(encoding="utf-8"))
        sources.append((f"backend/docs/pages/{path.name}", body))
    for path in sorted((backend / "legal" / "documents").glob("*.md")):
        sources.append((f"backend/legal/documents/{path.name}", path.read_text(encoding="utf-8")))
    return sources


class RenderReadingTests(SimpleTestCase):
    """render_reading() in backend/blog/render.py: render_markdown's HTML with an id on every
    heading, each code block drawn by components/code_block.html, and the list of a page's `##`
    sections, for the reading pages (Docs, Journal, legal)."""

    # A failure shows the message (it names the file and the fix), not the page-sized HTML.
    longMessage = False

    def _render(self, text):
        from blog.render import render_reading

        return render_reading(text)

    def test_h2_and_h3_headings_get_ids(self):
        reading = self._render("## First part\n\ntext\n\n### A detail\n\nmore\n")
        message = "backend/blog/render.py: render_reading must give headings ids (the toc extension)"
        self.assertIn('<h2 id="first-part">First part</h2>', reading.html, message)
        self.assertIn('<h3 id="a-detail">A detail</h3>', reading.html, message)

    def test_every_heading_level_gets_an_id(self):
        # The toc extension gives every heading an id whatever toc_depth says (that setting
        # only limits the section list), so the legal documents' own `#` heading has one too.
        reading = self._render("# One\n\n## Two\n\n### Three\n\n#### Four\n\n##### Five\n\n###### Six\n")
        self.assertEqual(
            _headings(reading.html),
            [("h1", "one"), ("h2", "two"), ("h3", "three"), ("h4", "four"), ("h5", "five"), ("h6", "six")],
        )

    def test_sections_are_the_h2_headings_in_order_with_plain_text_titles(self):
        from blog.render import Section

        reading = self._render(
            "# Page title\n\n"
            "## Using `veilfile` with *care*\n\ntext\n\n"
            "### A detail under it\n\ntext\n\n"
            "## Fish & chips, **bold** and [a link](/docs/)\n\ntext\n\n"
            "## What's next?\n"
        )
        self.assertEqual(
            reading.sections,
            (
                Section(id="using-veilfile-with-care", title="Using veilfile with care"),
                Section(id="fish-chips-bold-and-a-link", title="Fish & chips, bold and a link"),
                Section(id="whats-next", title="What's next?"),
            ),
            "backend/blog/render.py: sections must be the ## headings only, in order, flat "
            '(toc_depth="2-2"), with their text and no markup',
        )

    def test_section_titles_are_text_not_html(self):
        # Templates escape what they print, so a title must hold the characters themselves:
        # a title of "Fish &amp; chips" would be shown as "Fish &amp;amp; chips".
        reading = self._render("## Fish &amp; chips\n\n## 1 < 2 > 0\n\n## `a<b>`\n")
        self.assertEqual(
            [section.title for section in reading.sections],
            ["Fish & chips", "1 < 2 > 0", "a<b>"],
            "backend/blog/render.py: render_reading must html.unescape each token's name",
        )

    def test_a_numbered_heading_keeps_its_number_in_the_title_and_the_id(self):
        # The legal documents and the quickstart number their sections ("## 3. Upload").
        reading = self._render("## 3. Upload\n")
        self.assertEqual([(s.id, s.title) for s in reading.sections], [("3-upload", "3. Upload")])

    def test_results_are_frozen_values(self):
        from dataclasses import FrozenInstanceError

        reading = self._render("## One\n\n## Two\n")
        self.assertIsInstance(reading.sections, tuple)
        with self.assertRaises(FrozenInstanceError):
            reading.sections[0].title = "Changed"
        with self.assertRaises(FrozenInstanceError):
            reading.html = ""

    def test_duplicate_headings_get_distinct_ids_and_the_sections_match_the_html(self):
        reading = self._render(
            "## Notes\n\none\n\n## Notes\n\ntwo\n\n### Notes\n\nthree\n\n## Notes\n\nfour\n"
        )
        headings = _headings(reading.html)
        ids = [heading_id for _tag, heading_id in headings]
        self.assertEqual(ids, ["notes", "notes_1", "notes_2", "notes_3"])
        self.assertEqual(
            [section.id for section in reading.sections],
            [heading_id for tag, heading_id in headings if tag == "h2"],
            "backend/blog/render.py: a section's id must be the id of the h2 it names in the HTML",
        )
        self.assertEqual([section.title for section in reading.sections], ["Notes", "Notes", "Notes"])

    def test_a_text_with_no_h2_has_no_sections(self):
        for name, text in (
            ("empty", ""),
            ("a paragraph", "Just words.\n"),
            ("h1 and h3 only", "# Title\n\n### A detail\n\ntext\n"),
        ):
            with self.subTest(text=name):
                self.assertEqual(self._render(text).sections, ())

    def test_headings_in_code_are_not_sections(self):
        reading = self._render("```\n## not a heading\n```\n\n    ## nor this\n\n## Real\n")
        self.assertEqual(_headings(reading.html), [("h2", "real")])
        self.assertEqual([section.id for section in reading.sections], ["real"])

    def test_a_toc_marker_stays_text(self):
        # By default the toc extension swaps a paragraph that is only "[TOC]" for a generated
        # contents list. The Markdown here is the owner's: it must come out as typed.
        reading = self._render("Intro.\n\n[TOC]\n\nIn a sentence [TOC] too.\n\n## One\n\n## Two\n")
        self.assertNotIn(
            'class="toc"',
            reading.html,
            "backend/blog/render.py: render_reading must configure the toc extension with "
            'marker="" so a "[TOC]" in a page stays text',
        )
        self.assertIn("<p>[TOC]</p>", reading.html)
        self.assertIn("<p>In a sentence [TOC] too.</p>", reading.html)
        self.assertEqual([section.id for section in reading.sections], ["one", "two"])

    def test_tables_get_the_scroll_region_render_markdown_gives(self):
        html = self._render("| a | b |\n|---|---|\n| 1 | 2 |\n").html
        self.assertIn(
            '<div class="table-scroll" tabindex="0" role="region" aria-label="Scrollable table"><table>',
            html,
        )
        self.assertIn("</table></div>", html)

    def test_a_code_block_is_the_code_block_component_with_its_own_copy_button(self):
        # A fence with a language, a fence without, and an indented block: three blocks,
        # three components. The scroll box is the component's focusable <pre> (the one
        # Begin's snippets have), and its Copy button reads that <pre> by its id.
        reading = self._render(
            "```bash\nlong\n```\n\n```\nplain\n```\n\nText.\n\n    indented\n"
        )
        message = (
            "backend/blog/render.py: render_reading must draw each code block with "
            "components/code_block.html (id, code, label), once per block"
        )
        self.assertEqual(reading.html.count('<div class="code-block" data-code-block>'), 3, message)
        self.assertEqual(_all_ids(reading.html), ["code-block-1", "code-block-2", "code-block-3"], message)
        self.assertEqual(
            [button_id for button_id, _label in _copy_buttons(reading.html)],
            ["code-block-1", "code-block-2", "code-block-3"],
            "each Copy button must name the id of the <pre> in its own block",
        )
        self.assertIn('<pre tabindex="0" id="code-block-2"><code>plain</code></pre>', reading.html, message)
        self.assertNotIn("<pre>", reading.html, message)
        self.assertNotIn('aria-label="Scrollable code block"', reading.html, message)

    def test_a_blocks_text_is_the_text_of_the_block_exactly(self):
        # Copy copies the <pre>'s text, so that text must be what the author typed in the
        # fence: quotes, apostrophes, angle brackets, ampersands, backslashes, indentation,
        # braces that look like template tags, and a non-ASCII letter.
        first = (
            "curl -X POST \"https://example.test/a?b=1&c=2\" \\\n"
            "  -H 'Authorization: Bearer <key>' \\\n"
            "  -d '{\"name\": \"{{ name }}\", \"if\": \"{% if x %}\", \"home\": \"$HOME\"}' # café"
        )
        second = "    {\n      \"two\": 2\n    }\n\nafter a blank line"
        reading = self._render(f"## Upload\n\n```bash\n{first}\n```\n\n```\n{second}\n```\n\n    indented\n    block\n")
        self.assertEqual(
            _pre_texts(reading.html),
            [first, second, "indented\nblock"],
            "backend/blog/render.py: render_reading must pass each block's text to the component "
            "decoded (html.unescape) and without the newline the Markdown parser ends it with",
        )

    def test_block_ids_are_unique_and_skip_the_ids_a_heading_or_raw_html_has(self):
        # "Code block 1" has the id code-block-1; the raw HTML has code-block-3. Blocks must
        # not repeat either, however the page grows.
        reading = self._render(
            "## Code block 1\n\n```\na\n```\n\n"
            "## Code block 2\n\n```\nb\n```\n\n"
            '<div id="code-block-3">raw HTML</div>\n\n'
            "```\nc\n```\n"
        )
        ids = _all_ids(reading.html)
        self.assertEqual(
            ids,
            ["code-block-1", "code-block-4", "code-block-2", "code-block-5", "code-block-3", "code-block-6"],
            "backend/blog/render.py: a code block's id must skip every id the page already has "
            "(headings from the toc extension, ids in raw HTML)",
        )
        self.assertEqual(len(set(ids)), len(ids))

    def test_each_button_is_named_by_the_language_and_the_heading_above_its_block(self):
        reading = self._render(
            "```\nbefore any heading\n```\n\n"
            "## Upload\n\n```bash\none\n```\n\n```json\n{}\n```\n\n"
            "### Using `veilfile` with *care*\n\n```\nthree\n```\n\n"
            "## Fish & chips\n\n```bash\na\n```\n\n```bash\nb\n```\n\n```bash\nc\n```\n"
        )
        self.assertEqual(
            [label for _id, label in _copy_buttons(reading.html)],
            [
                "Copy the code",
                "Copy the bash code under Upload",
                "Copy the json code under Upload",
                "Copy the code under Using veilfile with care",
                "Copy the bash code under Fish & chips",
                "Copy the bash code under Fish & chips (2)",
                "Copy the bash code under Fish & chips (3)",
            ],
            "backend/blog/render.py: a button's label is 'Copy the', the fence's language, 'code', "
            "'under' and the heading above (plain text); a repeated label gets (2), (3)",
        )

    def test_render_markdown_has_no_copy_buttons(self):
        # The feed and the legal text comparison use render_markdown: a Copy button has no place
        # in RSS, and the legal documents' words are compared as they were.
        from blog.render import render_markdown

        html = render_markdown("```bash\nls\n```\n\n    indented\n")
        self.assertNotIn("data-code-block", html, "backend/blog/render.py: render_markdown must stay as it was")
        self.assertNotIn("<button", html)
        self.assertEqual(html.count('<pre tabindex="0" role="region" aria-label="Scrollable code block">'), 2)

    def test_reading_html_is_render_markdown_plus_heading_ids_and_code_blocks_for_every_content_file(self):
        # render_markdown's output cannot be compared before and after this change in one test, so
        # it is pinned from the other side: render_reading may differ from it by heading ids and by
        # the way each code block is drawn, and by nothing else. Heading ids are taken off both
        # (only heading ids: the quickstart's raw HTML carries ids of its own), and each code block
        # on each side is swapped for its text, so the blocks are compared by what they say.
        from blog.render import render_markdown

        sources = _reading_sources()
        self.assertEqual(
            {name.split("/")[1] for name, _text in sources},
            {"blog", "docs", "legal"},
            "a content folder is empty or moved: this test must read the Journal, Docs and legal files",
        )
        for name, text in sources:
            with self.subTest(file=name):
                reading = _same_blocks(_COMPONENT_CODE_RE, _HEADING_ID_RE.sub(r"\1>", self._render(text).html))
                markdown = _same_blocks(_MARKDOWN_CODE_RE, render_markdown(text))
                self.assertEqual(
                    reading,
                    markdown,
                    f"{name}: render_reading(text).html differs from render_markdown(text) by more "
                    "than heading ids and the drawing of its code blocks. Fix backend/blog/render.py: "
                    "render_reading may add the toc extension and _draw_code_blocks to what "
                    "render_markdown does, and nothing else.",
                )

    def test_every_code_block_in_a_content_file_has_one_copy_button(self):
        from blog.render import render_markdown

        for name, text in _reading_sources():
            with self.subTest(file=name):
                html = self._render(text).html
                blocks = render_markdown(text).count("<pre")
                # Only the buttons inside code blocks: the quickstart's raw-HTML switcher (Task 5) has Copy buttons of its own.
                in_blocks = [button for block in re.findall(r'<div class="code-block" data-code-block>.*?</div>', html, re.DOTALL)
                             for button in _copy_buttons(block)]
                self.assertEqual(
                    (html.count("<pre"), html.count("data-code-block"), len(in_blocks)),
                    (blocks, blocks, blocks),
                    f"{name} has {blocks} code blocks and each needs a Copy button. A block the page "
                    "shows without one is a fence with { } attributes, or a raw HTML <pre>: write it "
                    "as a plain fence (``` and an optional language). The legal documents are the "
                    "owner's: stop and ask.",
                )
                copy_buttons = _copy_buttons(html)
                for index, kind in ((0, "an id"), (1, "a name")):
                    values = [button[index] for button in copy_buttons]
                    self.assertEqual(
                        len(set(values)),
                        len(values),
                        f"{name}: two Copy buttons share {kind}. Fix backend/blog/render.py "
                        "(_draw_code_blocks numbers the ids and the repeated labels).",
                    )

    def test_every_id_in_a_rendered_content_file_is_unique(self):
        for name, text in _reading_sources():
            with self.subTest(file=name):
                ids = _all_ids(self._render(text).html)
                repeated = sorted({value for value in ids if ids.count(value) > 1})
                self.assertEqual(
                    repeated,
                    [],
                    f"{name} renders the id {repeated} more than once. Heading ids come from the "
                    "heading text: reword one of the headings, or change the id of the raw HTML "
                    "that shares it (code blocks skip taken ids, render.py). The legal documents "
                    "are the owner's: stop and ask.",
                )

    def test_ids_in_a_rendered_content_file_never_repeat_an_id_the_shell_puts_on_every_page(self):
        # A heading called "Main" would get id="main", the skip link's target. Only shell.html is
        # read here; an id a layout or component writes into its own markup belongs in this test too.
        from django.contrib.auth.models import AnonymousUser
        from django.template.loader import render_to_string
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        shell_ids = set(_all_ids(render_to_string("shell.html", {}, request=request)))
        self.assertIn("main", shell_ids, 'backend/templates/shell.html lost <main id="main">: update this test')
        for name, text in _reading_sources():
            with self.subTest(file=name):
                page_ids = set(_all_ids(self._render(text).html))
                self.assertEqual(
                    sorted(shell_ids & page_ids),
                    [],
                    f"{name} has an id that is also an id in backend/templates/shell.html "
                    "(the skip link's target is main). Reword the heading. The legal documents are "
                    "the owner's: stop and ask.",
                )
````

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_fix_web.RenderReadingTests`
Expected: `Ran 20 tests`, `FAILED (errors=53)`. Fourteen of the 20 tests error outright, one passes (`test_render_markdown_has_no_copy_buttons`, which pins that `render_markdown` stays as it is), and five run only subtests, which the runner counts: the 3 cases of `test_a_text_with_no_h2_has_no_sections` and the nine content files for each of the four tests that read them, so the 53 are 14 + 3 + 36. 52 of the 53 fail with `ImportError: cannot import name 'render_reading' from 'blog.render'`; only `test_sections_are_the_h2_headings_in_order_with_plain_text_titles` reports `cannot import name 'Section' from 'blog.render'`, because it imports `Section` first.

- [ ] **Step 3: Write the pipeline**

Replace the whole of `backend/blog/render.py` with:

```python
"""Markdown rendering for the Journal, Docs and the legal documents.

Uses the standard `markdown` package. `render_markdown` is the plain pipeline
(the feed and the legal text comparison use it). `render_reading` is the same
pipeline with an `id` on every heading, the list of the page's `##` sections,
and each code block drawn by the code_block component (a Copy button), for the
reading layout.
"""

import html as _html
import re
from dataclasses import dataclass

import markdown as _markdown
from django.template.loader import render_to_string

# Keyboard users cannot scroll an `overflow-x: auto` region unless it is in
# the tab order (WCAG 2.1.1). Code blocks and tables get focusable region
# wrappers so wide content is scrollable without a mouse. (A bare `<pre>` is
# what render_markdown writes; render_reading's code blocks are the
# code_block component's, whose `<pre>` is focusable already.)
_PRE_RE = re.compile(r"<pre>")
_TABLE_OPEN_RE = re.compile(r"<table>")

# What both pipelines understand. `render_reading` adds `toc` to these.
_EXTENSIONS = ["fenced_code", "tables"]

# Walking the HTML once, in order: a heading (its inner HTML) or a code block the Markdown
# parser wrote. Its code is escaped text, so it holds no `<`; a `<pre>` with tags inside it
# (raw HTML) or attributes on it is not matched and stays as it is. An optional
# `language-xxx` class is the fence's language.
_HEADING_OR_CODE_RE = re.compile(
    r"<h[1-6][^>]*>(?P<heading>.*?)</h[1-6]>"
    r'|<pre><code(?: class="language-(?P<language>[^"]*)")?>(?P<code>[^<]*)</code></pre>'
)
_TAG_RE = re.compile(r"<[^>]*>")
_ID_RE = re.compile(r'(?<![\w-])id="([^"]*)"')


@dataclass(frozen=True)
class Section:
    """One `##` heading: its `id` attribute in the HTML and its text without markup."""

    id: str
    title: str


@dataclass(frozen=True)
class Reading:
    """A rendered page. `html` is trusted HTML, like render_markdown's (the
    Markdown is the owner's and raw HTML in it passes through): print it with
    |safe. `sections` is every `##` heading in order, empty when there are none."""

    html: str
    sections: tuple[Section, ...]


def _mark_scroll_regions(html):
    html = _PRE_RE.sub(
        '<pre tabindex="0" role="region" aria-label="Scrollable code block">',
        html,
    )
    html = _TABLE_OPEN_RE.sub(
        '<div class="table-scroll" tabindex="0" role="region" '
        'aria-label="Scrollable table"><table>',
        html,
    )
    return html.replace("</table>", "</table></div>")


def _draw_code_blocks(html):
    """Draw every code block the Markdown parser wrote (a fence or an indented
    block) with components/code_block.html: a focusable scroll box under a Copy
    button (spec 4.3), the component Begin's snippets use. The component takes
    three plain strings.

    - `code` is the block's text: the parser's escaped text decoded, less the
      one newline it ends a block with. The component escapes it again, so what
      a reader sees and Copy copies is what the author typed.
    - `id` is `code-block-1`, `code-block-2` and so on in page order, skipping
      any number whose id the page already has (a heading called "Code block 1"
      has the id `code-block-1`; raw HTML has ids of its own). The ids are
      therefore unique on the page.
    - `label` is the button's accessible name; the visible word stays Copy. It
      is "Copy the", the fence's language if it names one, "code", and "under"
      with the text of the heading above if there is one: "Copy the bash code
      under Upload". The heading tells which block of many, and the language
      tells two blocks under one heading apart. A label the page already has
      gets "(2)", "(3)" after it, so no two buttons on a page share a name; a
      block with no language and no heading above is "Copy the code".
    """
    taken = set(_ID_RE.findall(html))
    labels = set()
    heading = ""
    number = 0

    def draw(match):
        nonlocal heading, number
        if match["heading"] is not None:
            heading = _html.unescape(_TAG_RE.sub("", match["heading"])).strip()
            return match[0]
        number += 1
        while f"code-block-{number}" in taken:
            number += 1
        block_id = f"code-block-{number}"
        language = f"{match['language']} " if match["language"] else ""
        label = f"Copy the {language}code"
        if heading:
            label += f" under {heading}"
        name, repeat = label, 1
        while name in labels:
            repeat += 1
            name = f"{label} ({repeat})"
        labels.add(name)
        context = {
            "id": block_id,
            "code": _html.unescape(match["code"]).removesuffix("\n"),
            "label": name,
        }
        return render_to_string("components/code_block.html", context).strip()

    return _HEADING_OR_CODE_RE.sub(draw, html)


def render_markdown(text):
    html = _markdown.markdown(text, extensions=_EXTENSIONS)
    return _mark_scroll_regions(html)


def render_reading(text):
    """The HTML `render_markdown(text)` makes, with an `id` on its headings and
    each code block drawn by the code_block component, and the page's `##`
    sections.

    The `toc` extension gives the ids (its slugify: lower case, ASCII, words
    joined by hyphens; a repeated heading gets `_1`, `_2` after its id) and
    keeps the heading list in `toc_tokens`. Two settings matter:

    - `marker=""`: by default the extension replaces a paragraph that is only
      `[TOC]` with a generated contents list. The owner's text must come out
      as typed.
    - `toc_depth="2-2"`: the list holds the `##` headings only, flat. A single
      number counts from `#` and nests every `##` under its `#`. Every heading,
      `#` to `######`, gets an id whatever the depth; that is how the
      extension works, and an id on a `#` does no harm.

    A token's `name` is the heading's text with the tags taken off but still
    HTML-escaped (`&amp;`), so it is unescaped here: templates escape what
    they print.

    The code blocks are drawn after the ids exist (so no block's id can repeat
    a heading's) and before the scroll regions are marked (the component's own
    `<pre>` is already focusable and is not the bare `<pre>` that step looks
    for; tables still get theirs).
    """
    md = _markdown.Markdown(
        extensions=[*_EXTENSIONS, "toc"],
        extension_configs={"toc": {"marker": "", "toc_depth": "2-2"}},
    )
    html = _mark_scroll_regions(_draw_code_blocks(md.convert(text)))
    sections = tuple(
        Section(id=token["id"], title=_html.unescape(token["name"]))
        for token in md.toc_tokens
    )
    return Reading(html=html, sections=sections)
```

`render_markdown` now ends in the same helper `render_reading` uses, instead of a copy of the `<pre>` and table wrapping; its text is the same as before (the shared helper holds the old lines unchanged), and `Markdown` is given the same two extensions as before. `render_reading` draws the code blocks first and marks the scroll regions after, in that order: the other way round, the `<pre>` the parser wrote is already `<pre tabindex="0" role="region" …>` and the code block pattern finds nothing (the tests above fail for it).

- [ ] **Step 4: Run the new tests and the tests that already cover `render_markdown`**

First confirm nothing else uses what changed. Run, from the repo root: `grep -rl "from blog.render import" backend --include='*.py'`
Expected: exactly five files, in any order: `backend/blog/views.py`, `backend/docs/views.py` and `backend/legal/views.py` (each `from blog.render import render_markdown`), and `backend/drop_core/tests.py` and `backend/drop_core/tests_fix_web.py` (imports inside tests). Nothing imports a name this step removed (it removed none).

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_fix_web.RenderReadingTests`
Expected: `Ran 20 tests`, `OK`.

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_fix_web drop_core.tests.AccessibilityTests blog docs legal pages`
Expected: `OK`. These are the tests that render the existing pages and call `render_markdown` (`test_render_markdown_marks_scroll_regions`, `ContentGuardTests`, the Docs, Journal and legal page tests), and the component's own (`pages.tests_code_block`). No existing test needed a change: `render_markdown`'s output is byte-identical (checked while updating this task, against main at 7d94ea2, over all nine content files: one post, five Docs pages, three legal documents), and no view or template uses `render_reading` yet.

Look once at the names the real Docs blocks get. Run, from the repo root:

```bash
(cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py shell -c "
from blog.render import render_reading
from drop_core.tests_fix_web import _copy_buttons, _reading_sources
for name, text in _reading_sources():
    for button, label in _copy_buttons(render_reading(text).html):
        print(name.split('/')[-1], button, label)
")
```
Expected: after Django's one line about imported objects, one line per code block of the Journal, Docs and legal files, 13 at the time of writing (the lines follow the content), none repeated, such as `agent.md code-block-2 Copy the bash code under Claude Code`, `agent.md code-block-7 Copy the json code under Cursor`, `api.md code-block-3 Copy the json code under Upload` and `quickstart.md code-block-1 Copy the bash code under 3. Upload`. (The quickstart's setup commands are raw HTML with their own tabs and copy buttons and get no block.)

- [ ] **Step 5: Run the whole suite**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: `OK` (about 3.5 minutes; the count changes, do not compare it with a number).

AGENTS.md needs no change in this task: it does not describe the renderer's functions, its repo-map line for `backend/blog, docs, legal/` stays true, and no page uses `render_reading` yet, so nothing it says about code blocks (Begin's include, the Journal recipe's "fenced code blocks") is untrue. The recipes and repo map are Task 8's; it says there that a fenced code block on a Journal or Docs page gets a Copy button named from its language and the heading above it, so the language goes after the opening fence.

- [ ] **Step 6: Commit**

```bash
git add backend/blog/render.py backend/drop_core/tests_fix_web.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -m "The Markdown pipeline can give reading pages heading ids, a list of their sections and a Copy button on each code block"
```

---

### Task 2: The reading family, and head entries for posts and Docs pages

**Files:**
- Create: `backend/pages/tests_reading_family.py`
- Modify: `backend/pages/registry.py`, `backend/pages/context_processors.py`, `backend/pages/templatetags/shell.py`, `backend/templates/shell.html`, `backend/blog/loader.py`

**Interfaces:**
- Consumes: `blog.loader.load_posts()` and `get_post(slug)` (published posts only: a post dated in the future is skipped, the same rule the Journal index uses); `docs.loader.load_pages()` and `get_page(slug)`; `registry.SECTIONS`; `request.resolver_match.url_name` and `.kwargs["slug"]`; `request_for`, `render_shell_as` and `LD_JSON` from `pages/tests_head.py`.
- Produces:
  - `layout="reading"` on the registry pages `docs-index`, `blog-index`, `terms`, `privacy` and `report-abuse`.
  - `pages.registry.layout_paths(layout: str) -> tuple[str, ...]`. For `"chapter"` it returns exactly what the old inline comprehension gave: `("/", "/about/", "/how/", "/begin/", "/plans/", "/promises/")`. For `"reading"` it returns the five registry paths, then `/blog/<slug>/` for each published post (newest first), then `/docs/<slug>/` for each Docs page (in `order`, so `/docs/index/` is among them). For `""` or a layout no page has it returns `()`.
  - `blog.loader.Post` gains `image: str = ""` and `image_alt: str = ""`, read from the front matter keys `image` and `image_alt`. Task 6 renders the header image from these; it must not add them again.
  - `shell_page` (the context processor) gives the URL names `blog-post` and `docs-page` an entry with the keys of a registry entry: `title`, `description`, `canonical`, `layout` (`"reading"`), `layout_paths`, `image`, plus the new key `article`. It gives `{"shell_page": None}`, and raises nothing, when the slug is unknown or the post is not yet published. The entry is still a `SimpleLazyObject`: only the slug is looked up at once; the entry (and its `static()` call) is built when a template first reads it.
  - `article` is `None` for every registry page, `{"type": "BlogPosting", "headline": <post title>, "published": <the post's date>}` for a post, and `{"type": "TechArticle", "headline": <page title>, "published": None}` for a Docs page.
  - `shell.html` writes `og:type` `article` when `shell_page.article` is set, with `article:published_time` when `published` is set. The `structured_data` tag adds the Article JSON-LD (with `data-page-meta`) between `Organization` and whatever the view passes.

**What stays the same for search results.** A post's `<title>` is `"<post title> | Veilfile blog"` and a Docs page's is its `seo_title`, else its `title`: exactly what `templates/blog/post.html` and `templates/docs/page.html` print today. Descriptions are the post's or page's own, and a missing one means no description tags, as before. Canonical addresses are `https://veilfile.com/blog/<slug>/` and `https://veilfile.com/docs/<slug>/`; `/docs/index/` (which the Docs view serves as the index page) keeps `https://veilfile.com/docs/` and is not an article.

- [ ] **Step 1: Write the failing tests**

Create `backend/pages/tests_reading_family.py`. Each of its four classes sets `longMessage = False`, so that a failed assertion prints its own message, which names the file and the fix, and not the page or the list it compared first. The assertions on the rendered head carry such a message for that reason.

```python
"""The reading family (spec 4.5, 4.6, 6.5, 6.6): the Journal, Docs and legal pages share one layout, and a Journal
post or a Docs page gets the head a registry page gets, built from its file.

Every class sets longMessage = False: a failed assertion then prints its own message, which names the file and the
fix, and not the whole page or list it compared first (the message carries what was found where that matters)."""
import json
import re
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils.html import escape

from blog import loader as blog_loader
from blog.views import RETIRED_SLUGS
from docs import loader as docs_loader
from pages import registry
from pages.context_processors import shell_page
from pages.tests_head import LD_JSON, render_shell_as, request_for


def post_file(slug, title, date, description="A description of the post, long enough to be a snippet.", extra=""):
    """The text of a Journal post file: front matter, then a one-line body."""
    lines = ["---", f"title: {title}", f"date: {date}", f"slug: {slug}"]
    if description:
        lines.append(f"description: {description}")
    lines += extra.splitlines()
    return "\n".join(lines + ["---", "", "Body.", ""])


READING_BODY = ("backend/templates/shell.html: a reading page's <body> carries data-layout=\"reading\" "
                "(shell_page.layout), which is what lets shell.js change in place within the family")


class TempContentMixin:
    """Swap the Journal's posts, or the Docs pages, for a few files made for one test."""

    def use_files(self, loader, directory_name, files):
        directory = Path(tempfile.mkdtemp(prefix="veilfile-reading-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        for name, text in files.items():
            (directory / name).write_text(text, encoding="utf-8")
        patcher = mock.patch.object(loader, directory_name, directory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_posts(self, files):
        self.use_files(blog_loader, "POSTS_DIR", files)

    def use_docs(self, files):
        self.use_files(docs_loader, "PAGES_DIR", files)


class ReadingFamilyTests(SimpleTestCase):
    longMessage = False

    def test_the_reading_layout_is_the_docs_the_journal_and_the_legal_pages(self):
        reading = [page.url_name for page in registry.PAGES if page.layout == "reading"]
        self.assertEqual(reading, ["docs-index", "blog-index", "terms", "privacy", "report-abuse"],
                         "backend/pages/registry.py: the Docs index, the Journal index and the three legal pages "
                         "are the registry pages with layout=\"reading\"; give a new reading page the same "
                         f"(found {reading})")
        chapters = [page.url_name for page in registry.PAGES if page.layout == "chapter"]
        self.assertEqual(chapters, ["landing", "about", "how", "begin", "plans", "promises"],
                         f"backend/pages/registry.py: the threshold and the chapters keep layout=\"chapter\" (found {chapters})")

    def test_the_chapter_family_is_what_it_was(self):
        self.assertEqual(registry.layout_paths("chapter"), ("/", "/about/", "/how/", "/begin/", "/plans/", "/promises/"),
                         "backend/pages/registry.py: layout_paths('chapter') is every chapter page's address in "
                         "registry order, as the context processor listed them before the reading layout existed "
                         f"(found {registry.layout_paths('chapter')})")

    def test_a_page_with_no_layout_has_no_family(self):
        self.assertEqual(registry.layout_paths(""), (), "backend/pages/registry.py: a page with no layout changes in place to nothing")
        self.assertEqual(registry.layout_paths("no-such-layout"), ())

    def test_the_reading_family_is_the_five_pages_then_every_post_and_every_docs_page(self):
        posts, pages = blog_loader.load_posts(), docs_loader.load_pages()
        self.assertTrue(posts and pages, "backend/blog/posts/ and backend/docs/pages/ should hold the content this test reads")
        expected = ["/docs/", "/blog/", "/terms/", "/privacy/", "/report-abuse/"]
        expected += [f"/blog/{post.slug}/" for post in posts]
        expected += [f"/docs/{page.slug}/" for page in pages]
        self.assertEqual(list(registry.layout_paths("reading")), expected,
                         "backend/pages/registry.py: layout_paths('reading') lists the five reading pages, then "
                         "/blog/<slug>/ for each published post and /docs/<slug>/ for each Docs page; shell.js changes "
                         f"in place to these addresses only (found {registry.layout_paths('reading')})")

    def test_no_address_repeats_or_holds_a_space(self):
        paths = registry.layout_paths("reading")
        self.assertEqual(len(paths), len(set(paths)), "backend/pages/registry.py: an address is listed twice in layout_paths('reading')")
        for path in paths:
            self.assertNotRegex(path, r"\s", "backend/pages/registry.py: data-layout-paths is a space-separated list, so an "
                                             "address must not hold whitespace; keep the slug check in blog/loader.py and docs/loader.py")


class FutureDatedPostTests(TempContentMixin, TestCase):
    longMessage = False

    def test_a_post_dated_in_the_future_is_not_in_the_family_and_has_no_entry(self):
        self.use_posts({
            "past.md": post_file("past-post", "A post from the past", "2020-01-01"),
            "future.md": post_file("future-post", "A post from the future", "2999-01-01"),
        })
        paths = registry.layout_paths("reading")
        self.assertIn("/blog/past-post/", paths,
                      "backend/pages/registry.py: layout_paths lists a post dated today or earlier")
        self.assertNotIn("/blog/future-post/", paths,
                         "backend/pages/registry.py: layout_paths must list published posts only (blog.loader.load_posts)")
        self.assertEqual(shell_page(request_for("/blog/future-post/")), {"shell_page": None},
                         "backend/pages/context_processors.py: a post that is not published has no head entry")
        self.assertEqual(self.client.get("/blog/future-post/").status_code, 404)
        self.assertEqual(shell_page(request_for("/blog/past-post/"))["shell_page"]["title"], "A post from the past | Veilfile blog")


class ReadingEntryTests(TempContentMixin, TestCase):
    longMessage = False

    def entry(self, path):
        return shell_page(request_for(path))["shell_page"]

    def a_docs_page(self):
        return next(page for page in docs_loader.load_pages() if page.slug != "index")

    def test_a_post_has_an_entry_with_the_keys_of_a_registry_page(self):
        post = blog_loader.load_posts()[0]
        entry = self.entry(f"/blog/{post.slug}/")
        self.assertEqual(set(entry), set(self.entry("/about/")),
                         "backend/pages/context_processors.py: a post's entry has the same keys as a registry page's")
        self.assertEqual(entry["title"], f"{post.title} | Veilfile blog",
                         "backend/pages/context_processors.py: a post's <title> is '<title> | Veilfile blog', as blog/post.html "
                         "rendered it; changing it changes search results")
        self.assertEqual(entry["description"], post.description)
        self.assertEqual(entry["canonical"], f"https://veilfile.com/blog/{post.slug}/")
        self.assertEqual(entry["layout"], "reading")
        self.assertEqual(entry["layout_paths"].split(), list(registry.layout_paths("reading")))
        self.assertIn(f"/blog/{post.slug}/", entry["layout_paths"].split())
        self.assertEqual(entry["image"], "https://veilfile.com/static/" + (post.image or "img/og.png"))
        self.assertEqual(entry["article"], {"type": "BlogPosting", "headline": post.title, "published": post.date})

    def test_a_docs_page_has_an_entry_with_its_seo_title(self):
        page = self.a_docs_page()
        entry = self.entry(f"/docs/{page.slug}/")
        self.assertEqual(set(entry), set(self.entry("/about/")))
        self.assertEqual(entry["title"], page.seo_title or page.title,
                         "backend/pages/context_processors.py: a Docs page's <title> is its seo_title, else its title, as "
                         "docs/page.html rendered it; changing it changes search results")
        self.assertEqual(entry["description"], page.description)
        self.assertEqual(entry["canonical"], f"https://veilfile.com/docs/{page.slug}/")
        self.assertEqual(entry["layout"], "reading")
        self.assertEqual(entry["layout_paths"].split(), list(registry.layout_paths("reading")))
        self.assertEqual(entry["image"], "https://veilfile.com/static/img/og.png")
        self.assertEqual(entry["article"], {"type": "TechArticle", "headline": page.title, "published": None})

    def test_a_docs_page_with_no_seo_title_uses_its_title(self):
        self.use_docs({"plain.md": "---\ntitle: A plain page\nslug: plain\ndescription: A page with no seo title.\norder: 7\n---\n\nBody.\n"})
        self.assertEqual(self.entry("/docs/plain/")["title"], "A plain page",
                         "backend/pages/context_processors.py: with no seo_title a Docs page's <title> is its title")

    def test_the_docs_index_under_its_slug_has_the_docs_index_head(self):
        entry = self.entry("/docs/index/")
        self.assertEqual(entry["title"], registry.page("docs-index").title)
        self.assertEqual(entry["canonical"], "https://veilfile.com/docs/",
                         "backend/pages/context_processors.py: /docs/index/ shows the index page, whose address is /docs/")
        self.assertIsNone(entry["article"])

    def test_a_registry_page_is_not_an_article(self):
        for path in ("/about/", "/docs/", "/blog/", "/terms/", "/privacy/", "/report-abuse/"):
            with self.subTest(path=path):
                self.assertIsNone(self.entry(path)["article"],
                                  "backend/pages/context_processors.py: an index or legal page is not an Article (spec 6.6)")

    def test_the_reading_registry_pages_list_the_whole_family(self):
        for path in ("/docs/", "/blog/", "/terms/", "/privacy/", "/report-abuse/"):
            with self.subTest(path=path):
                entry = self.entry(path)
                self.assertEqual(entry["layout"], "reading")
                self.assertEqual(entry["layout_paths"].split(), list(registry.layout_paths("reading")))

    def test_a_post_with_a_header_image_previews_with_it(self):
        self.use_posts({"with-image.md": post_file("with-image", "With an image", "2020-01-01",
                                                   extra="image: img/apple-touch-icon.png\nimage_alt: The Veilfile mark.")})
        post = blog_loader.get_post("with-image")
        self.assertEqual((post.image, post.image_alt), ("img/apple-touch-icon.png", "The Veilfile mark."),
                         "backend/blog/loader.py: a post's front matter 'image' and 'image_alt' are kept on the Post")
        self.assertEqual(self.entry("/blog/with-image/")["image"], "https://veilfile.com/static/img/apple-touch-icon.png",
                         "backend/pages/context_processors.py: a post with an 'image' previews with it, as an absolute address")

    def test_a_post_with_no_header_image_previews_with_the_mark(self):
        self.use_posts({"plain.md": post_file("plain-post", "A plain post", "2020-01-01")})
        post = blog_loader.get_post("plain-post")
        self.assertEqual((post.image, post.image_alt), ("", ""))
        self.assertEqual(self.entry("/blog/plain-post/")["image"], "https://veilfile.com/static/img/og.png")

    def test_the_entry_is_built_only_when_read(self):
        post = blog_loader.load_posts()[0]
        request = request_for(f"/blog/{post.slug}/")
        with mock.patch("pages.context_processors.static",
                        side_effect=ValueError("Missing staticfiles manifest entry for 'img/og.png'")):
            entry = shell_page(request)["shell_page"]  # handing it to a template builds nothing
            with self.assertRaises(ValueError, msg="backend/pages/context_processors.py: the entry is a SimpleLazyObject, built on first read"):
                entry["image"]

    def test_an_unknown_slug_has_no_entry_and_is_a_plain_404(self):
        for path in ("/blog/no-such-post/", "/docs/no-such-page/"):
            with self.subTest(path=path):
                self.assertEqual(shell_page(request_for(path)), {"shell_page": None},
                                 "backend/pages/context_processors.py: an unknown slug has no entry and raises nothing; the view answers 404")
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_a_retired_post_address_still_redirects_and_has_no_entry(self):
        for old, new in RETIRED_SLUGS.items():
            with self.subTest(old=old):
                self.assertEqual(shell_page(request_for(f"/blog/{old}/")), {"shell_page": None})
                response = self.client.get(f"/blog/{old}/")
                self.assertEqual((response.status_code, response["Location"]), (301, f"/blog/{new}/"))

    def test_every_page_inside_a_menu_section_has_an_entry(self):
        post, page = blog_loader.load_posts()[0], self.a_docs_page()
        samples = {"blog-post": f"/blog/{post.slug}/", "docs-page": f"/docs/{page.slug}/"}
        self.assertEqual(set(samples), set(registry.SECTIONS),
                         "backend/pages/registry.py: SECTIONS names a page that comes from files and has no registry entry; "
                         "give it a head entry in backend/pages/context_processors.py (shell_page) and a row in this test")
        for name, path in samples.items():
            with self.subTest(page=name):
                self.assertTrue(self.entry(path), f"backend/pages/context_processors.py: shell_page gives {name} no head entry")


class ReadingHeadTests(TempContentMixin, TestCase):
    longMessage = False

    def test_a_post_head_says_it_is_an_article_published_on_its_date(self):
        post = blog_loader.load_posts()[0]
        path = f"/blog/{post.slug}/"
        canonical = f"https://veilfile.com{path}"
        html = render_shell_as(path)
        for tag in (
            f"<title>{escape(post.title + ' | Veilfile blog')}</title>",
            f'<meta name="description" content="{escape(post.description)}" data-page-meta>',
            f'<link rel="canonical" href="{canonical}" data-page-meta>',
            '<meta property="og:type" content="article" data-page-meta>',
            f'<meta property="og:title" content="{escape(post.title + " | Veilfile blog")}" data-page-meta>',
            f'<meta property="og:url" content="{canonical}" data-page-meta>',
            f'<meta property="article:published_time" content="{post.date.isoformat()}" data-page-meta>',
        ):
            with self.subTest(tag=tag):
                self.assertIn(tag, html, "backend/templates/shell.html and backend/pages/context_processors.py: "
                                         f"a Journal post's head should hold {tag}")
        self.assertIn('data-layout="reading"', html, READING_BODY)
        self.assertIn(path, re.search(r'data-layout-paths="([^"]*)"', html).group(1).split(),
                      f"backend/pages/context_processors.py: {path} is one of the addresses in its own data-layout-paths")

    def test_a_docs_page_head_says_it_is_an_article_with_no_date(self):
        page = next(page for page in docs_loader.load_pages() if page.slug != "index")
        path = f"/docs/{page.slug}/"
        html = render_shell_as(path)
        for tag in (
            f"<title>{escape(page.seo_title or page.title)}</title>",
            f'<link rel="canonical" href="https://veilfile.com{path}" data-page-meta>',
            '<meta property="og:type" content="article" data-page-meta>',
        ):
            with self.subTest(tag=tag):
                self.assertIn(tag, html, "backend/templates/shell.html and backend/pages/context_processors.py: "
                                         f"a Docs page's head should hold {tag}")
        self.assertNotIn("article:published_time", html, "backend/templates/shell.html: a Docs page has no publication date")
        self.assertIn('data-layout="reading"', html, READING_BODY)

    def test_the_reading_registry_pages_are_websites_in_the_reading_layout(self):
        for path in ("/docs/", "/blog/", "/terms/", "/privacy/", "/report-abuse/"):
            with self.subTest(path=path):
                html = render_shell_as(path)
                self.assertIn('<meta property="og:type" content="website" data-page-meta>', html,
                              "backend/templates/shell.html: an index or legal page is og:type website; only an article says article")
                self.assertNotIn("article:published_time", html,
                                 "backend/templates/shell.html: only a Journal post has a publication date")
                self.assertIn('data-layout="reading"', html, READING_BODY)
                self.assertEqual([json.loads(block)["@type"] for block in LD_JSON.findall(html)], ["Organization"],
                                 "backend/pages/templatetags/shell.py: index and legal pages carry Organization only")

    def test_a_post_with_no_description_has_no_description_tags(self):
        self.use_posts({"bare.md": post_file("bare-post", "A bare post", "2020-01-01", description="")})
        html = render_shell_as("/blog/bare-post/")
        for needle in ('name="description"', 'property="og:description"'):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, html, "backend/templates/shell.html: with no description there is no description tag, "
                                               "as blog/post.html left it out")
        self.assertNotIn("description", json.loads(LD_JSON.findall(html)[1]))

    def test_a_post_says_it_is_a_blog_posting(self):
        post = blog_loader.load_posts()[0]
        path = f"/blog/{post.slug}/"
        items = [json.loads(block) for block in LD_JSON.findall(render_shell_as(path))]
        self.assertEqual([item["@type"] for item in items], ["Organization", "BlogPosting"])
        article = items[1]
        self.assertEqual(article["@context"], "https://schema.org")
        self.assertEqual(article["headline"], post.title)
        self.assertEqual(article["description"], post.description)
        self.assertEqual(article["datePublished"], post.date.isoformat())
        self.assertEqual(article["mainEntityOfPage"], {"@type": "WebPage", "@id": f"https://veilfile.com{path}"})
        self.assertEqual(article["image"], "https://veilfile.com/static/" + (post.image or "img/og.png"))
        self.assertEqual(article["author"]["name"], "Veilfile")
        self.assertEqual(article["publisher"]["name"], "Veilfile")

    def test_a_docs_page_says_it_is_a_tech_article(self):
        page = next(page for page in docs_loader.load_pages() if page.slug != "index")
        path = f"/docs/{page.slug}/"
        items = [json.loads(block) for block in LD_JSON.findall(render_shell_as(path))]
        self.assertEqual([item["@type"] for item in items], ["Organization", "TechArticle"])
        self.assertEqual(items[1]["headline"], page.title)
        self.assertEqual(items[1]["mainEntityOfPage"]["@id"], f"https://veilfile.com{path}")
        self.assertNotIn("datePublished", items[1], "backend/pages/templatetags/shell.py: a Docs page has no publication date")

    def test_every_json_ld_script_swaps_with_the_page(self):
        post = blog_loader.load_posts()[0]
        html = render_shell_as(f"/blog/{post.slug}/")
        self.assertEqual(html.count('type="application/ld+json"'), len(LD_JSON.findall(html)),
                         "backend/pages/templatetags/shell.py: every JSON-LD script carries data-page-meta, or it stays "
                         "behind when shell.js swaps the page")
        self.assertEqual(len(LD_JSON.findall(html)), 2,
                         "backend/pages/templatetags/shell.py: a post's head carries two JSON-LD scripts, Organization "
                         "and BlogPosting")

    def test_a_title_cannot_close_the_json_ld_script(self):
        hostile = "Close </script><b>this</b> & more"
        self.use_posts({"hostile.md": post_file("hostile-post", hostile, "2020-01-01")})
        html = render_shell_as("/blog/hostile-post/")
        self.assertNotIn("</script><b>", html, "backend/pages/templatetags/shell.py: JSON-LD must escape <, > and &")
        self.assertEqual(html.count("<script"), html.count("</script>"),
                         "backend/pages/templatetags/shell.py: a title must not open or close a script in the head")
        self.assertEqual(json.loads(LD_JSON.findall(html)[1])["headline"], hostile)
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_reading_family`
Expected: `Ran 26 tests`, then `FAILED`. Among the failures: `AttributeError: module 'pages.registry' has no attribute 'layout_paths'` (ReadingFamilyTests and FutureDatedPostTests), `KeyError: 'article'` and `AssertionError: '' != 'reading'` (ReadingEntryTests), `AttributeError: 'Post' object has no attribute 'image'` (the header image tests), and head tests that fail with a message naming `backend/templates/shell.html` and `backend/pages/context_processors.py` (and no page text before it). The tests that already pass are the ones that pin today's good behaviour: the unknown slug and retired slug tests.

- [ ] **Step 2: Put the five pages in the reading layout, and add `layout_paths`**

In `backend/pages/registry.py`, replace this exact text:

```python
The menu, sitemap.xml and the tests read from here; from PR 2 each page's <head> does too.
```

with:

```python
The menu, sitemap.xml and the tests read from here; from PR 2 each page's <head> does too.
Journal posts and Docs pages come from files, not from this list: pages/context_processors.py builds
their heads from the loaders, and layout_paths() adds their addresses to the reading layout.
```

Replace this exact text:

```python
from django.urls import reverse

SITE_ORIGIN = "https://veilfile.com"
```

with:

```python
from django.urls import reverse

from blog import loader as blog_loader
from docs import loader as docs_loader

SITE_ORIGIN = "https://veilfile.com"
```

Replace this exact text (the comment that says layout is "chapter" only):

```python
    layout: str = ""  # "chapter" for the threshold and the chapters; in-place changes stay within one layout
```

with:

```python
    # "chapter" for the threshold and the chapters, "reading" for the Journal, Docs and legal pages. In-place
    # changes stay within one layout: layout_paths() lists the addresses of each.
    layout: str = ""
```

Replace this exact text (the last five `Page(...)` entries and the closing parenthesis of `PAGES`):

```python
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
```

with:

```python
    Page("docs-index", "Veilfile docs | REST API, MCP, and agent setup",
         "Start here: five-minute quickstart, agent key setup, how uploads and expiry work, "
         "and the full REST and MCP API reference.",
         layout="reading"),
    Page("blog-index", "Veilfile blog | launches and build notes",
         "Notes from building Veilfile: launches, changes, pricing, and the reasoning behind each decision. "
         "Updated twice a month. RSS available.",
         layout="reading"),
    Page("terms", "Terms of service | Veilfile",
         "Veilfile's terms: acceptable use, accounts and API keys, data retention, billing and refunds, "
         "liability limits, and how to contact us.",
         layout="reading"),
    Page("privacy", "Privacy policy | Veilfile",
         "What Veilfile collects, why, how long files and metadata are kept, subprocessors, "
         "and how to request access or deletion.",
         layout="reading"),
    Page("report-abuse", "Report abuse | Veilfile",
         "Report a file on Veilfile that violates our terms. Email abuse@veilfile.com with the link "
         "and a description. CSAM reports go to NCMEC first.",
         layout="reading"),
)
```

Replace this exact text:

```python
def canonical_url(url_name):
    return SITE_ORIGIN + reverse(url_name)
```

with:

```python
def canonical_url(url_name):
    return SITE_ORIGIN + reverse(url_name)


def layout_paths(layout):
    """Every address a page in `layout` may change to in place (shell.js, spec 4.5), as a tuple of paths.

    The registry pages with that layout, in registry order. The reading layout adds every published
    Journal post (newest first) and every Docs page (in order): they come from files and have no registry
    entry. A post dated in the future is not published (blog.loader), so it is not here. No layout, or one
    no page has, is an empty family. The paths are joined with spaces into data-layout-paths, so none may
    hold whitespace; the loaders only accept slugs of [-a-zA-Z0-9_].
    """
    paths = [reverse(candidate.url_name) for candidate in PAGES if layout and candidate.layout == layout]
    if paths and layout == "reading":
        paths += [reverse("blog-post", args=[post.slug]) for post in blog_loader.load_posts()]
        paths += [reverse("docs-page", args=[doc.slug]) for doc in docs_loader.load_pages()]
    return tuple(paths)
```

Replace this exact text:

```python
# Pages that sit inside a menu section: on them the section's link is marked "true" (this
# section), not "page", so the menu does not open by itself the way it does on a chapter.
```

with:

```python
# Pages that sit inside a menu section: on them the section's link is marked "true" (in this
# section), not "page" (this very page).
# They come from files, not from PAGES: pages/context_processors.py gives each its own head entry
# (a test keeps the two lists together).
```

(The old comment's reason, "so the menu does not open by itself the way it does on a chapter", stopped being true when the menu started closed on every page; the new first sentence says only what the two marks mean.)

- [ ] **Step 3: Let a post keep its header image**

In `backend/blog/loader.py`, replace this exact text (in the module docstring; four spaces of indent):

```
    description: One sentence for SEO and RSS.
    ---
```

with:

```
    description: One sentence for SEO and RSS.
    image: img/journal/x.png            (optional: a path under backend/static/)
    image_alt: One sentence about it.   (required with image)
    ---
```

Replace this exact text:

```python
    description: str
    body_md: str
```

with:

```python
    description: str
    body_md: str
    image: str = ""  # front matter `image`: a path under backend/static/, shown at the top and used in link previews
    image_alt: str = ""  # front matter `image_alt`: what the image shows
```

Replace this exact text:

```python
                description=description,
                body_md=body,
            )
```

with:

```python
                description=description,
                body_md=body,
                image=meta.get("image", ""),
                image_alt=meta.get("image_alt", ""),
            )
```

(The content check in `pages/content_checks.py` already requires an `image` file to exist under `backend/static/` and to come with an `image_alt`.)

- [ ] **Step 4: Build entries for posts and Docs pages**

Replace the whole of `backend/pages/context_processors.py` with:

```python
"""The registry entry for the page being rendered, for the <head> in shell.html (spec 6.5, 6.6)."""
from django.templatetags.static import static
from django.urls import reverse
from django.utils.functional import SimpleLazyObject

from blog import loader as blog_loader
from docs import loader as docs_loader
from pages import registry


def _build(*, title, description, canonical, layout, image, article=None):
    """The entry the head reads, every address absolute; `image` is a static path.

    `article` is None for a page that is not an article, else what search results are told about it:
    {"type": "BlogPosting" or "TechArticle", "headline": ..., "published": a date, or None}.
    """
    return {
        "title": title,
        "description": description,
        "canonical": canonical,
        "layout": layout,
        # The only addresses shell.js changes to in place: every page in the same layout.
        "layout_paths": " ".join(registry.layout_paths(layout)),
        "image": registry.SITE_ORIGIN + static(image),
        "article": article,
    }


def _entry(page):
    """A registry page's entry: its title, description, canonical address, layout and link-preview image."""
    return _build(title=page.title, description=page.description, canonical=registry.canonical_url(page.url_name),
                  layout=page.layout, image="img/og.png")


def _post_entry(post):
    """A Journal post's entry. Its title and description are the ones blog/post.html rendered, so that search
    results do not change: "<title> | Veilfile blog" and the post's own description. Its link preview is the
    header image its front matter names, else the site's."""
    return _build(
        title=f"{post.title} | Veilfile blog",
        description=post.description,
        canonical=registry.SITE_ORIGIN + reverse("blog-post", args=[post.slug]),
        layout="reading",
        image=post.image or "img/og.png",
        article={"type": "BlogPosting", "headline": post.title, "published": post.date},
    )


def _docs_entry(page):
    """A Docs page's entry. Its title is the one docs/page.html rendered (the page's seo_title, else its title),
    so that search results do not change."""
    if page.slug == "index":  # /docs/index/ shows the index page, whose address is /docs/
        return _entry(registry.page("docs-index"))
    return _build(
        title=page.seo_title or page.title,
        description=page.description,
        canonical=registry.SITE_ORIGIN + reverse("docs-page", args=[page.slug]),
        layout="reading",
        image="img/og.png",
        article={"type": "TechArticle", "headline": page.title, "published": None},
    )


def shell_page(request):
    """{"shell_page": None} for a page with no entry, else its entry (see _build).

    A registry page has an entry; so does a Journal post or a Docs page whose slug is published (the URL names
    blog-post and docs-page, the same two registry.SECTIONS names). An unknown slug, or a post dated in the
    future, has none: the view answers 404.

    The entry is built when a template first reads it, not on every render: a page whose template never reads
    shell_page must not fail because img/og.png has no manifest entry after a bad collectstatic. Only a slug is
    looked up at once, to know whether there is an entry at all.
    """
    match = getattr(request, "resolver_match", None)
    name = getattr(match, "url_name", None)
    slug = (getattr(match, "kwargs", None) or {}).get("slug")
    if name == "blog-post":
        page, build = blog_loader.get_post(slug), _post_entry
    elif name == "docs-page":
        page, build = docs_loader.get_page(slug), _docs_entry
    else:
        build = _entry
        try:
            page = registry.page(name) if name else None
        except KeyError:
            page = None
    if page is None:
        return {"shell_page": None}
    return {"shell_page": SimpleLazyObject(lambda: build(page))}
```

- [ ] **Step 5: Add the Article JSON-LD**

In `backend/pages/templatetags/shell.py`, replace this exact text:

```python
@register.simple_tag(takes_context=True)
def structured_data(context):
    """The page's JSON-LD (spec 6.6): Organization on every registry page, then whatever the view
    passes as structured_data (a list of dicts). Each is its own script, marked for in-place changes."""
```

with:

```python
def _article(entry, organization):
    """The schema.org Article for a Journal post (BlogPosting) or a Docs page (TechArticle), from shell_page.
    Veilfile is its author and publisher."""
    article = entry["article"]
    veilfile = {key: organization[key] for key in ("@type", "name", "url")}
    data = {
        "@context": "https://schema.org",
        "@type": article["type"],
        "headline": article["headline"],
        "mainEntityOfPage": {"@type": "WebPage", "@id": entry["canonical"]},
        "image": entry["image"],
        "author": veilfile,
        "publisher": veilfile,
    }
    if entry["description"]:
        data["description"] = entry["description"]
    if article["published"]:
        data["datePublished"] = article["published"].isoformat()
    return data


@register.simple_tag(takes_context=True)
def structured_data(context):
    """The page's JSON-LD (spec 6.6): Organization on every registry page; then, on a Journal post or a Docs
    page, its Article (from shell_page.article); then whatever the view passes as structured_data (a list of
    dicts). Each is its own script, marked for in-place changes."""
```

Replace this exact text:

```python
    items = [organization, *(context.get("structured_data") or ())]
```

with:

```python
    items = [organization]
    entry = context.get("shell_page")
    if entry and entry.get("article"):
        items.append(_article(entry, organization))
    items.extend(context.get("structured_data") or ())
```

(The existing escaping, `json.dumps(...).translate(_SCRIPT_SAFE)`, applies to the Article too, so a title with `</script>` cannot close the script.)

- [ ] **Step 6: Say `article` in the head, and leave out empty descriptions**

In `backend/templates/shell.html`, replace this exact text (lines 19 to 27, from `{% if shell_page %}` to the `twitter:card` tag):

```
{% if shell_page %}<meta name="description" content="{{ shell_page.description }}" data-page-meta>
<link rel="canonical" href="{{ shell_page.canonical }}" data-page-meta>
<meta property="og:type" content="website" data-page-meta>
<meta property="og:site_name" content="Veilfile" data-page-meta>
<meta property="og:title" content="{{ shell_page.title }}" data-page-meta>
<meta property="og:description" content="{{ shell_page.description }}" data-page-meta>
<meta property="og:url" content="{{ shell_page.canonical }}" data-page-meta>
<meta property="og:image" content="{{ shell_page.image }}" data-page-meta>
<meta name="twitter:card" content="summary_large_image" data-page-meta>
```

with:

```
{% if shell_page %}{% if shell_page.description %}<meta name="description" content="{{ shell_page.description }}" data-page-meta>
{% endif %}<link rel="canonical" href="{{ shell_page.canonical }}" data-page-meta>
<meta property="og:type" content="{% if shell_page.article %}article{% else %}website{% endif %}" data-page-meta>
<meta property="og:site_name" content="Veilfile" data-page-meta>
<meta property="og:title" content="{{ shell_page.title }}" data-page-meta>
{% if shell_page.description %}<meta property="og:description" content="{{ shell_page.description }}" data-page-meta>
{% endif %}<meta property="og:url" content="{{ shell_page.canonical }}" data-page-meta>
<meta property="og:image" content="{{ shell_page.image }}" data-page-meta>
{% if shell_page.article.published %}<meta property="article:published_time" content="{{ shell_page.article.published|date:'Y-m-d' }}" data-page-meta>
{% endif %}<meta name="twitter:card" content="summary_large_image" data-page-meta>
```

The rendered tags for every registry page are byte for byte what they were (the existing `tests_head.py` assertions still hold); only the `{% if %}` pieces are new. Every tag keeps `data-page-meta`, so `shell.js` swaps them with the page.

- [ ] **Step 7: Run the new tests and the tests around them**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_reading_family pages.tests_shell pages.tests_head pages.tests_registry pages.tests_chapters pages.tests_static`
Expected: `Ran 119 tests`, then `OK` (26 of them in `pages.tests_reading_family`).

- [ ] **Step 8: Run the whole suite**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: OK. No existing test needs to change in this task: the old Journal, Docs and legal templates do not extend `shell.html` and never read `shell_page`, so `ShellPageIsLazyTests` (in `pages/tests_head.py`; it uses `/terms/` and `/docs/` as pages that do not extend the shell) still holds, and `RegistryMatchesRenderedPagesTests` (in `pages/tests_registry.py`) still sees the same titles and descriptions. Do not touch `ShellPageIsLazyTests` here; Tasks 4 and 5 edit it.

- [ ] **Step 9: Commit**

```bash
git add backend/pages/tests_reading_family.py backend/pages/registry.py \
  backend/pages/context_processors.py backend/pages/templatetags/shell.py backend/templates/shell.html \
  backend/blog/loader.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -m "Journal posts and Docs pages get a reading family, head entries and Article data"
```

---

---

### Task 3: The reading layout, its components, typography and ink

**What this task builds.** The Journal, Docs and the legal documents will share one page layout, "reading": a single column of Markdown text in Newsreader, centred like a chapter but starting at the top so a long page scrolls. This task builds the layout template, the CSS for everything Markdown can write inside it (headings, lists, tables, code, quotations, bold, long words), three small components that sit in the column (a list of a page's sections, previous and next links, a list of pages to read), and the ink on its links. "The cloth" is the WebGL background drawn by `static/js/veil.js`; "ink" is the wash it paints under a link when the pointer or keyboard is on it. The pages themselves move in Tasks 4 to 6. A fenced code block in the column is `components/code_block.html` (a Copy button over a scroll box, drawn by `static/css/components/code_block.css`, which Task 1's `render_reading` uses); this task makes the column leave it exactly as that stylesheet draws it anywhere, and tests that.

Decisions made here, so the executor does not have to guess:

- **Where reading typography lives:** in `backend/static/css/base.css`, straight after the `.chapter pre code` rule, because chapter typography lives there and every shell page already loads `base.css`. No extra stylesheet.
- **Links in the three components are prose links, not `.interactive`.** On chapter pages, links inside running text get a dotted underline that gives way to the flat fill or the ink wash, and `.interactive` (with `.label`) is kept for one-line labelled actions (the menu, Come in, Copy, a plan's button). A section list, a list of post titles and a "Previous" title are titles in running text that wrap onto several lines on a phone. `.interactive` draws its fill as a box inset from the element, sized for one short line, which looks wrong around a wrapped title; a dotted underline and a background on the link's own line boxes wrap correctly. So the rule that styles `.chapter :is(p, dd) a` also covers the reading column, in `li`, `td`, `th` and `blockquote` as well as `p` and `dd`, and the components put their links in `li` and `p`.
- **Element rules are written `.reading :where(…)`.** `:where()` adds no specificity, so a rule like `.reading :where(p)` weighs one class, the same as a component's own class (`.pager__item`). The component stylesheets are linked after `base.css`, so at equal weight the component wins. A test holds the link order.
- **The section list shows no numbers of its own.** The legal documents already number their headings in their words ("1. What we collect"), so a numbered list would read "1. 1. What we collect". It is still an `<ol>` (the order is the page's), with `role="list"` so Safari keeps the list semantics when `list-style: none` is set.
- **`entries` renders nothing for an empty sequence.** Its caller shows its own "Nothing here yet." text.
- **A code block is `code_block.css`'s, in the column as on Begin.** The column's own `pre` rules are for a bare `<pre>` (an indented code block, which Markdown makes without a fence). They weigh one class or less, so the component's rules (`.code-block pre`, one class and a type; `.code-block`, one class and linked later) win wherever both set a property. What a rule cannot lose by weight is what the column hands down to its text, and that is stopped at the block: `.reading :where(.code-block)` sets `font-style: normal` (a quotation is italic, code is not) and `overflow-wrap: normal` (the column wraps long words anywhere, which would reach Copy and the status line), and the paragraph margin leaves out `.visually-hidden` (the block's status line is a `<p>`, and the margin that clips it is what hides it). `ReadingCodeBlockTests` holds this: it draws the block inside the column (on its own, ending a list item, in a quotation) and outside it, with a small cascade written in the test module, and fails on any property the two differ in, except the column's own lining figures and its refusal of a synthetic bold.

**Files:**
- Create: `backend/templates/layouts/reading.html`
- Create: `backend/templates/components/sections.html`, `backend/templates/components/pager.html`, `backend/templates/components/entries.html`
- Create: `backend/static/css/components/sections.css`, `backend/static/css/components/pager.css`, `backend/static/css/components/entries.css`
- Create (tests): `backend/pages/tests_reading.py`
- Modify: `backend/templates/shell.html` (three stylesheet links, after `code_block.css`), `backend/static/css/base.css` (the reading column; the prose-link rules), `backend/static/js/veil.js` (`INK_TARGETS`), `backend/pages/catalog.py` (three entries and the `within` field), `backend/pages/views.py` (`component_catalog` shows `within`), `AGENTS.md` (step 4 of "Change or add a component")
- Modify (tests): `backend/pages/tests_catalog.py`

**Interfaces:**
- Consumes:
  - `blog.render.Section` (Task 1): a frozen dataclass with `id` and `title`.
  - `shell.html`'s blocks `preload_display`, `body_class` and `main`.
  - The `.table-scroll` wrapper that `render_markdown` writes around a table, and the focusable `<pre>` it writes for a code block that is not fenced.
  - `components/code_block.html` and `static/css/components/code_block.css` (PR 15; linked by `shell.html` after `base.css`): a `div.code-block` holding the Copy button, a focusable `<pre><code>` and a `p.visually-hidden` status line. `render_reading` (Task 1) renders every fenced block through it. Nothing here changes either file.
  - The `.chapter` rules in `base.css`.
  - Test helpers `request_for` (`pages/tests_head.py`), `render_shell` (`pages/tests_shell.py`) and `BANNED` (`pages/tests_chapters.py`).
- Produces:
  - `layouts/reading.html`: body class `layout-reading`; `<div class="reading">{% block reading %}{% endblock %}</div>` inside the one `<main>`; the Newsreader preload (the default `preload_reading` block, left alone) and the Cormorant italic 300 preload for the title (`preload_display`, as `chapter.html` does).
  - Plain `<h1>`, `<h2>`, `<h3>`, `<p>`, lists, tables, `<pre>`, `<blockquote>`, `<hr>`, `<strong>` and `<em>` inside `.reading` are styled by `base.css`. Templates add no classes to them. A `.code-block` inside `.reading` is drawn as `code_block.css` draws it, and nothing the column sets on its text reaches it (`.reading :where(.code-block)`).
  - `.reading__meta`: a mono, uppercase, muted line above an `<h1>`, for example `<p class="reading__meta">Journal · 2026-10-12 · 4 min read</p>`.
  - `components/sections.html` (input `sections`; renders nothing for fewer than two), `components/pager.html` (inputs `previous` and `next`, each `None` or an object with `url` and `title`; renders nothing when both are `None`), `components/entries.html` (input `entries`, objects with `url`, `title`, `description` and optional `date`, a `datetime.date`; an object with no `date` attribute is fine).
  - `Entry.within` in `pages/catalog.py`: the class of a wrapper `<div>` the catalog puts a component in, `"reading"` for these three.
  - `INK_TARGETS` in `veil.js` lists the reading column's prose links.

- [ ] **Step 1: Write the failing tests**

Create `backend/pages/tests_reading.py`:

```python
"""The reading layout (spec 4.6, 6.2): its template, its column's typography and ink, the components that go in it, and the code blocks inside it."""
import datetime
import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple

from django.conf import settings
from django.template import engines
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from blog.render import Section
from pages.tests_chapters import BANNED
from pages.tests_head import request_for
from pages.tests_shell import render_shell

BACKEND = Path(settings.BASE_DIR)
STATIC = BACKEND / "static"
TEMPLATES = BACKEND / "templates"
BASE_CSS = STATIC / "css" / "base.css"
COMPONENTS = ("sections", "pager", "entries")
# The templates this task adds: none may put a <header> or <footer> in <main>, and their words keep the voice.
OWN_TEMPLATES = ("layouts/reading.html", *(f"components/{name}.html" for name in COMPONENTS))
# The chapters' prose links (base.css, veil.js) and the reading column's, which are styled and inked the same way.
CHAPTER_LINKS = ".chapter :is(p, dd) a:not(.interactive, .label)"
READING_LINKS = ".reading :is(p, li, td, th, blockquote, dd) a:not(.interactive, .label)"


def selector_list(text):
    """A selector list split at its top-level commas (a comma inside :is() or :not() belongs to it), spaces collapsed."""
    parts, depth, current = [], 0, ""
    for char in text:
        depth += (char == "(") - (char == ")")
        if char == "," and depth == 0:
            parts.append(" ".join(current.split()))
            current = ""
        else:
            current += char
    return parts + [" ".join(current.split())]


def rules(css):
    """Each innermost rule of a stylesheet as (its selectors, {property: value}), comments removed."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(selector_list(selector), {name: " ".join(value.split()) for name, value in re.findall(r"([\w-]+)\s*:\s*([^;]+)", body)})
            for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def declarations(css, selector):
    """What the rules for exactly this selector (alone or in a list) set; a later rule overrides an earlier one."""
    found = {}
    for selectors, properties in rules(css):
        if selector in selectors:
            found.update(properties)
    return found


class ReadingLayoutTests(SimpleTestCase):
    longMessage = False  # the page is thousands of characters: print the message that names the file and the fix, not the page

    def setUp(self):
        page = engines["django"].from_string(
            '{% extends "layouts/reading.html" %}{% block reading %}<h1>Reading</h1><p>Words.</p>{% endblock %}')
        self.html = page.render({}, request_for("/app/components/"))  # a page outside the registry

    def test_the_body_wears_the_layouts_class(self):
        self.assertIn('<body class="layout-reading" data-veil-src="/static/js/veil.js">', self.html,
                      "backend/templates/layouts/reading.html: {% block body_class %}layout-reading{% endblock %}")

    def test_the_words_sit_in_the_reading_wrapper_in_the_one_main(self):
        self.assertEqual(self.html.count("<main"), 1, "backend/templates/layouts/reading.html: one <main>, the shell's")
        self.assertEqual(self.html.count("</main>"), 1, "backend/templates/layouts/reading.html: one <main>, the shell's")
        self.assertIn('<main id="main" class="shell-main"><div class="reading"><h1>Reading</h1><p>Words.</p></div></main>', self.html,
                      'backend/templates/layouts/reading.html: {% block main %}<div class="reading">{% block reading %}{% endblock %}</div>{% endblock %}')
        self.assertNotIn('class="chapter"', self.html, "backend/templates/layouts/reading.html: the column is .reading, not .chapter")

    def test_a_page_outside_the_registry_carries_no_layout_for_shell_js(self):
        self.assertNotIn("data-layout", self.html, "backend/templates/shell.html: data-layout comes only from a registry page's layout")

    def test_it_preloads_the_reading_face_and_the_display_face_of_the_title(self):
        for path in ("fonts/newsreader/normal-400-500-latin.woff2", "fonts/cormorant-garamond/italic-300-latin.woff2"):
            with self.subTest(path=path):
                self.assertIn(f'<link rel="preload" href="/static/{path}" as="font" type="font/woff2" crossorigin>', self.html,
                              f"backend/templates/layouts/reading.html: preload {path} (the display face in its preload_display block; the reading face is shell.html's default)")


class OwnTemplatesTests(SimpleTestCase):
    longMessage = False

    def test_none_puts_a_header_or_footer_element_in_main(self):
        for name in OWN_TEMPLATES:
            with self.subTest(template=name):
                self.assertNotRegex((TEMPLATES / name).read_text(encoding="utf-8"), r"<(header|footer)\b",
                                    f"backend/templates/{name}: base.css hides a bare <footer> without JavaScript and pads it on phones; "
                                    "use a <nav> or a <div> inside <main>")

    def test_their_words_keep_the_voice(self):
        for name in OWN_TEMPLATES:
            text = (TEMPLATES / name).read_text(encoding="utf-8")
            where = f"backend/templates/{name}"
            with self.subTest(template=name):
                self.assertNotIn("—", text, f"{where}: no em dashes; use a colon, a comma or two sentences")
                self.assertNotIn("!", text, f"{where}: no exclamation marks")
                for word in BANNED:
                    self.assertNotIn(word, text.lower(), f"{where}: avoid {word.strip()!r} (AGENTS.md, voice)")

    def test_the_component_stylesheets_come_after_base_css(self):
        # The reading column's element rules and a component's class rules weigh the same; the later stylesheet wins.
        html = render_shell()
        base = html.index('href="/static/css/base.css"')
        for name in COMPONENTS:
            link = f'href="/static/css/components/{name}.css"'
            with self.subTest(component=name):
                self.assertIn(link, html, f"backend/templates/shell.html: link css/components/{name}.css")
                self.assertGreater(html.index(link), base,
                                   f"backend/templates/shell.html: link css/components/{name}.css after css/base.css, "
                                   "so the component's classes override the reading column's element rules")


def section_list(*pairs):
    return tuple(Section(id=name, title=title) for name, title in pairs)


class SectionsTests(SimpleTestCase):
    longMessage = False

    def test_renders_nothing_for_fewer_than_two_sections(self):
        for sections in (None, (), section_list(("only", "Only one"))):
            with self.subTest(sections=sections):
                self.assertEqual(render_to_string("components/sections.html", {"sections": sections}).strip(), "",
                                 "backend/templates/components/sections.html: show nothing for fewer than two sections")

    def test_lists_the_sections_in_order_as_links_to_their_headings(self):
        html = render_to_string("components/sections.html",
                                {"sections": section_list(("auth", "Auth"), ("upload", "Upload"), ("errors", "Errors"))})
        self.assertIn('<nav class="sections" aria-label="Contents">', html, "backend/templates/components/sections.html: a <nav> labelled Contents")
        self.assertIn('<ol class="sections__list" role="list">', html, "backend/templates/components/sections.html: an <ol> with role=list")
        self.assertEqual(re.findall(r'<li><a href="#([^"]+)">([^<]+)</a></li>', html),
                         [("auth", "Auth"), ("upload", "Upload"), ("errors", "Errors")],
                         "backend/templates/components/sections.html: one <li><a href=\"#id\">title</a></li> per section, in order")

    def test_escapes_titles_and_ids(self):
        html = render_to_string("components/sections.html",
                                {"sections": section_list(('a" onclick="x', "<b>Fish</b> & chips"), ("b", "B"))})
        self.assertIn('<a href="#a&quot; onclick=&quot;x">&lt;b&gt;Fish&lt;/b&gt; &amp; chips</a>', html,
                      "backend/templates/components/sections.html: print {{ section.id }} and {{ section.title }} escaped")
        self.assertNotIn("<b>", html, "backend/templates/components/sections.html: never |safe a title")


class PagerTests(SimpleTestCase):
    longMessage = False
    OLDER = SimpleNamespace(url="/blog/older/", title="The older post")
    NEWER = SimpleNamespace(url="/blog/newer/", title="The newer post")

    def render(self, previous, next):
        return render_to_string("components/pager.html", {"previous": previous, "next": next})

    def test_renders_nothing_when_there_is_no_neighbour(self):
        self.assertEqual(self.render(None, None).strip(), "", "backend/templates/components/pager.html: show nothing when both are None")

    def test_names_both_neighbours_previous_first(self):
        html = self.render(self.OLDER, self.NEWER)
        self.assertIn('<nav class="pager" aria-label="More to read">', html, "backend/templates/components/pager.html: a <nav> labelled More to read")
        self.assertEqual(re.findall(r'<span class="pager__label label">(\w+)</span> <a href="([^"]+)" rel="(\w+)">([^<]+)</a>', html),
                         [("Previous", "/blog/older/", "prev", "The older post"), ("Next", "/blog/newer/", "next", "The newer post")],
                         "backend/templates/components/pager.html: Previous first, then Next, each a label and a link with rel=prev or rel=next")

    def test_the_newest_page_has_only_a_previous_and_the_oldest_only_a_next(self):
        newest, oldest = self.render(self.OLDER, None), self.render(None, self.NEWER)
        self.assertIn("Previous", newest, "backend/templates/components/pager.html: show Previous when there is one")
        self.assertNotIn("Next", newest, "backend/templates/components/pager.html: leave out Next when there is none")
        self.assertIn("Next", oldest, "backend/templates/components/pager.html: show Next when there is one")
        self.assertNotIn("Previous", oldest, "backend/templates/components/pager.html: leave out Previous when there is none")

    def test_escapes_titles(self):
        html = self.render(SimpleNamespace(url="/blog/x/", title="<i>One</i> & two"), None)
        self.assertIn(">&lt;i&gt;One&lt;/i&gt; &amp; two</a>", html, "backend/templates/components/pager.html: print {{ previous.title }} escaped")
        self.assertNotIn("<i>", html, "backend/templates/components/pager.html: never |safe a title")


class EntriesTests(SimpleTestCase):
    longMessage = False
    NEWER = SimpleNamespace(url="/blog/newer/", title="The newer post", description="What it says.", date=datetime.date(2026, 10, 12))
    OLDER = SimpleNamespace(url="/blog/older/", title="The older post", description="", date=datetime.date(2026, 10, 3))

    def render(self, entries):
        return render_to_string("components/entries.html", {"entries": entries})

    def test_renders_nothing_for_no_entries(self):
        self.assertEqual(self.render(()).strip(), "", "backend/templates/components/entries.html: show nothing for an empty sequence")

    def test_lists_each_entry_in_order_with_its_title_date_and_description(self):
        html = self.render((self.NEWER, self.OLDER))
        self.assertIn('<ol class="entries" role="list">', html, "backend/templates/components/entries.html: an <ol> with role=list")
        self.assertEqual(re.findall(r'<p class="entries__title"><a href="([^"]+)">([^<]+)</a></p>', html),
                         [("/blog/newer/", "The newer post"), ("/blog/older/", "The older post")],
                         "backend/templates/components/entries.html: each title is <p class=\"entries__title\"><a href=...>")
        self.assertEqual(re.findall(r'<time class="entries__date label" datetime="([^"]+)">([^<]+)</time>', html),
                         [("2026-10-12", "2026-10-12"), ("2026-10-03", "2026-10-03")],
                         "backend/templates/components/entries.html: each date is a <time class=\"entries__date label\" datetime=YYYY-MM-DD>")
        self.assertEqual(re.findall(r'<p class="entries__description">([^<]+)</p>', html), ["What it says."],
                         "backend/templates/components/entries.html: a description only when there is one")

    def test_an_entry_with_no_date_shows_none(self):
        # The Docs index lists pages that have no date at all: the value is None, or the object has no such attribute.
        html = self.render((SimpleNamespace(url="/docs/api/", title="API reference", description="Every endpoint.", date=None),
                            SimpleNamespace(url="/docs/agent/", title="Agent setup", description="Keys and clients.")))
        self.assertNotIn("<time", html, "backend/templates/components/entries.html: no <time> for an entry without a date")
        self.assertEqual(html.count('<li class="entries__item">'), 2, "backend/templates/components/entries.html: one <li class=\"entries__item\"> per entry")

    def test_escapes_titles_and_descriptions(self):
        html = self.render((SimpleNamespace(url="/blog/x/", title="<script>alert(1)</script>", description="Tom & <b>Jerry</b>", date=None),))
        self.assertNotIn("<script>", html, "backend/templates/components/entries.html: print {{ entry.title }} escaped")
        self.assertNotIn("<b>", html, "backend/templates/components/entries.html: print {{ entry.description }} escaped")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html, "backend/templates/components/entries.html: print {{ entry.title }} escaped")
        self.assertIn("Tom &amp; &lt;b&gt;Jerry&lt;/b&gt;", html, "backend/templates/components/entries.html: print {{ entry.description }} escaped")


class ReadingCssTests(SimpleTestCase):
    longMessage = False

    def setUp(self):
        self.css = BASE_CSS.read_text(encoding="utf-8")

    def declared(self, selector):
        found = declarations(self.css, selector)
        self.assertTrue(found, f"static/css/base.css: add the rule `{selector} {{ ... }}` for the reading column (spec 4.6)")
        return found

    def test_the_column_is_centred_at_the_measure_and_starts_at_the_top(self):
        main = self.declared(".layout-reading .shell-main")
        self.assertEqual(main.get("justify-items"), "center", "static/css/base.css: `.layout-reading .shell-main` centres the column: justify-items: center")
        self.assertEqual(main.get("align-content"), "start",
                         "static/css/base.css: `.layout-reading .shell-main` starts the column at the top: align-content: start (a chapter is centred, a long page scrolls)")
        column = self.declared(".reading")
        for name, value in (("max-width", "var(--measure)"), ("width", "100%"), ("min-width", "0")):
            self.assertEqual(column.get(name), value, f"static/css/base.css: `.reading` needs {name}: {value}")

    def test_long_words_wrap_in_running_text_and_code_and_tables_scroll_in_their_own_boxes(self):
        self.assertEqual(self.declared(".reading").get("overflow-wrap"), "anywhere",
                         "static/css/base.css: `.reading` needs overflow-wrap: anywhere, or one long address in a paragraph scrolls the page sideways on a phone")
        for selector in (".reading :where(pre)", ".reading :where(th, td)"):
            self.assertEqual(self.declared(selector).get("overflow-wrap"), "normal",
                             f"static/css/base.css: `{selector}` needs overflow-wrap: normal; these scroll sideways in their own boxes, "
                             "and `anywhere` would squeeze a table's columns to a letter wide")
        for selector in (".reading :where(pre)", ".reading :where(.table-scroll)"):
            self.assertEqual(self.declared(selector).get("overflow-x"), "auto",
                             f"static/css/base.css: `{selector}` needs overflow-x: auto, or a long line or a wide table scrolls the page")

    def test_a_table_has_hairlines_between_its_rows(self):
        self.assertEqual(self.declared(".reading :where(table)").get("border-collapse"), "collapse",
                         "static/css/base.css: `.reading :where(table)` needs border-collapse: collapse")
        self.assertIn("var(--hair)", self.declared(".reading :where(th, td)").get("border-bottom", ""),
                      "static/css/base.css: `.reading :where(th, td)` needs a border-bottom in var(--hair)")

    def test_bold_is_newsreaders_500_and_never_a_synthetic_bold(self):
        self.assertEqual(self.declared(".reading :where(strong, b)").get("font-weight"), "500",
                         "static/css/base.css: `.reading :where(strong, b)` needs font-weight: 500, the heaviest face Newsreader has")
        self.assertEqual(self.declared(".reading").get("font-synthesis-weight"), "none",
                         "static/css/base.css: `.reading` needs font-synthesis-weight: none, or a heavier request is drawn in a fake bold")
        self.assertEqual(self.declared(".reading :where(em, i, cite)").get("font-style"), "italic",
                         "static/css/base.css: `.reading :where(em, i, cite)` needs font-style: italic")

    def test_lists_keep_the_browsers_markers_and_an_indent_for_them(self):
        lists = self.declared(".reading :where(ul, ol)")
        self.assertNotIn("list-style", lists, "static/css/base.css: `.reading :where(ul, ol)` keeps the markers; a component that wants none sets its own list-style")
        self.assertIn("1.8em", lists.get("padding", ""), "static/css/base.css: `.reading :where(ul, ol)` needs padding-left: 1.8em for the markers")

    def test_the_reading_columns_prose_links_are_styled_like_the_chapters(self):
        every_rule = rules(self.css)
        for suffix in ("", ":focus-visible", ":hover", ":active", ".is-inked"):
            with self.subTest(suffix=suffix or "at rest"):
                both = [properties for selectors, properties in every_rule
                        if CHAPTER_LINKS + suffix in selectors and READING_LINKS + suffix in selectors]
                self.assertTrue(both, f"static/css/base.css: put `{READING_LINKS + suffix}` in the same rule as `{CHAPTER_LINKS + suffix}` "
                                      "(its selector list), so links in a list, a table or a quotation look and answer like a chapter's")

    def test_nothing_in_the_column_climbs_above_the_corners_or_makes_main_a_backdrop_root(self):
        # AGENTS.md, Site checks: nothing inside <main> uses a z-index above 2 (the corners are at 3), and <main> has no filter, opacity,
        # mask or will-change outside a change's cross-fade, the narrow top fade and the phone sheet's mask (base.css, none of them here).
        sheets = {"static/css/base.css": [(selectors, properties) for selectors, properties in rules(self.css)
                                          if any(".reading" in selector for selector in selectors)]}
        for name in COMPONENTS:
            sheets[f"static/css/components/{name}.css"] = rules((STATIC / "css" / "components" / f"{name}.css").read_text(encoding="utf-8"))
        for where, found in sheets.items():
            for selectors, properties in found:
                z_index = properties.get("z-index", "0")
                if re.fullmatch(r"-?\d+", z_index):
                    self.assertLessEqual(int(z_index), 2, f"{where}: `{selectors[0]}` has z-index {z_index}; nothing inside <main> may use more than 2")
        main = self.declared(".layout-reading .shell-main")
        for name in ("filter", "backdrop-filter", "opacity", "mask-image", "-webkit-mask-image", "will-change"):
            self.assertNotIn(name, main, f"static/css/base.css: `.layout-reading .shell-main` must not set {name}: it would make <main> a backdrop root (AGENTS.md, Site checks)")

    def test_no_stylesheet_has_an_em_dash(self):
        for path in sorted((STATIC / "css").rglob("*.css")):
            for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
                self.assertNotIn("—", line, f"backend/{path.relative_to(BACKEND)}:{number}: no em dashes, in comments too; use a colon, a comma or two sentences")


class ReadingInkTests(SimpleTestCase):
    longMessage = False

    def test_the_cloth_inks_the_reading_columns_prose_links_as_it_inks_the_chapters(self):
        source = (STATIC / "js" / "veil.js").read_text(encoding="utf-8")
        found = re.search(r'^const INK_TARGETS = "([^"]*)";', source, re.M)
        self.assertIsNotNone(found, 'static/js/veil.js: `const INK_TARGETS = "...";` is the selector for what the cloth inks')
        targets = selector_list(found.group(1))
        for selector in (".interactive", CHAPTER_LINKS, READING_LINKS):
            self.assertIn(selector, targets, f"static/js/veil.js: INK_TARGETS must list `{selector}`")


# A small cascade, to draw a code block twice, inside the reading column and outside it, and compare. It reads what base.css and the
# component stylesheets use: type, class, id and attribute selectors, :is(), :where(), :not(), :first-child, :last-child, and the
# descendant, child and sibling combinators. A state or a relation (:hover, :focus-visible, :has(), :root) and a pseudo-element are taken
# not to match, as on a page at rest. A declaration is compared as it is written; `inherit` passes the parent's declaration on.
class Declaration(NamedTuple):
    rank: tuple  # (important, specificity, order in the stylesheets): the greatest wins
    selector: str
    prop: str
    value: str


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children = tag, attrs, parent, []
        self.classes = set((attrs.get("class") or "").split())
        if parent:
            parent.children.append(self)


def parse_html(html):
    """The elements of html as a tree under a root node; text is not kept. The markup here has no void elements."""
    class Builder(HTMLParser):
        def __init__(self):
            super().__init__()
            self.root = self.open = Node("#root", {}, None)

        def handle_starttag(self, tag, attrs):
            self.open = Node(tag, dict(attrs), self.open)

        def handle_endtag(self, tag):
            self.open = self.open.parent

    builder = Builder()
    builder.feed(html)
    return builder.root


SIMPLE = re.compile(r"\*|[A-Za-z][\w-]*|[.#][\w-]+|\[[^\]]*\]|::?[\w-]+(?:\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\))?")


@lru_cache(maxsize=None)
def parse_selector(selector):
    """A complex selector as ((combinator, compound), ...), left to right; a compound's combinator is the one before it (" ", ">", "+", "~"; "" first)."""
    tokens, current, depth = [], "", 0
    for char in selector.strip():
        depth += (char in "([") - (char in ")]")
        if depth == 0 and (char.isspace() or char in ">+~"):
            if current:
                tokens.append(current)
                current = ""
            if char in ">+~":
                tokens.append(char)
        else:
            current += char
    if current:
        tokens.append(current)
    parts, combinator = [], ""
    for token in tokens:
        if token in (">", "+", "~"):
            combinator = token
        else:
            parts.append((combinator or (" " if parts else ""), token))
            combinator = ""
    return tuple(parts)


def specificity(selector):
    """(ids, classes and the like, types): :where() weighs nothing, :is(), :not() and :has() what their weightiest argument does."""
    total = [0, 0, 0]
    for _, compound in parse_selector(selector):
        for simple in SIMPLE.findall(compound):
            name, _, rest = simple.lstrip(":").partition("(")
            if simple[0] == "#":
                total[0] += 1
            elif simple[0] in ".[":
                total[1] += 1
            elif simple.startswith("::"):
                total[2] += 1
            elif simple[0] == ":" and name == "where":
                continue
            elif simple[0] == ":" and name in ("is", "not", "has"):
                heaviest = max(specificity(argument) for argument in selector_list(rest[:-1]))
                total = [a + b for a, b in zip(total, heaviest)]
            elif simple[0] == ":":
                total[1] += 1
            elif simple != "*":
                total[2] += 1
    return tuple(total)


def attribute_matches(inner, node):
    name, operator, value = re.fullmatch(r"\s*([\w-]+)\s*(?:([~|^$*]?=)\s*(.*?))?\s*", inner).groups()
    if name not in node.attrs:
        return False
    return operator is None or (operator == "=" and node.attrs[name] == value.strip("\"'"))


def compound_matches(compound, node):
    simples = SIMPLE.findall(compound)
    if node.tag == "#root" or not simples:
        return False
    for simple in simples:
        name, _, rest = simple.lstrip(":").partition("(")
        arguments = selector_list(rest[:-1]) if rest else []
        if simple == "*":
            continue
        if simple[0] == ".":
            found = simple[1:] in node.classes
        elif simple[0] == "#":
            found = node.attrs.get("id") == simple[1:]
        elif simple[0] == "[":
            found = attribute_matches(simple[1:-1], node)
        elif simple.startswith("::"):
            found = False
        elif simple[0] == ":" and name in ("is", "where"):
            found = any(matches(argument, node) for argument in arguments)
        elif simple[0] == ":" and name == "not":
            found = not any(matches(argument, node) for argument in arguments)
        elif simple[0] == ":" and name in ("first-child", "last-child"):
            found = node.parent.children[0 if name == "first-child" else -1] is node
        elif simple[0] == ":":
            found = False  # a state or a relation
        else:
            found = node.tag == simple.lower()
        if not found:
            return False
    return True


def matches(selector, node):
    parts = parse_selector(selector)

    def chain(index, element):
        combinator, compound = parts[index]
        if not compound_matches(compound, element):
            return False
        if index == 0:
            return True
        if combinator == ">":
            return element.parent is not None and chain(index - 1, element.parent)
        if combinator == " ":
            ancestor = element.parent
            while ancestor is not None:
                if chain(index - 1, ancestor):
                    return True
                ancestor = ancestor.parent
            return False
        before = element.parent.children[:element.parent.children.index(element)] if element.parent else []
        if combinator == "+":
            return bool(before) and chain(index - 1, before[-1])
        return any(chain(index - 1, sibling) for sibling in before)  # "~"

    return bool(parts) and chain(len(parts) - 1, node)


def cascade(stylesheets):
    """Every declaration of the stylesheets, given in the order a page links them, ranked as the cascade ranks them."""
    found, order = [], 0
    for css in stylesheets:
        for selectors, properties in rules(css):
            for selector in selectors:
                if selector.startswith("@"):
                    continue
                weight = specificity(selector)
                for prop, value in properties.items():
                    order += 1
                    important = value.endswith("!important")
                    found.append(Declaration((important, weight, order), selector, prop, value.removesuffix("!important").strip()))
    return found


# What a shorthand sets, so that a declaration of it and one of a longhand compete for the same thing.
SIDES = ("top", "right", "bottom", "left")
SHORTHANDS = {
    "margin": [f"margin-{side}" for side in SIDES],
    "padding": [f"padding-{side}" for side in SIDES],
    "border": [f"border-{side}-{part}" for side in SIDES for part in ("width", "style", "color")],
    **{f"border-{side}": [f"border-{side}-{part}" for part in ("width", "style", "color")] for side in SIDES},
    "background": ["background-color", "background-image"],
    "font": ["font-style", "font-weight", "font-size", "line-height", "font-family", "font-variant-numeric"],
    "overflow": ["overflow-x", "overflow-y"],
}
INHERITED = {"color", "font-family", "font-size", "font-style", "font-synthesis-weight", "font-variant-numeric", "font-weight", "letter-spacing",
             "line-height", "overflow-wrap", "text-align", "text-transform", "white-space"}
# A declaration of the initial value is the same as none.
INITIAL = {("overflow-wrap", "normal"), ("font-style", "normal")}
# What the column sets for all its text: lining figures (a chapter sets them too) and no synthetic bold (nothing in a block is bold).
THE_COLUMNS_OWN = {"font-variant-numeric", "font-synthesis-weight"}


def covers(prop, longhand):
    """Whether declaring prop sets longhand: it is the property itself, or a shorthand that includes it."""
    return longhand in SHORTHANDS.get(prop, (prop,))


def effective(node, longhand, matched):
    """The declaration that sets longhand on node: the cascade's winner among those matching it, or, for an inherited property
    that nothing sets here (or `inherit` does), its parent's."""
    winner = max((d for d in matched[node] if covers(d.prop, longhand)), default=None)
    if winner is not None and winner.value != "inherit":
        return winner
    if longhand in INHERITED and node.parent is not None:
        return effective(node.parent, longhand, matched)
    return None


def drawing(html, stylesheets, parts):
    """{(part, longhand): (property, value, selector) or None}: how each part of html is drawn, by the declarations that win."""
    root = parse_html(html)
    nodes, queue = [], [root]
    while queue:
        node = queue.pop()
        nodes.append(node)
        queue.extend(node.children)
    declared = cascade(stylesheets)
    matched = {node: [d for d in declared if matches(d.selector, node)] for node in nodes}
    longhands = sorted({longhand for d in declared if not d.prop.startswith("--") for longhand in SHORTHANDS.get(d.prop, [d.prop])})
    found = {}
    for name, find in parts.items():
        node = next(node for node in nodes if find(node))
        for longhand in longhands:
            winner = effective(node, longhand, matched)
            keep = winner is not None and (winner.prop, winner.value) not in INITIAL
            found[name, longhand] = (winner.prop, winner.value, winner.selector) if keep else None
    return found


# The five elements of components/code_block.html.
CODE_BLOCK_PARTS = {
    "block": lambda node: "code-block" in node.classes,
    "Copy button": lambda node: node.tag == "button",
    "pre": lambda node: node.tag == "pre",
    "code": lambda node: node.tag == "code",
    "status line": lambda node: "data-copy-status" in node.attrs,
}
# Where the column holds a code block, around it: as a block of its own, ending a list item, and in a quotation (which is italic).
COLUMN_AROUND = {
    "in the column": ('<div class="reading">', "</div>"),
    "ending a list item": ('<div class="reading"><ul><li><p>Run it:</p>', "</li></ul></div>"),
    "in a quotation": ('<div class="reading"><blockquote>', "</blockquote></div>"),
}


def shown(found):
    return f"`{found[0]}: {found[1]}` from `{found[2]}`" if found else "not set"


def code_block_differences(stylesheets):
    """What the column changes in how a code block is drawn, as sentences: the block drawn inside .reading, against the same markup
    outside it, for every property the stylesheets set except those the column sets for all its text."""
    block = render_to_string("components/code_block.html", {"id": "t-one", "code": "npm install -g veilfile", "label": "Copy the install command"})
    differences = []
    for where, (before, after) in COLUMN_AROUND.items():
        inside = drawing(before + block + after, stylesheets, CODE_BLOCK_PARTS)
        outside = drawing(before.replace(' class="reading"', "") + block + after, stylesheets, CODE_BLOCK_PARTS)
        grouped = {}  # the four sides of a margin are one finding
        for (part, longhand), here in inside.items():
            if longhand not in THE_COLUMNS_OWN and here != outside[part, longhand]:
                grouped.setdefault((part, shown(here), shown(outside[part, longhand])), []).append(longhand)
        differences += [f"{where}, the {part}, {', '.join(longhands)}: {here} inside .reading, {there} outside it"
                        for (part, here, there), longhands in grouped.items()]
    return differences


def linked_stylesheets():
    """The stylesheets of the shell, as their texts, in the order shell.html links them."""
    return [(STATIC / path).read_text(encoding="utf-8") for path in re.findall(r'href="/static/(css/[^"]+\.css)"', render_shell())]


class ReadingCodeBlockTests(SimpleTestCase):
    longMessage = False

    def test_the_column_draws_a_code_block_as_code_block_css_does(self):
        # A fenced block in a Journal post or a Docs page is components/code_block.html. code_block.css draws it (its box, its type, its
        # Copy button in a row of its own), and the column's rules for text must not reach into it, wherever it sits.
        differences = code_block_differences(linked_stylesheets())
        self.assertEqual(differences, [],
                         "static/css/base.css: a rule of the reading column changes how components/code_block.html is drawn (code_block.css decides that). "
                         "Keep the rule off the block, for example `p:not(.visually-hidden)`, or give `.reading :where(.code-block)` the value the block has outside the "
                         "column (font-style: normal, overflow-wrap: normal):\n" + "\n".join(differences))

    def test_the_comparison_reads_the_cascade_it_is_there_to_judge(self):
        # Not a pass for want of looking: the column's own rule for a bare <pre> does reach the block's <pre>, and code_block.css is what wins.
        block = render_to_string("components/code_block.html", {"id": "t-one", "code": "x", "label": "Copy x"})
        sheets = linked_stylesheets()
        pre = drawing('<div class="reading">' + block + "</div>", sheets, {"pre": CODE_BLOCK_PARTS["pre"]})
        for longhand in ("padding-top", "border-top-width", "overflow-x", "font-size"):
            self.assertEqual(pre["pre", longhand][2], ".code-block pre",
                             f"static/css/components/code_block.css must set {longhand} of `.code-block pre` and win over the column's `.reading :where(pre)` in base.css")
        reaching = [d for d in cascade(sheets) if d.selector == ".reading :where(pre)" and d.prop == "padding"]
        self.assertTrue(reaching, "static/css/base.css: `.reading :where(pre)` gives a bare <pre> its box; this test needs that rule to show code_block.css wins over it")
        self.assertEqual(specificity(".reading :where(pre)"), (0, 1, 0), "`:where()` must weigh nothing")
        self.assertEqual(specificity(".code-block pre"), (0, 1, 1))
        self.assertEqual(specificity(".reading :is(p, li) a:not(.interactive, .label)"), (0, 2, 2))

    def test_the_comparison_sees_a_rule_that_reaches_into_the_block(self):
        def differences_with(rule):  # base.css has the rule and .visually-hidden; the component's stylesheet comes after it, as shell.html links them
            return code_block_differences([".visually-hidden { margin: -1px } " + rule, ".code-block pre { padding: 1px }"])

        for rule in (".reading :where(p) { margin: 0 0 1rem }",                    # as late and as light as .visually-hidden: wins
                     ".reading .code-block pre { padding: 9px }",                  # heavier than the component's rule
                     ".reading :where(div) { border: 1px solid }",                 # adds a box the component does not draw
                     ".reading :where(blockquote) { font-style: italic }"):        # handed down to the block
            with self.subTest(rule=rule):
                self.assertTrue(differences_with(rule), f"the comparison missed `{rule}` reaching into the block")
        for rule in (".reading :where(pre) { padding: 9px }",                      # lighter than the component's rule
                     ".reading :where(p:not(.visually-hidden)) { margin: 0 0 1rem }",
                     ".reading :where(blockquote) { font-style: italic } .reading :where(.code-block) { font-style: normal }"):
            with self.subTest(rule=rule):
                self.assertEqual(differences_with(rule), [], f"the comparison called `{rule}` a rule that reaches into the block")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_reading`
Expected: `Ran 30 tests`, FAILED. Every test fails or errors except three that pass: `ReadingCssTests.test_no_stylesheet_has_an_em_dash` (no stylesheet has an em dash today), `ReadingCodeBlockTests.test_the_column_draws_a_code_block_as_code_block_css_does` (no rule of the column exists yet to reach the block) and `ReadingCodeBlockTests.test_the_comparison_sees_a_rule_that_reaches_into_the_block` (it brings its own stylesheets). `ReadingCodeBlockTests.test_the_comparison_reads_the_cascade_it_is_there_to_judge` fails with ``static/css/base.css: `.reading :where(pre)` gives a bare <pre> its box``. The layout tests error with `TemplateDoesNotExist: layouts/reading.html`. The component tests error with `TemplateDoesNotExist: components/sections.html` (and `pager.html`, `entries.html`). The two template-source tests (`test_none_puts_a_header_or_footer_element_in_main`, `test_their_words_keep_the_voice`) error with `FileNotFoundError` for `backend/templates/layouts/reading.html`. `test_the_component_stylesheets_come_after_base_css` fails with `backend/templates/shell.html: link css/components/sections.css`. The `ReadingCssTests` tests fail with messages that start `static/css/base.css: add the rule` or `static/css/base.css: put`; `test_nothing_in_the_column_climbs_above_the_corners_or_makes_main_a_backdrop_root` errors with `FileNotFoundError` for `static/css/components/sections.css`. `ReadingInkTests` fails with `static/js/veil.js: INK_TARGETS must list`.

- [ ] **Step 2: The layout, the three components and their stylesheets**

Create `backend/templates/layouts/reading.html`:

```django
{% extends "shell.html" %}
{% load static %}
{# Layout: reading. The Journal, Docs and the legal documents: one Newsreader column, starting at the top and scrolling with the page (spec 4.6, 6.2). #}
{% block preload_display %}<link rel="preload" href="{% static 'fonts/cormorant-garamond/italic-300-latin.woff2' %}" as="font" type="font/woff2" crossorigin>{% endblock %}
{% block body_class %}layout-reading{% endblock %}
{% block main %}<div class="reading">{% block reading %}{% endblock %}</div>{% endblock %}
```

(The Newsreader preload comes from `shell.html`'s `preload_reading` block, which this layout leaves as it is. The title is in the display face, so this layout preloads it, as `chapter.html` does.)

Create `backend/templates/components/sections.html`:

```django
{# Component: sections. The list of a page's sections, each a link to its heading (spec 4.6). #}
{# Inputs: sections, a sequence of objects with id and title (blog.render.Section); nothing is shown for fewer than two. #}
{% if sections|length > 1 %}<nav class="sections" aria-label="Contents">
<ol class="sections__list" role="list">
{% for section in sections %}<li><a href="#{{ section.id }}">{{ section.title }}</a></li>
{% endfor %}</ol>
</nav>
{% endif %}
```

Create `backend/templates/components/pager.html`:

```django
{# Component: pager. The page before and the page after, at the end of a reading page (spec 4.6). #}
{# Inputs: previous and next, each None or an object with url and title; nothing is shown when both are None. #}
{% if previous or next %}<nav class="pager" aria-label="More to read">
{% if previous %}<p class="pager__item pager__item--previous"><span class="pager__label label">Previous</span> <a href="{{ previous.url }}" rel="prev">{{ previous.title }}</a></p>
{% endif %}{% if next %}<p class="pager__item pager__item--next"><span class="pager__label label">Next</span> <a href="{{ next.url }}" rel="next">{{ next.title }}</a></p>
{% endif %}</nav>
{% endif %}
```

Create `backend/templates/components/entries.html`:

```django
{# Component: entries. A list of pages to read, each with its title, an optional date and its description (spec 4.6). #}
{# Inputs: entries, a sequence of objects with url, title, description and optional date (a date, shown as YYYY-MM-DD); nothing is shown for an empty sequence. #}
{% if entries %}<ol class="entries" role="list">
{% for entry in entries %}<li class="entries__item">
<p class="entries__title"><a href="{{ entry.url }}">{{ entry.title }}</a></p>
{% if entry.date %}<time class="entries__date label" datetime="{{ entry.date|date:'Y-m-d' }}">{{ entry.date|date:"Y-m-d" }}</time>
{% endif %}{% if entry.description %}<p class="entries__description">{{ entry.description }}</p>
{% endif %}</li>
{% endfor %}</ol>
{% endif %}
```

Create `backend/static/css/components/sections.css`:

```css
/* The list of a page's sections, at the top of a Docs page or a legal document (spec 4.6). It shows no numbers of its
   own: the legal documents number their headings in their words. Its links are the reading column's prose links
   (base.css), which cover list items. */
.sections {
  margin: 0 0 var(--space-5);
  padding: 0 0 var(--space-4);
  border-bottom: 1px solid var(--hair);
}

.sections__list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.sections__list li {
  margin: 0 0 var(--space-1);
}

.sections__list li:last-child {
  margin-bottom: 0;
}
```

Create `backend/static/css/components/pager.css`:

```css
/* The page before and the page after, at the end of a Journal post or a Docs page (spec 4.6): side by side where there is
   room, one under the other where there is not. Alone, a neighbour takes the whole row, so Previous stays at the left
   and Next at the right. The links are the reading column's prose links (base.css). */
.pager {
  container-type: inline-size;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3) var(--space-5);
  margin: var(--space-6) 0 0;
  padding: var(--space-4) 0 0;
  border-top: 1px solid var(--hair);
}

.pager__item {
  flex: 1 1 14rem;
  min-width: 0;
  margin: 0;
}

.pager__item--next {
  text-align: right;
}

.pager__label {
  display: block;
  margin-bottom: var(--space-1);
}

/* Where the two no longer fit side by side (two 14rem items and the 2.5rem gap: 30.5rem of the pager's own width, which is
   the column's and not the screen's, so the component catalog's narrow frames on a wide screen read as a phone does), both read
   from the left. */
@container (width < 30.5rem) {
  .pager__item--next {
    text-align: left;
  }
}
```

Create `backend/static/css/components/entries.css`:

```css
/* A list of pages to read (spec 4.6): the Journal's posts and the Docs index. Each has its title in the display face as a
   prose link (base.css), its date in mono and its description. */
.entries {
  margin: 0;
  padding: 0;
  list-style: none;
}

.entries__item {
  margin: 0 0 var(--space-5);
}

.entries__item:last-child {
  margin-bottom: 0;
}

.entries__title {
  margin: 0;
  font: var(--font-heading);
  font-variant-numeric: inherit; /* the font shorthand resets it, and .reading sets lining-nums */
}

.entries__date {
  display: block;
  margin: var(--space-1) 0 var(--space-2);
}

.entries__description {
  margin: 0;
}
```

In `backend/templates/shell.html`, replace this exact line (the last of the component links)

```django
<link rel="stylesheet" href="{% static 'css/components/code_block.css' %}">
```

with

```django
<link rel="stylesheet" href="{% static 'css/components/code_block.css' %}">
<link rel="stylesheet" href="{% static 'css/components/sections.css' %}">
<link rel="stylesheet" href="{% static 'css/components/pager.css' %}">
<link rel="stylesheet" href="{% static 'css/components/entries.css' %}">
```

(The three small stylesheets load on every shell page, chapters too. That is the repo's component rule, and `pages.tests_site_rules` requires the link in `shell.html`.)

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_reading`
Expected: FAILED, and only in `ReadingCssTests` (every test but `test_no_stylesheet_has_an_em_dash`), `ReadingInkTests` and `ReadingCodeBlockTests.test_the_comparison_reads_the_cascade_it_is_there_to_judge` (it needs the column's rule for a bare `<pre>`, which Step 4 writes). The layout, template-source, stylesheet-order and component tests (`ReadingLayoutTests`, `OwnTemplatesTests`, `SectionsTests`, `PagerTests`, `EntriesTests`) pass. (`pages.tests_catalog` and `pages.tests_site_rules` now fail until Step 3 gives the three components catalog entries.)

- [ ] **Step 3: The catalog shows the three components, inside the reading column**

The staff-only component catalog (`/app/components/`) shows every component in Paper and Ink, Motion on and off. These three only ever appear inside the reading column, whose rules style their links, so the catalog must show them inside it.

In `backend/pages/tests_catalog.py`, in `class CatalogPageTests`, after the method `test_uses_the_shell`, add:

```python
    def test_the_reading_columns_components_are_shown_inside_it(self):
        # sections, pager and entries only ever sit in the reading column, whose rules (base.css) style their links.
        inside = {entry.name for entry in catalog.entries() if entry.within == "reading"}
        self.assertEqual(inside, {"sections", "pager", "entries"},
                         "pages/catalog.py: pass within=\"reading\" to the Entry of each component the reading column holds")
        self.assertEqual(self.html.count('<div class="reading">'), 4 * len(inside))
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_catalog pages.tests_site_rules`
Expected: FAILED. `CatalogCoverageTests.test_every_component_has_an_entry` and `TemplateRulesTests.test_every_component_has_a_stylesheet_and_a_catalog_entry` fail (three components have no entry yet), and the new test errors with `AttributeError: 'Entry' object has no attribute 'within'`.

In `backend/pages/catalog.py`, replace this exact text

```python
from dataclasses import dataclass, field

from pages import registry
```

with

```python
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace

from blog.render import Section
from pages import registry
```

replace this exact line

```python
    context: dict = field(default_factory=dict)
```

with

```python
    context: dict = field(default_factory=dict)
    within: str = ""  # the class of the wrapper the page puts it in, for a component that only sits in one ("reading")
```

and replace this exact text (the last entry, `code_block`, and the end of the tuple)

```python
              {"id": "catalog-code", "code": BEGIN_SNIPPETS["claude_code"], "label": "Copy the Claude Code command"}),
    )
```

with

```python
              {"id": "catalog-code", "code": BEGIN_SNIPPETS["claude_code"], "label": "Copy the Claude Code command"}),
        Entry("sections", "The top of a Docs page or a legal document: links to its sections, left out when there are fewer than two.",
              {"sections": (Section(id="auth", title="Auth"), Section(id="upload", title="Upload"), Section(id="list", title="List"),
                            Section(id="revoke", title="Revoke"), Section(id="download", title="Download"),
                            Section(id="errors", title="Errors"))},
              within="reading"),
        Entry("pager", "The end of a Journal post or a Docs page: the page before and the page after, here the neighbours of How it works.",
              {"previous": SimpleNamespace(url="/docs/agent/", title="Agent setup"),
               "next": SimpleNamespace(url="/docs/api/", title="API reference")},
              within="reading"),
        Entry("entries", "The Journal and the Docs index: each page's title, date and description; a Docs page has no date.",
              {"entries": (
                  SimpleNamespace(url="/blog/introducing-veilfile/", title="Introducing Veilfile", date=date(2026, 10, 5),
                                  description="AI agents need somewhere private to put files. The options are public by default. "
                                              "Veilfile is the private alternative we built."),
                  SimpleNamespace(url="/docs/quickstart/", title="Quickstart", date=None,
                                  description="Sign up, verify your email, create a workspace, issue an API key, and upload your first artifact."),
                  SimpleNamespace(url="/docs/agent/", title="Agent setup", date=None,
                                  description="Give an agent a Veilfile key: env var setup, curl upload examples, MCP connection, key rotation."),
              )},
              within="reading"),
    )
```

(The new entries go last, so everything above them on the catalog page stays where it was.)

In `backend/pages/views.py`, replace this exact text

```python
from django.urls import reverse

from blog import loader as blog_loader
```

with

```python
from django.urls import reverse
from django.utils.html import format_html

from blog import loader as blog_loader
```

and replace this exact text (inside `component_catalog`)

```python
            frames.append({"theme": theme, "motion": motion, "caption": caption,
                           "html": render_to_string(f"components/{entry.name}.html", context)})
```

with

```python
            html = render_to_string(f"components/{entry.name}.html", context)
            if entry.within:  # a component that only sits inside a layout's column is shown inside it
                html = format_html('<div class="{}">{}</div>', entry.within, html)
            frames.append({"theme": theme, "motion": motion, "caption": caption, "html": html})
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_catalog pages.tests_site_rules`
Expected: OK.

- [ ] **Step 4: The reading column's typography, and prose links in lists, tables and quotations**

In `backend/static/css/base.css`, make these five replacements. They add the reading column's link selector to the five rules that style a chapter's prose links (at rest, keyboard focus, hover, press, and while the cloth holds a wash). The chapter selector stays exactly as it is, so chapter pages render as before.

1. Replace this exact text

```css
/* Inline links in prose: a dotted underline that gives way to the flat fill (spec 3.4). A labelled
   link (.label) is not one: it keeps its own look, and as .interactive it answers the cursor with
   its own inset fill, above. */
.chapter :is(p, dd) a:not(.interactive, .label) {
```

with

```css
/* Inline links in prose: a dotted underline that gives way to the flat fill (spec 3.4). A labelled
   link (.label) is not one: it keeps its own look, and as .interactive it answers the cursor with
   its own inset fill, above. A chapter's are in its paragraphs and descriptions; the reading column's are
   in those and in list items, table cells and quotations too, so a list of links or a table of them is marked
   as links as well. veil.js lists both (INK_TARGETS), where the cloth draws the wash. */
.chapter :is(p, dd) a:not(.interactive, .label),
.reading :is(p, li, td, th, blockquote, dd) a:not(.interactive, .label) {
```

2. Replace this exact line

```css
.chapter :is(p, dd) a:not(.interactive, .label):focus-visible {
```

with

```css
.chapter :is(p, dd) a:not(.interactive, .label):focus-visible,
.reading :is(p, li, td, th, blockquote, dd) a:not(.interactive, .label):focus-visible {
```

3. Replace this exact line (indented two spaces)

```css
  .chapter :is(p, dd) a:not(.interactive, .label):hover {
```

with

```css
  .chapter :is(p, dd) a:not(.interactive, .label):hover,
  .reading :is(p, li, td, th, blockquote, dd) a:not(.interactive, .label):hover {
```

4. Replace this exact line (indented two spaces)

```css
  .chapter :is(p, dd) a:not(.interactive, .label):active {
```

with

```css
  .chapter :is(p, dd) a:not(.interactive, .label):active,
  .reading :is(p, li, td, th, blockquote, dd) a:not(.interactive, .label):active {
```

5. Replace this exact line

```css
.chapter :is(p, dd) a:not(.interactive, .label).is-inked {
```

with

```css
.chapter :is(p, dd) a:not(.interactive, .label).is-inked,
.reading :is(p, li, td, th, blockquote, dd) a:not(.interactive, .label).is-inked {
```

Then replace this exact text

```css
.chapter pre code {
  font-size: .75em;
  line-height: 1.7;
}
```

with the same text followed by the reading column's rules:

```css
.chapter pre code {
  font-size: .75em;
  line-height: 1.7;
}

/* The reading column (spec 4.6): the Journal, Docs and the legal documents. One column of Newsreader at --measure,
   centred between the corners as a chapter is, but starting at the top, so a long page scrolls. Its words come from
   Markdown, so its elements are styled where they stand, and every element rule is written `.reading :where(...)`:
   that counts for one class and no element, so a component's own class (.pager__item, .entries__title) overrides it
   by source order. The component stylesheets are linked after this one (shell.html; a test holds them to it). */
.layout-reading .shell-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  justify-items: center;
  align-content: start;
}

/* Long words and addresses in running text wrap, anywhere, so the page never scrolls sideways; code blocks and tables
   do not wrap, they scroll inside their own boxes (below). min-width: 0 lets the column be narrower than its widest
   content. Newsreader has no face heavier than 500 (strong, below), so nothing here is drawn in a synthetic bold. */
.reading {
  width: 100%;
  min-width: 0;
  max-width: var(--measure);
  font-variant-numeric: lining-nums;
  font-synthesis-weight: none;
  overflow-wrap: anywhere;
}

/* The title is a chapter's title (.chapter__title): the display face, italic 300. */
.reading :where(h1) {
  margin: 0 0 var(--space-4);
  font-family: var(--font-display);
  font-style: italic;
  font-weight: 300;
  font-size: var(--title-size);
  line-height: 1.15;
  text-wrap: balance;
}

/* A heading is a chapter's heading (.chapter__heading); a smaller one is a chapter's label (.chapter__label): mono,
   uppercase, muted. scroll-margin-top leaves room above a heading that a section link jumps to (below 64rem the page
   also keeps clear of the top fade: scroll-padding-top, above). */
.reading :where(h2) {
  margin: var(--space-5) 0 var(--space-3);
  font: var(--font-heading);
  font-variant-numeric: inherit; /* the font shorthand resets it, and .reading sets lining-nums */
  scroll-margin-top: var(--space-4);
}

.reading :where(h3, h4, h5, h6) {
  margin: var(--space-4) 0 var(--space-2);
  font-family: var(--font-mono);
  font-size: var(--label-size);
  font-weight: 400;
  letter-spacing: var(--label-tracking);
  line-height: 1.4;
  text-transform: uppercase;
  color: var(--muted);
  scroll-margin-top: var(--space-4);
}

/* Not the visually hidden ones: a code block's status line is a paragraph, and the margin that clips it is what hides it. */
.reading :where(p:not(.visually-hidden), ul, ol, blockquote) {
  margin: 0 0 var(--space-3);
}

/* A line of mono above the title: JOURNAL, a date, a reading time. Two classes, so it wins over the paragraph rule above. */
.reading .reading__meta {
  margin: 0 0 var(--space-2);
  font-family: var(--font-mono);
  font-size: var(--label-size);
  letter-spacing: var(--label-tracking);
  line-height: 1.4;
  text-transform: uppercase;
  color: var(--muted);
}

.reading :where(strong, b) {
  font-weight: 500; /* the heaviest face Newsreader has */
}

.reading :where(em, i, cite) {
  font-style: italic;
}

.reading :where(ul, ol) {
  padding: 0 0 0 1.8em; /* room for the markers, which are the browser's */
}

.reading :where(li) {
  margin: 0 0 var(--space-1);
}

.reading :where(li > ul, li > ol) {
  margin: var(--space-1) 0 0;
}

.reading :where(blockquote) {
  padding: 0 0 0 var(--space-3);
  border-left: 2px solid var(--hair);
  font-style: italic;
}

.reading :where(blockquote em, blockquote i, blockquote cite) {
  font-style: normal;
}

.reading :where(hr) {
  margin: var(--space-5) 0;
  border: 0;
  border-top: 1px solid var(--hair);
}

.reading :where(code) {
  font-family: var(--font-mono);
  font-size: .8em;
}

/* A <pre> of its own, such as an indented block in the Markdown, is a chapter's (.chapter pre): its own box, scrolling
   sideways. A fenced block is components/code_block.html, below. */
.reading :where(pre) {
  margin: 0 0 var(--space-3);
  padding: var(--space-3);
  overflow-x: auto;
  overflow-wrap: normal;
  border: 1px solid var(--hair);
  background: var(--box);
}

.reading :where(pre code) {
  font-size: .75em;
  line-height: 1.7;
}

/* A fenced block is components/code_block.html, and code_block.css draws it: its box, its type, its Copy button in a row of its
   own. Every rule above that reaches it either weighs less than code_block.css's or weighs the same and comes earlier, so it
   loses; what is left is what the column hands down to its text, and that stops here. A quotation is italic and code is not;
   the column wraps long words anywhere, and Copy and the code keep their words whole. A test draws a block in the column and
   outside it and compares the two (pages.tests_reading). */
.reading :where(.code-block) {
  font-style: normal;
  overflow-wrap: normal;
}

/* A table scrolls sideways in its own box (.table-scroll, which render_markdown wraps every table in, with the
   focus a keyboard needs to scroll it). Its cells keep normal wrapping: `anywhere` would let auto layout squeeze every
   column to a letter, and the box would never need to scroll. */
.reading :where(.table-scroll) {
  margin: 0 0 var(--space-3);
  overflow-x: auto;
}

.reading :where(table) {
  width: 100%;
  border-collapse: collapse;
  font-size: .9em;
}

.reading :where(th, td) {
  padding: var(--space-2) var(--space-3) var(--space-2) 0;
  border-bottom: 1px solid var(--hair);
  text-align: left;
  vertical-align: top;
  overflow-wrap: normal;
}

.reading :where(th) {
  font-weight: 500;
  color: var(--muted);
}

/* The margin under the last line of an item or a quotation would only add to the one under the item or the quotation.
   After the rules above, so a bare code block or a rule that ends an item loses its margin too. (A fenced block keeps the
   one code_block.css gives it, which comes later; the margins that meet collapse into the larger.) */
.reading :where(li > :last-child, blockquote > :last-child) {
  margin-bottom: 0;
}

/* Images: base.css already caps them at the column's width (img, svg { max-width: 100% }); the height follows. */
.reading :where(img) {
  height: auto;
}

.reading > :where(img, figure) {
  margin: 0 0 var(--space-4);
}
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_reading`
Expected: `Ran 30 tests`, FAILED (failures=1): only `ReadingInkTests.test_the_cloth_inks_the_reading_columns_prose_links_as_it_inks_the_chapters`, with `static/js/veil.js: INK_TARGETS must list`. Every `ReadingCssTests` and `ReadingCodeBlockTests` test passes.

To see the code-block comparison bite, make two temporary edits in `base.css`: change `.reading :where(p:not(.visually-hidden), ul, ol, blockquote)` back to `.reading :where(p, ul, ol, blockquote)`, and empty the body of the `.reading :where(.code-block)` rule. Run `pages.tests_reading.ReadingCodeBlockTests`: it fails with a list that includes ``in a quotation, the pre, font-style: `font-style: italic` from `.reading :where(blockquote)` inside .reading, not set outside it``, ``in the column, the status line, margin-bottom, margin-left, margin-right, margin-top: `margin: 0 0 var(--space-3)` from `.reading :where(p, ul, ol, blockquote)` inside .reading, `margin: -1px` from `.visually-hidden` outside it`` and ``in the column, the Copy button, overflow-wrap: `overflow-wrap: anywhere` from `.reading` inside .reading, not set outside it``. Put both back and run it again: OK. (Chromium draws the same three differences: a block in a quotation italic, the status line's clipping margin replaced by a bottom margin, Copy's words free to wrap anywhere.)

- [ ] **Step 5: The cloth inks the reading column's links**

In `backend/static/js/veil.js`, replace this exact line

```js
const INK_TARGETS = ".interactive, .chapter :is(p, dd) a:not(.interactive, .label)";
```

with

```js
const INK_TARGETS = ".interactive, .chapter :is(p, dd) a:not(.interactive, .label), .reading :is(p, li, td, th, blockquote, dd) a:not(.interactive, .label)";
```

(`INK_TARGETS` is the selector for what the cloth paints an ink wash under on hover or keyboard focus. No other line of `veil.js` names `.chapter`, so nothing else changes. No existing test pins this line or the chapter prose-link selector; `ReadingInkTests` and `test_the_reading_columns_prose_links_are_styled_like_the_chapters` now do.)

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_reading pages.tests_veil pages.tests_shell pages.tests_static pages.tests_tokens pages.tests_chapters`
Expected: OK (`pages.tests_reading` runs 30 tests; `veil.js` was 11,789 B gzipped before this line and is about 23 B more, well inside its 15 KB budget; `shell.js` and the behaviours are untouched and stay at 10,218 of 10,240 B).

- [ ] **Step 6: The component recipe in AGENTS.md says what the catalog now does**

In `AGENTS.md`, in "Change or add a component", replace this exact text

```
4. Add an `Entry` for it to `backend/pages/catalog.py`, with sample inputs. If
   its markup has ids, give it an `id_prefix` input.
```

with

```
4. Add an `Entry` for it to `backend/pages/catalog.py`, with sample inputs. If
   its markup has ids, give it an `id_prefix` input. If it only ever sits in a
   layout's column (the reading column's `sections`, `pager` and `entries`), pass
   `within="reading"`: the catalog then shows it inside `<div class="reading">`,
   as the page does.
```

- [ ] **Step 7: Run the whole Django suite**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: OK (about 3.5 minutes; 30 more tests than before this task in `pages.tests_reading` and one more in `pages.tests_catalog`). No existing test needed an update: the only existing tests this task could trip are the two coverage tests that want a catalog entry and a linked stylesheet per component file (Steps 2 and 3 satisfy them), and nothing else in the suite pins `shell.html`'s stylesheet list beyond containing the ones it names, `INK_TARGETS`, or the chapter prose-link selector.

- [ ] **Step 8: Run the page checks in the browser**

This task changes `base.css`, which every page loads, and adds twelve frames to the end of the component catalog (after the code block's), which the checks open. In a fresh worktree, first make the database and the staff session as the Browser checks bullet of the Global Constraints says: without them the catalog answers 500 and the checks stop at once. Start the server and run the page checks (Global Constraints; on a laptop put `caffeinate -ims` in front of the command; this part took about 10 minutes at 7d94ea2, where the dry run at 3acafdc measured 9): `site-tests/.venv/bin/python site-tests/run_site_checks.py --base-url http://127.0.0.1:8777 --session-file site-tests/session.txt --out site-tests/artifacts --part pages`.
Expected: the last line starts `0 problem(s) across 7 page(s)` (the "note(s) to review by eye" are not problems). Open `site-tests/artifacts/component-catalog-1280-light-motion-on.png` and its Ink twin (`component-catalog-1280-dark-motion-on.png`): below the code block, `sections`, `pager` and `entries` each show four frames. Their links have a dotted underline; the entries' titles are in the display italic with the date in mono under them; nothing scrolls sideways at 375. The page is about 7,700 px tall at 1280 and 13,400 px at 375, and the harness cuts a screenshot at 8,000 px (`MAX_SHOT_HEIGHT`), so look at the 1280 pair: the 375 pictures end inside the section list's frames. A problem that names one of these components is this task's: fix the component's stylesheet, not the check. The catalog page is now taller, so expect its share of the run to grow somewhat.

- [ ] **Step 9: Commit**

```bash
git add backend/templates/layouts/reading.html backend/templates/components/sections.html backend/templates/components/pager.html backend/templates/components/entries.html backend/templates/shell.html \
  backend/static/css/components/sections.css backend/static/css/components/pager.css backend/static/css/components/entries.css backend/static/css/base.css backend/static/js/veil.js \
  backend/pages/catalog.py backend/pages/views.py backend/pages/tests_reading.py backend/pages/tests_catalog.py AGENTS.md
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -m "The reading layout: a Newsreader column for Markdown, with a section list, a pager, an entries list, and ink on its links"
```

---

---

### Task 4: Legal on the reading layout

The three legal documents (`terms`, `privacy`, `report-abuse`) move from their own standalone page onto the shell and the reading layout. The documents are the owner's (AGENTS.md invariant 8): nothing in `backend/legal/documents/` changes, and a test proves the words, the bold runs and the "Last updated" line are the same. The head comes from the registry, so `DOCUMENT_META` goes. The old-markup tests that pinned the legal pages move with them, and `templates/legal/` joins the directories the shell's template rules scan.

**Files:**
- Create: `backend/pages/tests_legal.py`
- Modify: `backend/legal/views.py`, `backend/templates/legal/document.html`
- Modify (tests): `backend/drop_core/tests.py`, `backend/drop_core/tests_menu.py`, `backend/drop_core/tests_ambient.py`, `backend/drop_core/tests_fix_web.py`, `backend/pages/tests_head.py`, `backend/pages/tests_site_rules.py`, `backend/pages/tests_content.py`
- Never touch: `backend/legal/documents/*`

**Interfaces:**
- Consumes:
  - Task 1: `render_reading(text) -> Reading` in `backend/blog/render.py`. `Reading.html` is the page's HTML with an `id` on every heading (`h1` to `h6`); `Reading.sections` is a tuple of `Section(id, title)`, one per `##` heading. It draws a fenced code block with a Copy button (`components/code_block.html`); the legal documents hold no code block, and a test here says so.
  - Task 2: `layout="reading"` on the registry pages `terms`, `privacy` and `report-abuse`, and the head `shell.html` builds from the registry (title, description, canonical, `og:type` website, Organization JSON-LD only, `data-layout`, `data-layout-paths`).
  - Task 3: `templates/layouts/reading.html` (body class `layout-reading`, a block named `reading`) and `templates/components/sections.html` (input `sections`; renders nothing for fewer than two; otherwise a `<nav>` labelled "Contents" with `#id` links).
- Produces:
  - `legal.views.split_before_sections(html) -> (intro_html, rest_html)`: everything before the first `<h2`, and the rest from that `<h2` on. With no `<h2` it returns `(html, "")`.
  - `legal.views.DOCUMENTS` and `DOCUMENTS_DIR` keep their shape (`name -> (filename, title)`); `drop_core/tests_fix_web.py` reads them.
  - `legal.views.DOCUMENT_META` is removed.
  - The context of `legal/document.html`: `intro_html`, `sections`, `body_html`.
  - `backend/pages/tests_legal.py`: 16 tests in four classes (on a base class that fetches the pages), every class setting `longMessage = False`, so that a failure prints the message that names the file and the fix and not the page it searched.
  - The directory tuple that `pages/tests_site_rules.py` (`shell_templates`) and `pages/tests_content.py` (`RealContentTests.test_every_shell_template`) scan becomes `("components", "layouts", "chapters", "pages", "legal")`. Task 5 appends `"docs"` and Task 6 appends `"blog"`.

Background for an engineer new to the repo. Each legal document is a Markdown file that starts with its own `# Heading`, which renders as the page's one `<h1>`. The reading layout must not add a second one. Terms and Privacy then have a "Last updated" line and numbered `##` sections. The section list goes immediately before the first section, so the page reads: the title, the "Last updated" line, the Contents list, then section 1. The sections are inside the rendered HTML, so the view splits the HTML at the first `<h2` and the template puts the list between the two halves. The text is untouched because the split only cuts the string in two and the halves are printed back to back. Report abuse has no `##` headings, so its split leaves everything in the first half, `sections` is empty and the component draws no list: that page reads as it always did. The reading column draws a fenced code block with a Copy button, a word the Markdown does not hold; the three documents hold no code block, so the word comparison below needs no allowance for it, and a test fails with the reason if one ever does.

- [ ] **Step 1: Write the failing tests**

Create `backend/pages/tests_legal.py`:

```python
"""The legal pages (spec 4.6): the owner's documents on the reading layout, their words untouched.

backend/legal/documents/* is the owner's (AGENTS.md invariant 8). The page may dress the documents but
never change them: these tests hold every word on the page to the Markdown, in order, and every bold
run to a <strong>.

Every class sets longMessage = False, so that a failure prints its own message, which names the file
and the fix, and not the page it searched (tens of kilobytes of HTML).
"""
import json
import re
from html import unescape

from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils.html import escape

from blog.render import render_markdown, render_reading
from legal import views
from pages import registry

DOCUMENTS = tuple(views.DOCUMENTS)  # terms, privacy, report-abuse
LD_JSON = re.compile(r'<script type="application/ld\+json" data-page-meta>(.*?)</script>', re.DOTALL)
NAV = re.compile(r"<nav\b.*?</nav>", re.DOTALL)


def source_file(name):
    filename, _title = views.DOCUMENTS[name]
    return views.DOCUMENTS_DIR / filename


def source_of(name):
    return source_file(name).read_text(encoding="utf-8")


def words(html):
    """What a reader reads in a piece of HTML: tags removed, entities decoded, white space made single spaces."""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def first_difference(shown, written):
    """Where two lists first part, a few items of each around the place, so that a failure can name it."""
    index = next((i for i, pair in enumerate(zip(shown, written)) if pair[0] != pair[1]),
                 min(len(shown), len(written)))

    def near(items):
        return items[max(index - 3, 0):index + 4]

    return (f"at item {index} (the page has {len(shown)} items, the document {len(written)}): "
            f"the page reads {near(shown)!r}, the document reads {near(written)!r}")


class LegalPageCase(TestCase):
    longMessage = False

    def page(self, name):
        response = self.client.get(f"/{name}/")
        self.assertEqual(response.status_code, 200,
                         f"/{name}/ answered {response.status_code}, not 200 (backend/legal/urls.py, backend/legal/views.py)")
        return response.content.decode()

    def article(self, name):
        found = re.findall(r"<article\b[^>]*>(.*?)</article>", self.page(name), re.DOTALL)
        self.assertEqual(len(found), 1, f"/{name}/ should hold its document in one <article> "
                         f"(backend/templates/legal/document.html), found {len(found)}")
        return found[0]


class LegalWordsTests(LegalPageCase):
    longMessage = False

    def test_the_page_says_what_the_document_says_and_nothing_else(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                shown = words(NAV.sub(" ", self.article(name))).split(" ")
                written = words(render_markdown(source_of(name))).split(" ")
                self.assertEqual(
                    shown, written,
                    f"The words on /{name}/ differ from {source_file(name).name} rendered the way it always was, "
                    f"{first_difference(shown, written)}. "
                    "The legal text is the owner's (AGENTS.md invariant 8): change the template, the view or the "
                    "stylesheet, never the document.")

    def test_every_bold_run_is_still_a_strong(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                written = re.findall(r"\*\*(.+?)\*\*", source_of(name))
                shown = [words(run) for run in re.findall(r"<strong>(.*?)</strong>", self.article(name), re.DOTALL)]
                self.assertEqual(
                    shown, written,
                    f"/{name}/: every **bold** run of {source_file(name).name} must stay a <strong> element, "
                    f"{first_difference(shown, written)}. "
                    "Its weight is the stylesheet's business (backend/static/css), not a reason to flatten it.")

    def test_the_last_updated_line_stays_a_plain_paragraph(self):
        for name in DOCUMENTS:
            for line in (line for line in source_of(name).split("\n") if line.startswith("Last updated:")):
                with self.subTest(document=name):
                    self.assertIn(f"<p>{line}</p>", self.article(name),
                                  f"/{name}/: the line '{line}' must stay a plain <p>, with no class and no wrapper")

    def test_no_document_holds_a_code_block(self):
        # The comparison above is made against render_markdown, which draws no Copy button. The reading column
        # draws every code block with one (components/code_block.html), and its word is not the document's.
        for name in DOCUMENTS:
            with self.subTest(document=name):
                self.assertNotIn(
                    "<pre", render_markdown(source_of(name)),
                    f"{source_file(name).name} now holds a code block. The reading column draws a code block with a Copy "
                    "button (backend/templates/components/code_block.html), a word the document does not hold, so the "
                    "word comparison in this file would count it. If the owner meant the code block, make words() in "
                    "this file leave the button out; do not edit the document.")


class LegalReadingLayoutTests(LegalPageCase):
    longMessage = False

    def test_every_document_has_a_registry_page_in_the_reading_layout(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                self.assertEqual(registry.page(name).layout, "reading",
                                 f'backend/pages/registry.py: the Page named "{name}" needs layout="reading"')

    def test_each_document_is_drawn_by_the_reading_layout(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                response = self.client.get(f"/{name}/")
                self.assertIn("layouts/reading.html", [template.name for template in response.templates],
                              "backend/templates/legal/document.html must extend layouts/reading.html")
                html = response.content.decode()
                self.assertRegex(html, r'<body class="[^"]*\blayout-reading\b',
                                 f"/{name}/: <body> needs the class layout-reading (backend/templates/layouts/reading.html)")
                self.assertIn('data-layout="reading"', html,
                              f'/{name}/: <body> needs data-layout="reading" (the Page in backend/pages/registry.py '
                              'has layout="reading" and backend/templates/shell.html writes it)')
                found = re.search(r'data-layout-paths="([^"]*)"', html)
                self.assertIsNotNone(found, f"/{name}/: <body> carries no data-layout-paths")
                for path in ("/terms/", "/privacy/", "/report-abuse/"):
                    self.assertIn(path, found.group(1).split(),
                                  f"/{name}/: in-place changes must reach {path} (layout_paths in backend/pages/registry.py)")

    def test_the_head_comes_from_the_registry_and_says_website(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                page, html = registry.page(name), self.page(name)
                self.assertIn(f"<title>{escape(page.title)}</title>", html,
                              f"/{name}/: the <title> must be the Page's title in backend/pages/registry.py")
                self.assertIn(f'<link rel="canonical" href="{registry.canonical_url(name)}" data-page-meta>', html,
                              f"/{name}/: the head must come from shell.html, which reads the Page in backend/pages/registry.py")
                self.assertIn('<meta property="og:type" content="website" data-page-meta>', html,
                              f"/{name}/ is not an article: og:type is website (backend/templates/shell.html)")
                kinds = [json.loads(block)["@type"] for block in LD_JSON.findall(html)]
                self.assertEqual(kinds, ["Organization"], f"/{name}/ is not an article: it carries Organization data only")

    def test_landmarks_survive_the_move(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                html = self.page(name)
                self.assertIn('<a class="skip-link label" href="#main">', html,
                              f"/{name}/ lost the skip link: backend/templates/legal/document.html must extend layouts/reading.html")
                self.assertIn('<main id="main" class="shell-main">', html,
                              f"/{name}/ lost its <main id=\"main\">: backend/templates/legal/document.html must extend layouts/reading.html")
                main = html[html.index('<main id="main"'):html.index("</main>")]
                self.assertEqual(len(re.findall(r"<h1[ >]", html)), 1,
                                 f"/{name}/ should have exactly one h1, the document's own # heading")
                self.assertEqual(len(re.findall(r"<h1[ >]", main)), 1, f"/{name}/: the h1 belongs inside <main>")
                for element in ("<header", "<footer"):
                    self.assertNotIn(element, main, f"/{name}/: no {element}> inside <main>; "
                                     "backend/static/css/base.css styles the bare element, so use a div")

    def test_the_report_abuse_address_is_still_a_mailto_link(self):
        self.assertIn('<a href="mailto:abuse@veilfile.com">abuse@veilfile.com</a>', self.article("report-abuse"),
                      "/report-abuse/: the address must stay a mailto link, as report-abuse.md writes it")


class LegalSectionListTests(LegalPageCase):
    longMessage = False

    def test_every_document_opens_with_its_own_heading(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                self.assertTrue(self.article(name).lstrip().startswith("<h1"),
                                f"/{name}/: the document's own # heading must come first in the <article>")

    def test_a_long_document_lists_its_sections_right_before_the_first_of_them(self):
        for name in ("terms", "privacy"):
            with self.subTest(document=name):
                article = self.article(name)
                navs = NAV.findall(article)
                self.assertEqual(len(navs), 1, f"/{name}/ should show one section list inside its <article>")
                self.assertIn("Contents", navs[0], f"/{name}/: the section list is labelled Contents "
                              "(backend/templates/components/sections.html)")
                nav = NAV.search(article)
                before, after = article[:nav.start()], article[nav.end():]
                self.assertRegex(before, r"</h1>\s*<p>Last updated: [^<]*</p>\s*$",
                                 f"/{name}/ should read: the h1 title, the 'Last updated' paragraph, the section list, then "
                                 "the first section (split_before_sections in backend/legal/views.py)")
                self.assertNotIn("<h2", before, f"/{name}/: the section list goes before the first section")
                self.assertTrue(after.lstrip().startswith("<h2"),
                                f"/{name}/: the first h2 must follow the section list directly "
                                "(backend/templates/legal/document.html)")
                sections = NAV.sub(" ", article)
                ids = re.findall(r'<h2\b[^>]*\bid="([^"]+)"', sections)
                self.assertEqual(len(ids), sections.count("<h2"),
                                 f"/{name}/: every ## heading needs an id (render_reading in backend/blog/render.py)")
                self.assertEqual(len(set(ids)), len(ids), f"/{name}/: two headings share an id")
                self.assertEqual(re.findall(r'href="#([^"]+)"', navs[0]), ids,
                                 f"/{name}/: the section list must link to every ## heading, in order "
                                 "(backend/templates/components/sections.html)")
                titles = [words(title) for title in re.findall(r"<h2\b[^>]*>(.*?)</h2>", sections, re.DOTALL)]
                listed = [words(title) for title in re.findall(r'<a\b[^>]*href="#[^"]*"[^>]*>(.*?)</a>', navs[0], re.DOTALL)]
                self.assertEqual(listed, titles, f"/{name}/: the section list must name the sections as the document does")

    def test_report_abuse_has_no_section_list(self):
        self.assertNotIn("<nav", self.article("report-abuse"),
                         "/report-abuse/ has no ## headings, so it shows no section list "
                         "(backend/templates/components/sections.html shows nothing for fewer than two)")


class LegalViewTests(SimpleTestCase):
    longMessage = False

    def test_the_registry_is_the_only_source_of_a_legal_pages_head(self):
        self.assertFalse(hasattr(views, "DOCUMENT_META"),
                         "backend/legal/views.py: a legal page's title, description and address come from its Page in "
                         "backend/pages/registry.py; delete DOCUMENT_META")

    def test_an_unknown_document_is_a_404(self):
        with self.assertRaises(Http404):
            views.legal_document(RequestFactory().get("/nope/"), "nope")

    def test_the_document_is_split_before_its_first_section_without_losing_a_character(self):
        for name in DOCUMENTS:
            with self.subTest(document=name):
                html = render_reading(source_of(name)).html
                intro, rest = views.split_before_sections(html)
                self.assertEqual(intro + rest, html,
                                 f"{name}: split_before_sections in backend/legal/views.py must cut the page in two "
                                 "without losing or adding a character")
                self.assertTrue(intro.startswith("<h1"),
                                f"{name}: the opening must start with the document's h1, it starts {intro[:40]!r}")
                self.assertNotIn("<h2", intro, f"{name}: the opening ends before the first <h2")
                self.assertTrue(rest == "" or rest.startswith("<h2"),
                                f"{name}: the rest must start at the first <h2, it starts {rest[:40]!r}")

    def test_a_document_without_sections_is_all_intro(self):
        self.assertEqual(views.split_before_sections("<p>Words.</p>"), ("<p>Words.</p>", ""),
                         "backend/legal/views.py: with no <h2 the whole page is the opening and the rest is empty")
        html = render_reading(source_of("report-abuse")).html
        self.assertEqual(views.split_before_sections(html), (html, ""),
                         "backend/legal/views.py: report-abuse.md has no ## heading, so all of it is the opening")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_legal -v 2 > /tmp/tests_legal.out 2>&1; grep -E '^(FAIL|ERROR): |^Ran |^FAILED|^OK' /tmp/tests_legal.out; cd ..` (every class sets `longMessage = False`, so each failure prints only its own message, and the whole output is about 20 KB; the `grep` lists one line per failure, and the file holds the message of each.)

Expected: `Ran 16 tests` and `FAILED (failures=12, errors=4)`, which are 7 of the 16 test methods (the counts are per document, because the loops use subtests). Each failure prints the message below and nothing before it:
- ERROR, `AttributeError: module 'legal.views' has no attribute 'split_before_sections'`: `test_a_document_without_sections_is_all_intro`, and `test_the_document_is_split_before_its_first_section_without_losing_a_character` for each of the three documents.
- FAIL, `backend/templates/legal/document.html must extend layouts/reading.html`: `test_each_document_is_drawn_by_the_reading_layout`, three times.
- FAIL, `/terms/ lost the skip link: backend/templates/legal/document.html must extend layouts/reading.html`: `test_landmarks_survive_the_move`, three times (the name changes with the document).
- FAIL, `/terms/: the head must come from shell.html, which reads the Page in backend/pages/registry.py`: `test_the_head_comes_from_the_registry_and_says_website`, three times.
- FAIL, `/terms/ should show one section list inside its <article>` (and `/privacy/`): `test_a_long_document_lists_its_sections_right_before_the_first_of_them`.
- FAIL, `backend/legal/views.py: a legal page's title, description and address come from its Page in backend/pages/registry.py; delete DOCUMENT_META`.

The other 9 tests pass already and must keep passing: the four words tests (the page's words, the bold runs, the "Last updated" line, and the guard that no document holds a code block; the old page said the same words), the registry-layout test (Task 2), the mailto test, the "opens with its own heading" test, the report-abuse "no section list" test and the 404 test.

- [ ] **Step 2: Render the legal documents through `render_reading`**

Replace the whole of `backend/legal/views.py` with:

```python
"""Legal views: terms of service, privacy policy, and abuse reporting pages.

The documents are the owner's (AGENTS.md invariant 8): this module renders them and never edits them.
A page's title, description and address come from its Page in pages/registry.py, not from here.
"""

from pathlib import Path

from django.http import Http404
from django.shortcuts import render

from blog.render import render_reading

DOCUMENTS_DIR = Path(__file__).resolve().parent / "documents"

DOCUMENTS = {
    "terms": ("terms-of-service.md", "Terms of Service"),
    "privacy": ("privacy-policy.md", "Privacy Policy"),
    "report-abuse": ("report-abuse.md", "Report abuse"),
}


def split_before_sections(html):
    """The page's opening and the rest, so the section list can sit between them.

    Every document opens with its # heading, which renders as the page's one h1, and Terms and
    Privacy follow it with a "Last updated" line. The rest starts at the first h2. A document with
    no h2 comes back whole as the opening, and the section list, which shows nothing for fewer
    than two sections, then draws nothing.
    """
    intro, found, rest = html.partition("<h2")
    return intro, found + rest


def legal_document(request, name):
    try:
        filename, _title = DOCUMENTS[name]
    except KeyError:
        raise Http404("No such document.")
    reading = render_reading((DOCUMENTS_DIR / filename).read_text(encoding="utf-8"))
    intro_html, body_html = split_before_sections(reading.html)
    return render(
        request,
        "legal/document.html",
        {"intro_html": intro_html, "sections": reading.sections, "body_html": body_html},
    )


def terms(request):
    return legal_document(request, "terms")


def privacy(request):
    return legal_document(request, "privacy")


def report_abuse(request):
    return legal_document(request, "report-abuse")
```

(`DOCUMENT_META` is gone: its titles, descriptions and addresses were copies of the three `Page` entries in `backend/pages/registry.py`, which `shell.html` now reads. `DOCUMENTS` keeps its shape because `drop_core/tests_fix_web.py` unpacks `(filename, title)` from it. `DOCUMENT_META` was used nowhere but this file; Step 9 greps for it.)

- [ ] **Step 3: Put the page on the reading layout**

Replace the whole of `backend/templates/legal/document.html` (61 lines of standalone page with its own `<style>`, wordmark and three-link footer) with:

```django
{% extends "layouts/reading.html" %}
{# Layout: reading. A legal document: the owner's Markdown, rendered by legal/views.py and never edited here (AGENTS.md invariant 8). #}
{# The document's own first-level heading is the page's h1. The section list sits right before the first section, after the title and the "Last updated" line, and shows nothing for fewer than two sections. #}
{% block reading %}
<article>{{ intro_html|safe }}
{% include "components/sections.html" with sections=sections only %}
{{ body_html|safe }}</article>
{% endblock %}
```

(Each `{# #}` comment is on one line: Django 6.1 shows a multi-line comment as visible text. The old page's `<title>`, description, canonical, `og:*` tags, skip link, menu, wordmark and footer are now `shell.html`'s, from the registry. `|safe` is the same trust the old page placed in `render_markdown`; `ContentGuardTests` is the guard.)

- [ ] **Step 4: Run the new tests and watch them pass**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_legal -v 2`
Expected: `Ran 16 tests` and `OK`.

- [ ] **Step 5: Bring `templates/legal/` under the shell's template rules**

`pages.tests_site_rules.shell_templates()` (the rules: no inline `style`, components included with `only`) and `pages.tests_content.RealContentTests.test_every_shell_template` (every character is in the shipped fonts) scan `shell.html` and four template folders. `templates/legal/` is not one of them, so `legal/document.html` escapes all three rules. Add the folder to the tuple in both files. On main, in `shell_templates` in `backend/pages/tests_site_rules.py` and in `test_every_shell_template` in `backend/pages/tests_content.py`, the tuple reads:

```python
("components", "layouts", "chapters", "pages")
```

In both files replace it with:

```python
("components", "layouts", "chapters", "pages", "legal")
```

(Task 5 adds `"docs"` after `"legal"` and Task 6 adds `"blog"` after that, each to this same tuple in these same two files.)

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules pages.tests_content`
Expected: `OK`. The new `legal/document.html` already follows the rules (no `style=`, its one component include ends with `only`, all its characters are ASCII), so the new tuple simply brings the folder under them and the run stays green. A later edit that breaks a rule fails with a message naming the file and the fix.

- [ ] **Step 6: Run the old-markup tests and watch them break**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests.LegalTests drop_core.tests.AccessibilityTests drop_core.tests_menu drop_core.tests_ambient pages.tests_head`
Expected: `FAILED`, with failures or errors in exactly these 25 test methods and no others (`LegalTests` all pass):
- `drop_core.tests.AccessibilityTests`: `test_skip_link_and_main_id_on_django_pages` (the three legal pages: the shell's skip link is `class="skip-link label"` and its main is `<main id="main" class="shell-main">`), `test_decorative_wordmark_cursor_hidden` (`/terms/`: the shell has a lockup, no `.cursor` wordmark).
- `drop_core.tests_menu.SharedMenuTests`: `test_menu_present_on_every_page`, `test_menu_link_order`, `test_menu_sits_after_skip_link` (each for the three legal pages), `test_menu_keyboard_behavior`, `test_menu_uses_menuitem_roles` (both read `/terms/` as "an old page").
- `drop_core.tests_menu.TrimmedFooterTests`: `test_footer_keeps_compliance_minimum`, `test_footer_is_one_quiet_line` (the shell's footer holds the two switches and no links).
- `drop_core.tests_ambient` (all read `/terms/` as "an old page", or list it in `FIELD_PAGES`): `test_boot_script_runs_before_any_style_or_meta`, `test_ambient_layer_present_on_every_page`, `test_django_pages_all_use_field_intensity`, `test_veil_present_only_on_text_heavy_pages`, `test_ambient_is_first_in_body`, `test_light_theme_tokens_defined`, `test_dark_tokens_unchanged`, `test_blob_alpha_ceiling_capped`, `test_transform_only_keyframes`, `test_print_hides_ambient`, `test_motion_off_stills_cursor`, `test_no_literal_hex_for_themed_surfaces`, `test_control_cluster_on_every_page`, `test_theme_segmented_group`, `test_motion_toggle_reports_state`.
- `pages.tests_head.ShellPageIsLazyTests.test_a_registry_page_that_never_reads_it_renders_without_the_link_preview_image`: `ValueError: Missing staticfiles manifest entry for 'img/og.png'`, because `/terms/` now reads the link-preview image.

- [ ] **Step 7: Move the old-markup tests to the new markup**

The old pages that remain on the old includes (`theme_boot`, `ambient`, `menu`) until PR 5 are the sign-in and sign-up pages, so `/accounts/login/` is the new stand-in for "an old page" (PR 2 used `/terms/` for the same job). It carries everything those tests assert: the `veilfile-theme-boot` script, the ambient layer and its tokens, `@media print`, the `.cursor` rule, the control cluster, the menu with its ten `role="menuitem"` links and the `"Escape"` handler.

`backend/drop_core/tests.py`:

1. In `AccessibilityTests.test_skip_link_and_main_id_on_django_pages`, replace

```
            "/docs/quickstart/",
            "/terms/",
            "/privacy/",
            "/report-abuse/",
            "/accounts/signup/",
```

with

```
            "/docs/quickstart/",
            "/accounts/signup/",
```

(The legal pages' skip link and `<main id="main">` are held by `pages.tests_legal.LegalReadingLayoutTests.test_landmarks_survive_the_move`, in the shell's markup.)

2. In `AccessibilityTests.test_decorative_wordmark_cursor_hidden`, replace `for url in ("/docs/quickstart/", "/terms/"):` with `for url in ("/docs/quickstart/",):`. (The shell's lockup is a mark with its name for screen readers; there is no wordmark cursor to hide on the legal pages. The Docs page leaves this loop in Task 5.)
3. In `LegalTests`, replace the whole method

```python
    def test_report_abuse_linked_from_legal_footer(self):
        for url in ("/terms/", "/privacy/", "/report-abuse/"):
            response = self.client.get(url)
            self.assertContains(response, "/report-abuse/")
```

with

```python
    def test_report_abuse_is_linked_from_every_legal_page(self):
        # The legal pages have no footer links: the menu's third group carries Terms, Privacy and
        # Report abuse (spec 4.4), so each document is one step from the abuse address.
        for url in ("/terms/", "/privacy/", "/report-abuse/"):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                menu = html[html.index('<nav class="menu"'):]
                menu = menu[:menu.index("</nav>")]
                self.assertIn('href="/report-abuse/"', menu)
```

(The old assertion looked for the string anywhere in the page, which the page's own canonical address satisfies on `/report-abuse/` itself; the new one looks in the menu. `test_report_abuse_email_is_a_link` in `AccessibilityTests` keeps passing unchanged: the mailto link is rendered as before.)

`backend/drop_core/tests_menu.py`:

1. In `MENU_PAGES`, replace

```
    "/terms/",
    "/privacy/",
    "/report-abuse/",
    "/accounts/login/",
```

with

```
    "/accounts/login/",
```

2. In `FOOTER_PAGES`, replace

```
    "/blog/",
    "/terms/",
    "/privacy/",
    "/report-abuse/",
]
```

with

```
    "/blog/",
]
```

3. Replace both `self.client.get("/terms/")` calls (in `SharedMenuTests.test_menu_keyboard_behavior` and `test_menu_uses_menuitem_roles`) with `self.client.get("/accounts/login/")`.

(The old compliance footer, "terms · privacy · report abuse" under every content page, is gone from the legal pages: the shell's footer holds only the two switches, and the menu's third group carries those three links, spec 4.4. `TrimmedFooterTests` keeps its other subjects, Docs and the Journal, until Tasks 5 and 6 move them.)

`backend/drop_core/tests_ambient.py`:

1. In `FIELD_PAGES`, delete the line `    ("/terms/", True),`.
2. Replace every `self.client.get("/terms/")` with `self.client.get("/accounts/login/")` (12 places, in `AmbientBootScriptTests`, `AmbientFieldTests`, `AmbientStylesTests` and `AmbientControlsTests`). Do the two that pass today as well (`test_boot_script_reads_theme_and_motion_keys`, `test_controls_script_writes_same_keys`): the shell's own boot script happens to contain the same strings, but these tests are about the old include.

`backend/pages/tests_head.py`: in `ShellPageIsLazyTests.test_a_registry_page_that_never_reads_it_renders_without_the_link_preview_image`, replace `            for path in ("/terms/", "/docs/"):` with `            for path in ("/docs/",):`. (`/terms/` now extends `shell.html` and reads the image, so it cannot be a subject of this test; Task 5 retires the test with `/docs/`.)

`backend/drop_core/tests_fix_web.py`, in `ContentGuardTests._rendered_content`, so the guard reads what the legal pages now serve:

1. Replace

```python
        from blog.render import render_markdown
        from docs import loader as docs_loader
        from legal.views import DOCUMENTS, DOCUMENTS_DIR
```

with (these three lines are unique in the file; `from blog.render import render_markdown` alone is not, because `RenderReadingTests` has it too, and a second import line would leave two `from blog.render import` lines in one method)

```python
        from blog.render import render_markdown, render_reading
        from docs import loader as docs_loader
        from legal.views import DOCUMENTS, DOCUMENTS_DIR
```

2. Replace `            yield f"legal/{filename}", render_markdown(body)` with `            yield f"legal/{filename}", render_reading(body).html`.

`backend/pages/context_processors.py`: run `grep -n "legal pages" backend/pages/context_processors.py`. Expected: no output. The sentence that named Docs and the legal pages among the pages that never extend `shell.html` was in the docstring of `shell_page`, and Task 2 replaced that whole file. A line printed means Task 2 was not applied as written: stop and fix that first.

- [ ] **Step 8: Run those tests and the pages tests**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages drop_core.tests.LegalTests drop_core.tests.AccessibilityTests drop_core.tests_menu drop_core.tests_ambient drop_core.tests_fix_web.ContentGuardTests -v 1`
Expected: `OK`. Among the `pages` tests, `RegistryMatchesRenderedPagesTests` now checks the legal heads as the shell renders them, and `InternalLinksTests` fetches all three legal pages (the section list's `#id` links are fragments and are skipped).

- [ ] **Step 9: Run the whole suite, and check the owner's files and `DOCUMENT_META`**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: `OK` (about 4 minutes).

Run: `git diff --quiet HEAD -- backend/legal/documents && echo untouched`
Expected: `untouched`.

Run: `grep -rIn "DOCUMENT_META" backend site-tests .github AGENTS.md README.md`
Expected: only the two lines of `test_the_registry_is_the_only_source_of_a_legal_pages_head` in `backend/pages/tests_legal.py` (the `hasattr` call and its message).

- [ ] **Step 10: Commit**

```bash
git add backend/legal/views.py backend/templates/legal/document.html backend/pages/tests_legal.py \
  backend/pages/tests_site_rules.py backend/pages/tests_content.py \
  backend/drop_core/tests.py backend/drop_core/tests_menu.py backend/drop_core/tests_ambient.py \
  backend/drop_core/tests_fix_web.py backend/pages/tests_head.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -m "Legal documents on the reading layout: the title, the date line, a section list and the owner's words unchanged, proven by a test"
```

---

### Task 5: Docs on the reading layout

**Files:**
- Create: `backend/templates/docs/index.html`, `backend/pages/tests_docs_reading.py`
- Modify: `backend/docs/loader.py`, `backend/docs/views.py`, `backend/templates/docs/page.html` (rewritten whole), `backend/docs/pages/quickstart.md` (its two raw HTML blocks only; no word of prose changes), `backend/static/css/base.css`, `backend/pages/content_checks.py` (the comment and the reason text only)
- Modify (tests): `backend/pages/tests_site_rules.py`, `backend/pages/tests_content.py`, `backend/drop_core/tests.py`, `backend/drop_core/tests_site_pages.py`, `backend/drop_core/tests_menu.py`, `backend/drop_core/tests_ambient.py`, `backend/drop_core/tests_fix_web.py`, `backend/pages/tests_head.py`

**Interfaces:**
- Consumes:
  - Task 1: `blog.render.render_reading(text) -> Reading`, with `Reading.html` and `Reading.sections` (a tuple of `Section`, each with `id` and `title`, one per `##` heading). Its `html` also makes every fenced code block a `components/code_block.html` (on main since PR #15): a `div.code-block[data-code-block]` holding a Copy button whose `data-copy` is the id of the block's `<pre>`, the `<pre>`, and a status line, with an id unique on the page. So a Docs page gets Copy on each of its fences without a line of its own: the views only call `render_reading`. `render_markdown` is unchanged and is not used by Docs.
  - Task 2: the `docs-index` and `docs-page` URL names carry a `shell_page` entry in the `reading` layout, so `<body>` has `data-layout="reading"` and `data-layout-paths` lists every Docs page. The entry for slug `index` (`/docs/index/` shows the Docs index) has the canonical address `/docs/`.
  - Task 3: `templates/layouts/reading.html` (block `reading` inside `<div class="reading">`), the class `reading__meta` (a mono meta line above an `h1`; a link in it is a prose link), and the components `components/sections.html` (input `sections`), `components/pager.html` (inputs `previous`, `next`, each `None` or an object with `url` and `title`) and `components/entries.html` (input `entries`, objects with `url`, `title`, `description` and an optional `date`).
  - Task 4: the folders that `pages/tests_site_rules.py` (`shell_templates`) and `pages/tests_content.py` (`RealContentTests.test_every_shell_template`) scan are `("components", "layouts", "chapters", "pages", "legal")`.
  - `shell.html`, which already loads `js/behaviors/tabs.js` and `copy.js` as `<script type="module">` in the head of every shell page, and `css/components/code_block.css` (PR #15). Both behaviours listen on `document`, so they work for markup swapped in by an in-place change. This task changes no JavaScript (the budget test stays where it is).
- Produces:
  - `docs.loader.neighbours(slug) -> tuple[Page | None, Page | None]`: the pages before and after `slug` in `(order, slug)` order; `(None, None)` for an unknown slug.
  - `docs.loader.Page.url`: `/docs/` for the index, else `/docs/<slug>/` (what `entries` and the pager link to).
  - `templates/docs/index.html` (the page's own words, then every other page through `entries`) and `templates/docs/page.html` (a meta line above the title that links back to the Docs index, the title, the section list, the words, then the pager). The old flat Docs nav is gone, so that meta line is a Docs page's way back; the index, being `/docs/`, has none.
  - The scan lists become `("components", "layouts", "chapters", "pages", "legal", "docs")`: `templates/docs/` is under the shell's template rules.
  - Copy on every fenced code block of every Docs page (the owner's request, 2026-10-16), held by `pages/tests_docs_reading.py`: each fence is one code block, its Copy button names the id of its `<pre>`, and the text in that `<pre>` is the fence's text exactly. The quickstart's raw-HTML commands are not fences and keep their own tabs and Copy.
  - The quickstart's markup contract, which `pages/tests_docs_reading.py` holds: a `data-tabs` group around the tablist and its panels (ids `shelltab-<shell>` and `shellpanel-<shell>`); every command in a `<code id="shellcmd-<name>" tabindex="0">`, beside its prompt and not inside it; every Copy button has `data-copy="<that id>"` and an `aria-label` starting "Copy "; the nearest `data-tabs` or `data-code-block` ancestor of every Copy button (where `copy.js` looks for its status line) is a `data-tabs` group, which holds one `<span role="status" data-copy-status>`.
  - CSS in `base.css`: `.command-group`, `.command-group__tabs`, `.command`, `.command__for`, `.command__shell`, `.command__line`, `.command__prompt`, `.command__copy`.
  - Every class in `pages/tests_docs_reading.py` sets `longMessage = False` (see Step 2).

Three facts the steps rely on (all checked in the code):
1. **The shared behaviours are already loaded when Docs is reached in place.** `/docs/` to `/docs/quickstart/` replaces only `<main>`'s children and the `[data-page-meta]` head elements (`changeTo` in `shell.js`); `tabs.js` and `copy.js` are loaded by `shell.html` on every page and delegate from `document`, so nothing needs re-checking on `veilfile:swapped`. A script inside `<main>` would not run after such a change, which is why the old inline script goes and a test keeps scripts out of Docs.
2. **`tabs.js` does nothing before a click.** It never hides a panel itself: the non-first panels carry `hidden` in the markup. Without JavaScript only the bash command would show, with five tabs that do nothing. Step 6 adds the rules that show every command (each under a line naming its shell) and hide the dead controls when `<html>` has no `js` class.
3. **`copy.js` finds a Copy button's status line with `button.closest("[data-tabs], [data-code-block]")`** (since PR #15), the nearest of the two. A fence's Copy sits in its `data-code-block`; the quickstart's six raw-HTML Copy buttons sit in a `data-tabs` group (the upload command's group has no tabs and carries `data-tabs` only for this) and in no code block. Each speaks in its own status line, and `QuickstartSwitcherTests` and `DocsPageTests` hold it.

- [ ] **Step 1: Check what this task builds on**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python -c "import django, os; os.environ['DJANGO_SETTINGS_MODULE']='config.settings'; django.setup(); from blog.render import render_reading, Section, Reading; assert 'data-code-block' in render_reading('~~~\nx\n~~~\n').html; print('render_reading ok')" && ls templates/layouts/reading.html templates/components/sections.html templates/components/pager.html templates/components/entries.html templates/components/code_block.html`
Expected: `render_reading ok`, then the five paths listed with no error. If anything is missing, stop: Tasks 1 to 3 have not landed (or Task 1 has not yet made fenced blocks into code blocks, as it must).

- [ ] **Step 2: Write the failing tests**

Create `backend/pages/tests_docs_reading.py`. Every class sets `longMessage = False`, as every new test class in this PR does: a failed `assertIn` on a whole page would otherwise print the page, up to 160 KB of HTML, before the message that names the file and the fix. With it off only the message prints, so each message below also says what it found.

```python
"""Docs on the reading layout (spec 4.6): each page but the index has a link back to the Docs index above its title, each
page opens with its sections and ends with links to the page before and the page after it, the index lists the pages, every
fenced code block has a Copy button (render_reading, Task 1), the quickstart's switcher and its commands' Copy buttons run on
the shell's shared behaviours, and no Docs page carries a script of its own (a script in <main> does not run after an
in-place change).

Every class sets longMessage = False. A failed assertIn on a whole page would otherwise print the page, up to 160 KB of HTML,
before the message that names the file and the fix; with it off only the message prints, so each message here also says what
was found."""
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.utils.html import escape

from docs import loader
from pages import content_checks, registry
from pages.fonts import FontRanges

BACKEND = Path(settings.BASE_DIR)
REPO = BACKEND.parent
QUICKSTART = "backend/docs/pages/quickstart.md"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _Tree(HTMLParser):
    """Every element, in document order, with its attributes, its ancestors and all the text inside it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.open, self.elements = [], []

    def handle_starttag(self, tag, attrs):
        element = {"tag": tag, "attrs": dict(attrs), "parents": tuple(self.open), "text": ""}
        self.elements.append(element)
        if tag not in VOID:
            self.open.append(element)

    def handle_startendtag(self, tag, attrs):
        self.elements.append({"tag": tag, "attrs": dict(attrs), "parents": tuple(self.open), "text": ""})

    def handle_endtag(self, tag):
        for index in range(len(self.open) - 1, -1, -1):
            if self.open[index]["tag"] == tag:
                del self.open[index:]
                break

    def handle_data(self, data):
        for element in self.open:
            element["text"] += data


def elements(html):
    tree = _Tree()
    tree.feed(html)
    tree.close()
    return tree.elements


def classes(element):
    return element["attrs"].get("class", "").split()


def inside(element, ancestor):
    """True when `element` is nested anywhere in `ancestor` (that very element, not an equal one)."""
    return any(parent is ancestor for parent in element["parents"])


def scope_of(button):
    """Where copy.js looks for a Copy button's status line: the button's nearest ancestor with data-tabs or data-code-block."""
    for parent in reversed(button["parents"]):
        if "data-tabs" in parent["attrs"] or "data-code-block" in parent["attrs"]:
            return parent
    return None


def main_of(html):
    return html[html.index("<main"):html.index("</main>")]


class NeighboursTests(SimpleTestCase):
    longMessage = False

    def write_pages(self, *pages):
        """Docs pages (slug, order) in a temporary folder that docs.loader reads from for this test."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for slug, order in pages:
            (Path(folder.name) / f"{slug}.md").write_text(
                f"---\ntitle: {slug.title()}\nslug: {slug}\norder: {order}\n---\n\nBody.\n", encoding="utf-8")
        patch = mock.patch.object(loader, "PAGES_DIR", Path(folder.name))
        patch.start()
        self.addCleanup(patch.stop)

    def around(self, slug):
        previous, following = loader.neighbours(slug)
        return (previous and previous.slug, following and following.slug)

    def test_pages_are_neighbours_by_order_not_by_name(self):
        self.write_pages(("zeta", 1), ("alpha", 2), ("mid", 3))
        found = self.around("alpha")
        self.assertEqual(found, ("zeta", "mid"), f"backend/docs/loader.py: neighbours() follows `order`: alpha sits between zeta and mid, found {found}")

    def test_the_first_page_has_no_previous_and_the_last_has_no_next(self):
        self.write_pages(("zeta", 1), ("alpha", 2), ("mid", 3))
        self.assertEqual(self.around("zeta"), (None, "alpha"))
        self.assertEqual(self.around("mid"), ("alpha", None))

    def test_pages_of_equal_order_follow_the_slug(self):
        self.write_pages(("mid", 3), ("tie-b", 5), ("tie-a", 5))
        self.assertEqual(self.around("tie-a"), ("mid", "tie-b"))
        self.assertEqual(self.around("tie-b"), ("tie-a", None))

    def test_a_lone_page_and_an_unknown_slug_have_no_neighbours(self):
        self.write_pages(("solo", 1))
        self.assertEqual(self.around("solo"), (None, None))
        self.assertEqual(self.around("nope"), (None, None))

    def test_the_real_pages_chain_from_the_first_to_the_last(self):
        pages = loader.load_pages()
        walked = [pages[0]]
        for _ in pages:
            following = loader.neighbours(walked[-1].slug)[1]
            if following:
                walked.append(following)
        self.assertEqual([page.slug for page in walked], [page.slug for page in pages])
        self.assertIsNone(loader.neighbours(pages[0].slug)[0])

    def test_a_page_knows_its_address(self):
        urls = {page.slug: page.url for page in loader.load_pages()}
        self.assertEqual(urls["index"], "/docs/")
        self.assertEqual(urls["quickstart"], "/docs/quickstart/")


class DocsPageTests(TestCase):
    longMessage = False

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, f"{url} answers {response.status_code}, not 200")
        return response, response.content.decode()

    def test_every_page_has_one_h1_with_its_title_inside_main(self):
        for page in loader.load_pages():
            with self.subTest(page=page.slug):
                _, html = self.get(page.url)
                self.assertEqual(html.count("<h1"), 1, f"{page.url} has {html.count('<h1')} h1 elements; a page has exactly one")
                self.assertIn(f"<h1>{escape(page.title)}</h1>", main_of(html),
                              f"backend/templates/docs/: {page.url} shows its title as the h1 inside <main>")

    def test_every_page_but_the_index_has_a_link_back_to_the_docs_above_its_title(self):
        # The old flat nav is gone: without this line a reader deep in the Docs has only the pager and the menu.
        for page in loader.load_pages():
            with self.subTest(page=page.slug):
                _, html = self.get(page.url)
                before_title = main_of(html).split("<h1", 1)[0]
                if page.slug == "index":
                    self.assertNotIn('href="/docs/"', before_title, "the index is /docs/: it needs no link to itself")
                    continue
                self.assertIn('<article>\n<p class="reading__meta"><a href="/docs/">Docs</a></p>\n', before_title,
                              f"backend/templates/docs/page.html: {page.url} opens, inside its <article>, with a meta line "
                              "that links to /docs/, above the h1")

    def test_the_shell_gives_every_page_a_skip_link_and_a_main(self):
        for page in loader.load_pages():
            with self.subTest(page=page.slug):
                _, html = self.get(page.url)
                self.assertIn('<a class="skip-link label" href="#main">', html,
                              f"backend/templates/docs/: {page.url} lost the skip link; the templates extend layouts/reading.html, which extends shell.html")
                self.assertIn('<main id="main" class="shell-main">', html,
                              f"backend/templates/docs/: {page.url} lost <main id=\"main\" class=\"shell-main\">; the templates extend layouts/reading.html")

    def test_the_index_lists_every_other_page_in_order_with_its_description(self):
        pages = [page for page in loader.load_pages() if page.slug != "index"]
        response, html = self.get("/docs/")
        self.assertEqual([entry.slug for entry in response.context["entries"]], [page.slug for page in pages])
        listed = html.split('id="docs-pages"', 1)[1]
        positions = [listed.index(f'href="{page.url}"') for page in pages]
        self.assertEqual(positions, sorted(positions),
                         f"backend/templates/docs/index.html: the list follows the pages' `order`; the pages stand at {positions}")
        for page in pages:
            self.assertIn(escape(page.description), listed, f"{page.url}: the list shows the page's description")
        self.assertNotIn('href="/docs/"', listed, "the index does not list itself")

    def test_the_index_alias_names_the_docs_index_as_its_canonical_address(self):
        # /docs/index/ shows the same page as /docs/ (docs/views.py); a search engine must be told which address is the page.
        _, html = self.get("/docs/index/")
        canonical = registry.canonical_url("docs-index")
        self.assertIn(f'<link rel="canonical" href="{canonical}" data-page-meta>', html,
                      f"backend/pages/context_processors.py: /docs/index/ shows the Docs index, so its canonical address is {canonical}")

    def test_the_index_keeps_its_own_words(self):
        _, html = self.get("/docs/")
        self.assertIn("New here? Start with the", main_of(html), "backend/templates/docs/index.html: the index prints the words of backend/docs/pages/index.md")

    def test_a_page_opens_with_the_list_of_its_sections(self):
        for page in loader.load_pages():
            titles = re.findall(r"^## (.+?)\s*$", page.body_md, re.MULTILINE)
            with self.subTest(page=page.slug):
                response, html = self.get(page.url)
                if len(titles) < 2:
                    self.assertNotIn('href="#', main_of(html), f"{page.url} has fewer than two sections: no list")
                    continue
                sections = response.context["sections"]
                self.assertEqual([section.title for section in sections], titles)
                for section in sections:
                    link, heading = f'href="#{section.id}"', f'id="{section.id}"'
                    self.assertIn(link, html, f"backend/templates/docs/page.html: {page.url} lists no link {link}; it includes components/sections.html with the sections")
                    self.assertIn(heading, html, f"{page.url}: no heading has {heading}; render_reading in backend/blog/render.py gives headings their ids")
                    self.assertLess(html.index(link), html.index(heading), f"{page.url}: the list comes before the section it links to")

    def test_a_page_ends_with_its_previous_and_next_pages(self):
        pages = loader.load_pages()
        for index, page in enumerate(pages):
            with self.subTest(page=page.slug):
                _, html = self.get(page.url)
                after = html.split("</article>", 1)[1].split("</main>", 1)[0]
                wanted = [] if page.slug == "index" else (
                    ([pages[index - 1].url] if index else []) + ([pages[index + 1].url] if index + 1 < len(pages) else []))
                found = re.findall(r'href="([^"]*)"', after)
                self.assertEqual(found, wanted,
                                 f"backend/templates/docs/page.html: {page.url} ends with links to the page before it and the page after it, in that order; "
                                 f"it has {found}, not {wanted}")

    def test_every_page_changes_in_place_to_the_pages_it_links_to(self):
        pages = loader.load_pages()
        for page in pages:
            with self.subTest(page=page.slug):
                _, html = self.get(page.url)
                found = re.search(r'data-layout-paths="([^"]*)"', html)
                self.assertIn('data-layout="reading"', html, f"backend/pages/registry.py: {page.url} is not in the reading layout")
                self.assertIsNotNone(found, f"backend/pages/context_processors.py: {page.url} has no data-layout-paths")
                paths = found.group(1).split()
                for other in pages:
                    self.assertIn(other.url, paths, f"backend/pages/registry.py: layout_paths('reading') lacks {other.url}, so the link to it "
                                                    f"on {page.url} loads a whole page instead of changing in place")

    def test_ids_are_unique_on_every_page(self):
        # The headings' ids come from their text, the code blocks' from render_reading, and the quickstart's raw HTML has ids of its own.
        for page in loader.load_pages():
            with self.subTest(page=page.slug):
                _, html = self.get(page.url)
                seen, repeated = set(), set()
                for element in elements(html):
                    name = element["attrs"].get("id")
                    if name is None:
                        continue
                    if name in seen:
                        repeated.add(name)
                    seen.add(name)
                self.assertEqual(repeated, set(), f"{page.url}: these ids are used twice: {sorted(repeated)}. Rename the heading or the raw HTML "
                                                  f"in backend/docs/pages/{page.slug}.md (a code block's id comes from render_reading in backend/blog/render.py)")

    def test_every_fenced_code_block_is_a_code_block_with_a_copy_button_for_its_exact_text(self):
        # The pages write their commands and configuration in fenced blocks, and render_reading (Task 1) makes each one a
        # components/code_block.html with a Copy button. The fences are counted in the Markdown, so a page rendered the old way
        # (render_markdown), or a block that lost its Copy button, fails here. The quickstart's raw HTML commands are not fences:
        # they have their own tabs and Copy (QuickstartSwitcherTests).
        for page in loader.load_pages():
            fenced = [text for _fence, text in re.findall(r"^(`{3,}|~{3,})[^\n]*\n(.*?)^\1[ \t]*$", page.body_md, re.MULTILINE | re.DOTALL)]
            with self.subTest(page=page.slug):
                _, html = self.get(page.url)
                tree = elements(html)
                blocks = [element for element in tree if "data-code-block" in element["attrs"]]
                self.assertEqual(len(blocks), len(fenced),
                                 f"{page.url}: backend/docs/pages/{page.slug}.md has {len(fenced)} fenced code blocks and the page shows {len(blocks)} code blocks. "
                                 "Fix: backend/docs/views.py renders a page with render_reading, which makes each fence a components/code_block.html; "
                                 "write a code block as a fence, never as indented text")
                for number, (block, source) in enumerate(zip(blocks, fenced), start=1):
                    where = f"{page.url}, code block {number}"
                    parts = [element for element in tree if inside(element, block)]
                    buttons = [element for element in parts if "data-copy" in element["attrs"]]
                    boxes = [element for element in parts if element["tag"] == "pre"]
                    statuses = [element for element in parts if "data-copy-status" in element["attrs"]]
                    found = (len(buttons), len(boxes), len(statuses))
                    self.assertEqual(found, (1, 1, 1), f"{where}: a code block is one Copy button, one <pre> and one status line, found {found}")
                    self.assertTrue(buttons[0]["attrs"].get("aria-label", "").startswith("Copy "),
                                    f"{where}: the Copy button's name starts with the word it shows, found {buttons[0]['attrs'].get('aria-label')!r}")
                    self.assertEqual(buttons[0]["attrs"]["data-copy"], boxes[0]["attrs"].get("id"),
                                     f"{where}: the Copy button names the id of the block's <pre>")
                    self.assertEqual(statuses[0]["attrs"].get("role"), "status", f"{where}: the status line has role=\"status\"")
                    copied, written = boxes[0]["text"].strip(), source.strip()
                    self.assertEqual(copied, written,
                                     f"{where}: Copy would put {copied!r} on the clipboard, and the Markdown has {written!r}. "
                                     "Fix: render_reading in backend/blog/render.py hands the block's text to the component unescaped")


class QuickstartSwitcherTests(TestCase):
    """The switcher is raw HTML in the Markdown file; tabs.js and copy.js run it through data attributes."""

    longMessage = False

    def setUp(self):
        self.html = self.client.get("/docs/quickstart/").content.decode()
        self.elements = elements(self.html)
        self.by_id = {element["attrs"]["id"]: element for element in self.elements if "id" in element["attrs"]}

    def test_the_tabs_pair_with_their_panels_inside_a_data_tabs_group(self):
        tabs = [element for element in self.elements if element["attrs"].get("role") == "tab"]
        names = [tab["text"] for tab in tabs]
        self.assertEqual(names, ["bash", "zsh", "fish", "PowerShell", "cmd"], f"{QUICKSTART}: the five shells, in this order, found {names}")
        for tab in tabs:
            name = tab["text"]
            with self.subTest(tab=name):
                self.assertTrue(any("data-tabs" in parent["attrs"] for parent in tab["parents"]),
                                f"{QUICKSTART}: the {name} tab is not inside an element with data-tabs, so tabs.js ignores it. "
                                "Fix: put data-tabs on the div around the tablist and the panels")
                panel = self.by_id.get(tab["attrs"].get("aria-controls"))
                self.assertIsNotNone(panel, f"{QUICKSTART}: the {name} tab's aria-controls names no element. Fix: give its panel that id")
                self.assertEqual(panel["attrs"].get("role"), "tabpanel")
                self.assertEqual(panel["attrs"].get("aria-labelledby"), tab["attrs"]["id"],
                                 f"{QUICKSTART}: the {name} panel is labelled by its tab, found {panel['attrs'].get('aria-labelledby')!r}")

    def test_the_first_tab_starts_selected_and_the_other_panels_start_hidden(self):
        tabs = [element for element in self.elements if element["attrs"].get("role") == "tab"]
        self.assertEqual([tab["attrs"].get("aria-selected") for tab in tabs], ["true", "false", "false", "false", "false"])
        self.assertEqual([tab["attrs"].get("tabindex") for tab in tabs], ["0", "-1", "-1", "-1", "-1"])
        panels = [self.by_id[tab["attrs"]["aria-controls"]] for tab in tabs]
        hidden = ["hidden" in panel["attrs"] for panel in panels]
        self.assertEqual(hidden, [False, True, True, True, True],
                         f"{QUICKSTART}: only the first panel is shown at first; tabs.js shows the others by removing hidden. Found hidden: {hidden}")

    def test_every_copy_button_names_its_command_and_has_a_status_line(self):
        buttons = [element for element in self.elements if "command__copy" in classes(element)]
        self.assertEqual(len(buttons), 6,
                         f"{QUICKSTART}: one Copy button (class command__copy) for each of the five shells and one for the upload command, found {len(buttons)}")
        for button in buttons:
            name = button["attrs"].get("data-copy", "")
            with self.subTest(button=name):
                target = self.by_id.get(name)
                self.assertIsNotNone(target, f"{QUICKSTART}: a Copy button's data-copy names no element. Fix: put id=\"{name}\" on the command's code element")
                self.assertTrue(button["attrs"].get("aria-label", "").startswith("Copy "),
                                f"{QUICKSTART}: a Copy button needs an aria-label that says what it copies, like \"Copy bash setup command\"")
                # copy.js reads the status line from the button's nearest element with data-tabs or data-code-block.
                scope = scope_of(button)
                self.assertTrue(scope is not None and "data-tabs" in scope["attrs"],
                                f"{QUICKSTART}: the Copy button of {name} sits outside any element with data-tabs, or inside a code block; "
                                "copy.js uses the nearest of data-tabs and data-code-block to find its status line")
                status = [element for element in self.elements if inside(element, scope) and "data-copy-status" in element["attrs"]]
                self.assertEqual(len(status), 1,
                                 f"{QUICKSTART}: the group of {name} needs one <span role=\"status\" data-copy-status>, which copy.js fills with Copied; it has {len(status)}")
                self.assertEqual(status[0]["attrs"].get("role"), "status")

    def test_the_fenced_command_is_a_code_block_and_the_raw_html_commands_are_not(self):
        # The revoke command is a fence, so render_reading gives it a code block's Copy. The six commands above it are raw HTML
        # with their own tabs and Copy, and must not be wrapped in a code block as well.
        blocks = [element for element in self.elements if "data-code-block" in element["attrs"]]
        self.assertEqual(len(blocks), 1, f"{QUICKSTART}: one fenced block (the revoke command), so one code block on the page, found {len(blocks)}")
        boxes = [element for element in self.elements if element["tag"] == "pre" and inside(element, blocks[0])]
        self.assertTrue(boxes and "curl -X DELETE" in boxes[0]["text"], f"{QUICKSTART}: the code block holds the revoke command, found {[box['text'] for box in boxes]}")
        for shell in ("bash", "zsh", "fish", "powershell", "cmd", "upload"):
            command = self.by_id.get(f"shellcmd-{shell}")
            self.assertIsNotNone(command, f"{QUICKSTART}: no element has id=\"shellcmd-{shell}\"")
            self.assertFalse(any("data-code-block" in parent["attrs"] for parent in command["parents"]),
                             f"{QUICKSTART}: the {shell} command is inside a code block; it keeps its own prompt, tabs and Copy")

    def test_copy_takes_the_command_and_not_its_prompt(self):
        lines = [element for element in self.elements if "command__line" in classes(element)]
        self.assertEqual(len(lines), 6, f"{QUICKSTART}: six command lines, each a div with class command__line, found {len(lines)}")
        for number, line in enumerate(lines, start=1):
            prompt = [e for e in self.elements if inside(e, line) and "command__prompt" in classes(e)]
            code = [e for e in self.elements if inside(e, line) and e["tag"] == "code"]
            with self.subTest(command=number):
                self.assertEqual((len(prompt), len(code)), (1, 1),
                                 f"{QUICKSTART}: a command line is one prompt, one code element and one Copy button, found {len(prompt)} prompts and {len(code)} code elements")
                self.assertTrue(code[0]["text"].strip(), f"{QUICKSTART}: command line {number} has no command for Copy to copy")
                self.assertFalse(code[0]["text"].startswith(prompt[0]["text"]),
                                 f"{QUICKSTART}: the prompt is inside the element Copy reads. Fix: keep the prompt in its own span beside the code element")
                self.assertEqual(prompt[0]["attrs"].get("aria-hidden"), "true")


class NoScriptTests(TestCase):
    longMessage = False

    def test_no_docs_page_carries_a_script(self):
        for page in loader.load_pages():
            with self.subTest(page=page.slug):
                html = self.client.get(page.url).content.decode()
                self.assertNotIn("<script", main_of(html).lower(),
                                 f"{page.url} has a script in <main>. It would not run after an in-place change. Fix: use a behaviour "
                                 "in backend/static/js/behaviors/ with a data attribute, as tabs.js and copy.js do")
        for path in sorted((BACKEND / "templates" / "docs").glob("*.html")):
            with self.subTest(template=path.name):
                self.assertNotIn("<script", path.read_text(encoding="utf-8").lower(), f"{path.relative_to(REPO)}: Docs run no script of their own; see static/js/behaviors/")

    def test_the_shared_behaviours_load_on_every_docs_page_not_only_where_they_are_used(self):
        # /docs/ has no tabs and no Copy button, and /docs/quickstart/ is reached from it by an in-place change, which runs
        # no new script: tabs.js and copy.js must already be loaded, so shell.html loads them on every page.
        for url in ("/docs/", "/docs/quickstart/"):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                head = html[:html.index("</head>")]
                for behaviour in ("tabs", "copy"):
                    self.assertIn(f"js/behaviors/{behaviour}.js", head,
                                  f"backend/templates/shell.html: load js/behaviors/{behaviour}.js on every shell page, not only where it is used")


class WithoutJavaScriptTests(SimpleTestCase):
    """The tabs and Copy need JavaScript. Without it the quickstart must still show every command (spec 9)."""

    longMessage = False

    RULES = (
        (":root:not(.js) .command[hidden]", "display: block", "the other four shells' commands would stay hidden"),
        (":root:not(.js) .command-group__tabs", "display: none", "the tabs would be buttons that do nothing"),
        (":root:not(.js) .command__copy", "display: none", "Copy would be a button that does nothing"),
        (".js .command__shell", "display: none", "each command would name its shell twice, once in the tab and once above the command"),
    )

    def test_the_commands_show_without_javascript_and_the_dead_controls_do_not(self):
        css = (BACKEND / "static" / "css" / "base.css").read_text(encoding="utf-8")
        for selector, declaration, why in self.RULES:
            with self.subTest(selector=selector):
                self.assertRegex(css, re.escape(selector) + r"\s*\{[^}]*" + re.escape(declaration),
                                 f"backend/static/css/base.css: restore `{selector} {{ {declaration}; }}`; without it {why}")


class RawHtmlExceptionTests(SimpleTestCase):
    longMessage = False

    def test_every_exception_still_carries_raw_html_and_says_why(self):
        fonts = FontRanges.load()
        for path, reason in content_checks.RAW_HTML_ALLOWED.items():
            with self.subTest(path=path):
                self.assertTrue(reason.strip(), f"backend/pages/content_checks.py: RAW_HTML_ALLOWED needs a reason for {path}")
                text, problems = content_checks.read_file(REPO / path, REPO)
                self.assertEqual(problems, [])
                raw = [problem for problem in content_checks.check_doc_page(path.replace(".md", "-unlisted.md"), text, fonts)
                       if "raw HTML" in problem.problem]
                self.assertTrue(raw, f"backend/pages/content_checks.py: RAW_HTML_ALLOWED lists {path}, which has no raw HTML any more. "
                                     "Fix: delete its entry, so that a tag added to it later is caught")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_docs_reading`
Expected: `Ran 26 tests` and `FAILED (failures=16, errors=52)` (the runner counts subtests: once for each of the five Docs pages in the tests that loop over them). 23 of the 26 tests fail; the other 3 pass already and must keep passing: `test_the_index_keeps_its_own_words`, `test_the_first_tab_starts_selected_and_the_other_panels_start_hidden` and `RawHtmlExceptionTests`. The 23:
- ERROR, `AttributeError: module 'docs.loader' has no attribute 'neighbours'`: five of the six `NeighboursTests`. ERROR, `AttributeError: 'Page' object has no attribute 'url'`: the sixth (`test_a_page_knows_its_address`) and, for each page, the `DocsPageTests` tests that loop over the pages (the link back and the new code-block test among them). ERROR, `KeyError: 'entries'`: the index list.
- FAIL: the canonical-address test (the old page's canonical link has no `data-page-meta`); `NoScriptTests` (the old template holds a `<script>` and its head loads neither `tabs.js` nor `copy.js`; its per-page loop errors on `page.url`); `WithoutJavaScriptTests` (the four CSS rules are missing); and every `QuickstartSwitcherTests` test but the first-tab one (no `data-tabs` yet, no `command__copy`, and no code block yet, because the view still renders with `render_markdown`).

- [ ] **Step 3: Give pages an address and neighbours**

In `backend/docs/loader.py`, replace this exact text:

```python
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
```

with:

```python
from dataclasses import dataclass
from pathlib import Path

from django.urls import reverse

logger = logging.getLogger(__name__)
```

Replace this exact text:

```python
    seo_title: str = ""  # head <title>; falls back to `title` when unset
```

with:

```python
    seo_title: str = ""  # head <title>; falls back to `title` when unset

    @property
    def url(self):
        """The page's address: /docs/ for the index, else /docs/<slug>/. The index list and the pager link to it."""
        return reverse("docs-index") if self.slug == "index" else reverse("docs-page", args=[self.slug])
```

Replace this exact text:

```python
def get_page(slug):
    for page in load_pages():
        if page.slug == slug:
            return page
    return None
```

with:

```python
def get_page(slug):
    for page in load_pages():
        if page.slug == slug:
            return page
    return None


def neighbours(slug):
    """(previous, next): the pages before and after `slug` in `order`. Either is None at an end of the list, and both
    are None for an unknown slug."""
    pages = load_pages()
    for index, page in enumerate(pages):
        if page.slug == slug:
            return (
                pages[index - 1] if index else None,
                pages[index + 1] if index + 1 < len(pages) else None,
            )
    return (None, None)
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_docs_reading.NeighboursTests`
Expected: `Ran 6 tests ... OK`.

- [ ] **Step 4: The views, the two templates and the template rules**

Replace the whole of `backend/docs/views.py` with:

```python
"""Docs views: index and page.

`/docs/` renders the `index` page and lists the others; every other page lives at `/docs/<slug>/`, opens with
the list of its sections and ends with links to the page before and the page after it. Unknown slugs 404.
"""

from django.http import Http404
from django.shortcuts import render

from blog.render import render_reading
from docs.loader import get_page, load_pages, neighbours


def docs_index(request):
    page = get_page("index")
    if page is None:
        raise Http404("No such page.")
    return render(
        request,
        "docs/index.html",
        {
            "page": page,
            "body_html": render_reading(page.body_md).html,
            "entries": [other for other in load_pages() if other.slug != "index"],
        },
    )


def docs_page(request, slug):
    if slug == "index":
        return docs_index(request)
    page = get_page(slug)
    if page is None:
        raise Http404("No such page.")
    reading = render_reading(page.body_md)
    previous_page, next_page = neighbours(slug)
    return render(
        request,
        "docs/page.html",
        {
            "page": page,
            "body_html": reading.html,
            "sections": reading.sections,
            "previous_page": previous_page,
            "next_page": next_page,
        },
    )
```

Create `backend/templates/docs/index.html`:

```django
{% extends "layouts/reading.html" %}
{# The Docs index: the page's own words, then every other page with its description (spec 4.6). #}
{% block reading %}
<article>
<h1>{{ page.title }}</h1>
{{ body_html|safe }}
<section aria-labelledby="docs-pages">
<h2 id="docs-pages">All pages</h2>
{% include "components/entries.html" with entries=entries only %}
</section>
</article>
{% endblock %}
```

Replace the whole of `backend/templates/docs/page.html` (the old standalone document, 167 lines, with its inline style and script) with:

```django
{% extends "layouts/reading.html" %}
{# A Docs page: a way back to the index, its title, the list of its sections, its words, then the pages before and after it (spec 4.6). #}
{% block reading %}
<article>
<p class="reading__meta"><a href="{% url 'docs-index' %}">Docs</a></p>
<h1>{{ page.title }}</h1>
{% include "components/sections.html" with sections=sections only %}
{{ body_html|safe }}
</article>
{% include "components/pager.html" with previous=previous_page next=next_page only %}
{% endblock %}
```

(The head, the skip link, the menu, the ambient background and the footer links that the old page wrote by hand now come from `shell.html`. `includes/cursor.html`, `page_transition.html`, `theme_boot.html`, `ambient.html` and `menu.html` are no longer used by Docs; Task 8 removes the ones nothing else uses. The old flat Docs nav is gone, so the meta line above the title is a page's way back to the index: it sits inside the `<article>`, so the pager test, which reads the links between `</article>` and `</main>`, is unaffected; its link is a prose link, styled by Task 3's rule for links in the reading column's paragraphs. The index has no such line, because it is the page the line links to. A fenced block in a page's words arrives as a code block, with its Copy button, from `render_reading`: the templates print `body_html` as it is.)

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_docs_reading.DocsPageTests pages.tests_docs_reading.NoScriptTests`
Expected: `Ran 13 tests ... OK` (the quickstart still has its old markup; Step 5 changes it). If `test_the_index_alias_names_the_docs_index_as_its_canonical_address` is the only failure, Task 2's `docs-page` entry does not map the slug `index` to the canonical `/docs/`: fix that in `backend/pages/context_processors.py`, not here. If `test_every_fenced_code_block_is_a_code_block_with_a_copy_button_for_its_exact_text` is the only failure, `render_reading` does not yet make a code block of each fence, or hands the component text that is still escaped: fix that in `backend/blog/render.py` (Task 1), not here.

Now bring the Docs templates under the shell's template rules (spec 7.2: no inline style, components included with `only`, every character in the shipped fonts). Task 4 left the folders those rules scan as `("components", "layouts", "chapters", "pages", "legal")`. In `backend/pages/tests_site_rules.py` (`shell_templates`), replace this exact text:

```python
    for directory in ("components", "layouts", "chapters", "pages", "legal"):
```

with:

```python
    for directory in ("components", "layouts", "chapters", "pages", "legal", "docs"):
```

In `backend/pages/tests_content.py` (`RealContentTests.test_every_shell_template`), replace this exact text:

```python
        for directory in ("components", "layouts", "chapters", "pages", "legal"):
```

with:

```python
        for directory in ("components", "layouts", "chapters", "pages", "legal", "docs"):
```

(Task 6 adds `"blog"` after `"docs"` in the same way.)

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules pages.tests_content`
Expected: `OK`. The two Docs templates already follow all three rules (no `style` attribute, every include is `with ... only`, no character outside ASCII), so the new tuple simply brings the folder under the rules and the run stays green; a template added to `templates/docs/` later that breaks one fails here, with a message naming the file.

- [ ] **Step 5: The quickstart's switcher and copy boxes on the shared behaviours**

In `backend/docs/pages/quickstart.md`, replace this exact text (the whole switcher, lines 23 to 57):

```html
<div class="shell-switcher">
<div class="shell-tabs" role="tablist" aria-label="Shell">
<button type="button" role="tab" id="shelltab-bash" aria-selected="true" aria-controls="shellpanel-bash" data-os="macOS · Linux" tabindex="0" class="shell-tab active">bash</button>
<button type="button" role="tab" id="shelltab-zsh" aria-selected="false" aria-controls="shellpanel-zsh" data-os="macOS · Linux" tabindex="-1" class="shell-tab">zsh</button>
<button type="button" role="tab" id="shelltab-fish" aria-selected="false" aria-controls="shellpanel-fish" data-os="macOS · Linux" tabindex="-1" class="shell-tab">fish</button>
<button type="button" role="tab" id="shelltab-powershell" aria-selected="false" aria-controls="shellpanel-powershell" data-os="Windows · macOS · Linux" tabindex="-1" class="shell-tab">PowerShell</button>
<button type="button" role="tab" id="shelltab-cmd" aria-selected="false" aria-controls="shellpanel-cmd" data-os="Windows" tabindex="-1" class="shell-tab">cmd</button>
</div>
<p class="shell-os muted small" aria-live="polite">macOS · Linux</p>
<div class="term" role="tabpanel" id="shellpanel-bash" aria-labelledby="shelltab-bash" tabindex="0">
<span class="term-prompt" aria-hidden="true">$</span>
<code class="term-code">echo 'export VEILFILE_API_KEY="PASTE_YOUR_KEY_HERE"' &gt;&gt; ~/.bashrc &amp;&amp; source ~/.bashrc</code>
<button type="button" class="term-copy" aria-label="Copy bash setup command" aria-live="polite">copy</button>
</div>
<div class="term" role="tabpanel" id="shellpanel-zsh" aria-labelledby="shelltab-zsh" tabindex="0" hidden>
<span class="term-prompt" aria-hidden="true">$</span>
<code class="term-code">echo 'export VEILFILE_API_KEY="PASTE_YOUR_KEY_HERE"' &gt;&gt; ~/.zshrc &amp;&amp; source ~/.zshrc</code>
<button type="button" class="term-copy" aria-label="Copy zsh setup command" aria-live="polite">copy</button>
</div>
<div class="term" role="tabpanel" id="shellpanel-fish" aria-labelledby="shelltab-fish" tabindex="0" hidden>
<span class="term-prompt" aria-hidden="true">$</span>
<code class="term-code">set -Ux VEILFILE_API_KEY "PASTE_YOUR_KEY_HERE"</code>
<button type="button" class="term-copy" aria-label="Copy fish setup command" aria-live="polite">copy</button>
</div>
<div class="term" role="tabpanel" id="shellpanel-powershell" aria-labelledby="shelltab-powershell" tabindex="0" hidden>
<span class="term-prompt" aria-hidden="true">PS&gt;</span>
<code class="term-code">$env:VEILFILE_API_KEY="PASTE_YOUR_KEY_HERE"; [Environment]::SetEnvironmentVariable("VEILFILE_API_KEY", "PASTE_YOUR_KEY_HERE", "User")</code>
<button type="button" class="term-copy" aria-label="Copy PowerShell setup command" aria-live="polite">copy</button>
</div>
<div class="term" role="tabpanel" id="shellpanel-cmd" aria-labelledby="shelltab-cmd" tabindex="0" hidden>
<span class="term-prompt" aria-hidden="true">C:\&gt;</span>
<code class="term-code">set "VEILFILE_API_KEY=PASTE_YOUR_KEY_HERE" &amp;&amp; setx VEILFILE_API_KEY "PASTE_YOUR_KEY_HERE"</code>
<button type="button" class="term-copy" aria-label="Copy cmd setup command" aria-live="polite">copy</button>
</div>
</div>
```

with (the five commands, the tab names and the `aria-label`s are unchanged; the prompt stays outside the element Copy reads; the OS line, which the old script rewrote on every choice, is now a static line inside each panel, and `command__shell` names the shell for the no-JavaScript view only):

```html
<div class="command-group" data-tabs>
<div class="command-group__tabs" role="tablist" aria-label="Shell">
<button type="button" role="tab" id="shelltab-bash" aria-selected="true" aria-controls="shellpanel-bash" tabindex="0" class="command-group__tab label interactive">bash</button>
<button type="button" role="tab" id="shelltab-zsh" aria-selected="false" aria-controls="shellpanel-zsh" tabindex="-1" class="command-group__tab label interactive">zsh</button>
<button type="button" role="tab" id="shelltab-fish" aria-selected="false" aria-controls="shellpanel-fish" tabindex="-1" class="command-group__tab label interactive">fish</button>
<button type="button" role="tab" id="shelltab-powershell" aria-selected="false" aria-controls="shellpanel-powershell" tabindex="-1" class="command-group__tab label interactive">PowerShell</button>
<button type="button" role="tab" id="shelltab-cmd" aria-selected="false" aria-controls="shellpanel-cmd" tabindex="-1" class="command-group__tab label interactive">cmd</button>
</div>
<div class="command" role="tabpanel" id="shellpanel-bash" aria-labelledby="shelltab-bash">
<div class="command__for"><span class="command__shell">bash · </span>macOS · Linux</div>
<div class="command__line">
<span class="command__prompt" aria-hidden="true">$</span>
<code id="shellcmd-bash" tabindex="0">echo 'export VEILFILE_API_KEY="PASTE_YOUR_KEY_HERE"' &gt;&gt; ~/.bashrc &amp;&amp; source ~/.bashrc</code>
<button type="button" class="command__copy label interactive" data-copy="shellcmd-bash" aria-label="Copy bash setup command">Copy</button>
</div>
</div>
<div class="command" role="tabpanel" id="shellpanel-zsh" aria-labelledby="shelltab-zsh" hidden>
<div class="command__for"><span class="command__shell">zsh · </span>macOS · Linux</div>
<div class="command__line">
<span class="command__prompt" aria-hidden="true">$</span>
<code id="shellcmd-zsh" tabindex="0">echo 'export VEILFILE_API_KEY="PASTE_YOUR_KEY_HERE"' &gt;&gt; ~/.zshrc &amp;&amp; source ~/.zshrc</code>
<button type="button" class="command__copy label interactive" data-copy="shellcmd-zsh" aria-label="Copy zsh setup command">Copy</button>
</div>
</div>
<div class="command" role="tabpanel" id="shellpanel-fish" aria-labelledby="shelltab-fish" hidden>
<div class="command__for"><span class="command__shell">fish · </span>macOS · Linux</div>
<div class="command__line">
<span class="command__prompt" aria-hidden="true">$</span>
<code id="shellcmd-fish" tabindex="0">set -Ux VEILFILE_API_KEY "PASTE_YOUR_KEY_HERE"</code>
<button type="button" class="command__copy label interactive" data-copy="shellcmd-fish" aria-label="Copy fish setup command">Copy</button>
</div>
</div>
<div class="command" role="tabpanel" id="shellpanel-powershell" aria-labelledby="shelltab-powershell" hidden>
<div class="command__for"><span class="command__shell">PowerShell · </span>Windows · macOS · Linux</div>
<div class="command__line">
<span class="command__prompt" aria-hidden="true">PS&gt;</span>
<code id="shellcmd-powershell" tabindex="0">$env:VEILFILE_API_KEY="PASTE_YOUR_KEY_HERE"; [Environment]::SetEnvironmentVariable("VEILFILE_API_KEY", "PASTE_YOUR_KEY_HERE", "User")</code>
<button type="button" class="command__copy label interactive" data-copy="shellcmd-powershell" aria-label="Copy PowerShell setup command">Copy</button>
</div>
</div>
<div class="command" role="tabpanel" id="shellpanel-cmd" aria-labelledby="shelltab-cmd" hidden>
<div class="command__for"><span class="command__shell">cmd · </span>Windows</div>
<div class="command__line">
<span class="command__prompt" aria-hidden="true">C:\&gt;</span>
<code id="shellcmd-cmd" tabindex="0">set "VEILFILE_API_KEY=PASTE_YOUR_KEY_HERE" &amp;&amp; setx VEILFILE_API_KEY "PASTE_YOUR_KEY_HERE"</code>
<button type="button" class="command__copy label interactive" data-copy="shellcmd-cmd" aria-label="Copy cmd setup command">Copy</button>
</div>
</div>
<span class="visually-hidden" role="status" data-copy-status></span>
</div>
```

Replace this exact text (the upload command, lines 63 to 67):

```html
<div class="term">
<span class="term-prompt" aria-hidden="true">$</span>
<code class="term-code">curl -X POST https://veilfile.com/api/v1/artifacts -H "Authorization: Bearer $VEILFILE_API_KEY" -F "file=@screenshot.png" -F "ttl_days=7"</code>
<button type="button" class="term-copy" aria-label="Copy upload command" aria-live="polite">copy</button>
</div>
```

with (this group has no tabs; `data-tabs` is what copy.js uses to find the group's own status line, as in the install line):

```html
<div class="command-group" data-tabs>
<div class="command">
<div class="command__line">
<span class="command__prompt" aria-hidden="true">$</span>
<code id="shellcmd-upload" tabindex="0">curl -X POST https://veilfile.com/api/v1/artifacts -H "Authorization: Bearer $VEILFILE_API_KEY" -F "file=@screenshot.png" -F "ttl_days=7"</code>
<button type="button" class="command__copy label interactive" data-copy="shellcmd-upload" aria-label="Copy upload command">Copy</button>
</div>
</div>
<span class="visually-hidden" role="status" data-copy-status></span>
</div>
```

(The fenced `curl -X DELETE` block below the upload command is not touched: it is Markdown, so `render_reading` makes it a code block with its own Copy, and `copy.js` finds that Copy button's status line in its `data-code-block`, not in either of these `data-tabs` groups. The six commands above keep their own tabs and Copy, as the owner asked. Since PR #15 `copy.js` takes the nearest of `data-tabs` and `data-code-block`; each raw-HTML Copy button has a `data-tabs` group and no code block around it, so it finds its own group's status line, and `QuickstartSwitcherTests` holds that. The install-line checks in `site-tests/run_site_checks.py`, `install_line_problems` and `install_line_height_problems`, pick `page.locator("[data-tabs]").first`; these two groups carry `data-tabs` as well, so Task 7 keeps both out of the reading part, which checks the quickstart's tabs with its own `reading_tabs_problems`, and they never run on `/docs/quickstart/`: it is not one of the `PAGES` the pages part opens. `code_block_problems`, the check for PR #15's code blocks, opens Begin only.)

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_docs_reading.QuickstartSwitcherTests pages.tests_docs_reading.NoScriptTests pages.tests_content`
Expected: `OK` (the 5 tests of `QuickstartSwitcherTests` and the 2 of `NoScriptTests`, and the content checks still pass: the file has no characters outside the fonts and its raw HTML is still allowed).

- [ ] **Step 6: Style the switcher and make it work without JavaScript**

In `backend/static/css/base.css`, replace this exact text:

```css
.plans {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 10.5rem), 1fr));
```

with:

```css
/* Docs (spec 4.6): the quickstart's shell switcher and its command boxes. They are raw HTML in
   backend/docs/pages/quickstart.md, run by tabs.js and copy.js through data-tabs and data-copy, and they are styled
   here, with the rules every page has, because an in-place change brings no stylesheet with it. A command scrolls
   inside its own box, its prompt and Copy held in place. Without JavaScript the tabs and Copy are hidden and every
   shell's command shows, each under a line that names its shell. */
.command-group {
  margin: var(--space-3) 0 var(--space-4);
}

.command-group__tabs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3) 1.6em;
  margin: 0 0 var(--space-3);
}

.command .command__for {
  margin: 0 0 var(--space-2);
  font-family: var(--font-mono);
  font-size: .75em;
  line-height: 1.5;
  color: var(--muted);
}

.command .command__line {
  display: flex;
  align-items: center;
  gap: 1.2em;
  padding: .95em 1.2em .95em 1.3em;
  border: 1px solid var(--hair);
  background: var(--box);
  font-family: var(--font-mono);
  font-size: .75em;
  line-height: 1.5;
  color: var(--ink);
}

.command .command__line code {
  flex: 1 1 auto;
  min-width: 0;
  padding: 0;
  overflow-x: auto;
  white-space: nowrap;
  border: 0;
  background: none;
  font: inherit;
}

.command .command__prompt {
  flex: none;          /* never shrinks: under .reading's overflow-wrap: anywhere a shrinking prompt wraps letter by letter */
  white-space: nowrap;
  color: var(--muted);
}

/* Copy says Copy, Copied or Selected: it keeps the width of the longest (eight letters, each its advance plus the
   label's .2em tracking, and the .8em padding either side) so the box never jumps. */
.command .command__copy {
  flex: none;
  min-width: calc(8ch + 3.2em);
  text-align: center;
}

.js .command__shell {
  display: none;
}

:root:not(.js) .command[hidden] {
  display: block;
}

:root:not(.js) .command + .command {
  margin-top: var(--space-3);
}

:root:not(.js) .command-group__tabs {
  display: none;
}

:root:not(.js) .command__copy {
  display: none;
}

.plans {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 10.5rem), 1fr));
```

(The selectors start with `.command ` so they outrank Task 3's `.reading code`, `.reading p` and similar rules whatever their order. No colour value is typed: the rule in `pages.tests_site_rules` that keeps colours in `tokens.css` stays satisfied. The fenced blocks need no rule here: `code_block.css`, which `shell.html` links on every page, styles them, and its `.code-block pre` rules outweigh Task 3's `.reading :where(pre)`.)

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_docs_reading pages.tests_site_rules pages.tests_shell pages.tests_tokens`
Expected: `OK` (26 tests in `pages.tests_docs_reading`).

- [ ] **Step 7: Say why quickstart.md keeps its raw HTML**

In `backend/pages/content_checks.py`, replace this exact text:

```python
# Docs pages that may still carry raw HTML, and why. Remove an entry when its markup becomes a component.
RAW_HTML_ALLOWED = {
    "backend/docs/pages/quickstart.md": "the shell switcher and copy boxes, which become components when Docs move onto the shell (PR 4)",
```

with:

```python
# Docs pages that carry raw HTML, and why. Remove an entry when its page has none left (a test fails while an entry is stale).
RAW_HTML_ALLOWED = {
    "backend/docs/pages/quickstart.md": "the shell switcher (five tabs over one command each) and the upload command: each command sits beside its prompt, "
                                        "outside what Copy copies, and Markdown has no way to write tabs. The markup carries the data-tabs and data-copy "
                                        "attributes that static/js/behaviors/tabs.js and copy.js run (a fenced block needs none of this: render_reading "
                                        "gives it a code block with its own Copy)",
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_content pages.tests_docs_reading.RawHtmlExceptionTests`
Expected: `OK`.

- [ ] **Step 8: Move the old-markup tests**

Each change below is forced by the move; the reason is given with it. Make them in this order. Where a replacement is written for the state after Task 4, the text quoted is what Task 4 leaves.

**`backend/drop_core/tests.py`**, in `DocsTests`. The flat nav on every page (`docnav`) is gone: pages are listed by the index and linked by the pager, both pinned in `pages/tests_docs_reading.py`. Replace this exact text:

```python
    def test_docs_nav_lists_pages(self):
        response = self.client.get("/docs/quickstart/")
        for slug in self.DOCS_SLUGS:
            self.assertContains(response, f"/docs/{slug}/")
```

with:

```python
    def test_docs_index_lists_pages(self):
        # Pages have no nav of their own now: the index lists each one with its description, and each page ends
        # with links to its neighbours (pages/tests_docs_reading.py).
        response = self.client.get("/docs/")
        for slug in self.DOCS_SLUGS:
            self.assertContains(response, f'href="/docs/{slug}/"')
```

**`backend/drop_core/tests.py`**, in `AccessibilityTests`.

1. `test_skip_link_and_main_id_on_django_pages` looks for the old page's `<a class="skip-link" href="#main">` and `<main id="main">`. The shell writes `<a class="skip-link label" href="#main">` and `<main id="main" class="shell-main">`, which `DocsPageTests.test_the_shell_gives_every_page_a_skip_link_and_a_main` now pins for Docs. In its `urls` list, replace this exact text:

```python
            "/blog/introducing-veilfile/",
            "/docs/quickstart/",
            "/accounts/signup/",
```

with:

```python
            "/blog/introducing-veilfile/",
            "/accounts/signup/",
```

2. `test_decorative_wordmark_cursor_hidden` checks the old page's `<span class="cursor" aria-hidden="true">_</span>`; the shell's mark (an SVG) has no underscore. Task 4 moved `/terms/`, the other page in this loop, off the old mark, so no page is left for the loop. Replace this exact text (after Task 4 the loop reads `for url in ("/docs/quickstart/",):`):

```python
    def test_decorative_wordmark_cursor_hidden(self):
        # The trailing "_" in the wordmark must not be announced.
        for url in ("/docs/quickstart/",):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(
                    response, '<span class="cursor" aria-hidden="true">_</span>'
                )
                self.assertNotContains(response, "<span>file_</span>")
        # Auth pages use the .brand mark: static underscore, still hidden.
        response = self.client.get("/accounts/signup/")
        self.assertContains(response, '<span aria-hidden="true">_</span>')
```

with:

```python
    def test_decorative_wordmark_cursor_hidden(self):
        # The trailing "_" in the wordmark must not be announced. Auth pages use the .brand mark:
        # static underscore, still hidden.
        response = self.client.get("/accounts/signup/")
        self.assertContains(response, '<span aria-hidden="true">_</span>')
```

3. `test_env_switcher_apg_tabs_pattern` read the keyboard keys out of `templates/docs/page.html`'s inline script. They are `tabs.js`'s now, and the markup hands the switcher to it with `data-tabs`. The OS-hint live region is gone with the script (the OS is static text in each panel). Replace this exact text:

```python
    # The shell switcher moved from the React landing to the docs quickstart
    # (approved 2026-10-06 redesign). The APG tabs markup ships in the docs
    # markdown; the keyboard behavior ships in the docs page template. Both
    # are asserted against the shipped source, since there is no JS test
    # harness.
```

with:

```python
    # The shell switcher moved from the React landing to the docs quickstart
    # (approved 2026-10-06 redesign). The APG tabs markup ships in the docs
    # markdown; the keyboard behavior ships in static/js/behaviors/tabs.js,
    # which the markup's data-tabs hands the switcher to. Both are asserted
    # against the shipped source, since there is no JS test harness.
```

and this exact text:

```python
        tpl = (root / "backend" / "templates" / "docs" / "page.html").read_text()
        self.assertIn("ArrowRight", tpl)
        self.assertIn("ArrowLeft", tpl)
        self.assertIn("Home", tpl)
        self.assertIn("End", tpl)
        self.assertIn("aria-selected", tpl)
        # the OS hint announces politely (markup ships in the quickstart)
        self.assertIn('aria-live="polite"', md)
```

with:

```python
        self.assertIn("data-tabs", md)
        tabs = (root / "backend" / "static" / "js" / "behaviors" / "tabs.js").read_text()
        self.assertIn("ArrowRight", tabs)
        self.assertIn("ArrowLeft", tabs)
        self.assertIn("Home", tabs)
        self.assertIn("End", tabs)
        self.assertIn("aria-selected", tabs)
        # the page carries no script of its own (pages/tests_docs_reading.py)
        self.assertNotIn("<script", md)
```

4. `test_docs_nav_marks_current_page` looked for `class="current" aria-current="page"` on the old docnav. Replace this exact text:

```python
    def test_docs_nav_marks_current_page(self):
        response = self.client.get("/docs/quickstart/")
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, 'class="current" aria-current="page"')
```

with:

```python
    def test_menu_marks_where_the_reader_is_in_docs(self):
        # Docs pages have no nav of their own now: the menu's Docs link marks the page ("page" on the
        # index, "true" on a page inside the section).
        response = self.client.get("/docs/")
        self.assertContains(response, 'href="/docs/" aria-current="page">Docs</a>')
        response = self.client.get("/docs/quickstart/")
        self.assertContains(response, 'href="/docs/" aria-current="true">Docs</a>')
```

(`test_copy_buttons_named_and_live` needs no change: `quickstart.md` still holds `aria-label="Copy bash setup command"` and `aria-label="Copy upload command"`. `test_render_markdown_marks_scroll_regions` is about `render_markdown` and is untouched.)

**`backend/drop_core/tests_site_pages.py`** (`DocsSetupFlowTests`). Four assertions were about the inline script's text being in the rendered page (`ArrowRight`, `Home`, `aria-live="polite"` for the OS hint, `navigator.clipboard.writeText`); the keys and the clipboard call are now in `tabs.js` and `copy.js`, so the tests read those files and check the page loads them. Replace this exact text:

```python
from django.test import TestCase

```

with:

```python
from pathlib import Path

from django.test import TestCase

BEHAVIOURS = Path(__file__).resolve().parent.parent / "static" / "js" / "behaviors"

```

Replace this exact text:

```python
    def test_quickstart_switcher_keyboard_behavior(self):
        html = self._html()
        self.assertIn("ArrowRight", html)
        self.assertIn("ArrowLeft", html)
        self.assertIn("Home", html)
        self.assertIn("End", html)
        self.assertIn("aria-selected", html)
        self.assertIn('aria-live="polite"', html)  # OS hint announcement
```

with:

```python
    def test_quickstart_switcher_keyboard_behavior(self):
        # The keys are tabs.js's, which the switcher's data-tabs hands it to; the page carries no script.
        html = self._html()
        self.assertIn("data-tabs", html)
        self.assertIn("js/behaviors/tabs.js", html)
        self.assertIn('aria-selected="true"', html)
        tabs = (BEHAVIOURS / "tabs.js").read_text()
        for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
            with self.subTest(key=key):
                self.assertIn(key, tabs)
```

Replace this exact text:

```python
        self.assertIn('aria-label="Copy upload command"', html)
        self.assertIn("navigator.clipboard.writeText", html)
```

with:

```python
        self.assertIn('aria-label="Copy upload command"', html)
        # Copy is copy.js's: it reads data-copy, writes to the clipboard and tells screen readers through the status line.
        self.assertIn('data-copy="shellcmd-bash"', html)
        self.assertIn('role="status" data-copy-status', html)
        self.assertIn("js/behaviors/copy.js", html)
        self.assertIn("navigator.clipboard.writeText", (BEHAVIOURS / "copy.js").read_text())
```

(The other tests in the class, the shell ids, the one-liners, the upload line, the key-name note and the limits, read words that did not change and need no edit.)

**`backend/drop_core/tests_menu.py`.** `/docs/` and `/docs/quickstart/` no longer have the old hamburger menu (`class="site-menu-wrap"`) or the old three-link footer; the shell's menu and footer are pinned by `pages/tests_shell.py` and `pages/tests_registry.py`. Replace this exact text:

```python
MENU_PAGES = [
    "/docs/",
    "/docs/quickstart/",
```

with:

```python
MENU_PAGES = [
```

and this exact text:

```python
FOOTER_PAGES = [
    "/docs/",
    "/docs/quickstart/",
```

with:

```python
FOOTER_PAGES = [
```

(Tasks 4 and 6 take their pages out of the same two lists in the same way.)

**`backend/drop_core/tests_ambient.py`.** The old CSS ambient blobs, veil and control cluster are not on Docs any more (the cloth and the shell's switches replace them, and `pages/tests_veil.py` and the browser checks cover those). Replace this exact text in `FIELD_PAGES`:

```python
    ("/docs/", True),
```

with nothing (delete the line), and replace this exact text (the whole method, with the blank line after it):

```python
    def test_docs_page_gets_field_and_veil(self):
        html = _html(self.client.get("/docs/quickstart/"))
        self.assertIn('data-ambient="field"', html)
        self.assertIn('class="veil"', html)

```

with nothing (delete it).

(`NoTemplateCommentLeakTests` keeps `/docs/quickstart/` in its list on purpose: it still guards the new templates against a multi-line `{# #}` comment, which Django would print.)

**`backend/drop_core/tests_fix_web.py`.** Two edits. First, `ContentGuardTests._rendered_content` must read what the Docs pages now serve: `render_reading` also makes each fence a code block with a button, a `<pre>` and a status line, which the guard (no script, no event handler, no `javascript:` address) must see as the pages show it. Task 4 did the same for the legal documents. Replace this exact text:

```python
            yield f"docs/{page.slug}", render_markdown(page.body_md)
```

with:

```python
            yield f"docs/{page.slug}", render_reading(page.body_md).html
```

(`render_reading` is already imported there by Task 4; `render_markdown` stays for the Journal's posts until Task 6.) Second, `test_guard_allows_the_docs_shell_switcher_markup` checks that the markup the quickstart really ships does not trip the active-HTML guard; its sample must be the new markup. Replace this exact text:

```python
                '<div class="shell-switcher">'
                '<div class="shell-tabs" role="tablist" aria-label="Shell">'
                '<button type="button" role="tab" id="shelltab-bash" '
                'aria-selected="true" aria-controls="shellpanel-bash" '
                'data-os="macOS · Linux" tabindex="0" class="shell-tab active">'
                "bash</button></div>"
                '<p class="shell-os muted small" aria-live="polite">macOS · Linux</p>'
                '<div class="term" role="tabpanel" id="shellpanel-bash" '
                'aria-labelledby="shelltab-bash" tabindex="0">'
                '<span class="term-prompt" aria-hidden="true">$</span>'
                '<code class="term-code">veilfile login</code></div></div>'
```

with:

```python
                '<div class="command-group" data-tabs>'
                '<div class="command-group__tabs" role="tablist" aria-label="Shell">'
                '<button type="button" role="tab" id="shelltab-bash" '
                'aria-selected="true" aria-controls="shellpanel-bash" tabindex="0" '
                'class="command-group__tab label interactive">bash</button></div>'
                '<div class="command" role="tabpanel" id="shellpanel-bash" '
                'aria-labelledby="shelltab-bash">'
                '<div class="command__line">'
                '<span class="command__prompt" aria-hidden="true">$</span>'
                '<code id="shellcmd-bash" tabindex="0">veilfile login</code>'
                '<button type="button" class="command__copy label interactive" '
                'data-copy="shellcmd-bash" aria-label="Copy bash setup command">'
                "Copy</button></div></div>"
                '<span class="visually-hidden" role="status" data-copy-status></span></div>'
```

**`backend/pages/tests_head.py`.** `ShellPageIsLazyTests.test_a_registry_page_that_never_reads_it_renders_without_the_link_preview_image` asserts that a registry page does not extend `shell.html`. Task 4 left it only `/docs/`, and Docs extends the shell now: no registry page is off the shell. Replace this exact text (after Task 4 the loop reads `for path in ("/docs/",):`):

```python
    def test_a_registry_page_that_never_reads_it_renders_without_the_link_preview_image(self):
        with mock.patch("pages.context_processors.static", static_without_the_link_preview_image):
            for path in ("/docs/",):
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertNotIn("shell.html", [template.name for template in response.templates],
                                     f"{path} now extends shell.html; pick a registry page that does not")

```

with nothing (delete it, with the blank line after it). The class keeps `test_the_value_is_built_only_when_read` and `test_a_page_outside_the_registry_still_gets_a_falsy_value`, and the helper `static_without_the_link_preview_image` stays (the first of them uses it).

**`backend/drop_core/tests_fix_docs.py`** (the docs price test) needs no change: it reads `backend/docs/pages/*.md` and `backend/templates/**/*.html` for plan figures. The new Docs templates state none, and `how-it-works.md`, whose plan table keeps that test producing facts, is untouched.

**No other test counts or compares the text or links of a rendered Docs page** (a Copy button per code block adds a button and the word Copy, and the quickstart's raw-HTML buttons and the code blocks' buttons are told apart in `pages/tests_docs_reading.py`). Checked: `pages.tests_site_rules.InternalLinksTests` collects `<a href>` only, and a Copy button has none; `pages.tests_chapters.BeginTests` reads Begin; `pages.tests_registry`, `pages.tests_sitemap` and `drop_core.tests_fix_docs` read heads, the sitemap and the Markdown files; `drop_core.tests_site_pages` asserts substrings of the quickstart that did not change.

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests.DocsTests drop_core.tests.AccessibilityTests drop_core.tests_site_pages drop_core.tests_menu drop_core.tests_ambient drop_core.tests_fix_web drop_core.tests_fix_docs pages.tests_head pages.tests_registry pages.tests_sitemap pages.tests_site_rules pages.tests_docs_reading`
Expected: `OK`. (`drop_core.tests_fix_web` includes Task 1's `RenderReadingTests`. With Task 1 as amended they pass. If one fails here on a Docs page's code block, Task 1's equality with `render_markdown` has not been restated for the component's wrapper: fix it in Task 1's test, not here.)

- [ ] **Step 9: Run the whole suite and the budget test**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: `OK` (about 3.5 minutes; the count has grown by the 26 tests of `pages.tests_docs_reading` and shrunk by two, so it is 24 more than Task 4 left: `ShellPageIsLazyTests` loses its registry-page test and `drop_core.tests_ambient` loses `test_docs_page_gets_field_and_veil`, both in Step 8).

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_static`
Expected: `OK`. This task adds no JavaScript: `shell.js` plus `behaviors/*.js` measure 10218 of 10240 bytes gzipped on main (`shell.js` 7562, `come-in.js` 1123, `copy.js` 900, `tabs.js` 633), so there is no room for a byte.

- [ ] **Step 10: Commit**

```bash
git add backend/docs/loader.py backend/docs/views.py backend/templates/docs/index.html backend/templates/docs/page.html \
  backend/docs/pages/quickstart.md backend/static/css/base.css backend/pages/content_checks.py backend/pages/tests_docs_reading.py \
  backend/pages/tests_site_rules.py backend/pages/tests_content.py \
  backend/pages/tests_head.py backend/drop_core/tests.py backend/drop_core/tests_site_pages.py backend/drop_core/tests_menu.py \
  backend/drop_core/tests_ambient.py backend/drop_core/tests_fix_web.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -m "Docs on the reading layout: a way back to the index, the sections, previous and next, Copy on every code block, and the quickstart switcher on the shared tabs and Copy"
```

---

---

### Task 6: The Journal on the reading layout, and the feed

**Files:**
- Create: `backend/blog/tests_loader.py`, `backend/blog/tests_journal.py`, `backend/blog/tests_feed.py`
- Modify: `backend/blog/loader.py`, `backend/blog/views.py` (whole file), `backend/blog/feeds.py` (whole file), `backend/templates/blog/index.html` (whole file), `backend/templates/blog/post.html` (whole file), `backend/static/css/base.css` (one rule appended), `frontend/index.html` (one attribute), `AGENTS.md`
- Modify (tests): `backend/drop_core/tests.py`, `backend/drop_core/tests_fix_web.py`, `backend/drop_core/tests_menu.py`, `backend/drop_core/tests_ambient.py`, `backend/pages/tests_site_rules.py`, `backend/pages/tests_content.py`

**Interfaces:**
- Consumes:
  - Task 1: `blog.render.render_reading(text) -> Reading`. A post uses `reading.html`, in which each fenced code block is already a `components/code_block.html` (the Copy button, the `<pre>` it names by an id that is unique on the page, and the status Copy speaks), so neither the post's file nor its template asks for Copy; its `sections` are not used, because posts have no section list. `render_markdown` is unchanged and stays the feed's renderer.
  - Task 2: `layout="reading"` on the `blog-index` registry page, the head entry for `blog-post`, and `Post.image` and `Post.image_alt` on `blog.loader.Post` (read from the front matter keys `image` and `image_alt`; both `""` when the front matter has none). The post page reads those two fields for its header image; this task does not add them.
  - Task 3: `templates/layouts/reading.html` (it fills `{% block main %}` and offers `{% block reading %}`), the class `reading__meta`, `components/entries.html` (input `entries`) and `components/pager.html` (inputs `previous`, `next`, labelled "More to read").
  - Tasks 4 and 5: the tuple of template folders that `pages/tests_site_rules.py` (`shell_templates`) and `pages/tests_content.py` (`RealContentTests.test_every_shell_template`) scan, as they left it: `("components", "layouts", "chapters", "pages", "legal", "docs")`.
  - `pages.registry.SITE_ORIGIN` (`https://veilfile.com`).
- Produces:
  - In `blog.loader`: `neighbours(slug) -> (older, newer)`, `reading_minutes(text) -> int`, `image_size(name) -> (width, height) | None`, and the constants `STATIC_DIR` and `WORDS_PER_MINUTE`.
  - In `blog.feeds`: `absolute_links(html, page_url)`, `ContentFeed` and `BlogFeed`.
  - The CSS class `reading__image` (header image), defined in `base.css`.
  - Test helpers `blog.tests_loader.FakeJournal`, `png_header` and `words`.
  - The scanned tuple of template folders, now `("components", "layouts", "chapters", "pages", "legal", "docs", "blog")`.
- Decisions made here:
  - **Dates** are shown as `2026-10-05` (ISO, in mono), as the old index and old posts showed them. The index hands the entries component a `datetime.date`. The meta line writes the same text inside `<time datetime="2026-10-05">`.
  - **A way back**: in a post's meta line the word Journal links to the index (`{% url 'blog-index' %}`), because the pager leads only to neighbouring posts. The visible text of the line is unchanged: `Journal · 2026-10-05 · 3 min read`.
  - **Reading time** is the number of words in the Markdown body divided by 200, rounded up, at least 1. A word is a run of characters between spaces that holds a letter or a digit, so `##`, `-`, `>`, `|---|` and a bare fence are not words. Code blocks count: a reader reads them too, and leaving them out would promise a shorter read than the page takes. The words are the Markdown's, not the page's, so the Copy button on a code block adds none.
  - **The header image's size** is read from the file when it is a PNG (the first 24 bytes), so the browser keeps the space before the file arrives and the text does not jump. Another format is shown without width and height. There is no new dependency.
  - **The feed** puts the whole post in `<content:encoded>`. Its `<description>` stays the post's description. Every address in it is absolute from `SITE_ORIGIN`, including the relative links and images inside the post's own HTML.
  - **Copy on code blocks** comes from `render_reading` (Task 1, through `components/code_block.html`, which `copy.js` already serves), so `blog/post.html` prints `reading.html` as it always did and a post's file writes nothing for it. **The feed keeps `render_markdown`**: a Copy button needs a script, which no feed reader runs, so a test pins that an item holds the plain `<pre>` and no button. The new page tests read a post the way `copy.js` does (the button's `data-copy` names a `<pre id>`, whose trimmed text is what reaches the clipboard) and do not depend on how Task 1 numbers or labels the blocks.
  - **Failure messages**: every test class in `tests_journal.py` and `tests_feed.py` sets `longMessage = False`, so a failed assertion prints the message that names the file and the fix, not the page or the feed it searched. Where an assertion carries no message, its container is a short text. (Task 8 holds the convention test.)

- [ ] **Step 1: Write the failing loader tests**

Create `backend/blog/tests_loader.py`:

```python
"""The Journal's loader: neighbours, reading time and the size of a header image (spec 4.6)."""
import struct
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from blog import loader


def png_header(width, height):
    """The first 29 bytes of a PNG: its signature and IHDR chunk, all that image_size() reads."""
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
            + b"\x08\x06\x00\x00\x00")


def words(count):
    """A text of count words, for reading-time tests."""
    return " ".join(["word"] * count)


class FakeJournal:
    """Mixin for a test case: an empty posts directory and an empty static directory, in temporary
    places, that the loader reads instead of backend/blog/posts/ and backend/static/."""

    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.posts_dir = Path(directory.name) / "posts"
        self.static_dir = Path(directory.name) / "static"
        self.posts_dir.mkdir()
        (self.static_dir / "img").mkdir(parents=True)
        for name, path in (("POSTS_DIR", self.posts_dir), ("STATIC_DIR", self.static_dir)):
            patcher = mock.patch.object(loader, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_post(self, slug, day, body="Body.", **front_matter):
        """backend/blog/posts/<slug>.md as the loader reads it. The title is the slug as words
        ("my-post" is "My post"); front_matter adds lines such as image."""
        header = {"title": slug.replace("-", " ").capitalize(), "date": day, "slug": slug,
                  "description": f"The post called {slug}.", **front_matter}
        lines = "".join(f"{key}: {value}\n" for key, value in header.items())
        (self.posts_dir / f"{slug}.md").write_text(f"---\n{lines}---\n\n{body}\n", encoding="utf-8")


class NeighbourTests(FakeJournal, SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.write_post("first", "2026-01-10")
        self.write_post("second", "2026-02-10")
        self.write_post("third", "2026-03-10")

    def around(self, slug):
        older, newer = loader.neighbours(slug)
        return (older.slug if older else None, newer.slug if newer else None)

    def test_a_post_between_two_others_has_both_neighbours(self):
        self.assertEqual(self.around("second"), ("first", "third"))

    def test_the_newest_post_has_only_an_older_neighbour(self):
        self.assertEqual(self.around("third"), ("second", None))

    def test_the_oldest_post_has_only_a_newer_neighbour(self):
        self.assertEqual(self.around("first"), (None, "second"))

    def test_a_lone_post_has_no_neighbours(self):
        (self.posts_dir / "first.md").unlink()
        (self.posts_dir / "third.md").unlink()
        self.assertEqual(self.around("second"), (None, None))

    def test_a_post_dated_in_the_future_is_never_a_neighbour(self):
        self.write_post("later", "2999-01-01")
        self.assertEqual(self.around("third"), ("second", None),
                         "backend/blog/loader.py: neighbours() must walk load_posts(), which hides posts dated in the future")
        self.assertEqual(self.around("later"), (None, None))

    def test_an_unknown_slug_has_no_neighbours(self):
        self.assertEqual(self.around("nope"), (None, None))

    def test_posts_on_one_day_still_form_one_chain(self):
        self.write_post("also-second", "2026-02-10")
        chain = [post.slug for post in loader.load_posts()]
        self.assertEqual(len(chain), 4)
        for index, slug in enumerate(chain):
            with self.subTest(slug=slug):
                older = chain[index + 1] if index + 1 < len(chain) else None
                newer = chain[index - 1] if index else None
                self.assertEqual(self.around(slug), (older, newer))


class ReadingMinutesTests(SimpleTestCase):
    def test_two_hundred_words_a_minute_rounded_up(self):
        for count, minutes in ((1, 1), (200, 1), (201, 2), (400, 2), (401, 3)):
            with self.subTest(words=count):
                self.assertEqual(loader.reading_minutes(words(count)), minutes)

    def test_a_post_with_no_words_still_takes_a_minute(self):
        self.assertEqual(loader.reading_minutes(""), 1)

    def test_the_marks_markdown_adds_are_not_words(self):
        body = "## Heading\n\n- " + words(199) + "\n\n---\n\n> \n\n| - | - |\n|---|---|\n"
        self.assertEqual(loader.reading_minutes(body), 1,
                         "backend/blog/loader.py: reading_minutes() counts only runs of characters that hold a letter or a digit")

    def test_code_is_read_too(self):
        body = "```\n" + words(200) + "\n```\n\nThen one more line."
        self.assertEqual(loader.reading_minutes(body), 2,
                         "backend/blog/loader.py: reading_minutes() counts the words in code blocks, which a reader reads")


class ImageSizeTests(FakeJournal, SimpleTestCase):
    def test_a_png_reports_its_width_and_height(self):
        (self.static_dir / "img" / "wide.png").write_bytes(png_header(1200, 630))
        self.assertEqual(loader.image_size("img/wide.png"), (1200, 630))

    def test_a_file_that_is_not_a_png_reports_nothing(self):
        (self.static_dir / "img" / "mark.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        self.assertIsNone(loader.image_size("img/mark.svg"))

    def test_a_png_cut_short_reports_nothing(self):
        (self.static_dir / "img" / "cut.png").write_bytes(png_header(10, 10)[:20])
        self.assertIsNone(loader.image_size("img/cut.png"))

    def test_a_missing_file_a_folder_or_nothing_reports_nothing(self):
        for name in ("img/nope.png", "img", "", "img/a\0b.png"):
            with self.subTest(name=name):
                self.assertIsNone(loader.image_size(name))

    def test_a_path_that_leaves_backend_static_reports_nothing(self):
        (self.static_dir.parent / "outside.png").write_bytes(png_header(10, 10))
        self.assertIsNone(loader.image_size("../outside.png"))
        self.assertIsNone(loader.image_size(str(self.static_dir.parent / "outside.png")))
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test blog.tests_loader`
Expected: every test ERRORs, none passes. The ones built on `FakeJournal` fail in `setUp` with `AttributeError: <module 'blog.loader' ...> does not have the attribute 'STATIC_DIR'`; `ReadingMinutesTests` fails with `AttributeError: module 'blog.loader' has no attribute 'reading_minutes'`.

- [ ] **Step 2: Teach the loader about neighbours, reading time and the size of a header image**

In `backend/blog/loader.py`, make these two replacements, then append the new functions. (Task 2 has already added `Post.image`, `Post.image_alt`, the two front matter lines in the module docstring and the lines in `load_posts()` that read them, and tests them in `pages/tests_reading_family.py`; do not add them again.)

1. Replace

```
import logging
import re
from dataclasses import dataclass
```

with

```
import logging
import math
import re
import struct
from dataclasses import dataclass
```

2. Replace

```
POSTS_DIR = Path(__file__).resolve().parent / "posts"
```

with

```
POSTS_DIR = Path(__file__).resolve().parent / "posts"
# Where a post's `image` is looked up, for image_size().
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
# How fast a reader is taken to read, for reading_minutes().
WORDS_PER_MINUTE = 200
```

3. Append to the end of the file (after `get_post`):

```python


def neighbours(slug):
    """(older, newer): the published posts either side of the post with this slug.

    Posts are taken in load_posts() order, newest first, so a post dated in the future is neither
    found nor anyone's neighbour. Either side is None at the end of the list, and both are None
    for a slug that is not published.
    """
    posts = load_posts()
    for index, post in enumerate(posts):
        if post.slug == slug:
            older = posts[index + 1] if index + 1 < len(posts) else None
            newer = posts[index - 1] if index > 0 else None
            return older, newer
    return None, None


def reading_minutes(text):
    """Whole minutes to read a post's Markdown at 200 words a minute, rounded up, never fewer than 1.

    A word is a run of characters between spaces that holds a letter or a digit, so the marks
    Markdown adds (#, -, >, |, ---, a fence) are not words. Code blocks count: a reader reads
    them too, and leaving them out would promise a shorter read than the page takes. The words
    are the Markdown's own, so the Copy button the page puts on a code block adds none.
    """
    words = sum(1 for token in text.split() if any(char.isalnum() for char in token))
    return max(1, math.ceil(words / WORDS_PER_MINUTE))


def image_size(name):
    """(width, height) of the PNG at backend/static/<name>, read from its header, else None.

    The post page puts these on the <img> so the browser keeps the space before the file arrives.
    None for a file that is missing, is not a PNG, or lies outside backend/static/ (the page then
    shows the image without a size).
    """
    try:
        path = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in path.parents:
            return None
        with path.open("rb") as handle:
            header = handle.read(24)
    except (OSError, ValueError):
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", header[16:24])
    return (width, height) if width and height else None
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test blog.tests_loader`
Expected: `Ran 16 tests` and `OK`.

- [ ] **Step 3: Write the failing page tests**

Create `backend/blog/tests_journal.py` (the outer fence has four backticks because `CODED_BODY` holds fenced code blocks):

````python
"""The Journal on the reading layout (spec 4.6): the index, a post, its pager, its header image and its code blocks."""
import re
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from blog.tests_loader import FakeJournal, png_header, words
from pages.tests_chapters import BANNED

BACKEND = Path(settings.BASE_DIR)
# A post with three fenced code blocks: the second holds markup and an ampersand, the third has no language.
CODED_BODY = """## Install

Run this first:

```bash
veilfile login --token "$TOKEN" && echo 'ok'
```

Markup in code is shown, not run:

```
<script>alert(1)</script>
<a href="/not-a-link">x</a> & more
  indented line
```

Then upload:

```
veilfile push report.pdf
```
"""
# The text of those three blocks, as a reader sees it and Copy puts it on the clipboard.
CODED_BLOCKS = [
    """veilfile login --token "$TOKEN" && echo 'ok'""",
    """<script>alert(1)</script>\n<a href="/not-a-link">x</a> & more\n  indented line""",
    "veilfile push report.pdf",
]


def main(html):
    """What a visitor reads: the page from <main> to </main>."""
    return html[html.index("<main"):html.index("</main>")]


class CopyParts(HTMLParser):
    """What the Copy tests read from a page: every id (repeats included), each Copy button (the id it names, its accessible
    name and its visible word) and the text of each <pre> that has an id, which is what copy.js puts on the clipboard (trimmed)."""

    def __init__(self):
        super().__init__()
        self.ids = []
        self.buttons = []
        self.pres = {}
        self.button = None
        self.pre = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.append(attributes["id"])
        if tag == "button" and "data-copy" in attributes:
            self.button = {"target": attributes["data-copy"], "label": attributes.get("aria-label", ""), "word": ""}
            self.buttons.append(self.button)
        if tag == "pre" and "id" in attributes:
            self.pre = attributes["id"]
            self.pres[self.pre] = ""

    def handle_endtag(self, tag):
        if tag == "button":
            self.button = None
        if tag == "pre":
            self.pre = None

    def handle_data(self, data):
        if self.button is not None:
            self.button["word"] += data
        if self.pre is not None:
            self.pres[self.pre] += data


class JournalIndexTests(FakeJournal, TestCase):
    longMessage = False  # a failure prints the message that names the file and the fix, not a page

    def setUp(self):
        super().setUp()
        self.write_post("first", "2026-01-10")
        self.write_post("second", "2026-02-10")
        self.write_post("third", "2026-03-10")

    def index(self):
        response = self.client.get("/blog/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_index_has_the_reading_layout_and_one_heading_named_journal(self):
        html = self.index()
        self.assertIn('<div class="reading">', html, "backend/templates/blog/index.html must extend layouts/reading.html")
        self.assertEqual(re.findall(r"<h1[ >].*?</h1>", main(html)), ["<h1>Journal</h1>"],
                         "backend/templates/blog/index.html: one <h1>Journal</h1> in <main>")

    def test_the_index_lists_every_post_newest_first_with_its_description_and_date(self):
        text = main(self.index())
        order = [text.index(f'href="/blog/{slug}/"') for slug in ("third", "second", "first")]
        self.assertEqual(order, sorted(order),
                         "backend/blog/views.py: blog_index must pass the posts newest first, as load_posts() lists them")
        self.assertIn("Third", text, "backend/templates/components/entries.html: show each entry's title")
        self.assertIn("The post called second.", text, "backend/templates/components/entries.html: show each entry's description")
        self.assertIn("2026-02-10", text,
                      "backend/templates/components/entries.html: write entry.date as {{ entry.date|date:'Y-m-d' }}; "
                      "the Journal hands it a date")

    def test_a_post_dated_in_the_future_is_not_listed(self):
        self.write_post("later", "2999-01-01")
        self.assertNotIn("/blog/later/", self.index(), "backend/blog/views.py: blog_index lists load_posts(), which hides future posts")

    def test_an_empty_journal_says_so(self):
        for path in self.posts_dir.glob("*.md"):
            path.unlink()
        text = main(self.index())
        self.assertIn("Nothing here yet.", text, "backend/templates/blog/index.html: an empty Journal says 'Nothing here yet.'")
        self.assertNotIn("<ol", text, "backend/templates/blog/index.html: an empty Journal shows no list")


class JournalPostTests(FakeJournal, TestCase):
    longMessage = False

    def setUp(self):
        super().setUp()
        self.write_post("first", "2026-01-10")
        self.write_post("second", "2026-02-10")
        self.write_post("third", "2026-03-10")

    def post(self, slug="second"):
        response = self.client.get(f"/blog/{slug}/")
        self.assertEqual(response.status_code, 200)
        return response

    def test_the_meta_line_comes_before_the_title_and_the_title_before_the_body(self):
        text = main(self.post().content.decode())
        found = re.search(r'<p class="reading__meta">.*?</p>', text)
        self.assertIsNotNone(found, 'backend/templates/blog/post.html: write the meta line as <p class="reading__meta">...</p>')
        meta = found.group(0)
        self.assertEqual(re.sub(r"<[^>]+>", "", meta), "Journal · 2026-02-10 · 1 min read")
        self.assertIn('<time datetime="2026-02-10">2026-02-10</time>', meta,
                      "backend/templates/blog/post.html: write the date as <time datetime=\"YYYY-MM-DD\">YYYY-MM-DD</time>")
        self.assertIn('<a href="/blog/">Journal</a>', meta,
                      "backend/templates/blog/post.html: the word Journal in the meta line links to the index "
                      "({% url 'blog-index' %}), the reader's way back")
        self.assertLess(text.index("<article>"), text.index(meta), "backend/templates/blog/post.html: the meta line is inside <article>")
        self.assertLess(text.index(meta), text.index("<h1>Second</h1>"), "backend/templates/blog/post.html: the meta line comes before the title")
        self.assertLess(text.index("<h1>Second</h1>"), text.index("<p>Body.</p>"), "backend/templates/blog/post.html: the title comes before the body")

    def test_the_reading_time_counts_the_words_of_the_body(self):
        self.write_post("long", "2026-04-10", body=words(450))
        self.assertIn(" · 3 min read</p>", main(self.post("long").content.decode()),
                      "backend/blog/views.py: pass minutes=reading_minutes(post.body_md); 450 words at 200 a minute is 3")

    def test_a_post_has_one_heading_in_main_and_no_header_or_footer_element(self):
        text = main(self.post().content.decode())
        self.assertEqual(len(re.findall(r"<h1[ >]", text)), 1, "backend/templates/blog/post.html: one <h1> in <main>")
        self.assertIsNone(re.search(r"<(header|footer)[ >]", text),
                          "backend/templates/blog/post.html: no <header> or <footer> inside <main>; base.css styles the bare elements")

    def test_the_index_and_a_post_keep_the_shells_skip_link_and_main(self):
        for path in ("/blog/", "/blog/second/"):
            with self.subTest(path=path):
                html = self.client.get(path).content.decode()
                self.assertIn('<a class="skip-link label" href="#main">', html,
                              "backend/templates/blog/: extend layouts/reading.html, which extends shell.html (the skip link)")
                self.assertIn('<main id="main" class="shell-main">', html,
                              "backend/templates/blog/: extend layouts/reading.html, which extends shell.html (<main>)")

    def test_headings_in_the_body_have_ids_and_a_post_has_no_section_list(self):
        self.write_post("headed", "2026-06-10", body="## Why\n\nBecause.\n\n## How\n\nLike this.")
        text = main(self.post("headed").content.decode())
        self.assertIn('<h2 id="why">Why</h2>', text, "backend/blog/views.py: render the body with render_reading, which gives headings ids")
        self.assertNotIn("Contents", text, "backend/templates/blog/post.html: a post has no section list")

    def test_previous_is_the_older_post_and_next_the_newer_one(self):
        response = self.post("second")
        self.assertEqual(response.context["previous"]["url"], "/blog/first/")
        self.assertEqual(response.context["next"]["url"], "/blog/third/")
        text = main(response.content.decode())
        self.assertIn('href="/blog/first/"', text, "backend/templates/blog/post.html: include components/pager.html with previous and next")
        self.assertIn('href="/blog/third/"', text, "backend/templates/blog/post.html: include components/pager.html with previous and next")

    def test_the_newest_post_has_no_next_and_the_oldest_no_previous(self):
        newest, oldest = self.post("third"), self.post("first")
        self.assertEqual(newest.context["previous"]["url"], "/blog/second/")
        self.assertIsNone(newest.context["next"])
        self.assertIsNone(oldest.context["previous"])
        self.assertEqual(oldest.context["next"]["url"], "/blog/second/")

    def test_a_post_dated_in_the_future_is_not_found_and_is_not_linked(self):
        self.write_post("later", "2999-01-01")
        self.assertEqual(self.client.get("/blog/later/").status_code, 404)
        newest = self.post("third")
        self.assertIsNone(newest.context["next"],
                          "backend/blog/loader.py: neighbours() must never return a post dated in the future")
        self.assertNotIn('href="/blog/later/"', newest.content.decode(),
                         "backend/blog/loader.py: neighbours() must never return a post dated in the future")

    def test_the_only_post_has_no_pager(self):
        (self.posts_dir / "first.md").unlink()
        (self.posts_dir / "third.md").unlink()
        self.assertNotIn("More to read", self.post("second").content.decode(),
                         "backend/templates/components/pager.html: nothing is shown when there is neither a previous nor a next post")

    def test_an_unknown_post_is_a_plain_404(self):
        self.assertEqual(self.client.get("/blog/nope/").status_code, 404)


class JournalCodeBlockTests(FakeJournal, TestCase):
    """A fenced code block in a post gets Copy from render_reading (components/code_block.html); nothing in the post's file or
    in templates/blog/post.html has to ask for it. The feed does not get it (blog/tests_feed.py)."""

    longMessage = False

    def setUp(self):
        super().setUp()
        self.write_post("coded", "2026-05-10", body=CODED_BODY)

    def page(self, slug="coded"):
        html = self.client.get(f"/blog/{slug}/").content.decode()
        parts = CopyParts()
        parts.feed(html)
        return html, parts

    def test_each_fenced_block_has_a_copy_button_that_copies_exactly_its_text(self):
        _, parts = self.page()
        self.assertEqual(len(parts.buttons), len(CODED_BLOCKS),
                         "backend/blog/render.py: render_reading must put a Copy button (components/code_block.html) on each fenced "
                         "code block, and backend/templates/blog/post.html must print its html")
        for button, expected in zip(parts.buttons, CODED_BLOCKS):
            with self.subTest(code=expected[:24]):
                self.assertEqual(button["word"], "Copy", "backend/templates/components/code_block.html: the button's visible word is Copy")
                self.assertTrue(button["label"].startswith("Copy ") and len(button["label"]) > len("Copy "),
                                "backend/blog/render.py: the label render_reading gives each code block names what Copy copies, "
                                f"as in 'Copy the code'; this one is {button['label']!r}")
                self.assertIn(button["target"], parts.pres,
                              f"backend/blog/render.py: the button names the id {button['target']!r}, which is no <pre id> on the page")
                self.assertEqual(parts.pres[button["target"]].strip(), expected,
                                 "backend/blog/render.py: Copy must put the block's text on the clipboard exactly as written; "
                                 "pass the component the code unescaped (it escapes the code itself)")

    def test_each_block_is_in_the_article_with_the_status_copy_speaks(self):
        html, _ = self.page()
        article = main(html)
        article = article[article.index("<article>"):article.index("</article>")]
        self.assertEqual(article.count("<div class=\"code-block\" data-code-block>"), len(CODED_BLOCKS),
                         "backend/blog/render.py: each fenced code block is a components/code_block.html inside the post's <article>")
        self.assertEqual(article.count("data-copy-status"), len(CODED_BLOCKS),
                         "backend/templates/components/code_block.html: copy.js finds its status inside the block's [data-code-block]")

    def test_no_id_on_the_page_repeats_and_code_is_shown_not_run(self):
        html, parts = self.page()
        repeated = sorted({value for value in parts.ids if parts.ids.count(value) > 1})
        self.assertEqual(repeated, [],
                         f"backend/blog/render.py: the page holds the id {repeated} more than once; each code block's id must be unique "
                         "on the page, and different from the ids of the headings and of the shell")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html, "backend/blog/render.py: markup in a code block is shown as text")
        self.assertNotIn("<script>alert(1)", html, "backend/blog/render.py: markup in a code block must be escaped, never live")

    def test_the_reading_time_is_the_markdowns_and_the_copy_buttons_add_nothing_to_it(self):
        # 200 words are one minute; a page that counted the word Copy of the button would say two.
        self.write_post("exact", "2026-05-11", body="```\n" + words(200) + "\n```")
        found = re.search(r'<p class="reading__meta">.*?</p>', main(self.page("exact")[0]))
        self.assertIsNotNone(found, 'backend/templates/blog/post.html: write the meta line as <p class="reading__meta">...</p>')
        self.assertTrue(found.group(0).endswith(" · 1 min read</p>"),
                        "backend/blog/loader.py: reading_minutes() counts the words of the Markdown body, not of the page")

    def test_a_post_without_code_and_the_index_have_no_copy_button(self):
        self.write_post("plain", "2026-05-12")
        for path in ("/blog/plain/", "/blog/"):
            with self.subTest(path=path):
                html = self.client.get(path).content.decode()
                self.assertNotIn("data-copy", html, "backend/blog/render.py: a Copy button appears only on a fenced code block")


class JournalHeaderImageTests(FakeJournal, TestCase):
    longMessage = False

    def test_the_header_image_follows_the_title_with_its_alt_text_and_size(self):
        (self.static_dir / "img" / "checkout.png").write_bytes(png_header(1200, 630))
        self.write_post("shot", "2026-05-10", image="img/checkout.png",
                        image_alt="A failing checkout page: the total is wrong.")
        text = main(self.client.get("/blog/shot/").content.decode())
        image = ('<img class="reading__image" src="/static/img/checkout.png" '
                 'alt="A failing checkout page: the total is wrong." width="1200" height="630">')
        self.assertIn(image, text, "backend/templates/blog/post.html: show the header image with its alt text and, for a PNG, its width and height")
        self.assertLess(text.index("<article>"), text.index("<h1>Shot</h1>"))
        self.assertLess(text.index("<h1>Shot</h1>"), text.index(image), "backend/templates/blog/post.html: the header image follows the title")
        self.assertLess(text.index(image), text.index("<p>Body.</p>"), "backend/templates/blog/post.html: the header image comes before the body")
        self.assertLess(text.index(image), text.index("</article>"))

    def test_an_image_whose_size_is_unknown_is_shown_without_one(self):
        (self.static_dir / "img" / "mark.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        self.write_post("mark", "2026-05-10", image="img/mark.svg", image_alt="The mark.")
        text = main(self.client.get("/blog/mark/").content.decode())
        self.assertIn('<img class="reading__image" src="/static/img/mark.svg" alt="The mark.">', text,
                      "backend/templates/blog/post.html: write width and height only when image_size is known")

    def test_a_post_with_no_image_shows_none(self):
        self.write_post("plain", "2026-05-10")
        self.assertNotIn("<img", main(self.client.get("/blog/plain/").content.decode()),
                         "backend/templates/blog/post.html: show an <img> only when the post has an image")


class JournalTemplateRulesTests(SimpleTestCase):
    longMessage = False

    def test_the_header_image_keeps_its_shape_when_the_column_narrows(self):
        css = (BACKEND / "static" / "css" / "base.css").read_text(encoding="utf-8")
        rule = re.search(r"\.reading__image\s*{([^}]*)}", css)
        self.assertIsNotNone(rule, "backend/static/css/base.css: add a .reading__image rule (templates/blog/post.html uses the class)")
        self.assertIn("height: auto", rule.group(1),
                      "backend/static/css/base.css: .reading__image needs height: auto, or its height attribute "
                      "stretches the picture when max-width narrows it")

    def test_the_words_the_templates_add_follow_the_voice_rules(self):
        for path in sorted((BACKEND / "templates" / "blog").glob("*.html")):
            source = re.sub(r"{#.*?#}|{%.*?%}|{{.*?}}", " ", path.read_text(encoding="utf-8"))
            text = re.sub(r"<[^>]+>", " ", source)
            with self.subTest(template=path.name):
                self.assertNotIn("—", text, f"backend/templates/blog/{path.name}: no em dashes; use a comma, a colon or a full stop")
                self.assertNotIn("!", text, f"backend/templates/blog/{path.name}: no exclamation marks")
                for word in BANNED:
                    self.assertNotIn(word, text.lower(), f"backend/templates/blog/{path.name}: do not write '{word.strip()}'")
````

`JournalCodeBlockTests` is the Copy contract for a post: each fenced block is a `components/code_block.html` inside the `<article>`; its button's `data-copy` names a `<pre id>` whose trimmed text is the fence's text exactly (so markup in a block is escaped once, not twice, and never live); no id repeats on the page; and a block of exactly 200 words still reads as one minute, because the reading time counts the Markdown and not the word "Copy".

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test blog.tests_journal`
Expected: `Ran 24 tests`, `FAILED (failures=12, errors=3)`. Fourteen of the 24 tests fail or error against the old pages (that is 15 reports, because the skip link test reports once for each of its two pages), each with the message that names the file and the fix:
- the layout test (`backend/templates/blog/index.html must extend layouts/reading.html`);
- the meta line and reading time tests (the old page has no `reading__meta` paragraph), the skip link test and the heading id test;
- the two header image tests that expect an `<img>`;
- the CSS test (`.reading__image` is not styled yet);
- the three pager tests (`KeyError: 'previous'` and `KeyError: 'next'`, because the old view passes neither);
- the two Copy tests (`backend/blog/render.py: render_reading must put a Copy button ...`, because the old view prints `render_markdown`'s plain `<pre>`), and the exact 200 words reading time test (the old page has no meta line to read).

The other 10 already pass: the index listing the posts newest first with their description and date, a future post not listed, the empty index, one `h1` in `<main>` with no `header` or `footer`, the plain 404, a lone post with no pager, markup in a code block shown as text with no id repeated (there is no Copy yet to repeat one), no Copy button on a post without code or on the index, no image on a post without one, and the voice rules over the old templates.

- [ ] **Step 4: The views**

Replace the whole of `backend/blog/views.py` with:

```python
"""Journal views: the index, one post, and the retired addresses."""

from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from blog.loader import get_post, image_size, load_posts, neighbours, reading_minutes
from blog.render import render_reading

# The first post was replaced on 2026-10-05 (founder decision: the old
# name must not appear anywhere user-facing). Keep the old URL working.
RETIRED_SLUGS = {"renaming-deadrop-to-veilfile": "introducing-veilfile"}


def retired_post_redirect(request, slug):
    return redirect("blog-post", slug=RETIRED_SLUGS[slug], permanent=True)


def _entry(post):
    """One post as components/entries.html and components/pager.html take it."""
    return {
        "url": reverse("blog-post", args=[post.slug]),
        "title": post.title,
        "description": post.description,
        "date": post.date,
    }


def blog_index(request):
    return render(request, "blog/index.html", {"entries": [_entry(post) for post in load_posts()]})


def blog_post(request, slug):
    post = get_post(slug)
    if post is None:
        raise Http404("No such post.")
    older, newer = neighbours(slug)
    return render(
        request,
        "blog/post.html",
        {
            "post": post,
            "reading": render_reading(post.body_md),
            "minutes": reading_minutes(post.body_md),
            # (width, height) of a PNG header image, so the page keeps its space before the file
            # arrives; None for any other format (the image is shown without a size).
            "image_size": image_size(post.image) if post.image else None,
            # The pager's "Previous" is the older post and "Next" the newer one.
            "previous": _entry(older) if older else None,
            "next": _entry(newer) if newer else None,
        },
    )
```

- [ ] **Step 5: The two templates**

Replace the whole of `backend/templates/blog/index.html` with:

```django
{% extends "layouts/reading.html" %}
{# Page: the Journal's list of posts. Context: entries, one {url, title, description, date} for each published post, newest first. #}
{% block reading %}
<h1>Journal</h1>
{% if entries %}{% include "components/entries.html" with entries=entries only %}{% else %}<p>Nothing here yet.</p>{% endif %}
{% endblock %}
```

Replace the whole of `backend/templates/blog/post.html` with:

```django
{% extends "layouts/reading.html" %}
{% load static %}
{# Page: one Journal post. Context: post, minutes, reading (from render_reading), image_size (width, height) or None, previous and next ({url, title}) or None. #}
{% block reading %}
<article>
<p class="reading__meta"><a href="{% url 'blog-index' %}">Journal</a> · <time datetime="{{ post.date|date:'Y-m-d' }}">{{ post.date|date:'Y-m-d' }}</time> · {{ minutes }} min read</p>
<h1>{{ post.title }}</h1>
{% if post.image %}<img class="reading__image" src="{% static post.image %}" alt="{{ post.image_alt }}"{% if image_size %} width="{{ image_size.0 }}" height="{{ image_size.1 }}"{% endif %}>
{% endif %}{{ reading.html|safe }}
</article>
{% include "components/pager.html" with previous=previous next=next only %}
{% endblock %}
```

Each `{# #}` comment is on one line (Django 6.1 prints a multi-line one as text). The old inline styles, the old menu and ambient includes, the footer links and the hard-coded head are gone: the shell and the registry supply them. The word Journal in the meta line links to the index, the reader's way back; the pager leads only to the neighbouring posts. Copy on a post's code blocks needs nothing in either template: `reading.html` already holds each fenced block as a `components/code_block.html`.

- [ ] **Step 6: Style the header image**

`base.css` already gives every `img` `display: block; max-width: 100%`. With the `width` and `height` attributes on the tag, that alone would squash a wide picture on a phone (the height would stay fixed while the width shrinks), so add the one rule that keeps its shape. Run from the repo root:

```bash
cat >> backend/static/css/base.css <<'EOF'

/* A Journal post's header image (templates/blog/post.html). Its width and height attributes give the browser the
   picture's shape before the file arrives; height: auto keeps that shape when max-width (above) narrows the
   picture to the column. */
.reading__image {
  height: auto;
  margin: 0 0 var(--space-4);
}
EOF
```

- [ ] **Step 7: Move the old-markup tests**

These tests asserted the old Journal markup (a standalone page with its own menu, ambient blobs, footer and skip link). The Journal is now a shell page, so each is changed or removed. The behaviour they protected has a new test above: the skip link and `<main>` in `test_the_index_and_a_post_keep_the_shells_skip_link_and_main`, one `h1` and no `header` or `footer` in `<main>`, the menu and footer in `pages/tests_shell.py`.

(a) `backend/drop_core/tests.py`, `AccessibilityTests.test_skip_link_and_main_id_on_django_pages`. The list asserts `<a class="skip-link" href="#main">` and `<main id="main">`, which the shell writes differently (`class="skip-link label"`, `class="shell-main"`). Tasks 4 and 5 have already taken the legal pages and `"/docs/quickstart/",` out of it. Replace these two lines (twelve spaces of indent) with nothing, so that both lines go:

```python
            "/blog/",
            "/blog/introducing-veilfile/",
```

The list is then `"/accounts/signup/"`, `"/accounts/login/"` and `"/accounts/password_reset/"`, the pages that still have the old markup.

(b) Same file, `BlogTests.test_feed_is_valid_rss`. It passes today only because it pins neither the feed's name nor its origin. The version below pins both, so it fails (`'Veilfile Blog' != 'Veilfile Journal'`) until Step 11 replaces the feed, and Step 9 leaves it out of its run. Replace

```python
    def test_feed_is_valid_rss(self):
        response = self.client.get("/blog/feed.xml")
        self.assertEqual(response.status_code, 200)
        self.assertIn("rss", response["Content-Type"])
        self.assertContains(response, "Introducing Veilfile")
        self.assertContains(response, "/blog/introducing-veilfile/")
        self.assertNotContains(response, "renaming-deadrop-to-veilfile")
```

with

```python
    def test_feed_is_valid_rss(self):
        from xml.etree import ElementTree

        response = self.client.get("/blog/feed.xml")
        self.assertEqual(response.status_code, 200)
        self.assertIn("rss", response["Content-Type"])
        channel = ElementTree.fromstring(response.content).find("channel")
        self.assertEqual(channel.findtext("title"), "Veilfile Journal")
        self.assertContains(response, "Introducing Veilfile")
        self.assertContains(response, "https://veilfile.com/blog/introducing-veilfile/")
        self.assertNotContains(response, "renaming-deadrop-to-veilfile")
```

(c) `backend/drop_core/tests_menu.py`. The shell has its own menu and footer (`site-menu-wrap`, `role="menuitem"` and the three-link footer are the old pages' markup). Delete the line `    "/blog/",` from `MENU_PAGES` and from `FOOTER_PAGES`; it appears exactly twice in the file, once in each list. Leave `("/blog/", "blog"),` in `MENU_LINKS`: the old menu on the sign-in and sign-up pages still links the Journal.

(d) `backend/drop_core/tests_ambient.py`. The ambient blobs, theme boot include and veil belong to the old pages.
- Replace

```python
from django.test import TestCase

from blog.loader import load_posts

# (url, expect_veil)
```

with

```python
from django.test import TestCase

# (url, expect_veil)
```

- Delete the line `    ("/blog/", True),` from `FIELD_PAGES`.
- Delete this whole test from `AmbientFieldTests`, with the blank line after it:

```python
    def test_blog_post_gets_field_and_veil(self):
        slug = load_posts()[0].slug
        html = _html(self.client.get(f"/blog/{slug}/"))
        self.assertIn('data-ambient="field"', html)
        self.assertIn('class="veil"', html)

```

- In `NoTemplateCommentLeakTests`, which is still worth running on the new templates (a multi-line `{# #}` comment would print as text), replace

```python
        urls.append(f"/blog/{load_posts()[0].slug}/")
```

with

```python
        urls.append("/blog/")
        urls.append(f"/blog/{load_posts()[0].slug}/")
```

(e) The rules for shell templates (no inline `style`, components included with `only`, every character in the shipped fonts) apply to the folders in one tuple that two files scan: `shell_templates` in `backend/pages/tests_site_rules.py` and `RealContentTests.test_every_shell_template` in `backend/pages/tests_content.py`. Tasks 4 and 5 each added their folder (`legal`, `docs`); add the Journal's. In both files, replace

```python
("components", "layouts", "chapters", "pages", "legal", "docs")
```

with

```python
("components", "layouts", "chapters", "pages", "legal", "docs", "blog")
```

The Journal's two templates already obey all three rules (no `style`, each component included with `only`, and the only character outside ASCII, the middle dot of the meta line, is in all three shipped faces, as the motion switch's `· on` in `components/controls.html` already shows), so the new tuple simply brings the folder under the rules and the run stays green:

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules pages.tests_content`
Expected: `OK`.

- [ ] **Step 8: Retire the old footer tests**

Tasks 4 and 5 already took the legal and Docs pages out of `FOOTER_PAGES`; with `/blog/` gone from it in Step 7, no page is left that has the old three-link footer. A list that names no page checks nothing, so remove the checks themselves. In `backend/drop_core/tests_menu.py`:
- delete `FOOTER_PAGES`, the function `_footer`, the whole class `TrimmedFooterTests` (the last class in the file) and the line `import re`, which nothing else uses, and leave two blank lines before the next top-level `def` (deleting `FOOTER_PAGES` with its blank lines leaves one before `def _html`);
- replace the module docstring

```python
"""Hamburger menu on every Django page + trimmed compliance footers.

Founder direction 2026-10-06: the hamburger menu carries every destination,
so page footers keep only the compliance minimum (terms, privacy, report
abuse). The menu ships as one shared include, mirroring the audited
MenuButton on the React landing: icon button top right, Escape and
outside-click close, focus returns to the button.
"""
```

with

```python
"""Hamburger menu on the Django pages that are not on the shell yet (sign in, sign up).

Founder direction 2026-10-06: the hamburger menu carries every destination. The menu ships as one
shared include (templates/includes/menu.html), mirroring the audited MenuButton on the React landing:
icon button top right, Escape and outside-click close, focus returns to the button. Pages on the shell
have their own menu (pages/tests_shell.py), and their footer holds only the two switches.
"""
```

- [ ] **Step 9: Run the Journal's page tests and everything that touched these pages**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test blog.tests_loader blog.tests_journal drop_core.tests_menu drop_core.tests_ambient drop_core.tests.AccessibilityTests drop_core.tests_fix_web pages.tests_registry pages.tests_sitemap pages.tests_site_rules pages.tests_content pages.tests_head`
Expected: `OK` (`drop_core.tests.BlogTests` is not in this run: the feed test of Step 7 (b) fails until Step 11, whose run holds it). If `test_the_index_lists_every_post_newest_first_with_its_description_and_date` fails on the date, the message names `components/entries.html`: Task 3's component must write `entry.date` as `{{ entry.date|date:'Y-m-d' }}`.

- [ ] **Step 10: Write the failing feed tests**

Create `backend/blog/tests_feed.py` (the outer fence has four backticks because `BODY` holds a fenced code block):

````python
"""The Journal's RSS feed (spec 6.6): every post in full, every address absolute on the public origin."""
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from blog.feeds import absolute_links
from blog.loader import get_post
from blog.render import render_markdown
from blog.tests_loader import FakeJournal
from pages import registry

BACKEND = Path(settings.BASE_DIR)
REPO = BACKEND.parent
ORIGIN = registry.SITE_ORIGIN
CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
ATOM = "{http://www.w3.org/2005/Atom}"
BODY = """## Why

Read the [docs](/docs/), jump to [the end](#end) or see ![A shot](/static/img/shot.png).
Write <https://example.com/a> to us, or [mail us](mailto:hi@example.com). The text `href="/not-a-link"` stays.

```
<a href="/also-not-a-link">code</a>
```

## End
"""


class Links(HTMLParser):
    """The (tag, address) of every <a href> and <img src> in some HTML."""

    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a" and "href" in attributes:
            self.found.append((tag, attributes["href"]))
        if tag == "img" and "src" in attributes:
            self.found.append((tag, attributes["src"]))


class FeedTests(FakeJournal, TestCase):
    longMessage = False  # a failure prints the message that names the file and the fix, not a feed

    def setUp(self):
        super().setUp()
        self.write_post("first", "2026-01-10", body=BODY)
        self.write_post("second", "2026-02-10")

    def feed(self):
        response = self.client.get("/blog/feed.xml")
        self.assertEqual(response.status_code, 200)
        self.assertIn("rss", response["Content-Type"])
        return response, ElementTree.fromstring(response.content).find("channel")

    def item(self, channel, title):
        return next(item for item in channel.findall("item") if item.findtext("title") == title)

    def test_the_feed_is_titled_veilfile_journal_and_describes_the_journal(self):
        _, channel = self.feed()
        self.assertEqual(channel.findtext("title"), "Veilfile Journal")
        self.assertEqual(channel.findtext("description"), "Notes on Veilfile: launches, changes, and build notes.")

    def test_the_feed_declares_the_content_module_and_every_item_carries_the_full_post(self):
        response, channel = self.feed()
        self.assertIn('xmlns:content="http://purl.org/rss/1.0/modules/content/"', response.content.decode(),
                      "backend/blog/feeds.py: ContentFeed.rss_attributes must declare the content namespace on <rss>")
        first = self.item(channel, "First")
        self.assertEqual(first.findtext("description"), "The post called first.",
                         "backend/blog/feeds.py: an item's <description> is the post's description")
        full = first.findtext(CONTENT)
        message = "backend/blog/feeds.py: an item's <content:encoded> is the whole post, from item_extra_kwargs"
        self.assertIn("<h2>Why</h2>", full, message)
        self.assertIn("<h2>End</h2>", full, message)
        self.assertIn("Read the", full, message)
        self.assertEqual(self.item(channel, "Second").findtext(CONTENT), "<p>Body.</p>", message)

    def test_every_address_the_feed_gives_is_absolute_on_the_public_origin(self):
        response, channel = self.feed()
        addresses = [channel.findtext("link"), channel.find(f"{ATOM}link").get("href")]
        for item in channel.findall("item"):
            addresses += [item.findtext("link"), item.findtext("guid")]
        self.assertEqual(len(addresses), 6)
        for address in addresses:
            self.assertTrue(address.startswith(ORIGIN + "/"),
                            f"backend/blog/feeds.py: {address} must start with registry.SITE_ORIGIN, not the request's host")
        self.assertNotIn("testserver", response.content.decode(),
                         "backend/blog/feeds.py: build every address from registry.SITE_ORIGIN, never from the request's host")

    def test_each_guid_is_the_post_address_and_says_so(self):
        _, channel = self.feed()
        for item in channel.findall("item"):
            guid = item.find("guid")
            self.assertEqual(guid.text, item.findtext("link"))
            self.assertEqual(guid.get("isPermaLink"), "true")

    def test_links_and_images_inside_a_post_are_absolute_too(self):
        _, channel = self.feed()
        full = self.item(channel, "First").findtext(CONTENT)
        links = Links()
        links.feed(full)
        self.assertEqual(links.found, [
            ("a", f"{ORIGIN}/docs/"),
            ("a", f"{ORIGIN}/blog/first/#end"),
            ("img", f"{ORIGIN}/static/img/shot.png"),
            ("a", "https://example.com/a"),
            ("a", "mailto:hi@example.com"),
        ])
        self.assertIn('<code>href="/not-a-link"</code>', full, "backend/blog/feeds.py: absolute_links must change real <a> and <img> tags only")
        self.assertIn("&lt;a href=&quot;/also-not-a-link&quot;&gt;", full, "backend/blog/feeds.py: absolute_links must leave code blocks alone")

    def test_the_feed_carries_each_post_as_render_markdown_makes_it_with_no_copy_buttons(self):
        _, channel = self.feed()
        full = self.item(channel, "First").findtext(CONTENT)
        expected = absolute_links(render_markdown(get_post("first").body_md), f"{ORIGIN}/blog/first/")
        self.assertEqual(full, expected,
                         "backend/blog/feeds.py: build each item's content_html from render_markdown, as the feed always has: "
                         "render_reading is for pages and puts a Copy button on each fenced code block")
        for marker in ("data-copy", "data-code-block", "code-block", "<button", ">Copy<"):
            self.assertNotIn(marker, full,
                             f"backend/blog/feeds.py: found {marker!r} in an item. A feed reader runs no script, so Copy has no place "
                             "in the feed: use render_markdown there")
        self.assertIn("<pre", full, "backend/blog/feeds.py: the fenced code block itself stays in the item")

    def test_the_feed_lists_the_twenty_newest_published_posts_newest_first(self):
        for day in range(1, 23):
            self.write_post(f"march-{day:02d}", f"2026-03-{day:02d}")
        self.write_post("later", "2999-01-01")
        _, channel = self.feed()
        titles = [item.findtext("title") for item in channel.findall("item")]
        self.assertEqual(len(titles), 20, "backend/blog/feeds.py: the feed lists the FEED_ITEMS (20) newest posts")
        self.assertEqual(titles[:3], ["March 22", "March 21", "March 20"], "backend/blog/feeds.py: newest first")
        self.assertNotIn("First", titles, "backend/blog/feeds.py: the feed lists the FEED_ITEMS (20) newest posts")
        self.assertNotIn("Later", titles, "backend/blog/feeds.py: the feed must list load_posts(), which hides future posts")


class AbsoluteLinksTests(SimpleTestCase):
    longMessage = False

    PAGE = f"{ORIGIN}/blog/a-post/"

    def test_an_address_is_made_absolute_against_the_page(self):
        for html, expected in (
            ('<a href="/docs/">x</a>', f'<a href="{ORIGIN}/docs/">x</a>'),
            ('<a href="#why">x</a>', f'<a href="{self.PAGE}#why">x</a>'),
            ('<img alt="a" src="shot.png" />', f'<img alt="a" src="{self.PAGE}shot.png" />'),
            ('<a href="//cdn.example.com/x">x</a>', '<a href="https://cdn.example.com/x">x</a>'),
        ):
            with self.subTest(html=html):
                self.assertEqual(absolute_links(html, self.PAGE), expected)

    def test_absolute_addresses_and_mail_links_are_left_alone(self):
        html = '<a href="https://example.com/a?b=1&amp;c=2">x</a> <a href="mailto:hi@example.com">y</a>'
        self.assertEqual(absolute_links(html, self.PAGE), html)

    def test_text_that_only_looks_like_a_tag_attribute_is_left_alone(self):
        html = '<p>Use <code>href="/x"</code> and <code>&lt;a href=&quot;/y&quot;&gt;</code>.</p>'
        self.assertEqual(absolute_links(html, self.PAGE), html)


class NamingTests(SimpleTestCase):
    longMessage = False

    def test_nothing_calls_the_feed_or_the_journal_veilfile_blog(self):
        files = [REPO / "frontend" / "index.html", BACKEND / "blog" / "feeds.py", *sorted((BACKEND / "templates").rglob("*.html"))]
        for path in files:
            with self.subTest(file=str(path.relative_to(REPO))):
                self.assertNotIn("veilfile blog", path.read_text(encoding="utf-8").lower(),
                                 f"{path.relative_to(REPO)}: the feed and the page are called Veilfile Journal; change the name")
````

`test_the_feed_carries_each_post_as_render_markdown_makes_it_with_no_copy_buttons` holds the feed to `render_markdown`: an item must equal the post rendered that way (with its addresses made absolute) and hold no `data-copy`, no `data-code-block`, no `<button` and no Copy, because the Copy buttons of the pages need a script that no feed reader runs. The classes set `longMessage = False`, as in `tests_journal.py`.

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test blog.tests_feed`
Expected: one ERROR, `ImportError: cannot import name 'absolute_links' from 'blog.feeds'`, and no test runs, because the module fails to import.

- [ ] **Step 11: The feed**

Replace the whole of `backend/blog/feeds.py` with:

```python
"""The Journal's RSS feed: every post in full, every address absolute on the public origin."""

import re
from datetime import datetime, time, timezone
from urllib.parse import urljoin

from django.contrib.syndication.views import Feed
from django.urls import reverse
from django.utils.feedgenerator import Rss201rev2Feed

from blog.loader import load_posts
from blog.render import render_markdown
from pages import registry

FEED_ITEMS = 20
CONTENT_NAMESPACE = "http://purl.org/rss/1.0/modules/content/"

# Only the attributes of real <a> and <img> tags: inline code such as `href="/x"` is text, not a link.
_TAG_RE = re.compile(r"<(?:a|img)\b[^>]*>")
_ATTRIBUTE_RE = re.compile(r'(?<![\w-])(href|src)="([^"]*)"')


def absolute_links(html, page_url):
    """html with the address in every <a href> and <img src> made absolute against page_url.

    A feed reader does not know which site a relative address belongs to, so /docs/, #why and
    /static/img/shot.png in a post become https://veilfile.com/docs/, the post's own address with
    #why, and https://veilfile.com/static/img/shot.png. Addresses that are already absolute
    (https://..., mailto:...) are left as they are.
    """

    def one_tag(tag):
        return _ATTRIBUTE_RE.sub(
            lambda attribute: f'{attribute.group(1)}="{urljoin(page_url, attribute.group(2))}"', tag.group(0))

    return _TAG_RE.sub(one_tag, html)


class ContentFeed(Rss201rev2Feed):
    """RSS 2.0 with the content module: an item's full HTML goes in <content:encoded>."""

    def rss_attributes(self):
        return {**super().rss_attributes(), "xmlns:content": CONTENT_NAMESPACE}

    def add_item_elements(self, handler, item):
        super().add_item_elements(handler, item)
        if item.get("content_html"):
            handler.addQuickElement("content:encoded", item["content_html"])


class BlogFeed(Feed):
    """/blog/feed.xml. Each item's <description> is the post's description and <content:encoded> its
    whole text. Every address is built from registry.SITE_ORIGIN, the origin the canonical addresses
    use, so a reader gets the same address whatever host asked for the feed."""

    feed_type = ContentFeed
    title = "Veilfile Journal"
    description = "Notes on Veilfile: launches, changes, and build notes."

    def link(self):
        return registry.SITE_ORIGIN + reverse("blog-index")

    def feed_url(self):
        return registry.SITE_ORIGIN + reverse("blog-feed")

    def items(self):
        return load_posts()[:FEED_ITEMS]

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.description

    def item_pubdate(self, item):
        return datetime.combine(item.date, time.min).replace(tzinfo=timezone.utc)

    def item_link(self, item):
        return registry.SITE_ORIGIN + reverse("blog-post", args=[item.slug])

    def item_guid(self, item):
        return self.item_link(item)

    item_guid_is_permalink = True

    def item_extra_kwargs(self, item):
        # render_markdown, not render_reading: the pages' Copy buttons need a script, which no feed reader runs.
        return {"content_html": absolute_links(render_markdown(item.body_md), self.item_link(item))}
```

(The feed no longer reads `settings.DROP_BASE_URL`, which is the address of the API and OAuth endpoints and differs between a local run and production; the canonical origin does not. It keeps `render_markdown` on purpose, as the comment in `item_extra_kwargs` says, and the test above holds it to that.)

In `frontend/index.html` (the React app's head), replace

```html
title="Veilfile Blog" href="https://veilfile.com/blog/feed.xml"
```

with

```html
title="Veilfile Journal" href="https://veilfile.com/blog/feed.xml"
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test blog drop_core.tests.BlogTests drop_core.tests_fix_web`
Expected: `OK` (the `blog` label runs `tests_loader`, `tests_journal` and `tests_feed`).

- [ ] **Step 12: Make AGENTS.md true again**

In `AGENTS.md`, "Write a Journal post", step 2, replace

```
     It needs `image_alt`, one sentence describing the image. The header image is
     not shown yet (the Journal's new reading pages will show it); images in the
     body, step 3, are shown today.
```

with

```
     It needs `image_alt`, one sentence describing the image. The post page shows
     it under the title. Use a PNG: the page reads the size from the file and
     keeps the space for it, so the text does not jump when the image arrives;
     another format is shown too, without that.
   - The page adds "Journal · date · N min read" above the title itself. The
     reading time is the words in the body at 200 a minute (code blocks count),
     so write it nowhere.
   - Each fenced code block in the body gets a Copy button on the page, which
     copies the fence's text. Write nothing for it.
```

- [ ] **Step 13: The content guard reads the Journal as it is served**

`backend/drop_core/tests_fix_web.py`, `ContentGuardTests._rendered_content`: the guard against active HTML (no script, event handlers or `javascript:` URLs) must read what the post pages now print. Tasks 4 and 5 switched the legal and Docs lines; this is the last one.

1. Replace `            yield f"blog/{path.name}", render_markdown(body)` with `            yield f"blog/{path.name}", render_reading(body).html`.
2. `render_markdown` is then no longer used in `_rendered_content`: replace the import line Task 4 left there, `        from blog.render import render_markdown, render_reading`, with `        from blog.render import render_reading`.

Each quoted text occurs once in the file (check with `grep -c`). Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_fix_web.ContentGuardTests`
Expected: OK.

- [ ] **Step 14: Run the whole suite**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: `OK` (about 3.5 minutes; the count changes, do not compare it).

- [ ] **Step 15: Commit**

```bash
git add backend/drop_core/tests_fix_web.py backend/blog/loader.py backend/blog/views.py backend/blog/feeds.py backend/blog/tests_loader.py backend/blog/tests_journal.py backend/blog/tests_feed.py \
  backend/templates/blog/index.html backend/templates/blog/post.html backend/static/css/base.css frontend/index.html AGENTS.md \
  backend/drop_core/tests.py backend/drop_core/tests_menu.py backend/drop_core/tests_ambient.py backend/pages/tests_site_rules.py backend/pages/tests_content.py
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -m "The Journal moves onto the reading layout: reading time, previous and next, the header image, and a feed that carries each post in full from the public origin"
```

---

---

### Task 7: Browser checks for the reading pages, and the `site-reading` CI job

**Files:**
- Modify: `site-tests/run_site_checks.py`, `.github/workflows/ci.yml`, `AGENTS.md` (the repo-map line for `site-tests/`, the "Site checks" section, the "CI must be green" paragraph), `README.md` (two lines), `docs/superpowers/specs/2026-10-08-site-redesign-design.md` (sections 10, 11 and 13: the CI jobs), `site-tests/requirements.txt` and `site-tests/package.json` (one comment each)
- Modify (tests): `backend/pages/tests_site_parts.py`

This task makes every "three jobs" to "four jobs" edit in the docs, `ci.yml`, the `site-tests/` comments and `tests_site_parts.py` (`site`, `site-cloth` and `site-ink` become those three and `site-reading`); Task 8 makes none of them.

**Interfaces:**
- Consumes (the pages and markup of Tasks 1 to 6; the checks read exactly these, and a message names the file to fix when one is missing):
  - Task 2: `<body data-layout="reading">` with the class `layout-reading`; every reading page, posts and Docs pages included, in the family's `data-layout-paths`; one `<link rel="canonical">`, one Organization and, on a post or a Docs page, one `BlogPosting` or `TechArticle` JSON-LD script, all with `data-page-meta` in the head.
  - Task 3: `<div class="reading">` inside `<main>`, with the page's one `h1` in `<main>`; `<nav>` "Contents" (links `#id`) and `<nav>` "More to read" (its links carry `rel="prev"` and `rel="next"`; the word Previous or Next is a separate label beside the link, so a link's side is its `rel`, never its text, which is the neighbour's title), each found by its accessible name; reading CSS that wraps long words and URLs and lets `.table-scroll` scroll in its own box, and a `scroll-margin-top` on `h2` and `h3` (a code block's `pre` scrolls in its own box by `static/css/components/code_block.css`, in a grid column of `minmax(0, 1fr)`, which Task 3 does not touch).
  - Task 1: an `id` on every `h2`, and every fenced code block drawn by `components/code_block.html`: a `.code-block` around a `pre` with an id of its own on the page, one Copy button (`[data-copy]` naming that id, an `aria-label` starting "Copy ") and one `[data-copy-status]`, so that Copy, which `copy.js` runs, puts the block's text on the clipboard.
  - Tasks 4 to 6: `/terms/`, `/docs/`, `/docs/quickstart/` (with `[data-tabs]`, `[data-copy]` and `[data-copy-status]`, a pager whose Next is the next Docs page, and its fenced `DELETE` example drawn as a `.code-block`), `/blog/` (entries linking `/blog/<slug>/`, newest first). A Docs page has a "Docs" link to `/docs/` above its `h1`, inside the article, and a post's meta line links "Journal" to `/blog/` (Tasks 5 and 6). Neither is an entry (an entry is a link in the index's list to `/blog/<slug>/` or `/docs/<slug>/`) and neither is in the "More to read" nav, and no check below reads one as the other: the Journal's posts are the links with a slug (`POST_PATHS`), the pager is read from its own nav by `rel`, and the menu is clicked inside `#shell-menu`.
  - Existing: `new_context`, `open_page`, `full_page_screenshot`, `try_state`, `try_history`, `set_menu`, `page_problems`, `bottom_row_problems`, `menu_problems`, `phone_menu_problems`, `corner_problems`, `no_switch_problems`, `sideways_problems`, `tab_state_problems`, `install_tab_keys_problems`, `PAGE_STATE`, `REVIEW_NOTES`, `CODE_BLOCKS` (the script that reads each `.code-block` of a page: its id, its text as a reader sees it, its Copy button's word and name, its status, and where button and code lie; the pages part's `code_block_problems` reads Begin's with it).
  - Existing, from PRs #14 and #15 (`fix/phone-scale` and `fix/phone-menu-ink-copy`, merged before this PR; this PR changes nothing in the menu or in the phone sheet): the menu starts closed on every page at every width and never pins open (`static/js/shell.js`), and `set_menu` opens it with MENU, as a visitor does. The browser checks are in parts (`pages`, `cloth`, `ink`, and now `reading`), each a job in `ci.yml`, and `pages.tests_site_parts` holds every check in exactly one part.
- Produces, in `site-tests/run_site_checks.py`:
  - `READING_PAGES` (`/blog/`, `/docs/`, `/docs/quickstart/`, `/terms/`) and `reading_pages(browser, base_url, session_key) -> (pages, posts)`, which inserts the Journal's newest post after the Journal, found at run time from the Journal's first entry link, and returns the Journal's post paths newest first. `PAGES` is unchanged, so no page runs twice.
  - `--part reading` (`PART_STAGES["reading"]`), `run_reading(browser, base_url, session_key, out)`, `READING_RUN`; `--part all` is now pages, then cloth, then ink, then reading (the reading part loads no cloth, so it is last and needs no second look at WebGL).
  - `corner_problems(page, label, stops=None)` and `under_corner_problems(page, label, stops=None)`: with `stops`, that many scroll positions from the top to the end of the page instead of every quarter screen. Without it nothing changes.
  - Checks, each returning problem strings: `reading_menu_problems(page, label, path)`, `reading_measure_problems(page, label)`, `reading_sideways_problems(browser, base_url, session_key, site_page)`, `reading_sections_problems(browser, base_url, session_key, site_page)`, `reading_jump_problems(page, label, path, width, nav, headings)`, `reading_no_script_problems(browser, base_url, session_key, site_page, out)`, `reading_tabs_problems(page, label)`, `reading_code_problems(page, label, required=False)`, `reading_pager_problems(page, label, previous, following)`, `reading_arrival_problems(page, label, path)`, `reading_change_problems(browser, base_url, session_key, site_page, posts)`. `CODE_PAGES`: the reading pages that must have a code block (the quickstart), so that Copy on one is tried.
  - `pager_of(page)`: the page's previous and next links; a link is the previous or the next by its `rel` (`prev` or `next`).
  - `SHARED_PAGE_CHECKS`: the checks the pages part and the reading part both run, each on its own pages.
- Kept out of the reading part: `install_line_problems` and `install_line_height_problems`. They take `page.locator("[data-tabs]").first` and hard-code the threshold's install line (`.install-line__box`, the MCP, npm and pip tabs, the clipboard value `https://veilfile.com/mcp`), and the quickstart now has `[data-tabs]` groups of its own, so they would fail on it. Only `run_pages`, its edge cases and `come_in_problems` call them, on pages in `PAGES` (the quickstart is not one). The reading part checks the quickstart's tabs with `reading_tabs_problems`, which uses the generic `tab_state_problems` and `install_tab_keys_problems` on the first `main [data-tabs]` group that has a tab, and `test_the_reading_checks_run_in_the_reading_part_only` fails if `run_reading` ever reaches the two install-line checks.
- Produces, elsewhere: the CI job `site-reading`, the fourth (own 20 minutes, Chromium only, artifact `site-reading-screenshots`), and in `pages.tests_site_parts` the reading part as one more row of `JOBS` and `PART_ENTRIES`, which the tests already there hold to `--part all`, to `ci.yml` and to the docs; a change to the test that no check runs in two parts (the checks named in `SHARED_PAGE_CHECKS` may run in both the pages part and the reading part); and three new convention tests (the shared checks are run by both parts; reading checks only in the reading part; a page in one list only).

**How long the part should take.** `main`'s last CI run (7d94ea2) took 708 s for the checks step of the `site` job (the pages part: 11.8 minutes, 13 with the job's setup), 577 s for `site-cloth` and 755 s for `site-ink`, each against its own 20 minutes. A reading page here costs 8 or 9 browser contexts, because it gets 2 widths by 2 appearances with Motion on only (Motion changes only how a change fades, and the change checks watch that), a long-content run at three widths, a section-list run (two on a page that has the list), and two runs without JavaScript, where a page of the pages part gets 8 main states and some twenty edge cases. The corner scan, the one cost that grows with page height, looks at 8 scroll positions instead of every quarter screen, so a phone-width Terms page costs about what a chapter does. Expected: 10 to 20 s a page (the dry run printed 10, 14 to 15, 14, 17 and 18 to 19; Copy on the quickstart's code block adds about 2 s), 5 pages, about 10 s for the changes among them, so **about 85 s on an 8-core laptop (the dry run measured 81 to 86 s) and about 2 minutes on CI's 2-core runner, 2.5 with the job's setup (checkout, installs and starting the app took about 45 s in the last `site-ink` job)**, against the job's 20. If the laptop run takes more than 4 minutes, stop and report DONE_WITH_CONCERNS with the per-page times the run prints; the first things to cut are `READING_STOPS` (8 to 5), then the dark appearance at 375.

- [ ] **Step 1: Write the failing tests**

Replace the whole of `backend/pages/tests_site_parts.py` with the following. It is the file on `main` (7d94ea2) with the reading part added: the docstring, `JOBS` and `PART_ENTRIES` (the tests already there then hold the part to `--part all`, to `ci.yml` and to the docs), `Harness.names`, `test_no_check_falls_between_the_parts_or_runs_in_two` (which now lets the checks named in `SHARED_PAGE_CHECKS` run in both the pages part and the reading part), three new tests, and the docs test, which uses `assertTrue` and `assertIsNotNone` where it used `assertIn` and `assertRegex`, so that a failure prints its message and not the 60 KB of AGENTS.md before it.

```python
"""The browser checks run as CI jobs, `site` (the pages), `site-cloth` (the cloth), `site-ink` (the ink washes) and `site-reading` (the Journal, Docs and
legal pages), each with its own 20 minutes, and locally as `--part all`, which must be every one of them: a check that fell between the parts would pass
CI without ever running, and one in two would run twice (the few that the pages part and the reading part both run, each on its own pages, are named in
SHARED_PAGE_CHECKS in the harness). Every `site*` job in ci.yml is a part (a new one is one more part and one more name in the docs below).
site-tests/run_site_checks.py needs Playwright, which the backend does not install, so it is read as source here."""
import ast
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR).parent
HARNESS = ROOT / "site-tests" / "run_site_checks.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SPEC = ROOT / "docs" / "superpowers" / "specs" / "2026-10-08-site-redesign-design.md"
AGENTS = ROOT / "AGENTS.md"


class Harness:
    """The harness's module-level functions and assignments, and what each one reaches by name."""

    def __init__(self):
        tree = ast.parse(HARNESS.read_text(encoding="utf-8"))
        self.functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.values = {target.id: node.value for node in tree.body if isinstance(node, ast.Assign)
                       for target in node.targets if isinstance(target, ast.Name)}

    def reach(self, start):
        """Every module-level function or value that `start` names, directly or through what it names."""
        seen, todo = set(), [start]
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            node = self.functions.get(name) or self.values.get(name)
            if node is not None:
                todo += [use.id for use in ast.walk(node) if isinstance(use, ast.Name) and (use.id in self.functions or use.id in self.values)]
        return seen

    def checks(self, start):
        """The check functions (named ..._problems) that `start` reaches."""
        return {name for name in self.reach(start) if name in self.functions and name.endswith("_problems")}

    def names(self, value):
        """The names a module-level value (a tuple or list of functions, say) lists."""
        return {use.id for use in ast.walk(self.values[value]) if isinstance(use, ast.Name)}


# CI job, the part it runs, the artifact it keeps. A new part goes here, in ci.yml and in the docs (the last test).
JOBS = (("site", "pages", "site-screenshots"), ("site-cloth", "cloth", "site-cloth-screenshots"), ("site-ink", "ink", "site-ink-screenshots"),
        ("site-reading", "reading", "site-reading-screenshots"))
# The part's entry point in the harness, and the list of its families if it has one.
PART_ENTRIES = {"pages": ("run_pages", None), "cloth": ("cloth_problems", "CLOTH_FAMILIES"), "ink": ("ink_part_problems", "INK_FAMILIES"),
                "reading": ("run_reading", None)}


class SiteChecksSplitTests(SimpleTestCase):
    def test_all_is_every_part_in_order(self):
        stages = ast.literal_eval(Harness().values["PART_STAGES"])
        self.assertEqual(stages["all"], tuple(stage for _, part, _ in JOBS for stage in stages[part]),
                         "run_site_checks.py: `--part all` must run the stages of every part, in the order of the jobs, and nothing else")
        self.assertEqual(set(stages), {part for _, part, _ in JOBS} | {"all"}, "run_site_checks.py: PART_STAGES has a part for each CI job, and `all`")

    def test_every_part_that_another_follows_in_all_has_a_label_for_the_webgl_look(self):
        # `run` looks at WebGL again before a part that follows another in `all` and names the part before it from PREVIOUS_STAGE: a part without a
        # label would be a KeyError in the middle of the run, after the earlier parts had spent their time (a fourth part after `ink` makes `ink` one).
        harness = Harness()
        stages = ast.literal_eval(harness.values["PART_STAGES"])["all"]
        labels = ast.literal_eval(harness.values["PREVIOUS_STAGE"])
        for stage in stages[:-1]:
            self.assertTrue(labels.get(stage, "").strip(), f"run_site_checks.py: PREVIOUS_STAGE has no label for `{stage}`, which another part follows in `--part all`")

    def test_no_check_falls_between_the_parts_or_runs_in_two(self):
        harness = Harness()
        reached = {part: harness.checks(entry) for part, (entry, _) in PART_ENTRIES.items()}
        everything = harness.checks("run")
        shared = set().union(*(harness.checks(name) for name in harness.names("SHARED_PAGE_CHECKS")))
        for part, found in reached.items():
            self.assertTrue(found, f"{part}: the part reaches no check")
        self.assertEqual(everything - set().union(*reached.values()), set(),
                         "these checks run in `--part all` but in no part, so CI would never run them: put them under a part's entry point")
        for first in reached:
            for second in reached:
                if first < second:
                    pair = {first, second} == {"pages", "reading"}  # the one pair that runs some checks on both its own pages
                    advice = ", or, if the reading part means to run the same check on its own pages, name it in SHARED_PAGE_CHECKS" if pair else ""
                    self.assertEqual((reached[first] & reached[second]) - (shared if pair else set()), set(),
                                     f"these checks run in both `{first}` and `{second}`, so CI runs them twice: call each from one part{advice}")
        defined = {name for name in harness.functions if name.endswith("_problems")}
        self.assertEqual(defined - everything, set(), "these checks are defined but no part runs them")

    def test_every_family_is_in_one_part_and_runs_there(self):
        harness = Harness()
        listed = {}
        for part, (entry, families) in PART_ENTRIES.items():
            if families is None:
                continue
            listed[part] = {use.id for use in ast.walk(harness.values[families]) if isinstance(use, ast.Name)}
            self.assertTrue(listed[part], f"{families} is empty")
            self.assertLessEqual(listed[part], harness.checks(entry), f"{families}: a family not run by {entry}")
            for other, (other_entry, _) in PART_ENTRIES.items():
                if other != part:
                    self.assertEqual(listed[part] & harness.checks(other_entry), set(), f"{families}: a family that {other_entry} runs too")
        self.assertEqual(listed["cloth"] & listed["ink"], set(), "a family is in both CLOTH_FAMILIES and INK_FAMILIES")

    def test_the_shared_page_checks_are_run_by_the_pages_part_and_the_reading_part(self):
        harness = Harness()
        listed = harness.names("SHARED_PAGE_CHECKS")
        self.assertTrue(listed, "run_site_checks.py: SHARED_PAGE_CHECKS names no check")
        self.assertEqual(listed - (harness.checks("run_pages") & harness.checks("run_reading")), set(),
                         "run_site_checks.py: SHARED_PAGE_CHECKS names checks that the pages part and the reading part do not both run: take them out of it")

    def test_the_reading_checks_run_in_the_reading_part_only(self):
        harness = Harness()
        named = {name for name in harness.functions if name.startswith("reading_") and name.endswith("_problems")}
        self.assertTrue(named, "run_site_checks.py: no check is named reading_..._problems, but the reading part's checks are")
        self.assertEqual(named - harness.checks("run_reading"), set(),
                         "run_site_checks.py: these checks are named for the reading part but run_reading never reaches them: call them from run_reading")
        self.assertEqual(named & harness.checks("run_pages"), set(),
                         "run_site_checks.py: the pages part runs these reading checks, which are for the pages in READING_PAGES: call them from run_reading only")
        self.assertEqual({"install_line_problems", "install_line_height_problems"} & harness.reach("run_reading"), set(),
                         "run_site_checks.py: run_reading reaches the install-line checks, which look for the threshold's install line in the page's first [data-tabs] and would fail on the quickstart's shell tabs: keep them out of run_reading (reading_tabs_problems checks the quickstart's tabs)")

    def test_a_page_is_in_one_list_of_pages_only(self):
        harness = Harness()
        paths = {name: {call.args[0].value for call in harness.values[name].elts} for name in ("PAGES", "READING_PAGES")}
        self.assertTrue(paths["PAGES"] and paths["READING_PAGES"])
        self.assertEqual(paths["PAGES"] & paths["READING_PAGES"], set(),
                         "run_site_checks.py: these pages are in both PAGES and READING_PAGES, so two parts would check them: keep each in one (a Journal, Docs or legal page in READING_PAGES)")

    def test_the_workflow_runs_each_part_in_a_job_of_its_own(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        jobs = {}
        for match in re.finditer(r"^  ([a-z-]+):\n((?:    .*\n|\n)+)", text, re.M):
            jobs[match.group(1)] = match.group(2)
        for job, part, artifact in JOBS:
            body = jobs.get(job, "")
            self.assertIn(f"python -u site-tests/run_site_checks.py --part {part} ", body, f"ci.yml: the `{job}` job should run `--part {part}` with unbuffered output")
            self.assertIn("timeout-minutes: 20", body, f"ci.yml: the `{job}` job has its own 20 minutes")
            self.assertIn(f"name: {artifact}\n", body, f"ci.yml: the `{job}` job keeps its screenshots as `{artifact}`")
            for other_job, other_part, _ in JOBS:
                if other_job != job:
                    self.assertNotIn(f"--part {other_part} ", body, f"ci.yml: the `{job}` job runs only its own part")
        self.assertEqual(sorted(re.findall(r"^  (site[a-z-]*):\n", text, re.M)), sorted(job for job, _, _ in JOBS),
                         "ci.yml: every `site*` job is a part of the browser checks: add it to JOBS here, give it a part in run_site_checks.py and name it in the docs")

    def test_the_site_job_installs_webkit_for_the_checks_that_run_in_it(self):
        # webkit_sheet_problems skips itself, with a line, where WebKit is not installed: in CI that would be a check that passes by not running.
        self.assertIn("webkit_sheet_problems", Harness().checks("run_pages"))
        text = WORKFLOW.read_text(encoding="utf-8")
        site = text[text.index("\n  site:\n"):text.index("\n  site-cloth:\n")]
        self.assertRegex(site, r"playwright install --with-deps chromium webkit\n", "ci.yml: the `site` job runs a check in WebKit, so it installs it")

    def test_the_docs_name_every_site_job_where_they_say_which_checks_are_required(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        jobs = re.findall(r"^  (site[a-z-]*):\n", text, re.M)
        self.assertTrue({job for job, _, _ in JOBS} <= set(jobs), "ci.yml: the browser checks are the `site*` jobs")

        def paragraph(path, marker):
            """The text from the line that has the marker to the next blank line."""
            whole = path.read_text(encoding="utf-8")
            # assertTrue and assertIsNotNone, not assertIn and assertRegex: those print the whole document (AGENTS.md is 60 KB) before the message
            self.assertTrue(marker in whole, f"{path.name} no longer has {marker!r}; update this test to where the docs now say it")
            start = whole.rindex("\n", 0, whole.index(marker)) + 1
            return whole[start:whole.find("\n\n", start)]

        for where, block in (
            ("the spec's description of the browser-check jobs (section 10)", paragraph(SPEC, "- **Browser-check jobs")),
            ("the spec's branch protection bullet (section 10)", paragraph(SPEC, "- **Branch protection:**")),
            ("the spec's launch step (section 11)", paragraph(SPEC, "**Voice pass and launch:**")),
            ("AGENTS.md's rule on how the browser checks run", paragraph(AGENTS, "The browser checks run as")),
            ("AGENTS.md's rule on which checks to wait for", paragraph(AGENTS, "wait for the `backend`")),
            ("ci.yml's header on the required checks", text[:text.index("name: CI")]),
        ):
            for job in jobs:
                self.assertIsNotNone(re.search(rf"(?<![\w-])`?{re.escape(job)}`?(?![\w-])", block), f"{where} should name the `{job}` job")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_parts -v 2`
Expected: `Ran 10 tests`, `FAILED (failures=3, errors=4)`. `test_every_part_that_another_follows_in_all_has_a_label_for_the_webgl_look`, `test_every_family_is_in_one_part_and_runs_there` and `test_the_site_job_installs_webkit_for_the_checks_that_run_in_it` pass. The others fail before any code exists:
- `test_all_is_every_part_in_order`: `KeyError: 'reading'`.
- `test_no_check_falls_between_the_parts_or_runs_in_two` and `test_the_shared_page_checks_are_run_by_the_pages_part_and_the_reading_part`: `KeyError: 'SHARED_PAGE_CHECKS'`.
- `test_a_page_is_in_one_list_of_pages_only`: `KeyError: 'READING_PAGES'`.
- `test_the_reading_checks_run_in_the_reading_part_only`: `AssertionError: set() is not true : run_site_checks.py: no check is named reading_..._problems, but the reading part's checks are`.
- `test_the_workflow_runs_each_part_in_a_job_of_its_own`: `AssertionError: 'python -u site-tests/run_site_checks.py --part reading ' not found in '' : ci.yml: the `site-reading` job should run `--part reading` with unbuffered output`.
- `test_the_docs_name_every_site_job_where_they_say_which_checks_are_required`: `AssertionError: False is not true : ci.yml: the browser checks are the `site*` jobs`.

- [ ] **Step 2: The reading pages, the corner positions and the docstrings**

Every replacement below is in `site-tests/run_site_checks.py`; each old text occurs exactly once (checked against `main` at 7d94ea2, and Tasks 1 to 6 do not touch this file). Do not rely on line numbers.

Replace

```python
"""Browser checks for every page on the new shell (spec 10; CI jobs `site`, `site-cloth` and `site-ink`).
```

with

```python
"""Browser checks for every page on the new shell (spec 10; CI jobs `site`, `site-cloth`, `site-ink` and `site-reading`).
```

Replace

```python
      --session-file site-tests/session.txt --out site-tests/artifacts [--part pages|cloth|ink|all]

The checks come in parts, which CI runs as jobs (`site`, `site-cloth` and `site-ink`, each with its own 20 minutes) and `--part all`, the
default, runs one after the other: `pages` (every page and what a visitor can bring, then the in-place changes), `cloth` (the WebGL cloth's
families) and `ink` (the ink washes, the phone menu's sheet included: the cloth's second half).
"""
```

with

```python
      --session-file site-tests/session.txt --out site-tests/artifacts [--part pages|cloth|ink|reading|all]

The checks come in parts, which CI runs as jobs (`site`, `site-cloth`, `site-ink` and `site-reading`, each with its own 20 minutes) and `--part all`, the
default, runs one after the other: `pages` (every page and what a visitor can bring, then the in-place changes), `cloth` (the WebGL cloth's
families), `ink` (the ink washes, the phone menu's sheet included: the cloth's second half) and `reading` (the Journal, the Docs
and the legal documents: long columns, section lists, code blocks, previous and next, and the changes among them). Opening each page with
Motion on and off is the pages part's; the reading part opens each of its pages in Paper and Ink with Motion on only (Motion changes only
how a change fades, which its change checks watch).
"""
```

Replace (the Docs no longer set their heading above `<main>`)

```python
# address, the menu's current mark, the heading (in <main>, and the page's first anywhere: the Docs set theirs
# above <main>) and whether <main> is showing.
```

with

```python
# address, the menu's current mark, the heading (in <main>, and the page's first anywhere) and whether <main> is showing.
```

Replace

```python
    SitePage("/plans/", "plans", chapter=True),
)
```

with

```python
    SitePage("/plans/", "plans", chapter=True),
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
```

Replace the head of `under_corner_problems`

```python
def under_corner_problems(page, label):
    """Wherever the page is scrolled, nothing in <main> shows under a corner label: from 64rem up the page runs between
    the corners, below it the page fades out along the top, before the mark and MENU, and the bottom corners wait at its end."""
    problems, seen = [], set()
    height = page.viewport_size["height"]
    bottom = page.evaluate("() => document.documentElement.scrollHeight - innerHeight")
    for y in range(0, bottom + 1, max(1, height // 4)):
```

with

```python
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
```

and `corner_problems`

```python
def corner_problems(page, label):
    """The corners' rows are level and, wherever the page is scrolled, nothing in <main> shows under a corner."""
    return corner_row_problems(page, label) + under_corner_problems(page, label)
```

with

```python
def corner_problems(page, label, stops=None):
    """The corners' rows are level and, wherever the page is scrolled (at `stops` positions, if given), nothing in <main> shows under a corner."""
    return corner_row_problems(page, label) + under_corner_problems(page, label, stops)
```

(The other callers pass no `stops`, so the pages part looks at what it looked at before.)

- [ ] **Step 3: The reading checks**

In `site-tests/run_site_checks.py`, replace these four lines (they sit just above `run_pages`)

```python
# What each part runs, in order: `all` is every part, one after the other, so a stage cannot be left out of every job
# (pages.tests_site_parts holds the split to this and to what `run` reaches).
PART_STAGES = {"pages": ("pages",), "cloth": ("cloth",), "ink": ("ink",), "all": ("pages", "cloth", "ink")}
PARTS = tuple(PART_STAGES)
```

with all of the following (the reading checks, then the new `PART_STAGES`, `PARTS` and `SHARED_PAGE_CHECKS`):

```python
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
    if page.locator("header [data-menu-toggle]").get_attribute("aria-expanded") != "false" or page.locator("#shell-menu").is_visible():
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
PART_STAGES = {"pages": ("pages",), "cloth": ("cloth",), "ink": ("ink",), "reading": ("reading",), "all": ("pages", "cloth", "ink", "reading")}
PARTS = tuple(PART_STAGES)
# The checks that the pages part runs on its pages and the reading part on its own: each is one check of one page in one state, so
# CI running both is not running one check twice. Any other check is in one part only (pages.tests_site_parts reads this list).
SHARED_PAGE_CHECKS = (page_problems, sideways_problems, bottom_row_problems, menu_problems, phone_menu_problems, corner_problems,
                      no_switch_problems, tab_state_problems, install_tab_keys_problems)
```

- [ ] **Step 4: Run the part from `run()` and `--part`**

Still in `site-tests/run_site_checks.py`, in `run()`, replace

```python
    """Run a part of the checks (PARTS): `pages`, `cloth` and `ink` are what CI runs as three jobs, and `all` is every one, one after the
    other. Every part starts with the WebGL look, which also prints the pointer and hover media the hover fills answer to (a part that
    follows another in `all` looks again, since the earlier one may have taken WebGL away)."""
```

with

```python
    """Run a part of the checks (PARTS): `pages`, `cloth`, `ink` and `reading` are what CI runs as four jobs, and `all` is every one, one after the
    other. Every part starts with the WebGL look, which also prints the pointer and hover media the hover fills answer to (a part that
    follows another in `all` looks again, since the earlier one may have taken WebGL away; the reading part, which runs last and
    loads no cloth, needs no look)."""
```

Replace

```python
            elif stage == "cloth":
                problems += cloth_problems(browser, base_url, session_key, previous)
            else:
                problems += ink_part_problems(browser, base_url, session_key, previous)
```

with

```python
            elif stage == "cloth":
                problems += cloth_problems(browser, base_url, session_key, previous)
            elif stage == "ink":
                problems += ink_part_problems(browser, base_url, session_key, previous)
            else:
                problems += run_reading(browser, base_url, session_key, out)
```

Replace (the line the run ends with is unchanged for `pages`, `cloth` and `ink`)

```python
    scope = {"pages": f"across {len(PAGES)} page(s)", "cloth": f"in the {len(CLOTH_FAMILIES)} cloth families", "ink": f"in the {len(INK_FAMILIES)} ink families",
             "all": f"across {len(PAGES)} page(s) and in {len(CLOTH_FAMILIES)} cloth and {len(INK_FAMILIES)} ink families"}[part]
```

with

```python
    scope = {"pages": f"across {len(PAGES)} page(s)", "cloth": f"in the {len(CLOTH_FAMILIES)} cloth families", "ink": f"in the {len(INK_FAMILIES)} ink families",
             "reading": f"across {len(READING_RUN['pages'])} reading page(s)",
             "all": f"across {len(PAGES)} page(s) and {len(READING_RUN['pages'])} reading page(s), and in {len(CLOTH_FAMILIES)} cloth and {len(INK_FAMILIES)} ink families"}[part]
```

Replace

```python
                        help="pages: every page and the in-place changes; cloth: the WebGL cloth's families; ink: the ink washes, "
                             "the phone menu's sheet included; all (the default): every part, one after the other")
```

with

```python
                        help="pages: every page and the in-place changes; cloth: the WebGL cloth's families; ink: the ink washes, "
                             "the phone menu's sheet included; reading: the Journal, the Docs and the legal documents; "
                             "all (the default): every part, one after the other")
```

(The line the run ends with is unchanged for `pages`, `cloth` and `ink`.)

- [ ] **Step 5: The CI job and the words that name the jobs**

`.github/workflows/ci.yml`: replace the header

```yaml
# site (the page checks), site-cloth (the cloth's families) and site-ink (the ink
# washes, in the phone menu's sheet too). Every site* job must pass; they join the
# required checks when the redesign launches (with the owner's go-ahead). A new
# part is a new job here and a name in the docs that list the required checks
# (pages.tests_site_parts holds both).
```

with

```yaml
# site (the page checks), site-cloth (the cloth's families), site-ink (the ink
# washes, in the phone menu's sheet too) and site-reading (the Journal, Docs and
# legal pages). Every site* job must pass; they join the required checks when the
# redesign launches (with the owner's go-ahead). A new part is a new job here and
# a name in the docs that list the required checks (pages.tests_site_parts holds
# both).
```

and add the job at the end of the file, after `site-ink` (not between `site` and `site-cloth`: `test_the_site_job_installs_webkit_for_the_checks_that_run_in_it` reads the text from `site:` to `site-cloth:` as the `site` job). Replace the last lines of the `site-ink` job

```yaml
          name: site-ink-screenshots
          path: |
            site-tests/artifacts/
            site-tests/server.log
          retention-days: 14
```

with

```yaml
          name: site-ink-screenshots
          path: |
            site-tests/artifacts/
            site-tests/server.log
          retention-days: 14

  site-reading:
    name: site-reading
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
      # The Journal, the Docs and the legal documents: long columns, section lists, code blocks, previous and next, and the changes among them.
      # Chromium only (the reading checks have no WebKit pass), and the cloth is stubbed out, so no GPU or SwiftShader work is asked of the runner.
      - name: Browser checks, the reading pages
        run: python -u site-tests/run_site_checks.py --part reading --base-url http://127.0.0.1:8000 --session-file site-tests/session.txt --out site-tests/artifacts
      - name: Keep the screenshots and the server log
        if: always()
        uses: actions/upload-artifact@v4.4.3
        with:
          name: site-reading-screenshots
          path: |
            site-tests/artifacts/
            site-tests/server.log
          retention-days: 14
```

`AGENTS.md` (five replacements):

1. Replace

```
site-tests/            browser checks for pages on the new shell (CI jobs `site`, `site-cloth` and `site-ink`)
```

with

```
site-tests/            browser checks for pages on the new shell (CI jobs `site`, `site-cloth`, `site-ink` and `site-reading`)
```

2. Replace

```
`site-cloth` (`--part cloth`, the cloth's families, below) and `site-ink` (`--part ink`, the ink washes and the phone sheet that carries them, below). A new part is a new job in `ci.yml`, a name wherever the docs list the required checks (the spec's section 10 and 11, this file's "wait for" rule, `ci.yml`'s header) and an entry in `pages.tests_site_parts`, which fails until all three are done. `site` opens every page on the new shell in Chromium at phone
```

with

```
`site-cloth` (`--part cloth`, the cloth's families, below), `site-ink` (`--part ink`, the ink washes and the phone sheet that carries them, below) and
`site-reading` (`--part reading`, the reading pages, below). A new part is a new job in `ci.yml`, a name wherever the docs list the required checks (the spec's section 10 and 11, this file's "wait for" rule, `ci.yml`'s header) and an entry in `pages.tests_site_parts`, which fails until all three are done. `site` opens every page in `PAGES` (the reading pages are `site-reading`'s) in Chromium at phone
```

3. Replace the line

```
The cloth has checks of its own. Chromium runs with SwiftShader (`GL_ARGS`), so
```

with

```
The reading pages (the Journal, its newest post, the Docs, the quickstart and Terms) are in `READING_PAGES` in
`site-tests/run_site_checks.py`, not in `PAGES`, and `site-reading` checks them: they are long, and in `PAGES` they would
take the pages part past what it can spare. The newest post is not written down: the part takes it from the Journal's first
entry, so a new post needs no edit. Each page opens at 375 and 1280 in Paper and Ink with Motion on (Motion changes only how
a change fades, which the change checks watch); the corners are looked at in eight scroll positions, not every quarter of a
screen, and the menu as the pages part looks at it. Then come the checks a long column calls for. A long word in a heading,
a long URL, a long code line in a code block and a wide table are added to the column at 1280, 375 and 320: the page must not
scroll sideways, a word and a URL wrap, and code and tables scroll inside their own boxes. The column is no wider than 65
characters. The menu starts closed on every page and never pins open: that is checked at 1280 and 375, and again after each
change in place. A Docs page or legal document with two or more `##` headings has a section list, each of whose links lands
its heading at the top, and at 375 below the top fade. Every fenced code block in the reading column is drawn by
`components/code_block.html` (`render_reading`): `reading_code_problems` checks that each has its Copy button and an id of its
own, and that Copy puts exactly the code as a reader sees it on the clipboard and says Copied in its own block's status (the
quickstart's `DELETE` example must be there). A page with no JavaScript still works. The changes among the pages (the Journal,
a post and the posts either side of it, the Docs, the quickstart and the Docs page after it, Terms) all happen in place, with
the head swapped as a direct load has it (title, canonical address, structured data), the menu closed and focus on the heading;
Back and Forward put the pages back, and Terms to About is an ordinary load. A check that finds nothing to measure says so: as
a problem where the page must have the thing, as a note in `review-by-eye.txt` where it may rightly lack it (a Journal with one
post has no neighbours to link). A check that the pages part also runs on its own pages is named in `SHARED_PAGE_CHECKS`. The
part loads no cloth, so it is the last in `--part all`; it takes about 85 s on a laptop.

The cloth has checks of its own. Chromium runs with SwiftShader (`GL_ARGS`), so
```

4. Replace

```
`site-screenshots` (`site-cloth` and `site-ink` keep the few their families take as `site-cloth-screenshots` and `site-ink-screenshots`), with `review-by-eye.txt`: what axe could not decide (usually contrast over a fill or the grain), for a person
to look at in the screenshots. Pages are listed in `PAGES` in
`site-tests/run_site_checks.py`; add a page there when it moves onto the
shell. To run it locally (`--part pages`, `--part cloth` or `--part ink` runs one job's checks, `--part all`, the
default, every part, one after the other; a test, `pages.tests_site_parts`, holds `all` to the parts so that no check falls between them or runs in two; `webgl_precondition` runs at the start of each part):
```

with

```
`site-screenshots` (`site-reading` keeps its own as `site-reading-screenshots`, and `site-cloth` and `site-ink` the few their families take as `site-cloth-screenshots` and `site-ink-screenshots`), with `review-by-eye.txt`: what axe could not decide (usually contrast over a fill or the grain), and what a check found nothing to measure, for a person
to look at in the screenshots. Pages are listed in `PAGES` in
`site-tests/run_site_checks.py`; add a page there when it moves onto the
shell, except a reading page (the Journal, the Docs, the legal documents), which goes in `READING_PAGES`. To run it locally (`--part pages`, `--part cloth`, `--part ink` or `--part reading` runs one job's checks, `--part all`, the
default, every part, one after the other; a test, `pages.tests_site_parts`, holds `all` to the parts so that no check falls between them or runs in two, and a check that the pages part and the reading part both run on their own pages must be named in `SHARED_PAGE_CHECKS`; `webgl_precondition` runs at the start of each part):
```

5. Replace

```
`python-cli` checks and for every browser-check job (`site`, `site-cloth` and `site-ink`; the list is in `ci.yml`), and merge only when they all pass.
```

with

```
`python-cli` checks and for every browser-check job (`site`, `site-cloth`, `site-ink` and `site-reading`; the list is in `ci.yml`), and merge only when they all pass.
```

`README.md` (two replacements):

1. Replace

```
(jobs `site`, `site-cloth` and `site-ink`; see AGENTS.md, "Site checks")
```

with

```
(jobs `site`, `site-cloth`, `site-ink` and `site-reading`; see AGENTS.md, "Site checks")
```

2. Replace

```
(CI jobs `site`, `site-cloth`, `site-ink`)
```

with

```
(CI jobs `site`, `site-cloth`, `site-ink`, `site-reading`)
```

`site-tests/requirements.txt` and `site-tests/package.json`: in each, replace

```
(CI jobs `site`, `site-cloth` and `site-ink`)
```

with

```
(CI jobs `site`, `site-cloth`, `site-ink` and `site-reading`)
```

`docs/superpowers/specs/2026-10-08-site-redesign-design.md` (six replacements: 1 to 4 in section 10, 5 in section 11 step 7, 6 in section 13; each old text is one line or part of one):

1. Replace

```
- **Browser-check jobs, `site`, `site-cloth` and `site-ink`:**
```

with

```
- **Browser-check jobs, `site`, `site-cloth`, `site-ink` and `site-reading`:**
```

2. Replace

```
so they are split in two). A later part is one more job of this list.
```

with

```
so they are split in two); `site-reading` runs the reading pages' checks (`--part reading`: the Journal, the Docs and the legal documents, whose long columns, section lists, code blocks, previous and next links and changes among themselves need checks of their own). A later part is one more job of this list.
```

3. Replace

```
`site-cloth` and `site-ink` save the few their checks take (`site-cloth-screenshots`, `site-ink-screenshots`).
```

with

```
`site-reading` saves its own (`site-reading-screenshots`), and `site-cloth` and `site-ink` the few their checks take (`site-cloth-screenshots`, `site-ink-screenshots`).
```

4. Replace

```
every browser-check job (`site`, `site-cloth` and `site-ink`) becomes a required check at launch,
```

with

```
every browser-check job (`site`, `site-cloth`, `site-ink` and `site-reading`) becomes a required check at launch,
```

5. Replace

```
branch protection for the browser-check jobs (`site`, `site-cloth` and `site-ink`).
```

with

```
branch protection for the browser-check jobs (`site`, `site-cloth`, `site-ink` and `site-reading`).
```

6. Replace

```
The `site` job multiplies pages by states, and the cloth's checks add to it, so they run as jobs of their own (`site-cloth` and `site-ink`: the same run, in parts).
```

with

```
The `site` job multiplies pages by states, and the reading pages and the cloth's checks add to it, so they run as jobs of their own (`site-reading`, `site-cloth` and `site-ink`: the same run, in parts).
```

(Replacement 1 keeps the bullet's first words, `- **Browser-check jobs`, which `pages.tests_site_parts` finds the bullet by. The spec's section 10 also has the lines `  - `site` runs at 375px and 1280px, in Paper and Ink, with Motion on and off.`, `  - `site` visits every public page and the main dashboard pages, ...` and `  - `site` fails on:`. They stay true for the pages part and are left alone here: any amendment of them for the reading part is Task 8's, and must say that the reading part runs Paper and Ink with Motion on only.)

- [ ] **Step 6: Run the tests, and look at the harness once without a browser**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_parts -v 2`
Expected: `Ran 10 tests`, `OK`.

Run: `site-tests/.venv/bin/python site-tests/run_site_checks.py --help` from the repo root. (`site-tests/.venv` is Python 3.9: the harness must keep to syntax 3.9 reads.)
Expected: a usage line ending `[--part {pages,cloth,ink,reading,all}]`, and the help text for `--part` naming the Journal, the Docs and the legal documents.

- [ ] **Step 7: Run the reading part, and prove its checks can fail**

This is the first time Tasks 1 to 6 meet a browser, so findings are expected. Start the dev server in a second terminal (restart it if it is already running: templates are cached with `--noreload`): `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py runserver 127.0.0.1:8777 --noreload`. If `site-tests/session.txt` is missing or stale, make it as AGENTS.md's "Site checks" section says. From the repo root:

```bash
/usr/bin/time -p site-tests/.venv/bin/python -u site-tests/run_site_checks.py --base-url http://127.0.0.1:8777 --session-file site-tests/session.txt --out site-tests/artifacts --part reading
```

Expected: the `WebGL:` and `Pointer on` lines; one `reading: <path> took N s` line for each of `/blog/`, the newest post's path (the Journal's first entry), `/docs/`, `/docs/quickstart/` and `/terms/`; `reading: the changes among the pages took N s`; and last `0 problem(s) across 5 reading page(s); K note(s) to review by eye; screenshots and review-by-eye.txt in site-tests/artifacts`. While the Journal has one post, one of the notes (also in `site-tests/artifacts/review-by-eye.txt`) reads `nothing to measure: the Journal has one post (...), so previous and next between posts and the change from one post to another were not tried`; the others are axe's. `/usr/bin/time -p` prints `real`, `user` and `sys` on stderr: about 85 to 100 s real on an 8-core laptop, with user plus sys about the same (the dry run measured 81 to 86 s real and 92 s user plus sys, to which Copy on the quickstart's code block adds about 2 s; on that dry run CI's 2-core runner ran the pages and cloth parts about 1.2 times slower than the laptop). Write down all three numbers and the per-page times.

If problems are listed, each names what to fix. Fix the cause in the file it names (it belongs to Tasks 1 to 6; add the file to this task's commit and say so in the report), restart the server after a template or Python change, and run only this part again. Where each message points:
- `no .reading column`, `body reads data-layout`: `templates/layouts/reading.html` and the registry (Tasks 2 and 3).
- `menu starts open`, `menu is open on a reading page`: the menu rule in `static/js/shell.js`, which PR #14 (`fix/phone-scale`) settled before this PR (the menu starts closed on every page and never pins open) and this PR does not touch; so look at that change not having reached this checkout, or at a template or script of Tasks 2 to 6 that opens the menu.
- `[data-page-meta] elements are not those a direct load gets`, `structured data besides Organization`, `canonical addresses`, `title reads`: `templates/shell.html`, the `structured_data` tag, the context processor (Task 2).
- `does not wrap`, `does not scroll inside its own box`, `reaches`, `scrolls sideways`, `reading column is`: the reading CSS the message names (Task 3).
- `section list`, `ids`, `lands the heading`: `templates/components/sections.html` (Task 3), `render_reading` (Task 1), `scroll-padding-top` in `static/css/base.css`, the headings' `scroll-margin-top` in the reading CSS (Task 3).
- `pager`, `Previous`, `Next`: `templates/components/pager.html` (Task 3: its links carry `rel="prev"` and `rel="next"`, which `pager_of` reads) and the `neighbours` functions (Tasks 5 and 6).
- `tablist`, `data-copy-status`, and `Copy` in a message about the quickstart's tabs: `backend/docs/pages/quickstart.md` (Task 5).
- `code block`, `Copy put`, `code blocks' ids`: `render_reading` in `backend/blog/render.py` (Task 1), which draws every fenced block through `templates/components/code_block.html`, and `static/js/behaviors/copy.js`, which reads the block's id and writes its text.
- `accessibility:` lines: the template or stylesheet axe names.
- `could not click`, `never arrived`: first look at the real markup (`curl -s http://127.0.0.1:8777/docs/quickstart/ | grep -n 'aria-label'`); if this task's selector is wrong for it, fix the check, never weaken it.

If the run takes more than 4 minutes (about three times the dry run's), stop and report DONE_WITH_CONCERNS with the per-page times.

Then prove four checks can fail. Each one-off below runs a single check against the running server; restore every file afterwards.

(a) Long content. Find the declarations that wrap long words in the reading rules: `grep -rn "overflow-wrap\|word-break" backend/static/css`. Comment those declarations out of the `.reading` rules only, then run:

```bash
site-tests/.venv/bin/python - <<'EOF'
import sys
sys.path.insert(0, "site-tests")
from pathlib import Path
from playwright.sync_api import sync_playwright
import run_site_checks as checks

base, key = "http://127.0.0.1:8777", Path("site-tests/session.txt").read_text(encoding="utf-8").strip()
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=checks.GL_ARGS)
    for problem in checks.reading_sideways_problems(browser, base, key, checks.READING_PAGES[3]):
        print(problem)
    browser.close()
EOF
```

Expected: problems for `/terms/` at 320px, 375px and 1280px (a long word is wider than its box at every width), among them `the page scrolls sideways by` and `the long word in a heading does not wrap`. Restore with `git checkout -- <the css file>` and run it again: nothing is printed.

(b) The menu. With the server unchanged:

```bash
site-tests/.venv/bin/python - <<'EOF'
import sys
sys.path.insert(0, "site-tests")
from pathlib import Path
from playwright.sync_api import sync_playwright
import run_site_checks as checks

base, key = "http://127.0.0.1:8777", Path("site-tests/session.txt").read_text(encoding="utf-8").strip()
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=checks.GL_ARGS)
    terms = checks.READING_PAGES[3]
    context = checks.new_context(browser, base, key, terms, width=1280, theme=None, motion=None)
    page, _ = checks.open_page(context, base + terms.path)
    checks.set_menu(page, True)  # the page as it would look if the menu were open on load
    print(checks.reading_menu_problems(page, "proof", terms.path))
    browser.close()
EOF
```

Expected: one problem, `proof: the menu starts open on a reading page; the menu starts closed on every page and never pins open (shell.js, spec 4.4)`.

(c) The phone's top fade. In `backend/static/css/base.css` replace `scroll-padding-top: calc(var(--fade-top) + var(--fade-band));` with `scroll-padding-top: 0;`, then run the same one-off as (a) with `checks.reading_sections_problems(browser, base, key, checks.READING_PAGES[3])`. Expected: `/terms/ at 375px, the section list: the link to '1. ...': scroll-padding-top is 0px at 375px` for the links, and no problem at 1280px. Restore with `git checkout -- backend/static/css/base.css`.

(d) Copy on a code block. With the server unchanged, make Copy put something other than the code on the clipboard (a hidden element that `copy.js` prefers, `[data-copy-text]`, holding other words), and run the check on the quickstart:

```bash
site-tests/.venv/bin/python - <<'EOF'
import sys
sys.path.insert(0, "site-tests")
from pathlib import Path
from playwright.sync_api import sync_playwright
import run_site_checks as checks

base, key = "http://127.0.0.1:8777", Path("site-tests/session.txt").read_text(encoding="utf-8").strip()
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=checks.GL_ARGS)
    quickstart = checks.READING_PAGES[2]
    context = checks.new_context(browser, base, key, quickstart, width=1280, theme=None, motion=None)
    page, _ = checks.open_page(context, base + quickstart.path)
    print("as it is:", checks.reading_code_problems(page, "proof", required=True))
    page.evaluate("""() => document.querySelectorAll('.code-block pre').forEach((pre) => {
        const other = document.createElement('span');
        other.hidden = true; other.dataset.copyText = ''; other.textContent = 'not the code';
        pre.append(other); })""")
    print(checks.reading_code_problems(page, "proof", required=True))
    browser.close()
EOF
```

Expected: `as it is: []`, then a list with one problem for each code block the check tries (the first, the middle one and the last), each `proof, code block N: Copy put 'not the code' on the clipboard; the block's text is '...'`. No file is edited, so there is nothing to restore.

Last, `git status --short` shows only this task's files (and any file a finding made you fix), besides the untracked `.DS_Store` and `.claude/` noted in the Global Constraints.

- [ ] **Step 8: Run the three old parts once**

The reading part must not have changed what the old parts do. Restart the server, then from the repo root:

```bash
/usr/bin/time -p site-tests/.venv/bin/python -u site-tests/run_site_checks.py --base-url http://127.0.0.1:8777 --session-file site-tests/session.txt --out site-tests/artifacts --part pages
/usr/bin/time -p site-tests/.venv/bin/python -u site-tests/run_site_checks.py --base-url http://127.0.0.1:8777 --session-file site-tests/session.txt --out site-tests/artifacts --part cloth
/usr/bin/time -p site-tests/.venv/bin/python -u site-tests/run_site_checks.py --base-url http://127.0.0.1:8777 --session-file site-tests/session.txt --out site-tests/artifacts --part ink
```

Expected: `0 problem(s) across 7 page(s); ...` for the first, `0 problem(s) in the 11 cloth families; ...` for the second and `0 problem(s) in the 4 ink families; ...` for the third (the numbers of families are whatever `CLOTH_FAMILIES` and `INK_FAMILIES` hold). Where WebKit is not installed for Playwright the first prints a line saying that its WebKit sheet checks were skipped; that is not a problem. The three take about half an hour together. Record `real`, `user` and `sys` for each, beside the reading part's. `main`'s last CI run (7d94ea2) took 708 s for the checks step of the `site` job (the pages part), 577 s for `site-cloth` and 755 s for `site-ink` (the jobs 777, 632 and 808 s, setup included). The dry run's laptop times for the pages and cloth parts (8.8 and 10.2 minutes) are from before PR #15, which added checks to the pages part and to the ink part, so the CI figures are the reference: CI's 2-core runner ran about 1.2 times slower than the 8-core laptop, so none of the three should take longer on the laptop than on CI, and the four jobs each stay under 15 of their 20 minutes. A problem here is not from this task's harness (only the reading part, the optional `stops` argument and the dispatch in `run` are new) but from Tasks 2 to 6, which changed the shell's template, `veil.js`, the stylesheets and the Docs (the pages part's checks that use the Docs as "another layout" now meet a Docs page on the shell): find which task caused it, fix it there, and report it; never weaken a check. Then stop the server (Ctrl-C).

- [ ] **Step 9: The whole Django suite**

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test`
Expected: `OK` (about 3.5 minutes).

- [ ] **Step 10: Commit**

```bash
git add site-tests/run_site_checks.py backend/pages/tests_site_parts.py .github/workflows/ci.yml AGENTS.md README.md \
  docs/superpowers/specs/2026-10-08-site-redesign-design.md site-tests/requirements.txt site-tests/package.json
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -m "Browser checks for the reading pages, in a part and a CI job of their own"
```

(Add, by name, any file of Tasks 1 to 6 that Step 7 or 8 made you fix, and say which in the report. Report the four timings: reading, pages, cloth and ink, each with real, user and sys.)

---

### Task 8: Recipes, the spec, cleanup and the last checks

**Files:**
- Modify: `AGENTS.md`, `README.md`, `docs/llms.txt`, `docs/superpowers/specs/2026-10-08-site-redesign-design.md`, `backend/templates/includes/ambient.html`, `backend/pages/registry.py` (the module docstring only)
- Modify only if the grep in Step 8 still finds the old words (Task 2 and Task 5 have rewritten them, so on this branch it does not): `backend/pages/content_checks.py` (the reason text only), `backend/pages/context_processors.py` (docstrings only)
- Delete: `backend/templates/includes/cursor.html`, `backend/templates/includes/page_transition.html`
- Test: `backend/pages/tests_site_rules.py`, `backend/pages/tests_content.py`, `backend/drop_core/tests_ambient.py` (one test removed); plus any test file Step 7 finds a leftover in

**Interfaces:**
- Consumes: everything Tasks 1 to 7 made: `layouts/reading.html` and the `blog/`, `docs/` and `legal/` templates that extend it (Tasks 3 to 6), the registry's `reading` family and `layout_paths()` (Task 2), `--part reading` and the `site-reading` CI job beside `site`, `site-cloth` and `site-ink` (Task 7: the browser checks are four parts now); `longMessage = False` on every test class of `pages/tests_reading_family.py`, `pages/tests_reading.py`, `pages/tests_legal.py`, `pages/tests_docs_reading.py`, `blog/tests_journal.py` and `blog/tests_feed.py` (Tasks 2 to 6, each for the classes it creates); and the directory tuple `("components", "layouts", "chapters", "pages", "legal", "docs", "blog")` that Tasks 4 to 6 built up, one folder each, in `shell_templates()` (`backend/pages/tests_site_rules.py`) and in `RealContentTests.test_every_shell_template` (`backend/pages/tests_content.py`), which this task replaces.
- Produces:
  - `missing_template_references(root)` in `backend/pages/tests_site_rules.py` (a test helper) and four conventions with tests whose failures name the file and the fix: every `{% include %}` and `{% extends %}` names a template that exists; the shell rules cover every template that extends the shell or a layout, wherever it sits (`shell_templates(root)` finds them by what they extend, so no tuple of directory names is left, and `test_every_shell_template` uses the same function); no `<header>` or `<footer>` in a page template; every test class in the reading pages' test modules sets `longMessage = False` (`READING_TEST_MODULES`, `cases_defined_in()` and `cases_that_print_whole_pages()` in the same file), so a failure there prints the message that names the file and the fix and not the 160 KB page that comes before it.
  - AGENTS.md recipes for Docs and the legal pages, the Journal recipe with a note on what needs no registering, a repo map that describes the reading pages, the test hygiene rule for page-sized assertions, and the spec amended for every ruling of this PR except the menu's (spec 4.4, which `fix/phone-scale` amends).

- [ ] **Step 1: Check the branch has everything this task cleans up after**

Run from the repo root:

```bash
git status --short
git fetch origin && git log --oneline HEAD..origin/main -- backend site-tests .github AGENTS.md README.md docs
grep -rn "includes/" backend/templates/blog backend/templates/docs backend/templates/legal
grep -rln 'extends "layouts/reading.html"' backend/templates | sort
ls backend/templates/components
grep -n "site-reading" .github/workflows/ci.yml
site-tests/.venv/bin/python site-tests/run_site_checks.py --help | grep reading
grep -L "longMessage = False" backend/pages/tests_reading_family.py backend/pages/tests_reading.py backend/pages/tests_legal.py backend/pages/tests_docs_reading.py backend/blog/tests_journal.py backend/blog/tests_feed.py
```

Expected, in order: only the untracked `.DS_Store` and `.claude/` (a fresh worktree also shows the linked `venv`, `site-tests/.venv` and `site-tests/node_modules`); nothing from the `git log`; nothing from the first `grep` (the three directories include nothing from `includes/` any more); five files from the second (`backend/templates/blog/index.html`, `blog/post.html`, `docs/index.html`, `docs/page.html`, `legal/document.html`); a component list that contains `code_block.html` (from `main`), `entries.html`, `pager.html` and `sections.html`; at least one line naming the `site-reading` job; a line of `--help` that lists `reading` among the `--part` choices; and nothing from the last `grep` (`-L` lists the files that never say `longMessage = False`, and each of the six says it). If any of these is not so, stop and report which of Tasks 2 to 7 is incomplete: the rest of this task assumes them. If the `git log` lists commits, someone changed files this plan edits: stop and report.

- [ ] **Step 2: Write the guard that every include and extends names a template that exists**

When an old include is deleted, a template that still names it is a server error on every page that renders it, and the tests notice only for pages they happen to render (the error pages and the throttled page are rarely rendered). Make the check permanent before deleting anything.

In `backend/pages/tests_site_rules.py`, replace this exact text:

```
import re
from html.parser import HTMLParser
```

with:

```
import re
import tempfile
from html.parser import HTMLParser
```

Then, directly after the whole function `shell_templates` (before `class InternalLinksTests`), add:

```python
TEMPLATE_REFERENCE = re.compile(r"{%\s*(?:include|extends)\s+[\"']([^\"']+)[\"']")


def missing_template_references(root):
    """(file, line, name) for every {% include %} or {% extends %} under root that names a template not under root."""
    missing = []
    for path in sorted(root.rglob("*.html")):
        for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
            missing += [(path.relative_to(root).as_posix(), number, name)
                        for name in TEMPLATE_REFERENCE.findall(line) if not (root / name).is_file()]
    return missing
```

At the end of `class TemplateRulesTests` (after `test_colors_live_only_in_tokens_css`), add:

```python
    def test_every_include_and_extends_names_a_template_that_exists(self):
        # A template that names a missing one is a server error on every page that renders it, and the tests only
        # notice for the pages they happen to render: deleting an old include must leave nothing pointing at it.
        missing = missing_template_references(TEMPLATES)
        self.assertEqual(missing, [], "\n" + "\n".join(
            f"backend/templates/{where}:{number}: names {name}, which is not a file under backend/templates; "
            "fix the name, or restore the file if it is still needed" for where, number, name in missing))

    def test_a_missing_include_is_reported_with_its_file_and_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "includes").mkdir()
            (root / "includes" / "there.html").write_text("there", encoding="utf-8")
            (root / "page.html").write_text(
                '{% extends "gone_base.html" %}\n{% include "includes/there.html" %}\n'
                '{% include "includes/gone.html" with a=1 only %}\n', encoding="utf-8")
            self.assertEqual(missing_template_references(root),
                             [("page.html", 1, "gone_base.html"), ("page.html", 3, "includes/gone.html")])
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules.TemplateRulesTests -v 2`
Expected: OK, with both new tests listed as `ok`.

Prove the guard can fail. From the repo root:

```bash
mv backend/templates/includes/menu.html backend/templates/includes/menu.html.held
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules.TemplateRulesTests.test_every_include_and_extends_names_a_template_that_exists; cd ..
mv backend/templates/includes/menu.html.held backend/templates/includes/menu.html
git status --short backend/templates/includes
```

Expected: FAIL with 18 lines of the form `backend/templates/registration/login.html:32: names includes/menu.html, which is not a file under backend/templates; fix the name, or restore the file if it is still needed` (the 5 device pages, the OAuth page and the 12 registration pages, in that order, each with its own line number); then `git status` prints nothing for `includes` (the file is back).

- [ ] **Step 3: Prove nothing uses the two old includes, then delete them**

`cursor.html` (the blinking underscore of the old wordmark) and `page_transition.html` (the old cross-document fade) were included only by the old Journal, Docs and legal templates, which Tasks 4 to 6 rewrote. The forms, device and OAuth pages still use `theme_boot.html`, `ambient.html` and `menu.html`: those stay.

```bash
grep -rn "includes/cursor.html\|includes/page_transition.html" backend site-tests tools .github AGENTS.md README.md docs/llms.txt
grep -rn "cursor.html\|page_transition" backend --include='*.py'
for f in theme_boot ambient menu; do echo "$f $(grep -rl "includes/$f.html" backend/templates | wc -l | tr -d ' ')"; done
```

Expected: nothing from the first two commands; then `theme_boot 18`, `ambient 18`, `menu 18` (the 12 registration, 5 device and 1 OAuth page). If either of the first two prints a line, that file still has a user: stop and fix the user, do not delete.

```bash
git rm backend/templates/includes/cursor.html backend/templates/includes/page_transition.html
ls backend/templates/includes
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules.TemplateRulesTests; cd ..
```

Expected: `ls` lists `ambient.html`, `menu.html` and `theme_boot.html`; the tests print OK (nothing names the deleted files).

- [ ] **Step 4: Delete the wordmark cursor rule that only those pages needed**

`ambient.html` carries one rule for the blinking cursor of the old wordmark. No template has that markup now:

```bash
grep -rn 'class="cursor' backend
```

Expected: nothing. (If it prints a line, a template still draws the cursor: stop and fix that first.)

In `backend/templates/includes/ambient.html`, replace this exact text:

```
  /* the toggle stills the wordmark cursor too: steady-on, never hidden */
  html[data-motion="off"] .cursor { animation: none; opacity: 1; }
```

with nothing (delete both lines; the line before them, `html[data-motion="off"] .ambient .blob { animation: none; }`, and the `@media (prefers-reduced-motion: reduce)` block after them stay).

In `backend/drop_core/tests_ambient.py`, delete the whole method `test_motion_off_stills_cursor` (from its `def` line to the blank line before the next `def`). It pins the rule just removed. Task 4 moved it from `/terms/` to `/accounts/login/` (it changed every `self.client.get("/terms/")` in this file): delete it wherever it is. After Task 4 it reads:

```python
    def test_motion_off_stills_cursor(self):
        # The motion toggle is labeled as motion control: data-motion="off"
        # stills the wordmark cursor too, resting at full opacity (steady-on,
        # never hidden — a disappearing cursor reads as broken).
        html = _html(self.client.get("/accounts/login/"))
        self.assertIn('html[data-motion="off"] .cursor', html)
        self.assertIn("opacity: 1", html)
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_ambient`
Expected: OK. (The `.veil` rules and the `veil` parameter in `ambient.html` are now used by no page, because every form passes `veil=False`. They stay: the whole include goes when the forms move, PR 5 and 6.)

- [ ] **Step 5: Make the shell's template rules follow what a template extends**

The rules in `pages/tests_site_rules.py` (no inline `style=`, components included with `only`, and the header and footer rule added below) and the character check in `pages/tests_content.py` cover the templates that `shell_templates()` returns. It finds them by directory name, and Tasks 4, 5 and 6 each added their folder to the tuple, in `shell_templates()` and in `RealContentTests.test_every_shell_template`: on this branch both read `("components", "layouts", "chapters", "pages", "legal", "docs", "blog")`. Spec 7.2 says older templates are checked as they move onto the shell, and a tuple does that only while whoever moves a page remembers to extend it (PR 5 and 6 move the forms). Replace the tuple with the rule itself: a template is under the shell rules when it extends the shell or a layout, wherever it sits. `components/` is the one folder still named, because a component is included and never extends anything. Every folder is already in the tuple, so no real template fails today and a failing run cannot come from a rule violation: the proof is a test of `shell_templates` on a fake set of templates.

In `backend/pages/tests_site_rules.py`, at the end of `class TemplateRulesTests`, add:

```python
    def test_a_template_is_under_the_shell_rules_by_what_it_extends(self):
        # Spec 7.2: older templates are checked as they move onto the shell. A template that extends the shell or a
        # layout is under the rules wherever it sits, so moving a page onto the shell needs no list to extend.
        templates = {
            "shell.html": "<html>{% block main %}{% endblock %}</html>",
            "components/box.html": "{# Component: box #}\n{# Inputs: none #}\n<div></div>",
            "layouts/wide.html": '{% extends "shell.html" %}',
            "notes/entry.html": "{% extends 'layouts/wide.html' %}",
            "forms/login.html": '{% include "includes/menu.html" %}',
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, text in templates.items():
                (root / name).parent.mkdir(exist_ok=True)
                (root / name).write_text(text, encoding="utf-8")
            covered = {path.relative_to(root).as_posix() for path in shell_templates(root)}
        self.assertEqual(covered, {"shell.html", "components/box.html", "layouts/wide.html", "notes/entry.html"},
                         "backend/pages/tests_site_rules.py: shell_templates() must cover shell.html, components/ and every "
                         "template that extends the shell or a layout, in any directory, and no other template")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules.TemplateRulesTests.test_a_template_is_under_the_shell_rules_by_what_it_extends`
Expected: ERROR, ending `TypeError: shell_templates() takes 0 positional arguments but 1 was given` (the function knows one root and a list of directory names), and `FAILED (errors=1)`.

In `backend/pages/tests_site_rules.py`, replace this exact function (as Task 6 left it):

```python
def shell_templates():
    paths = [TEMPLATES / "shell.html"]
    for directory in ("components", "layouts", "chapters", "pages", "legal", "docs", "blog"):
        paths += sorted((TEMPLATES / directory).glob("*.html"))
    return [path for path in paths if path.is_file()]
```

with:

```python
EXTENDS_THE_SHELL = re.compile(r"{%\s*extends\s+[\"'](?:shell\.html|layouts/[\w-]+\.html)[\"']")


def shell_templates(root=TEMPLATES):
    """The templates under the shell rules (spec 7.2): shell.html, every component, and every template under root that
    extends the shell or a layout, in whatever directory it sits. A page is under the rules the moment it moves onto the shell."""
    paths = [root / "shell.html"] + sorted((root / "components").glob("*.html"))
    paths += [path for path in sorted(root.rglob("*.html"))
              if path not in paths and EXTENDS_THE_SHELL.search(path.read_text(encoding="utf-8"))]
    return [path for path in paths if path.is_file()]
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules.TemplateRulesTests.test_a_template_is_under_the_shell_rules_by_what_it_extends`
Expected: OK.

In `backend/pages/tests_content.py`, so the character check uses the same function instead of a second copy of the directory list, replace this exact text:

```
from pages.fonts import FontRanges
```

with:

```
from pages.fonts import FontRanges
from pages.tests_site_rules import shell_templates
```

and replace this exact method (as Task 6 left it):

```python
    def test_every_shell_template(self):
        problems = self.run_check("backend/templates/shell.html", check_template_characters)
        for directory in ("components", "layouts", "chapters", "pages", "legal", "docs", "blog"):
            problems += self.run_check(f"backend/templates/{directory}/*.html", check_template_characters)
        self.assert_clean(problems)
```

with:

```python
    def test_every_shell_template(self):
        problems = []
        for path in shell_templates():
            text, file_problems = read_file(path, REPO)
            problems += file_problems + (check_template_characters(str(path.relative_to(REPO)), text, FONTS) if text else [])
        self.assert_clean(problems)
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules pages.tests_content -v 1`
Expected: OK. If a failure names a template, fix that template as its message says; never weaken a test.

Check that the rule reaches the same folders the tuple named, and no form, device or OAuth template:

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py shell -c "from pages.tests_site_rules import shell_templates; print(sorted({p.parent.name for p in shell_templates()}))"; cd ..
```

Expected: `['blog', 'chapters', 'components', 'docs', 'layouts', 'legal', 'pages', 'templates']` (`templates` is where `shell.html` sits; ignore a `UserWarning` line about `staticfiles` in a fresh worktree). If `blog`, `docs` or `legal` is missing, its templates do not extend `layouts/reading.html`: stop and report which of Tasks 4 to 6 is incomplete. A folder such as `registration` in the list would mean a template there already extends the shell; the run above names any rule it breaks.

Then add the last template convention. In `backend/pages/tests_site_rules.py`, at the end of `class TemplateRulesTests` (after the test added above), add:

```python
    def test_no_header_or_footer_element_in_a_page_template(self):
        # base.css styles the bare header and footer elements for the shell's own top and bottom
        # (`:root:not(.js) footer { display: none }`), so one inside <main> would vanish without JavaScript.
        element = re.compile(r"<(?:header|footer)\b")
        for path in shell_templates():
            if path.name == "shell.html":
                continue
            with self.subTest(template=str(path.relative_to(REPO))):
                self.assertIsNone(element.search(path.read_text(encoding="utf-8")),
                                  f"{path.relative_to(REPO)}: use <section>, <div> or <nav> here; a page's <main> may not "
                                  "hold a <header> or <footer> (base.css styles those bare elements for the shell)")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules pages.tests_content -v 1`
Expected: OK. If a failure names a reading template (a `<header>` or `<footer>`, an inline `style=`, a component included without `only`, or a character a font lacks), fix that template as its message says; never weaken a test.

Prove the header/footer guard can fail:

```bash
echo '<footer></footer>' >> backend/templates/docs/page.html
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules.TemplateRulesTests.test_no_header_or_footer_element_in_a_page_template; cd ..
git checkout -- backend/templates/docs/page.html
git status --short backend/templates/docs
```

Expected: FAIL naming `backend/templates/docs/page.html: use <section>, <div> or <nav> here; ...`; then `git status` prints nothing for `docs` (the template is restored).

- [ ] **Step 6: Hold the reading pages' test classes to `longMessage = False` (test first)**

The reading pages' tests compare whole pages: `assertIn` and `assertEqual` on up to 160 KB of HTML. Python prints both sides of a failed `assertIn` or `assertEqual` before the message, so the message that names the file and the fix comes after up to 160 KB of HTML, out of reach of a reader (Muse's agents) who takes the first lines of a failure. A class with `longMessage = False` prints the message alone. Tasks 2 to 6 set it on the classes they create; this step makes it a convention that a test holds, for the six modules whose assertions are page-sized.

In `backend/pages/tests_site_rules.py`, at the end of the module (after `test_no_header_or_footer_element_in_a_page_template`, with two blank lines before), add:

```python
class ReadableFailuresTests(SimpleTestCase):
    def test_every_test_class_of_the_reading_pages_prints_only_its_message(self):
        for dotted in READING_TEST_MODULES:
            path = "backend/" + dotted.replace(".", "/") + ".py"
            with self.subTest(module=dotted):
                module = importlib.import_module(dotted)
                self.assertTrue(cases_defined_in(module),
                                f"{path}: defines no TestCase class, so this check sees nothing; if the module was renamed or "
                                "moved, fix READING_TEST_MODULES in backend/pages/tests_site_rules.py")
                loud = cases_that_print_whole_pages(module)
                self.assertEqual(loud, [], f"{path}: {', '.join(loud)} must set `longMessage = False` in the class body. unittest "
                                 "prints both sides of assertIn and assertEqual before the message, which for a page is up to "
                                 "160 KB, and the message that names the file and the fix then comes last, out of reach")

    def test_a_class_that_prints_whole_pages_is_found_by_name(self):
        module = types.ModuleType("fake_reading_tests")

        def define(name, base, **attributes):
            return type(name, (base,), {"__module__": module.__name__, **attributes})

        module.QuietTests = define("QuietTests", SimpleTestCase, longMessage=False)
        module.InheritsQuietTests = define("InheritsQuietTests", module.QuietTests)
        module.LoudTests = define("LoudTests", SimpleTestCase)
        module.SimpleTestCase = SimpleTestCase  # imported, not defined in the module: never counted
        module.not_a_case = lambda: None
        self.assertEqual(sorted(cases_defined_in(module)), ["InheritsQuietTests", "LoudTests", "QuietTests"],
                         "backend/pages/tests_site_rules.py: cases_defined_in() must list the TestCase classes a module defines, "
                         "and none it imports")
        self.assertEqual(cases_that_print_whole_pages(module), ["LoudTests"],
                         "backend/pages/tests_site_rules.py: a class is reported when its longMessage is not False; "
                         "a subclass of a quiet class is quiet")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules.ReadableFailuresTests`
Expected: both ERROR with `NameError` (`READING_TEST_MODULES` and `types` are not defined yet), and `FAILED (errors=2)`.

In `backend/pages/tests_site_rules.py`, replace this exact text:

```
import re
import tempfile
from html.parser import HTMLParser
```

with:

```
import importlib
import re
import tempfile
import types
import unittest
from html.parser import HTMLParser
```

and replace this exact line:

```python
class ReadableFailuresTests(SimpleTestCase):
```

with:

```python
# The test modules whose assertions compare whole pages. Each of their classes sets longMessage = False (ReadableFailuresTests).
READING_TEST_MODULES = ("pages.tests_reading_family", "pages.tests_reading", "pages.tests_legal", "pages.tests_docs_reading",
                        "blog.tests_journal", "blog.tests_feed")


def cases_defined_in(module):
    """{name: class} for every unittest TestCase class the module defines itself, not one it imports."""
    return {name: value for name, value in vars(module).items()
            if isinstance(value, type) and issubclass(value, unittest.TestCase) and value.__module__ == module.__name__}


def cases_that_print_whole_pages(module):
    """The names of the module's TestCase classes whose longMessage is not False (a subclass inherits it from its base)."""
    return sorted(name for name, case in cases_defined_in(module).items() if case.longMessage is not False)


class ReadableFailuresTests(SimpleTestCase):
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules.ReadableFailuresTests -v 2`
Expected: `Ran 2 tests`, both `ok`, then `OK`. (A new test module for the reading pages goes in `READING_TEST_MODULES` and sets `longMessage = False` on its classes; a module outside the list is free to print what it likes.)

Prove the check can fail on a real module. From the repo root:

```bash
python3 - <<'EOF'
from pathlib import Path
path = Path("backend/pages/tests_legal.py")
path.write_text(path.read_text(encoding="utf-8").replace("longMessage = False", "longMessage = True"), encoding="utf-8")
EOF
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules.ReadableFailuresTests.test_every_test_class_of_the_reading_pages_prints_only_its_message; cd ..
git checkout -- backend/pages/tests_legal.py
git status --short backend/pages/tests_legal.py
```

Expected: FAIL. unittest prints the test's own two short lists first (`Lists differ: [...] != []`), then ` : ` and the message, which begins `backend/pages/tests_legal.py:`, names the module's test classes and says they must set `longMessage = False` in the class body; then `git status` prints nothing for `tests_legal.py` (the module is restored).

- [ ] **Step 7: Look for what the move left behind in the tests of the old pages**

Tasks 4 to 6 moved the old-markup tests. What can remain is a list or a class that no page uses any more, which passes without testing anything. From the repo root:

```bash
grep -nE '^\s+"/(docs|blog|terms|privacy|report-abuse)/' backend/drop_core/tests_menu.py
grep -c "TrimmedFooterTests" backend/drop_core/tests_menu.py
grep -nE '^\s+\("/(docs|blog|terms|privacy|report-abuse)/[a-z-]*/?", (True|False)\)' backend/drop_core/tests_ambient.py
grep -n "def test_blog_post_gets_field_and_veil\|def test_docs_page_gets_field_and_veil" backend/drop_core/tests_ambient.py
grep -n 'class="cursor"' backend/drop_core/tests.py backend/drop_core/tests_site_pages.py
```

Expected: nothing from the first (the entries of `MENU_PAGES` and `FOOTER_PAGES` that are bare strings; the `MENU_LINKS` tuples start with `(` and rightly stay: the old menu on the forms still links to these pages); `0` from the second; nothing from the third, fourth and fifth. For each line that prints:
- A reading page still in `MENU_PAGES`, `FOOTER_PAGES` or `FIELD_PAGES`: delete the entry (the page has the shell's markup, so the old assertions on it cannot hold).
- `TrimmedFooterTests` count above 0: the three-link footer is gone from every page it pinned. Delete `FOOTER_PAGES`, the function `_footer` and the class `TrimmedFooterTests` from `backend/drop_core/tests_menu.py`, then delete `import re` (nothing else there uses it) and change the first line of the module docstring to `"""Hamburger menu on the Django pages that have not moved onto the shell yet.`.
- The two `*_gets_field_and_veil` tests or a `class="cursor"` assertion: delete the test (or the loop entry), because it asserts the old page's markup.

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test drop_core.tests_menu drop_core.tests_ambient drop_core.tests_site_pages -v 1`
Expected: OK.

- [ ] **Step 8: Fix comments that still describe the pages as unmoved**

```bash
grep -rn "when Docs move onto the shell\|A page not yet on the shell must\|(Docs, the legal pages)" backend --include='*.py'
```

Expected: one line, `backend/pages/registry.py:7`. Task 2 replaces `context_processors.py` whole and Task 5 Step 7 rewrites the `content_checks.py` reason, so their old words are gone, and Task 2 has already put its sentence about Journal posts and Docs pages into the registry docstring. Make the edit for each line that prints; skip the `content_checks.py` and `context_processors.py` edits when the grep does not print them. Leave `static/js/shell.js:3` and `static/css/tokens.css:4` ("pages not yet on the shell read the same storage") as they are: the forms and the dashboard are not on the shell yet.

In `backend/pages/content_checks.py`, replace this exact line:

```
    "backend/docs/pages/quickstart.md": "the shell switcher and copy boxes, which become components when Docs move onto the shell (PR 4)",
```

with:

```
    "backend/docs/pages/quickstart.md": "the shell switcher and copy boxes, written as the shell's own markup (data-tabs, data-copy) because Markdown has no tabs or Copy button",
```

In `backend/pages/registry.py`, replace this exact text:

```
description of 70 to 160 characters, then run the tests. A page not yet on the shell must
render the same title and description itself (a test compares them).
```

with:

```
description of 70 to 160 characters, then run the tests. Every page here renders its <head>
from its entry (shell.html), and a test compares what it renders with this list.
```

In `backend/pages/context_processors.py`, replace this exact text:

```
    The entry is built when a template first reads it, not on every render: a registry page whose
    template never extends shell.html (Docs, the legal pages) must not fail because img/og.png has no
    manifest entry after a bad collectstatic.
```

with:

```
    The entry is built when a template first reads it, not on every render: a page whose template
    never reads it must not fail because img/og.png has no manifest entry after a bad collectstatic.
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages -v 1`
Expected: OK.

- [ ] **Step 9: llms.txt names the Journal and its feed (test first)**

`docs/llms.txt` is hand-written and served at `/llms.txt`. Nothing in it became wrong (it names the Docs address, which is unchanged), but it never mentioned the Journal, and the feed now carries every post in full, which is what an agent reading it can use. `LlmsTxtTests` in `backend/pages/tests_site_rules.py` is the test that pins it (every chapter and the MCP address).

In `backend/pages/tests_site_rules.py`, replace this exact line:

```
        self.assertIn("https://YOUR-DEPLOY/mcp", text)
```

with:

```
        self.assertIn("https://YOUR-DEPLOY/mcp", text)

    def test_names_the_journal_and_its_feed(self):
        text = (REPO / "docs" / "llms.txt").read_text(encoding="utf-8")
        for name in ("blog-index", "blog-feed"):
            address = registry.SITE_ORIGIN + reverse(name)
            with self.subTest(address=address):
                self.assertTrue(address in text, f"docs/llms.txt: add {address} to 'Pages for people' (the Journal and its full-text feed)")
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules.LlmsTxtTests`
Expected: FAIL for both subtests, with `docs/llms.txt: add https://veilfile.com/blog/ to 'Pages for people' (the Journal and its full-text feed)` and the same for `https://veilfile.com/blog/feed.xml`.

In `docs/llms.txt`, replace this exact line:

```
- Plans and limits: https://veilfile.com/plans/
```

with:

```
- Plans and limits: https://veilfile.com/plans/
- Journal (notes on Veilfile, each post in full in the feed): https://veilfile.com/blog/ and https://veilfile.com/blog/feed.xml
```

Run: `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_rules.LlmsTxtTests drop_core.tests_fix_docs`
Expected: OK (the plan-figure scan reads `docs/llms.txt` too; the new line has no figure).

- [ ] **Step 10: Bring AGENTS.md up to date**

Make each replacement in `AGENTS.md`. Each old text appears exactly once; if one does not match, a task before this one edited the same place: make the smallest equivalent edit so that the result says what the "with" text says. The "Site checks" section, the `site-tests/` line of the repo map, the "The browser checks run as" paragraph and the "wait for" paragraph (the job names) are Task 7's: do not touch them.

(a) Repo map. No earlier task edits this block; the old text is main's. Replace this exact text:

````
backend/blog, docs, legal/   Markdown-rendered pages (legal text is the owner's)
backend/templates/     Django templates: shell.html, layouts/, chapters/ (the threshold and
                       the chapters) and components/ for the new shell; older pages (Docs,
                       Journal, sign-in, device flow, OAuth consent) until they move onto it
backend/pages/         public-page registry and views (threshold, chapters), sitemap,
                       component catalog, content checks
````

with:

````
backend/blog, docs, legal/   Markdown-rendered reading pages: the Journal and its
                       feed, Docs, and the legal documents (the legal text is the
                       owner's)
backend/templates/     Django templates: shell.html, layouts/ (chapter, reading),
                       chapters/ (the threshold and the chapters), components/, and
                       the blog/, docs/ and legal/ pages, all on the new shell; older
                       pages (sign-in, device flow, OAuth consent) until they move
                       onto it
backend/pages/         public-page registry (its reading layout also covers every
                       Journal post and Docs page) and views (threshold, chapters),
                       sitemap, component catalog, content checks
````

(b) What no check can see. Replace this exact text (it spans two lines):

````
masking very tall elements (long Docs or Journal pages, when
they move onto the shell) are unverified in iOS Safari and Android Chrome.
````

with:

````
masking very tall elements (the long Docs, Journal and legal pages, now
on the shell) are unverified in iOS Safari and Android Chrome.
````

(c) "Write a Journal post". Task 6 already rewrote the header-image bullet of step 2 (the image is shown, and a PNG is advised): leave it. Replace this exact text (the end of step 3):

````
   `code`, fenced code blocks, and images with alt text, like
   `![A failing checkout page](/static/img/journal/checkout.png)`. No HTML
   and no styling: the template decides how everything looks.
````

with:

````
   `code`, fenced code blocks (each with a Copy button), and images with alt
   text, like `![A failing checkout page](/static/img/journal/checkout.png)`.
   No HTML and no styling: the template decides how everything looks.
   Nothing else needs registering. The post's address, its place in the index,
   the feed and the sitemap, its link preview, its reading time and the older and
   newer links on the posts beside it all come from this file.
````

(d) The in-place rule for the second layout. In "A new chapter", replace this exact text:

````
3. A `Page` in `backend/pages/registry.py` with `layout="chapter"`. That is all
   in-place changes need: `shell.js` changes in place to every registry page
   of the same layout.
````

with:

````
3. A `Page` in `backend/pages/registry.py` with `layout="chapter"`. That is all
   in-place changes need: `shell.js` changes in place to every registry page
   of the same layout. The other layout is `reading` (the Journal, Docs and
   Terms, Privacy and Report abuse): its paths also include every Journal post
   and Docs page, which `layout_paths()` in the same file lists from their
   loaders, so a new post or Docs page needs no `Page`. A change never crosses
   from one layout to the other; that is an ordinary page load.
````

(e) The two missing recipes (spec 7.1, items 3 and 4). Replace this exact text:

````
7. A line in `PAGES` in `site-tests/run_site_checks.py`, with `chapter=True`.

### Change or add a component
````

with:

````
7. A line in `PAGES` in `site-tests/run_site_checks.py`, with `chapter=True`.

### Edit Docs

Who: usually the CTO's team. The pages describe the API, the MCP server and
setup, so what they say must be true of the code.

1. Open `backend/docs/pages/<name>.md`: `index` (`/docs/`), `quickstart`,
   `agent`, `how-it-works` or `api` (`/docs/<name>/`). The file name is the
   slug.
2. Keep the header at the top of the file, with exactly these five keys:

   ```
   ---
   title: Limits
   slug: limits
   seo_title: Limits | Veilfile docs
   description: How big a file can be, how long a link lasts, and how many uploads each plan allows.
   order: 6
   ---
   ```

   - `title`: 60 characters or fewer. The page shows it as its heading, so do
     not start the body with a heading that repeats it.
   - `seo_title`: 60 characters or fewer, the title search results show.
   - `description`: 70 to 160 characters, plain sentences.
   - `order`: a whole number. Pages are listed from the smallest, and the
     previous and next links at the foot of a page follow it.
3. Write the body in Markdown: `##` headings, paragraphs, lists, tables, links,
   `code` and fenced code blocks (each with a Copy button). A page with two or
   more `##` headings opens with a list of them; `###` headings are on the page
   but not in the list. Every heading gets an address from its words
   (`/docs/api/#errors`), so renaming one breaks links to it: search the repo
   for the old one first. A wide table or a long code line scrolls inside its
   own box; a long word wraps.
4. No raw HTML. The one exception is `quickstart.md` (`RAW_HTML_ALLOWED` in
   `backend/pages/content_checks.py`): its shell tabs and Copy buttons are
   written as the shell's own markup (`data-tabs`, `role="tab"`, `data-copy`)
   because Markdown cannot express them. Change their words; keep every tag,
   attribute and id.
5. Same voice as the chapters (step 3 of "Edit a chapter's words"), and state
   only what the product does. Prices and limits must match `plans.py`, tool
   names and error codes must exist in the code, and documented paths must
   resolve (`backend/drop_core/tests_fix_docs.py` checks all three). The client
   snippets under `### Claude Code`, `### Codex` and `### Cursor` in `agent.md`
   must equal Begin's, which are in `backend/pages/snippets.py`: change them
   only together (step 5 of "Edit a chapter's words"; `pages.tests_chapters`
   checks).
6. A new page is a new file written the same way, with an `order` between its
   neighbours. It joins the Docs index, the sitemap, the previous and next links
   and in-place changes by itself, with no registry entry. Add its slug to
   `DOCS_SLUGS` in `DocsTests` (`backend/drop_core/tests.py`) and raise the 5 in
   `_assert_skipped` in `DocsLoaderResilienceTests`
   (`backend/drop_core/tests_fix_web.py`).
7. Check it:
   `cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_content drop_core.tests_fix_docs drop_core.tests.DocsTests`.
   A failure from `pages.tests_content` names the file, the line, the problem
   and the fix.
8. Open a pull request.

### Legal pages

Who: the owner only. Stop and ask.

The Terms of Service, the Privacy Policy and the abuse policy
(`backend/legal/documents/*.md`) are the owner's words (invariant 8). Do not
edit them, not even for a typo or the "Last updated" line, and do not add a
document. If a code change makes one of them inaccurate, say so in your report
and wait for the owner.

How the pages look is code: `backend/templates/legal/document.html` and the
reading layout's CSS may change while every word stays as it is. A test in
`backend/pages/` renders each document from its Markdown and fails when the
page's words differ. Check it:
`cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages drop_core.tests.LegalTests`.

### Change or add a component
````

(f) The hygiene rule for page-sized assertions. In "Docs and tests hygiene", replace this exact text:

````
- Match surrounding style and comment density. Keep changes focused; no
  drive-by refactors.
````

with:

````
- Match surrounding style and comment density. Keep changes focused; no
  drive-by refactors.
- A test that compares a whole page (up to 160 KB of HTML) sets
  `longMessage = False` on its class and passes a message that names the file
  and the fix, so a failure prints that message and not the page before it.
  `pages.tests_site_rules` (`ReadableFailuresTests`) holds the reading pages'
  test modules to this.
````

Check: `grep -n "^### " AGENTS.md | sed -n 1,12p` lists `### Write a Journal post`, `### Edit a chapter's words`, `### Edit Docs`, `### Legal pages` and `### Change or add a component` in that order.

- [ ] **Step 11: README.md**

In `README.md`, replace this exact line:

```
  blog/, docs/     Markdown-rendered blog and /docs/ pages
```

with:

```
  blog/, docs/     Markdown-rendered Journal posts (and their feed) and Docs pages
```

and replace this exact line:

```
  templates/       shell.html, layouts/, chapters/ and components/, plus the older sign-in and device pages
```

with:

```
  templates/       shell.html, layouts/, chapters/ and components/, the Journal, Docs and legal pages that use them, plus the older sign-in, device and OAuth pages
```

(The job names in `README.md`, `site-tests/package.json` and `site-tests/requirements.txt` are Task 7's.)

- [ ] **Step 12: Amend the spec where the build refined it**

In `docs/superpowers/specs/2026-10-08-site-redesign-design.md`, make each replacement. Each old text appears exactly once; if one does not match, the spec changed since this plan: make the smallest equivalent edit and record a ruling. Do not touch section 4.4 (the menu: `fix/phone-scale` amends it) or the job names in sections 10, 11 and 13 (Task 7 amends them).

§3.4, inline links. Replace:

```
  - **Inline links** have a dotted underline that gives way to the wash.
```

with:

```
  - **Inline links** (in a chapter's paragraphs and definitions, and in a reading page's paragraphs, list items, table cells and quotes) have a dotted underline that gives way to the wash.
```

§4.3, Begin's code blocks (the sentence promised the component to the Docs and the Journal). Replace:

```
The Docs and the Journal will render the same component for their code blocks.
```

with:

```
The Docs and the Journal render the same component for each fenced code block (§4.6).
```

§4.6, the column. Replace:

```
  - Code blocks, tables and long URLs scroll sideways inside their own boxes. The page itself never scrolls sideways.
```

with:

```
  - Code blocks and tables scroll sideways inside their own boxes; a long word or URL in running text wraps. The page itself never scrolls sideways.
  - Every fenced code block in the column (in a Journal post or a Docs page; the legal documents have none) is the code block of §4.3 (`components/code_block.html`), with its Copy button. The feed carries the plain code, and the quickstart's install tabs keep their own Copy buttons.
  - Bold text is Newsreader at weight 500, its heaviest face, with synthetic bold turned off, so `**bold**` is never drawn in a fallback face.
  - The Journal, Docs and legal pages are one layout family. They change in place among themselves (§4.5); a reading page to a chapter, or back, is an ordinary page load. The menu starts closed on a reading page, as on every page (§4.4).
```

§4.6, the post. Replace:

```
  - **Post:** a meta line (JOURNAL · date · reading time), the title, the body, then the previous and next posts.
```

with:

```
  - **Post:** a meta line (JOURNAL · date · reading time), the title, the header image when the post names one (with its alt text), the body, then the older and newer posts. The reading time counts 200 words a minute, rounded up.
```

§4.6, the feed. Replace:

```
  - **Sources:** Markdown files in `backend/blog/posts/`, as today. Addresses under `/blog/` don't change.
```

with:

```
  - **Sources:** Markdown files in `backend/blog/posts/`, as today. Addresses under `/blog/` don't change.
  - **Feed:** `/blog/feed.xml`, titled Veilfile Journal, carries each post's full text beside its description. Every link in it is absolute, on the same origin as the canonical addresses.
```

§4.6, Docs. Replace:

```
- **Docs:** each page opens with a short list of its sections. The sources stay in `backend/docs/pages/*.md`.
```

with:

```
- **Docs:**
  - Each page opens with a short list of its sections (its `##` headings; none when there are fewer than two) and ends with links to the previous and next pages, in the order of their `order`. The index lists the pages.
  - The sources stay in `backend/docs/pages/*.md`. The quickstart's install tabs and Copy buttons use the shell's own `tabs.js` and `copy.js`.
```

§4.6, legal and the head. Replace:

```
- **Legal:** the same layout. The document text is owner-only and does not change in this project.
```

with:

```
- **Legal:**
  - The same layout. A document's own heading is the page's heading, and a list of its sections follows it, as on Docs.
  - The document text is owner-only and does not change in this project: a test compares each page's text with its Markdown source.
- **Head:** every reading page has a unique title, a description, a canonical address and a link preview image (a Journal post's own `image` when its front matter names one). Structured data is in §6.6.
```

§6.1, the old includes. Replace:

```
- The old includes: `ambient.html`, `cursor.html`, `page_transition.html`, and the per-template inline CSS.
```

with:

```
- The old includes: `ambient.html`, the old `menu.html`, `cursor.html`, `page_transition.html`, and the per-template inline CSS. `cursor.html` and `page_transition.html` go with the reading pages (§11, step 4); the others stay while the forms use them (step 5) and go with the rest in step 6.
```

§6.5, the registry and the dynamic pages. Replace:

```
Journal posts and Docs pages are added to the sitemap from their loaders, as today.
```

with:

```
Journal posts and Docs pages are added to the sitemap from their loaders, as today, and the `reading` layout family lists them too: `layout_paths("reading")` is the five registry pages and every published post and Docs page, and a post's or Docs page's head (title, description, canonical address, image and structured data) is built from its loader, so a new post or page needs no registry entry.
```

Replace:

```
a test checks that it mentions every chapter and the MCP address.
```

with:

```
a test checks that it mentions every chapter, the Journal and its feed, and the MCP address.
```

§6.6. Replace:

```
Open Graph tags and an image (img/og.png: the mark on paper, 1200 by 630).
```

with:

```
Open Graph tags and an image (img/og.png: the mark on paper, 1200 by 630; a Journal post that names an `image` uses it).
```

and replace:

```
- **Structured data:** `Article` for Journal posts, `Product` and `Offer` for plans, `Organization` site-wide.
```

with:

```
- **Structured data:** `Article` for Journal posts (as `BlogPosting`) and for Docs pages (as `TechArticle`), both kinds of `Article`; `Product` and `Offer` for plans; `Organization` site-wide. It swaps with the page during in-place changes (§4.5), as the rest of the head does.
```

§7.2. Replace:

```
These template and CSS rules apply to the new shell's files (`shell.html`, `components/`, `layouts/`, `chapters/`, `pages/` and `backend/static/css/`). Older templates are checked as they move onto the shell.
```

with:

```
These template and CSS rules apply to the new shell's files: `shell.html`, `components/`, every template that extends the shell or a layout (wherever it sits: `layouts/`, `chapters/`, `pages/`, `blog/`, `docs/` and `legal/` today) and `backend/static/css/`. Older templates are checked as they move onto the shell: a template is found by what it extends, not by a list of directories, so a page that moves onto the shell is under the rules at once, and a test fails for an `include` or `extends` that names a template that does not exist.
```

§9, semantics. Replace:

```
  - landmarks (`header`, `nav`, `main`, `footer`);
```

with:

```
  - landmarks (`header`, `nav`, `main`, `footer`), with no `header` or `footer` element inside `main`; on reading pages the section list and the previous and next links are each a `nav` with its own label;
```

§10, the browser checks. Replace:

```
  - `site` visits every public page and the main dashboard pages, signed in with a test user.
```

with:

```
  - `site` and `site-reading` between them visit every public page and the main dashboard pages, signed in with a test user.
```

Replace:

```
  - `site` runs at 375px and 1280px, in Paper and Ink, with Motion on and off.
```

with:

```
  - `site` runs at 375px and 1280px, in Paper and Ink, with Motion on and off; `site-reading` runs them in Paper and Ink with Motion on (Motion changes only how a change fades, which its change checks watch).
```

Replace:

```
  - `site` fails on:
```

with:

```
  - `site` and `site-reading` fail on:
```

§11, step 4. Replace:

```
4. **Reading pages:** Journal, Docs and legal.
```

with:

```
4. **Reading pages:** Journal, Docs and legal, on one reading layout, with the `site-reading` job for their browser checks. The old includes only they used (`cursor.html`, `page_transition.html`) go with them; `ambient.html`, the old `menu.html` and `theme_boot.html` stay while the forms use them (step 5) and go in step 6.
```

- [ ] **Step 13: Run the tests that read these documents, and the recipes' own check commands**

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_site_parts pages.tests_content drop_core.tests_fix_docs drop_core.tests.DocsTests drop_core.tests.LegalTests drop_core.tests.BlogTests -v 1
```

Expected: OK. These are the commands the new recipes tell Muse to run, plus the test that reads AGENTS.md, the spec and `ci.yml` for the job names. If `test_the_docs_name_every_site_job_where_they_say_which_checks_are_required` fails, its message names the document that does not name a job. Those places (AGENTS.md's "The browser checks run as" and "wait for" paragraphs, the spec's browser-check-jobs and branch-protection bullets and step 7 of section 11, `ci.yml`'s header) are Task 7's and this task edits none of them: stop and report that Task 7 is incomplete.

If a label in the command above does not exist (a class that an earlier task renamed), find it with `grep -n "^class .*Tests" backend/drop_core/tests.py` and correct the label here and in AGENTS.md's recipe in the same edit, because the recipe must name a command that runs.

- [ ] **Step 14: The whole suite, migrations, production static files and the two script budgets**

From the repo root:

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test
```

Expected: `OK` after about 4 minutes (`main` alone takes 3 minutes 42 seconds; a fresh worktree prints `UserWarning: No directory at: .../backend/staticfiles/`; ignore it). Do not hardcode the test count.

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py makemigrations --check --dry-run
```

Expected: `No changes detected`.

```bash
cd backend && DJANGO_DEBUG=False DJANGO_SECRET_KEY=local-check-not-a-secret DJANGO_ALLOW_SQLITE=True ../venv/bin/python manage.py collectstatic --noinput --verbosity 1 && ls staticfiles/css/components/sections.*.css staticfiles/css/components/pager.*.css staticfiles/css/components/entries.*.css && rm -rf staticfiles
```

Expected: a `static files copied` line with no error (a stylesheet that refers to a file that does not exist fails here, as it would in the Docker build), then one hashed file per name (`sections.<hash>.css`, `pager.<hash>.css`, `entries.<hash>.css`).

The two gzip budgets (spec 8), measured the way the tests measure them:

```bash
cd backend && ../venv/bin/python - <<'EOF'
import gzip
from pathlib import Path
js = Path("static/js")
files = [js / "shell.js", *sorted((js / "behaviors").glob("*.js"))]
print("shell.js + behaviors:", sum(len(gzip.compress(p.read_bytes(), 9)) for p in files), "of", 10 * 1024)
print("veil.js:", len(gzip.compress((js / "veil.js").read_bytes(), 9)), "of", 15 * 1024)
EOF
DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py test pages.tests_static pages.tests_veil
```

Expected: two lines; the first number is 10218, as on `main` after the phone fixes (10,218 of 10,240, 22 bytes to spare), because this PR changes none of the files it counts (`shell.js`, `come-in.js`, `copy.js` and `tabs.js`); the second is at most 15360 (11,789 on `main`; Task 3 adds the reading column's links to `INK_TARGETS` in `veil.js`); then the tests print OK. Record both numbers in your report. If the first is not 10218, stop and report: either this PR touched those files, which it must not, or `main` moved under it.

- [ ] **Step 15: The four parts of the browser checks, once each, timed**

A fresh worktree has none of the checkout's environments: link `venv`, `site-tests/.venv` and `site-tests/node_modules` from the main checkout first. Start the server fresh (templates are cached with `--noreload`, and this task changed templates) and make the staff session (a fresh database needs `migrate` first):

```bash
cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py migrate --noinput && SITE_SESSION_FILE=../site-tests/session.txt DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py shell -c "exec(open('../site-tests/make_staff_session.py').read())"; cd ..
lsof -ti tcp:8777
(cd backend && DJANGO_DEBUG=True DJANGO_SECRET_KEY=dev-only-key ../venv/bin/python manage.py runserver 127.0.0.1:8777 --noreload > ../site-tests/server.log 2>&1 &)
for attempt in $(seq 1 30); do curl -fsS http://127.0.0.1:8777/healthz >/dev/null && break; sleep 1; done; curl -fsS http://127.0.0.1:8777/healthz
```

Expected: `lsof` prints nothing (no old server on the port: the health check cannot tell yours from an old one); the last command prints `{"status": "ok"}`.

Run the four parts one after the other, each with its own screenshots folder (`--part all` runs the same four in one process; the loop times each part on its own):

```bash
caffeinate -ims bash -c 'for part in pages reading cloth ink; do
  echo "=== part $part"
  /usr/bin/time -p site-tests/.venv/bin/python -u site-tests/run_site_checks.py --base-url http://127.0.0.1:8777 --session-file site-tests/session.txt --out site-tests/artifacts/$part --part $part
  echo "exit status $?"
done' > site-tests/parts.log 2>&1
```

Expected for each part: a summary line starting `0 problem(s)` (the "note(s) to review by eye" are not problems; the counts of pages or families depend on the part), `exit status 0`, and three timing lines, `real`, `user` and `sys`, in seconds. Write the four triples in your report. Stop the server afterwards:

```bash
pkill -f "manage.py runserver 127.0.0.1:8777"
```

Report DONE_WITH_CONCERNS, with all the numbers, if any part's `real` exceeds 750 seconds or its `user` plus `sys` exceeds 600 seconds: CI gives each job 20 minutes on a 2-core runner that ran the checks about 1.2 times slower than the planning laptop, so 750 s here is about 15 minutes there, with a minute of setup on top. For reference, the last CI run of `main` (7d94ea2) took 708 s, 577 s and 755 s in the checks steps of the `site` (pages), `site-cloth` and `site-ink` jobs, and the dry run of this PR's reading part took 84 s (user plus sys 92) on an 8-core laptop. The four parts take about half an hour together: start the loop in the background and read the numbers from the log with `grep -E '^=== part|^real|^user|^sys|^exit status|problem\(s\)' site-tests/parts.log`. A problem a part reports is not a timing concern: fix it where it is (a template, a stylesheet, a check) and run that part again; do not change `ci.yml` or the harness's thresholds. The PR notes ask the owner for one CI run of the branch, to read each job's real duration.

- [ ] **Step 16: A final grep that nothing still includes a removed file**

```bash
grep -rn "includes/cursor.html\|includes/page_transition.html" backend site-tests tools .github AGENTS.md README.md docs/llms.txt
ls backend/templates/includes
for f in theme_boot ambient menu; do echo "$f $(grep -rl "includes/$f.html" backend/templates | wc -l | tr -d ' ')"; done
grep -rn 'class="cursor' backend
git status --short
```

Expected: nothing from the first `grep`; `ls` lists `ambient.html`, `menu.html` and `theme_boot.html`; `theme_boot 18`, `ambient 18`, `menu 18`; nothing from the last `grep`; and `git status` lists exactly: the two deleted includes (staged by `git rm`), the files named in Step 17, and the untracked `.DS_Store`, `.claude/` (and, in a fresh worktree, the linked `venv`, `site-tests/.venv`, `site-tests/node_modules`; the screenshots, `parts.log` and `server.log` are ignored by git). Anything else modified is something this task did not mean to change: find out why before committing. (`test_every_include_and_extends_names_a_template_that_exists`, from Step 2, now holds the include rule for good.)

- [ ] **Step 17: Commit**

Check once more that nothing changed on `main` while this ran: `git fetch origin && git log --oneline HEAD..origin/main -- backend site-tests .github AGENTS.md README.md docs`. Expected: nothing (if it lists commits, stop and report instead of committing).

```bash
git add AGENTS.md README.md docs/llms.txt docs/superpowers/specs/2026-10-08-site-redesign-design.md \
  backend/templates/includes/ambient.html backend/pages/registry.py \
  backend/pages/tests_site_rules.py backend/pages/tests_content.py \
  backend/drop_core/tests_ambient.py
git status --short
```

Also `git add`, by name, any other file `git status` shows as modified because of Steps 5 to 8 and 13 (for example `backend/pages/content_checks.py`, `backend/pages/context_processors.py`, `backend/drop_core/tests_menu.py`, `backend/drop_core/tests.py`); never `git add -A`. The two deleted includes are already staged.

```bash
git -c user.name=Veilfile -c user.email=support@veilfile.com commit -m "Recipes for the reading pages, the spec where the build refined it, and the old includes only they used removed" -m "AGENTS.md gains the Edit Docs and Legal pages recipes, and its repo map and Journal recipe describe the reading pages as they are now; the spec records the reading family, the feed, the heads, the section lists and Copy on every code block. cursor.html and page_transition.html are deleted with the wordmark cursor rule, the template rules now follow what a template extends instead of a list of directories, every include and extends must name a template that exists, every test class of the reading pages prints only its message, and llms.txt names the Journal and its feed."
```

Expected: one commit; `git status --short` then shows only the untracked `.DS_Store`, `.claude/` (and a fresh worktree's linked directories). Nothing is pushed.
