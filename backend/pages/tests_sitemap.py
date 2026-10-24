"""sitemap.xml (spec 6.5): built from the registry, the Docs pages and the published Journal posts."""
from django.test import TestCase

from blog import loader as blog_loader
from docs import loader as docs_loader
from pages import registry
from pages.views import sitemap


class SitemapTests(TestCase):
    def setUp(self):
        self.response = self.client.get("/sitemap.xml")
        self.xml = self.response.content.decode()

    def test_is_served_by_the_pages_app_as_xml(self):
        self.assertEqual(self.response.status_code, 200)
        self.assertEqual(self.response["Content-Type"], "application/xml")
        self.assertIs(self.response.resolver_match.func, sitemap)

    def test_lists_every_registry_page(self):
        for page in registry.PAGES:
            if page.in_sitemap:
                with self.subTest(page=page.url_name):
                    self.assertIn(f"<loc>{registry.canonical_url(page.url_name)}</loc>", self.xml)

    def test_lists_every_docs_page(self):
        for doc in docs_loader.load_pages():
            if doc.slug != "index":
                with self.subTest(doc=doc.slug):
                    self.assertIn(f"<loc>https://veilfile.com/docs/{doc.slug}/</loc>", self.xml)

    def test_only_posts_carry_a_lastmod(self):
        # A post has a real date. Pages and Docs have none we can trust, and a guessed or
        # hard-coded lastmod teaches search engines to ignore the field, so they leave it out.
        for page in registry.PAGES:
            if page.in_sitemap:
                with self.subTest(page=page.url_name):
                    self.assertIn(f"<loc>{registry.canonical_url(page.url_name)}</loc></url>", self.xml)
        for doc in docs_loader.load_pages():
            if doc.slug != "index":
                with self.subTest(doc=doc.slug):
                    self.assertIn(f"<loc>https://veilfile.com/docs/{doc.slug}/</loc></url>", self.xml)

    def test_lists_published_posts_with_their_dates(self):
        posts = blog_loader.load_posts()
        self.assertTrue(posts)
        for post in posts:
            with self.subTest(post=post.slug):
                self.assertIn(
                    f"<loc>https://veilfile.com/blog/{post.slug}/</loc><lastmod>{post.date:%Y-%m-%d}</lastmod>",
                    self.xml,
                )

    def test_each_address_appears_once(self):
        locations = [line for line in self.xml.splitlines() if "<loc>" in line]
        self.assertEqual(len(locations), len(set(locations)))
