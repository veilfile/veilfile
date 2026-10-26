"""The error pages (spec 4.9): 404, 403, a failed CSRF check and 500, and the one page for a file link that leads nowhere.

404, 403 and the CSRF failure are drawn on the shell (templates/404.html, 403.html, 403_csrf.html). 500 is not: it is
templates/500.html, which needs no request, no context processor and no static file, so it still draws when something
deeper is broken.

Rules these views keep (tests: pages/tests_errors.py, pages/tests_artifact_miss.py):

- Nothing about the failure reaches the page: not the exception, not the path, not the CSRF reason. Two requests that
  fail for different reasons get the same words.
- A page that cannot be drawn is never a worse error. A 404 or 403 whose shell template raises (a missing static file,
  a database that is down) is answered with the bare page below and its own status, not with a 500.
- /a/<token> answers a miss from one function, artifact_miss(), for every kind of miss.
"""
import logging

from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

# The words of the bare page when the shell cannot be drawn: (title, message), by the template that failed. 500 has none:
# templates/500.html says its own words when it is given nothing.
BARE_WORDS = {
    "404.html": ("Not found", "There is nothing at this address. Check it for a typo, or start from the home page."),
    "403.html": ("Not allowed", "You do not have access to this page. If you expected to, sign in with the account that does."),
    "403_csrf.html": ("Form not sent", "Go back, reload that page and send the form again."),
}
# The same words as templates/404.html says for a file link that leads nowhere (spec 4.9). A test keeps these and the
# templates' words together.
GONE_WORDS = ("Nothing here", "This link does not lead to a file. If someone shared it with you, ask them for a new link.")


def log_failure(exc, message, *args):
    """Log `exc` at ERROR with the traceback of its own frames and nothing it was raised while handling.

    Whatever was being handled is the exception's __context__: a Resolver404 quotes the path, a FileNotFoundError the
    stored file's name, and for /a/<token> both are the token. This logger does not redact paths (django.request does),
    so the chain is cut before it is written.
    """
    exc.__context__ = None
    exc.__cause__ = None
    logger.error(message, *args, exc_info=exc)


def bare_page(status, words=None):
    """templates/500.html with this status. It is the 500 itself, and what a shell page falls back on when it cannot be
    drawn. It is drawn with no request, no context processor and no static file, so whatever was broken does not matter.
    `words` is (title, message), or None for the 500's own. If even this cannot be drawn, one line of plain text."""
    context = {"title": words[0], "message": words[1]} if words else {}
    try:
        return HttpResponse(render_to_string("500.html", context), status=status)
    except Exception as exc:
        log_failure(exc, "500.html could not be drawn; sent plain text")
        return HttpResponse("Something went wrong.", status=status, content_type="text/plain; charset=utf-8")


def shell_error_page(request, template, status):
    """`template` drawn on the shell for this request, with this status.

    shell_page is emptied: an error page has no registry entry, even when the view that failed belongs to one (a head
    with that page's canonical address and layout would claim the error is the page). plain_menu asks for the ordinary
    menu, not the dashboard's with its Sign out button and token, even for a failure at a dashboard address (shell_menu).
    If the shell cannot be drawn, the bare page answers with the same status, and the failure is logged (log_failure)
    without the path.
    """
    try:
        response = render(request, template, {"shell_page": None, "plain_menu": True}, status=status)
    except Exception as exc:
        log_failure(exc, "%s could not be drawn on the shell; sent the bare page instead", template)
        response = bare_page(status, BARE_WORDS[template])
    if request.path.startswith("/app/"):
        # Every answer under /app/ is kept out of search results, the shell's 404 for an address that is not the dashboard's included
        # (dashboard.views.dashboard_view does the same for the dashboard's own answers).
        response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def not_found(request, exception=None):
    """handler404. `exception` is never read: it names which kind of 404 this is."""
    return shell_error_page(request, "404.html", 404)


def permission_denied(request, exception=None):
    """handler403. `exception` is never read."""
    return shell_error_page(request, "403.html", 403)


def csrf_failure(request, reason=""):
    """CSRF_FAILURE_VIEW. `reason` is never read: it can quote the Origin header, and Django logs it already."""
    return shell_error_page(request, "403_csrf.html", 403)


def server_error(request=None):
    """handler500. `request` is never read: the page is drawn without it."""
    return bare_page(500)


def artifact_miss():
    """The answer for /a/<token> when the link leads nowhere: the token is unknown, expired, revoked, held, or its stored
    file is gone (spec 4.9). One function, so no case can differ from another.

    The page is drawn with no request, so it never reads the session: it is the same for everyone who asks, it adds
    no Vary header and it costs no query beyond the lookup of the token. It carries the headers of a file that is
    found, because a bearer link's page is as private as its bytes: nothing caches it and nothing indexes it.
    """
    try:
        response = HttpResponse(render_to_string("404.html", {"gone_link": True}), status=404)
    except Exception as exc:
        log_failure(exc, "404.html could not be drawn for a file link; sent the bare page instead")
        response = bare_page(404, GONE_WORDS)
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
