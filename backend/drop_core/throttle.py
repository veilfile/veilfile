"""Cache-backed throttles for the interactive account flows: login (site
and admin), the password checks on the email-change and password-change
forms, password reset, and signup per IPv6 /64.

Counters live in Django's cache, so they are shared across workers once
production runs a shared cache backend (per-process with LocMemCache).
A cache outage fails open with a warning rather than locking everyone
out or returning 500s. Throttled responses never say whether an account
exists: a locked username looks the same whether or not it is registered.
"""

import logging
import time
import unicodedata
from functools import wraps

from django.core.cache import cache
from django.shortcuts import render
from django.utils.crypto import salted_hmac

from .email_verification import (
    RESEND_MIN_INTERVAL,
    SIGNUP_MAX_PER_IP_PER_HOUR,
    client_ip,
    throttle_ip,
)

logger = logging.getLogger(__name__)

# Login: failed attempts per fixed window. Per-username catches guessing
# spread across many IPs; per-IP catches spraying across many usernames.
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES_PER_USERNAME = 10
LOGIN_MAX_FAILURES_PER_IP = 30

# Password reset: every request can send an email, which spends the shared
# SMTP quota that signup verification also depends on.
PASSWORD_RESET_WINDOW_SECONDS = 60 * 60
PASSWORD_RESET_MAX_PER_ADDRESS = 3
PASSWORD_RESET_MAX_PER_IP = 10

# Signup per IPv6 /64, next to the per-address EmailSendLog count in
# email_verification.signup_attempt_allowed, which an IPv6 client could
# dodge by rotating addresses inside its /64. (For IPv4 the address is the
# network, and that count already covers it.)
SIGNUP_WINDOW_SECONDS = 60 * 60

_DIGEST_SALT = "veilfile-throttle"


def _digest(value):
    """Keyed digest for cache keys and log lines: the raw username or
    address never lands in the cache table or the logs."""
    return salted_hmac(_DIGEST_SALT, value.strip().lower()).hexdigest()[:32]


def _username_key(username):
    """Cache key for a typed username's failure budget, or None.

    Folds the value the way Django's login form does before it looks the
    user up (strip, then NFKC), and ignores case, so look-alike spellings
    of one account (fullwidth or math-alphanumeric letters) share one
    budget instead of each getting a fresh one.
    """
    folded = unicodedata.normalize("NFKC", (username or "").strip()).strip()
    if not folded:
        return None
    return f"login-fail:user:{_digest(folded.casefold())}"


def _count(key):
    try:
        return int(cache.get(key) or 0)
    except Exception:
        logger.warning("throttle cache read failed; failing open", exc_info=True)
        return 0


def _hit(key, window_seconds):
    """Count one event in a fixed window that starts at the first event.
    Returns the new count, or 0 when the cache is unavailable.

    The window's end is stored beside the counter and re-applied after
    each increment. Django's stock DatabaseCache implements incr() as a
    get() plus a set() with the default timeout (300 s), which would
    otherwise move the expiry on every hit and shrink a 15-minute or
    hourly window to five minutes.
    """
    end_key = f"{key}:end"
    try:
        now = time.time()
        if cache.add(key, 1, timeout=window_seconds):
            cache.set(end_key, now + window_seconds, timeout=window_seconds)
            return 1
        try:
            count = cache.incr(key)
        except ValueError:
            # Expired between add() and incr(): start a fresh window.
            cache.set(key, 1, timeout=window_seconds)
            cache.set(end_key, now + window_seconds, timeout=window_seconds)
            return 1
        end = cache.get(end_key)
        if isinstance(end, (int, float)):
            remaining = int(end - now)
        else:
            remaining = window_seconds
        cache.touch(key, max(1, min(remaining, window_seconds)))
        return count
    except Exception:
        logger.warning("throttle cache write failed; failing open", exc_info=True)
        return 0


def _hit_and_log(key, limit, window_seconds, what):
    """_hit, plus one WARNING when the key reaches its limit (an abuse
    signal for ops). Keys carry an IP/network or a keyed digest, never a
    username, address or password."""
    if _hit(key, window_seconds) == limit:
        logger.warning(
            "%s throttle engaged: %s reached %d in %d min",
            what,
            key,
            limit,
            window_seconds // 60,
        )


def _delete(*keys):
    try:
        cache.delete_many(keys)
    except Exception:
        logger.warning("throttle cache delete failed", exc_info=True)


def _login_keys(ip, username):
    keys = []
    network = throttle_ip(ip)
    if network:
        keys.append((f"login-fail:ip:{network}", LOGIN_MAX_FAILURES_PER_IP))
    user_key = _username_key(username)
    if user_key:
        keys.append((user_key, LOGIN_MAX_FAILURES_PER_USERNAME))
    return keys


def login_locked(ip, username):
    return any(_count(key) >= limit for key, limit in _login_keys(ip, username))


def record_login_failure(ip, username):
    for key, limit in _login_keys(ip, username):
        _hit_and_log(key, limit, LOGIN_WINDOW_SECONDS, "Login")


def clear_login_failures(username):
    """Reset that username's counter (not the IP's): after a successful
    login, and after a password reset, so the owner can always get back
    in after an attacker deliberately locked the account."""
    user_key = _username_key(username)
    if user_key:
        _delete(user_key, f"{user_key}:end")


