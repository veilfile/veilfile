"""Veilfile core models: workspaces, API keys, artifacts, usage counters."""

import hashlib
import secrets as stdlib_secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Workspace(models.Model):
    """A billing/ownership boundary. One user owns one workspace in MVP."""

    PLAN_FREE = "free"
    PLAN_TEAM = "team"
    PLAN_TEAM_PLUS = "team_plus"
    PLAN_SCALE = "scale"
    PLAN_CHOICES = [
        (PLAN_FREE, "Free"),
        (PLAN_TEAM, "Team"),
        (PLAN_TEAM_PLUS, "Team Plus"),
        (PLAN_SCALE, "Scale"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="workspaces", on_delete=models.CASCADE
    )
    plan = models.CharField(max_length=16, choices=PLAN_CHOICES, default=PLAN_FREE)
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.plan})"


def get_or_create_workspace(user):
    """Return the user's workspace, creating it on first use."""
    workspace = Workspace.objects.filter(owner=user).order_by("created_at").first()
    if workspace is None:
        workspace = Workspace.objects.create(
            owner=user, name=f"{user.get_username()}'s workspace"
        )
    return workspace


class APIKey(models.Model):
    """Agent credential. Only the sha256 hash is stored; the raw key
    (``vlf_...``) is shown exactly once at creation time."""

    KEY_PREFIX = "vlf_"

    workspace = models.ForeignKey(
        Workspace, related_name="api_keys", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=255)
    key_prefix = models.CharField(max_length=32)  # first 12 chars of the raw key
    key_hash = models.CharField(max_length=64, db_index=True)  # sha256 hex
    is_active = models.BooleanField(default=True)
    # True for keys minted through the RFC 8628 device flow (``veilfile
    # login``). Device-issued keys count against plan key caps like any
    # other key (drop_core.key_caps).
    device_issued = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.key_prefix}…)"

    @classmethod
    def create_key(cls, workspace, name):
        """Create a key, returning ``(instance, raw_key)``. The raw key is
        unrecoverable after this call — only its hash is persisted."""
        raw = cls.KEY_PREFIX + stdlib_secrets.token_urlsafe(24)
        instance = cls.objects.create(
            workspace=workspace,
            name=name,
            key_prefix=raw[:12],
            key_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        )
        return instance, raw

    @classmethod
    def verify(cls, raw):
        """Return the active APIKey for ``raw``, or None. Fast-path rejects
        anything that isn't a ``vlf_`` key without touching the database.
        Keys of a deactivated (suspended) owner never verify."""
        if not isinstance(raw, str) or not raw.startswith(cls.KEY_PREFIX):
            return None
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        try:
            return cls.objects.get(
                key_hash=digest, is_active=True, workspace__owner__is_active=True
            )
        except cls.DoesNotExist:
            return None


def _new_token():
    return stdlib_secrets.token_urlsafe(32)


