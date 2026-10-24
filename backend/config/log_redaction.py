"""Keep bearer secrets out of the logs.

Several URLs are capabilities: whoever has the URL has the access. That
covers artifact links (/a/<token>, the only access control on a private
file), one-time upload URLs, email-verification links and password-reset
links, and OAuth/device parameters travel in query strings. Request logging
(gunicorn's access log, Django's "Internal Server Error: <path>" lines) must
never write them out, or anyone who can read the service logs could open any
live artifact.

Pure functions only: the gunicorn master imports this module (through
config.gunicorn_logging) without loading Django.
"""

import logging
import re
from urllib.parse import urlsplit

REDACTED = "<redacted>"

# Path prefixes whose remaining segments are bearer secrets.
SECRET_PATH_PREFIXES = (
    "/a/",  # artifact retrieval link
    "/api/v1/uploads/",  # one-time upload URL (MCP create_upload_url)
    "/accounts/verify-email/",  # email verification token
    "/accounts/reset/",  # password reset <uidb64>/<token>
)

# Defense in depth for any other route: a long URL-safe segment containing an
# uppercase letter looks like secrets.token_urlsafe() output. Slugs and UUIDs
# are lowercase, and hashed static asset names contain a dot.
_TOKEN_LIKE = re.compile(r"(?=[A-Za-z0-9_-]*[A-Z])[A-Za-z0-9_-]{20,}")


def redact_path(path):
    """Return ``path`` (optionally with a query string) with every secret
    segment replaced by <redacted> and any query string replaced by
    ?<redacted>."""
    if not path:
        return path
    path, has_query, _query = path.partition("?")
    # //a/<token> still carries a real token.
    path = re.sub(r"/{2,}", "/", path)
    for prefix in SECRET_PATH_PREFIXES:
        if path.startswith(prefix) and len(path) > len(prefix):
            rest = path[len(prefix):].split("/")
            path = prefix + "/".join(REDACTED if segment else "" for segment in rest)
            break
    else:
        path = "/".join(
            REDACTED if _TOKEN_LIKE.fullmatch(segment) else segment
            for segment in path.split("/")
        )
    return path + ("?" + REDACTED if has_query else "")


def redact_uri(uri):
    """redact_path for a request target, which may also be absolute-form
    (http://host/path)."""
    if not uri or uri.startswith("/"):
        return redact_path(uri)
    parts = urlsplit(uri)
    if parts.scheme and parts.hostname:
        origin = f"{parts.scheme}://{parts.hostname}"
        if parts.port:
            origin += f":{parts.port}"
        target = parts.path + ("?" + parts.query if parts.query else "")
        return origin + redact_path(target or "/")
    return REDACTED


def redact_referer(referer):
    """Reduce a Referer to its origin (scheme://host[:port]). The path and
    query of the referring page can be a secret link too, ours or another
    site's."""
    if not referer or referer == "-":
        return referer
    parts = urlsplit(referer)
    if parts.scheme and parts.hostname:
        origin = f"{parts.scheme}://{parts.hostname}"
        if parts.port:
            origin += f":{parts.port}"
        return origin
    return REDACTED


class RedactPathsFilter(logging.Filter):
    """Logging filter for django.request, whose 4xx/5xx records carry the
    request path as an argument ("Internal Server Error: /a/<token>")."""

    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_path(arg) if isinstance(arg, str) and arg.startswith("/") else arg
                for arg in record.args
            )
        return True
