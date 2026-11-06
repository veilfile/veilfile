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
from django.template.loader import render_to_string
from django.utils import timezone

from pages.errors import not_found

from .email_verification import is_email_verified
from .models import Invitation, Membership, Workspace
from .plans import PLANS

logger = logging.getLogger(__name__)

INVITES_PER_HOUR = 10
INVITES_PER_DAY = 30
INVITES_PER_ADDRESS_PER_DAY = 3
MAX_WAITING = 25
SITE_INVITES_PER_DAY = 100
DECLINE_WAIT = timedelta(days=30)


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


def same_address(a, b):
    """Whether two addresses are one, ignoring case and compatibility forms."""
    def fold(value):
        return unicodedata.normalize("NFKC", value).casefold()

    return fold(a) == fold(b)


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


def find_invitation(raw_token):
    """The waiting, unexpired invitation behind a link's token, or None."""
    if not settings.TEAM_SEATS:
        return None
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
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
    if Membership.objects.filter(workspace=workspace, removed_at__isnull=True, user__email__iexact=email).exists():
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
    email = email.strip().lower()
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
