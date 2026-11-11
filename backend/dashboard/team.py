"""A member's own team pages (spec 4.8, team seats): leaving the team.

A member's pages live under /app/team/, because a member's Overview must not contain /app/members/. Every view is the shell's 404 while the
switch is off. The rules are in drop_core/teams.py.
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from drop_core.models import Membership, current_membership
from drop_core.teams import SeatChangeRefused, has_other_members, leave_team, team_seats_only

from .members import TEAM_SUSPENDED
from .views import dashboard_view

ADMINISTRATOR_STAYS = "You administer this team, so you cannot leave it while other people are on it. Make someone else the administrator first."


def _member(request):
    """The signed-in person's current member row, or a redirect to Members, with its sentence, for an administrator of a team with
    other people. An administrator alone, and a person with no team, get the shell's 404. Callers check for the redirect with
    isinstance(..., HttpResponse)."""
    member = current_membership(request.user)
    if member.is_administrator:
        if has_other_members(member.workspace):
            messages.info(request, ADMINISTRATOR_STAYS)
            return redirect("app-members")
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
            if refused.code != "team_suspended":
                raise Http404("No such page.") from None
            messages.info(request, TEAM_SUSPENDED)
            return redirect("app")
        messages.success(request, "You have left the team. Connect your agents again with veilfile login.")
        return redirect("app")
    administrator = Membership.objects.filter(
        workspace_id=member.workspace_id, role=Membership.ADMINISTRATOR, removed_at__isnull=True
    ).select_related("user").first()
    return render(request, "dashboard/team_leave.html", {"admin_email": administrator.user.email if administrator else ""})
