"""Team seats: the switch, and what hangs off it."""
import hashlib
import logging
import secrets
import unicodedata
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.template.loader import render_to_string
from django.utils import timezone

from pages.errors import not_found

from .email_verification import is_email_verified
from .models import (
    FILE_VISIBILITY_ALWAYS_ONLY_ME,
    FILE_VISIBILITY_ALWAYS_TEAM,
    FILE_VISIBILITY_MEMBERS_CHOOSE,
    APIKey,
    Artifact,
    Invitation,
    Membership,
    MemberUsageCounter,
    PendingUpload,
    Workspace,
    current_membership,
    has_other_members,
)
from .oauth import revoke_user_oauth_grants
from .plans import PLANS

logger = logging.getLogger(__name__)

INVITES_PER_HOUR = 10
INVITES_PER_DAY = 30
INVITES_PER_ADDRESS_PER_DAY = 3
MAX_WAITING = 25
SITE_INVITES_PER_DAY = 100
DECLINE_WAIT = timedelta(days=30)
FILE_VISIBILITY_SETTINGS = (FILE_VISIBILITY_MEMBERS_CHOOSE, FILE_VISIBILITY_ALWAYS_TEAM, FILE_VISIBILITY_ALWAYS_ONLY_ME)


def team_seats_only(view):
    """`view`, but the shell's 404 while the switch is off, before anything else runs."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not settings.TEAM_SEATS:
            return not_found(request)
        return view(request, *args, **kwargs)

    return wrapper


class InviteRefused(Exception):
    """An invitation was not sent. `code` names why, and the Members page has words for each."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def fold_address(value):
    """An address with compatibility forms and case folded away, so that one mailbox has one spelling."""
    return unicodedata.normalize("NFKC", value).casefold()


def same_address(a, b):
    """Whether two addresses are one, ignoring case and compatibility forms."""
    return fold_address(a) == fold_address(b)


def seat_limit(workspace):
    """How many people the plan allows on the team, or None for no limit. None as well while the switch is off."""
    if not settings.TEAM_SEATS:
        return None
    return PLANS[workspace.plan]["seats"]


def _open(workspace):
    return Invitation.objects.filter(
        workspace=workspace, accepted_at__isnull=True, declined_at__isnull=True, revoked_at__isnull=True
    )


def seats_in_use(workspace):
    """The people on the team now, plus the invitations still waiting and not expired. 0 while the switch is off."""
    if not settings.TEAM_SEATS:
        return 0
    people = Membership.objects.filter(workspace=workspace, removed_at__isnull=True).count()
    return people + _open(workspace).filter(expires_at__gt=timezone.now()).count()


def _digest(raw_token):
    return hashlib.sha256(raw_token.encode()).hexdigest()


def find_invitation(raw_token):
    """The waiting, unexpired invitation behind a link's token, or None."""
    if not settings.TEAM_SEATS:
        return None
    token_hash = _digest(raw_token)
    return Invitation.objects.filter(
        token_hash=token_hash,
        accepted_at__isnull=True,
        declined_at__isnull=True,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).select_related("workspace").first()


def _still_administrator(member):
    """Re-read the caller's row. Call it under the workspace lock."""
    return Membership.objects.filter(
        pk=member.pk, workspace_id=member.workspace_id, role=Membership.ADMINISTRATOR, removed_at__isnull=True
    ).exists()


