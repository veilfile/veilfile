"""Template tags for pages on the new shell."""
import json

from django import template
from django.core.exceptions import NON_FIELD_ERRORS
from django.middleware.csrf import get_token
from django.templatetags.static import static
from django.utils.safestring import mark_safe

from pages import registry

register = template.Library()

# JSON-LD sits inside <script>: these three must never appear raw in it.
_SCRIPT_SAFE = {ord("<"): "\\u003c", ord(">"): "\\u003e", ord("&"): "\\u0026"}


def _inside_dashboard(context):
    """Whether this page draws the dashboard's menu (spec 4.4): a signed-in visitor, on an address whose URL name registry.DASHBOARD_SECTIONS lists,
    on a page that has not asked for the ordinary menu. A page asks with `plain_menu`: the error pages and the throttled page do, since they carry
    nothing that belongs to a person and no token (AGENTS.md, "Error pages"), whichever address they are drawn for. A page drawn with no request
    (the miss page of /a/<token>) is never inside."""
    request = context.get("request")
    user = getattr(request, "user", None)
    match = getattr(request, "resolver_match", None)
    return bool(
        user is not None and user.is_authenticated
        and getattr(match, "url_name", None) in registry.DASHBOARD_SECTIONS
        and not context.get("plain_menu")
    )


@register.simple_tag(takes_context=True)
def shell_menu(context):
    """The menu's groups for this request: Sign in or Dashboard, and the current page marked; inside the dashboard, its sections instead (see _inside_dashboard)."""
    request = context.get("request")
    user = getattr(request, "user", None)
    match = getattr(request, "resolver_match", None)
    return registry.menu_groups(
        signed_in=bool(user is not None and user.is_authenticated),
        current=getattr(match, "url_name", None),
        inside_dashboard=_inside_dashboard(context),
    )


@register.simple_tag(takes_context=True)
def shell_sign_out(context):
    """The CSRF token for the Sign out button that ends group 2 of the menu inside the dashboard, and an empty string on every other page. It is asked
    for only inside the dashboard, so that no other page sets the CSRF cookie or carries a token: the public pages, the throttled page and the miss
    page of /a/<token> stay as they are (shell.html passes it to the menu as sign_out)."""
    return get_token(context["request"]) if _inside_dashboard(context) else ""


def _summary_takes_focus(form, names):
    """Whether the error summary, and not a field, takes focus: the form has an error that belongs to no field and none of `names` has one."""
    errors = getattr(form, "errors", None) or {}
    return bool(errors.get(NON_FIELD_ERRORS)) and not any(errors.get(name) for name in names)


@register.simple_tag
def focus_field(form, *names, default=""):
    """The name of the field that takes focus when a form page loads: the first of `names` (the form's fields in the order the page draws them) that the form reports an error for, so
    that a form sent back with errors puts the visitor on the field to put right, and not at the top of the page or on the page's usual field; with no error on any of them,
    `default` (the field of a page that exists to take one input, or none). When the only errors are the form's own (see focus_summary) the summary has focus, so the result is empty and
    no field has it, the page's usual one included. The page passes the result to each field as `focus`, and components/field.html prints autofocus
    on the one it names, so the browser does it with no script and one field has it."""
    errors = getattr(form, "errors", None) or {}
    named = next((name for name in names if errors.get(name)), None)
    if named is not None:
        return named
    return "" if _summary_takes_focus(form, names) else default


@register.simple_tag
def focus_summary(form, *names):
    """Whether the form's error summary (components/form_errors.html) takes focus when the page loads: true when the form has an error that belongs to no field, such as a refused sign-in,
    and none of `names` (the same fields as focus_field, in the order the page draws them) has one. An alert that is on the page as it loads is announced inconsistently by screen readers,
    and focus is left at the top of the page, so the page moves focus to it. The page passes the result to the component as `focus`, which prints tabindex="-1" and autofocus; a field in
    error wins (focus_field names it), so the page has one autofocus."""
    return _summary_takes_focus(form, names)


def _article(entry, organization):
    """The schema.org Article for a Journal post (BlogPosting) or a Docs page (TechArticle), from shell_page.
    Veilfile is its author and publisher."""
    article = entry["article"]
    veilfile = {key: organization[key] for key in ("@type", "name", "url")}
    data = {
        "@context": "https://schema.org",
        "@type": article["type"],
        "headline": article["headline"],
        "mainEntityOfPage": {"@type": "WebPage", "@id": entry["canonical"]},
        "image": entry["image"],
        "author": veilfile,
        "publisher": veilfile,
    }
    if entry["description"]:
        data["description"] = entry["description"]
    if article["published"]:
        data["datePublished"] = article["published"].isoformat()
    return data


@register.simple_tag(takes_context=True)
def structured_data(context):
    """The page's JSON-LD (spec 6.6): Organization on every registry page; then, on a Journal post or a Docs
    page, its Article (from shell_page.article); then whatever the view passes as structured_data (a list of
    dicts). Each is its own script, marked for in-place changes."""
    organization = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Veilfile",
        "url": registry.SITE_ORIGIN + "/",
        "logo": registry.SITE_ORIGIN + static("img/apple-touch-icon.png"),
    }
    items = [organization]
    entry = context.get("shell_page")
    if entry and entry.get("article"):
        items.append(_article(entry, organization))
    items.extend(context.get("structured_data") or ())
    return mark_safe("".join(
        '<script type="application/ld+json" data-page-meta>'
        + json.dumps(item, ensure_ascii=False).translate(_SCRIPT_SAFE)
        + "</script>\n"
        for item in items
    ))
