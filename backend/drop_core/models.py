"""Veilfile core models: workspaces, API keys, artifacts, usage counters."""

import hashlib
import secrets as stdlib_secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone


FILE_VISIBILITY_MEMBERS_CHOOSE = "members_choose"
FILE_VISIBILITY_ALWAYS_TEAM = "always_team"
FILE_VISIBILITY_ALWAYS_ONLY_ME = "always_only_me"


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
    # Set by a staff action. While set, every key, token, upload URL and device login of the team is refused, and
    # every link of the team answers as a missing file does.
    suspended_at = models.DateTimeField(null=True, blank=True)
    # Whether a new file starts shared with the team or only with its uploader. Read under the workspace lock at upload, and
    # changing it never relabels a file already stored.
    file_visibility = models.CharField(
        max_length=16, default=FILE_VISIBILITY_MEMBERS_CHOOSE, db_default=FILE_VISIBILITY_MEMBERS_CHOOSE
    )

    def __str__(self):
        return f"{self.name} ({self.plan})"


def get_or_create_workspace(user):
    """Return the user's workspace, creating it on first use.

    Only signup and current_membership call it."""
    workspace = Workspace.objects.filter(owner=user).order_by("created_at").first()
    if workspace is None:
        workspace = Workspace.objects.create(
            owner=user, name=f"{user.get_username()}'s workspace"
        )
    return workspace


VISIBILITY_ONLY_ME = "only_me"
VISIBILITY_TEAM = "team"
VISIBILITY_CHOICES = [
    (VISIBILITY_ONLY_ME, "Only me"),
    (VISIBILITY_TEAM, "Team"),
]


class Membership(models.Model):
    """A person's place in a workspace. The workspace's owner is its one
    current administrator, unless they have moved to another place; other
    people are members. Rows are never
    deleted by application code: removing a person sets ``removed_at``."""

    ADMINISTRATOR = "administrator"
    MEMBER = "member"
    ROLE_CHOICES = [
        (ADMINISTRATOR, "Administrator"),
        (MEMBER, "Member"),
    ]

    workspace = models.ForeignKey(
        Workspace, related_name="memberships", on_delete=models.CASCADE
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="memberships", on_delete=models.CASCADE
    )
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=MEMBER)
    created_at = models.DateTimeField(auto_now_add=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    # Per-member allowances; NULL means no allowance.
    upload_allowance = models.PositiveIntegerField(null=True, blank=True)
    storage_allowance_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    key_allowance = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "user"], name="membership_one_per_person"
            ),
            models.UniqueConstraint(
                fields=["workspace"],
                condition=models.Q(role="administrator", removed_at__isnull=True),
                name="membership_one_administrator",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(role="member", removed_at__isnull=True),
                name="membership_one_team",
            ),
        ]

    @property
    def is_current(self):
        return self.removed_at is None

    @property
    def is_administrator(self):
        return self.role == self.ADMINISTRATOR

    def __str__(self):
        return f"{self.user_id} in {self.workspace_id} ({self.role})"


class NoCurrentAdministrator(Exception):
    """The workspace's administrator row is closed, so nothing may be written for it."""


def _administrator_row(workspace):
    membership, _ = Membership.objects.get_or_create(
        workspace=workspace,
        user_id=workspace.owner_id,
        defaults={"role": Membership.ADMINISTRATOR},
    )
    return membership


def administrator_of(workspace):
    """The workspace's current administrator, created if the row is missing."""
    membership = _administrator_row(workspace)
    if membership.removed_at is not None:
        raise NoCurrentAdministrator(workspace.pk)
    return membership


def current_membership(user, *, reopen=True):
    """The one place this person is now: a team beats an own workspace, then the oldest first.

    A person with no current row gets their own workspace back, so that leaving
    or being removed strands nobody. Reopening a closed row is for a signed-in
    person. A credential passes reopen=False and gets None for a closed row, so
    that a token cannot undo a closure. Reopening first stops the row's keys, and
    the device logins and upload URLs still waiting from before it closed.
    A person with no rows at all still gets a first workspace, with either value.
    It can write, so call it before any transaction, as with administrator_of.
    """
    rows = list(
        Membership.objects.filter(user=user, removed_at__isnull=True)
        .select_related("workspace")
        .order_by("workspace__created_at", "pk")
    )
    for row in rows:
        if row.role == Membership.MEMBER:
            return row
    if rows:
        return rows[0]
    workspace = get_or_create_workspace(user)
    row = _administrator_row(workspace)
    if row.removed_at is not None:
        if not reopen:
            return None
        # Stop these first, so nothing made before the row closed comes back with it.
        with transaction.atomic():
            APIKey.objects.filter(member=row, is_active=True).update(is_active=False)
            PendingUpload.objects.filter(member=row, used=False).update(used=True)
            DeviceCode.objects.filter(
                workspace=workspace, approved_by=user, status=DeviceCode.STATUS_APPROVED
            ).update(status=DeviceCode.STATUS_DENIED)
            Membership.objects.filter(pk=row.pk).update(removed_at=None)
        row.removed_at = None
    row.workspace = workspace
    return row


