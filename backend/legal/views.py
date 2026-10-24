"""Legal views: terms of service, privacy policy, and abuse reporting pages.

The documents are the owner's (AGENTS.md invariant 8): this module renders them and never edits them.
A page's title, description and address come from its Page in pages/registry.py, not from here.
"""

from pathlib import Path

from django.http import Http404
from django.shortcuts import render

from blog.render import render_reading

DOCUMENTS_DIR = Path(__file__).resolve().parent / "documents"

DOCUMENTS = {
    "terms": ("terms-of-service.md", "Terms of Service"),
    "privacy": ("privacy-policy.md", "Privacy Policy"),
    "report-abuse": ("report-abuse.md", "Report abuse"),
}


def split_before_sections(html):
    """The page's opening and the rest, so the section list can sit between them.

    Every document opens with its # heading, which renders as the page's one h1, and Terms and
    Privacy follow it with a "Last updated" line. The rest starts at the first h2. A document with
    no h2 comes back whole as the opening, and the section list, which shows nothing for fewer
    than two sections, then draws nothing.
    """
    intro, found, rest = html.partition("<h2")
    return intro, found + rest


def legal_document(request, name):
    try:
        filename, _title = DOCUMENTS[name]
    except KeyError:
        raise Http404("No such document.")
    reading = render_reading((DOCUMENTS_DIR / filename).read_text(encoding="utf-8"))
    intro_html, body_html = split_before_sections(reading.html)
    return render(
        request,
        "legal/document.html",
        {"intro_html": intro_html, "sections": reading.sections, "body_html": body_html},
    )


def terms(request):
    return legal_document(request, "terms")


def privacy(request):
    return legal_document(request, "privacy")


def report_abuse(request):
    return legal_document(request, "report-abuse")
