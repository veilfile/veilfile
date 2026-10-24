"""File-backed docs page loading.

Pages are markdown files in backend/docs/pages/ with frontmatter:

    ---
    title: Quickstart
    slug: quickstart
    description: One sentence for SEO.
    order: 2
    ---

    Body...

Pages are listed in `order`. Every page is public and permanent; there is
no dating, no hiding, and no feed. A page with unreadable or invalid
frontmatter (missing key, bad order, duplicate slug) is skipped with a
logged warning.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from django.urls import reverse

logger = logging.getLogger(__name__)

PAGES_DIR = Path(__file__).resolve().parent / "pages"

# Same pattern as the <slug:slug> URL converter: anything else would make
# reverse()/{% url %} raise on every page that lists it.
_SLUG_RE = re.compile(r"^[-a-zA-Z0-9_]+$")


@dataclass
class Page:
    slug: str
    title: str
    description: str
    order: int
    body_md: str
    seo_title: str = ""  # head <title>; falls back to `title` when unset

    @property
    def url(self):
        """The page's address: /docs/ for the index, else /docs/<slug>/. The index list and the pager link to it."""
        return reverse("docs-index") if self.slug == "index" else reverse("docs-page", args=[self.slug])


def _parse_frontmatter(text):
    """Split simple `key: value` frontmatter from the body. No YAML dep."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, text[end + 4 :].lstrip("\n")


def load_pages():
    """All docs pages, in `order`."""
    pages = []
    if not PAGES_DIR.is_dir():
        return pages
    seen_slugs = set()
    for path in sorted(PAGES_DIR.glob("*.md")):
        try:
            meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            slug = meta["slug"]
            if not _SLUG_RE.match(slug):
                raise ValueError(f"invalid slug {slug!r}")
            title = meta["title"]
            description = meta.get("description", "")
            order = int(meta.get("order", 99))
        except (OSError, UnicodeDecodeError, KeyError, ValueError) as exc:
            logger.warning(
                "docs: skipping %s: bad frontmatter (%s: %s)",
                path.name, type(exc).__name__, exc,
            )
            continue
        if slug in seen_slugs:
            logger.warning("docs: skipping %s: duplicate slug %r", path.name, slug)
            continue
        seen_slugs.add(slug)
        pages.append(
            Page(
                slug=slug,
                title=title,
                description=description,
                order=order,
                body_md=body,
                seo_title=meta.get("seo_title", ""),
            )
        )
    return sorted(pages, key=lambda p: (p.order, p.slug))


def get_page(slug):
    for page in load_pages():
        if page.slug == slug:
            return page
    return None


def neighbours(slug):
    """(previous, next): the pages before and after `slug` in `order`. Either is None at an end of the list, and both
    are None for an unknown slug."""
    pages = load_pages()
    for index, page in enumerate(pages):
        if page.slug == slug:
            return (
                pages[index - 1] if index else None,
                pages[index + 1] if index + 1 < len(pages) else None,
            )
    return (None, None)
