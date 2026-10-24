"""Secret scanning for uploaded artifacts.

Only flag names are ever returned. NEVER log or return file contents.
Only the first 1 MiB of a file is scanned.

Every pattern must stay linear on adversarial input: no nested or
unbounded quantifiers that can backtrack into each other (tests run each
one over 1 MiB of hostile text). Patterns with no fixed prefix (AWS secret
keys, OpenAI-style sk- keys, secret assignments) also pass a validator so
git SHAs, hyphenated prose, placeholders and ordinary code are not flagged.
"""

import re

SCAN_LIMIT_BYTES = 1024 * 1024

# Locations reported per flag in an upload response. A file full of
# secret-shaped lines would otherwise produce tens of thousands of hits.
MAX_LOCATIONS_PER_FLAG = 20


def _has_mixed_case(text):
    return any(c.isupper() for c in text) and any(c.islower() for c in text)


def _starts_token(match, joiners=""):
    """True when the match is not glued onto a preceding word. Checked
    here rather than with a leading lookbehind so the regex keeps its
    literal-prefix fast path."""
    start = match.start()
    if start == 0:
        return True
    before = match.string[start - 1]
    if before in "nrt" and start >= 2 and match.string[start - 2] == "\\":
        # An escaped newline/tab in a JSON or log string ("...\nsk_live_").
        return True
    return not (before.isascii() and before.isalnum()) and before not in joiners


def _prefixed_token(match):
    return _starts_token(match)


def _sk_token(match):
    return _starts_token(match, joiners="_-")


# A long unbroken letters-and-digits run is a key body even in one case:
# DeepSeek/DashScope sk-<32 hex>, OpenRouter sk-or-v1-<64 hex>.
_KEY_RUN = re.compile(r"[A-Za-z0-9]{20,}")


def _letters_and_digits(text):
    return any(c.isdigit() for c in text) and any(c.isalpha() for c in text)


def _openai_key(match):
    """Key-shaped body: a digit plus mixed case, or a 20+ character run of
    letters and digits. Hyphenated prose (sk-learn-is-...) has neither."""
    body = match.group(0)[len("sk-"):]
    if not _sk_token(match) or not any(c.isdigit() for c in body):
        return False
    if _has_mixed_case(body):
        return True
    return any(_letters_and_digits(run.group(0)) for run in _KEY_RUN.finditer(body))


def _aws_secret(match):
    # Real keys are 40 random base64 characters: upper, lower and a digit
    # or symbol are near-certain. Git SHAs (single-case hex) and absolute
    # paths that happen to be 40 characters long are not.
    text = match.group(0)
    if not _has_mixed_case(text):
        return False
    if not any(c.isdigit() or c in "+/" for c in text):
        return False
    return not (text.startswith("/") and text.count("/") >= 3)


def _is_placeholder(value):
    # Template and variable references, not the secret itself.
    return "{{" in value or value.startswith(("${", "$(", "<"))


def _quoted_value(match):
    return not _is_placeholder(match.group("value"))


def _env_value(match):
    # Unquoted .env value: skip references, placeholders, paths, URLs,
    # ARNs, plain numbers and code expressions (calls, concatenation).
    value = match.group("value")
    if value[0] in "$<{%/*" or "://" in value or len(set(value)) < 4:
        return False
    if value.isdigit() or value.startswith("arn:") or _is_placeholder(value):
        return False
    if any(c in value for c in "(),"):
        return False
    return any(c.isdigit() for c in value) or _has_mixed_case(value)