def _check_and_create(member, email, token_hash, now):
    """Everything invite decides under the workspace lock. Returns the new row."""
    workspace = Workspace.objects.select_for_update().get(pk=member.workspace_id)
    if not _still_administrator(member):
        raise PermissionDenied
    if workspace.suspended_at is not None:
        raise InviteRefused("workspace_suspended")
    # Close expired rows first, so that the one-waiting constraint and the count agree.
    _open(workspace).filter(expires_at__lte=now).update(revoked_at=now)
    member_emails = Membership.objects.filter(workspace=workspace, removed_at__isnull=True).values_list(
        "user__email", flat=True
    )
    if any(same_address(existing, email) for existing in member_emails):
        raise InviteRefused("already_on_team")
    if _open(workspace).filter(email__iexact=email).exists():
        raise InviteRefused("already_waiting")
    if Invitation.objects.filter(workspace=workspace, email__iexact=email, declined_at__gt=now - DECLINE_WAIT).exists():
        raise InviteRefused("declined_recently")
    if _open(workspace).count() >= MAX_WAITING:
        raise InviteRefused("too_many_waiting")
    team_rows = Invitation.objects.filter(workspace=workspace)
    if (
        team_rows.filter(created_at__gt=now - timedelta(hours=1)).count() >= INVITES_PER_HOUR
        or team_rows.filter(created_at__gt=now - timedelta(days=1)).count() >= INVITES_PER_DAY
    ):
        raise InviteRefused("team_rate_limited")
    since_yesterday = Invitation.objects.filter(created_at__gt=now - timedelta(days=1))
    if since_yesterday.filter(email__iexact=email).count() >= INVITES_PER_ADDRESS_PER_DAY:
        raise InviteRefused("address_rate_limited")
    if since_yesterday.count() >= SITE_INVITES_PER_DAY:
        logger.warning("Invitations are paused: the site-wide limit of %s a day is reached.", SITE_INVITES_PER_DAY)
        raise InviteRefused("site_rate_limited")
    limit = seat_limit(workspace)
    if limit is not None and seats_in_use(workspace) >= limit:
        raise InviteRefused("no_free_seat")
    return Invitation.objects.create(
        workspace=workspace,
        email=email,
        token_hash=token_hash,
        invited_by=member.user,
        expires_at=now + Invitation.TTL,
    )


def invite(member, email, *, build_url):
    """Invite `email` to the administrator's team and mail the link. `build_url` turns the raw token into an absolute URL.

    Raises PermissionDenied for anyone but the administrator, ValidationError for a bad address and InviteRefused
    otherwise. Call it outside a transaction: the mail goes out after the row is committed."""
    if not settings.TEAM_SEATS or not member.is_administrator:
        raise PermissionDenied
    email = fold_address(email.strip())
    if "\n" in email or "\r" in email:
        raise ValidationError("Enter a valid email address.")
    validate_email(email)
    if not is_email_verified(member.user):
        raise InviteRefused("email_verification_required")
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    now = timezone.now()
    try:
        with transaction.atomic():
            invitation = _check_and_create(member, email, token_hash, now)
    except IntegrityError:
        raise InviteRefused("already_waiting") from None
    try:
        expires = invitation.expires_at
        body = render_to_string(
            "registration/invitation_email.txt",
            {
                "inviter": member.user.email,
                "expires": f"{expires:%B} {expires.day}, {expires:%Y} at {expires:%H:%M} UTC",
                "url": build_url(raw),
            },
        )
        send_mail("You are invited to a team on Veilfile", body, settings.DEFAULT_FROM_EMAIL, [email])
    except Exception:
        # The row goes, so the seat and every limit it counted against are free again.
        Invitation.objects.filter(pk=invitation.pk).delete()
        raise InviteRefused("send_failed") from None
    return invitation


def cancel_invitation(member, invitation_id):
    """Close a waiting invitation of the administrator's own team. True when one was closed."""
    if not settings.TEAM_SEATS or not member.is_administrator:
        return False
    with transaction.atomic():
        Workspace.objects.select_for_update().get(pk=member.workspace_id)
        if not _still_administrator(member):
            return False
        return bool(
            Invitation.objects.filter(
                pk=invitation_id,
                workspace_id=member.workspace_id,
                accepted_at__isnull=True,
                declined_at__isnull=True,
                revoked_at__isnull=True,
            ).update(revoked_at=timezone.now())
        )


