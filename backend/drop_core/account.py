"""The account's usage, API keys, files and connected apps, as plain functions.

Each function answers what the JSON endpoint of the same name answers, as an ``Answer``: the HTTP status, the
body and any headers. The REST views in ``views.py`` send an ``Answer`` as a ``Response`` (``errors.answer_response``),
and the dashboard's views call the same functions and draw their pages from the ``Answer``, so a rule lives in one
place: the key cap, the verified-email gate, the keyset order of the file list, what revoking a file does.

Nothing here reads a request or imports Django REST framework. A function takes the signed-in ``user`` and works on
that user's own workspace, grants and files, so a caller cannot name anyone else's. The JSON API has one more kind of
caller, an API key or an OAuth token, which names its workspace itself and has no user: those views pass
``workspace=`` (and the workspace's owner as ``user``). The dashboard never does.

A 204 answer's body is for callers in this process. The REST views send no body with a 204.
"""

import unicodedata
import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from types import MappingProxyType
from typing import Any, Mapping, NamedTuple

from django.conf import settings
from django.db.models import Q
from oauth2_provider.models import get_application_model, get_grant_model

from . import funnel
from .email_verification import is_email_verified
from .key_caps import KeyCapReached, active_key_count, create_key_within_cap
from .models import APIKey, Artifact, UsageCounter, get_or_create_workspace
from .oauth import list_user_oauth_connections, revoke_user_oauth_grants
from .plans import PLANS
from .services import (
    REVOKE_PENDING_DELETE,
    _current_month,
    workspace_storage_bytes,
)
from .services import revoke_artifact as revoke_stored_artifact


class Answer(NamedTuple):
    """What an endpoint answers: ``status``, the JSON-ready ``body`` and ``headers`` (``X-Next-Cursor`` and so on)."""

    status: int
    body: Any
    # Read-only, so that the one empty default cannot be written to by a caller.
    headers: Mapping[str, str] = MappingProxyType({})


def _workspace_of(user, workspace):
    return workspace if workspace is not None else get_or_create_workspace(user)


def artifact_url(artifact):
    """The bearer link to an artifact."""
    return f"{settings.DROP_BASE_URL}/a/{artifact.token}"


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


def usage(user, *, workspace=None):
    """GET /api/v1/usage/: the plan, this month's uploads, keys and storage against their caps, and the account's
    email. An API key or OAuth token names its ``workspace`` and passes the workspace's owner as ``user``."""
    workspace = _workspace_of(user, workspace)
    plan = PLANS[workspace.plan]
    counter = UsageCounter.objects.filter(
        workspace=workspace, year_month=_current_month()
    ).first()
    return Answer(
        200,
        {
            "plan": workspace.plan,
            "uploads_used": counter.uploads if counter else 0,
            "uploads_cap": plan["uploads"],
            "ttl_days": plan["ttl_days"],
            "keys_used": active_key_count(workspace),
            "keys_cap": plan["keys"],
            "storage_used_bytes": workspace_storage_bytes(workspace),
            "storage_cap_bytes": plan["storage_gb"] * 1024**3,
            "email": user.email or "",
            "email_verified": is_email_verified(user),
        },
    )


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


def list_keys(user, *, workspace=None):
    """GET /api/v1/keys/: every key of the workspace, newest first, revoked ones included. Never the key itself."""
    workspace = _workspace_of(user, workspace)
    keys = workspace.api_keys.order_by("-created_at").all()
    return Answer(
        200,
        [
            {
                "id": k.id,
                "name": k.name,
                "key_prefix": k.key_prefix,
                "created_at": k.created_at.isoformat(),
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                "is_active": k.is_active,
            }
            for k in keys
        ],
    )


def key_name_error(name):
    """Validate the optional key name; return an error message or ''."""
    if name is None:
        return ""
    if not isinstance(name, str):
        return "The name must be a string. Send name as text, or leave it out."
    max_length = APIKey._meta.get_field("name").max_length
    if len(name.strip()) > max_length:
        return f"The name is longer than {max_length} characters. Use a shorter name."
    # Postgres text cannot hold NUL (the insert would 500); other control
    # characters have no place in a one-line label either. Surrounding
    # whitespace is stripped before saving, so only the kept part counts.
    if any(unicodedata.category(ch) == "Cc" for ch in name.strip()):
        return "The name contains control characters. Remove them and try again."
    return ""


