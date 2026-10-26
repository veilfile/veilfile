"""Keys and Connected apps (spec 4.8): the pages that show a person's credentials and take them back.

Invariant 1 (AGENTS.md): a new key's raw value lives in one place only, the answer to the POST that made it. It is never put in the session, a
message, a cookie, a URL, a log or the cache, and that answer is `Cache-Control: no-store`. The POST also carries a one-time token (forms.py)
that the Keys page issued, so a reload, which resends the POST, finds it spent and makes no second key.

A 200 from the Keys POST therefore always means a key was made and is on the page; every refusal has the status the JSON endpoint gives it
(400, 402, 403) or 409 for a spent token.
"""
from datetime import datetime

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from drop_core import account
from drop_core.key_caps import key_cap
from drop_core.models import APIKey, get_or_create_workspace
from pages.errors import not_found

from .forms import KeyForm, issue_key_token, spend_key_token
from .views import dashboard_view

SPENT = (
    "That form was already used or is out of date, so no key was made. A key is shown only once, when it is made. "
    "If you didn't copy yours, revoke it below and make a new one."
)
ALREADY_REVOKED = "That key was already revoked."


def _when(value):
    """An ISO time string of an account function's answer as an aware datetime, or None."""
    return datetime.fromisoformat(value) if value else None


def _key_rows(user):
    return [
        {**item, "created_at": _when(item["created_at"]), "last_used_at": _when(item["last_used_at"])}
        for item in account.list_keys(user).body
    ]


def _keys_page(request, form, status=200, refusal=None):
    """The Keys page with a fresh form token. A page that is the answer to a refused POST has the refusal's status and says why; none of them
    holds a key."""
    context = {"rows": _key_rows(request.user), "form": form, "token": issue_key_token(request.session), "refusal": refusal}
    response = render(request, "dashboard/keys.html", context, status=status)
    response["Cache-Control"] = "no-store"  # the form carries a one-time token, and Back must not show a spent one
    return response


def _refusal(user, answer):
    """The kind of a refused create_key and the sentence for it. The codes are the JSON endpoint's; one this page does not know shows the
    endpoint's own message."""
    body = answer.body if isinstance(answer.body, dict) else {}
    message = body.get("error") or "No key was made."
    code = body.get("code")
    if code == "email_verification_required":
        return {"kind": "unverified", "message": message}
    if code == "key_limit_reached":
        workspace = get_or_create_workspace(user)
        cap = key_cap(workspace)
        if cap is not None:
            message = (
                f"Your {workspace.plan.capitalize()} plan allows {cap} active {'key' if cap == 1 else 'keys'}. "
                "Revoke one below to make room, or change plans."
            )
        return {"kind": "cap", "message": message}
    return {"kind": "other", "message": message}


def _key_made(request, answer):
    """The one page that shows a key: the answer to the POST that made it, 200, never cached."""
    body = answer.body
    raw_key = body["api_key"]
    # shell_page is emptied, as pages/errors.py does: the answer is at the Keys address, whose section entry would make it a page that changes in place.
    context = {"shell_page": None, "key_name": body["name"], "new_key": raw_key}
    response = render(request, "dashboard/key_made.html", context)
    response["Cache-Control"] = "no-store"
    return response


def _create(request):
    if not spend_key_token(request.session, request.POST.get("token")):
        return _keys_page(request, KeyForm(), 409, {"kind": "spent", "message": SPENT})
    # Spent before the key is made, and saved now: SessionMiddleware saves nothing after a 500, so a failure once the key exists would
    # otherwise leave the token spendable, and a retry would make a second key.
    request.session.save()
    form = KeyForm(request.POST)
    if not form.is_valid():
        return _keys_page(request, form, 400)
    answer = account.create_key(request.user, form.cleaned_data["name"], request=request)
    if answer.status == 201:
        return _key_made(request, answer)
    return _keys_page(request, form, answer.status, _refusal(request.user, answer))


@dashboard_view
@require_http_methods(["GET", "HEAD", "POST"])
def keys(request):
    if request.method == "POST":
        return _create(request)
    return _keys_page(request, KeyForm())


def _own_key(user, key_id):
    """The key of this person's workspace with this id, or None: another workspace's key and a missing one are the same."""
    return APIKey.objects.filter(pk=key_id, workspace=get_or_create_workspace(user)).first()


@dashboard_view
@require_http_methods(["GET", "HEAD", "POST"])
def key_revoke(request, key_id):
    key = _own_key(request.user, key_id)
    if key is None:
        return not_found(request)
    if not key.is_active:
        messages.info(request, ALREADY_REVOKED)
        return redirect("app-keys")
    if request.method == "POST":
        if account.revoke_key(request.user, key.pk).status != 204:
            return not_found(request)
        messages.success(request, "The key is revoked.")
        return redirect("app-keys")
    return render(request, "dashboard/key_revoke.html", {"key": key})


def _app_rows(user):
    return [{**item, "last_authorized_at": _when(item["last_authorized_at"])} for item in account.list_grants(user).body]


@dashboard_view
@require_http_methods(["GET", "HEAD"])
def apps(request):
    return render(request, "dashboard/apps.html", {"rows": _app_rows(request.user)})


@dashboard_view
@require_http_methods(["GET", "HEAD", "POST"])
def app_disconnect(request, app_id):
    app = next((row for row in _app_rows(request.user) if row["id"] == app_id), None)
    if app is None:
        return not_found(request)
    if request.method == "POST":
        if account.disconnect_grant(request.user, app_id).status != 204:
            return not_found(request)
        messages.success(request, f"{app['name']} is disconnected.")
        return redirect("app-apps")
    return render(request, "dashboard/app_disconnect.html", {"app": app})
