#!/usr/bin/env python3
"""veilfile — command-line client for Veilfile.

Single-file, standard-library-only CLI. Talks to the RFC 8628 device flow
so `veilfile login` never asks you to paste a secret into the terminal.

    veilfile login        start the browser approval flow, store the API key
    veilfile logout       revoke the key server-side, delete the local copy
    veilfile status       confirm the stored key is valid (never prints it)
    veilfile push FILE    upload a file, print its share URL
    veilfile pull URL     download an artifact to the current directory

The key is stored in a 0600 credentials file under the platform config dir
(~/.config/veilfile on macOS/Linux, %APPDATA%/veilfile on Windows, where the
per-user ACL on %APPDATA% protects it instead of mode bits).
VEILFILE_API_KEY, when set, takes precedence over the file.
"""

import argparse
import errno
import http.client
import io
import ipaddress
import json
import mimetypes
import os
import sys
import time
import unicodedata
import webbrowser
from email.message import Message
from email.utils import collapse_rfc2231_value
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

__version__ = "0.4.0"

DEFAULT_BASE_URL = "https://veilfile.com"
_DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
_ENV_VAR = "VEILFILE_API_KEY"
_SLOW_DOWN_INCREMENT = 5
_MAX_POLL_BACKOFF = 60  # seconds; cap for transient-error backoff and Retry-After
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # server cap; checked client-side first
_CHUNK_SIZE = 65536
_NAME_MAX = 255  # longest file name most filesystems accept
# Never printed or used in a file name: control and format characters
# (escape sequences, bidi overrides), lone surrogates (unprintable), and
# the line/paragraph separators str.splitlines() breaks on.
_UNSAFE_CATEGORIES = frozenset(["Cc", "Cf", "Cs", "Zl", "Zp"])
# Names Windows maps to devices whatever the extension ("NUL.txt" is NUL).
_WINDOWS_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"]
    + [f"{port}{n}" for port in ("COM", "LPT") for n in "0123456789¹²³"]
)


# ---------------------------------------------------------------------------
# Key storage
# ---------------------------------------------------------------------------


def config_dir() -> Path:
    """Platform config dir: ~/.config/veilfile, or %APPDATA%/veilfile on Windows."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "veilfile"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "veilfile"


def credentials_path() -> Path:
    return config_dir() / "credentials"


def read_stored_key() -> Tuple[Optional[str], str]:
    """Return (key, source). Source is 'environment', 'file', or 'none'.

    The VEILFILE_API_KEY environment variable takes precedence over the file,
    matching the MCP setup docs.
    """
    env_key = (os.environ.get(_ENV_VAR) or "").strip()
    if env_key:
        return env_key, "environment"
    key = _read_file_key()
    if key:
        return key, "file"
    return None, "none"


def _read_file_key() -> Optional[str]:
    """The key in the credentials file, ignoring VEILFILE_API_KEY."""
    return _read_file_entry()[0]


def _read_file_entry() -> Tuple[Optional[str], Optional[str]]:
    """(key, base_url) from the credentials file, ignoring VEILFILE_API_KEY.

    base_url is None for files written before it was recorded.
    """
    path = credentials_path()
    if not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    key = str(data.get("api_key") or "").strip()
    base_url = data.get("base_url")
    return key or None, base_url if isinstance(base_url, str) else None


def write_stored_key(key: str, base_url: Optional[str] = None) -> Path:
    """Write the key to the credentials file with 0600 permissions.

    The key goes into a new temp file created 0600 with O_EXCL, which is
    synced and then renamed over the old file. A pre-existing file with
    looser permissions never holds the new key, and a failed write leaves
    the previous key in place. base_url records which server issued the
    key, so a later login elsewhere never sends it there.
    """
    directory = config_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        # mkdir's mode only applies when it creates the directory.
        os.chmod(str(directory), 0o700)
    except OSError:
        pass
    path = credentials_path()
    tmp_path = directory / f".credentials.{os.urandom(8).hex()}.tmp"
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        record = {"api_key": key}
        if base_url:
            record["base_url"] = base_url.rstrip("/")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp_path), str(path))
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return path


def delete_stored_key() -> bool:
    """Delete the credentials file. Returns True if one existed."""
    path = credentials_path()
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class ApiError(Exception):
    """A clean, user-facing API failure (network down, bad response, ...)."""


def _origin(url: str) -> Optional[Tuple[str, str, Optional[int]]]:
    """(scheme, host, port), or None when url cannot be parsed."""
    try:
        parsed = urlparse(url)  # raises on e.g. "https://[x"
        scheme = parsed.scheme.lower()
        port = parsed.port  # raises on a malformed or out-of-range port
    except ValueError:
        return None
    if port is None:
        port = {"http": 80, "https": 443}.get(scheme)
    return scheme, (parsed.hostname or "").lower(), port


def _redirect_allowed(old_url: str, new_url: str) -> bool:
    """A hop may keep its origin (scheme, host and port), or upgrade
    http://host/ to https://host/ on the default ports. Nothing else."""
    old, new = _origin(old_url), _origin(new_url)
    if old is None or new is None or not new[1] or new[0] not in ("http", "https"):
        return False
    if new == old:
        return True
    return old == ("http", new[1], 80) and new == ("https", old[1], 443)


