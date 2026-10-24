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
