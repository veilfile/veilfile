"""The Members page (spec 4.8, team seats): the people on the team and what each uses, the invitations, allowances and the setting for new files.

Every view is for an administrator of a team that has these pages (drop_core.teams.has_team_features), and is the shell's 404 for anyone else and
for everyone while the switch is off. The page reads counts and sums and never a file's name. The rules are in drop_core/teams.py.
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from drop_core.file_access import live_files_of
from drop_core.key_caps import key_cap
from drop_core.models import (
    FILE_VISIBILITY_ALWAYS_ONLY_ME,
    FILE_VISIBILITY_ALWAYS_TEAM,
    FILE_VISIBILITY_MEMBERS_CHOOSE,
    Invitation,
    Membership,
    current_membership,
)
from drop_core.plans import GB, PLANS
from drop_core.teams import (
    InviteRefused,
    cancel_invitation,
    SeatChangeRefused,
    has_team_features,
    invite,
    member_rows,
    seat_limit,
    seats_in_use,
    set_allowances,
    remove_member,
    set_file_visibility_setting,
    team_seats_only,
    waiting_invitations,
)

from .forms import AllowanceForm, InviteForm, gb_text
from .views import dashboard_view

# What invite refuses with, and the sentence for each (the codes are drop_core.teams.invite's).
REFUSALS = {
    "email_verification_required": "Verify your email before you invite anyone.",
    "no_free_seat": "Every seat is in use, so no invitation was sent. Cancel a waiting invitation, or change plans.",
    "already_on_team": "That person is already on this team.",
    "already_waiting": "An invitation to that address is already waiting. Cancel it to send a new one.",
    "team_rate_limited": "This team has sent as many invitations as it can for now. Try again later.",
    "address_rate_limited": "That address has had too many invitations today. Try again tomorrow.",
    "too_many_waiting": "This team has too many invitations waiting. Cancel some to send more.",
    "declined_recently": "That address declined an invitation from this team recently, so no invitation was sent.",
    "site_rate_limited": "Invitations are paused for now. Try again tomorrow.",
    "send_failed": "The invitation could not be sent. Try again in a moment.",
    "workspace_suspended": "This account is suspended, so no invitation was sent. Contact support@veilfile.com.",
}
BAD_SETTING = "Choose one of the options for new files."
NOT_WAITING = "That invitation is not waiting any more."
PERSON_GONE = "That person is not on the team any more."
TEAM_SUSPENDED = "This team is suspended, so nobody can be removed or leave it until the suspension ends. Contact support@veilfile.com."
VISIBILITY_CHOICES = (
    (FILE_VISIBILITY_MEMBERS_CHOOSE, "Each person decides", "New files start as only them."),
    (FILE_VISIBILITY_ALWAYS_TEAM, "Everyone on the team", ""),
    (FILE_VISIBILITY_ALWAYS_ONLY_ME, "Only the person who uploaded them", ""),
)


def _administrator(request):
    """The signed-in administrator's row of a team that has these pages, else the shell's 404."""
    member = current_membership(request.user)
    if not member.is_administrator or not has_team_features(member.workspace):
        raise Http404("No such page.")
    return member


def _capped(allowance, limit):
    """The team's limit when the allowance is set above it, else None."""
    return limit if allowance is not None and limit is not None and allowance > limit else None


def _rows(workspace):
    plan = PLANS[workspace.plan]
    limits = {"uploads": plan["uploads"], "storage": plan["storage_gb"] * GB, "keys": key_cap(workspace)}
    return [
        {
            **row,
            "uploads_cap": _capped(row["upload_allowance"], limits["uploads"]),
            "storage_cap": _capped(row["storage_allowance"], limits["storage"]),
            "keys_cap": _capped(row["key_allowance"], limits["keys"]),
        }
        for row in member_rows(workspace)
    ]


def _page(request, member, form=None, refusal="", status=200):
    workspace = member.workspace
    context = {
        "rows": _rows(workspace),
        "waiting": waiting_invitations(workspace),
        "seats_used": seats_in_use(workspace),
        "seat_limit": seat_limit(workspace),
        "form": form or InviteForm(),
        "refusal": refusal,
        "visibility": workspace.file_visibility,
        "visibility_choices": VISIBILITY_CHOICES,
    }
    return render(request, "dashboard/members.html", context, status=status)


def _invite(request, member):
    form = InviteForm(request.POST)
    if not form.is_valid():
        return _page(request, member, form, status=400)
    try:
        invitation = invite(
            member, form.cleaned_data["email"], build_url=lambda raw: request.build_absolute_uri(reverse("invitation", args=[raw]))
        )
    except ValidationError as error:
        form.add_error("email", error)
        return _page(request, member, form, status=400)
    except InviteRefused as refused:
        return _page(request, member, form, REFUSALS[refused.code], status=400)
    except PermissionDenied:
        raise Http404("No such page.") from None
    messages.success(request, f"The invitation is on its way to {invitation.email}.")
    return redirect("app-members")


