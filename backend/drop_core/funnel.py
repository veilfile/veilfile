"""First-party funnel instrumentation for Veilfile activation.

Server-side only. No third-party scripts, no paid analytics, no new
vendors (spending freeze). Privacy: events hold a stage name, a
timestamp, an optional workspace FK, and an optional random anonymous
id. No IP addresses, user agents, usernames, emails, file names, or file
contents are ever recorded here.

Recording never raises: analytics must not break product flows, so any
unexpected failure degrades to "no event recorded" (and is logged).

Anonymous stages (landing visit, signup started) never create a database
session. They are deduplicated with one small first-party cookie
(``vf_funnel``: a random hex id plus the anonymous stages already seen),
scoped to the browser session. Obvious bots and HEAD requests are not
counted and get no cookie. Requests authenticated with an API key or
OAuth token never touch the session or the cookie.

Events older than the retention window are deleted by the
``prune_funnel_events`` management command (run it daily).
"""

import functools
import logging
import os
import re
import uuid
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

LANDING_VISIT = "landing_visit"
SIGNUP_STARTED = "signup_started"
SIGNUP_COMPLETED = "signup_completed"
KEY_ISSUED = "key_issued"
UPLOAD_COMPLETED = "upload_completed"

# Ordered funnel stages for reporting. first_upload / second_upload are
# derived from upload_completed events (upload_number >= 1 / >= 2).
FUNNEL_ORDER = [
    LANDING_VISIT,
    SIGNUP_STARTED,
    SIGNUP_COMPLETED,
    KEY_ISSUED,
    "first_upload",
    "second_upload",
]

# Pre-signup stages: counted per browser, never for bots or HEAD requests.
# The single-letter codes record "already counted" in the cookie.
_ANONYMOUS_STAGE_CODES = {LANDING_VISIT: "l", SIGNUP_STARTED: "s"}

COOKIE_NAME = "vf_funnel"
_ANON_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_PENDING_COOKIE_ATTR = "_vf_funnel_cookie"

# Real browsers all send a "Mozilla/..." user agent; HTTP libraries and
# most scripts do not. Crawlers, link unfurlers, monitors and headless
# automation that do claim Mozilla/ identify themselves with one of these.
_BOT_UA_RE = re.compile(
    r"bot\b|crawl|spider|slurp|externalhit|externalagent|fetcher|headless"
    r"|lighthouse|inspectiontool|mediapartners|pingdom|archiver|preview"
    r"|chatgpt-user|claude-user|perplexity-user|\+https?://",
    re.IGNORECASE,
)

DEFAULT_RETENTION_DAYS = 365
RETENTION_ENV = "VEILFILE_FUNNEL_RETENTION_DAYS"


def is_bot(request):
    """Best-effort: True for requests that are obviously not a person."""
    ua = request.META.get("HTTP_USER_AGENT", "")
    return not ua.startswith("Mozilla/") or bool(_BOT_UA_RE.search(ua))


def _is_credential_request(request):
    """True when an API key or OAuth token authenticated the request."""
    auth = getattr(request, "auth", None)
    return auth is not None and getattr(auth, "workspace", None) is not None


def _read_cookie(request):
    """(anonymous_id, seen stage codes) from the funnel cookie. A missing
    or malformed cookie reads as a fresh browser."""
    raw = request.COOKIES.get(COOKIE_NAME, "")
    anon, _sep, seen = raw.partition(".")
    if not _ANON_ID_RE.match(anon):
        return "", set()
    return anon, set(seen) & set(_ANONYMOUS_STAGE_CODES.values())


def record(request, stage, workspace=None, upload_number=None,
           once_per_session=False, once_per_workspace=False):
    """Record one funnel event. Returns the event, or None when deduped,
    skipped (bot, HEAD) or when recording failed.

    ``once_per_session`` dedupes anonymous stages per browser session via
    the funnel cookie; views that record them must be wrapped with
    ``sets_funnel_cookie`` so the cookie reaches the browser."""
    from .models import FunnelEvent

    try:
        anonymous_id = ""
        if request is not None and stage in _ANONYMOUS_STAGE_CODES:
            if request.method == "HEAD" or is_bot(request):
                return None
            anonymous_id, seen = _read_cookie(request)
            code = _ANONYMOUS_STAGE_CODES[stage]
            if once_per_session and code in seen:
                return None
            anonymous_id = anonymous_id or uuid.uuid4().hex
            seen.add(code)
            setattr(
                request,
                _PENDING_COOKIE_ATTR,
                f"{anonymous_id}.{''.join(sorted(seen))}",
            )
        elif request is not None and not _is_credential_request(request):
            # Link post-signup stages to the browser's earlier anonymous
            # stages when the cookie is there; never mint one here.
            anonymous_id, _seen = _read_cookie(request)
        if once_per_workspace and workspace is not None:
            if FunnelEvent.objects.filter(
                workspace=workspace, stage=stage
            ).exists():
                return None
        return FunnelEvent.objects.create(
            stage=stage,
            workspace=workspace,
            anonymous_id=anonymous_id,
            upload_number=upload_number,
        )
    except Exception:
        logger.exception("funnel: failed to record %s event", stage)
        return None


