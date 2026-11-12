"""Deleting an account for staff (team seats, ways out).

The admin's stock delete runs one transaction and stops at the files and keys that point at a membership row. This service
stops the account's credentials on every team first, then deletes its files and keys there in batches, so that the bytes go
after each batch, and last deletes the account, which takes its own workspaces with it. It refuses what would lose a team,
a subscription or evidence."""
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from . import file_access
from .models import APIKey, Invitation, MemberUsageCounter, Membership, UsageCounter, Workspace
from .oauth import revoke_user_oauth_grants
from .teams import _close_member_row, _deny_device_logins

User = get_user_model()

REFUSALS = {
    "administers_people": "This account administers a team with other people on it. Make someone else the administrator first.",
    "pays_through_stripe": "This account pays for a plan through Stripe. Cancel the subscription in Stripe first.",
    "has_held_files": "Deleting this account would delete files that are held. Release or delete them first.",
    "changed": "Something changed while the account was being deleted, so it was not deleted. It is inactive now.",
}


class DeletionRefused(Exception):
    """The account was not deleted. `code` is administers_people, pays_through_stripe, has_held_files or changed."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _owned_ids(user):
    return list(Workspace.objects.filter(owner=user).values_list("pk", flat=True))


def _row_ids(user):
    return list(Membership.objects.filter(user=user).values_list("pk", flat=True))


def why_not_delete(user):
    """The code of the first reason this account cannot be deleted now, or None. Its sentence is in REFUSALS."""
    owned = _owned_ids(user)
    if Membership.objects.filter(workspace_id__in=owned, removed_at__isnull=True).exclude(user=user).exists():
        return "administers_people"
    if Workspace.objects.filter(pk__in=owned).exclude(stripe_subscription_id="").exists():
        return "pays_through_stripe"
    if file_access.held_files_at_stake(owned, _row_ids(user)):
        return "has_held_files"
    return None


def deletion_summary(user):
    """What deleting the account takes, as counts only: the teams it has a row on and does not own, and the files and keys in all, its own
    workspace's included. It reads no file name."""
    owned = _owned_ids(user)
    rows = _row_ids(user)
    return {
        "teams": Membership.objects.filter(user=user).exclude(workspace_id__in=owned).values("workspace_id").distinct().count(),
        "files": file_access.files_at_stake(owned, rows),
        "keys": APIKey.objects.filter(Q(workspace_id__in=owned) | Q(member_id__in=rows)).count(),
    }


def _changed_since_the_check(user):
    """Whether the account now has something the first check did not see: a reason in why_not_delete, or a row on a team it
    does not own that still has a file or a key, which would make the delete raise RestrictedError. Call it under the locks."""
    if why_not_delete(user) is not None:
        return True
    owned = _owned_ids(user)
    rows = list(Membership.objects.filter(user=user).exclude(workspace_id__in=owned).values_list("pk", flat=True))
    return file_access.files_at_stake([], rows) > 0 or APIKey.objects.filter(member_id__in=rows).exists()


def delete_account(user):
    """Delete the account and what goes with it. Raises DeletionRefused when why_not_delete names a reason.

    Step by step, outside any transaction. When the first check passes, the account is made inactive and its administrator
    offers are cleared, so that nobody can sign in, upload, accept an offer or join while it is deleted. Then, on each team the
    account is on and does not own, one transaction stops its credentials; then its files, keys and waiting invitations go.
    Last, in one transaction, the workspaces it owns are locked and checked again, and the account is deleted with them. When
    something changed, it raises DeletionRefused("changed") and leaves the account inactive. A failure after the first step
    leaves the stopped credentials stopped, which is safe.

    The last transaction takes its locks in the order upload_artifact does: the workspace counters of the month, then the
    member counters, then the workspace rows. An upload holds its counters while it waits for the workspace row, so a deletion
    that locked the workspace rows first and then deleted the counters would wait on that upload while the upload waits on it,
    and Postgres would end one of them with a deadlock."""
    if connection.in_atomic_block:
        raise RuntimeError("delete_account must run outside any transaction")
    code = why_not_delete(user)
    if code is not None:
        raise DeletionRefused(code)
    User.objects.filter(pk=user.pk).update(is_active=False)
    Membership.objects.filter(user=user).update(offered_administrator_at=None)
    now = timezone.now()
    owned = _owned_ids(user)
    elsewhere = list(Membership.objects.filter(user=user).exclude(workspace_id__in=owned))
    for row in elsewhere:
        with transaction.atomic():
            _deny_device_logins(row.workspace_id, user.pk)
            Workspace.objects.select_for_update().get(pk=row.workspace_id)
            current = Membership.objects.filter(pk=row.pk, removed_at__isnull=True).first()
            if current is not None:
                _close_member_row(current, now)
                revoke_user_oauth_grants(user)
    for row in elsewhere:
        file_access.delete_files_of(row)
        APIKey.objects.filter(member=row).delete()
    Invitation.objects.filter(
        invited_by=user, accepted_at__isnull=True, declined_at__isnull=True, revoked_at__isnull=True
    ).update(revoked_at=now)
    with transaction.atomic():
        owned = _owned_ids(user)
        list(UsageCounter.objects.select_for_update().filter(workspace_id__in=owned).order_by("pk"))
        list(
            MemberUsageCounter.objects.select_for_update()
            .filter(Q(member__workspace_id__in=owned) | Q(member__user=user))
            .order_by("pk")
        )
        list(Workspace.objects.select_for_update().filter(pk__in=_owned_ids(user)).order_by("pk"))
        if _changed_since_the_check(user):
            raise DeletionRefused("changed")
        user.delete()