def wait_in_words(seconds):
    """How long a visitor is asked to wait, as words for the throttled page: "1 minute", "15 minutes", "60 minutes",
    "24 hours". Whole minutes (at least one) under two hours, then whole hours."""
    minutes = max(1, seconds // 60)
    if minutes < 120:
        return f"{minutes} minute" + ("" if minutes == 1 else "s")
    return f"{minutes // 60} hours"


def throttled_response(request, title, message, retry_after_seconds, sign_in_links=True):
    """The 429 page, with its Retry-After.

    Everything the page shows is fixed by the throttle that refuses (its title, its message, how long it asks for), never
    by the visitor, the account or the address, so two visitors refused for the same reason get the same bytes.
    `sign_in_links` says which secondary links the page offers: Log in and Forgot password? (the default, for a visitor who
    is signed out) or Back to dashboard (for one who is signed in, who has no use for the sign-in page).
    """
    response = render(
        request,
        "registration/throttled.html",
        {
            "title": title,
            "message": message,
            "retry_wait": wait_in_words(retry_after_seconds),
            "sign_in_links": sign_in_links,
            # The page of a throttle, whatever address was asked for: no registry or dashboard entry (so no family to change in place to), and the
            # ordinary menu, with no Sign out button and no token (shell_menu).
            "shell_page": None,
            "plain_menu": True,
        },
        status=429,
    )
    response["Retry-After"] = str(retry_after_seconds)
    return response


def login_throttled_response(request):
    return throttled_response(
        request,
        "Too many sign-in attempts",
        "Sign-in is paused for this account or network after repeated "
        "failed attempts.",
        LOGIN_WINDOW_SECONDS,
    )


def throttle_login(view):
    """Wrap a login view (Django's LoginView or the admin login) with
    per-username and per-IP failure limits.

    A POST that re-renders the form (200) is a failed attempt; a redirect
    is a successful one. While locked, the view is not called at all, so
    even the correct password is refused until the window passes.
    """

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.method != "POST":
            return view(request, *args, **kwargs)
        ip = client_ip(request)
        username = request.POST.get("username", "")
        if login_locked(ip, username):
            return login_throttled_response(request)
        response = view(request, *args, **kwargs)
        if response.status_code == 200:
            record_login_failure(ip, username)
        elif 300 <= response.status_code < 400:
            clear_login_failures(username)
        return response

    return wrapped


def password_reset_ip_allowed(ip):
    network = throttle_ip(ip)
    return not network or _count(f"pwreset:ip:{network}") < PASSWORD_RESET_MAX_PER_IP


def password_reset_address_allowed(email):
    return _count(f"pwreset:addr:{_digest(email)}") < PASSWORD_RESET_MAX_PER_ADDRESS


def record_password_reset(ip, email):
    network = throttle_ip(ip)
    if network:
        _hit_and_log(
            f"pwreset:ip:{network}",
            PASSWORD_RESET_MAX_PER_IP,
            PASSWORD_RESET_WINDOW_SECONDS,
            "Password reset",
        )
    _hit_and_log(
        f"pwreset:addr:{_digest(email)}",
        PASSWORD_RESET_MAX_PER_ADDRESS,
        PASSWORD_RESET_WINDOW_SECONDS,
        "Password reset",
    )


def password_reset_throttled_response(request):
    return throttled_response(
        request,
        "Too many password reset requests",
        "Password reset has been requested too many times from this network or for this "
        "address.",
        PASSWORD_RESET_WINDOW_SECONDS,
    )


def _ipv6_network(ip):
    """The /64 an IPv6 client is throttled under; None for IPv4."""
    network = throttle_ip(ip)
    return network if network and "/" in network else None


def signup_network_allowed(ip):
    network = _ipv6_network(ip)
    return not network or _count(f"signup:net:{network}") < SIGNUP_MAX_PER_IP_PER_HOUR


def record_signup_attempt(ip):
    network = _ipv6_network(ip)
    if network:
        _hit_and_log(
            f"signup:net:{network}",
            SIGNUP_MAX_PER_IP_PER_HOUR,
            SIGNUP_WINDOW_SECONDS,
            "Signup",
        )


def signup_throttled_response(request):
    """The 429 for a signup from an address (or IPv6 network) that has tried too often in the last hour."""
    return throttled_response(
        request,
        "Too many signup attempts",
        "Signup is paused for this address after repeated attempts.",
        SIGNUP_WINDOW_SECONDS,
    )


# How long each limit of email_verification.resend_allowed() asks a visitor to wait, by the reason it returns.
VERIFICATION_RETRY_AFTER = {
    "too_soon": int(RESEND_MIN_INTERVAL.total_seconds()),
    "account_limit": 60 * 60,
    "address_limit": 24 * 60 * 60,
}


def verification_throttled_response(request, reason):
    """The 429 for a verification email that resend_allowed() refused, `reason` being what it returned.

    "too_soon" (the visitor asked a moment ago) has words of its own; the hourly limit of the account and the daily
    limit of the address read alike, each with the wait it asks for. The page holds nothing of the account or the
    address, so it is the same for every visitor who is refused for the same reason. The view is login_required, so the
    visitor is signed in: the page offers the dashboard, not the sign-in page.
    """
    wait = VERIFICATION_RETRY_AFTER.get(reason, VERIFICATION_RETRY_AFTER["account_limit"])
    if reason == "too_soon":
        return throttled_response(
            request,
            "Verification email already sent",
            "A verification email went out a moment ago. Check your inbox and your spam folder.",
            wait,
            sign_in_links=False,
        )
    return throttled_response(
        request,
        "Too many verification emails",
        "Verification emails are paused for a while after repeated requests.",
        wait,
        sign_in_links=False,
    )
