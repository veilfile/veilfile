"""The reading family (spec 4.5, 4.6, 6.5, 6.6): the Journal, Docs and legal pages share one layout, and a Journal
post or a Docs page gets the head a registry page gets, built from its file.

Every class sets longMessage = False: a failed assertion then prints its own message, which names the file and the
fix, and not the whole page or list it compared first (the message carries what was found where that matters)."""
import json
import re
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils.html import escape

from blog import loader as blog_loader
from blog.views import RETIRED_SLUGS
from docs import loader as docs_loader
from pages import registry
from pages.context_processors import shell_page
from pages.tests_head import LD_JSON, render_shell_as, request_for


def post_file(slug, title, date, description="A description of the post, long enough to be a snippet.", extra=""):
    """The text of a Journal post file: front matter, then a one-line body."""
    lines = ["---", f"title: {title}", f"date: {date}", f"slug: {slug}"]
    if description:
        lines.append(f"description: {description}")
    lines += extra.splitlines()
    return "\n".join(lines + ["---", "", "Body.", ""])


READING_BODY = ("backend/templates/shell.html: a reading page's <body> carries data-layout=\"reading\" "
                "(shell_page.layout), which is what lets shell.js change in place within the family")


class TempContentMixin:
    """Swap the Journal's posts, or the Docs pages, for a few files made for one test."""

    def use_files(self, loader, directory_name, files):
        directory = Path(tempfile.mkdtemp(prefix="veilfile-reading-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        for name, text in files.items():
            (directory / name).write_text(text, encoding="utf-8")
        patcher = mock.patch.object(loader, directory_name, directory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_posts(self, files):
        self.use_files(blog_loader, "POSTS_DIR", files)

    def use_docs(self, files):
        self.use_files(docs_loader, "PAGES_DIR", files)


class ReadingFamilyTests(SimpleTestCase):
    longMessage = False

    def test_the_reading_layout_is_the_docs_the_journal_and_the_legal_pages(self):
        reading = [page.url_name for page in registry.PAGES if page.layout == "reading"]
        self.assertEqual(reading, ["docs-index", "blog-index", "terms", "privacy", "report-abuse"],
                         "backend/pages/registry.py: the Docs index, the Journal index and the three legal pages "
                         "are the registry pages with layout=\"reading\"; give a new reading page the same "
                         f"(found {reading})")
        chapters = [page.url_name for page in registry.PAGES if page.layout == "chapter"]
        self.assertEqual(chapters, ["landing", "about", "how", "begin", "plans", "promises"],
                         f"backend/pages/registry.py: the threshold and the chapters keep layout=\"chapter\" (found {chapters})")

    def test_the_chapter_family_is_what_it_was(self):
        self.assertEqual(registry.layout_paths("chapter"), ("/", "/about/", "/how/", "/begin/", "/plans/", "/promises/"),
                         "backend/pages/registry.py: layout_paths('chapter') is every chapter page's address in "
                         "registry order, as the context processor listed them before the reading layout existed "
                         f"(found {registry.layout_paths('chapter')})")

    def test_a_page_with_no_layout_has_no_family(self):
        self.assertEqual(registry.layout_paths(""), (), "backend/pages/registry.py: a page with no layout changes in place to nothing")
        self.assertEqual(registry.layout_paths("no-such-layout"), ())

    def test_the_reading_family_is_the_five_pages_then_every_post_and_every_docs_page(self):
        posts, pages = blog_loader.load_posts(), docs_loader.load_pages()
        self.assertTrue(posts and pages, "backend/blog/posts/ and backend/docs/pages/ should hold the content this test reads")
        expected = ["/docs/", "/blog/", "/terms/", "/privacy/", "/report-abuse/"]
        expected += [f"/blog/{post.slug}/" for post in posts]
        expected += [f"/docs/{page.slug}/" for page in pages]
        self.assertEqual(list(registry.layout_paths("reading")), expected,
                         "backend/pages/registry.py: layout_paths('reading') lists the five reading pages, then "
                         "/blog/<slug>/ for each published post and /docs/<slug>/ for each Docs page; shell.js changes "
                         f"in place to these addresses only (found {registry.layout_paths('reading')})")

    def test_no_address_repeats_or_holds_a_space(self):
        paths = registry.layout_paths("reading")
        self.assertEqual(len(paths), len(set(paths)), "backend/pages/registry.py: an address is listed twice in layout_paths('reading')")
        for path in paths:
            self.assertNotRegex(path, r"\s", "backend/pages/registry.py: data-layout-paths is a space-separated list, so an "
                                             "address must not hold whitespace; keep the slug check in blog/loader.py and docs/loader.py")


class FutureDatedPostTests(TempContentMixin, TestCase):
    longMessage = False

    def test_a_post_dated_in_the_future_is_not_in_the_family_and_has_no_entry(self):
        self.use_posts({
            "past.md": post_file("past-post", "A post from the past", "2020-01-01"),
            "future.md": post_file("future-post", "A post from the future", "2999-01-01"),
        })
        paths = registry.layout_paths("reading")
        self.assertIn("/blog/past-post/", paths,
                      "backend/pages/registry.py: layout_paths lists a post dated today or earlier")
        self.assertNotIn("/blog/future-post/", paths,
                         "backend/pages/registry.py: layout_paths must list published posts only (blog.loader.load_posts)")
        self.assertEqual(shell_page(request_for("/blog/future-post/")), {"shell_page": None},
                         "backend/pages/context_processors.py: a post that is not published has no head entry")
        self.assertEqual(self.client.get("/blog/future-post/").status_code, 404)
        self.assertEqual(shell_page(request_for("/blog/past-post/"))["shell_page"]["title"], "A post from the past | Veilfile blog")


class ReadingEntryTests(TempContentMixin, TestCase):
    longMessage = False

    def entry(self, path):
        return shell_page(request_for(path))["shell_page"]

    def a_docs_page(self):
        return next(page for page in docs_loader.load_pages() if page.slug != "index")

    def test_a_post_has_an_entry_with_the_keys_of_a_registry_page(self):
        post = blog_loader.load_posts()[0]
        entry = self.entry(f"/blog/{post.slug}/")
        self.assertEqual(set(entry), set(self.entry("/about/")),
                         "backend/pages/context_processors.py: a post's entry has the same keys as a registry page's")
        self.assertEqual(entry["title"], f"{post.title} | Veilfile blog",
                         "backend/pages/context_processors.py: a post's <title> is '<title> | Veilfile blog', as blog/post.html "
                         "rendered it; changing it changes search results")
        self.assertEqual(entry["description"], post.description)
        self.assertEqual(entry["canonical"], f"https://veilfile.com/blog/{post.slug}/")
        self.assertEqual(entry["layout"], "reading")
        self.assertEqual(entry["layout_paths"].split(), list(registry.layout_paths("reading")))
        self.assertIn(f"/blog/{post.slug}/", entry["layout_paths"].split())
        self.assertEqual(entry["image"], "https://veilfile.com/static/" + (post.image or "img/og.png"))
        self.assertEqual(entry["article"], {"type": "BlogPosting", "headline": post.title, "published": post.date})

    def test_a_docs_page_has_an_entry_with_its_seo_title(self):
        page = self.a_docs_page()
        entry = self.entry(f"/docs/{page.slug}/")
        self.assertEqual(set(entry), set(self.entry("/about/")))
        self.assertEqual(entry["title"], page.seo_title or page.title,
                         "backend/pages/context_processors.py: a Docs page's <title> is its seo_title, else its title, as "
                         "docs/page.html rendered it; changing it changes search results")
        self.assertEqual(entry["description"], page.description)
        self.assertEqual(entry["canonical"], f"https://veilfile.com/docs/{page.slug}/")
        self.assertEqual(entry["layout"], "reading")
        self.assertEqual(entry["layout_paths"].split(), list(registry.layout_paths("reading")))
        self.assertEqual(entry["image"], "https://veilfile.com/static/img/og.png")
        self.assertEqual(entry["article"], {"type": "TechArticle", "headline": page.title, "published": None})

    def test_a_docs_page_with_no_seo_title_uses_its_title(self):
        self.use_docs({"plain.md": "---\ntitle: A plain page\nslug: plain\ndescription: A page with no seo title.\norder: 7\n---\n\nBody.\n"})
        self.assertEqual(self.entry("/docs/plain/")["title"], "A plain page",
                         "backend/pages/context_processors.py: with no seo_title a Docs page's <title> is its title")

    def test_the_docs_index_under_its_slug_has_the_docs_index_head(self):
        entry = self.entry("/docs/index/")
        self.assertEqual(entry["title"], registry.page("docs-index").title)
        self.assertEqual(entry["canonical"], "https://veilfile.com/docs/",
                         "backend/pages/context_processors.py: /docs/index/ shows the index page, whose address is /docs/")
        self.assertIsNone(entry["article"])

    def test_a_registry_page_is_not_an_article(self):
        for path in ("/about/", "/docs/", "/blog/", "/terms/", "/privacy/", "/report-abuse/"):
            with self.subTest(path=path):
                self.assertIsNone(self.entry(path)["article"],
                                  "backend/pages/context_processors.py: an index or legal page is not an Article (spec 6.6)")

    def test_the_reading_registry_pages_list_the_whole_family(self):
        for path in ("/docs/", "/blog/", "/terms/", "/privacy/", "/report-abuse/"):
            with self.subTest(path=path):
                entry = self.entry(path)
                self.assertEqual(entry["layout"], "reading")
                self.assertEqual(entry["layout_paths"].split(), list(registry.layout_paths("reading")))

    def test_a_post_with_a_header_image_previews_with_it(self):
        self.use_posts({"with-image.md": post_file("with-image", "With an image", "2020-01-01",
                                                   extra="image: img/apple-touch-icon.png\nimage_alt: The Veilfile mark.")})
        post = blog_loader.get_post("with-image")
        self.assertEqual((post.image, post.image_alt), ("img/apple-touch-icon.png", "The Veilfile mark."),
                         "backend/blog/loader.py: a post's front matter 'image' and 'image_alt' are kept on the Post")
        self.assertEqual(self.entry("/blog/with-image/")["image"], "https://veilfile.com/static/img/apple-touch-icon.png",
                         "backend/pages/context_processors.py: a post with an 'image' previews with it, as an absolute address")

    def test_a_post_with_no_header_image_previews_with_the_mark(self):
        self.use_posts({"plain.md": post_file("plain-post", "A plain post", "2020-01-01")})
        post = blog_loader.get_post("plain-post")
        self.assertEqual((post.image, post.image_alt), ("", ""))
        self.assertEqual(self.entry("/blog/plain-post/")["image"], "https://veilfile.com/static/img/og.png")

    def test_the_entry_is_built_only_when_read(self):
        post = blog_loader.load_posts()[0]
        request = request_for(f"/blog/{post.slug}/")
        with mock.patch("pages.context_processors.static",
                        side_effect=ValueError("Missing staticfiles manifest entry for 'img/og.png'")):
            entry = shell_page(request)["shell_page"]  # handing it to a template builds nothing
            with self.assertRaises(ValueError, msg="backend/pages/context_processors.py: the entry is a SimpleLazyObject, built on first read"):
                entry["image"]

    def test_an_unknown_slug_has_no_entry_and_is_a_plain_404(self):
        for path in ("/blog/no-such-post/", "/docs/no-such-page/"):
            with self.subTest(path=path):
                self.assertEqual(shell_page(request_for(path)), {"shell_page": None},
                                 "backend/pages/context_processors.py: an unknown slug has no entry and raises nothing; the view answers 404")
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_a_retired_post_address_still_redirects_and_has_no_entry(self):
        for old, new in RETIRED_SLUGS.items():
            with self.subTest(old=old):
                self.assertEqual(shell_page(request_for(f"/blog/{old}/")), {"shell_page": None})
                response = self.client.get(f"/blog/{old}/")
                self.assertEqual((response.status_code, response["Location"]), (301, f"/blog/{new}/"))

    def test_every_page_inside_a_menu_section_has_an_entry(self):
        post, page = blog_loader.load_posts()[0], self.a_docs_page()
        samples = {"blog-post": f"/blog/{post.slug}/", "docs-page": f"/docs/{page.slug}/"}
        self.assertEqual(set(samples), set(registry.SECTIONS),
                         "backend/pages/registry.py: SECTIONS names a page that comes from files and has no registry entry; "
                         "give it a head entry in backend/pages/context_processors.py (shell_page) and a row in this test")
        for name, path in samples.items():
            with self.subTest(page=name):
                self.assertTrue(self.entry(path), f"backend/pages/context_processors.py: shell_page gives {name} no head entry")


class ReadingHeadTests(TempContentMixin, TestCase):
    longMessage = False

    def test_a_post_head_says_it_is_an_article_published_on_its_date(self):
        post = blog_loader.load_posts()[0]
        path = f"/blog/{post.slug}/"
        canonical = f"https://veilfile.com{path}"
        html = render_shell_as(path)
        for tag in (
            f"<title>{escape(post.title + ' | Veilfile blog')}</title>",
            f'<meta name="description" content="{escape(post.description)}" data-page-meta>',
            f'<link rel="canonical" href="{canonical}" data-page-meta>',
            '<meta property="og:type" content="article" data-page-meta>',
            f'<meta property="og:title" content="{escape(post.title + " | Veilfile blog")}" data-page-meta>',
            f'<meta property="og:url" content="{canonical}" data-page-meta>',
            f'<meta property="article:published_time" content="{post.date.isoformat()}" data-page-meta>',
        ):
            with self.subTest(tag=tag):
                self.assertIn(tag, html, "backend/templates/shell.html and backend/pages/context_processors.py: "
                                         f"a Journal post's head should hold {tag}")
        self.assertIn('data-layout="reading"', html, READING_BODY)
        self.assertIn(path, re.search(r'data-layout-paths="([^"]*)"', html).group(1).split(),
                      f"backend/pages/context_processors.py: {path} is one of the addresses in its own data-layout-paths")

    def test_a_docs_page_head_says_it_is_an_article_with_no_date(self):
        page = next(page for page in docs_loader.load_pages() if page.slug != "index")
        path = f"/docs/{page.slug}/"
        html = render_shell_as(path)
        for tag in (
            f"<title>{escape(page.seo_title or page.title)}</title>",
            f'<link rel="canonical" href="https://veilfile.com{path}" data-page-meta>',
            '<meta property="og:type" content="article" data-page-meta>',
        ):
            with self.subTest(tag=tag):
                self.assertIn(tag, html, "backend/templates/shell.html and backend/pages/context_processors.py: "
                                         f"a Docs page's head should hold {tag}")
        self.assertNotIn("article:published_time", html, "backend/templates/shell.html: a Docs page has no publication date")
        self.assertIn('data-layout="reading"', html, READING_BODY)

    def test_the_reading_registry_pages_are_websites_in_the_reading_layout(self):
        for path in ("/docs/", "/blog/", "/terms/", "/privacy/", "/report-abuse/"):
            with self.subTest(path=path):
                html = render_shell_as(path)
                self.assertIn('<meta property="og:type" content="website" data-page-meta>', html,
                              "backend/templates/shell.html: an index or legal page is og:type website; only an article says article")
                self.assertNotIn("article:published_time", html,
                                 "backend/templates/shell.html: only a Journal post has a publication date")
                self.assertIn('data-layout="reading"', html, READING_BODY)
                self.assertEqual([json.loads(block)["@type"] for block in LD_JSON.findall(html)], ["Organization"],
                                 "backend/pages/templatetags/shell.py: index and legal pages carry Organization only")

    def test_a_post_with_no_description_has_no_description_tags(self):
        self.use_posts({"bare.md": post_file("bare-post", "A bare post", "2020-01-01", description="")})
        html = render_shell_as("/blog/bare-post/")
        for needle in ('name="description"', 'property="og:description"'):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, html, "backend/templates/shell.html: with no description there is no description tag, "
                                               "as blog/post.html left it out")
        self.assertNotIn("description", json.loads(LD_JSON.findall(html)[1]))

    def test_a_post_says_it_is_a_blog_posting(self):
        post = blog_loader.load_posts()[0]
        path = f"/blog/{post.slug}/"
        items = [json.loads(block) for block in LD_JSON.findall(render_shell_as(path))]
        self.assertEqual([item["@type"] for item in items], ["Organization", "BlogPosting"])
        article = items[1]
        self.assertEqual(article["@context"], "https://schema.org")
        self.assertEqual(article["headline"], post.title)
        self.assertEqual(article["description"], post.description)
        self.assertEqual(article["datePublished"], post.date.isoformat())
        self.assertEqual(article["mainEntityOfPage"], {"@type": "WebPage", "@id": f"https://veilfile.com{path}"})
        self.assertEqual(article["image"], "https://veilfile.com/static/" + (post.image or "img/og.png"))
        self.assertEqual(article["author"]["name"], "Veilfile")
        self.assertEqual(article["publisher"]["name"], "Veilfile")

    def test_a_docs_page_says_it_is_a_tech_article(self):
        page = next(page for page in docs_loader.load_pages() if page.slug != "index")
        path = f"/docs/{page.slug}/"
        items = [json.loads(block) for block in LD_JSON.findall(render_shell_as(path))]
        self.assertEqual([item["@type"] for item in items], ["Organization", "TechArticle"])
        self.assertEqual(items[1]["headline"], page.title)
        self.assertEqual(items[1]["mainEntityOfPage"]["@id"], f"https://veilfile.com{path}")
        self.assertNotIn("datePublished", items[1], "backend/pages/templatetags/shell.py: a Docs page has no publication date")

    def test_every_json_ld_script_swaps_with_the_page(self):
        post = blog_loader.load_posts()[0]
        html = render_shell_as(f"/blog/{post.slug}/")
        self.assertEqual(html.count('type="application/ld+json"'), len(LD_JSON.findall(html)),
                         "backend/pages/templatetags/shell.py: every JSON-LD script carries data-page-meta, or it stays "
                         "behind when shell.js swaps the page")
        self.assertEqual(len(LD_JSON.findall(html)), 2,
                         "backend/pages/templatetags/shell.py: a post's head carries two JSON-LD scripts, Organization "
                         "and BlogPosting")

    def test_a_title_cannot_close_the_json_ld_script(self):
        hostile = "Close </script><b>this</b> & more"
        self.use_posts({"hostile.md": post_file("hostile-post", hostile, "2020-01-01")})
        html = render_shell_as("/blog/hostile-post/")
        self.assertNotIn("</script><b>", html, "backend/pages/templatetags/shell.py: JSON-LD must escape <, > and &")
        self.assertEqual(html.count("<script"), html.count("</script>"),
                         "backend/pages/templatetags/shell.py: a title must not open or close a script in the head")
        self.assertEqual(json.loads(LD_JSON.findall(html)[1])["headline"], hostile)
