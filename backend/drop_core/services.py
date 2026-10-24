"""Core artifact lifecycle: upload validation, caps, TTL, storage, revocation."""

import io
import logging
import os
import re
import secrets as stdlib_secrets
import unicodedata
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    Artifact,
    BlockedHash,
    PendingUpload,
    SafetyEvent,
    UsageCounter,
    Workspace,
)
from .plans import PLANS
from .safety import (
    MODE_ENFORCE,
    MODE_LOG,
    content_sha256,
    detect_image_kind,
    safety_mode,
    screen_bytes,
    verify_magic_bytes,
)
from .secrets import locate_flags, scan_bytes
from .signals import mark_bytes_handled
from .storage import get_storage

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MiB


class StorageError(Exception):
    """Raised when the storage backend fails; carries a short error code."""

    def __init__(self, code="storage_unavailable"):
        super().__init__(code)
        self.code = code


class RejectedUpload(ValueError):
    """A rejected upload with a stable code and a human detail string.

    Views map the code to the HTTP status and surface the detail alongside
    the generic error message. Detail is safe to show: it names only the
    detected kind, the file extension, and the public allowlist. Never use
    this for safety-seam blocks (content_not_allowed stays deliberately
    bare so a refused upload reveals nothing about which signal fired).
    """

    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = detail

ALLOWED_EXTENSIONS = {
    "png", "jpg", "jpeg", "webp", "gif",
    "txt", "md", "log", "json", "har", "pdf",
}

BLOCKED_EXTENSIONS = {
    "exe", "dll", "sh", "bat", "cmd", "ps1",
    "zip", "tar", "gz", "rar", "7z",
}


# Stored and served Content-Type comes from the validated extension, never
# from the client: a declared text/html or image/svg+xml must not render on
# the app origin, and clients that send no type (the npm CLI) still get
# inline previews. Anything not listed is served as an octet-stream download.
MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "pdf": "application/pdf",
    "txt": "text/plain",
    "log": "text/plain",
    "md": "text/plain",
    "json": "application/json",
    "har": "application/json",
}

ON_SECRET_CHOICES = ("flag", "reject")

# Stored filenames are capped in UTF-8 bytes so a pulled file fits common
# file-system name limits (255 bytes) with room to spare.
FILENAME_MAX_BYTES = 200
# Path separators, percent (decoded by some clients into separators), drive
# colons, and quotes (header framing). Control/format characters are
# handled by Unicode category below.
_UNSAFE_FILENAME_CHARS = frozenset('/\\%:"')
_UNSAFE_FILENAME_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def _extension(filename):
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def mime_type_for(filename):
    """The Content-Type for ``filename``, from its extension alone."""
    return MIME_TYPES.get(_extension(filename or ""), "application/octet-stream")


def sanitize_filename(name):
    """A single safe path component for storage and Content-Disposition.

    Keeps the last component of either separator style, replaces
    separators, '%', ':', quotes and control/format characters with '_',
    strips leading/trailing dots and spaces, and caps the length — all
    without touching the extension, which upload validation reads. An
    empty stem becomes ``upload``. Also applied when serving, so rows
    stored before this existed are made safe too. Idempotent, so the
    served name is the stored and listed name.
    """
    if not isinstance(name, str):
        name = ""
    # Truncating a long name without a usable extension can leave an
    # extension-shaped tail ("x..de"); a second pass settles it, and the
    # pass after that never changes anything.
    return _sanitize_filename_pass(_sanitize_filename_pass(name))


def _sanitize_filename_pass(name):
    # Trailing dots and spaces go before the extension is split off, so
    # the extension is read from the name as it will be stored.
    base = name.replace("\\", "/").rsplit("/", 1)[-1].rstrip(" .")
    stem, dot, ext = base.rpartition(".")
    if not (dot and ext.isascii() and ext.isalnum() and len(ext) <= 16):
        stem, ext = base, ""
    stem = "".join(
        "_"
        if c in _UNSAFE_FILENAME_CHARS
        or unicodedata.category(c) in _UNSAFE_FILENAME_CATEGORIES
        else c
        for c in stem
    )
    budget = FILENAME_MAX_BYTES - (len(ext) + 1 if ext else 0)
    stem = stem.strip(" .")
    stem = stem.encode("utf-8")[:budget].decode("utf-8", "ignore").strip(" .")
    stem = stem or "upload"
    return f"{stem}.{ext}" if ext else stem