def verification_required(user):
    """The 403 ``email_verification_required`` answer when the user's email is not verified, else None.

    No verified contact, no keys: the email is the abuse-response path (bans, takedowns), so key issuance waits on
    verification. Existing keys are unaffected, only minting is gated. ``create_key`` makes this check first; the
    JSON endpoint makes it too before it reads the request body, so that an unverified account is told to verify
    whatever its body holds."""
    if is_email_verified(user):
        return None
    return Answer(
        403,
        {
            "error": (
                "Verify your email address before creating API keys. "
                "Check your inbox for the verification link."
            ),
            "code": "email_verification_required",
        },
    )


def create_key(user, name, *, request=None, body_is_object=True):
    """POST /api/v1/keys/: make a key for the user's workspace.

    The answer's 201 body carries ``api_key``, the raw key. It is the only place the raw key exists: it is not stored
    (only its hash is), so the caller must show it once and keep it nowhere. Refusals, in this order: the email is not
    verified (403 ``email_verification_required``), the name is not acceptable (400 ``invalid_request``; the JSON API
    passes ``body_is_object=False`` when its body was not a JSON object), the plan's key cap is reached (402
    ``key_limit_reached``, and no key is made). ``name`` may be None or empty: the key is then called "Untitled key".

    ``request`` is the HTTP request, for the funnel's anonymous id (a cookie): pass it from a view.
    """
    refusal = verification_required(user)
    if refusal is not None:
        return refusal
    name_error = (
        key_name_error(name)
        if body_is_object
        else "The request body must be a JSON object. Send the body as an object, such as one with a name field."
    )
    if name_error:
        return Answer(400, {"error": name_error, "code": "invalid_request"})
    workspace = get_or_create_workspace(user)
    name = (name or "").strip() or "Untitled key"
    # Every active key counts against the plan cap, terminal
    # (device-issued) keys included.
    try:
        key, raw = create_key_within_cap(workspace, name)
    except KeyCapReached:
        return Answer(
            402,
            {
                "error": "Your workspace has reached its plan's API key limit. Revoke a key or upgrade your plan.",
                "code": "key_limit_reached",
            },
        )
    funnel.record(request, funnel.KEY_ISSUED, workspace=workspace)
    # Raw key is returned exactly once; it is never persisted.
    return Answer(
        201,
        {
            "id": key.id,
            "name": key.name,
            "key_prefix": key.key_prefix,
            "api_key": raw,
        },
    )


def revoke_key(user, key_id, *, workspace=None, only_key_id=None):
    """DELETE /api/v1/keys/<id>/: revoke one key of the workspace (204), or 404 for a key that is not its own.

    The row is kept with ``is_active`` False. A caller that is itself an API key passes its own id as ``only_key_id``
    and may revoke only that key (403 ``session_required`` for a sibling): a leaked CI key must not be able to lock
    out the workspace's other agents."""
    workspace = _workspace_of(user, workspace)
    key = APIKey.objects.filter(id=key_id, workspace=workspace).first()
    if key is None:
        return Answer(
            404, {"error": "No API key with that id exists in this workspace. Check the id.", "code": "not_found"}
        )
    if only_key_id is not None and key.pk != only_key_id:
        # An API key may revoke itself (``veilfile logout``, and the CLIs
        # dropping the key a re-login replaced, both authenticate as the
        # key being revoked), never a sibling.
        return Answer(
            403,
            {
                "error": (
                    "An API key can only revoke itself. "
                    "Revoke other keys from the dashboard."
                ),
                "code": "session_required",
            },
        )
    key.is_active = False
    key.save(update_fields=["is_active"])
    return Answer(204, {})


# ---------------------------------------------------------------------------
# Files (artifacts)
# ---------------------------------------------------------------------------

# Optional keyset pagination for GET /api/v1/artifacts (?limit=, ?cursor=).
# Without either parameter the full list is returned exactly as before.
ARTIFACT_PAGE_DEFAULT = 50
ARTIFACT_PAGE_MAX = 200
_EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)


def _artifact_cursor(artifact):
    """Opaque position after ``artifact``: created_at in epoch
    microseconds plus the id as tiebreaker."""
    micros = (artifact.created_at - _EPOCH) // timedelta(microseconds=1)
    return f"{micros}-{artifact.id.hex}"


