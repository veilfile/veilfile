"""Email verification for signups: tokens, sending, and abuse throttling.

No new dependencies: tokens use django.core.signing, mail goes through
the configured email backend (console locally, Gmail SMTP in prod).
"""

import ipaddress
import logging
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import EmailSendLog, EmailVerification

logger = logging.getLogger(__name__)

SIGNER_SALT = "veilfile-email-verification"
TOKEN_MAX_AGE = 60 * 60 * 24 * 3  # verification links live 3 days

# Abuse throttling knobs.
SIGNUP_MAX_PER_IP_PER_HOUR = 10
RESEND_MIN_INTERVAL = timedelta(seconds=60)
RESEND_MAX_PER_ACCOUNT_PER_HOUR = 5
RESEND_MAX_PER_ADDRESS_PER_DAY = 5


def _signer():
    return signing.TimestampSigner(salt=SIGNER_SALT)


def email_digest(email):
    """Stable keyed digest of an address, so tokens never carry the address.
    Keyed (SECRET_KEY) so a leaked link cannot be dictionary-checked
    against guessable addresses."""
    return salted_hmac(SIGNER_SALT + ":email", email.strip().lower()).hexdigest()[:32]


def password_fingerprint(user):
    """Keyed digest of the password hash: any password change (reset or
    change) invalidates outstanding verification links, so a link minted
    from a hijacked session dies when the owner recovers the account."""
    return salted_hmac(SIGNER_SALT, user.password or "").hexdigest()[:16]


def make_verification_token(user, email):
    """Return a signed, timestamped, URL-safe token binding user pk to a
    digest of the address and the current password. The raw address never
    appears in the link, so any valid address (including
    ``ops/billing@...``) routes cleanly."""
    return signing.dumps(
        {"u": user.pk, "e": email_digest(email), "p": password_fingerprint(user)},
        salt=SIGNER_SALT,
    )


def parse_verification_token(token, max_age=None):
    """Return ((user_pk, email_digest, password_fingerprint), None) or
    (None, 'expired'|'invalid'). The fingerprint is None for legacy tokens.

    Also accepts the legacy ``<pk>:<email>`` format so links mailed before
    the format change keep working until they expire.
    """
    if max_age is None:
        max_age = TOKEN_MAX_AGE
    try:
        payload = signing.loads(token, salt=SIGNER_SALT, max_age=max_age)
    except signing.SignatureExpired:
        return None, "expired"
    except Exception:
        # BadSignature, or a legacy token: same salt, so its signature
        # checks out but its body is not base64 JSON.
        payload = None
    if isinstance(payload, dict):
        pk, digest, fingerprint = payload.get("u"), payload.get("e"), payload.get("p")
        if pk is None or not isinstance(digest, str) or not digest:
            return None, "invalid"
        if not isinstance(fingerprint, str) or not fingerprint:
            return None, "invalid"
        return (pk, digest, fingerprint), None
    try:
        value = _signer().unsign(token, max_age=max_age)
    except signing.SignatureExpired:
        return None, "expired"
    except signing.BadSignature:
        return None, "invalid"
    pk, _, email = value.partition(":")
    if not pk or not email:
        return None, "invalid"
    return (pk, email_digest(email), None), None


def is_email_verified(user):
    """True only when the user has a verified EmailVerification row."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    try:
        return user.email_verification.is_verified
    except EmailVerification.DoesNotExist:
        return False


def get_or_create_verification(user, email):
    """Return the user's EmailVerification row for ``email``.

    Changing the address resets verification state: the old address's
    token can no longer verify, and new keys stay blocked until the new
    address is verified.
    """
    email = email.strip().lower()
    record, _ = EmailVerification.objects.get_or_create(
        user=user, defaults={"email": email}
    )
    if record.email.lower() != email:
        record.email = email
        record.is_verified = False
        record.verified_at = None
        record.save(update_fields=["email", "is_verified", "verified_at"])
    return record


# Proxy hops inside the hosting platform (Render's private network). They
# are appended after the public hops and never count toward the trusted
# hop budget.
_INTERNAL_PROXY_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)

# Cloudflare's published edge ranges (https://www.cloudflare.com/ips-v4 and
# /ips-v6, fetched 2026-10-08). The client IP header is trusted only from
# these peers. Cloudflare changes the list rarely; refresh it if it does.
_CLOUDFLARE_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "173.245.48.0/20",
        "103.21.244.0/22",
        "103.22.200.0/22",
        "103.31.4.0/22",
        "141.101.64.0/18",
        "108.162.192.0/18",
        "190.93.240.0/20",
        "188.114.96.0/20",
        "197.234.240.0/22",
        "198.41.128.0/17",
        "162.158.0.0/15",
        "104.16.0.0/13",
        "104.24.0.0/14",
        "172.64.0.0/13",
        "131.0.72.0/22",
        "2400:cb00::/32",
        "2606:4700::/32",
        "2803:f800::/32",
        "2405:b500::/32",
        "2405:8100::/32",
        "2a06:98c0::/29",
        "2c0f:f248::/32",
    )
)

_warned_untrusted_client_ip_header = False


def _parse_ip(value):
    try:
        ip = ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None
    # A zone index ('fe80::1%eth0') never belongs to a client address seen
    # over the internet; refusing it keeps '%...' text out of audit fields.
    if getattr(ip, "scope_id", None):
        return None
    return ip


def _is_internal_proxy(ip):
    return any(ip in net for net in _INTERNAL_PROXY_NETWORKS)


def _is_cloudflare(ip):
    return any(ip in net for net in _CLOUDFLARE_NETWORKS)


def _public_forwarded_hops(request):
    """X-Forwarded-For entries with trailing internal hops removed. The last
    entry left is the public peer that connected to the platform's proxy:
    the proxy appends it, so a client cannot choose it."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if not forwarded.strip():
        return []
    entries = [entry.strip() for entry in forwarded.split(",")]
    while entries:
        tail = _parse_ip(entries[-1])
        if tail is None or not _is_internal_proxy(tail):
            break
        entries.pop()
    return entries


