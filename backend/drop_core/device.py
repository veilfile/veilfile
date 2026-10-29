"""RFC 8628-style device authorization flow for ``veilfile login``.

The terminal is unauthenticated, so the flow is:

1. Terminal: POST /api/v1/device/code -> device_code, user_code, verification URIs.
2. User: opens the verification URI in a browser, logs in, approves.
3. Terminal: POST /api/v1/device/token (polls) -> the vlf_ API key once approved.

The issued credential is a regular ``vlf_`` API key (drop_core.models.APIKey)
flagged ``device_issued=True``; it counts toward the plan's key cap like any
other key (drop_core.key_caps). Only the sha256 of the secret device_code is
stored.

Approval only records the decision: the workspace, the approver, and, when
the workspace is at its key cap, the existing key to replace. The key itself
is minted by the first poll after approval, in one transaction that flips the
grant approved -> consumed, revokes the replaced key, and creates the new
one; the raw key goes out in that poll's response and is never written
anywhere (not the database, not a cache). So concurrent polls cannot mint two
keys, an approval that is never polled never creates a key, and any worker or
instance can answer any request. Device/user codes are never logged.
"""

import hashlib
import ipaddress
import logging
import secrets as stdlib_secrets
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import (
    RequestDataTooBig,
    TooManyFieldsSent,
    TooManyFilesSent,
)
from django.db import transaction
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from rest_framework import status
from rest_framework.exceptions import ParseError, UnsupportedMediaType
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import funnel
from .email_verification import client_ip, is_email_verified
from .key_caps import (
    KeyCapReached,
    active_keys,
    create_key_within_cap,
    key_cap,
    needs_replacement,
)
from .models import APIKey, DeviceCode, get_or_create_workspace

logger = logging.getLogger(__name__)

# User codes use an unambiguous alphabet: no I, L, O, 0, or 1.
_USER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_USER_CODE_LENGTH = 8
_DEVICE_CODE_TTL_SECONDS = 600  # 10 minutes
_POLL_INTERVAL_SECONDS = 5
# RFC 8628 §3.5: each slow_down adds 5 seconds to the polling interval.
_SLOW_DOWN_STEP_SECONDS = 5
_MAX_POLL_INTERVAL_SECONDS = 60
# An approved grant can still be redeemed briefly after it expires: the
# terminal's last poll may land just after the 10-minute deadline.
_APPROVED_GRACE_SECONDS = 60

_DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
_KEY_NAME = "Terminal login"

# Rate limit: device-code issuance per IP (per /64 for IPv6) per hour.
_CODE_ISSUE_LIMIT_PER_HOUR = 30
# Backstop for the per-IP limit: at most this many live pending grants
# site-wide. Real traffic is a handful at a time; the ceiling sits high so
# that reaching it (and locking out every login) takes hundreds of
# addresses, while the short retention below still bounds the table.
_MAX_LIVE_PENDING_CODES = 10000

# Attempt limiting on the browser pages: user codes that don't match a live
# grant, per signed-in user and per IP, within a sliding-ish window.
_LOOKUP_WINDOW_SECONDS = 600
_LOOKUP_FAILURES_PER_USER = 10
_LOOKUP_FAILURES_PER_IP = 30

# Grants that issued a key are kept a day after expiry (troubleshooting);
# the rest, which is all a flood of unapproved requests can create, an hour.
_DEVICE_CODE_RETENTION = timedelta(days=1)
_UNUSED_DEVICE_CODE_RETENTION = timedelta(hours=1)
_PURGE_INTERVAL_SECONDS = 600
# replace_key is an APIKey primary key (bigint); 18 digits always fits.
_MAX_KEY_ID_DIGITS = 18


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_user_code():
    """An 8-char code from the unambiguous alphabet, retrying on collision."""
    for _ in range(10):
        code = "".join(stdlib_secrets.choice(_USER_CODE_ALPHABET) for _ in range(_USER_CODE_LENGTH))
        if not DeviceCode.objects.filter(user_code=code).exists():
            return code
    # Astronomically unlikely (32^8 space); fall back to a longer retry.
    raise RuntimeError("could not mint a unique device user code")