def _artifact_page(artifacts, raw_limit, raw_cursor):
    """(artifacts, next_cursor), or None when ``raw_limit`` or ``raw_cursor``
    is not usable. Unpaginated requests get the queryset back untouched and no
    cursor."""
    if raw_limit is None and raw_cursor is None:
        return artifacts, None
    try:
        limit = ARTIFACT_PAGE_DEFAULT if raw_limit is None else int(raw_limit)
        artifacts = artifacts.order_by("-created_at", "-id")
        if raw_cursor:
            micros, _sep, id_hex = raw_cursor.partition("-")
            created_at = _EPOCH + timedelta(microseconds=int(micros))
            artifact_id = uuid.UUID(hex=id_hex)
            artifacts = artifacts.filter(
                Q(created_at__lt=created_at)
                | Q(created_at=created_at, id__lt=artifact_id)
            )
    except (TypeError, ValueError, OverflowError):
        return None
    limit = max(1, min(ARTIFACT_PAGE_MAX, limit))
    page = list(artifacts[: limit + 1])
    if len(page) <= limit:
        return page, None
    page = page[:limit]
    return page, _artifact_cursor(page[-1])


def list_artifacts(user, *, cursor=None, limit=None, workspace=None):
    """GET /api/v1/artifacts/: the workspace's files, newest first, expired and held ones included (their link
    answers 404).

    With neither ``limit`` nor ``cursor`` the whole list comes back. With either, a page: ``limit`` (1 to 200,
    default 50) in keyset order, and when more follow, the header ``X-Next-Cursor`` holds the ``cursor`` of the next
    page. ``limit`` and ``cursor`` are the plain values of the query string; bad ones are 400 ``invalid_request``."""
    workspace = _workspace_of(user, workspace)
    artifacts = Artifact.objects.filter(workspace=workspace).order_by("-created_at").all()
    page = _artifact_page(artifacts, limit, cursor)
    if page is None:
        return Answer(
            400,
            {
                "error": "The limit must be a whole number, and the cursor must be a value from the X-Next-Cursor header. Correct the one that is wrong, or leave both out.",
                "code": "invalid_request",
            },
        )
    artifacts, next_cursor = page
    body = [
        {
            "id": str(a.id),
            "filename": a.filename,
            "mime_type": a.mime_type,
            "size_bytes": a.size_bytes,
            "url": artifact_url(a),
            "expires_at": a.expires_at.isoformat(),
            "secret_flags": a.secret_flags,
            "created_at": a.created_at.isoformat(),
        }
        for a in artifacts
    ]
    # The body stays a bare list; the next page is in a header.
    headers = {"X-Next-Cursor": next_cursor} if next_cursor else {}
    return Answer(200, body, headers)


def revoke_artifact(user, artifact_id, *, workspace=None):
    """DELETE /api/v1/artifacts/<id>/: revoke one of the workspace's files. Its link stops working at once and
    its file and row are deleted (204), or 404 for a file that is not the workspace's.

    If the storage delete fails the link is still dead and the row stays, expired, for the sweeper to retry. That is
    a 204 too, as the REST API has always answered it, and the body says ``"storage_deleted": False``."""
    workspace = _workspace_of(user, workspace)
    result = revoke_stored_artifact(workspace, artifact_id)
    if not result:
        return Answer(404, {"error": "No file with that id exists in this workspace. Check the id.", "code": "not_found"})
    return Answer(204, {"storage_deleted": result != REVOKE_PENDING_DELETE})


# ---------------------------------------------------------------------------
# Connected apps (OAuth grants)
# ---------------------------------------------------------------------------


def list_grants(user):
    """GET /api/v1/oauth/grants/: the apps the user has connected over OAuth that can still act, newest first."""
    return Answer(200, list_user_oauth_connections(user))


def disconnect_grant(user, app_id):
    """DELETE /api/v1/oauth/grants/<app_id>/: disconnect one app. The user's tokens and pending codes for it are
    revoked (204); the app's client row is kept, so it can ask for consent again. 404 when the user has nothing
    connected under that id, whoever else may."""
    application = get_application_model().objects.filter(pk=app_id).first()
    revoked = 0
    if application is not None:
        # Pending authorization codes count too: they are deleted, so
        # the app is disconnected even if it never redeemed one.
        pending = get_grant_model().objects.filter(
            user=user, application=application
        ).exists()
        revoked = revoke_user_oauth_grants(user, application=application)
        revoked += int(pending)
    if not revoked:
        return Answer(404, {"error": "No connected app with that id exists for you. Check the id under Connected apps on the dashboard.", "code": "not_found"})
    return Answer(204, {})