def client_ip(request):
    """The client's IP address for per-IP throttles, or None.

    If ``VEILFILE_CLIENT_IP_HEADER`` (default ``CF-Connecting-IP``) holds a
    valid IP and the request reached the platform from a Cloudflare edge
    address, that wins: Cloudflare overwrites any client-supplied copy. A
    request from anywhere else (a direct-to-origin path) could set the
    header itself, so it is ignored there. Otherwise, the hop-based
    X-Forwarded-For logic:

    Render terminates TLS behind Cloudflare. Cloudflare appends the address
    it saw to X-Forwarded-For, then Render's proxy appends the Cloudflare
    edge it saw, so the header reads ``<anything the client sent>,
    <visitor>, <cloudflare edge>`` (sometimes followed by Render-internal
    private hops). Only entries appended by trusted proxies are believable;
    the client controls everything to their left and can never append to
    the right.

    So: skip trailing internal (private-network) hops, then take the entry
    ``VEILFILE_TRUSTED_PROXY_HOPS`` positions from the right, provided the
    last of them is a Cloudflare edge address. When it is not, the request
    reached the platform without Cloudflare and only the last entry (the
    one the platform's proxy appended) is the client's: the entries to its
    left are whatever the client typed, so the hop count is not applied.
    A shorter chain has no client-supplied entries, so its leftmost entry
    is used. Anything that does not parse as an IP falls back to
    REMOTE_ADDR. The result is always a normalized IP string or None, never
    header text.
    """
    global _warned_untrusted_client_ip_header
    remote = _parse_ip(request.META.get("REMOTE_ADDR"))
    fallback = str(remote) if remote is not None else None
    entries = _public_forwarded_hops(request)
    header = (getattr(settings, "VEILFILE_CLIENT_IP_HEADER", "") or "").strip()
    if header:
        meta_key = "HTTP_" + header.upper().replace("-", "_")
        header_ip = _parse_ip(request.META.get(meta_key))
        peer = _parse_ip(entries[-1]) if entries else None
        if header_ip is not None and peer is not None and _is_cloudflare(peer):
            return str(header_ip)
        if header_ip is not None and not _warned_untrusted_client_ip_header:
            # Once per process: either traffic is reaching the origin
            # without Cloudflare, or the proxy chain changed.
            _warned_untrusted_client_ip_header = True
            logger.warning(
                "client_ip: ignoring %s because the request did not arrive from "
                "a Cloudflare address; using X-Forwarded-For instead",
                header,
            )
    hops = getattr(settings, "VEILFILE_TRUSTED_PROXY_HOPS", 2)
    if hops <= 0 or not entries:
        return fallback
    peer = _parse_ip(entries[-1])
    if hops > 1 and (peer is None or not _is_cloudflare(peer)):
        # More than one public hop is only believable when the last one is
        # Cloudflare's edge. Without it the request did not come through
        # Cloudflare (it reached the platform directly), the last entry is
        # the address the platform's proxy appended, and every entry left of
        # it was typed by the client: counting ``hops`` places from the right
        # would land on one of those and let the client pick its own identity.
        hops = 1
    candidate = _parse_ip(entries[max(len(entries) - hops, 0)])
    if candidate is None:
        return fallback
    return str(candidate)