class Artifact(models.Model):
    """An uploaded file. The unguessable ``token`` is the bearer credential
    for the public ``/a/<token>`` URL."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace, related_name="artifacts", on_delete=models.CASCADE
    )
    api_key = models.ForeignKey(
        APIKey, related_name="artifacts", null=True, blank=True, on_delete=models.SET_NULL
    )
    filename = models.CharField(max_length=512)
    mime_type = models.CharField(max_length=128, default="application/octet-stream")
    size_bytes = models.BigIntegerField()
    token = models.CharField(max_length=64, unique=True, default=_new_token)
    expires_at = models.DateTimeField(db_index=True)
    secret_flags = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    # Content safety (Phase 0: schema + seam, enforcement behind
    # VEILFILE_SAFETY_MODE). safety_status: clear | pending | held.
    # Held artifacts are never served (serve_artifact 404s).
    safety_status = models.CharField(max_length=16, default="clear", db_index=True)
    content_sha256 = models.CharField(max_length=64, default="", db_index=True)
    # Staged for perceptual hashing; unused until the pdqhash dependency
    # is vetted and approved.
    pdq_hash = models.CharField(max_length=256, null=True, blank=True, default=None)

    def __str__(self):
        return f"{self.filename} ({self.size_bytes} bytes)"

    def is_expired(self):
        return timezone.now() >= self.expires_at


class PendingUpload(models.Model):
    """A minted one-time upload URL (MCP `create_upload_url`).

    The agent PUTs raw file bytes to ``/api/v1/uploads/<token>`` within
    15 minutes. The token is the auth (unguessable, like artifact tokens);
    the claim flips ``used`` atomically so the URL is single-use. Expired
    rows are inert (the claim query ignores them) and are deleted by the
    regular sweep.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace, related_name="pending_uploads", on_delete=models.CASCADE
    )
    # CASCADE, not SET_NULL: a URL minted by a key dies with it. A nulled
    # key would read as a legacy row and stay usable after the delete.
    api_key = models.ForeignKey(
        APIKey,
        related_name="pending_uploads",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    filename = models.CharField(max_length=512)
    # TTL already clamped to the plan max at mint time.
    ttl_days = models.PositiveIntegerField()
    ttl_adjusted = models.BooleanField(default=False)
    # on_secret chosen at mint time ("flag" | "reject"). The PUT applies
    # the stricter of this and its own ?on_secret query parameter.
    on_secret = models.CharField(max_length=8, default="flag")
    # Set when an OAuth token minted the URL (api_key is None then): the
    # PUT refuses once the owner has no live grant for this application.
    # Plain integer, not an FK: a deleted application must kill the URL.
    oauth_application_id = models.BigIntegerField(null=True, blank=True)
    token = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    used = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"pending {self.filename} (used={self.used})"


class UsageCounter(models.Model):
    """Per-workspace monthly upload counter for plan caps."""

    workspace = models.ForeignKey(
        Workspace, related_name="usage_counters", on_delete=models.CASCADE
    )
    year_month = models.CharField(max_length=7)  # e.g. '2026-10'
    uploads = models.IntegerField(default=0)

    class Meta:
        unique_together = [("workspace", "year_month")]

    def __str__(self):
        return f"{self.workspace_id} {self.year_month}: {self.uploads}"


class BlockedHash(models.Model):
    """Exact SHA-256 digests of confirmed-bad content.

    Checked before any upload persists. Seeded empty; filled from
    confirmed abuse reports. Never committed to the repo.
    """

    sha256 = models.CharField(max_length=64, unique=True, db_index=True)
    reason = models.CharField(max_length=255, default="", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"blocked {self.sha256[:12]}… ({self.reason})"


class SafetyEvent(models.Model):
    """Metadata-only record of a safety screening outcome.

    Never holds file bytes, thumbnails, or data URIs — metadata only,
    so the table is safe to query, export, and hand to a reviewer.
    """

    REVIEW_PENDING = "pending"
    REVIEW_RELEASED = "released"
    REVIEW_HELD = "keep_held"
    REVIEW_ESCALATED = "escalated"
    REVIEW_DISMISSED = "dismissed"
    REVIEW_CHOICES = [
        (REVIEW_PENDING, "Pending"),
        (REVIEW_RELEASED, "Released"),
        (REVIEW_HELD, "Keep held"),
        (REVIEW_ESCALATED, "Escalated"),
        (REVIEW_DISMISSED, "Dismissed"),
    ]

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    workspace = models.ForeignKey(
        Workspace, related_name="safety_events", on_delete=models.CASCADE
    )
    api_key = models.ForeignKey(
        APIKey,
        related_name="safety_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    artifact = models.ForeignKey(
        Artifact,
        related_name="safety_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    signal = models.CharField(max_length=64)  # e.g. blocked_hash_match
    score = models.FloatField(default=0.0)
    content_sha256 = models.CharField(max_length=64, default="", db_index=True)
    action_taken = models.CharField(max_length=32)  # blocked | logged
    uploader_ip = models.CharField(max_length=64, default="", blank=True)
    user_agent = models.CharField(max_length=512, default="", blank=True)
    review_status = models.CharField(
        max_length=16, choices=REVIEW_CHOICES, default=REVIEW_PENDING, db_index=True
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.signal} {self.action_taken} ({self.created_at:%Y-%m-%d})"


class FunnelEvent(models.Model):
    """Privacy-respecting activation funnel analytics (first-party only).

    Aggregate counts and conversion rates only. Each row holds a stage
    name, a timestamp, an optional workspace link, an optional random
    anonymous session id, and (for uploads) which number upload this was
    for the workspace. Never holds IPs, user agents, usernames, emails,
    file names, or file contents.
    """

    STAGE_CHOICES = [
        ("landing_visit", "Landing visit"),
        ("signup_started", "Signup started"),
        ("signup_completed", "Signup completed"),
        ("key_issued", "API key issued"),
        ("upload_completed", "Upload completed"),
    ]

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    stage = models.CharField(max_length=32, choices=STAGE_CHOICES, db_index=True)
    workspace = models.ForeignKey(
        Workspace,
        null=True,
        blank=True,
        related_name="funnel_events",
        on_delete=models.CASCADE,
    )
    # Random per-browser-session hex token (from the vf_funnel cookie; see
    # drop_core.funnel). Links pre-signup anonymous stages to post-signup
    # workspace stages without identifying anyone.
    anonymous_id = models.CharField(max_length=64, default="", blank=True, db_index=True)
    # For upload_completed: 1 = first upload by this workspace, 2 = second, ...
    upload_number = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.stage} ({self.created_at:%Y-%m-%d})"


class EmailVerification(models.Model):
    """Tracks whether a user's email address is verified.

    New signups must verify before API keys are issued. Pre-existing
    users have no row until they add an email; their existing keys keep
    working, but minting new keys is blocked until verified.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name="email_verification",
        on_delete=models.CASCADE,
    )
    email = models.EmailField()
    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        state = "verified" if self.is_verified else "pending"
        return f"{self.email} ({state})"


class DeviceCode(models.Model):
    """One pending RFC 8628 device authorization grant.

    Created by the unauthenticated terminal (POST /api/v1/device/code),
    approved or denied in the browser (GET/POST /device/*), then polled
    by the terminal (POST /api/v1/device/token) until the API key is
    handed over. The secret ``device_code`` is never stored — only its
    sha256. Approval only records the decision; the API key is minted by
    the first poll after approval and returned in that response, so the
    raw key is never stored anywhere (not in the database, not in a cache).
    Rows are purged an hour after they expire, or a day after for grants
    that issued a key (device.purge_stale_device_codes).
    """

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_DENIED = "denied"
    STATUS_EXPIRED = "expired"
    STATUS_CONSUMED = "consumed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_DENIED, "Denied"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_CONSUMED, "Consumed"),
    ]

    # 8 chars from the unambiguous alphabet, stored raw; displayed XXXX-XXXX.
    user_code = models.CharField(max_length=16, unique=True, db_index=True)
    device_code_hash = models.CharField(max_length=64, unique=True, db_index=True)
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True
    )
    # Set at approval time — the device is unauthenticated at initiation.
    workspace = models.ForeignKey(
        Workspace,
        null=True,
        blank=True,
        related_name="device_codes",
        on_delete=models.CASCADE,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        related_name="device_code_approvals",
        on_delete=models.SET_NULL,
    )
    # The minted key, set when the terminal's poll redeems the approval.
    api_key = models.ForeignKey(
        APIKey,
        null=True,
        blank=True,
        related_name="device_code",
        on_delete=models.SET_NULL,
    )
    # Chosen on the consent page when the workspace is at its plan's key
    # cap: this key is revoked in the same transaction that mints the new
    # one, so the active key count never exceeds the cap.
    replace_key = models.ForeignKey(
        APIKey,
        null=True,
        blank=True,
        related_name="+",
        on_delete=models.SET_NULL,
    )
    # Who asked for the grant, shown on the consent page so the user can
    # tell a request they started from one someone sent them. db_default
    # keeps the column default in the database, so the previous release's
    # INSERTs (which omit these columns) work during the deploy overlap.
    requester_ip = models.CharField(
        max_length=64, blank=True, default="", db_default=""
    )
    requester_user_agent = models.CharField(
        max_length=256, blank=True, default="", db_default=""
    )
    expires_at = models.DateTimeField(db_index=True)
    interval_seconds = models.IntegerField(default=5)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"device code {self.user_code_display} ({self.status})"

    @property
    def user_code_display(self):
        """The 8-char code formatted as XXXX-XXXX for display."""
        code = self.user_code or ""
        return f"{code[:4]}-{code[4:]}" if len(code) == 8 else code

    def is_expired(self):
        return timezone.now() > self.expires_at

    def mark_expired(self):
        # Conditional on the status this instance saw, so a stale instance
        # never overwrites a concurrent approval, denial or redemption.
        DeviceCode.objects.filter(pk=self.pk, status=self.status).update(
            status=self.STATUS_EXPIRED
        )
        self.status = self.STATUS_EXPIRED

    class Meta:
        ordering = ["-created_at"]


class EmailSendLog(models.Model):
    """Append-only log of verification emails sent and signup attempts.

    Powers abuse throttling: per-account, per-address, and per-IP limits.
    Holds no message content and no secrets.
    """

    PURPOSE_SIGNUP_ATTEMPT = "signup_attempt"
    PURPOSE_VERIFICATION_SENT = "verification_sent"
    PURPOSE_CHOICES = [
        (PURPOSE_SIGNUP_ATTEMPT, "Signup attempt"),
        (PURPOSE_VERIFICATION_SENT, "Verification email sent"),
    ]

    email = models.EmailField(default="", blank=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    purpose = models.CharField(max_length=32, choices=PURPOSE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"{self.purpose} ({self.created_at:%Y-%m-%d %H:%M})"


class LoopHeartbeat(models.Model):
    """One row: when the background loop's sweep_expired pass last finished.

    Written by ``drop_core.sweep.record_loop_pass()`` and watched by
    ``maybe_sweep()``. It lives in the database, not the cache, so a cull of
    the cache table or a cleared cache cannot lose it.
    """

    last_pass = models.DateTimeField()

    def __str__(self):
        return f"last loop pass {self.last_pass:%Y-%m-%d %H:%M}"
