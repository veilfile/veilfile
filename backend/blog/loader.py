"""File-backed blog post loading.

Posts are markdown files in backend/blog/posts/ with frontmatter:

    ---
    title: My post
    date: 2026-10-06
    slug: my-post
    description: One sentence for SEO and RSS.
    image: img/journal/x.png            (optional: a path under backend/static/)
    image_alt: One sentence about it.   (required with image)
    ---

    Body...

Posts dated in the future are hidden until their date. A post with
unreadable or invalid frontmatter (missing key, bad date, duplicate slug)
is skipped with a logged warning; it never takes down the blog, the feed
or the sitemap.
"""

import logging
import math
import re
import struct
from dataclasses import dataclass
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

POSTS_DIR = Path(__file__).resolve().parent / "posts"
# Where a post's `image` is looked up, for image_size().
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
# How fast a reader is taken to read, for reading_minutes().
WORDS_PER_MINUTE = 200

# Same pattern as the <slug:slug> URL converter: anything else would make
# reverse()/{% url %} raise on every page that lists it.
_SLUG_RE = re.compile(r"^[-a-zA-Z0-9_]+$")


@dataclass
class Post:
    slug: str
    title: str
    date: date
    description: str
    body_md: str
    image: str = ""  # front matter `image`: a path under backend/static/, shown at the top and used in link previews
    image_alt: str = ""  # front matter `image_alt`: what the image shows


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


def load_posts():
    """All published posts, newest first."""
    today = date.today()
    posts = []
    if not POSTS_DIR.is_dir():
        return posts
    seen_slugs = set()
    for path in sorted(POSTS_DIR.glob("*.md")):
        try:
            meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            slug = meta["slug"]
            if not _SLUG_RE.match(slug):
                raise ValueError(f"invalid slug {slug!r}")
            title = meta["title"]
            post_date = date.fromisoformat(meta["date"])
            description = meta.get("description", "")
        except (OSError, UnicodeDecodeError, KeyError, ValueError) as exc:
            logger.warning(
                "blog: skipping %s: bad frontmatter (%s: %s)",
                path.name, type(exc).__name__, exc,
            )
            continue
        if slug in seen_slugs:
            logger.warning("blog: skipping %s: duplicate slug %r", path.name, slug)
            continue
        seen_slugs.add(slug)
        if post_date > today:
            continue
        posts.append(
            Post(
                slug=slug,
                title=title,
                date=post_date,
                description=description,
                body_md=body,
                image=meta.get("image", ""),
                image_alt=meta.get("image_alt", ""),
            )
        )
    return sorted(posts, key=lambda p: p.date, reverse=True)


def get_post(slug):
    for post in load_posts():
        if post.slug == slug:
            return post
    return None


def neighbours(slug):
    """(older, newer): the published posts either side of the post with this slug.

    Posts are taken in load_posts() order, newest first, so a post dated in the future is neither
    found nor anyone's neighbour. Either side is None at the end of the list, and both are None
    for a slug that is not published.
    """
    posts = load_posts()
    for index, post in enumerate(posts):
        if post.slug == slug:
            older = posts[index + 1] if index + 1 < len(posts) else None
            newer = posts[index - 1] if index > 0 else None
            return older, newer
    return None, None


def reading_minutes(text):
    """Whole minutes to read a post's Markdown at 200 words a minute, rounded up, never fewer than 1.

    A word is a run of characters between spaces that holds a letter or a digit, so the marks
    Markdown adds (#, -, >, |, ---, a fence) are not words. Code blocks count: a reader reads
    them too, and leaving them out would promise a shorter read than the page takes. The words
    are the Markdown's own, so the Copy button the page puts on a code block adds none.
    """
    words = sum(1 for token in text.split() if any(char.isalnum() for char in token))
    return max(1, math.ceil(words / WORDS_PER_MINUTE))


def image_size(name):
    """(width, height) of the PNG at backend/static/<name>, read from its header, else None.

    The post page puts these on the <img> so the browser keeps the space before the file arrives.
    None for a file that is missing, is not a PNG, or lies outside backend/static/ (the page then
    shows the image without a size).
    """
    try:
        path = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in path.parents:
            return None
        with path.open("rb") as handle:
            header = handle.read(24)
    except (OSError, ValueError):
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", header[16:24])
    return (width, height) if width and height else None