class _RedirectRefused(URLError):
    """A redirect tried to leave the original origin, or to drop https."""

    def __init__(self, target: str):
        self.target = target
        super().__init__(f"refusing to follow a redirect to {target}")


class _SameHostRedirectHandler(HTTPRedirectHandler):
    """Follow redirects only on the same origin, and never from https to http.

    urllib's default handler follows a 3xx to any host and replays every
    request header there, Authorization included. That would hand the API
    key to whoever the redirect points at (another host, or another port
    on the same host), and let a redirect undo pull's host check. Each hop
    is checked against the previous one, so a chain of redirects stays on
    the original origin.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _redirect_allowed(req.full_url, newurl):
            try:
                fp.close()
            except Exception:
                pass
            new = urlparse(newurl)
            raise _RedirectRefused(f"{new.scheme}://{new.netloc}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _urlopen(request: Request, timeout: float):
    """urlopen() whose redirects are pinned by _SameHostRedirectHandler."""
    try:
        return build_opener(_SameHostRedirectHandler).open(request, timeout=timeout)
    except ValueError as exc:
        # urllib raises ValueError for a URL it cannot parse, such as a
        # redirect to "http://[": report it like any other failed request.
        raise URLError(f"invalid URL ({exc})") from exc


def _lower_headers(items) -> Dict[str, str]:
    return {str(name).lower(): str(value) for name, value in items}


def _header_int(value: str) -> Optional[int]:
    """A non-negative integer header value, or None.

    ASCII digits only: headers are decoded as latin-1, and str.isdigit()
    also accepts superscripts such as '²', which int() rejects.
    """
    value = value.strip()
    return int(value) if value.isascii() and value.isdigit() else None


def _retry_after_seconds(headers: Dict[str, str]) -> Optional[int]:
    """Retry-After in seconds, or None when absent (or an HTTP date)."""
    return _header_int(headers.get("retry-after") or "")


def _printable(text: object, limit: int = 300) -> str:
    """Server-supplied text made safe for the terminal: control and format
    characters (escape sequences, bidi overrides), lone surrogates and line
    separators removed, so it prints as one line; length capped."""
    cleaned = "".join(
        c for c in str(text) if unicodedata.category(c) not in _UNSAFE_CATEGORIES
    )
    return cleaned[:limit]


def _request(
    method: str, url: str, key: Optional[str] = None, body: Optional[dict] = None
) -> Tuple[int, object]:
    """One JSON request. Returns (status, parsed body). Raises ApiError."""
    status, _headers, parsed = _request_with_headers(method, url, key=key, body=body)
    return status, ({} if parsed is None else parsed)


def _request_with_headers(
    method: str, url: str, key: Optional[str] = None, body: Optional[dict] = None
) -> Tuple[int, Dict[str, str], object]:
    """Like _request, plus the response headers (names lower-cased).

    The parsed body is None when the response was not a JSON object or
    array (empty, an HTML error page, ...), so callers can tell a proxy's
    error page from a real API answer.
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with _urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = response.status
            response_headers = _lower_headers(response.getheaders())
    except HTTPError as exc:
        status = exc.code
        response_headers = _lower_headers((exc.headers or {}).items())
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception as read_exc:
            # Status arrived but the body did not (truncated chunked read,
            # reset connection). Treat as transient, not as an empty body —
            # an empty body would be misread as an unknown server response.
            raise ApiError(
                f"Could not read the response from {url}: {read_exc}"
            ) from read_exc
    except (
        URLError,
        OSError,
        TimeoutError,
        http.client.HTTPException,  # e.g. IncompleteRead on chunked bodies
    ) as exc:
        raise ApiError(f"Could not reach {url}: {exc}") from exc
    try:
        parsed = json.loads(raw) if raw.strip() else None
    except ValueError:
        parsed = None
    if not isinstance(parsed, (dict, list)):
        parsed = None
    return status, response_headers, parsed


def _api(
    base_url: str, method: str, path: str, key: Optional[str] = None,
    body: Optional[dict] = None,
) -> Tuple[int, object]:
    return _request(method, base_url.rstrip("/") + path, key=key, body=body)


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


def _bounded_int(value: object, default: int, low: int, high: int) -> int:
    """An integer server field clamped to at most `high`.

    Falls back to `default` when the value is missing, malformed ("5s") or
    below `low`, instead of crashing the login flow.
    """
    try:
        number = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default
    if number < low:
        return default
    return min(number, high)


def _transient_delay(wait: int, failures: int, retry_after: Optional[int]) -> int:
    """Backoff after `failures` consecutive transient errors (RFC 8628 3.5)."""
    delay = min(_MAX_POLL_BACKOFF, wait * 2 ** min(failures, 6))
    if retry_after is not None:
        delay = max(delay, min(retry_after, _MAX_POLL_BACKOFF))
    return max(wait, delay)


