"""Views for public pages on the new shell."""
from django.http import Http404
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from blog import loader as blog_loader
from docs import loader as docs_loader
from drop_core import funnel
from drop_core.plans import PLANS
from pages import catalog, registry
from pages.install import INSTALL_TABS
from pages.plan_cards import plan_cards
from pages.snippets import BEGIN_SNIPPETS


def sitemap(request):
    """sitemap.xml: every registry page, every Docs page and every published Journal post.

    Leaves out /app/*, /a/<token>, /llms.txt and the API, as robots.txt does. Only posts carry a
    lastmod, because only they have a real date; a guessed or hard-coded one teaches search
    engines to ignore the field for the whole site.
    """
    entries = [
        {"loc": registry.canonical_url(page.url_name), "lastmod": None}
        for page in registry.PAGES
        if page.in_sitemap
    ]
    entries += [
        {"loc": registry.SITE_ORIGIN + reverse("docs-page", args=[doc.slug]), "lastmod": None}
        for doc in docs_loader.load_pages()
        if doc.slug != "index"
    ]
    entries += [
        {"loc": registry.SITE_ORIGIN + reverse("blog-post", args=[post.slug]), "lastmod": post.date.isoformat()}
        for post in blog_loader.load_posts()
    ]
    return render(request, "sitemap.xml", {"entries": entries}, content_type="application/xml")


FRAMES = (
    ("light", "on", "Paper, motion on"),
    ("dark", "on", "Ink, motion on"),
    ("light", "off", "Paper, motion off"),
    ("dark", "off", "Ink, motion off"),
)


def _numbered(context, index):
    """A sample's context for frame ``index``: the keys that hold an id get the frame's number, so ids stay unique across the four frames."""
    context = dict(context)
    for key in ("list_id", "id_prefix", "id"):
        if key in context:
            context[key] = f"{context[key]}-{index}"
    return context


def component_catalog(request):
    """Every component in Paper and Ink, Motion on and off. Staff only; everyone else gets a 404."""
    if not (request.user.is_authenticated and request.user.is_staff):
        raise Http404("No such page.")
    components = []
    for entry in catalog.entries():
        frames = []
        for index, (theme, motion, caption) in enumerate(FRAMES):
            # The entry's context, then each of its states, one under the other (their ids differ), each numbered for the frame.
            html = mark_safe("".join(render_to_string(f"components/{entry.name}.html", _numbered(sample, index))
                                     for sample in (entry.context, *entry.states)))
            if entry.within:  # a component that only sits inside a layout's column is shown inside it
                html = format_html('<div class="{}">{}</div>', entry.within, html)
            frames.append({"theme": theme, "motion": motion, "caption": caption, "html": html})
        components.append({"entry": entry, "frames": frames})
    response = render(request, "pages/catalog.html", {"components": components})
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


# What a chapter's template is given besides the request: Begin's code snippets (pages/snippets.py), which a template cannot hold.
CHAPTER_CONTEXT = {"begin": {"snippets": BEGIN_SNIPPETS}}


def chapter(request, name):
    """A chapter (spec 4.3): templates/chapters/<name>.html; its <head> comes from the registry."""
    return render(request, f"chapters/{name}.html", CHAPTER_CONTEXT.get(name))


def gallery(request):
    """The social cards gallery (unlisted): the nine social cards on the shell, with no menu or sitemap entry."""
    return render(request, "pages/gallery.html")


def plans(request):
    """Plans (spec 4.3): the four plans from drop_core/plans.py, each a link into sign-up with it chosen."""
    cards = plan_cards()
    product = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Veilfile",
        "description": registry.page("plans").description,
        "url": registry.canonical_url("plans"),
        "offers": [
            {"@type": "Offer", "name": card["name"], "description": ", ".join(card["limits"]),
             "price": str(PLANS[card["slug"]]["price_usd"]), "priceCurrency": "USD",
             "url": registry.SITE_ORIGIN + card["signup_url"]}
            for card in cards
        ],
    }
    return render(request, "chapters/plans.html", {"plans": cards, "structured_data": [product]})


@funnel.sets_funnel_cookie
def landing(request):
    """The threshold (spec 4.2). Records one funnel event per browser session, as the old landing page
    did (cookie-deduped; no database session; bots and HEAD not counted)."""
    funnel.record(request, funnel.LANDING_VISIT, once_per_session=True)
    return render(request, "chapters/threshold.html", {"install_tabs": INSTALL_TABS})
