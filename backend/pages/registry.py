"""Every public page, and the menu, in one place (spec 6.5).

The menu, sitemap.xml and the tests read from here; from PR 2 each page's <head> does too.
Journal posts and Docs pages come from files, not from this list: pages/context_processors.py builds
their heads from the loaders, and layout_paths() adds their addresses to the reading layout.
To add a public page: add a Page below with a title of 60 characters or fewer and a
description of 70 to 160 characters, then run the tests. Every page here renders its <head>
from its entry (shell.html), and a test compares what it renders with this list.
"""
from dataclasses import dataclass

from django.urls import reverse

from blog import loader as blog_loader
from docs import loader as docs_loader

SITE_ORIGIN = "https://veilfile.com"


@dataclass(frozen=True)
class Page:
    url_name: str
    title: str
    description: str
    in_sitemap: bool = True
    # "chapter" for the threshold and the chapters, "reading" for the Journal, Docs and legal pages. In-place
    # changes stay within one layout: layout_paths() lists the addresses of each. ("app" is the dashboard's: its
    # five sections are DASHBOARD_MENU, not pages of this list.)
    layout: str = ""


@dataclass(frozen=True)
class MenuLink:
    label: str
    url_name: str
    audience: str = "everyone"  # "everyone", "signed-in" or "signed-out"


PAGES = (
    Page("landing", "Veilfile | Private ephemeral artifact hosting for AI agents",
         "Veilfile hosts private files for AI agents. Upload files via REST or MCP. "
         "Links are unguessable and expire on schedule. Plans cost $0 to $12 a month.",
         layout="chapter"),
    Page("about", "About | Veilfile",
         "Each file your agents make gets a private link that stops when its time is up. "
         "Nothing sits in a public repository, a bucket, or a paste that lives forever.",
         layout="chapter"),
    Page("how", "How it works | Veilfile",
         "An agent uploads a file and gets an unguessable link to share. "
         "When its days run out, the link stops working and the file is deleted soon after.",
         layout="chapter"),
    Page("begin", "Begin | Veilfile",
         "Connect your agent to the MCP server at veilfile.com/mcp, install the command-line tool "
         "with npm or pip, or upload over plain HTTP.",
         layout="chapter"),
    Page("plans", "Plans | Veilfile",
         "Veilfile has four plans: Free ($0), Team ($4/month), Team Plus ($6/month), and Scale ($12/month). Prices include tax. "
         "The price you see is the price you pay.",
         layout="chapter"),
    Page("promises", "What we promise | Veilfile",
         "Veilfile promises that the link is the only way in, that expired files are deleted from storage, "
         "and that file contents are never logged.",
         layout="chapter"),
    Page("docs-index", "Veilfile docs | REST API, MCP, and agent setup",
         "Start with the quickstart, then set up an agent key, learn how uploads and expiry work, "
         "and look up the upload, list, revoke, and download endpoints.",
         layout="reading"),
    Page("blog-index", "Veilfile blog | launches and build notes",
         "Read notes on how Veilfile is built, including launches, changes, pricing, and the reasoning behind them. "
         "An RSS feed is available.",
         layout="reading"),
    Page("terms", "Terms of service | Veilfile",
         "Veilfile's terms cover acceptable use, accounts and API keys, data retention, billing and refunds, "
         "liability limits, and how to contact us.",
         layout="reading"),
    Page("privacy", "Privacy policy | Veilfile",
         "Read what Veilfile collects and why, how long it keeps files and metadata, which subprocessors it uses, "
         "and how to request access or deletion.",
         layout="reading"),
    Page("report-abuse", "Report abuse | Veilfile",
         "Report a file that violates our terms by emailing abuse@veilfile.com with the link "
         "and details. We report apparent child sexual exploitation to NCMEC.",
         layout="reading"),
    Page("gallery", "Social cards | Veilfile",
         "Nine cards made for Veilfile's social posts, drawn in the site's paper style. "
         "This page is not linked from anywhere on the site.",
         in_sitemap=False, layout=""),
)

# The menu's three groups (spec 4.4). MENU[0] is the chapters, in the order the menu shows them.
MENU = (
    (MenuLink("About", "about"), MenuLink("How it works", "how"), MenuLink("Begin", "begin"),
     MenuLink("Promises", "promises"), MenuLink("Plans", "plans")),
    (MenuLink("Docs", "docs-index"), MenuLink("Journal", "blog-index"),
     MenuLink("Sign in", "login", audience="signed-out"), MenuLink("Dashboard", "app", audience="signed-in")),
    (MenuLink("Terms", "terms"), MenuLink("Privacy", "privacy"),
     MenuLink("Report abuse", "report-abuse"), MenuLink("llms.txt", "llms-txt")),
)