def _poll_for_key(
    base_url: str, device_code: str, interval: int, expires_in: int
) -> str:
    """Poll the token endpoint until the grant resolves. Returns the API key.

    Network errors, 5xx, 429 and answers that are not JSON (a proxy's HTML
    error page, say) are transient: back off, honouring Retry-After, and
    keep polling until the code expires. Raises SystemExit with a
    user-facing message on terminal states.
    """
    deadline = time.monotonic() + expires_in
    wait = max(1, interval)
    delay = wait
    failures = 0  # consecutive transient failures
    last_problem = ""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            detail = f" (Last error: {last_problem}.)" if last_problem else ""
            sys.stderr.write(
                f"The code expired before approval.{detail} "
                "Run `veilfile login` again.\n"
            )
            raise SystemExit(1)
        time.sleep(min(delay, remaining))
        delay = wait
        try:
            status, headers, body = _request_with_headers(
                "POST",
                base_url.rstrip("/") + "/api/v1/device/token",
                body={"grant_type": _DEVICE_GRANT_TYPE, "device_code": device_code},
            )
        except ApiError as exc:
            # Transient network failure mid-poll: back off and retry until
            # the grant expires rather than killing the whole login flow.
            failures += 1
            last_problem = str(exc)
            delay = _transient_delay(wait, failures, None)
            sys.stderr.write(f"{exc} — retrying in {delay}s…\n")
            continue
        if status >= 500 or status == 429 or body is None:
            # A deploy, a proxy's error page, throttling: the grant is
            # still alive, so wait longer and poll again.
            failures += 1
            last_problem = f"HTTP {status}" + (
                "" if body is not None else ", not a JSON response"
            )
            delay = _transient_delay(wait, failures, _retry_after_seconds(headers))
            sys.stderr.write(
                f"The server answered {last_problem} — retrying in {delay}s…\n"
            )
            continue
        failures = 0
        last_problem = ""
        if status == 200 and isinstance(body, dict) and body.get("api_key"):
            return str(body["api_key"])
        error = str(body.get("error") or "") if isinstance(body, dict) else ""
        described = body.get("error_description") if isinstance(body, dict) else None
        description = _printable(described) if isinstance(described, str) else ""
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            wait += _SLOW_DOWN_INCREMENT
            delay = wait
            continue
        if error == "expired_token":
            sys.stderr.write(
                "The code expired before approval. Run `veilfile login` again.\n"
            )
            raise SystemExit(1)
        if error == "access_denied":
            # The server explains refusals it makes itself (e.g. the plan's
            # key cap was reached after approval); the browser's deny button
            # sends no description.
            reason = f": {description}" if description else " in the browser."
            sys.stderr.write(f"Login was denied{reason} Nothing was stored.\n")
            raise SystemExit(1)
        if error == "invalid_grant":
            sys.stderr.write(
                "This login code is no longer valid. Run `veilfile login` again.\n"
            )
            raise SystemExit(1)
        what = (
            _printable(error, 64)
            if error
            else f"unexpected response from the server, HTTP {status}"
        )
        sys.stderr.write(
            f"Login failed ({what}){f': {description}' if description else '.'} "
            "Run `veilfile login` again.\n"
        )
        raise SystemExit(1)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _browser_url_allowed(url: str, base_url: str) -> bool:
    """Open only https (or loopback http) pages on the base URL's own origin.

    The URL comes from the server, so a hostile --base-url must not be able
    to make the CLI open file:, custom-scheme or third-party URLs. Browsers
    parse some URLs differently from urllib: they read '\\' as '/' and drop
    tabs and newlines, so "https://evil.example\\@veilfile.com/" opens
    evil.example. Backslashes, whitespace, control characters and userinfo
    are refused outright, so both parsers must agree on the host.
    """
    if any(c == "\\" or ord(c) <= 0x20 or ord(c) == 0x7F for c in url):
        return False
    origin = _origin(url)
    if origin is None or not origin[1] or origin != _origin(base_url):
        return False
    if "@" in urlparse(url).netloc:  # parses: _origin just did
        return False
    scheme, host, _port = origin
    return scheme == "https" or (scheme == "http" and _is_loopback(host))


def _matching_key_entry(keys: object, key: str) -> Optional[dict]:
    """The active entry for `key` in a GET /api/v1/keys list (by prefix)."""
    if not isinstance(keys, list):
        return None
    return next(
        (
            entry
            for entry in keys
            if isinstance(entry, dict)
            and entry.get("key_prefix") == key[:12]
            and entry.get("is_active")
            and entry.get("id") is not None
        ),
        None,
    )