class JoinRefused(Exception):
    """A person was not joined. `code` is one of why_not_join's, or not_valid for a link that no longer works."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _current_workspace_ids(user):
    """The workspaces where the person has a current row."""
    return list(Membership.objects.filter(user=user, removed_at__isnull=True).values_list("workspace_id", flat=True))


def why_not_join(user, invitation):
    """The code that would refuse `user` joining the invitation's team, or None. It writes nothing.

    The codes are checked in this order: wrong_person, email_verification_required, on_this_team, on_another_team,
    administers_people, account_suspended, has_paid_plan, no_free_seat."""
    if not settings.TEAM_SEATS:
        return None
    if not same_address(user.email, invitation.email):
        return "wrong_person"
    if not is_email_verified(user) or not same_address(user.email_verification.email, invitation.email):
        return "email_verification_required"
    rows = list(Membership.objects.filter(user=user, removed_at__isnull=True).select_related("workspace"))
    if any(row.workspace_id == invitation.workspace_id for row in rows):
        return "on_this_team"
    if any(row.role == Membership.MEMBER for row in rows):
        return "on_another_team"
    administered = [row.workspace for row in rows]
    if any(has_other_members(workspace) for workspace in administered):
        return "administers_people"
    if any(workspace.suspended_at is not None for workspace in administered):
        return "account_suspended"
    if any(workspace.plan != Workspace.PLAN_FREE or workspace.stripe_subscription_id for workspace in administered):
        return "has_paid_plan"
    limit = seat_limit(invitation.workspace)
    # The invitation's own hold becomes this person's seat, so only the people count.
    if limit is not None and Membership.objects.filter(workspace=invitation.workspace, removed_at__isnull=True).count() >= limit:
        return "no_free_seat"
    return None


def _close_administrator_row(row, now):
    """Stop what a workspace of the person's own made, then close the row. Call it under that workspace's lock."""
    APIKey.objects.filter(member=row, is_active=True).update(is_active=False)
    PendingUpload.objects.filter(member=row, used=False).update(used=True)
    _open(row.workspace).update(revoked_at=now)
    Membership.objects.filter(pk=row.pk).update(removed_at=now)


def _join_once(user, digest, team_id):
    """One attempt at the join. Returns the person's row, or None when the person's rows changed before the locks were taken."""
    now = timezone.now()
    with transaction.atomic():
        ids = sorted({team_id, *_current_workspace_ids(user)})
        locked = {w.pk: w for w in Workspace.objects.select_for_update().filter(pk__in=ids).order_by("pk")}
        if not set(_current_workspace_ids(user)) <= set(locked):
            return None
        team = locked[team_id]
        invitation = _open(team).select_for_update().filter(token_hash=digest, expires_at__gt=now).first()
        if invitation is None or team.suspended_at is not None:
            raise JoinRefused("not_valid")
        invitation.workspace = team
        code = why_not_join(user, invitation)
        if code is not None:
            raise JoinRefused(code)
        for row in Membership.objects.filter(user=user, removed_at__isnull=True, role=Membership.ADMINISTRATOR).select_related("workspace"):
            row.workspace = locked[row.workspace_id]
            _close_administrator_row(row, now)
        row = Membership.objects.filter(workspace=team, user=user).first()
        if row is None:
            row = Membership.objects.create(workspace=team, user=user, role=Membership.MEMBER)
        else:
            APIKey.objects.filter(member=row, is_active=True).update(is_active=False)
            Membership.objects.filter(pk=row.pk).update(
                role=Membership.MEMBER, removed_at=None, upload_allowance=None, storage_allowance_bytes=None, key_allowance=None
            )
            row.refresh_from_db()
        Invitation.objects.filter(pk=invitation.pk).update(accepted_at=now)
        revoke_user_oauth_grants(user)
        return row


def accept_invitation(user, raw_token):
    """Move `user` onto the invitation's team, and stop what their own account made. Returns the new Membership.

    Raises JoinRefused. The workspace rows of the team and of the person's own places are locked in primary-key order, and
    the person's rows are read again under the locks."""
    if not settings.TEAM_SEATS:
        raise JoinRefused("not_valid")
    current_membership(user)  # before any lock: it can write
    digest = _digest(raw_token)
    team_id = Invitation.objects.filter(token_hash=digest).values_list("workspace_id", flat=True).first()
    if team_id is None:
        raise JoinRefused("not_valid")
    try:
        for _ in range(3):
            row = _join_once(user, digest, team_id)
            if row is not None:
                return row
    except IntegrityError:
        # membership_one_team: the person joined somewhere else in the meantime.
        raise JoinRefused("on_another_team") from None
    raise JoinRefused("on_another_team")


