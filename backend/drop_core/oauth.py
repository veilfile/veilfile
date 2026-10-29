"""Phase 2: MCP-spec OAuth authorization server (django-oauth-toolkit).

Veilfile's OAuth credentials are a *parallel* credential to ``vlf_`` API
keys: a DOT access token resolves to the same Workspace principal through
``OAuthCredentialAdapter`` (exposes ``.workspace``, plus ``.is_oauth=True``).
Both auth layers (``drop_core.auth`` and ``drop_mcp``) accept the adapter
anywhere they accept an ``APIKey``.

Design notes:
- Tokens are looked up by their plaintext value in the DOT AccessToken
  table; a token authorizes only when the row exists, is unexpired, and
  carries the ``mcp:use`` scope. Revocation deletes the row, so a revoked
  token can never authorize again.
- The email-verification gate is enforced at the authorize step (same as
  Phase 1's device_approve): an unverified logged-in user gets 403 and no
  grant is created. Token resolution re-checks it (and that the user is
  active), so no token-issuing path can bypass the gate.
- Only the authorization-code grant (plus refresh) exists: DCR refuses
  other grant types, the token endpoint refuses them, and the password and
  implicit grants are off via DOT's RFC 9700 flags (settings).
- Consent-phishing hardening: DCR ``client_name`` is attacker-controlled,
  so the consent page HTML-escapes it, frames the app as an *unverified
  third-party app*, and shows the redirect-URI host prominently.
- Never log tokens, codes, or secrets. No secret material is written to
  files, the message bus, or logs.
"""

import hashlib
import ipaddress
import json
import re
from urllib.parse import urlparse, urlsplit

from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from oauth2_provider.models import (
    get_access_token_model,
    get_application_model,
    get_grant_model,
    get_refresh_token_model,
    refresh_token_expire_timedelta,
)
from oauth2_provider.oauth2_validators import OAuth2Validator
from oauth2_provider.settings import oauth2_settings
from oauth2_provider.views import AuthorizationView, RevokeTokenView, TokenView
from oauth2_provider.views.dynamic_client_registration import (
    DynamicClientRegistrationManagementView,
    DynamicClientRegistrationView,
)

from .email_verification import client_ip, is_email_verified, throttle_ip
from .models import APIKey, get_or_create_workspace

# Single OAuth scope: the agent surface of a vlf_ API key (MCP tools, the
# artifact API, usage). Key management stays dashboard/API-key only.
OAUTH_SCOPE_MCP_USE = "mcp:use"

# The only grant types Veilfile issues tokens through (RFC 7591 names).
ALLOWED_GRANT_TYPES = ("authorization_code", "refresh_token")

# Per-IP throttling on the abuse-relevant OAuth endpoints. DCR mirrors the
# Phase 1 device/code 30/hr pattern; /o/token is bounded but not hostile to
# legit clients.
_DCR_REGISTER_LIMIT_PER_HOUR = 30
_TOKEN_LIMIT_PER_HOUR = 300
# /o/revoke_token/ authenticates the client exactly as /o/token/ does (a
# PBKDF2 check of its secret, about 0.2 s of CPU), so it gets the same budget.
_REVOKE_LIMIT_PER_HOUR = 300


# ---------------------------------------------------------------------------
# Per-IP throttling helper (mirrors Phase 1's device/code pattern)
# ---------------------------------------------------------------------------


def _ip_allowed(request, bucket, limit_per_hour):
    """Per-IP rate limit (an IPv6 client counts by its /64). Returns
    (allowed, retry_after_seconds)."""
    ip = throttle_ip(client_ip(request))
    if not ip:
        return True, 0
    key = f"{bucket}:{ip}"
    count = cache.get(key)
    if count is None:
        cache.set(key, 1, timeout=3600)
        return True, 0
    if count >= limit_per_hour:
        try:
            retry_after = cache.ttl(key)
        except Exception:
            retry_after = None
        return False, retry_after if retry_after else 3600
    try:
        cache.incr(key)
    except ValueError:
        # The counter's hour ended (or the row was culled) between get() and
        # incr(): this request starts the next window. Django caches raise
        # ValueError for a key that is gone; device._bump does the same.
        cache.set(key, 1, timeout=3600)
    return True, 0