@team_seats_only
@dashboard_view
@require_http_methods(["GET", "HEAD", "POST"])
def members(request):
    """The page: a GET draws it and changes nothing; a POST invites the address in the form, then comes back here or shows why not."""
    member = _administrator(request)
    if request.method == "POST":
        return _invite(request, member)
    return _page(request, member)


@team_seats_only
@dashboard_view
@require_http_methods(["GET", "HEAD", "POST"])
def file_visibility_setting(request):
    """POST: choose who can see the team's new files. A GET goes to the Members page."""
    member = _administrator(request)
    if request.method != "POST":
        return redirect("app-members")
    if not set_file_visibility_setting(member, request.POST.get("file_visibility", "")):
        return _page(request, member, refusal=BAD_SETTING, status=400)
    messages.success(request, "The setting is saved.")
    return redirect("app-members")


def _person(member, member_id):
    person = Membership.objects.filter(pk=member_id, workspace_id=member.workspace_id, removed_at__isnull=True).select_related("user").first()
    if person is None:
        raise Http404("No such person.")
    return person


@team_seats_only
@dashboard_view
@require_http_methods(["GET", "HEAD", "POST"])
def member_allowance(request, member_id):
    """The allowance of one person on the team. A GET shows the form; a POST saves it, and an empty field clears that allowance."""
    member = _administrator(request)
    person = _person(member, member_id)
    if request.method == "POST":
        form = AllowanceForm(request.POST)
        if form.is_valid():
            if not set_allowances(
                member, person.pk, uploads=form.cleaned_data["uploads"], storage_bytes=form.cleaned_data["storage_gb"], keys=form.cleaned_data["keys"]
            ):
                raise Http404("No such person.")
            messages.success(request, "The allowance is saved.")
            return redirect("app-members")
        status = 400
    else:
        form = AllowanceForm(
            initial={
                "uploads": "" if person.upload_allowance is None else str(person.upload_allowance),
                "storage_gb": gb_text(person.storage_allowance_bytes),
                "keys": "" if person.key_allowance is None else str(person.key_allowance),
            }
        )
        status = 200
    return render(request, "dashboard/member_allowance.html", {"form": form, "email": person.user.email}, status=status)


@team_seats_only
@dashboard_view
@require_http_methods(["GET", "HEAD", "POST"])
def invitation_cancel(request, invitation_id):
    """Cancel a waiting invitation: a GET asks first and changes nothing; the POST cancels and goes back to Members."""
    member = _administrator(request)
    invitation = Invitation.objects.filter(pk=invitation_id, workspace_id=member.workspace_id).first()
    if invitation is None:
        raise Http404("No such invitation.")
    if request.method == "POST":
        if cancel_invitation(member, invitation.pk):
            messages.success(request, "The invitation is canceled.")
        else:
            messages.info(request, NOT_WAITING)
        return redirect("app-members")
    if not any(waiting.pk == invitation.pk for waiting in waiting_invitations(member.workspace)):
        messages.info(request, NOT_WAITING)
        return redirect("app-members")
    return render(request, "dashboard/invitation_cancel.html", {"address": invitation.email})


@team_seats_only
@dashboard_view
@require_http_methods(["GET", "HEAD", "POST"])
def member_remove(request, member_id):
    """Remove a member: a GET asks first, with the choice for their files, and changes nothing; the POST removes them and goes back to Members.

    Only the exact choice "revoke" ends their files. Anything else lets them run out."""
    member = _administrator(request)
    person = Membership.objects.filter(pk=member_id, workspace_id=member.workspace_id).select_related("user").first()
    if person is None:
        raise Http404("No such person.")
    if person.removed_at is not None or person.is_administrator:
        messages.info(request, PERSON_GONE)
        return redirect("app-members")
    if request.method == "POST":
        revoke = request.POST.get("files") == "revoke"
        try:
            remove_member(member, person.pk, revoke_files=revoke)
        except PermissionDenied:
            raise Http404("No such page.") from None
        except SeatChangeRefused as refused:
            messages.info(request, TEAM_SUSPENDED if refused.code == "team_suspended" else PERSON_GONE)
            return redirect("app-members")
        email = person.user.email
        messages.success(request, f"{email} is no longer on the team, and their files are revoked." if revoke else f"{email} is no longer on the team.")
        return redirect("app-members" if has_team_features(member.workspace) else "app")
    count, size = live_files_of(person, timezone.now())
    return render(request, "dashboard/member_remove.html", {"email": person.user.email, "count": count, "size": size})
