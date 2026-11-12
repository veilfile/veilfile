"""A member's own team pages (spec 4.8, team seats): leaving the team.

A member's pages live under /app/team/, because a member's Overview must not contain /app/members/. Every view is the shell's 404 while the
switch is off. The rules are in drop_core/teams.py.
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from drop_billing import account as billing_account
from drop_core.email_verification import is_email_verified
from drop_core.models import Membership, Workspace, current_membership, is_barred
from drop_core.teams import (
    SeatChangeRefused,
    accept_administrator_offer,
    administrator_offer_for,
    decline_administrator_offer,
    has_other_members,
    has_team_features,
    leave_team,
    no_longer_on_team,
    team_seats_only,
)

from .members import PERSON_GONE, REQUEST_NOT_WAITING, TEAM_SUSPENDED
from .views import dashboard_view

ADMINISTRATOR_STAYS = "You administer this team, so you cannot leave it while other people are on it. Make someone else the administrator first."


def _member(request):
    """The signed-in person's current member row, or a redirect to Members, with its sentence, for an administrator of a team with
    other people. An administrator alone who has just left a team goes to Overview, where the notice says so. An administrator alone
    otherwise, and a person with no team, get the shell's 404. Callers check for the redirect with
    isinstance(..., HttpResponse)."""
    member = current_membership(request.user)
    if member.is_administrator:
        if has_other_members(member.workspace):
            messages.info(request, ADMINISTRATOR_STAYS)
            return redirect("app-members")
        if no_longer_on_team(member):
            return redirect("app")
        raise Http404("No such page.")
    return member


@team_seats_only
@dashboard_view
@require_http_methods(["GET", "HEAD", "POST"])
def team_leave(request):
    """Leave the team: a GET asks first and changes nothing; the POST leaves and goes to Overview."""
    member = _member(request)
    if isinstance(member, HttpResponse):
        return member
    if request.method == "POST":
        try:
            leave_team(member)
        except PermissionDenied:
            raise Http404("No such page.") from None
        except SeatChangeRefused as refused:
            if refused.code == "team_suspended":
                messages.info(request, TEAM_SUSPENDED)
                return redirect("app")
            if refused.code in ("person_gone", "not_on_a_team"):
                messages.info(request, PERSON_GONE)
                return redirect("app")
            raise Http404("No such page.") from None
        messages.success(request, "You have left the team. Connect your agents again with veilfile login.")
        return redirect("app")
    administrator = Membership.objects.filter(
        workspace_id=member.workspace_id, role=Membership.ADMINISTRATOR, removed_at__isnull=True
    ).select_related("user").first()
    return render(request, "dashboard/team_leave.html", {"admin_email": administrator.user.email if administrator else ""})


@team_seats_only
@dashboard_view
@require_http_methods(["GET", "HEAD", "POST"])
def team_administrator(request):
    """The request to become the administrator: a GET shows it and changes nothing; a POST with action=accept accepts it and goes to Members,
    and with action=decline declines it and goes to Overview. Any other action changes nothing."""
    member = current_membership(request.user)
    if member.is_administrator:
        # An administrator alone has no team, so no page; one with people has no request waiting for them.
        if not has_other_members(member.workspace):
            raise Http404("No such page.")
        messages.info(request, REQUEST_NOT_WAITING)
        return redirect("app-members")
    action = request.POST.get("action") if request.method == "POST" else None
    offer = administrator_offer_for(member)
    if offer is None or (request.method == "POST" and action not in ("accept", "decline")):
        messages.info(request, REQUEST_NOT_WAITING)
        return redirect("app")
    if action == "decline":
        if decline_administrator_offer(member):
            messages.success(request, "You declined the request.")
        else:
            messages.info(request, REQUEST_NOT_WAITING)
        return redirect("app")
    if action == "accept":
        try:
            accept_administrator_offer(member)
        except SeatChangeRefused as refused:
            if refused.code == "chosen_not_verified":
                return redirect("app-team-administrator")
            messages.info(request, TEAM_SUSPENDED if refused.code == "team_suspended" else REQUEST_NOT_WAITING)
            return redirect("app")
        # The member's workspace was loaded before the transfer, so Stripe's customer is read again.
        billing_account.move_billing_email(Workspace.objects.get(pk=member.workspace_id))
        messages.success(request, "You are now the administrator of this team.")
        return redirect("app-members" if has_team_features(member.workspace) else "app")
    return render(request, "dashboard/team_administrator.html", {"admin_email": offer["admin_email"], "verified": is_email_verified(request.user), "team_on_free": is_barred(member)})