def _revoke_own_key(base_url: str, key: str) -> Optional[str]:
    """Revoke `key` on the server, authenticating as the key itself.

    Returns 'revoked', 'invalid' (the server already rejects the key) or
    'missing' (no active key with its prefix); None when it could not be
    revoked. Never raises.
    """
    try:
        status, keys = _api(base_url, "GET", "/api/v1/keys", key=key)
        if status == 401:
            return "invalid"
        if status != 200 or not isinstance(keys, list):
            return None
        match = _matching_key_entry(keys, key)
        if match is None:
            return "missing"
        del_status, _ = _api(
            base_url, "DELETE", f"/api/v1/keys/{match['id']}", key=key
        )
    except ApiError:
        return None
    return "revoked" if del_status in (200, 204, 401, 404) else None


def _revoke_previous_key(
    base_url: str, old_key: str, old_base_url: Optional[str], new_key: str
) -> Optional[str]:
    """Revoke the key a re-login just replaced in the credentials file.

    Best effort. The old key authenticates its own revocation, so this also
    works when the new login is for another account. It is only ever sent
    to the server that issued it; files written before that was recorded
    are taken to hold a key for the default server. A failure is only a
    note: the new key is already stored and working. Returns
    _revoke_own_key's state, or None when nothing was sent.
    """
    if old_key[:12] == new_key[:12]:
        return None
    issuer = (old_base_url or DEFAULT_BASE_URL).rstrip("/")
    here = _origin(base_url)
    if here is None or _origin(issuer) != here:
        if old_base_url:
            source = f"was issued by {_printable(issuer)}"
        else:
            source = (
                "was saved by an older veilfile version, which did not record "
                f"its server (assumed {DEFAULT_BASE_URL})"
            )
        print(
            f"Note: the previously stored key ({old_key[:12]}…) {source}. "
            "It was replaced here but not revoked; revoke it on that server "
            "if you no longer need it."
        )
        return None
    state = _revoke_own_key(base_url, old_key)
    if state == "revoked":
        print("Revoked the previously stored key.")
    elif state is None:
        print(
            f"Note: could not revoke the previously stored key ({old_key[:12]}…). "
            "Revoke it in the dashboard."
        )
    # 'invalid' / 'missing': already revoked (e.g. replaced on the approval
    # page), nothing left to do.
    return state


def cmd_login(args: argparse.Namespace) -> int:
    # The key this CLI stored earlier (from the file, not VEILFILE_API_KEY):
    # revoked once the new one is safely stored, so re-logins do not pile up
    # live keys.
    previous_key, previous_base_url = _read_file_entry()
    try:
        status, headers, body = _request_with_headers(
            "POST", args.base_url.rstrip("/") + "/api/v1/device/code", body={}
        )
    except ApiError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    if status == 429:
        retry_after = _retry_after_seconds(headers)
        sys.stderr.write(
            "Too many login attempts from this address. "
            + (
                f"Retry in {retry_after} seconds.\n"
                if retry_after is not None
                else "Wait a while and retry.\n"
            )
        )
        return 1
    if status != 200 or not isinstance(body, dict) or not body.get("device_code"):
        sys.stderr.write("Could not start the login flow. Try again.\n")
        return 1

    device_code = str(body["device_code"])
    user_code = _printable(body.get("user_code") or "", 64)
    complete_uri = str(
        body.get("verification_uri_complete") or body.get("verification_uri") or ""
    )
    interval = _bounded_int(body.get("interval"), 5, 1, 60)
    expires_in = _bounded_int(body.get("expires_in"), 600, 1, 3600)

    print("To log in, open this page and enter the code:\n", flush=True)
    if complete_uri:
        print(f"  {_printable(complete_uri, 2048)}\n", flush=True)
    if user_code:
        print(f"  Code: {user_code}\n", flush=True)
    if complete_uri and _browser_url_allowed(complete_uri, args.base_url):
        try:
            if webbrowser.open(complete_uri):
                print("(Opened in your browser.)\n", flush=True)
        except Exception:
            pass
    elif complete_uri:
        print(
            "(Not opened automatically: it is not an https page on "
            f"{args.base_url}.)\n",
            flush=True,
        )
    print("Waiting for approval in the browser… (Ctrl-C to cancel)", flush=True)

    try:
        api_key = _poll_for_key(args.base_url, device_code, interval, expires_in)
    except KeyboardInterrupt:
        sys.stderr.write("\nLogin cancelled. Nothing was stored.\n")
        return 130

    try:
        path = write_stored_key(api_key, args.base_url)
    except OSError as exc:
        sys.stderr.write(f"Login approved, but the key could not be stored: {exc}\n")
        if args.print_key:
            print(f"\nAPI key: {api_key}")
            sys.stderr.write(
                "The key above is active but was not saved. Keep it safe, "
                "or revoke it in the dashboard.\n"
            )
            return 1
        # Nobody holds the new key, so it must not stay active unseen
        # (terminal keys count toward the plan's key cap).
        if _revoke_own_key(args.base_url, api_key) is None:
            sys.stderr.write(
                f"The new key ({api_key[:12]}…) could not be revoked. "
                "Revoke it in the dashboard.\n"
            )
        else:
            sys.stderr.write("The new key was revoked again.\n")
        # At the plan's key cap the approval page has the user pick a key
        # to replace, often this CLI's own: the server revoked it when it
        # issued the new key, so do not promise the old login still works.
        sys.stderr.write(
            "If you chose a key to replace on the approval page, it is "
            "already revoked. Fix the problem above, then run "
            "`veilfile login` again.\n"
        )
        return 1

    # Confirm the key works and learn who it belongs to (never print the key).
    try:
        status, usage = _api(args.base_url, "GET", "/api/v1/usage", key=api_key)
    except ApiError:
        status, usage = 0, {}
    account = (
        _printable(usage.get("email") or "", 254).strip()
        if status == 200 and isinstance(usage, dict)
        else ""
    )

    if args.print_key:
        print(f"\nAPI key: {api_key}")
    print(f"\nLogged in{f' as {account}' if account else ''}.")
    print(f"Key stored in {path} (not shown).")
    revoked = None
    if previous_key and previous_key != api_key:
        revoked = _revoke_previous_key(
            args.base_url, previous_key, previous_base_url, api_key
        )
    existing_env = (os.environ.get(_ENV_VAR) or "").strip()
    if existing_env and existing_env == previous_key and revoked is not None:
        # Typically `login --print-key` output copied into a shell profile
        # or MCP config: it takes precedence, and it no longer works.
        sys.stderr.write(
            f"Warning: {_ENV_VAR} is set in this shell to the previously "
            "stored key, which is now revoked. Unset it to use the new key, "
            "and update it wherever else you set it (shell profile, MCP config).\n"
        )
    elif existing_env:
        print(
            f"Note: {_ENV_VAR} is set in this shell and takes precedence "
            "over the stored key."
        )
    return 0


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------


