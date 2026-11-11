"""Holding files for staff. A hold stops the file's link and the API key that uploaded it, and tells the team's administrator
once, without naming the file or the person (the Words of team seats, ways out)."""
import logging
from collections import namedtuple

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string

from . import file_access
from .models import APIKey, Membership, Workspace

logger = logging.getLogger(__name__)

# held: files newly held. told: administrators emailed. not_told: administrators whose email failed. held_ids: the ids newly held.
HoldResult = namedtuple("HoldResult", ["held", "told", "not_told", "held_ids"])


def _administrator_of_team(workspace_id):
    """The team's current administrator row, or None. A plain read that never writes, unlike administrator_of."""
    return (
        Membership.objects.filter(workspace_id=workspace_id, role=Membership.ADMINISTRATOR, removed_at__isnull=True)
        .select_related("user")
        .first()
    )


def _hold_in_workspace(workspace_id, artifact_ids):
    """Hold the files under the team's lock and stop the keys that uploaded them. Returns (changed ids, their uploads, the
    administrator row read under the same lock, so that a transfer cannot send the notice to the former one)."""
    with transaction.atomic():
        if not Workspace.objects.select_for_update().filter(pk=workspace_id).exists():
            return [], [], None
        changed = file_access.mark_held(workspace_id, artifact_ids)
        uploads = file_access.uploads_of(changed)
        keys = [key for _, _, key in uploads if key is not None]
        if keys:
            APIKey.objects.filter(pk__in=keys).update(is_active=False)
        administrator = _administrator_of_team(workspace_id)
    return changed, uploads, administrator


def _tell(administrator, count, key_stopped):
    """Email the administrator. False when it fails, and the log holds the exception's class only."""
    try:
        body = render_to_string("registration/file_held_email.txt", {"count": count, "key_stopped": key_stopped})
        send_mail("A file on your Veilfile team was held", body, settings.DEFAULT_FROM_EMAIL, [administrator.user.email])
    except Exception as exc:
        logger.error("Hold notice failed: %s", type(exc).__name__)
        return False
    return True


def hold_files(artifact_ids):
    """Hold the listed files, stop the keys that uploaded them and email each team's current administrator once. A file that is
    already held changes nothing and sends nothing. A failed email never undoes a hold. Call it outside a transaction."""
    held = told = not_told = 0
    held_ids = []
    for workspace_id in {workspace for workspace, _, _ in file_access.uploads_of(artifact_ids)}:
        changed, uploads, administrator = _hold_in_workspace(workspace_id, artifact_ids)
        held += len(changed)
        held_ids.extend(changed)
        someone_else = any(member != getattr(administrator, "pk", None) for _, member, _ in uploads)
        if not changed or administrator is None or not someone_else:
            continue
        if _tell(administrator, len(changed), any(key is not None for _, _, key in uploads)):
            told += 1
        else:
            not_told += 1
    return HoldResult(held, told, not_told, held_ids)


def release_files(artifact_ids):
    """Clear the hold on the listed files and return the ids it cleared. The keys that uploaded them stay stopped."""
    return file_access.mark_clear(artifact_ids)
