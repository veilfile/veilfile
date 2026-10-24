"""The component catalog's entries: every component in templates/components/, with sample inputs.

A test fails when a component has no entry here. Add one when you add a component.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from django.utils.safestring import mark_safe

from blog.render import Section
from pages import registry
from pages.install import INSTALL_TABS
from pages.plan_cards import plan_cards
from pages.snippets import BEGIN_SNIPPETS


@dataclass(frozen=True)
class Entry:
    name: str  # the file name in templates/components/, without .html
    note: str  # one line: what it is and where it appears
    context: dict = field(default_factory=dict)
    within: str = ""  # the class of the wrapper the page puts it in, for a component that only sits in one ("reading", "form-page")
    states: tuple = ()  # more samples of the same component, each a context of its own (a field blank, with help, with an error): the frame draws them after `context`'s, one under the other, and their ids must differ


def _file_samples():
    """Five files for the file_list entry: one that works, one with secret flags, an expired one, a held one and one with a
    512-character name that has no place to break."""
    added = datetime(2026, 10, 22, 13, 58, tzinfo=timezone.utc)

    def sample(number, name, *, state="live", size=3400, flags=(), days=7):
        return {"id": f"00000000-0000-4000-8000-{number:012d}", "name": name, "size": size, "added": added,
                "expires": added + timedelta(days=days), "flags": list(flags), "state": state,
                "url": f"https://veilfile.com/a/catalog-sample-{number}" if state == "live" else ""}

    return (
        sample(1, "release-notes.md"),
        sample(2, "deploy.env", size=912, flags=("Stripe key", "Password or secret")),
        sample(3, "screenshots.zip", state="expired", size=48_300_000, days=-1),
        sample(4, "export.csv", state="held", size=2_150_000_000),
        sample(5, "x" * 508 + ".txt"),
    )


def entries():
    return (
        Entry("mark", "The Let go mark on its own, in the current text color."),
        Entry("lockup", "Top left on every page: the mark alone, linking to the threshold.", {"home_url": "/"}),
        Entry("menu", "Top right on every page: MENU and its three groups, here signed out with About current. Inside the dashboard group 1 lists its five sections, "
                      "group 2 is Docs, Journal and Site and ends with a Sign out button (a form that posts the page's token), here with Files current.",
              {"groups": registry.menu_groups(signed_in=False, current="about"), "list_id": "catalog-menu"},
              states=({"groups": registry.menu_groups(signed_in=True, current="app-files", inside_dashboard=True), "list_id": "catalog-menu-dashboard",
                       "sign_out": "catalog-not-a-token"},)),
        Entry("controls", "The bottom corners on every page: the appearance switch bottom left and the motion switch (FX) bottom right, "
                          "each alone there (a corner passes show); shown here together, with no show."),
        Entry("plan", "One plan on the Plans chapter, here Team: name, price, limits and a link into sign-up.",
              {"plan": next(card for card in plan_cards() if card["slug"] == "team")}),
        Entry("install_line", "The threshold after Come in: MCP, npm and pip, Copy, and the line after Copy.",
              {"tabs": INSTALL_TABS, "id_prefix": "catalog-install"}),
        Entry("code_block", "A code snippet on Begin, in the Docs and in the Journal: a scroll box with Copy in a row of its own along its top, at the right, so the code keeps the whole width.",
              {"id": "catalog-code", "code": BEGIN_SNIPPETS["claude_code"], "label": "Copy the Claude Code command"}),
        Entry("file_list", "The dashboard's Files section: a stacked row for each file with its size, times, secret flags in words and state, "
                           "Copy and Revoke while its link works, and the way to older or newer files. Shown with five files (one that works, one with "
                           "flags, an expired one, a held one and one with a 512-character name), with no files at all, and with no older ones.",
              {"files": _file_samples(), "id_prefix": "catalog-files", "older_url": "/app/files/?cursor=1-2", "newest_url": "/app/files/"},
              within="app",
              states=({"files": [], "id_prefix": "catalog-files-empty", "older_url": "", "newest_url": ""},
                      {"files": [], "id_prefix": "catalog-files-none-older", "older_url": "", "newest_url": "/app/files/"})),
        Entry("sections", "The top of a Docs page or a legal document: links to its sections, left out when there are fewer than two.",
              {"sections": (Section(id="auth", title="Auth"), Section(id="upload", title="Upload"), Section(id="list", title="List"),
                            Section(id="revoke", title="Revoke"), Section(id="download", title="Download"),
                            Section(id="errors", title="Errors"))},
              within="reading"),
        Entry("pager", "The end of a Journal post or a Docs page: the page before and the page after, here the neighbours of How it works.",
              {"previous": SimpleNamespace(url="/docs/agent/", title="Agent setup"),
               "next": SimpleNamespace(url="/docs/api/", title="API reference")},
              within="reading"),
        Entry("entries", "The Journal and the Docs index: each page's title, date and description; a Docs page has no date.",
              {"entries": (
                  SimpleNamespace(url="/blog/introducing-veilfile/", title="Introducing Veilfile", date=date(2026, 10, 5),
                                  description="AI agents need somewhere private to put files. The options are public by default. "
                                              "Veilfile is the private alternative we built."),
                  SimpleNamespace(url="/docs/quickstart/", title="Quickstart", date=None,
                                  description="Sign up, verify your email, create a workspace, issue an API key, and upload your first artifact."),
                  SimpleNamespace(url="/docs/agent/", title="Agent setup", date=None,
                                  description="Give an agent a Veilfile key: env var setup, curl upload examples, MCP connection, key rotation."),
              )},
              within="reading"),
        Entry("field", "One form field on every form page: a mono label above a hairline input, the help line above the input and the errors under it. "
                       "Shown blank, with a help line, with an error (the line goes solid ink, a small mono label reads Check this and the sentence is in full ink) and as a code.",
              {"id": "catalog-field-blank", "name": "username", "label": "Username or email", "type": "text",
               "autocomplete": "username", "required": True},
              within="form-page",
              states=(
                  {"id": "catalog-field-help", "name": "email", "label": "Email", "type": "email", "autocomplete": "email", "required": True,
                   "help": "We verify this address before issuing API keys. It is how we reach you about your account."},
                  {"id": "catalog-field-error", "name": "email", "label": "Email", "type": "email", "value": "ana@example.com", "autocomplete": "email",
                   "required": True, "errors": ["An account with this email address already exists."]},
                  {"id": "catalog-field-code", "name": "user_code", "label": "Device code", "autocomplete": "off", "required": True, "mono": True,
                   "extra": mark_safe('placeholder="XXXX-XXXX" spellcheck="false"')},
              )),
        Entry("form_errors", "The top of a form that has something to say as a whole: a summary with a solid ink line down its left, the mark Check this and each sentence in full ink. "
                             "Shown with one sentence of the page's own, and with the form's errors.",
              {"message": "Invalid username, email, or password. Try again."},
              within="form-page",
              states=({"errors": ["Your current password is incorrect."]},)),
        Entry("meter", "The dashboard's Overview: one figure against its limit, as a mono label, the numbers in words and a hairline bar. "
                       "Shown with a limit, as a size, over its limit (the bar stays full and the figures say so), with no limit and as a value with a unit (no bar).",
              {"id": "catalog-meter-count", "label": "Successful uploads this month", "used": 37, "cap": 100,
               "note": "Counted by calendar month, in UTC."},
              within="meters",
              states=(
                  {"id": "catalog-meter-size", "label": "Storage", "used": 3 * 1024**3 // 2, "cap": 25 * 1024**3, "unit": "bytes"},
                  {"id": "catalog-meter-over", "label": "API keys", "used": 7, "cap": 5},
                  {"id": "catalog-meter-unlimited", "label": "API keys", "used": 3, "cap": None, "unlimited": True},
                  {"id": "catalog-meter-value", "label": "Links last up to", "used": 90, "cap": None, "unit": "day"},
              )),
    )
