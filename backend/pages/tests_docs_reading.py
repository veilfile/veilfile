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
