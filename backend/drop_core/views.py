"""Veilfile views: agent API (API-key auth), key management (session auth),
public artifact serving, and signup."""

import io
import ipaddress
import logging
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.files.uploadhandler import FileUploadHandler
from django.http import FileResponse, Http404, HttpResponse, UnreadablePostError
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.http import content_disposition_header, url_has_allowed_host_and_scheme
from oauth2_provider.models import (
    get_access_token_model,
    get_refresh_token_model,
)
from rest_framework.authentication import SessionAuthentication
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from pages.errors import artifact_miss

from .auth import (
    APIKeyAuthentication,
    IsAPIKeyAuthenticated,
    IsAPIKeyOrSessionAuthenticated,
)
from .errors import answer_response
from .oauth import revoke_user_oauth_grants
from . import account
from . import funnel
from . import throttle
from .email_verification import (
    client_ip,
    email_digest,
    get_or_create_verification,
    is_email_verified,
    parse_verification_token,
    password_fingerprint,
    resend_allowed,
    send_email_change_notice,
    send_verification_email,
    signup_attempt_allowed,
)
from .forms import EmailUpdateForm, SignupForm
from .models import (
    APIKey,
    Artifact,
    EmailSendLog,
    EmailVerification,
    PendingUpload,
    get_or_create_workspace,
)
from .plans import PLANS
from .services import (
    MAX_FILE_SIZE,
    RejectedUpload,
    StorageError,
    mime_type_for,
    normalize_on_secret,
    sanitize_filename,
    stricter_on_secret,
    upload_artifact,
)
from .secrets import SECRET_FLAGS_WARNING
from .storage import get_storage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ERROR_STATUS = {
    "file_too_large": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    "file_type_not_allowed": status.HTTP_400_BAD_REQUEST,
    "secret_detected": status.HTTP_400_BAD_REQUEST,
    "monthly_cap_reached": status.HTTP_429_TOO_MANY_REQUESTS,
    "storage_cap_reached": status.HTTP_402_PAYMENT_REQUIRED,
}

ERROR_MESSAGES = {
    "file_too_large": "File exceeds the 25 MiB limit.",
    "file_type_not_allowed": "File type is not allowed.",
    # Deliberately identical to file_type_not_allowed: a refused upload
    # reveals nothing about which safety signal fired.
    "content_not_allowed": "File type is not allowed.",
    "secret_detected": "Secret-shaped content detected; upload rejected (on_secret=reject).",
    "monthly_cap_reached": "Monthly upload cap for your plan has been reached.",
    "storage_cap_reached": (
        "Storage cap for your plan has been reached. "
        "Delete old artifacts or upgrade your plan."
    ),
    "invalid_request": "Invalid upload parameter.",
}


def _request_workspace(request):
    """Workspace for either an API-key/OAuth caller or a session-authed user."""
    auth = request.auth
    if auth is not None and getattr(auth, "workspace", None) is not None:
        return auth.workspace
    return get_or_create_workspace(request.user)