# (name, pattern, validator or None). A name may appear more than once;
# scan results list each name once, in this order.
_PATTERNS = [
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}"), None),
    ("github_token", re.compile(r"gh[ousr]_[A-Za-z0-9]{36}"), _prefixed_token),
    (
        "openai_key",
        re.compile(r"sk-(?!ant-)[A-Za-z0-9_-]{20,}"),
        _openai_key,
    ),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), _sk_token),
    # Separate entries keep a literal prefix per regex (an alternation
    # without one scans an order of magnitude slower).
    ("stripe_key", re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{20,}"), _prefixed_token),
    ("stripe_key", re.compile(r"rk_(?:live|test)_[A-Za-z0-9]{20,}"), _prefixed_token),
    ("stripe_key", re.compile(r"whsec_[A-Za-z0-9]{24,}"), _prefixed_token),
    # Not glued onto a preceding word: "SOUTHEASTASIA..." is not a key id.
    ("aws_access_key", re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}"), _prefixed_token),
    # AWS secret access keys are 40-char base64-ish strings with no fixed
    # prefix, so they are detected independently of the AKIA key id.
    (
        "aws_secret_key",
        re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"),
        _aws_secret,
    ),
    (
        "private_key",
        re.compile(
            # PKCS#1/#8 (RSA, EC, DSA, ENCRYPTED), OpenSSH and PGP armor,
            # the SSH2 (RFC 4716) form and PuTTY .ppk files.
            r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----"
            r"|---- BEGIN SSH2 ENCRYPTED PRIVATE KEY ----"
            r"|PuTTY-User-Key-File-[0-9]: "
        ),
        None,
    ),
    (
        "slack_token",
        re.compile(r"xox[abeoprs]-[A-Za-z0-9-]{10,}|xapp-[0-9]-[A-Za-z0-9-]{10,}"),
        None,
    ),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z\-_]{35}"), None),
    (
        "generic_secret",
        # Quoted value after a secret-named key: password = "...",
        # STRIPE_SECRET_KEY='...', {"api_key": "..."}, secretKey: '...'.
        # Only key-like suffixes count, so secret_name: "..." and
        # password_reset_token: "..." are not read as secret values.
        re.compile(
            r"(?:api[_-]?key|secret|passwd|password)"
            r"(?:[_-]?(?:access[_-]?key|key|token|secret|base|value)){0,2}"
            r"['\"]?[ \t]{0,32}[:=][ \t]{0,32}['\"](?P<value>[^'\"\r\n]{8,})['\"]",
            re.IGNORECASE,
        ),
        _quoted_value,
    ),
    (
        "generic_secret",
        # Unquoted .env / shell assignment: AWS_SECRET_ACCESS_KEY=...
        # No spaces around "=" (as in .env and shell syntax), so Python
        # constants like ACCESS_TOKEN_LIFETIME = timedelta(...) are skipped.
        re.compile(
            r"^[ \t]{0,8}(?:export[ \t]{1,8})?[A-Z0-9_]{0,32}"
            r"(?:SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE_KEY|TOKEN)"
            r"(?:_[A-Z0-9]{1,16}){0,4}=(?P<value>[^\s'\"#]{12,})",
            re.MULTILINE,
        ),
        _env_value,
    ),
]


def _decode(data):
    return data[:SCAN_LIMIT_BYTES].decode("utf-8", errors="ignore")


def _valid_matches(text, pattern, validator):
    for match in pattern.finditer(text):
        if validator is None or validator(match):
            yield match


def scan_bytes(data: bytes) -> list:
    """Scan ``data`` for known secret shapes. Returns a list of flag names."""
    if not data:
        return []
    text = _decode(data)
    flags = []
    for name, pattern, validator in _PATTERNS:
        if name in flags:
            continue
        if next(_valid_matches(text, pattern, validator), None) is not None:
            flags.append(name)
    return flags


def locate_flags(data: bytes, limit: int = MAX_LOCATIONS_PER_FLAG):
    """Return ``({name: [{"line": n, "offset": m}, ...]}, truncated)``.

    ``line`` is 1-based; ``offset`` is the character offset into the decoded
    scan window (byte-identical for ASCII). At most ``limit`` locations are
    kept per flag, earliest first; ``truncated`` is True when any flag had
    more. Linear in the size of the scan window.
    """
    if not data:
        return {}, False
    text = _decode(data)
    per_name = {}
    for name, pattern, validator in _PATTERNS:
        hits = per_name.setdefault(name, [])
        line, pos, found = 1, 0, 0
        for match in _valid_matches(text, pattern, validator):
            start = match.start()
            # Matches arrive in order, so count newlines incrementally
            # instead of rescanning from the start of the file each time.
            line += text.count("\n", pos, start)
            pos = start
            hits.append((start, line))
            found += 1
            if found > limit:
                break
    grouped = {}
    truncated = False
    for name, hits in per_name.items():
        if not hits:
            continue
        hits = sorted(set(hits))
        if len(hits) > limit:
            truncated = True
            hits = hits[:limit]
        grouped[name] = [{"line": line, "offset": start} for start, line in hits]
    return grouped, truncated


def scan_bytes_with_locations(data: bytes, limit: int = MAX_LOCATIONS_PER_FLAG) -> list:
    """Scan ``data`` and return ``(name, line, offset)`` per match, capped at
    ``limit`` matches per flag (see :func:`locate_flags`)."""
    grouped, _ = locate_flags(data, limit)
    return [
        (name, hit["line"], hit["offset"])
        for name, hits in grouped.items()
        for hit in hits
    ]


def flag_locations(data: bytes) -> dict:
    """Group hits by flag name: ``{name: [{"line": n, "offset": m}, ...]}``.

    Empty when clean. Capped at ``MAX_LOCATIONS_PER_FLAG`` per flag; use
    :func:`locate_flags` to also learn whether the list was truncated.
    """
    grouped, _ = locate_flags(data)
    return grouped


# Advisory warning attached to upload responses when secret_flags is
# non-empty. The scan never blocks: it flags, and the agent is told to
# involve the human before sharing the link.
SECRET_FLAGS_WARNING = (
    "secret_flags is non-empty: this file may contain a secret. "
    "Ask the user whether to keep or revoke this upload before sharing the link."
)