def has_other_members(workspace):
    """Whether more than one person is currently in the workspace."""
    return Membership.objects.filter(workspace=workspace, removed_at__isnull=True).count() > 1


def current_member(workspace, user):
    """The user's current membership of the workspace, or None."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if user.pk == workspace.owner_id:
        membership = _administrator_row(workspace)
        return membership if membership.removed_at is None else None
    return Membership.objects.filter(
        workspace=workspace, user=user, removed_at__isnull=True
    ).first()


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
    # The person who made the key. After migration 0014 no key names no
    # member, and a stray key with none is refused. The field stays
    # blank=True only so that existing rows and admin forms load. RESTRICT:
    # deleting a user still removes the whole account, but a membership row
    # alone cannot be deleted under its keys.
    member = models.ForeignKey(
        Membership,
        related_name="api_keys",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
    )

    def __str__(self):
        return f"{self.name} ({self.key_prefix}…)"

    @classmethod
    def create_key(cls, workspace, name, member=None):
        """Create a key, returning ``(instance, raw_key)``. The key belongs
        to ``member``, or to the account's administrator when none is given.
        The raw key is unrecoverable after this call; only its hash is
        persisted."""
        raw = cls.KEY_PREFIX + stdlib_secrets.token_urlsafe(24)
        instance = cls.objects.create(
            workspace=workspace,
            name=name,
            key_prefix=raw[:12],
            key_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            member=member or administrator_of(workspace),
        )
        return instance, raw

    @property
    def acting_member(self):
        """The membership the key acts as."""
        return self.member

    @property
    def person(self):
        """The user the key acts as."""
        return self.acting_member.user

    @classmethod
    def verify(cls, raw):
        """Return the active APIKey for ``raw``, or None. Fast-path rejects
        anything that isn't a ``vlf_`` key without touching the database.
        A key verifies while its person is active and still a member, and its team is not suspended."""
        if not isinstance(raw, str) or not raw.startswith(cls.KEY_PREFIX):
            return None
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        try:
            return cls.objects.select_related("member__user").get(
                key_hash=digest,
                is_active=True,
                member__user__is_active=True,
                member__removed_at__isnull=True,
                workspace__suspended_at__isnull=True,
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
    # The person who uploaded the file. After migration 0014 no file names no
    # member. The field stays blank=True only so that existing rows and admin
    # forms load. RESTRICT, so a file never loses its uploader.
    member = models.ForeignKey(
        Membership,
        related_name="artifacts",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
    )
    # Files from before an account is a team are only for their uploader.
    visibility = models.CharField(
        max_length=8,
        choices=VISIBILITY_CHOICES,
        default=VISIBILITY_ONLY_ME,
        db_default=VISIBILITY_ONLY_ME,
    )
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
    # The person whose credential minted the URL. After migration 0014 no
    # upload URL names no member, and a stray URL with none is refused. The
    # field stays blank=True only so that existing rows and admin forms load.
    # CASCADE, as for api_key: a URL dies with its minter.
    member = models.ForeignKey(
        Membership,
        related_name="pending_uploads",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )

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


class MemberUsageCounter(models.Model):
    """Per-member monthly upload counter for allowances. It has no foreign
    key to the workspace, so inserting a row never touches the workspace row."""

    member = models.ForeignKey(
        Membership, related_name="usage_counters", on_delete=models.CASCADE
    )
    year_month = models.CharField(max_length=7)  # e.g. '2026-10'
    uploads = models.IntegerField(default=0)

    class Meta:
        unique_together = [("member", "year_month")]

    def __str__(self):
        return f"{self.member_id} {self.year_month}: {self.uploads}"


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
    member = models.ForeignKey(
        Membership,
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


class Invitation(models.Model):
    """An administrator's invitation of one address to the team. While it waits and has not expired, it holds a seat.

    Only the hash of the link's token is stored. An invitation ends in exactly one of accepted, declined or revoked;
    an expired one is closed as revoked the next time its team invites."""

    TTL = timedelta(days=7)

    workspace = models.ForeignKey(Workspace, related_name="invitations", on_delete=models.CASCADE)
    email = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, related_name="+", on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "email"],
                condition=models.Q(accepted_at__isnull=True, declined_at__isnull=True, revoked_at__isnull=True),
                name="invitation_one_waiting",
            ),
        ]

    def __str__(self):
        return f"{self.email} to {self.workspace_id}"