def _format_user_code(code):
    """Format the stored 8-char code as XXXX-XXXX for display."""
    code = (code or "").upper()
    return f"{code[:4]}-{code[4:8]}" if len(code) == _USER_CODE_LENGTH else code


def _normalize_user_code(raw):
    """Accept XXXX-XXXX, xxxx-xxxx, or xxxxxxxx with stray whitespace."""
    return "".join((raw or "").upper().split()).replace("-", "")


def _hash_device_code(device_code):
    # surrogatepass: a JSON body can carry a lone surrogate ("\ud800"),
    # which strict UTF-8 refuses. Such a code simply matches no grant.
    return hashlib.sha256(device_code.encode("utf-8", "surrogatepass")).hexdigest()


def _issue_device_code(requester_ip="", requester_user_agent=""):
    """Mint a DeviceCode row. Returns (instance, raw_device_code, formatted user_code)."""
    raw_device_code = stdlib_secrets.token_urlsafe(32)
    instance = DeviceCode.objects.create(
        device_code_hash=_hash_device_code(raw_device_code),
        user_code=_new_user_code(),
        expires_at=timezone.now() + timedelta(seconds=_DEVICE_CODE_TTL_SECONDS),
        interval_seconds=_POLL_INTERVAL_SECONDS,
        requester_ip=(requester_ip or "")[:64],
        requester_user_agent=(requester_user_agent or "")[:256],
    )
    return instance, raw_device_code, _format_user_code(instance.user_code)


def _verification_uri(request):
    return request.build_absolute_uri(reverse("device"))


