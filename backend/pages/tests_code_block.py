"""The code block (spec 4.3, 6.2): a snippet in a focusable scroll box with a Copy button that never covers it, and the status Copy speaks."""
import re
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase

STATIC = Path(settings.BASE_DIR) / "static"


def render_block(**inputs):
    return render_to_string("components/code_block.html", {"id": "t-one", "code": "npm install -g veilfile", "label": "Copy the install command", **inputs})


class CodeBlockTests(SimpleTestCase):
    def test_the_snippet_is_a_focusable_scroll_box(self):
        # tabindex lets a keyboard user scroll a long command; the id is what Copy reads.
        self.assertIn('<pre tabindex="0" id="t-one"><code>npm install -g veilfile</code></pre>', render_block())

    def test_the_code_is_escaped_by_the_template_and_keeps_its_lines(self):
        html = render_block(code='cat <<EOF\n  "a" & <b>\nEOF')
        self.assertIn("<code>cat &lt;&lt;EOF\n  &quot;a&quot; &amp; &lt;b&gt;\nEOF</code>", html)
        self.assertNotIn("<b>", html)

    def test_copy_reads_the_block_by_its_id_and_is_named_by_its_label(self):
        html = render_block()
        self.assertIn('<button class="code-block__copy label interactive" type="button" data-copy="t-one" aria-label="Copy the install command">Copy</button>', html)
        html = render_block(label='Copy the "quoted" <command>')
        self.assertIn('aria-label="Copy the &quot;quoted&quot; &lt;command&gt;"', html)

    def test_the_status_is_one_polite_region_in_the_block_and_empty_until_copy_speaks(self):
        html = render_block()
        self.assertEqual(html.count('role="status" data-copy-status></p>'), 1)
        self.assertIn('<p class="visually-hidden" role="status" data-copy-status></p>', html)
        self.assertTrue(html.lstrip().startswith('<div class="code-block" data-code-block>'))
        self.assertTrue(html.rstrip().endswith("</div>"))

    def test_copy_looks_for_its_status_inside_the_block(self):
        # With one block on a page the document's first status would do; with five, each Copy speaks in its own block.
        script = (STATIC / "js/behaviors/copy.js").read_text(encoding="utf-8")
        self.assertIn('button.closest("[data-tabs], [data-code-block]")', script)

    def test_copy_is_not_shown_without_javascript(self):
        css = (STATIC / "css/components/code_block.css").read_text(encoding="utf-8")
        self.assertRegex(css, r":root:not\(\.js\) \.code-block__copy \{\s*display: none;")

    def test_the_stylesheet_keeps_the_button_in_a_row_of_its_own_above_the_code(self):
        css = (STATIC / "css/components/code_block.css").read_text(encoding="utf-8")
        self.assertNotRegex(css, r"position:\s*absolute", "code_block.css: the button is a grid item in its own row, never laid over the code")
        self.assertNotIn("@media", css, "code_block.css: one layout at every width, the code has the whole of it")
        self.assertRegex(css, r"\.code-block \{\s*display: grid;\s*grid-template-columns: minmax\(0, 1fr\);")

    def test_the_button_comes_before_the_code_in_the_markup_and_in_the_row_above_it(self):
        html = render_block()
        self.assertLess(html.index("<button"), html.index("<pre"))
