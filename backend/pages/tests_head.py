"""Every registry page's <head> comes from the registry (spec 6.5, 6.6)."""
import json
import re
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.template import engines
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import resolve
from django.utils.html import escape

from pages import registry
from pages.context_processors import shell_page

LD_JSON = re.compile(r'<script type="application/ld\+json" data-page-meta>(.*?)</script>', re.DOTALL)


def request_for(path):
    """A request for path that carries the URL name the registry is keyed by."""
    request = RequestFactory().get(path)
    request.user = AnonymousUser()
    request.resolver_match = resolve(path)
    return request


def render_shell_as(path, context=None):
    """shell.html as it renders for path."""
    return render_to_string("shell.html", context or {}, request=request_for(path))


class HeadFromRegistryTests(SimpleTestCase):
    def test_a_registry_page_gets_its_title_description_and_address(self):
        html = render_shell_as("/about/")
        page = registry.page("about")
        canonical = registry.canonical_url("about")
        self.assertIn(f"<title>{escape(page.title)}</title>", html)
        for tag in (
            f'<meta name="description" content="{escape(page.description)}" data-page-meta>',
            f'<link rel="canonical" href="{canonical}" data-page-meta>',
            '<meta property="og:type" content="website" data-page-meta>',
            '<meta property="og:site_name" content="Veilfile" data-page-meta>',
            f'<meta property="og:title" content="{escape(page.title)}" data-page-meta>',
            f'<meta property="og:description" content="{escape(page.description)}" data-page-meta>',
            f'<meta property="og:url" content="{canonical}" data-page-meta>',
            '<meta property="og:image" content="https://veilfile.com/static/img/og.png" data-page-meta>',
            '<meta name="twitter:card" content="summary_large_image" data-page-meta>',
        ):
            with self.subTest(tag=tag):
                self.assertIn(tag, html)

    def test_every_registry_page_says_who_it_belongs_to(self):
        items = [json.loads(block) for block in LD_JSON.findall(render_shell_as("/about/"))]
        self.assertEqual(items[0]["@type"], "Organization")
        self.assertEqual(items[0]["url"], "https://veilfile.com/")
        self.assertEqual(items[0]["logo"], "https://veilfile.com/static/img/apple-touch-icon.png")

    def test_a_view_can_add_structured_data(self):
        html = render_shell_as("/about/", {"structured_data": [{"@type": "Product", "name": "Veilfile"}]})
        self.assertEqual([json.loads(block)["@type"] for block in LD_JSON.findall(html)], ["Organization", "Product"])

    def test_structured_data_cannot_close_its_script(self):
        # _SCRIPT_SAFE escapes <, > and &; check all three, inside the block the view's item sits in.
        html = render_shell_as("/about/", {"structured_data": [{"name": "</script><b> Tom & Jerry"}]})
        self.assertNotIn("</script><b>", html)
        self.assertIn("\\u003c/script\\u003e\\u003cb\\u003e", html)
        block = LD_JSON.findall(html)[-1]  # the view's item comes after the Organization
        self.assertIn("Tom \\u0026 Jerry", block)
        self.assertNotIn("&", block)

    def test_a_page_outside_the_registry_gets_a_bare_head(self):
        html = render_shell_as("/app/components/")
        self.assertIn("<title>Veilfile</title>", html)
        self.assertNotIn("data-page-meta", html)
        self.assertNotIn("data-layout", html)

    def test_the_reading_face_preload_sits_in_a_block_pages_can_empty(self):
        # Rendered, not read from the source: a block that was mis-nested or repeated outside itself would pass
        # a grep of shell.html and still leave the download on a page that has no reading text.
        preload = "fonts/newsreader/normal-400-500-latin.woff2"
        self.assertIn(preload, render_shell_as("/about/"))
        child = engines["django"].from_string('{% extends "shell.html" %}{% block preload_reading %}{% endblock %}')
        html = child.render({}, request_for("/about/"))
        self.assertNotIn(preload, html)
        self.assertIn("fonts/ibm-plex-mono/normal-400-latin.woff2", html)  # emptying the block leaves the other preload


def static_without_the_link_preview_image(path):
    """What `static` does after a bad collectstatic: no manifest entry for img/og.png."""
    if path == "img/og.png":
        raise ValueError("Missing staticfiles manifest entry for 'img/og.png'")
    return f"/static/{path}"


class ShellPageIsLazyTests(TestCase):
    def test_the_value_is_built_only_when_read(self):
        request = request_for("/about/")
        with mock.patch("pages.context_processors.static", static_without_the_link_preview_image):
            page = shell_page(request)["shell_page"]  # handing it to a template builds nothing
            with self.assertRaises(ValueError):
                page["image"]
        page = shell_page(request)["shell_page"]
        self.assertTrue(page)
        self.assertEqual(page["title"], registry.page("about").title)
        self.assertEqual(page["layout"], "chapter")
        self.assertIn("/about/", page["layout_paths"].split())

    def test_a_page_outside_the_registry_still_gets_a_falsy_value(self):
        self.assertFalse(shell_page(request_for("/app/components/"))["shell_page"])
