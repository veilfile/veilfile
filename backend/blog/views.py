"""Journal views: the index, one post, and the retired addresses."""

from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from blog.loader import get_post, image_size, load_posts, neighbours, reading_minutes
from blog.render import render_reading

# The first post was replaced on 2026-10-05 (founder decision: the old
# name must not appear anywhere user-facing). Keep the old URL working.
RETIRED_SLUGS = {"renaming-deadrop-to-veilfile": "introducing-veilfile"}


def retired_post_redirect(request, slug):
    return redirect("blog-post", slug=RETIRED_SLUGS[slug], permanent=True)


def _entry(post):
    """One post as components/entries.html and components/pager.html take it."""
    return {
        "url": reverse("blog-post", args=[post.slug]),
        "title": post.title,
        "description": post.description,
        "date": post.date,
    }


def blog_index(request):
    return render(request, "blog/index.html", {"entries": [_entry(post) for post in load_posts()]})


def blog_post(request, slug):
    post = get_post(slug)
    if post is None:
        raise Http404("No such post.")
    older, newer = neighbours(slug)
    return render(
        request,
        "blog/post.html",
        {
            "post": post,
            "reading": render_reading(post.body_md),
            "minutes": reading_minutes(post.body_md),
            # (width, height) of a PNG header image, so the page keeps its space before the file
            # arrives; None for any other format (the image is shown without a size).
            "image_size": image_size(post.image) if post.image else None,
            # The pager's "Previous" is the older post and "Next" the newer one.
            "previous": _entry(older) if older else None,
            "next": _entry(newer) if newer else None,
        },
    )