def decline_invitation(raw_token):
    """Close a waiting invitation as declined, on the strength of the link alone. True when one was closed."""
    if not settings.TEAM_SEATS:
        return False
    digest = _digest(raw_token)
    team_id = Invitation.objects.filter(token_hash=digest).values_list("workspace_id", flat=True).first()
    if team_id is None:
        return False
    with transaction.atomic():
        Workspace.objects.select_for_update().get(pk=team_id)
        return bool(
            Invitation.objects.filter(
                token_hash=digest, accepted_at__isnull=True, declined_at__isnull=True, revoked_at__isnull=True
            ).update(declined_at=timezone.now())
        )


def has_team_features(workspace):
    """Whether the team has the Members page: the switch is on, and the plan allows more than one person (or has no limit on
    them), or someone else is on the team, or an invitation is waiting. False before any query while the switch is off."""
    if not settings.TEAM_SEATS:
        return False
    limit = seat_limit(workspace)
    if limit is None or limit > 1:
        return True
    return has_other_members(workspace) or _open(workspace).filter(expires_at__gt=timezone.now()).exists()


def waiting_invitations(workspace):
    """The invitations still waiting and not expired, oldest first. Empty while the switch is off."""
    if not settings.TEAM_SEATS:
        return []
    return list(_open(workspace).filter(expires_at__gt=timezone.now()).order_by("created_at", "pk"))


def member_rows(workspace):
    """One dict for each current person on the team, the administrator first: the person's row and role, and what they use against
    what they are allowed. Each figure is one query across the team and never one per person, and none reads a file's name.
    Empty while the switch is off."""
    if not settings.TEAM_SEATS:
        return []
    now = timezone.now()
    people = list(
        Membership.objects.filter(workspace=workspace, removed_at__isnull=True)
        .select_related("user")
        .order_by("role", "created_at", "pk")
    )
    ids = [person.pk for person in people]
    uploads = dict(
        MemberUsageCounter.objects.filter(member_id__in=ids, year_month=f"{now:%Y-%m}").values_list("member_id", "uploads")
    )
    # A sum of the live files' sizes for each person: no file's name is read.
    storage = dict(
        Artifact.objects.filter(workspace=workspace, member_id__in=ids, expires_at__gt=now)
        .values("member_id")
        .annotate(total=Sum("size_bytes"))
        .values_list("member_id", "total")
    )
    keys = dict(
        APIKey.objects.filter(workspace=workspace, member_id__in=ids, is_active=True)
        .values("member_id")
        .annotate(total=Count("pk"))
        .values_list("member_id", "total")
    )
    return [
        {
            "person": person,
            "role": person.role,
            "uploads_used": uploads.get(person.pk, 0),
            "upload_allowance": person.upload_allowance,
            "storage_used": storage.get(person.pk) or 0,
            "storage_allowance": person.storage_allowance_bytes,
            "keys_used": keys.get(person.pk, 0),
            "key_allowance": person.key_allowance,
        }
        for person in people
    ]


def set_allowances(administrator, member_id, *, uploads, storage_bytes, keys):
    """Set (or, with None, clear) what one current person of the administrator's team may use. True when a person was changed."""
    if not settings.TEAM_SEATS or not administrator.is_administrator:
        return False
    with transaction.atomic():
        Workspace.objects.select_for_update().get(pk=administrator.workspace_id)
        if not _still_administrator(administrator):
            return False
        return bool(
            Membership.objects.filter(pk=member_id, workspace_id=administrator.workspace_id, removed_at__isnull=True).update(
                upload_allowance=uploads, storage_allowance_bytes=storage_bytes, key_allowance=keys
            )
        )


def set_file_visibility_setting(administrator, value):
    """Choose who can see the team's new files. Files that already exist keep the visibility they have. True when it was saved."""
    if not settings.TEAM_SEATS or not administrator.is_administrator or value not in FILE_VISIBILITY_SETTINGS:
        return False
    with transaction.atomic():
        Workspace.objects.select_for_update().get(pk=administrator.workspace_id)
        if not _still_administrator(administrator):
            return False
        Workspace.objects.filter(pk=administrator.workspace_id).update(file_visibility=value)
        return True
