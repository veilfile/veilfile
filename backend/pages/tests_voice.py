"""The voice rules (PR 7), held as tests: no heading ends with a full stop, the Markdown pages keep the
voice, and the claims that were wrong stay fixed."""
import re
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from pages import registry
from pages.tests_chapters import BANNED

BACKEND = Path(settings.BASE_DIR)
REPO = BACKEND.parent


def template_text(source):
    """The source with every {% %}, {{ }} and {# #} removed and the line breaks kept."""
    return re.sub(r"\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}", lambda m: "\n" * m.group().count("\n"), source, flags=re.DOTALL)


class _Headings(HTMLParser):
    TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "dt", "title"}

    def __init__(self):
        super().__init__()
        self.found, self._open = [], None

    def handle_starttag(self, tag, attrs):
        if tag in self.TAGS:
            self._open = (self.getpos()[0], tag, [])

    def handle_data(self, data):
        if self._open:
            self._open[2].append(data)

    def handle_endtag(self, tag):
        if self._open and tag == self._open[1]:
            line, _, parts = self._open
            self.found.append((line, " ".join("".join(parts).split())))
            self._open = None


def html_headings(html):
    """(line, visible text) for every h1 to h6, dt and title element."""
    parser = _Headings()
    parser.feed(html)
    parser.close()
    return parser.found


def markdown_headings(text):
    """(line, text) for every Markdown heading line outside fenced code."""
    found, fenced = [], False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and re.match(r"#{1,6} ", line):
            found.append((number, line.lstrip("#").strip()))
    return found


def ends_with_full_stop(text):
    """True when the text ends with a full stop that is not an ellipsis."""
    text = text.strip()
    return text.endswith(".") and not text.endswith("...")


def stop_message(path, line, text):
    return f"{path}:{line}: the heading {text!r} ends with a full stop; drop it, since a heading is a label"


def stops_in(headings, path):
    return [stop_message(path, line, text) for line, text in headings if ends_with_full_stop(text)]


class HelperTests(SimpleTestCase):
    def test_the_full_stop_rule(self):
        self.assertTrue(ends_with_full_stop("Nothing here."))
        self.assertFalse(ends_with_full_stop("Nothing here"))
        self.assertFalse(ends_with_full_stop("Why shouldn’t I use a gist?"))
        self.assertFalse(ends_with_full_stop("Wait..."))

    def test_headings_are_found_with_template_tags_removed(self):
        html = template_text('<h1 class="x">Switch to {{ plan }}.</h1>\n<dt>Why?</dt>')
        self.assertEqual(html_headings(html), [(1, "Switch to ."), (2, "Why?")])

    def test_template_text_keeps_line_breaks_inside_removed_tags(self):
        self.assertEqual(html_headings(template_text('{# a\nb #}\n<h1>Gone.</h1>')), [(3, "Gone.")])

    def test_markdown_headings_skip_fenced_code(self):
        self.assertEqual(markdown_headings("# A\n```\n# not\n```\n## B.\n"), [(1, "A"), (5, "B.")])


class HeadingStopTests(TestCase):
    def test_templates(self):
        problems = []
        for path in sorted((BACKEND / "templates").rglob("*.html")):
            problems += stops_in(html_headings(template_text(path.read_text())), path)
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_markdown_pages(self):
        problems = []
        for folder in (BACKEND / "docs" / "pages", BACKEND / "blog" / "posts"):
            for path in sorted(folder.glob("*.md")):
                problems += stops_in(markdown_headings(path.read_text()), path)
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_registry_titles(self):
        problems = [stop_message("pages/registry.py", page.url_name, page.title)
                    for page in registry.PAGES if ends_with_full_stop(page.title)]
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_rendered_public_pages(self):
        problems = []
        for page in registry.PAGES:
            url = reverse(page.url_name)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            problems += stops_in(html_headings(response.content.decode()), url)
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_rendered_error_pages(self):
        problems = []
        for name, context in (("404.html", {}), ("404.html (gone link)", {"gone_link": True}),
                              ("403.html", {}), ("403_csrf.html", {}), ("500.html", {})):
            html = render_to_string(name.split(" ")[0], context)
            problems += stops_in(html_headings(html), f"templates/{name} (rendered)")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


def prose_lines():
    """(path, line number, line) for the Markdown pages and llms.txt, outside fenced code, inline code removed."""
    files = sorted((BACKEND / "docs" / "pages").glob("*.md")) + sorted((BACKEND / "blog" / "posts").glob("*.md"))
    files.append(REPO / "docs" / "llms.txt")
    for path in files:
        fenced = False
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if not fenced:
                yield path, number, line.replace("![", "").split("`")[0::2]


class MarkdownVoiceTests(SimpleTestCase):
    def test_no_em_dash_exclamation_mark_or_banned_word(self):
        problems = []
        for path, number, pieces in prose_lines():
            text = " ".join(pieces)
            if "\u2014" in text:
                problems.append(f"{path}:{number}: an em dash in {text!r}; use a comma, a colon or a new sentence")
            if "!" in text:
                problems.append(f"{path}:{number}: an exclamation mark in {text!r}; end the sentence with a full stop")
            for word in BANNED:
                if word in text.lower():
                    problems.append(f"{path}:{number}: the banned phrase {word.strip()!r} in {text!r}; say it plainly")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


OLD_PHRASES = (
    ("backend/templates/device/includes/grant_summary.html", "Disconnect every app"),
    ("backend/templates/device/includes/grant_summary.html", "revoke any of them"),
    ("backend/docs/pages/api.md", "Every Veilfile endpoint"),
    ("backend/templates/chapters/how.html", "anything shaped like a secret"),
    ("backend/templates/chapters/promises.html", "anything shaped like a key or a token"),
    ("backend/templates/chapters/promises.html", "We look only for the shapes of secrets"),
    ("backend/pages/registry.py", "Updated twice a month"),
    ("backend/blog/posts/introducing-veilfile.md", "issued per agent"),
    ("backend/blog/posts/introducing-veilfile.md", "not by extension"),
    ("backend/blog/posts/introducing-veilfile.md", "required on every account"),
    ("backend/docs/pages/agent.md", "key rotation, and instant revocation"),
    ("backend/docs/pages/index.md", "full REST and MCP API reference"),
    ("backend/pages/registry.py", "full REST and MCP API reference"),
    ("backend/docs/pages/how-it-works.md", "Nothing caches the bytes"),
    ("docs/llms.txt", "go to NCMEC"),
)


class WrongClaimTests(SimpleTestCase):
    def test_the_fourteen_claims_stay_fixed(self):
        for relative, phrase in OLD_PHRASES:
            with self.subTest(file=relative, phrase=phrase):
                self.assertNotIn(
                    phrase, (REPO / relative).read_text(),
                    f"{relative}: the old wording {phrase!r} is back; it was wrong, so use the corrected wording")
        self.assertIn("apparent child sexual exploitation", (REPO / "docs" / "llms.txt").read_text(),
                      "docs/llms.txt: the report line must say 'apparent child sexual exploitation'")


class ClaudeAppsTwinTests(SimpleTestCase):
    longMessage = False

    def test_begin_and_the_docs_give_the_same_claude_apps_steps(self):
        for relative in ("backend/templates/chapters/begin.html", "backend/docs/pages/agent.md"):
            text = (REPO / relative).read_text()
            for step in ("Customize, then Connectors", "Register automatically"):
                with self.subTest(file=relative, step=step):
                    self.assertIn(
                        step, text,
                        f"{relative}: the Claude apps steps must say {step!r}, as Begin and the Docs give the same steps")