def _rate_limited_response(retry_after, oauth_error="rate_limited"):
    response = JsonResponse(
        {
            "error": oauth_error,
            "error_description": (
                "Too many requests came from this network in the past hour. "
                "Wait the number of seconds in the Retry-After header, "
                "then try again."
            ),
        },
        status=429,
    )
    response["Retry-After"] = str(retry_after)
    return response


# ---------------------------------------------------------------------------
# Credential adapter: DOT AccessToken -> workspace principal
# ---------------------------------------------------------------------------


class OAuthCredentialAdapter:
    """Duck-type-compatible credential for an OAuth access token.

    Exposes ``.workspace`` (the token user's workspace), ``.is_oauth=True``,
    ``.pk`` (for per-credential throttling keys), ``.name`` (the client
    name), ``.application`` (the DOT client row, so a token can disconnect
    its own app) and ``.application_id`` (the client's id). ``.api_key`` is
    None: services store the APIKey FK only for real vlf_ keys, and
    artifacts uploaded over OAuth simply have no key row.
    """

    is_oauth = True

    def __init__(self, access_token, workspace):
        self._token = access_token
        self.workspace = workspace

    @property
    def pk(self):
        return f"oauth-token-{self._token.pk}"

    @property
    def application(self):
        return self._token.application

    @property
    def name(self):
        app = self._token.application
        return app.name if app is not None else "OAuth client"

    @property
    def api_key(self):
        return None

    @property
    def application_id(self):
        # The OAuth client this token belongs to (one-time upload URLs
        # minted with it die once the user's grants for it are revoked).
        return self._token.application_id

    @property
    def last_used_at(self):
        # OAuth usage is bounded by token expiry; no per-request writes.
        return None


def resolve_oauth_credential(raw_token):
    """Return an OAuthCredentialAdapter for a valid DOT access token, else None.

    Valid = the row exists, is not expired, the scope includes ``mcp:use``,
    and the token's user is active with a verified email. The verification
    re-check closes any token-issuing path that skipped the consent-page
    gate. The raw token value is never logged.
    """
    if not isinstance(raw_token, str) or not raw_token.strip():
        return None
    access_token_model = get_access_token_model()
    # Look up by the stored checksum (works whether or not the raw token is
    # persisted at rest), falling back to the plaintext column.
    checksum = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
    token = access_token_model.objects.filter(token_checksum=checksum).first()
    if token is None:
        token = access_token_model.objects.filter(token=raw_token.strip()).first()
    if token is None:
        return None
    if token.is_expired():
        return None
    if not token.allow_scopes([OAUTH_SCOPE_MCP_USE]):
        return None
    if token.user is None:
        return None
    if not token.user.is_active or not is_email_verified(token.user):
        return None
    workspace = get_or_create_workspace(token.user)
    return OAuthCredentialAdapter(token, workspace)


class VeilfileOAuth2Validator(OAuth2Validator):
    """DOT's validator (settings OAUTH2_VALIDATOR_CLASS), except that an
    authorization code or refresh token of a deactivated user mints nothing:
    suspending a user (is_active=False) also stops refresh-token rotation,
    with ``invalid_grant``. Nothing is revoked, so reactivating the user
    lets the app refresh again while its refresh token is still live.
    """

    def validate_code(self, client_id, code, client, request, *args, **kwargs):
        valid = super().validate_code(client_id, code, client, request, *args, **kwargs)
        return valid and _user_is_active(request)

    def validate_refresh_token(self, refresh_token, client, request, *args, **kwargs):
        valid = super().validate_refresh_token(
            refresh_token, client, request, *args, **kwargs
        )
        return valid and _user_is_active(request)


def _user_is_active(request):
    user = getattr(request, "user", None)
    return user is not None and user.is_active


