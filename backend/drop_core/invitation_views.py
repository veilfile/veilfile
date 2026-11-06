"""The invitation page: what an invitation offers, joining and declining.

The words are in templates/registration/invitation.html, invitation_invalid.html and invitation_declined.html. A link that is
not valid says so one way whatever the reason, and a signed-out page says nothing of whether an account exists.
"""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .teams import JoinRefused, accept_invitation, decline_invitation, find_invitation, joined_through, team_seats_only, why_not_join

JOINED = "You have joined the team. Connect your agents again with veilfile login."


def _invalid(request):
    return render(request, "registration/invitation_invalid.html", status=404)


def _invalid_or_joined(request, token):
    """The not-valid answer, except for a second click on Join by a person already on that team, who goes to Overview."""
    if request.method == "POST" and request.POST.get("action") == "accept" and joined_through(request.user, token):
        return redirect("app")
    return _invalid(request)


def _page(request, invitation, code=None, status=200):
    """The invitation's page for this visitor. `code` is the refusal to show, or None to work it out."""
    inviter = invitation.invited_by
    if inviter is None:
        return _invalid(request)
    user = request.user
    if code is None and user.is_authenticated:
        code = why_not_join(user, invitation)
    context = {
        "inviter": inviter.email,
        "address": invitation.email,
        "here": request.path,
        "code": code,
        "signed_in": user.is_authenticated,
    }
    return render(request, "registration/invitation.html", context, status=status)


def _answer(request, token):
    if request.method == "POST" and request.POST.get("action") == "decline":
        if decline_invitation(token):
            return render(request, "registration/invitation_declined.html")
        return _invalid(request)
    invitation = find_invitation(token)
    if invitation is None or invitation.workspace.suspended_at is not None:
        return _invalid_or_joined(request, token)
    if request.method != "POST":
        return _page(request, invitation)
    if request.POST.get("action") != "accept":
        return _page(request, invitation, status=400)
    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={request.path}")
    try:
        accept_invitation(request.user, token)
    except JoinRefused as refused:
        if refused.code == "not_valid":
            return _invalid_or_joined(request, token)
        return _page(request, invitation, code=refused.code, status=400)
    messages.success(request, JOINED)
    return redirect("app")


@team_seats_only
@ensure_csrf_cookie
@require_http_methods(["GET", "HEAD", "POST"])
def invitation(request, token):
    """The page behind an invitation link. A GET writes nothing. A POST with action=accept joins a signed-in person, and with
    action=decline closes the invitation on the strength of the link alone."""
    response = _answer(request, token)
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "no-store"
    return response