def set_cookie(request, response):
    """Attach the funnel cookie when ``record`` asked for one."""
    value = getattr(request, _PENDING_COOKIE_ATTR, None)
    if value:
        # No max_age: a browser-session cookie, like the visit it counts.
        response.set_cookie(
            COOKIE_NAME,
            value,
            secure=request.is_secure(),
            httponly=True,
            samesite="Lax",
        )
    return response


def sets_funnel_cookie(view):
    """Decorator for views that record anonymous stages."""

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        return set_cookie(request, view(request, *args, **kwargs))

    return wrapper


def record_workspace_event(workspace, stage, upload_number=None):
    """Record an event when there is no HTTP request (e.g. MCP tool call)."""
    return record(None, stage, workspace=workspace, upload_number=upload_number)


def retention_days():
    """Days of funnel events to keep (env override, else the default)."""
    raw = os.environ.get(RETENTION_ENV, "").strip()
    try:
        days = int(raw)
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    return days if days > 0 else DEFAULT_RETENTION_DAYS


def prune_events(older_than_days=None):
    """Delete funnel events older than the retention window. Returns the
    number of rows deleted."""
    from .models import FunnelEvent

    days = older_than_days if older_than_days is not None else retention_days()
    cutoff = timezone.now() - timedelta(days=days)
    deleted, _per_model = FunnelEvent.objects.filter(created_at__lt=cutoff).delete()
    return deleted


def _distinct_workspaces(stage, min_upload_number=None):
    from .models import FunnelEvent

    qs = FunnelEvent.objects.filter(stage=stage, workspace__isnull=False)
    if min_upload_number is not None:
        qs = qs.filter(upload_number__gte=min_upload_number)
    return qs.values("workspace_id").distinct().count()


def _workspaces_with_second_upload():
    """Workspaces with a second upload. upload_number comes from the live
    artifact count, which drops when artifacts expire, so two upload
    events count as well as an event numbered 2 or more."""
    from django.db.models import Count

    from .models import FunnelEvent

    uploads = FunnelEvent.objects.filter(
        stage=UPLOAD_COMPLETED, workspace__isnull=False
    )
    numbered = set(
        uploads.filter(upload_number__gte=2)
        .values_list("workspace_id", flat=True)
        .distinct()
    )
    repeated = set(
        uploads.values("workspace_id")
        .annotate(n=Count("id"))
        .filter(n__gte=2)
        .values_list("workspace_id", flat=True)
    )
    return len(numbered | repeated)


def funnel_summary():
    """Ordered funnel rows: stage, distinct-entity count, and conversion
    rates against the top of funnel and the previous stage."""
    from .models import FunnelEvent

    counts = {
        LANDING_VISIT: FunnelEvent.objects.filter(stage=LANDING_VISIT)
        .values("anonymous_id")
        .distinct()
        .count(),
        SIGNUP_STARTED: FunnelEvent.objects.filter(stage=SIGNUP_STARTED)
        .values("anonymous_id")
        .distinct()
        .count(),
        SIGNUP_COMPLETED: _distinct_workspaces(SIGNUP_COMPLETED),
        KEY_ISSUED: _distinct_workspaces(KEY_ISSUED),
        "first_upload": _distinct_workspaces(UPLOAD_COMPLETED, min_upload_number=1),
        "second_upload": _workspaces_with_second_upload(),
    }
    rows = []
    top = counts[LANDING_VISIT]
    prev = None
    for stage in FUNNEL_ORDER:
        count = counts[stage]
        rows.append(
            {
                "stage": stage,
                "count": count,
                "of_top": (count / top) if top else 0.0,
                "of_prev": (count / prev) if prev else (1.0 if stage == LANDING_VISIT else 0.0),
            }
        )
        prev = count
    return rows