def _upload_failure_response(exc):
    """Map an upload_artifact failure to an error response."""
    if isinstance(exc, StorageError):
        return Response(
            {"error": "Storage is temporarily unavailable.", "code": exc.code},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    code = str(exc)
    body = {"error": ERROR_MESSAGES.get(code, "Upload failed."), "code": code}
    # Rejections that know why (e.g. file_type_not_allowed) say so;
    # safety-seam blocks stay deliberately bare.
    detail = getattr(exc, "detail", "")
    if detail:
        body["detail"] = detail
        if code == "invalid_request":
            # Published CLIs print only ``error``: name the bad parameter
            # there too, not just the generic message.
            body["error"] = detail
    return Response(
        body,
        status=ERROR_STATUS.get(code, status.HTTP_400_BAD_REQUEST),
    )


def _artifact_response_body(artifact):
    """201 body shared by the REST upload and the one-time-URL PUT."""
    body = {
        "id": str(artifact.id),
        "url": account.artifact_url(artifact),
        "expires_at": artifact.expires_at.isoformat(),
        "size_bytes": artifact.size_bytes,
        "secret_flags": artifact.secret_flags,
    }
    # Additive only: present (true) when the applied TTL differs from what
    # was requested (clamped down/up, or unparseable input fell back).
    # Agents must read expires_at, not the value they sent.
    if getattr(artifact, "ttl_adjusted", False):
        body["ttl_adjusted"] = True
    # Advisory, never blocking: a flagged upload IS stored. Tell the
    # agent to involve the human before sharing the link.
    if artifact.secret_flags:
        body["warning"] = SECRET_FLAGS_WARNING
        body["secret_flag_locations"] = getattr(
            artifact, "secret_flag_locations", {}
        )
        # Additive only: locations are capped per flag.
        if getattr(artifact, "secret_flag_locations_truncated", False):
            body["secret_flag_locations_truncated"] = True
    return body


def _file_too_large_response():
    return Response(
        {"error": ERROR_MESSAGES["file_too_large"], "code": "file_too_large"},
        status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    )


# Room for the multipart framing and the small text fields (ttl_days,
# on_secret) around a MAX_FILE_SIZE file.
MULTIPART_OVERHEAD = 1024 * 1024


class _FileTooLarge(APIException):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_detail = {
        "error": ERROR_MESSAGES["file_too_large"],
        "code": "file_too_large",
    }


class _BoundedUploadHandler(FileUploadHandler):
    """Refuse oversize multipart uploads before Django buffers them.

    Runs first in the handler chain: rejects on the declared
    Content-Length before any body byte is read, and on the running size
    of each file part, so a rejected upload never fills memory or a temp
    file past MAX_FILE_SIZE. Raises a DRF 413, which surfaces wherever the
    body is first parsed (the view, or the session CSRF check).
    """

    def handle_raw_input(
        self, input_data, META, content_length, boundary, encoding=None
    ):
        if content_length > MAX_FILE_SIZE + MULTIPART_OVERHEAD:
            raise _FileTooLarge()

    def receive_data_chunk(self, raw_data, start):
        if start + len(raw_data) > MAX_FILE_SIZE:
            raise _FileTooLarge()
        return raw_data

    def file_complete(self, file_size):
        return None


_BODY_CHUNK = 64 * 1024


def _read_body_bounded(request, limit):
    """The raw request body, or None when it is longer than ``limit``.

    Reads the stream in chunks instead of ``request.body``, so the upload
    cap, not DATA_UPLOAD_MAX_MEMORY_SIZE (2.5 MB), bounds a PUT body.
    Accumulates in a BytesIO, whose getvalue() hands over its buffer
    instead of copying it, so a full-size body is held about once.
    """
    buf = io.BytesIO()
    while True:
        chunk = request.read(_BODY_CHUNK)
        if not chunk:
            return buf.getvalue()
        if buf.tell() + len(chunk) > limit:
            return None
        buf.write(chunk)


# SafetyEvent.uploader_ip is a 64-character column.
AUDIT_IP_MAX_LENGTH = 64


def _audit_ip(request):
    """The uploader IP to record on a SafetyEvent.

    ``client_ip`` reads X-Forwarded-For, which the client can prefill, so
    its value is only kept when it parses as an IP address; otherwise the
    socket peer (REMOTE_ADDR) is recorded. Capped to the column width: an
    oversized value must never make the safety-block insert fail and skip
    the key suspension and alert that follow it.
    """
    for candidate in (client_ip(request), request.META.get("REMOTE_ADDR")):
        try:
            address = ipaddress.ip_address((candidate or "").strip())
        except ValueError:
            continue
        return str(address)[:AUDIT_IP_MAX_LENGTH]
    return ""


def _minting_credential_active(pending):
    """False once the credential that minted ``pending`` is revoked.

    API-key URLs follow the key row. OAuth-minted URLs follow the owner's
    grants for the minting application: any unexpired access token or
    unrevoked refresh token keeps them alive (a routine token refresh does
    not kill the URL; revoking the grants does). Rows with neither (minted
    before this check existed) stay valid until they expire.
    """
    owner = pending.workspace.owner
    if not owner.is_active:
        # Same rule as APIKey.verify / resolve_oauth_credential: a
        # suspended owner's credentials, and the URLs they minted, are dead.
        return False
    if pending.api_key_id is not None:
        return pending.api_key.is_active
    if pending.oauth_application_id is not None:
        if not is_email_verified(owner):
            return False
        grant = {
            "user_id": owner.pk,
            "application_id": pending.oauth_application_id,
        }
        return (
            get_access_token_model()
            .objects.filter(expires__gt=timezone.now(), **grant)
            .exists()
            or get_refresh_token_model()
            .objects.filter(revoked__isnull=True, **grant)
            .exists()
        )
    return True


class APIKeyUploadThrottle(SimpleRateThrottle):
    """100 uploads/hour per API key."""

    scope = "upload"
    rate = "100/hour"

    def get_cache_key(self, request, view):
        auth = request.auth
        if auth is None or getattr(auth, "workspace", None) is None:
            return None
        return self.cache_format % {"scope": self.scope, "ident": f"key-{auth.pk}"}


# ---------------------------------------------------------------------------
# Agent API (API-key auth)
# ---------------------------------------------------------------------------


class ArtifactListCreateView(APIView):
    """GET lists artifacts newest-first; POST uploads a new one.

    Accepts API-key auth (agents) or session auth (dashboard)."""

    authentication_classes = [APIKeyAuthentication, SessionAuthentication]
    permission_classes = [IsAPIKeyOrSessionAuthenticated]
    throttle_classes = [APIKeyUploadThrottle]

    def initialize_request(self, request, *args, **kwargs):
        # Bound multipart parsing before anything (including the session
        # CSRF check) touches the body.
        if request.method == "POST":
            request.upload_handlers.insert(0, _BoundedUploadHandler(request))
        return super().initialize_request(request, *args, **kwargs)

    def get_throttles(self):
        # Throttle uploads only; listing is unlimited.
        if self.request.method == "POST":
            return super().get_throttles()
        return []

    def get(self, request):
        answer = account.list_artifacts(
            request.user,
            cursor=request.query_params.get("cursor"),
            limit=request.query_params.get("limit"),
            workspace=_request_workspace(request),
        )
        response = answer_response(answer)
        next_cursor = answer.headers.get("X-Next-Cursor")
        if next_cursor:
            # The body stays a bare list; the next page is in headers.
            query = urlencode({"limit": len(answer.body), "cursor": next_cursor})
            response["Link"] = f'<{request.path}?{query}>; rel="next"'
        return response

    def post(self, request):
        # Session uploads publish public links, so they wait on a verified
        # contact address like key minting does. Credential (API key /
        # OAuth) uploads are unaffected: minting the credential was gated.
        if request.auth is None and not is_email_verified(request.user):
            return Response(
                {
                    "error": (
                        "Verify your email address before uploading. "
                        "Check your inbox for the verification link."
                    ),
                    "code": "email_verification_required",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        upload = request.FILES.get("file")
        if upload is None:
            return Response(
                {"error": "No file provided.", "code": "file_missing"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if upload.size > MAX_FILE_SIZE:
            return _file_too_large_response()
        ttl_days = request.data.get("ttl_days")
        on_secret = request.data.get("on_secret") or request.query_params.get("on_secret")
        data = upload.read()
        try:
            artifact = upload_artifact(
                workspace=_request_workspace(request),
                api_key=request.auth if isinstance(request.auth, APIKey) else None,
                filename=upload.name,
                data=data,
                mime_type=None,  # derived from the extension by the service
                ttl_days=ttl_days,
                uploader_ip=_audit_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                on_secret=on_secret,
            )
        except (ValueError, StorageError) as exc:
            return _upload_failure_response(exc)
        workspace = _request_workspace(request)
        upload_number = Artifact.objects.filter(workspace=workspace).count()
        funnel.record(
            request, funnel.UPLOAD_COMPLETED,
            workspace=workspace, upload_number=upload_number,
        )
        return Response(
            _artifact_response_body(artifact), status=status.HTTP_201_CREATED
        )


class ArtifactDeleteView(APIView):
    """Revoke an artifact early. API-key auth (agents) or session auth."""

    authentication_classes = [APIKeyAuthentication, SessionAuthentication]
    permission_classes = [IsAPIKeyOrSessionAuthenticated]

    def delete(self, request, artifact_id):
        return answer_response(
            account.revoke_artifact(
                request.user, artifact_id, workspace=_request_workspace(request)
            )
        )


class PendingUploadView(APIView):
    """PUT raw file bytes to a minted one-time upload URL.

    No API key: the unguessable token in the path IS the auth (same pattern
    as ``/a/<token>`` downloads). Expired, used, and unknown tokens — and
    tokens whose minting credential has since been revoked — all 404
    identically. Cheap checks (credential, parameters, size) run and the
    body is read before the claim; the claim flips ``used`` atomically,
    so two concurrent PUTs cannot both succeed. The body then runs the
    full upload pipeline (``upload_artifact``): type verification, secret
    scan, caps, quota. A rejected upload releases the claim so the agent
    can retry the same URL; only a success or a safety block consumes it.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def put(self, request, token):
        not_found = Response(
            {"error": "Upload URL not found or expired.", "code": "not_found"},
            status=status.HTTP_404_NOT_FOUND,
        )
        pending = (
            PendingUpload.objects.select_related("workspace__owner", "api_key")
            .filter(token=token, used=False, expires_at__gt=timezone.now())
            .first()
        )
        if pending is None:
            return not_found
        if not _minting_credential_active(pending):
            # A revoked credential's URLs die with it.
            PendingUpload.objects.filter(pk=pending.pk).update(used=True)
            return not_found
        try:
            # ?on_secret may tighten the minted value, never loosen it.
            on_secret = stricter_on_secret(
                normalize_on_secret(pending.on_secret),
                normalize_on_secret(request.query_params.get("on_secret")),
            )
        except RejectedUpload as exc:
            return _upload_failure_response(exc)
        try:
            declared = int(request.META.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            declared = 0
        if declared > MAX_FILE_SIZE:
            return _file_too_large_response()
        try:
            data = _read_body_bounded(request, MAX_FILE_SIZE)
        except UnreadablePostError:
            return Response(
                {"error": "Could not read the upload body.", "code": "invalid_request"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if data is None:
            return _file_too_large_response()

        claimed = PendingUpload.objects.filter(
            pk=pending.pk, used=False, expires_at__gt=timezone.now()
        ).update(used=True)
        if claimed != 1:
            return not_found
        try:
            artifact = upload_artifact(
                workspace=pending.workspace,
                api_key=pending.api_key,
                filename=pending.filename,
                data=data,
                mime_type=None,  # derived from the extension by the service
                ttl_days=pending.ttl_days,
                uploader_ip=_audit_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                on_secret=on_secret,
            )
        except (ValueError, StorageError) as exc:
            if str(exc) != "content_not_allowed":
                # Rejected or failed, nothing stored: release the claim so
                # a corrected retry can use the URL before it expires. A
                # safety block keeps it consumed.
                PendingUpload.objects.filter(pk=pending.pk).update(used=False)
            response = _upload_failure_response(exc)
            if isinstance(exc, StorageError):
                # The service logged it by artifact id. django.request
                # would log the 502 with the path, which holds the upload
                # token, still live because the claim was released.
                response._has_been_logged = True
            return response
        workspace = pending.workspace
        upload_number = Artifact.objects.filter(workspace=workspace).count()
        funnel.record(
            request, funnel.UPLOAD_COMPLETED,
            workspace=workspace, upload_number=upload_number,
        )
        return Response(
            _artifact_response_body(artifact), status=status.HTTP_201_CREATED
        )


# ---------------------------------------------------------------------------
# Key management + usage (session auth, dashboard)
# ---------------------------------------------------------------------------


def _is_oauth_credential(request):
    return bool(getattr(request.auth, "is_oauth", False))


def _session_required_response(message):
    return Response(
        {"error": message, "code": "session_required"},
        status=status.HTTP_403_FORBIDDEN,
    )


_OAUTH_KEYS_FORBIDDEN = "OAuth tokens cannot manage API keys. Use the dashboard."


class KeyListCreateView(APIView):
    # Key management works for the dashboard (session) and, for listing and
    # revoking itself, for an agent holding an API key (``veilfile logout``
    # finds and revokes its own key). OAuth tokens (third-party MCP apps) cannot
    # touch keys at all, and no credential can mint one. The workspace
    # always comes from the credential, never from request.user, because
    # key auth leaves request.user anonymous.
    permission_classes = [IsAPIKeyOrSessionAuthenticated]

    def get(self, request):
        if _is_oauth_credential(request):
            return _session_required_response(_OAUTH_KEYS_FORBIDDEN)
        return answer_response(
            account.list_keys(request.user, workspace=_request_workspace(request))
        )

    def post(self, request):
        # Credentials cannot mint credentials: only the dashboard session
        # can. Say so plainly instead of the verification error below
        # (request.user is anonymous under key auth).
        if request.auth is not None:
            return _session_required_response(
                "API keys and OAuth tokens cannot create keys. "
                "Create keys from the dashboard."
            )
        # The gates are drop_core.account.create_key's, which the dashboard
        # calls too: a verified email, a usable name, the plan's key cap. The
        # email comes first, before the body is read, as it always did.
        refusal = account.verification_required(request.user)
        if refusal is not None:
            return answer_response(refusal)
        data = request.data
        is_object = hasattr(data, "get")
        return answer_response(
            account.create_key(
                request.user,
                data.get("name") if is_object else None,
                request=request,
                body_is_object=is_object,
            )
        )


class KeyDeleteView(APIView):
    # See KeyListCreateView: workspace comes from the credential. Revoking
    # a key revokes only that key; OAuth grants are managed separately
    # (Connected apps on the dashboard, /api/v1/oauth/grants).
    permission_classes = [IsAPIKeyOrSessionAuthenticated]

    def delete(self, request, key_id):
        if _is_oauth_credential(request):
            return _session_required_response(_OAUTH_KEYS_FORBIDDEN)
        return answer_response(
            account.revoke_key(
                request.user,
                key_id,
                workspace=_request_workspace(request),
                # An API key may revoke only itself; a session may revoke any.
                only_key_id=request.auth.pk if request.auth is not None else None,
            )
        )


class OAuthGrantRevokeView(APIView):
    """POST /api/v1/oauth/revoke-grants — revoke all OAuth tokens for the
    signed-in user (dashboard action). Revokes DOT AccessToken + RefreshToken
    rows for the user's Applications; the DCR client rows are kept so the
    client can re-authorize.

    An OAuth token may call it to disconnect its own app (a logout), never
    the user's other apps. API keys get 403: a key has no OAuth grants of
    its own, and a leaked key must not disconnect the user's apps."""

    authentication_classes = [APIKeyAuthentication, SessionAuthentication]
    permission_classes = [IsAPIKeyOrSessionAuthenticated]

    def post(self, request):
        if _is_oauth_credential(request):
            application = request.auth.application
            if application is None:
                # Never widen an app-less token's logout to every app.
                return _session_required_response(
                    "Disconnect apps from the dashboard."
                )
            revoked = revoke_user_oauth_grants(
                request.auth.workspace.owner, application=application
            )
        elif request.auth is not None:
            return _session_required_response(
                "API keys cannot disconnect OAuth apps. "
                "Use Connected apps on the dashboard."
            )
        else:
            revoked = revoke_user_oauth_grants(request.user)
        return Response({"revoked_tokens": revoked}, status=status.HTTP_200_OK)


class OAuthGrantListView(APIView):
    """GET /api/v1/oauth/grants — the signed-in user's connected OAuth apps
    (dashboard "Connected apps"). Session only: a credential must not be
    able to enumerate or disconnect other apps."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return answer_response(account.list_grants(request.user))


class OAuthGrantDeleteView(APIView):
    """DELETE /api/v1/oauth/grants/<app_id> — disconnect one app: revoke the
    user's tokens and pending codes for it. The DCR client row is kept, so
    the app can ask for consent again. Session only (CSRF-protected)."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def delete(self, request, app_id):
        return answer_response(account.disconnect_grant(request.user, app_id))


class UsageView(APIView):
    """Usage for the calling account. API-key auth (agents) or session auth
    (dashboard). API-key callers see the key's workspace, including the
    workspace owner's email — the same data the dashboard shows."""

    authentication_classes = [APIKeyAuthentication, SessionAuthentication]
    permission_classes = [IsAPIKeyOrSessionAuthenticated]

    def get(self, request):
        workspace = _request_workspace(request)
        auth = request.auth
        if auth is not None and getattr(auth, "workspace", None) is not None:
            account_user = workspace.owner
        else:
            account_user = request.user
        return answer_response(account.usage(account_user, workspace=workspace))


# ---------------------------------------------------------------------------
# Public artifact serving + signup
# ---------------------------------------------------------------------------

# Rendered in the browser: raster images, PDF and plain text. Everything
# else (JSON/HAR, unknown) downloads.
INLINE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "application/pdf",
    "text/plain",
}
# Text types are always UTF-8 (upload verification decodes them as such).
UTF8_TYPES = {"text/plain", "application/json"}


def _is_missing_object(exc):
    """True for an S3/R2 "no such key" error: the object is gone, the
    backend is fine."""
    error = getattr(exc, "response", None)
    if not isinstance(error, dict):
        return False
    code = str((error.get("Error") or {}).get("Code", ""))
    return code in ("NoSuchKey", "404", "NotFound")


def serve_artifact(request, token):
    """Public, unauthenticated download. The 256-bit token IS the auth."""
    # Every kind of miss is the same answer, from one function (pages.errors.artifact_miss), so a link
    # that never existed, one that expired and one whose object is gone cannot be told apart.
    try:
        artifact = Artifact.objects.get(token=token)
    except Artifact.DoesNotExist:
        return artifact_miss()
    if artifact.is_expired():
        return artifact_miss()
    if artifact.safety_status == "held":
        # Do not confirm the object exists.
        return artifact_miss()
    try:
        fileobj = get_storage().open(artifact.token)
    except FileNotFoundError:
        logger.warning("serve: stored object missing for artifact id=%s", artifact.id)
        return artifact_miss()
    except Exception as exc:
        if _is_missing_object(exc):
            return artifact_miss()
        # A backend outage is not "link expired": say 503 so clients retry
        # and operators see it. Log only the exception type: the message
        # can carry the object URL, and this token is live.
        logger.error(
            "serve: storage open failed for artifact id=%s (%s)",
            artifact.id,
            type(exc).__name__,
        )
        response = HttpResponse(
            "This file is temporarily unavailable. Try again in about 30 seconds.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )
        response["Cache-Control"] = "no-store"
        response["Retry-After"] = "30"
        # Logged above by id. Without this, django.request would log the
        # 5xx as "Service Unavailable: /a/<token>" at ERROR, which the
        # production LOGGING config prints.
        response._has_been_logged = True
        return response

    # Type and name come from the stored filename via the server-side map,
    # never the stored mime_type, so rows uploaded before the map existed
    # (client-chosen types, unsanitized names) are served safely too.
    filename = sanitize_filename(artifact.filename)
    mime = mime_type_for(filename)
    is_inline = mime in INLINE_TYPES
    response = FileResponse(
        fileobj,
        content_type=f"{mime}; charset=utf-8" if mime in UTF8_TYPES else mime,
        as_attachment=not is_inline,
        filename=filename,
    )
    # RFC 6266 quoting (filename*= for non-ASCII) for inline and
    # attachment alike.
    response["Content-Disposition"] = content_disposition_header(
        not is_inline, filename
    )
    # Hardening: artifact URLs are bearer secrets. Nothing may cache the
    # bytes (revocation must bite everywhere instantly), and nothing may
    # index them.
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    # Defense in depth for browser-rendered files (PDFs, text): a sandboxed
    # document gets an opaque origin, so a malicious file cannot touch the
    # app origin. allow-downloads keeps user-initiated saves working.
    response["Content-Security-Policy"] = "sandbox allow-downloads"
    return response


def safe_next_url(request):
    """The ?next= of a sign in or sign up page when it is an address on this site that a redirect may
    use, else "".

    It is the test LoginView applies after a sign-in: this host, http or https, https when the request
    is. A page that sends a visitor on, or links to the page that will, reads the address here and
    never from the query itself.
    """
    url = request.GET.get("next", "")
    allowed = url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return url if url and allowed else ""


def signed_in_destination(request):
    """Where a signed-in visitor to sign in or sign up is sent: the safe ?next=, else the dashboard.
    A next that names the page itself would send the visitor round in a circle, so it counts as none."""
    url = safe_next_url(request)
    if url and urlsplit(url).path != request.path:
        return url
    return settings.LOGIN_REDIRECT_URL


class SignInView(auth_views.LoginView):
    """Sign in (registration/login.html).

    A signed-in visitor who opens the page goes on to ?next= or the dashboard. One who posts it is
    signed in as the account they name, as before: answering that POST with a redirect would make
    throttle_login, which reads a redirect as a successful sign-in, clear the failure count of
    whichever account the POST named.
    """

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(signed_in_destination(request))
        return super().get(request, *args, **kwargs)


@funnel.sets_funnel_cookie
def signup(request):
    # Plans selectable from the landing page: ?plan=free|team|scale.
    # Paid plans skip the dashboard and go straight to Stripe checkout
    # after the account is created. Invalid values fall back to free.
    plan = request.GET.get("plan", "free")
    if plan not in PLANS:
        plan = "free"
    paid = plan in ("team", "scale")

    # Already signed in with a paid plan picked: skip the signup form. A GET
    # only shows a confirm step (links and prefetchers follow GETs); the
    # POST starts checkout. Subscribers switch plans from the dashboard.
    if request.user.is_authenticated and paid:
        if request.method == "POST":
            return redirect(_paid_signup_checkout(request.user, plan))
        if get_or_create_workspace(request.user).stripe_subscription_id:
            return redirect("/app/?billing=already_subscribed")
        return render(
            request,
            "registration/checkout_confirm.html",
            {
                "plan": plan,
                "plan_name": plan.title(),
                "price_usd": PLANS[plan]["price_usd"],
            },
        )

    # A signed-in visitor has no use for the form: send them on (a POST is not a visit and is handled
    # as ever). The paid branch above comes first, so a picked plan still reaches its checkout step.
    if request.user.is_authenticated and request.method in ("GET", "HEAD"):
        return redirect(signed_in_destination(request))

    if request.method == "POST":
        # The signup endpoint sends email: throttle it per IP.
        ip = client_ip(request)
        if not signup_attempt_allowed(ip) or not throttle.signup_network_allowed(ip):
            return throttle.signup_throttled_response(request)
        throttle.record_signup_attempt(ip)
        EmailSendLog.objects.create(
            ip_address=ip, purpose=EmailSendLog.PURPOSE_SIGNUP_ATTEMPT
        )
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            workspace = get_or_create_workspace(user)
            EmailVerification.objects.create(user=user, email=user.email)
            # First send never trips the resend throttles (fresh record).
            send_verification_email(user, request)
            login(request, user)
            funnel.record(
                request,
                funnel.SIGNUP_COMPLETED,
                workspace=workspace,
                once_per_workspace=True,
            )
            if paid:
                return redirect(_paid_signup_checkout(user, plan))
            return redirect(safe_next_url(request) or "/app/")
    else:
        form = SignupForm()
        funnel.record(request, funnel.SIGNUP_STARTED, once_per_session=True)
    return render(
        request,
        "registration/signup.html",
        {
            "form": form,
            "plan": plan,
            "plan_blurb": _plan_blurb(plan),
            "next": safe_next_url(request),
        },
    )


@login_required
def email_verification(request):
    """Manage the user's email address and verification state.

    Pre-existing users land here from the dashboard prompt to add an
    address; everyone can re-send the verification link (throttled).

    Changing the address needs the current password. The new address is
    held on the EmailVerification row only; ``user.email`` (where password
    reset links go) changes when the new address is verified. A previously
    verified address gets a change notice.
    """
    record = EmailVerification.objects.filter(user=request.user).first()

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update_email":
            ip = client_ip(request)
            username = request.user.get_username()
            # The password check is a guessing oracle: share the login
            # failure budget.
            if throttle.login_locked(ip, username):
                return throttle.login_throttled_response(request)
            form = EmailUpdateForm(request.user, request.POST)
            if form.is_valid():
                email = form.cleaned_data["email"]
                allowed, reason = resend_allowed(request.user, email)
                if not allowed:
                    return throttle.verification_throttled_response(request, reason)
                notify = (
                    record is not None
                    and record.is_verified
                    and record.email.lower() != email
                )
                old_email = record.email if record is not None else ""
                record = get_or_create_verification(request.user, email)
                send_verification_email(request.user, request)
                if notify:
                    send_email_change_notice(request.user, old_email, email)
                return redirect("/accounts/verify-email/?sent=1")
            if "password" in form.errors:
                throttle.record_login_failure(ip, username)
        elif action == "resend":
            if record is None:
                return redirect("/accounts/verify-email/")
            allowed, reason = resend_allowed(request.user, record.email)
            if not allowed:
                return throttle.verification_throttled_response(request, reason)
            send_verification_email(request.user, request)
            return redirect("/accounts/verify-email/?sent=1")
        else:
            form = EmailUpdateForm(request.user)
    else:
        form = EmailUpdateForm(
            request.user, initial={"email": record.email if record else ""}
        )
    return render(
        request,
        "registration/verify_email.html",
        {
            "form": form,
            "record": record,
            "verified": is_email_verified(request.user),
            "sent": request.GET.get("sent") == "1",
        },
    )


def verify_email(request, token):
    """Click-through from the verification email. Marks the address
    verified when the token is valid, unexpired, and still matches the
    address on the user's record, then makes it the account address."""
    parsed, error = parse_verification_token(token)
    if error:
        return render(
            request,
            "registration/verify_email_invalid.html",
            {"reason": error},
        )
    pk, digest, fingerprint = parsed
    User = get_user_model()
    try:
        user = User.objects.get(pk=pk)
        record = user.email_verification
    except (User.DoesNotExist, EmailVerification.DoesNotExist, ValueError):
        return render(
            request,
            "registration/verify_email_invalid.html",
            {"reason": "invalid"},
        )
    taken = (
        User.objects.filter(email__iexact=record.email).exclude(pk=user.pk).exists()
    )
    stale_password = fingerprint is not None and not constant_time_compare(
        fingerprint, password_fingerprint(user)
    )
    if email_digest(record.email) != digest or taken or stale_password:
        # Address changed after the link was sent (the old token dies),
        # another account claimed the address while this one was pending,
        # or the password changed since (account recovery kills links).
        return render(
            request,
            "registration/verify_email_invalid.html",
            {"reason": "invalid"},
        )
    email = record.email
    if not record.is_verified:
        record.is_verified = True
        record.verified_at = timezone.now()
        record.save(update_fields=["is_verified", "verified_at"])
    if (user.email or "").lower() != email.lower():
        user.email = email
        user.save(update_fields=["email"])
    return render(request, "registration/verify_email_done.html", {})


class ThrottledPasswordResetView(auth_views.PasswordResetView):
    """Password reset with per-IP and per-address limits. Every request can
    send an email from the shared SMTP account that signup verification
    also needs. The limits count requests, not accounts, so a throttled
    response says nothing about whether the address is registered."""

    def post(self, request, *args, **kwargs):
        if not throttle.password_reset_ip_allowed(client_ip(request)):
            return throttle.password_reset_throttled_response(request)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        email = form.cleaned_data["email"]
        if not throttle.password_reset_address_allowed(email):
            return throttle.password_reset_throttled_response(self.request)
        throttle.record_password_reset(client_ip(self.request), email)
        return super().form_valid(form)


class RevokingPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    """Setting a new password through a reset link disconnects every OAuth
    app (access and refresh tokens). Other sessions die with the password
    hash. API keys are left alone: they are explicit per-agent credentials
    the owner can see and revoke on the dashboard, and killing them would
    break unattended agents mid-run.

    The new password also clears that username's login lock, and the reset
    signs the owner in (the link already proves control of the mailbox), so
    an attacker who keeps re-locking the username cannot keep the owner out.
    """

    post_reset_login = True

    def form_valid(self, form):
        response = super().form_valid(form)
        revoke_user_oauth_grants(form.user)
        throttle.clear_login_failures(form.user.get_username())
        return response


class RevokingPasswordChangeView(auth_views.PasswordChangeView):
    """Password change: same OAuth revocation as a reset (see above). The
    old-password check is a guessing oracle for whoever holds the session,
    so wrong old passwords share the login failure budget."""

    def post(self, request, *args, **kwargs):
        if throttle.login_locked(client_ip(request), request.user.get_username()):
            return throttle.login_throttled_response(request)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        if "old_password" in form.errors:
            throttle.record_login_failure(
                client_ip(self.request), self.request.user.get_username()
            )
        return super().form_invalid(form)

    def form_valid(self, form):
        response = super().form_valid(form)
        revoke_user_oauth_grants(form.user)
        return response


def _paid_signup_checkout(user, plan):
    """Start Stripe checkout for a paid-plan signup and return where to
    send the user: the checkout URL, or the dashboard flagged
    ``?billing=already_subscribed`` or ``?billing=error&plan=...`` (the
    failure is logged by drop_billing), where upgrading stays possible.
    """
    # Lazy import: drop_billing imports drop_core.models at module level.
    from drop_billing.views import paid_signup_redirect

    return paid_signup_redirect(user, plan)


def _plan_blurb(plan):
    if plan == "team":
        return "You're signing up for the Team plan ($4/mo, tax included). You'll go straight to Stripe checkout after creating your account."
    if plan == "scale":
        return "You're signing up for the Scale plan ($12/mo, tax included). You'll go straight to Stripe checkout after creating your account."
    return None


def llms_txt(request):
    """Serve the agent-facing llms.txt (single source: docs/llms.txt).

    Substitutes the live base URL for the YOUR-DEPLOY placeholder so
    agents reading it get a working quickstart with zero editing.
    """
    import os
    from pathlib import Path

    path = Path(settings.BASE_DIR).parent / "docs" / "llms.txt"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        raise Http404("llms.txt not available.")
    base_url = os.environ.get("DROP_BASE_URL")
    if base_url:
        content = content.replace("https://YOUR-DEPLOY", base_url.rstrip("/"))
    return HttpResponse(content, content_type="text/plain")