def normalize_on_secret(value):
    """``"flag"`` or ``"reject"`` (case-insensitive; omitted/blank is
    ``"flag"``). Anything else raises ``invalid_request``: a typo must not
    silently store a secret."""
    if value is None:
        return "flag"
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return "flag"
        if text in ON_SECRET_CHOICES:
            return text
    raise RejectedUpload(
        "invalid_request", 'on_secret must be "flag" or "reject"'
    )


def stricter_on_secret(*values):
    """``"reject"`` when any of the (normalized) values is ``"reject"``."""
    return "reject" if "reject" in values else "flag"


def _allowed_list():
    """The public allowlist, read from code — never hardcoded in messages."""
    return ", ".join(sorted(ALLOWED_EXTENSIONS))


def _describe_detected(data):
    """Best-effort content kind for rejection messages (magic bytes only)."""
    kind = detect_image_kind(data)
    if kind:
        return kind
    if data.startswith(b"%PDF-"):
        return "pdf"
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "unknown binary"
    return "text"


_INTEGER_RE = re.compile(r"[+-]?[0-9]+")


def _parse_ttl_days(ttl_days):
    """The requested TTL as an int, or None when omitted (or blank).

    Accepts ints and whole-number strings (form fields arrive as text).
    Anything else — decimals, exponents, words — raises
    ``invalid_request``: guessing would hand out the plan maximum to a
    caller who asked for a short-lived link.
    """
    if ttl_days is None:
        return None
    if isinstance(ttl_days, int) and not isinstance(ttl_days, bool):
        return ttl_days
    if isinstance(ttl_days, str):
        text = ttl_days.strip()
        if not text:
            return None
        if _INTEGER_RE.fullmatch(text):
            return int(text)
    raise RejectedUpload(
        "invalid_request", "ttl_days must be a whole number of days, e.g. 7"
    )


def _clamp_ttl(plan, ttl_days):
    """Resolve the applied TTL and whether it differs from the request.

    Above-max values clamp down to the plan max, sub-1 values clamp up to
    1 day, and an omitted value takes the plan max. Non-integer values
    raise ``RejectedUpload("invalid_request")``.
    """
    max_ttl = plan["ttl_days"]
    requested = _parse_ttl_days(ttl_days)
    if requested is None:
        return max_ttl, False
    ttl = max(1, min(requested, max_ttl))
    return ttl, ttl != requested


def _check_extension(filename):
    ext = _extension(filename)
    if ext in BLOCKED_EXTENSIONS or ext not in ALLOWED_EXTENSIONS:
        raise RejectedUpload(
            "file_type_not_allowed",
            f"extension '.{ext or '(none)'}' is not allowed; "
            f"allowed: {_allowed_list()}",
        )
    return ext


UPLOAD_URL_TTL = timedelta(minutes=15)


def _new_upload_token():
    return stdlib_secrets.token_urlsafe(32)


def mint_upload_url(
    workspace,
    api_key,
    filename,
    ttl_days=None,
    on_secret="flag",
    oauth_application_id=None,
):
    """Mint a one-time upload URL for ``workspace``.

    Validates the filename extension against the allowlist now (cheap early
    rejection); the PUT body still runs the full upload pipeline. Returns
    the ``PendingUpload``. Raises ``RejectedUpload`` for a disallowed
    extension or an invalid ``ttl_days`` / ``on_secret``. ``on_secret`` is
    stored and enforced by the PUT; ``oauth_application_id`` records the
    OAuth client that minted the URL (None for API keys).
    """
    filename = sanitize_filename(filename or "upload.bin")
    _check_extension(filename)
    on_secret = normalize_on_secret(on_secret)
    plan = PLANS[workspace.plan]
    ttl, adjusted = _clamp_ttl(plan, ttl_days)
    return PendingUpload.objects.create(
        workspace=workspace,
        api_key=api_key,
        filename=filename,
        ttl_days=ttl,
        ttl_adjusted=adjusted,
        on_secret=on_secret,
        oauth_application_id=oauth_application_id,
        token=_new_upload_token(),
        expires_at=timezone.now() + UPLOAD_URL_TTL,
    )


