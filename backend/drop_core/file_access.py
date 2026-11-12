"""Which files a person may see and revoke. Every list and revoke goes through here, so that "only me" holds on every surface."""
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import (
    FILE_VISIBILITY_MEMBERS_CHOOSE,
    VISIBILITY_CHOICES,
    VISIBILITY_TEAM,
    Artifact,
    Membership,
    PendingUpload,
)


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


def expire_files_of(row, now):
    """End the live files one person uploaded to their team, of either visibility, by setting their expiry to `now`. Held
    files are left alone, so staff keep them as evidence. Returns how many files it ended. It reads no file name."""
    return (
        Artifact.objects.filter(member=row, workspace_id=row.workspace_id, expires_at__gt=now)
        .exclude(safety_status="held")
        .update(expires_at=now)
    )


def live_files_of(row, now):
    """How many live files one person has on their team, and their bytes, as a tuple. Held files count, because they still
    take room. It reads no file name."""
    totals = Artifact.objects.filter(member=row, workspace_id=row.workspace_id, expires_at__gt=now).aggregate(
        count=Count("pk"), total=Sum("size_bytes")
    )
    return totals["count"], totals["total"] or 0


def end_upload_urls_of(row):
    """Mark the unused upload URLs one person minted on their team as used, so that none of them takes a file again."""
    PendingUpload.objects.filter(member=row, workspace_id=row.workspace_id, used=False).update(used=True)


def uploads_of(artifact_ids):
    """The workspace, member and API key of each listed file, as (workspace id, member id, key id) tuples. The member and the key
    can be None. It reads no file name."""
    return list(Artifact.objects.filter(pk__in=list(artifact_ids)).values_list("workspace_id", "member_id", "api_key_id"))


def mark_held(workspace_id, artifact_ids):
    """Hold the listed files of one workspace that are live and not held yet, and return the ids it changed. Call it under
    that workspace's lock.

    The rows are locked as they are chosen. A revoke expires its row before it deletes the bytes, and its expiry skips a
    held row, so on the same row exactly one of the two wins: a file a revoke has already expired is not held here, and a
    revoke that comes after the hold finds the file held and deletes nothing."""
    changed = list(
        Artifact.objects.select_for_update()
        .filter(workspace_id=workspace_id, pk__in=list(artifact_ids), expires_at__gt=timezone.now())
        .exclude(safety_status="held")
        .values_list("pk", flat=True)
    )
    Artifact.objects.filter(pk__in=changed).update(safety_status="held")
    return changed


def is_held(artifact_id):
    """Whether the file is held now. A fresh read, for a caller that must not act on a stale one."""
    return Artifact.objects.filter(pk=artifact_id, safety_status="held").exists()


def mark_clear(artifact_ids):
    """Clear the hold on the listed files and return the ids that were held."""
    with transaction.atomic():
        changed = list(
            Artifact.objects.select_for_update()
            .filter(pk__in=list(artifact_ids), safety_status="held")
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        Artifact.objects.filter(pk__in=changed).update(safety_status="clear")
    return changed


def held_file_count(workspace):
    """How many live files of this team are held, whoever uploaded them, a current member or a removed one."""
    return Artifact.objects.filter(workspace=workspace, safety_status="held", expires_at__gt=timezone.now()).count()


def held_files_at_stake(owned_workspace_ids, row_ids):
    """Whether any held file would go with an account: one in a workspace the account owns, or one uploaded by any of its
    rows. A held file counts whether it is live or expired and not yet swept, because staff keep it as evidence until it is gone."""
    return Artifact.objects.filter(safety_status="held").filter(
        Q(workspace_id__in=list(owned_workspace_ids)) | Q(member_id__in=list(row_ids))
    ).exists()


def files_at_stake(owned_workspace_ids, row_ids):
    """How many files would go with an account: those in a workspace it owns and those uploaded by any of its rows. It reads no
    file name."""
    return Artifact.objects.filter(
        Q(workspace_id__in=list(owned_workspace_ids)) | Q(member_id__in=list(row_ids))
    ).count()


def delete_files_of(row, batch=500):
    """Delete the files one row uploaded that are not held, `batch` rows at a time and one transaction for each, so that the
    receiver removes each batch's bytes right after its commit. It never deletes a held file. Each batch locks its rows with the
    held ones excluded and deletes only what that lock returns, so a hold and this delete of one row cannot both win: the hold
    takes its rows under the team's workspace lock (mark_held), and Postgres tests a locked row's WHERE clause again after the
    other transaction commits. Held files stay, and the loop ends when only they are left. Returns how many files it deleted.
    Call it outside a transaction."""
    deleted = 0
    while True:
        ids = list(Artifact.objects.filter(member=row).exclude(safety_status="held").order_by("pk").values_list("pk", flat=True)[:batch])
        if not ids:
            return deleted
        with transaction.atomic():
            locked = list(
                Artifact.objects.select_for_update()
                .filter(pk__in=ids)
                .exclude(safety_status="held")
                .order_by("pk")
                .values_list("pk", flat=True)
            )
            Artifact.objects.filter(pk__in=locked).delete()
        deleted += len(locked)