def _ip_bucket(ip):
    """The rate-limit bucket for a client IP: the address for IPv4, its
    /64 for IPv6, since a single subscriber usually holds a whole /64."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if address.version == 6:
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ipaddress.IPv6Network((int(address) >> 64 << 64, 64)))
    return str(address)


def _bump(key, window):
    """Increment a cache counter that lives ``window`` seconds from its
    first hit. Returns the new count."""
    cache.add(key, 0, timeout=window)
    try:
        return cache.incr(key)
    except ValueError:
        # The counter expired between add() and incr().
        cache.set(key, 1, timeout=window)
        return 1


def _code_issue_allowed(ip):
    """Per-IP rate limit on device-code issuance. Returns (allowed, retry_after)."""
    if not ip:
        return True, 0
    key = f"device-code-rate:{_ip_bucket(ip)}"
    if _bump(key, 3600) > _CODE_ISSUE_LIMIT_PER_HOUR:
        try:
            retry_after = cache.ttl(key)
        except Exception:
            retry_after = None
        return False, retry_after if retry_after else 3600
    return True, 0


def _too_many_pending():
    return (
        DeviceCode.objects.filter(
            status=DeviceCode.STATUS_PENDING, expires_at__gt=timezone.now()
        ).count()
        >= _MAX_LIVE_PENDING_CODES
    )


def purge_stale_device_codes():
    """Delete device grants an hour after they expire, or a day after for
    grants that issued a key. Returns the number of rows deleted.

    Runs opportunistically on issuance; also safe to call from the regular
    sweep. Deleting a row never touches the API key it issued.
    """
    now = timezone.now()
    deleted, _ = DeviceCode.objects.filter(
        Q(expires_at__lt=now - _DEVICE_CODE_RETENTION)
        | Q(expires_at__lt=now - _UNUSED_DEVICE_CODE_RETENTION, api_key__isnull=True)
    ).delete()
    return deleted


def _maybe_purge():
    """Purge at most once per interval. Never raises."""
    try:
        if cache.add("device-code-purge", 1, timeout=_PURGE_INTERVAL_SECONDS):
            purge_stale_device_codes()
    except Exception:
        logger.exception("device: purging stale device codes failed")


def _lookup_counter_keys(request):
    keys = [(f"device-lookup-fail:user:{request.user.pk}", _LOOKUP_FAILURES_PER_USER)]
    ip = client_ip(request)
    if ip:
        keys.append((f"device-lookup-fail:ip:{_ip_bucket(ip)}", _LOOKUP_FAILURES_PER_IP))
    return keys


def _lookup_blocked(request):
    """True once this user or IP has entered too many codes that matched no
    live grant. Checked before every lookup, so a blocked caller learns
    nothing about which codes exist."""
    return any(
        (cache.get(key) or 0) >= limit for key, limit in _lookup_counter_keys(request)
    )


def _record_lookup_failure(request):
    for key, _ in _lookup_counter_keys(request):
        _bump(key, _LOOKUP_WINDOW_SECONDS)


def _find_pending(raw_user_code):
    """Return (DeviceCode, None) for a live pending grant, else (None, problem).

    ``problem`` is "invalid" (unknown or already resolved code) or
    "expired". Expired rows are lazily marked expired here.
    """
    normalized = _normalize_user_code(raw_user_code)
    # Only well-formed codes reach the database (a NUL byte, for one,
    # makes Postgres raise instead of finding nothing).
    if len(normalized) != _USER_CODE_LENGTH or not set(normalized) <= set(
        _USER_CODE_ALPHABET
    ):
        return None, "invalid"
    try:
        device_code = DeviceCode.objects.get(user_code=normalized)
    except DeviceCode.DoesNotExist:
        return None, "invalid"
    if device_code.is_expired():
        if device_code.status == DeviceCode.STATUS_PENDING:
            device_code.mark_expired()
        return None, "expired"
    if device_code.status != DeviceCode.STATUS_PENDING:
        return None, "invalid"
    return device_code, None


def _error_page(request, title, message, show_resend=False, http_status=400):
    return render(
        request,
        "device/device_error.html",
        {
            "error_title": title,
            "error_message": message,
            "show_resend": show_resend,
        },
        status=http_status,
    )


def _too_many_attempts_page(request):
    return _error_page(
        request,
        "Too many attempts",
        "Too many codes didn't match a sign-in request. Wait 10 minutes, "
        "then enter the code shown in your terminal.",
        http_status=429,
    )


def _lookup_problem_page(request, problem):
    """Error page for a code that matched no live grant on approve/deny."""
    if problem == "expired":
        return _error_page(
            request,
            "This code has expired",
            "Device codes last 10 minutes. Run veilfile login again to get a fresh code.",
            http_status=410,
        )
    return _error_page(
        request,
        "Invalid code",
        "We couldn't find that device code. Check the terminal and try again.",
        http_status=400,
    )


def _render_consent(request, device_code, error=None, selected_key="", http_status=200):
    workspace = get_or_create_workspace(request.user)
    replacement_required = needs_replacement(workspace)
    return render(
        request,
        "device/device_consent.html",
        {
            "device_code": device_code,
            "account": request.user.email or request.user.get_username(),
            "key_cap": key_cap(workspace),
            "replacement_required": replacement_required,
            "replaceable_keys": (
                list(active_keys(workspace).order_by("created_at"))
                if replacement_required
                else []
            ),
            "selected_key": selected_key,
            "error": error,
            # The same sentence as a list, for components/form_errors.html (it draws nothing for an empty one).
            "errors": [error] if error else [],
            # The approval POST still enforces the email gate (403); the
            # consent page shows the notice early so it never surprises.
            "email_verified": is_email_verified(request.user),
        },
        status=http_status,
    )


# ---------------------------------------------------------------------------
# API: issuance + token polling (unauthenticated)
# ---------------------------------------------------------------------------


class DeviceCodeView(APIView):
    """POST /api/v1/device/code — start a device authorization grant."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        ip = client_ip(request)
        allowed, retry_after = _code_issue_allowed(ip)
        if not allowed:
            response = Response(
                {
                    "error": (
                        "Too many sign-ins were started from this network in the "
                        "past hour. Wait the number of seconds in the "
                        "Retry-After header, then run veilfile login again."
                    ),
                    "code": "rate_limited",
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            response["Retry-After"] = str(retry_after)
            return response
        _maybe_purge()
        if _too_many_pending():
            response = Response(
                {
                    "error": (
                        "Too many sign-ins are in progress. Run veilfile login "
                        "again in a minute."
                    ),
                    "code": "rate_limited",
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            response["Retry-After"] = "60"
            return response
        device_code, raw_device_code, user_code = _issue_device_code(
            requester_ip=ip,
            requester_user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        verification_uri = _verification_uri(request)
        return Response(
            {
                "device_code": raw_device_code,
                "user_code": user_code,
                "verification_uri": verification_uri,
                "verification_uri_complete": f"{verification_uri}?user_code={user_code}",
                "expires_in": _DEVICE_CODE_TTL_SECONDS,
                "interval": _POLL_INTERVAL_SECONDS,
            },
            status=status.HTTP_200_OK,
        )


def _token_error(error, description=None):
    body = {"error": error}
    if description:
        body["error_description"] = description
    return Response(body, status=status.HTTP_400_BAD_REQUEST)


# What a poll that lost the redemption race reports, by the grant's status.
_LOST_RACE_ERRORS = {
    DeviceCode.STATUS_DENIED: "access_denied",
    DeviceCode.STATUS_EXPIRED: "expired_token",
}


def _redeem(device_code):
    """Mint the terminal's key for an approved grant and return the token
    response. The approved -> consumed flip, the cap check, the replaced
    key's revocation and the new key are one transaction."""
    refused = None
    with transaction.atomic():
        claimed = DeviceCode.objects.filter(
            pk=device_code.pk, status=DeviceCode.STATUS_APPROVED
        ).update(status=DeviceCode.STATUS_CONSUMED)
        if not claimed:
            # Another poll redeemed it first, or it was resolved meanwhile.
            current = (
                DeviceCode.objects.filter(pk=device_code.pk)
                .values_list("status", flat=True)
                .first()
            )
            return _token_error(_LOST_RACE_ERRORS.get(current, "invalid_grant"))
        # Deploy overlap: the previous release minted the key at approval
        # and held the raw value in its own process cache, so that key can
        # never be delivered here. Revoke it before minting the real one.
        legacy_key_id = (
            DeviceCode.objects.filter(pk=device_code.pk)
            .values_list("api_key_id", flat=True)
            .first()
        )
        if legacy_key_id is not None:
            APIKey.objects.filter(pk=legacy_key_id).update(is_active=False)
        approver = device_code.approved_by
        if approver is None or not approver.is_active:
            refused = (
                "The account that approved this login is no longer active. "
                "Run veilfile login again and approve it from an active "
                "account."
            )
        else:
            try:
                key, raw_key = create_key_within_cap(
                    device_code.workspace,
                    _KEY_NAME,
                    device_issued=True,
                    replacing=device_code.replace_key,
                )
            except KeyCapReached:
                # The plan's cap changed (or another key was created) after
                # approval. Nothing is minted; the user approves again.
                refused = (
                    "Your workspace has reached its plan's API key limit. Run "
                    "veilfile login again and choose a key to replace, or "
                    "revoke a key in the dashboard."
                )
        if refused:
            DeviceCode.objects.filter(pk=device_code.pk).update(
                status=DeviceCode.STATUS_DENIED
            )
        else:
            DeviceCode.objects.filter(pk=device_code.pk).update(api_key=key)
    if refused:
        return _token_error("access_denied", refused)
    funnel.record_workspace_event(device_code.workspace, funnel.KEY_ISSUED)
    # The raw key leaves in this response only; it is never persisted.
    return Response({"api_key": raw_key}, status=status.HTTP_200_OK)


class DeviceTokenView(APIView):
    """POST /api/v1/device/token — poll for the approved API key.

    Errors follow RFC 8628 (HTTP 400, ``{"error": ...}``). Accepts a JSON
    object or a form-encoded body; anything else is ``invalid_request``.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        try:
            data = request.data
        except (
            ParseError,
            UnsupportedMediaType,
            # Django's own body limits (too many fields, too large, too
            # many files) would otherwise answer with its HTML 400.
            RequestDataTooBig,
            TooManyFieldsSent,
            TooManyFilesSent,
        ):
            return _token_error("invalid_request")
        # JSON arrays, strings and numbers parse fine but carry no fields.
        if not isinstance(data, dict):
            return _token_error("invalid_request")
        grant_type = data.get("grant_type")
        raw_device_code = data.get("device_code")
        if not isinstance(grant_type, str) or not grant_type:
            return _token_error("invalid_request")
        if grant_type != _DEVICE_GRANT_TYPE:
            return _token_error("unsupported_grant_type")
        if not isinstance(raw_device_code, str) or not raw_device_code:
            return _token_error("invalid_request")
        try:
            device_code = DeviceCode.objects.select_related(
                "workspace", "approved_by", "replace_key"
            ).get(device_code_hash=_hash_device_code(raw_device_code))
        except DeviceCode.DoesNotExist:
            return _token_error("invalid_grant")

        if device_code.status == DeviceCode.STATUS_APPROVED:
            grace_ends = device_code.expires_at + timedelta(
                seconds=_APPROVED_GRACE_SECONDS
            )
            if timezone.now() > grace_ends:
                # Approved but never collected: nothing was minted.
                device_code.mark_expired()
                return _token_error("expired_token")
            return _redeem(device_code)

        # Lazily expire past-due grants.
        if device_code.is_expired():
            if device_code.status == DeviceCode.STATUS_PENDING:
                device_code.mark_expired()
            return _token_error("expired_token")

        if device_code.status == DeviceCode.STATUS_CONSUMED:
            return _token_error("invalid_grant")
        if device_code.status == DeviceCode.STATUS_DENIED:
            return _token_error("access_denied")
        if device_code.status == DeviceCode.STATUS_EXPIRED:
            return _token_error("expired_token")

        # Pending: enforce the poll interval (slow_down), else authorization_pending.
        now = timezone.now()
        if (
            device_code.last_polled_at is not None
            and now - device_code.last_polled_at
            < timedelta(seconds=device_code.interval_seconds)
        ):
            # RFC 8628 §3.5: the interval grows for this and later polls,
            # and a too-fast poll still counts as a poll.
            DeviceCode.objects.filter(pk=device_code.pk).update(
                last_polled_at=now,
                interval_seconds=min(
                    device_code.interval_seconds + _SLOW_DOWN_STEP_SECONDS,
                    _MAX_POLL_INTERVAL_SECONDS,
                ),
            )
            return _token_error("slow_down")
        DeviceCode.objects.filter(pk=device_code.pk).update(last_polled_at=now)
        return _token_error("authorization_pending")


# ---------------------------------------------------------------------------
# Browser: consent pages (login required)
# ---------------------------------------------------------------------------


@login_required
def device_page(request):
    """GET /device — consent page for a user code, or the code-entry form."""
    raw_code = request.GET.get("user_code", "").strip()
    if not raw_code:
        return render(
            request,
            "device/device_enter_code.html",
            {"email_verified": is_email_verified(request.user)},
        )
    if _lookup_blocked(request):
        return _too_many_attempts_page(request)
    device_code, problem = _find_pending(raw_code)
    if problem:
        _record_lookup_failure(request)
    if problem == "expired":
        return _error_page(
            request,
            "This code has expired",
            "Device codes last 10 minutes. Run veilfile login again to get a fresh code.",
            http_status=410,
        )
    if problem == "invalid":
        error = "We couldn't find that code."
        return render(
            request,
            "device/device_enter_code.html",
            {
                "error": error,
                # The same sentence as a list: components/field.html takes its errors as one.
                "errors": [error],
                "user_code": raw_code,
                "email_verified": is_email_verified(request.user),
            },
        )
    return _render_consent(request, device_code)


@login_required
@require_POST
def device_approve(request):
    """POST /device/approve — record the approval; the terminal's next poll
    mints the key."""
    if _lookup_blocked(request):
        return _too_many_attempts_page(request)
    raw_code = request.POST.get("user_code", "")
    device_code, problem = _find_pending(raw_code)
    if problem:
        _record_lookup_failure(request)
        return _lookup_problem_page(request, problem)
    # NON-NEGOTIABLE: email verification gates key issuance. A logged-in
    # session alone is not enough — the email is the abuse-response path.
    if not is_email_verified(request.user):
        return _error_page(
            request,
            "Verify your email first",
            "Approving a terminal key requires a verified email address, "
            "so we can reach you if the key is ever abused. Check your "
            "inbox for the verification link, or send a fresh one.",
            show_resend=True,
            http_status=403,
        )
    workspace = get_or_create_workspace(request.user)
    raw_replace = (request.POST.get("replace_key") or "").strip()
    # Anti-phishing: approval from a link someone sent you must not be a
    # single click. The user attests they started this login themselves.
    if request.POST.get("confirm") != "yes":
        return _render_consent(
            request,
            device_code,
            error=(
                "Confirm that you ran veilfile login yourself and that the "
                "code matches your terminal."
            ),
            selected_key=raw_replace,
            http_status=400,
        )
    replace_key = None
    if raw_replace:
        # isascii(): str.isdigit() also accepts "²", which int() rejects.
        if (
            raw_replace.isascii()
            and raw_replace.isdigit()
            and len(raw_replace) <= _MAX_KEY_ID_DIGITS
        ):
            replace_key = active_keys(workspace).filter(pk=int(raw_replace)).first()
        if replace_key is None:
            return _render_consent(
                request,
                device_code,
                error="That key is no longer active in this workspace. Choose another.",
                http_status=400,
            )
    elif needs_replacement(workspace):
        return _render_consent(
            request,
            device_code,
            error="Your plan's API key limit is reached. Choose a key to replace.",
            http_status=400,
        )
    # Atomic claim: a double-submit, or a deny racing this approval,
    # resolves the grant exactly once.
    claimed = DeviceCode.objects.filter(
        pk=device_code.pk,
        status=DeviceCode.STATUS_PENDING,
        expires_at__gt=timezone.now(),
    ).update(
        workspace=workspace,
        approved_by=request.user,
        replace_key=replace_key,
        status=DeviceCode.STATUS_APPROVED,
    )
    if not claimed:
        return _error_page(
            request,
            "Already handled",
            "This device code was already approved or denied.",
            http_status=400,
        )
    return render(request, "device/device_success.html", {"replace_key": replace_key})


@login_required
@require_POST
def device_deny(request):
    """POST /device/deny — refuse the terminal's authorization request."""
    if _lookup_blocked(request):
        return _too_many_attempts_page(request)
    raw_code = request.POST.get("user_code", "")
    device_code, problem = _find_pending(raw_code)
    if problem:
        _record_lookup_failure(request)
        return _lookup_problem_page(request, problem)
    denied = DeviceCode.objects.filter(
        pk=device_code.pk, status=DeviceCode.STATUS_PENDING
    ).update(status=DeviceCode.STATUS_DENIED)
    if not denied:
        return _error_page(
            request,
            "Already handled",
            "This device code was already approved or denied.",
            http_status=400,
        )
    return render(request, "device/device_denied.html", {})