def _current_month():
    return timezone.now().strftime("%Y-%m")


def workspace_storage_bytes(workspace):
    """Live (non-expired) artifact bytes for a workspace.

    Expired-but-not-yet-swept artifacts are excluded: they are logically
    gone, and the daily sweeper reclaims their storage. This grants a
    small grace window between expiry and the sweep.
    """
    from django.db.models import Sum

    total = Artifact.objects.filter(
        workspace=workspace, expires_at__gt=timezone.now()
    ).aggregate(total=Sum("size_bytes"))["total"]
    return total or 0


def upload_artifact(
    workspace,
    api_key,
    filename,
    data: bytes,
    mime_type,
    ttl_days=None,
    uploader_ip="",
    user_agent="",
    on_secret="flag",
):
    """Validate, scan, store and record an artifact.

    Raises ``ValueError`` with one of: ``file_too_large``,
    ``file_type_not_allowed``, ``invalid_request``, ``monthly_cap_reached``,
    ``storage_cap_reached``, ``content_not_allowed``, ``secret_detected``.
    ``ttl_days`` is clamped to the plan maximum; a non-integer value raises
    ``invalid_request``. The returned artifact carries a non-persistent
    ``ttl_adjusted`` attribute: True when the requested ``ttl_days`` was not
    the TTL that got applied. ``on_secret="reject"`` rejects (400
    ``secret_detected``, nothing stored) when any secret flag fires; the
    default ``"flag"`` keeps the advisory behavior; any other value raises
    ``invalid_request``. ``mime_type`` is ignored: the stored type comes
    from the validated extension (``mime_type_for``). The filename is
    sanitized (``sanitize_filename``) before validation.
    """
    # Lazy expiry sweep (at most once/24h): Render cron jobs need a paid
    # plan, so the web service sweeps on upload instead. Never blocks upload.
    from .sweep import maybe_sweep

    maybe_sweep()

    if len(data) > MAX_FILE_SIZE:
        raise ValueError("file_too_large")

    plan = PLANS[workspace.plan]
    # Fail closed on bad parameters before any work is done.
    on_secret = normalize_on_secret(on_secret)
    ttl, ttl_adjusted = _clamp_ttl(plan, ttl_days)

    filename = sanitize_filename(filename or "upload.bin")
    ext = _check_extension(filename)
    # Magic bytes, not the extension, decide what the file is. An .exe
    # renamed to .png must not reach the scanner or storage.
    if not verify_magic_bytes(data, ext):
        raise RejectedUpload(
            "file_type_not_allowed",
            f"file contents (detected {_describe_detected(data)}) do not match "
            f"extension '.{ext}'; allowed: {_allowed_list()}",
        )
    mime_type = mime_type_for(filename)

    # Cheap early cap checks so a capped workspace is refused before the
    # scan. Not authoritative: the reservation below re-checks atomically.
    storage_cap_bytes = plan["storage_gb"] * 1024**3
    counter, _ = UsageCounter.objects.get_or_create(
        workspace=workspace, year_month=_current_month()
    )
    if counter.uploads >= plan["uploads"]:
        raise ValueError("monthly_cap_reached")
    if workspace_storage_bytes(workspace) + len(data) > storage_cap_bytes:
        raise ValueError("storage_cap_reached")

    secret_flags = scan_bytes(data)
    if on_secret == "reject" and secret_flags:
        # Refused before anything persists: no DB row, no R2 object, no
        # quota consumed, no URL minted.
        raise RejectedUpload(
            "secret_detected",
            f"secret-shaped content detected ({', '.join(secret_flags)}); "
            "upload rejected (on_secret=reject), nothing stored",
        )

    # Safety seam (Phase 0). Runs after the secret scan, before anything
    # persists: refused bytes never reach the database or R2, never
    # consume quota, and never mint a URL.
    digest = content_sha256(data)
    mode = safety_mode()
    safety_status = "clear"
    if mode != "off":
        verdict = screen_bytes(data, mime_type)
        if verdict.signal == "scanner_error":
            # Fail open, but mark for async recheck.
            safety_status = "pending"
            logger.warning(
                "safety scanner degraded, upload allowed pending recheck: %s",
                verdict.detail,
            )
        elif verdict.action == "block":
            _record_safety_block(
                workspace=workspace,
                api_key=api_key,
                digest=digest,
                verdict=verdict,
                uploader_ip=uploader_ip,
                user_agent=user_agent,
                enforce=(mode == MODE_ENFORCE),
            )
            if mode == MODE_ENFORCE:
                raise ValueError("content_not_allowed")
            logger.warning(
                "safety log-mode: would block upload (signal=%s)",
                verdict.signal,
            )

    # Reserve quota atomically. The first statement is a conditional
    # UPDATE of the month's counter: it can never push the count past the
    # cap or lose an increment, and the row lock it takes (the write lock
    # on SQLite) queues concurrent uploads for this workspace until commit.
    # The workspace row is then locked as well, and the storage cap is
    # checked after this artifact row is written, so the sum includes
    # every upload committed before it. Any failure rolls back both.
    with transaction.atomic():
        month = _current_month()
        reserve = UsageCounter.objects.filter(
            workspace=workspace, year_month=month, uploads__lt=plan["uploads"]
        )
        if not reserve.update(uploads=F("uploads") + 1):
            # Cap reached, or the month rolled over since the precheck.
            UsageCounter.objects.get_or_create(workspace=workspace, year_month=month)
            if not reserve.update(uploads=F("uploads") + 1):
                raise ValueError("monthly_cap_reached")
        Workspace.objects.select_for_update().only("id").get(pk=workspace.pk)
        artifact = Artifact(
            workspace=workspace,
            api_key=api_key,
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(data),
            expires_at=timezone.now() + timedelta(days=ttl),
            secret_flags=secret_flags,
            safety_status=safety_status,
            content_sha256=digest,
        )
        artifact.save()
        if workspace_storage_bytes(workspace) > storage_cap_bytes:
            raise ValueError("storage_cap_reached")

    # Non-persistent signal for callers: True only when the requested
    # ttl_days was not the TTL that got applied. Views surface it as
    # "ttl_adjusted": true in the 201 response.
    artifact.ttl_adjusted = ttl_adjusted
    # Non-persistent: line/offset locations for each secret flag, so agents
    # can jump to the offending line. Only computed when flags fired, and
    # capped per flag (secret_flag_locations_truncated says when).
    if secret_flags:
        locations, truncated = locate_flags(data)
    else:
        locations, truncated = {}, False
    artifact.secret_flag_locations = locations
    artifact.secret_flag_locations_truncated = truncated

    storage = None
    try:
        storage = get_storage()
        storage.save(
            artifact.token,
            io.BytesIO(data),
            mime_type,
            metadata={
                "artifact-id": str(artifact.id),
                "workspace-id": str(workspace.id),
                "expires-at": artifact.expires_at.isoformat(),
            },
        )
    except Exception as exc:
        # Log before deleting the row: delete() clears artifact.id.
        logger.exception(
            "storage save failed for artifact id=%s workspace=%s (backend=%s)",
            artifact.id,
            workspace.id,
            os.environ.get("DROP_STORAGE", "local"),
        )
        if not _save_may_have_stored(storage, exc):
            # Nothing was stored, so there are no bytes for the
            # post_delete cleanup to remove.
            mark_bytes_handled(artifact)
        # Otherwise (e.g. a timeout after R2 stored the object) the
        # post_delete cleanup deletes whatever may have been stored.
        artifact.delete()
        # Give back the reserved upload: nothing was stored.
        UsageCounter.objects.filter(
            workspace=workspace, year_month=month, uploads__gt=0
        ).update(uploads=F("uploads") - 1)
        raise StorageError("storage_unavailable")

    return artifact


