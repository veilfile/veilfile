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
    description = "The Veilfile Journal shares launches, changes, and notes on how Veilfile is built."

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
