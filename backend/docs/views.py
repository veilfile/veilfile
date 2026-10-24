"""Docs views: index and page.

`/docs/` renders the `index` page and lists the others; every other page lives at `/docs/<slug>/`, opens with
the list of its sections and ends with links to the page before and the page after it. Unknown slugs 404.
"""

from django.http import Http404
from django.shortcuts import render

from blog.render import render_reading
from docs.loader import get_page, load_pages, neighbours


def docs_index(request):
    page = get_page("index")
    if page is None:
        raise Http404("No such page.")
    return render(
        request,
        "docs/index.html",
        {
            "page": page,
            "body_html": render_reading(page.body_md).html,
            "entries": [other for other in load_pages() if other.slug != "index"],
        },
    )


def docs_page(request, slug):
    if slug == "index":
        return docs_index(request)
    page = get_page(slug)
    if page is None:
        raise Http404("No such page.")
    reading = render_reading(page.body_md)
    previous_page, next_page = neighbours(slug)
    return render(
        request,
        "docs/page.html",
        {
            "page": page,
            "body_html": reading.html,
            "sections": reading.sections,
            "previous_page": previous_page,
            "next_page": next_page,
        },
    )
