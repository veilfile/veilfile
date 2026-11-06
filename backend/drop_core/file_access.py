"""Which files a person may see and revoke. Every list and revoke goes through here, so that "only me" holds on every surface."""
from django.db.models import Q

from .models import VISIBILITY_TEAM, Artifact, Membership


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
