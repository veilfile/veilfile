"""Plan API-key caps.

Every active ``vlf_`` key in a workspace counts toward its plan's key cap
(``PLANS[plan]["keys"]``, ``None`` = unlimited), whether it was created in
the dashboard or minted by the device flow (``veilfile login``). OAuth
grants are not API keys and never count.

At the cap, a new key can only be minted as a *replacement*: the caller
names an active key of their own in the same workspace, and it is revoked in
the same transaction that creates the new one. A replacement never raises the
active count, so a workspace that is already over its cap (keys left over
from a bigger plan) can still rotate keys but never add one.
"""

from django.db import transaction

from .models import APIKey, Workspace, administrator_of
from .plans import PLANS


class KeyCapReached(Exception):
    """Minting another key would exceed the workspace's plan key cap."""


class MemberKeyCapReached(KeyCapReached):
    """Minting another key would exceed the key allowance set for this member."""


def key_cap(workspace):
    """The plan's API-key cap, or None for unlimited."""
    return PLANS[workspace.plan]["keys"]


def active_keys(workspace):
    return APIKey.objects.filter(workspace=workspace, is_active=True)


def active_key_count(workspace):
    return active_keys(workspace).count()


def replaceable_keys(workspace, member):
    """The member's own active keys in the workspace, oldest first: the only keys they may replace."""
    return active_keys(workspace).filter(member=member).order_by("created_at")


def needs_replacement(workspace, member):
    """True when the team is at (or over) its cap, or the member is at their
    key allowance, so a new key must replace one of the member's own."""
    cap = key_cap(workspace)
    if cap is not None and active_key_count(workspace) >= cap:
        return True
    allowance = member.key_allowance
    return allowance is not None and replaceable_keys(workspace, member).count() >= allowance


def create_key_within_cap(workspace, name, device_issued=False, replacing=None, member=None):
    """Mint a key for ``workspace`` unless that would exceed the plan cap.

    ``replacing`` (an APIKey) is deactivated in the same transaction when
    it is still an active key of this member; a teammate's key is ignored,
    so the caps then apply as for a new key. The workspace row is
    locked (SELECT ... FOR UPDATE on Postgres) so concurrent mints cannot
    both pass the check. Returns ``(key, raw_key)``; raises KeyCapReached, or
    MemberKeyCapReached when only the member's own allowance is reached.
    The person is resolved first, because creating the administrator row
    under that lock can deadlock with another request doing the same.
    """
    member = member or administrator_of(workspace)
    with transaction.atomic():
        locked = Workspace.objects.select_for_update().get(pk=workspace.pk)
        active = active_keys(locked)
        replaced_pk = None
        if replacing is not None:
            replaced_pk = (
                active.filter(pk=replacing.pk, member=member).values_list("pk", flat=True).first()
            )
        cap = key_cap(locked)
        if replaced_pk is None and cap is not None and active.count() >= cap:
            raise KeyCapReached()
        if member.key_allowance is not None:
            if replaced_pk is None and active.filter(member=member).count() >= member.key_allowance:
                raise MemberKeyCapReached()
        if replaced_pk is not None:
            APIKey.objects.filter(pk=replaced_pk).update(is_active=False)
        key, raw = APIKey.create_key(locked, name, member=member)
        if device_issued:
            key.device_issued = True
            key.save(update_fields=["device_issued"])
    return key, raw
