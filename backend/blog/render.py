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