# ---------------------------------------------------------------------------
# Dual-credential revocation: one path covers both stores
# ---------------------------------------------------------------------------


def revoke_user_oauth_grants(user, application=None):
    """Revoke every OAuth token/grant for ``user`` (optionally only those
    issued to ``application``). Access tokens are deleted (a deleted row can
    never authorize); refresh tokens are revoked, which also revokes their
    linked access tokens; unexchanged authorization codes are deleted so a
    code minted just before the revoke cannot be redeemed. DCR client rows
    are kept so the client can re-authorize.

    Tokens are matched on the token row's ``user`` (the approver), not the
    application's owner: DCR-registered clients are ownerless by design.
    Returns the number of tokens revoked.
    """
    access_token_model = get_access_token_model()
    refresh_token_model = get_refresh_token_model()
    scope = {"user": user}
    if application is not None:
        scope["application"] = application
    revoked = 0
    for token in access_token_model.objects.filter(**scope):
        token.revoke()
        revoked += 1
    for refresh in refresh_token_model.objects.filter(revoked__isnull=True, **scope):
        refresh.revoke()
        revoked += 1
    get_grant_model().objects.filter(**scope).delete()
    return revoked


def list_user_oauth_connections(user):
    """Apps ``user`` has connected over OAuth that can still act: an
    unexpired ``mcp:use`` access token, or one whose rotating refresh token
    is still live. One row per application, newest activity first.
    """
    access_token_model = get_access_token_model()
    now = timezone.now()
    refresh_window = refresh_token_expire_timedelta()
    connections = {}
    tokens = access_token_model.objects.filter(
        user=user, application__isnull=False
    ).select_related("application")
    for token in tokens:
        if not token.allow_scopes([OAUTH_SCOPE_MCP_USE]):
            continue  # e.g. DCR registration-management tokens
        live = not token.is_expired()
        if not live:
            refresh = getattr(token, "refresh_token", None)
            live = (
                refresh is not None
                and refresh.revoked is None
                and (refresh_window is None or token.expires + refresh_window > now)
            )
        if not live:
            continue
        app = token.application
        row = connections.get(app.pk)
        if row is None or token.created > row["_last"]:
            redirect = (app.redirect_uris or "").split()
            connections[app.pk] = {
                "id": app.pk,
                "name": app.name or "Unnamed app",
                "client_id": app.client_id,
                "redirect_host": urlparse(redirect[0]).netloc if redirect else "",
                "_last": token.created,
            }
    rows = sorted(connections.values(), key=lambda r: r["_last"], reverse=True)
    for row in rows:
        row["last_authorized_at"] = row.pop("_last").isoformat()
    return rows


# ---------------------------------------------------------------------------
# Authorize view: verification gate + phishing-hardened consent UI
# ---------------------------------------------------------------------------