def _save_may_have_stored(storage, exc):
    check = getattr(storage, "save_may_have_stored", None)
    return bool(check is not None and check(exc))


def _record_safety_block(
    *, workspace, api_key, digest, verdict, uploader_ip, user_agent, enforce
):
    """Record a block verdict and contain the uploader.

    Always metadata-only: the event row never holds file bytes. In
    enforce mode the API key is suspended immediately and a Tier-1
    alert is emitted; in log mode nothing is contained.
    """
    action_taken = "blocked" if enforce else "logged"
    SafetyEvent.objects.create(
        workspace=workspace,
        api_key=api_key,
        artifact=None,  # refused bytes never persist; there is no artifact
        signal=verdict.signal,
        score=verdict.score,
        content_sha256=digest,
        action_taken=action_taken,
        # Capped like user_agent: an overlong value must not fail this
        # insert and skip the containment below.
        uploader_ip=(uploader_ip or "")[:64],
        user_agent=(user_agent or "")[:512],
    )
    logger.warning(
        "safety %s: signal=%s workspace=%s key=%s",
        action_taken,
        verdict.signal,
        workspace.id,
        api_key.key_prefix if api_key else None,
    )
    if not enforce:
        return
    if api_key is not None:
        api_key.is_active = False
        api_key.save(update_fields=["is_active"])
    _write_safety_alert(
        subject="SAFETY-CRITICAL",
        body=(
            f"signal={verdict.signal} action=blocked "
            f"workspace={workspace.id} "
            f"key={api_key.key_prefix if api_key else None} "
            f"sha256={digest}"
        ),
    )