# Inside the dashboard (spec 4.4, 4.8) group 1 lists the five sections instead of the chapters, and group 2 is Docs, Journal and Site; the Sign out
# button that ends group 2 is the menu component's, since it needs the page's CSRF token. Group 3 is the same. The sections are pages of the "app"
# layout family (layout_paths): moving between them changes in place.
DASHBOARD_MENU = (
    MenuLink("Overview", "app"), MenuLink("Files", "app-files"), MenuLink("Keys", "app-keys"),
    MenuLink("Connected apps", "app-apps"), MenuLink("Billing", "app-billing"),
)
DASHBOARD_SITE_LINKS = (MenuLink("Docs", "docs-index"), MenuLink("Journal", "blog-index"), MenuLink("Site", "landing"))


def page(url_name):
    for candidate in PAGES:
        if candidate.url_name == url_name:
            return candidate
    raise KeyError(f"no public page named {url_name!r} in pages/registry.py")


def canonical_url(url_name):
    return SITE_ORIGIN + reverse(url_name)


def layout_paths(layout):
    """Every address a page in `layout` may change to in place (shell.js, spec 4.5), as a tuple of paths.

    The registry pages with that layout, in registry order. The reading layout adds every published
    Journal post (newest first) and every Docs page (in order): they come from files and have no registry
    entry. A post dated in the future is not published (blog.loader), so it is not here. No layout, or one
    no page has, is an empty family. The paths are joined with spaces into data-layout-paths, so none may
    hold whitespace; the loaders only accept slugs of [-a-zA-Z0-9_].
    """
    if layout == "app":
        return tuple(reverse(link.url_name) for link in DASHBOARD_MENU)
    paths = [reverse(candidate.url_name) for candidate in PAGES if layout and candidate.layout == layout]
    if paths and layout == "reading":
        paths += [reverse("blog-post", args=[post.slug]) for post in blog_loader.load_posts()]
        paths += [reverse("docs-page", args=[doc.slug]) for doc in docs_loader.load_pages()]
    return tuple(paths)


# Pages that sit inside a menu section: on them the section's link is marked "true" (in this
# section), not "page" (this very page).
# They come from files, not from PAGES: pages/context_processors.py gives each its own head entry
# (a test keeps the two lists together).
SECTIONS = {"docs-page": "docs-index", "blog-post": "blog-index"}

# The same for the dashboard (spec 4.8): every dashboard URL name, and the section of DASHBOARD_MENU it marks. A section marks itself "page"; a page
# inside one (a confirmation page) marks it "true". The menu inside the dashboard is drawn only for a name that is listed here (the shell_menu tag),
# so a new dashboard address is added here, with its section, when its route is.
DASHBOARD_SECTIONS = {
    "app": "app",
    "app-files": "app-files",
    "app-file-revoke": "app-files",
    "app-keys": "app-keys",
    "app-key-revoke": "app-keys",
    "app-apps": "app-apps",
    "app-app-disconnect": "app-apps",
    "app-billing": "app-billing",
    "app-billing-switch": "app-billing",
    "app-billing-checkout": "app-billing",
    "app-billing-portal": "app-billing",
}


def dashboard_section(url_name):
    """The DASHBOARD_MENU link for one of the five sections, else None: a confirmation page is in DASHBOARD_SECTIONS but is no section of the family."""
    return next((link for link in DASHBOARD_MENU if link.url_name == url_name), None)


def menu_groups(*, signed_in, current=None, inside_dashboard=False):
    """The menu as the menu component takes it: groups of {"label", "href", "current"}.

    current is "page" on the link to this page, "true" on the link to the section this page is
    in, and "" everywhere else; the component writes it as aria-current. inside_dashboard swaps group 1 for
    DASHBOARD_MENU and group 2 for Docs, Journal and Site, and takes the section from DASHBOARD_SECTIONS.
    """
    hidden = "signed-out" if signed_in else "signed-in"
    section = (DASHBOARD_SECTIONS if inside_dashboard else SECTIONS).get(current)
    menu = (DASHBOARD_MENU, DASHBOARD_SITE_LINKS, MENU[2]) if inside_dashboard else MENU

    def mark(url_name):
        return "page" if url_name == current else "true" if url_name == section else ""

    return [
        [
            {"label": link.label, "href": reverse(link.url_name), "current": mark(link.url_name)}
            for link in group
            if link.audience != hidden
        ]
        for group in menu
    ]
