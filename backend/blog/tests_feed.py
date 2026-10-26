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
        self.assertEqual(channel.findtext("description"), "The Veilfile Journal shares launches, changes, and notes on how Veilfile is built.")

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
        files = [BACKEND / "blog" / "feeds.py", *sorted((BACKEND / "templates").rglob("*.html"))]
        for path in files:
            with self.subTest(file=str(path.relative_to(REPO))):
                self.assertNotIn("veilfile blog", path.read_text(encoding="utf-8").lower(),
                                 f"{path.relative_to(REPO)}: the feed and the page are called Veilfile Journal; change the name")
