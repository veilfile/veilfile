"""Content-safety scanning seam for uploaded artifacts.

Phase 0: schema and seam, zero enforcement by default. ``screen_bytes``
computes a verdict from local signals (magic-byte verification happens in
the upload path; blocked-hash matching lives here). The
``VEILFILE_SAFETY_MODE`` environment variable controls behavior:

- ``off`` (default): the seam is inert — ``screen_bytes`` is not called.
- ``log``: verdicts are computed and recorded; uploads always proceed.
- ``enforce``: ``block`` verdicts refuse the upload before persistence.

Only verdict metadata ever leaves this module — never file bytes, never
content hashes of non-matching uploads.
"""

import hashlib
import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_LOG = "log"
MODE_ENFORCE = "enforce"
VALID_MODES = (MODE_OFF, MODE_LOG, MODE_ENFORCE)

ACTION_ALLOW = "allow"
ACTION_BLOCK = "block"
ACTION_HOLD = "hold"

# Extensions whose content must match image magic bytes.
_IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}
# Extensions that must decode as UTF-8 text.
_TEXT_EXTS = {"txt", "md", "log", "json", "har"}

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_GIF_MAGICS = (b"GIF87a", b"GIF89a")
_PDF_MAGIC = b"%PDF-"


@dataclass
class SafetyVerdict:
    """The outcome of screening one upload's bytes."""

    action: str = ACTION_ALLOW  # allow | block | hold
    signal: str = ""  # e.g. "blocked_hash_match", "scanner_error"
    score: float = 0.0
    latency_ms: float = 0.0
    detail: str = ""  # internal only; never exposed to clients


def safety_mode() -> str:
    """Current safety mode, defaulting to inert when unset or invalid."""
    mode = os.environ.get("VEILFILE_SAFETY_MODE", MODE_OFF).strip().lower()
    return mode if mode in VALID_MODES else MODE_OFF


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def detect_image_kind(data: bytes):
    """Return the image kind suggested by magic bytes, or None.

    ``jpg`` covers ``jpeg``. WebP is RIFF....WEBP. Returns None when the
    bytes match no known image type.
    """
    if data.startswith(_PNG_MAGIC):
        return "png"
    if data.startswith(_JPEG_MAGIC):
        return "jpg"
    if data.startswith(_GIF_MAGICS):
        return "gif"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "webp"
    return None


def verify_magic_bytes(data: bytes, ext: str) -> bool:
    """Check that ``data`` plausibly matches the claimed extension.

    Images must carry image magic bytes (any allowed image kind — a JPEG
    named ``.png`` is benign). PDFs must start with ``%PDF-``. Text types
    must decode as UTF-8. Anything else fails closed.
    """
    ext = (ext or "").lower()
    if ext in _IMAGE_EXTS:
        return detect_image_kind(data) is not None
    if ext == "pdf":
        return data.startswith(_PDF_MAGIC)
    if ext in _TEXT_EXTS:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return True
    return False


def _blocked_hash_match(digest: str) -> bool:
    # Local import: keeps module import light and avoids circulars.
    from .models import BlockedHash

    return BlockedHash.objects.filter(sha256=digest).exists()


def screen_bytes(data: bytes, mime_type: str) -> SafetyVerdict:
    """Screen one upload's bytes and return a verdict.

    Phase 0 signals: exact SHA-256 match against the local blocked-hash
    table (images and documents alike). Perceptual hashing (PDQ) and
    external lookups plug in here in later phases. Never raises: a
    scanner failure degrades to ``allow`` with ``signal="scanner_error"``
    so a down scanner cannot become a denial of service.
    """
    started = time.perf_counter()
    try:
        digest = content_sha256(data)
        if _blocked_hash_match(digest):
            return SafetyVerdict(
                action=ACTION_BLOCK,
                signal="blocked_hash_match",
                score=1.0,
                latency_ms=(time.perf_counter() - started) * 1000,
                detail="exact sha256 match against blocked-hash table",
            )
        return SafetyVerdict(
            action=ACTION_ALLOW,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
    except Exception as exc:  # noqa: BLE001 — scanner must never break uploads
        logger.exception("safety screen failed: %s", type(exc).__name__)
        return SafetyVerdict(
            action=ACTION_ALLOW,
            signal="scanner_error",
            latency_ms=(time.perf_counter() - started) * 1000,
            detail=f"scanner_error:{type(exc).__name__}",
        )
