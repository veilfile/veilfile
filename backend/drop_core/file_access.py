"""Which files a person may see and revoke. Every list and revoke goes through here, so that "only me" holds on every surface."""
from django.db.models import Q
from django.utils import timezone

from .models import FILE_VISIBILITY_MEMBERS_CHOOSE, VISIBILITY_CHOICES, VISIBILITY_TEAM, Artifact, Membership


class NotYourFile(Exception):
    """The person can see the file but may not revoke it."""


def files_visible_to(member):
    """The files this person can see: their own here, their own left behind
    under a membership that has ended, and those shared with the team here.

    It resolves the closed rows first, so the query has two indexed conditions
    and no join."""
    closed = Membership.objects.filter(user_id=member.user_id, removed_at__isnull=False).values_list("pk", flat=True)
    return Artifact.objects.filter(
        Q(member_id__in=[member.pk, *closed]) | Q(workspace_id=member.workspace_id, visibility=VISIBILITY_TEAM)
    )


def file_to_revoke(member, artifact_id):
    """The file if this person may revoke it, None if they cannot see it. Raises NotYourFile when they can see it and may not."""
    artifact = files_visible_to(member).filter(id=artifact_id).select_related("member", "workspace").first()
    if artifact is None:
        return None
    mine = artifact.member is not None and artifact.member.user_id == member.user_id
    shared_here = artifact.workspace_id == member.workspace_id and artifact.visibility == VISIBILITY_TEAM
    if mine or (member.is_administrator and shared_here):
        return artifact
    raise NotYourFile()


def set_visibility(member, artifact_id, visibility):
    """Share one of the person's own files with the team, or make it only theirs again. True when it changed.

    It works only on a file the person uploaded in their current workspace, while the administrator lets members choose, and
    while the file is live (not expired, not held, and the workspace not suspended). It writes the visibility and nothing else."""
    if visibility not in dict(VISIBILITY_CHOICES):
        return False
    changed = (
        Artifact.objects.filter(
            pk=artifact_id,
            member=member,
            workspace_id=member.workspace_id,
            workspace__file_visibility=FILE_VISIBILITY_MEMBERS_CHOOSE,
            workspace__suspended_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .exclude(safety_status="held")
        .update(visibility=visibility)
    )
    return bool(changed)


def file_details(member, ids):
    """What a team's Files page says of each listed file the person can see, by id: its visibility, who uploaded it
    ("You" or an address), and whether the person may revoke it or change its visibility. One query for all the ids."""
    now = timezone.now()
    choose = member.workspace.file_visibility == FILE_VISIBILITY_MEMBERS_CHOOSE
    details = {}
    artifacts = files_visible_to(member).filter(id__in=list(ids)).select_related("member__user", "workspace")
    for artifact in artifacts:
        mine = artifact.member is not None and artifact.member.user_id == member.user_id
        here = artifact.workspace_id == member.workspace_id
        live = artifact.expires_at > now and artifact.safety_status != "held" and artifact.workspace.suspended_at is None
        details[artifact.id] = {
            "visibility": artifact.visibility,
            "uploaded_by": "You" if mine else artifact.member.user.email if artifact.member is not None else "",
            "can_revoke": mine or (member.is_administrator and here and artifact.visibility == VISIBILITY_TEAM),
            "can_change_visibility": mine and here and choose and live,
        }
    return details