def _write_safety_alert(*, subject, body):
    """Emit a Tier-1 safety alert.

    Writes to VEILFILE_ALERT_DIR when configured (the org bus alerts
    directory in dev); always logs at CRITICAL so the event is visible
    even without a bus path configured.
    """
    logger.critical("SAFETY ALERT [%s] %s", subject, body)
    alert_dir = os.environ.get("VEILFILE_ALERT_DIR")
    if not alert_dir:
        return
    try:
        os.makedirs(alert_dir, exist_ok=True)
        stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(alert_dir, f"{stamp}-safety-critical.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# {subject}\n\n{body}\n")
    except OSError:
        logger.exception("failed to write safety alert file")


# revoke_artifact result when the link is dead but the bytes are not yet
# deleted. Truthy, so callers that only check "was it revoked" still work.
REVOKE_PENDING_DELETE = "pending_delete"


def revoke_artifact(workspace, artifact_id):
    """Revoke an artifact: its link stops working and its file and row are
    deleted.

    Returns False when the artifact is missing or belongs to another
    workspace, True when the file and row are gone, and
    ``REVOKE_PENDING_DELETE`` when the storage delete failed. In that case
    the row is kept, already expired, so the link 404s and the sweeper (or
    another revoke) retries the delete; the bytes are never lost track of.
    """
    try:
        artifact = Artifact.objects.get(id=artifact_id, workspace=workspace)
    except Artifact.DoesNotExist:
        return False
    # Kill the link first, so it is dead even while storage is slow or down.
    now = timezone.now()
    Artifact.objects.filter(id=artifact.id, expires_at__gt=now).update(expires_at=now)
    try:
        get_storage().delete(artifact.token)
    except Exception as exc:
        logger.error(
            "revoke: storage delete failed for artifact id=%s workspace=%s "
            "(%s: %s); row kept expired, sweeper will retry",
            artifact.id,
            workspace.id,
            type(exc).__name__,
            exc,
        )
        return REVOKE_PENDING_DELETE
    mark_bytes_handled(artifact)
    artifact.delete()
    return True