def cmd_logout(args: argparse.Namespace) -> int:
    key, source = read_stored_key()
    if not key:
        print("Not logged in.")
        return 0

    revoked = False
    try:
        status, keys = _api(args.base_url, "GET", "/api/v1/keys", key=key)
    except ApiError as exc:
        sys.stderr.write(f"{exc}\n (The local copy was left in place.)\n")
        return 1

    if status == 401:
        print("The stored key is already invalid on the server.")
    elif status == 200 and isinstance(keys, list):
        match = _matching_key_entry(keys, key)
        if match is not None:
            try:
                del_status, _ = _api(
                    args.base_url, "DELETE", f"/api/v1/keys/{match['id']}", key=key
                )
            except ApiError as exc:
                sys.stderr.write(f"{exc}\n (The local copy was left in place.)\n")
                return 1
            revoked = del_status in (200, 204, 401, 404)
            if not revoked:
                sys.stderr.write(
                    "Could not revoke the key on the server. "
                    "The local copy was left in place.\n"
                )
                return 1
        else:
            print("No matching active key found on the server.")
            revoked = True  # nothing left to revoke
    else:
        sys.stderr.write(
            f"Server rejected the request (HTTP {status}). "
            "The local copy was left in place.\n"
        )
        return 1

    if source == "file":
        delete_stored_key()
    elif source == "environment":
        print(f"Note: {_ENV_VAR} is still set in this shell; unset it to finish.")

    print("Logged out." if revoked else "Logged out (local copy removed).")
    return 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def _format_bytes(num: object) -> str:
    try:
        value = float(num)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "–"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def cmd_status(args: argparse.Namespace) -> int:
    key, source = read_stored_key()
    if not key:
        print("Not logged in. Run `veilfile login`.")
        return 1
    try:
        status, usage = _api(args.base_url, "GET", "/api/v1/usage", key=key)
    except ApiError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    if status == 401:
        print("The stored key is invalid or revoked. Run `veilfile login` again.")
        return 1
    if status != 200 or not isinstance(usage, dict):
        sys.stderr.write("Could not check status. Try again.\n")
        return 1

    email = str(usage.get("email") or "–")
    verified = usage.get("email_verified")
    account_line = email + ("" if verified else " (email not verified)")
    storage_cap = usage.get("storage_cap_bytes")
    uploads_cap = usage.get("uploads_cap")
    print(f"Account:  {account_line}")
    print(f"Plan:     {usage.get('plan', '–')}")
    print(
        f"Uploads:  {usage.get('uploads_used', '–')} / "
        f"{uploads_cap if uploads_cap is not None else '–'} this month"
    )
    print(
        f"Storage:  {_format_bytes(usage.get('storage_used_bytes'))} / "
        f"{_format_bytes(storage_cap) if storage_cap else '–'}"
    )
    print(f"Key from: {'environment variable' if source == 'environment' else credentials_path()}")
    return 0


# ---------------------------------------------------------------------------
# push / pull
# ---------------------------------------------------------------------------


