"""Blog URL routes."""

from django.urls import path

from blog import feeds, views
from blog.views import RETIRED_SLUGS

urlpatterns = [
    path("", views.blog_index, name="blog-index"),
    path("feed.xml", feeds.BlogFeed(), name="blog-feed"),
]
# Retired slugs redirect permanently to their replacements (301, no 404s).
for _old_slug in RETIRED_SLUGS:
    urlpatterns.append(
        path(f"{_old_slug}/", views.retired_post_redirect, {"slug": _old_slug})
    )
urlpatterns.append(path("<slug:slug>/", views.blog_post, name="blog-post"))
