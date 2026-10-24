"""Content checks (spec 7.2): every failure names the file, the line, the problem and the fix."""
import re
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from pages.content_checks import (
    check_doc_page, check_legal_document, check_post, check_template_characters, read_file,
)
from pages.fonts import FontRanges
from pages.tests_site_rules import shell_templates

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

    def test_a_code_block_ends_only_at_its_own_fence(self):
        # Inside a ~~~ block a ``` line is code, and a ```` block holds ``` lines (CommonMark).
        for block in ("~~~\n```\n<div>code</div>\n~~~", "````\n```\n<div>code</div>\n```\n````"):
            with self.subTest(block=block.split("\n")[0]):
                self.assertEqual(self.check(GOOD_POST + "\n" + block + "\n"), [])

    def test_an_unclosed_code_block_names_the_fix(self):
        # Unclosed, the block would run to the end: the rest of the post shows as code, unchecked.
        text = GOOD_POST + "\n~~~\nprint('hello')\n\n## Next\n"
        problems = "\n".join(self.check(text))
        line = text.split("\n").index("~~~") + 1
        self.assertIn(f"{POST_PATH}:{line}: the code block opened here is never closed", problems)
        self.assertIn("Fix: add a line ~~~ after the code", problems)

    def test_empty_title_names_the_fix(self):
        problems = "\n".join(self.check(post(title="")))
        self.assertIn(f"{POST_PATH}:2: the title is empty", problems)
        self.assertIn("Fix: write a title of 60 or fewer characters", problems)

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

    def test_docs_pages_report_an_unclosed_code_block(self):
        text = self.GOOD_DOC + "\n```bash\ncurl https://veilfile.com\n"
        problems = "\n".join(messages(check_doc_page("backend/docs/pages/agent.md", text, FONTS)))
        self.assertIn("the code block opened here is never closed", problems)

    def test_docs_titles_must_not_be_empty(self):
        for key in ("title", "seo_title"):
            with self.subTest(key=key):
                text = re.sub(rf"^{key}: .*$", f"{key}:", self.GOOD_DOC, flags=re.MULTILINE)
                problems = "\n".join(messages(check_doc_page("backend/docs/pages/agent.md", text, FONTS)))
                self.assertIn(f"the {key} is empty", problems)

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
        problems = []
        for path in shell_templates():
            text, file_problems = read_file(path, REPO)
            problems += file_problems + (check_template_characters(str(path.relative_to(REPO)), text, FONTS) if text else [])
        self.assert_clean(problems)