class VeilfileAuthorizationView(AuthorizationView):
    """DOT AuthorizationView with the email-verification gate and the
    Veilfile consent UI (same visual language as the device-flow pages).

    Unverified logged-in users get 403 — no grant, no code, no redirect.

    The consent page names the app that is asking, so it is built only for a
    valid client. A request DOT cannot validate (an unknown client_id, a
    redirect_uri the client did not register, a POST with fields missing)
    gets the error page, never a consent form with no app in it.
    """

    template_name = "oauth/authorize.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not is_email_verified(request.user):
            return self._error_page(
                "Verify your email first",
                (
                    "Authorizing an app requires a verified email address, "
                    "so we can reach you if the grant is ever abused. "
                    "Check your inbox for the verification link, or send a "
                    "fresh one."
                ),
                403,
                show_resend=True,
            )
        return super().dispatch(request, *args, **kwargs)

    def _error_page(self, title, message, status, show_resend=False):
        """The device flow's error page (templates/device/device_error.html), which takes error_title, error_message
        and show_resend. The OAuth gate and the requests DOT refuses are shown on it."""
        return render(
            self.request,
            "device/device_error.html",
            {"error_title": title, "error_message": message, "show_resend": show_resend},
            status=status,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Prominent redirect-URI host: the anti-phishing anchor on the page.
        context["redirect_host"] = urlparse(context.get("redirect_uri") or "").netloc
        return context

    def render_to_response(self, context, **response_kwargs):
        """DOT renders this view's template for three things: the consent page, a fatal client error (no valid
        client_id, or a redirect_uri the client did not register: it must not redirect there) and a form that failed
        validation. The last two have no application, and a consent page without one is a card with no app in it
        and buttons that carry only a token, so they get the error page. The status stays DOT's (400 for the first,
        200 for the second). The text is one fixed sentence for every refused request; nothing of the library's error
        or of the request is shown, because the page must not echo the request."""
        if context.get("application") is None:
            return self._error_page(
                "Invalid request",
                "The request from the app is incomplete or not valid. Nothing was authorized. Go back to the app and start again.",
                response_kwargs.get("status", 200),
            )
        return super().render_to_response(context, **response_kwargs)


# ---------------------------------------------------------------------------
# DCR redirect_uri validation (researcher report 2026-10-06: Mamun Khanka)
#
# DOT's DCR accepts any array of strings as redirect_uris, so an
# unauthenticated registrant could point the consent flow's authorization
# code at an attacker host (http, private IPs, odd schemes). DCR stays open
# and unauthenticated — the MCP spec requires it — but every redirect_uri
# is validated here on POST and on PUT (RFC 7592 management can replace
# redirect_uris too).
# ---------------------------------------------------------------------------

_MAX_REDIRECT_URI_LENGTH = 2048
# Real client metadata is a few hundred bytes. The cap bounds what a request
# makes the server parse, validate and store, before any of that happens.
_MAX_DCR_BODY_BYTES = 16 * 1024
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
# A last label a browser's host parser takes for a number: all digits, or 0x
# and hex digits (the URL standard's "ends in a number" check).
_NUMERIC_LABEL_RE = re.compile(r"^(?:[0-9]+|0x[0-9a-f]*)$")


def _validate_dcr_redirect_uri(uri):
    """Return None when ``uri`` is acceptable, else a human-readable reason.

    Rules: https only, except http for *literal* loopback IPs (RFC 8252
    §7.3 — ``127.0.0.0/8``, ``::1``). Non-loopback private, reserved,
    link-local, multicast, and unspecified IPs are rejected even over https.
    ``localhost`` and other non-IP local names are rejected (DNS rebinding);
    native apps use the literal loopback IP. No userinfo, no fragment.
    """

    def reject(reason):
        return reason

    if not isinstance(uri, str) or not uri:
        return reject("The redirect_uri must be a non-empty string.")
    if len(uri) > _MAX_REDIRECT_URI_LENGTH:
        return reject("The redirect_uri must be 2048 characters or fewer.")
    try:
        uri.encode("ascii")
    except UnicodeEncodeError:
        return reject("The redirect_uri must use ASCII characters only.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in uri):
        return reject("The redirect_uri must not contain control characters.")
    if any(ch.isspace() for ch in uri):
        # DOT stores redirect_uris space-separated: an embedded space would
        # smuggle extra, unvalidated URIs into the stored list.
        return reject("The redirect_uri must not contain spaces or other whitespace.")
    try:
        parts = urlsplit(uri)
    except ValueError:
        return reject(
            "The redirect_uri could not be parsed as a URI. Send a "
            "well-formed URI."
        )
    if parts.scheme not in ("http", "https"):
        return reject("The redirect_uri must use the http or https scheme.")
    if "@" in parts.netloc:
        return reject("The redirect_uri must not include a username or password.")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        return reject("The redirect_uri must include a host.")
    if parts.fragment:
        return reject("The redirect_uri must not include a fragment, the part after #.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        # NB: check loopback first — 127.0.0.1 also reports is_private.
        if ip.is_loopback:
            loopback = True
        elif (
            ip.is_private
            or ip.is_reserved
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return reject(
                "The redirect_uri host must not be a private, reserved, "
                "link-local, multicast or unspecified IP address."
            )
        else:
            loopback = False
    else:
        loopback = False
        lowered = host.lower()
        if (
            lowered == "localhost"
            or lowered.endswith(".localhost")
            or lowered.endswith(".local")
            or "." not in lowered
        ):
            return reject(
                "The redirect_uri host must be a public DNS name, not "
                "localhost, a .localhost name, a .local name or a name "
                "without a dot. A native app uses a literal loopback IP "
                "address instead."
            )
        if _NUMERIC_LABEL_RE.match(lowered.rsplit(".", 1)[-1]):
            # Browsers read a host whose last label is a number as an IPv4
            # address, in shorthand ('127.1', '10.1', '0x7f.1', '0177.0.0.1').
            # ipaddress does not, so these would pass as DNS names and slip
            # past the loopback, private and link-local rules above.
            return reject(
                "The redirect_uri host must be a DNS name or a full IP "
                "address, with IPv4 written as four numbers separated by "
                "dots and IPv6 in brackets."
            )
        if len(lowered) > 253 or not all(
            _DNS_LABEL_RE.match(label) for label in lowered.split(".")
        ):
            return reject(
                "The redirect_uri host is not a valid DNS name. Use at most "
                "253 characters in total, with labels separated by single "
                "dots, each label 1 to 63 characters of letters, digits and "
                "hyphens, and no label starting or ending with a hyphen."
            )
    if parts.scheme == "http" and not loopback:
        return reject(
            "The redirect_uri must use https, except for a literal "
            "loopback IP address such as 127.0.0.1 or [::1]."
        )
    return None


def _has_unstorable_text(value):
    """True if any string in a parsed JSON value (a key or a value) contains
    NUL, or a lone surrogate (JSON's ``"\\ud800"``). Postgres text columns
    take neither: NUL is refused by the database, and a lone surrogate cannot
    be encoded to UTF-8, so the insert would 500. Iterative, so deeply nested
    input cannot exhaust the stack."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if "\x00" in item:
                return True
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                return True
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


def _body_too_large(request):
    """True when a DCR body is over the cap. A declared Content-Length over
    it is enough: the body is not read."""
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        declared = 0
    return declared > _MAX_DCR_BODY_BYTES or len(request.body) > _MAX_DCR_BODY_BYTES


def _dcr_metadata_error_response(request):
    """Validate ``grant_types`` and ``redirect_uris`` in a DCR POST/PUT body,
    refuse a body over 16 KiB, and refuse NUL characters and lone surrogates
    anywhere in it.

    Returns a 400 JsonResponse (RFC 7591 ``invalid_client_metadata`` or
    ``invalid_redirect_uri``) on the first problem, else None. The size is
    checked first, before the body is parsed, and a PUT reaches this before
    its registration token is checked, so an anonymous caller cannot make
    the server work on a large body. Bodies the parent view would reject on
    its own (non-JSON, non-list) are left alone so its canonical errors
    stand.
    """
    if _body_too_large(request):
        return JsonResponse(
            {
                "error": "invalid_client_metadata",
                "error_description": (
                    "The request body is larger than 16 KiB. Send client "
                    "metadata of 16 KiB or less."
                ),
            },
            status=400,
        )
    try:
        data = json.loads(request.body)
    except RecursionError:
        # Nested a few thousand levels deep: well under the size cap, over
        # the parser's stack. The parent view cannot parse it either.
        return JsonResponse(
            {
                "error": "invalid_client_metadata",
                "error_description": (
                    "The request body is nested too deeply to read as JSON. "
                    "Send JSON with fewer levels of nesting."
                ),
            },
            status=400,
        )
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if _has_unstorable_text(data):
        # Postgres text columns cannot hold NUL or a lone surrogate: the
        # insert would 500.
        return JsonResponse(
            {
                "error": "invalid_client_metadata",
                "error_description": (
                    "The client metadata contains a NUL character or an "
                    "unpaired surrogate. Remove it and register again."
                ),
            },
            status=400,
        )
    grant_types = data.get("grant_types")
    if isinstance(grant_types, list):
        for grant_type in grant_types:
            if isinstance(grant_type, str) and grant_type not in ALLOWED_GRANT_TYPES:
                return JsonResponse(
                    {
                        "error": "invalid_client_metadata",
                        "error_description": (
                            f"The grant_type {grant_type[:80]!r} is not "
                            "supported. Use authorization_code, refresh_token "
                            "or both."
                        ),
                    },
                    status=400,
                )
    redirect_uris = data.get("redirect_uris", [])
    if not isinstance(redirect_uris, list):
        return None
    for uri in redirect_uris:
        reason = _validate_dcr_redirect_uri(uri)
        if reason is not None:
            shown = (
                uri
                if isinstance(uri, str) and len(uri) <= 120
                else "<not a usable URI>"
            )
            return JsonResponse(
                {
                    "error": "invalid_redirect_uri",
                    "error_description": f"The redirect_uri {shown!r} was rejected. {reason}",
                },
                status=400,
            )
    return None


# ---------------------------------------------------------------------------
# Throttled DCR + token endpoints
# ---------------------------------------------------------------------------


class ThrottledDynamicClientRegistrationView(DynamicClientRegistrationView):
    """DCR with a per-IP throttle (abuse-relevant: unbounded registration).

    Re-applies ``csrf_exempt``: overriding ``dispatch`` drops the parent's
    class-level exemption.
    """

    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST":
            allowed, retry_after = _ip_allowed(
                request, "oauth-dcr-register", _DCR_REGISTER_LIMIT_PER_HOUR
            )
            if not allowed:
                return _rate_limited_response(retry_after)
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        error = _dcr_metadata_error_response(request)
        if error is not None:
            return error
        return super().post(request, *args, **kwargs)


class ValidatedDCRManagementView(DynamicClientRegistrationManagementView):
    """RFC 7592 management endpoint with DCR's metadata validation.

    PUT can replace redirect_uris and grant_types, so it gets the identical
    validator.
    Only ``put`` is overridden, so the parent's csrf_exempt /
    login_not_required dispatch decorations stay intact.
    """

    def put(self, request, client_id, *args, **kwargs):
        error = _dcr_metadata_error_response(request)
        if error is not None:
            return error
        return super().put(request, client_id, *args, **kwargs)


class ThrottledTokenView(TokenView):
    """Token endpoint with a per-IP throttle that only serves the
    authorization_code and refresh_token grants (RFC 6749
    ``unsupported_grant_type`` for anything else, whatever grant a client
    row happens to be registered with).

    Re-applies ``csrf_exempt``: overriding ``dispatch`` drops the parent's
    class-level exemption.
    """

    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        allowed, retry_after = _ip_allowed(
            request, "oauth-token", _TOKEN_LIMIT_PER_HOUR
        )
        if not allowed:
            return _rate_limited_response(retry_after)
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        grant_type = request.POST.get("grant_type")
        if grant_type and grant_type not in ALLOWED_GRANT_TYPES:
            return JsonResponse(
                {
                    "error": "unsupported_grant_type",
                    "error_description": (
                        "The grant_type is not supported. Use "
                        "authorization_code or refresh_token."
                    ),
                },
                status=400,
            )
        return super().post(request, *args, **kwargs)


class ThrottledRevokeTokenView(RevokeTokenView):
    """RFC 7009 revocation with a per-IP throttle: it checks a confidential
    client's secret with the same password hash as the token endpoint, so an
    unthrottled flood of it costs CPU a request at a time.

    Re-applies ``csrf_exempt``: overriding ``dispatch`` drops the parent's
    class-level exemption.
    """

    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        allowed, retry_after = _ip_allowed(
            request, "oauth-revoke", _REVOKE_LIMIT_PER_HOUR
        )
        if not allowed:
            return _rate_limited_response(retry_after)
        return super().dispatch(request, *args, **kwargs)
