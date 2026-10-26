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
        self.assertIn("The Journal has no posts yet.", text, "backend/templates/blog/index.html: an empty Journal says 'The Journal has no posts yet.'")
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
