"""The registry entry for the page being rendered, for the <head> in shell.html (spec 6.5, 6.6)."""
from django.templatetags.static import static
from django.urls import reverse
from django.utils.functional import SimpleLazyObject

from blog import loader as blog_loader
from docs import loader as docs_loader
from pages import registry


def _build(*, title, description, canonical, layout, image, article=None):
    """The entry the head reads, every address absolute; `image` is a static path.

    `article` is None for a page that is not an article, else what search results are told about it:
    {"type": "BlogPosting" or "TechArticle", "headline": ..., "published": a date, or None}.
    """
    return {
        "title": title,
        "description": description,
        "canonical": canonical,
        "layout": layout,
        # The only addresses shell.js changes to in place: every page in the same layout.
        "layout_paths": " ".join(registry.layout_paths(layout)),
        "image": registry.SITE_ORIGIN + static(image),
        "article": article,
    }


def _entry(page):
    """A registry page's entry: its title, description, canonical address, layout and link-preview image."""
    return _build(title=page.title, description=page.description, canonical=registry.canonical_url(page.url_name),
                  layout=page.layout, image="img/og.png")


def _post_entry(post):
    """A Journal post's entry. Its title and description are the ones blog/post.html rendered, so that search
    results do not change: "<title> | Veilfile blog" and the post's own description. Its link preview is the
    header image its front matter names, else the site's."""
    return _build(
        title=f"{post.title} | Veilfile blog",
        description=post.description,
        canonical=registry.SITE_ORIGIN + reverse("blog-post", args=[post.slug]),
        layout="reading",
        image=post.image or "img/og.png",
        article={"type": "BlogPosting", "headline": post.title, "published": post.date},
    )


def _docs_entry(page):
    """A Docs page's entry. Its title is the one docs/page.html rendered (the page's seo_title, else its title),
    so that search results do not change."""
    if page.slug == "index":  # /docs/index/ shows the index page, whose address is /docs/
        return _entry(registry.page("docs-index"))
    return _build(
        title=page.seo_title or page.title,
        description=page.description,
        canonical=registry.SITE_ORIGIN + reverse("docs-page", args=[page.slug]),
        layout="reading",
        image="img/og.png",
        article={"type": "TechArticle", "headline": page.title, "published": None},
    )


def _app_entry(link):
    """A dashboard section's entry (spec 4.8): its title and the layout "app", whose family is the five sections. It has no canonical address,
    description or preview image, which is what keeps shell.html from printing the public head (canonical, Open Graph, JSON-LD, feed links)
    for a page that is signed in and kept out of search results; nor does it ask for a static file."""
    return {
        "title": f"{link.label} | Veilfile",
        "description": "",
        "canonical": "",
        "layout": "app",
        "layout_paths": " ".join(registry.layout_paths("app")),
        "image": "",
        "article": None,
    }


def shell_page(request):
    """{"shell_page": None} for a page with no entry, else its entry (see _build).

    A registry page has an entry; so does a Journal post or a Docs page whose slug is published (the URL names
    blog-post and docs-page, the same two registry.SECTIONS names). An unknown slug, or a post dated in the
    future, has none: the view answers 404. So does each of the dashboard's five sections (_app_entry): the
    family they change in place within. A page inside a section, such as a confirmation page, has none: it loads as an ordinary page.

    The entry is built when a template first reads it, not on every render: a page whose template never reads
    shell_page must not fail because img/og.png has no manifest entry after a bad collectstatic. Only a slug is
    looked up at once, to know whether there is an entry at all.
    """
    match = getattr(request, "resolver_match", None)
    name = getattr(match, "url_name", None)
    slug = (getattr(match, "kwargs", None) or {}).get("slug")
    if name == "blog-post":
        page, build = blog_loader.get_post(slug), _post_entry
    elif name == "docs-page":
        page, build = docs_loader.get_page(slug), _docs_entry
    elif registry.dashboard_section(name):
        page, build = registry.dashboard_section(name), _app_entry
    else:
        build = _entry
        try:
            page = registry.page(name) if name else None
        except KeyError:
            page = None
    if page is None:
        return {"shell_page": None}
    return {"shell_page": SimpleLazyObject(lambda: build(page))}