def _clean_component(name: str) -> str:
    """The last path component of an already-decoded name, made safe.

    Splits on both '/' and '\\', drops a Windows drive prefix ("C:"),
    control and format characters (escape sequences, bidi overrides), line
    separators (pull prints the path as one line) and quotes, and strips
    leading/trailing dots and spaces (so no dotfiles, '.' or '..'). On
    Windows it also replaces the characters Windows forbids (':' would
    name an alternate data stream, so "notes.md::$DATA" is notes.md) and
    defuses device names such as CON or NUL. Returns '' if nothing usable
    is left. Never percent-decodes: callers decode exactly once, before
    this, so an encoded separator cannot survive into a local path.
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    if len(base) >= 2 and base[1] == ":" and base[0].isascii() and base[0].isalpha():
        base = base[2:]
    cleaned = "".join(
        c
        for c in base
        if c != '"' and unicodedata.category(c) not in _UNSAFE_CATEGORIES
    ).strip(" .")
    if sys.platform == "win32" and cleaned:
        cleaned = "".join("_" if c in '<>:|?*' else c for c in cleaned)
        if cleaned.split(".", 1)[0].rstrip(" ").upper() in _WINDOWS_DEVICE_NAMES:
            cleaned = "_" + cleaned
    return cleaned


def _sanitize_filename(name: str) -> str:
    """A basename safe for multipart headers and local paths."""
    return _clean_component(name) or "upload"


def _encode_multipart(
    fields: Dict[str, str],
    files: Dict[str, Tuple[str, bytes, str]],
) -> Tuple[bytes, str]:
    """Build a multipart/form-data body. Returns (body, content_type)."""
    boundary = "veilfile" + os.urandom(16).hex()
    buf = io.BytesIO()
    for name, value in fields.items():
        buf.write(f"--{boundary}\r\n".encode("utf-8"))
        buf.write(
            f'Content-Disposition: form-data; name="{_sanitize_filename(name)}"'
            "\r\n\r\n".encode("utf-8")
        )
        buf.write(value.encode("utf-8"))
        buf.write(b"\r\n")
    for name, (filename, data, content_type) in files.items():
        buf.write(f"--{boundary}\r\n".encode("utf-8"))
        buf.write(
            f'Content-Disposition: form-data; name="{_sanitize_filename(name)}"; '
            f'filename="{_sanitize_filename(filename)}"\r\n'.encode("utf-8")
        )
        buf.write(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        buf.write(data)
        buf.write(b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode("utf-8"))
    return buf.getvalue(), f"multipart/form-data; boundary={boundary}"


def _request_raw(
    method: str,
    url: str,
    key: Optional[str] = None,
    body: Optional[bytes] = None,
    content_type: Optional[str] = None,
) -> Tuple[int, Dict[str, str], bytes]:
    """Non-JSON request (multipart upload, binary download).

    Returns (status, headers, body_bytes), header names lower-cased. Raises
    ApiError on transport failure. HTTP error statuses are returned, not
    raised, so callers can map them.
    """
    headers = {"Accept": "*/*"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if content_type:
        headers["Content-Type"] = content_type
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with _urlopen(request, timeout=60) as response:
            raw = response.read()
            return response.status, _lower_headers(response.getheaders()), raw
    except HTTPError as exc:
        try:
            raw = exc.read()
        except Exception as read_exc:
            raise ApiError(
                f"Could not read the response from {url}: {read_exc}"
            ) from read_exc
        return exc.code, _lower_headers((exc.headers or {}).items()), raw
    except (
        URLError,
        OSError,
        TimeoutError,
        http.client.HTTPException,
    ) as exc:
        raise ApiError(f"Could not reach {url}: {exc}") from exc


def cmd_push(args: argparse.Namespace) -> int:
    key, _source = read_stored_key()
    if not key:
        print("Not logged in. Run `veilfile login`.")
        return 1
    path = Path(args.file)
    if not path.is_file():
        sys.stderr.write(f"No such file: {args.file}\n")
        return 1
    try:
        size = path.stat().st_size
    except OSError as exc:
        sys.stderr.write(f"Could not read {args.file}: {exc}\n")
        return 1
    if size > _MAX_UPLOAD_BYTES:
        sys.stderr.write(
            f"{args.file} is {_format_bytes(size)}; the upload cap is "
            f"{_format_bytes(_MAX_UPLOAD_BYTES)}. Split it or pick a smaller file.\n"
        )
        return 1
    try:
        data = path.read_bytes()
    except OSError as exc:
        sys.stderr.write(f"Could not read {args.file}: {exc}\n")
        return 1

    filename = _sanitize_filename(path.name)
    guessed = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    fields: Dict[str, str] = {}
    if args.ttl_days is not None:
        fields["ttl_days"] = str(args.ttl_days)
    body, content_type = _encode_multipart(fields, {"file": (filename, data, guessed)})

    try:
        status, headers, raw = _request_raw(
            "POST",
            args.base_url.rstrip("/") + "/api/v1/artifacts",
            key=key,
            body=body,
            content_type=content_type,
        )
    except ApiError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    try:
        payload = json.loads(raw.decode("utf-8", errors="replace")) if raw.strip() else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    if status == 201 and payload.get("url"):
        print(payload["url"])
        flags = payload.get("secret_flags") or []
        if flags:
            warning = payload.get("warning") or (
                "The secret scanner flagged this file. "
                "Ask before sharing the link."
            )
            sys.stderr.write(f"Warning: {warning}\n")
        return 0
    if status == 401:
        sys.stderr.write(
            "The stored key is invalid or revoked. Run `veilfile login` again.\n"
        )
        return 1
    message = str(payload.get("error") or "").strip()
    code = str(payload.get("code") or "")
    if code == "monthly_cap_reached":
        # Sent as 429 with no Retry-After: retrying cannot help this month.
        sys.stderr.write(
            "Monthly upload cap reached for your plan. Retrying will not help "
            "until it resets next month; upgrade for a higher cap: "
            f"{args.base_url.rstrip('/')}/plans/\n"
        )
        return 1
    if status == 402 or code == "storage_cap_reached":
        sys.stderr.write(
            "Storage cap reached for your plan. "
            "Delete old artifacts or upgrade, then retry.\n"
        )
        return 1
    if status == 400:
        sys.stderr.write(
            f"Upload rejected: {message or 'the server refused the file.'}\n"
        )
        return 1
    if status == 413:
        sys.stderr.write("Upload rejected: file too large for the server.\n")
        return 1
    if status == 429:
        # Throttling sends Retry-After; say exactly how long. Without it,
        # pass on the server's own words rather than guess.
        retry_after = _retry_after_seconds(headers)
        if retry_after is not None:
            sys.stderr.write(f"Rate limited. Retry in {retry_after} seconds.\n")
        elif message:
            sys.stderr.write(f"Upload refused (HTTP 429): {_printable(message)}\n")
        else:
            sys.stderr.write("Rate limited. Wait a while and retry.\n")
        return 1
    sys.stderr.write(f"Upload failed (HTTP {status}).\n")
    return 1


def _filename_from_headers(headers: Dict[str, str], url_path: str) -> str:
    """Best-effort filename: Content-Disposition, then the URL path.

    Each candidate is percent-decoded exactly once, then reduced to a
    single safe component by _clean_component. The RFC 5987 filename*=
    form wins (decoded by the email parser); the quoted filename= form is
    decoded here because the server quote()s inline names.
    """
    message = Message()
    message["content-disposition"] = headers.get("content-disposition", "")
    extended: Optional[str] = None
    plain: Optional[str] = None
    try:
        params = message.get_params(header="content-disposition") or []
        for name, value in params[1:]:
            if name.lower() != "filename":
                continue
            if isinstance(value, tuple):
                extended = extended or collapse_rfc2231_value(value)
            else:
                plain = plain or unquote(value)
    except (TypeError, ValueError, LookupError):
        # Malformed RFC 2231 parameters make the email parser raise: mixed
        # filename*0*/filename* continuations (TypeError), or a charset
        # such as "\x00" or "idna" (ValueError, UnicodeError). The backend
        # never sends these, so ignore the header and use the URL instead.
        extended = plain = None
    from_url = unquote(urlparse(url_path).path.rsplit("/", 1)[-1])
    for candidate in (extended, plain, from_url):
        cleaned = _clean_component(candidate or "")
        if cleaned:
            return cleaned
    return "upload"


def _contained_path(directory: Path, name: str) -> Optional[Path]:
    """directory/name, or None unless it resolves directly inside directory.

    Fails closed: a separator, '.', '..', or an existing symlink that points
    elsewhere all give None.
    """
    candidate = directory / name
    try:
        root = os.path.realpath(str(directory))
        final = os.path.realpath(str(candidate))
    except (OSError, ValueError):
        return None
    if candidate.name != name or os.path.dirname(final) != root:
        return None
    return candidate


def _open_new_part_file(out_path: Path) -> Tuple[int, Path]:
    """Create a fresh, unpredictable .part file next to out_path.

    O_EXCL never follows or reuses an existing path, so a planted
    "<name>.part" symlink cannot redirect the download. A name with no
    room left for the suffix gets a short ".veilfile-<hex>.part" instead,
    so any name the filesystem accepts can still be downloaded.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    short = False
    while True:
        token = os.urandom(4).hex()
        name = f"{out_path.name}.{token}.part"
        # UTF-8 bytes are never fewer than the characters or UTF-16 units
        # that other filesystems count.
        if short or len(name.encode("utf-8", "surrogatepass")) > _NAME_MAX:
            name = f".veilfile-{token}.part"
        tmp_path = out_path.with_name(name)
        try:
            return os.open(str(tmp_path), flags, 0o666), tmp_path
        except FileExistsError:
            continue
        except OSError as exc:
            if short or exc.errno != errno.ENAMETOOLONG:
                raise
            short = True  # a filesystem with a lower limit


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def cmd_pull(args: argparse.Namespace) -> int:
    parsed = urlparse(args.url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        sys.stderr.write(f"Not a valid URL: {args.url}\n")
        return 1
    base_host = (urlparse(args.base_url).hostname or "").lower()
    if parsed.hostname.lower() != base_host:
        sys.stderr.write(
            f"Refusing to download from {parsed.hostname}: pull only fetches "
            f"from {base_host} (point --base-url at another deploy to use it).\n"
        )
        return 1

    request = Request(
        args.url,
        method="GET",
        headers={"Accept": "*/*", "User-Agent": f"veilfile/{__version__}"},
    )
    try:
        response = _urlopen(request, timeout=60)
    except HTTPError as exc:
        if exc.code == 404:
            sys.stderr.write(
                "Not found: the link is unknown, revoked, or expired.\n"
            )
        else:
            sys.stderr.write(f"Download failed (HTTP {exc.code}).\n")
        return 1
    except _RedirectRefused as exc:
        sys.stderr.write(
            f"Refusing to follow a redirect to {exc.target}: redirects must "
            "stay on the same host and port, and never drop https.\n"
        )
        return 1
    except (
        URLError,
        OSError,
        TimeoutError,
        http.client.HTTPException,
    ) as exc:
        sys.stderr.write(f"Could not reach {args.url}: {exc}\n")
        return 1

    with response:
        headers = {k.lower(): v for k, v in response.getheaders()}
        out_path = Path(args.out) if args.out else None
        if out_path is None or out_path.is_dir():
            # The name comes from whoever uploaded the artifact: it must
            # land directly inside the chosen directory, or nowhere.
            directory = out_path if out_path is not None else Path.cwd()
            name = _filename_from_headers(headers, args.url)
            out_path = _contained_path(directory, name)
            if out_path is None:
                sys.stderr.write(
                    f"Refusing to save {name}: {directory / name} does not "
                    f"resolve to a file inside {directory}.\n"
                )
                return 1
        if os.path.lexists(out_path) and not args.force:
            sys.stderr.write(
                f"{out_path} already exists; pass --force to overwrite it.\n"
            )
            return 1
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            sys.stderr.write(f"Could not create {out_path.parent}: {exc}\n")
            return 1
        expected = _header_int(headers.get("content-length", ""))
        total = 0
        try:
            fd, tmp_path = _open_new_part_file(out_path)
        except OSError as exc:
            sys.stderr.write(f"Could not write {out_path}: {exc}\n")
            return 1
        try:
            with os.fdopen(fd, "wb") as handle:
                while True:
                    chunk = response.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    total += len(chunk)
        except http.client.HTTPException as exc:
            _discard(tmp_path)
            sys.stderr.write(f"Download interrupted ({exc!r}). Try again.\n")
            return 1
        except OSError as exc:
            _discard(tmp_path)
            sys.stderr.write(f"Could not write {out_path}: {exc}\n")
            return 1
        if expected is not None and total != expected:
            _discard(tmp_path)
            sys.stderr.write(
                "Download incomplete (size mismatch). Try again.\n"
            )
            return 1
        if os.path.lexists(out_path) and not args.force:
            # Appeared while downloading: still never clobber silently.
            _discard(tmp_path)
            sys.stderr.write(
                f"{out_path} already exists; pass --force to overwrite it.\n"
            )
            return 1
        try:
            os.replace(tmp_path, out_path)
        except OSError as exc:
            _discard(tmp_path)
            sys.stderr.write(f"Could not save {out_path}: {exc}\n")
            return 1

    print(str(out_path))
    return 0


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="veilfile",
        description="Command-line client for Veilfile.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("VEILFILE_BASE_URL", DEFAULT_BASE_URL),
        help="API base URL (default: %(default)s).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="Log in via the browser; store the API key.")
    login.add_argument(
        "--print-key",
        action="store_true",
        help="Print the API key after storing it. Only use this if you "
        "explicitly need the raw key; it is never printed otherwise.",
    )
    login.set_defaults(func=cmd_login)

    logout = sub.add_parser(
        "logout", help="Revoke the key on the server; delete the local copy."
    )
    logout.set_defaults(func=cmd_logout)

    status = sub.add_parser(
        "status", help="Show account status for the stored key (never prints it)."
    )
    status.set_defaults(func=cmd_status)
    whoami = sub.add_parser("whoami", help="Alias for status.")
    whoami.set_defaults(func=cmd_status)

    push = sub.add_parser(
        "push", help="Upload a file; print its share URL."
    )
    push.add_argument("file", help="Local file to upload (max 25 MiB).")
    push.add_argument(
        "--ttl-days",
        type=int,
        default=None,
        help="Days until the link expires (default: your plan's TTL).",
    )
    push.set_defaults(func=cmd_push)

    pull = sub.add_parser(
        "pull", help="Download an artifact to the current directory."
    )
    pull.add_argument("url", help="The artifact's share URL.")
    pull.add_argument(
        "--out",
        default=None,
        help="Destination file or directory (default: current directory).",
    )
    pull.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the destination file if it already exists.",
    )
    pull.set_defaults(func=cmd_pull)

    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