def throttle_ip(ip):
    """The key a per-IP throttle counts ``ip`` under, or None.

    IPv4 addresses count as themselves. An IPv6 address counts as its /64:
    one subscriber or VM routinely holds a whole /64, so rotating addresses
    inside it must not reset the limits. Audit fields (EmailSendLog,
    SafetyEvent) keep the exact address from client_ip.
    """
    parsed = _parse_ip(ip)
    if parsed is None:
        return None
    if parsed.version == 6:
        if parsed.ipv4_mapped is not None:
            return str(parsed.ipv4_mapped)
        return str(ipaddress.ip_network(f"{parsed}/64", strict=False))
    return str(parsed)


# The privacy policy promises to delete send-log IP addresses after this
# many days. The throttles only look back a day, so older IPs serve no purpose.
EMAIL_IP_RETENTION_DAYS = 90


def clear_old_send_ips(older_than_days=EMAIL_IP_RETENTION_DAYS):
    """Blank the IP address on EmailSendLog rows older than the retention
    window, keeping the rest of the row. Returns the number of rows changed."""
    cutoff = timezone.now() - timedelta(days=older_than_days)
    return EmailSendLog.objects.filter(
        created_at__lt=cutoff, ip_address__isnull=False
    ).update(ip_address=None)


def signup_attempt_allowed(ip_address):
    """Per-IP throttle on the signup endpoint (it sends email)."""
    if not ip_address:
        return True
    cutoff = timezone.now() - timedelta(hours=1)
    attempts = (
        EmailSendLog.objects.filter(
            purpose=EmailSendLog.PURPOSE_SIGNUP_ATTEMPT,
            ip_address=ip_address,
            created_at__gte=cutoff,
        ).count()
    )
    return attempts < SIGNUP_MAX_PER_IP_PER_HOUR


def resend_allowed(user, email):
    """Check the resend throttles. Returns (allowed, reason)."""
    now = timezone.now()
    try:
        record = user.email_verification
    except EmailVerification.DoesNotExist:
        record = None
    if (
        record
        and record.last_sent_at
        and now - record.last_sent_at < RESEND_MIN_INTERVAL
    ):
        return False, "too_soon"
    hour_ago = now - timedelta(hours=1)
    recent_for_account = EmailSendLog.objects.filter(
        purpose=EmailSendLog.PURPOSE_VERIFICATION_SENT,
        user=user,
        created_at__gte=hour_ago,
    ).count()
    if recent_for_account >= RESEND_MAX_PER_ACCOUNT_PER_HOUR:
        return False, "account_limit"
    day_ago = now - timedelta(days=1)
    recent_for_address = EmailSendLog.objects.filter(
        purpose=EmailSendLog.PURPOSE_VERIFICATION_SENT,
        email__iexact=email.strip().lower(),
        created_at__gte=day_ago,
    ).count()
    if recent_for_address >= RESEND_MAX_PER_ADDRESS_PER_DAY:
        return False, "address_limit"
    return True, ""


def send_verification_email(user, request):
    """Send (or re-send) the verification link to the user's address.

    Always targets the address on the user's own EmailVerification row —
    one account can never trigger mail to an address it doesn't hold.
    Returns (sent, reason); reason is '' on success.
    """
    # Read the row fresh: user.email_verification may be a cached instance
    # from before an address change, which would mail the old address.
    record = EmailVerification.objects.filter(user=user).first()
    if record is None:
        return False, "no_address"
    allowed, reason = resend_allowed(user, record.email)
    if not allowed:
        return False, reason
    token = make_verification_token(user, record.email)
    verify_url = request.build_absolute_uri(
        reverse("verify-email", args=[token])
    )
    subject = "Verify your Veilfile email"
    body = render_to_string(
        "registration/verify_email_email.txt",
        {"verify_url": verify_url, "expiry_days": TOKEN_MAX_AGE // 86400},
    )
    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [record.email],
    )
    record.last_sent_at = timezone.now()
    record.save(update_fields=["last_sent_at"])
    EmailSendLog.objects.create(
        email=record.email,
        user=user,
        ip_address=client_ip(request),
        purpose=EmailSendLog.PURPOSE_VERIFICATION_SENT,
    )
    return True, ""


def _mask_email(email):
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def send_email_change_notice(user, old_email, new_email):
    """Tell the previous (verified) address that a change was requested.

    Sent before the new address is verified, so the owner hears about a
    change made from a hijacked session while password reset still goes
    to the old address. Holds no link: the old address only needs to know.
    """
    body = render_to_string(
        "registration/email_change_notice_email.txt",
        {
            "username": user.get_username(),
            "new_email_masked": _mask_email(new_email),
            "reset_url": f"{settings.DROP_BASE_URL}{reverse('password_reset')}",
        },
    )
    send_mail(
        "Someone asked to change your Veilfile email address",
        body,
        settings.DEFAULT_FROM_EMAIL,
        [old_email],
    )
