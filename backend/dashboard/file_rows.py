"""The rows of the Files list (spec 4.8): what the list endpoint answers, made ready for components/file_list.html.

The JSON list is the API's, which clients read (drop_core/account.py), so its rows carry neither a state nor words for
their flags. This module adds both, for the page alone, and leaves the API's answer as it is.
"""
import uuid
from datetime import datetime

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from drop_core.file_access import file_details, files_visible_to
from drop_core.models import current_membership, has_other_members

# The secret scanner's flag names (drop_core/secrets.py) in words. A test keeps this table as long as the scanner's list:
# a new pattern there needs its words here, or the page would print the name with its underscores.
FLAG_WORDS = {
    "github_pat": "GitHub token",
    "github_token": "GitHub token",
    "openai_key": "OpenAI key",
    "anthropic_key": "Anthropic key",
    "stripe_key": "Stripe key",
    "aws_access_key": "AWS access key",
    "aws_secret_key": "AWS secret key",
    "private_key": "Private key",
    "slack_token": "Slack token",
    "google_api_key": "Google API key",
    "generic_secret": "Password or secret",
}


def state_of(expires_at, held, now):
    """live, expired or held: expired once expires_at has passed (held or not), else held, else live."""
    return "expired" if expires_at <= now else "held" if held else "live"


def flag_words(flags):
    """Each flag in words, once each, in the order the scanner reported them."""
    words = []
    for flag in flags or ():
        word = FLAG_WORDS.get(flag) or str(flag).replace("_", " ").capitalize()
        if word not in words:
            words.append(word)
    return words


def held_ids(user, body):
    """Which of the listed files the safety review holds, as a set of id strings.

    The list endpoint does not say (safety_status is not in its body, which clients read), so this asks once, for the
    ids on the page. A held file's link answers like a missing one, and so does that of a suspended team's file, so the page
    must not offer either.
    """
    ids = [item["id"] for item in body]
    if not ids:
        return set()
    held = files_visible_to(current_membership(user)).filter(
        Q(safety_status="held") | Q(workspace__suspended_at__isnull=False), id__in=ids
    )
    return {str(pk) for pk in held.values_list("id", flat=True)}


def shows_team(user):
    """Whether the Files page shows the team's columns: the switch is on and someone else is on the team. False before any query
    while the switch is off."""
    return settings.TEAM_SEATS and has_other_members(current_membership(user).workspace)


def file_rows(user, body, *, now=None, team=None):
    """The list endpoint's rows (`body`) as the file_list component takes them.

    The endpoint lists expired rows until the sweep deletes them (and a revoke whose storage delete failed leaves one),
    and does not say which are held, so the state is worked out here (state_of). Only a live row carries its url: the
    link of the others opens nothing, and the page has no use for a bearer link that does not work. On a team (`team`, which
    is worked out when not given) each row also carries file_details' keys.
    """
    now = now or timezone.now()
    held = held_ids(user, body)
    details = file_details(current_membership(user), [item["id"] for item in body]) if (shows_team(user) if team is None else team) else None
    rows = []
    for item in body:
        expires = datetime.fromisoformat(item["expires_at"])
        state = state_of(expires, item["id"] in held, now)
        row = {
            "id": item["id"],
            "name": item["filename"],
            "size": item["size_bytes"],
            "added": datetime.fromisoformat(item["created_at"]),
            "expires": expires,
            "flags": flag_words(item["secret_flags"]),
            "state": state,
            "url": item["url"] if state == "live" else "",
        }
        if details is not None:
            row.update(details[uuid.UUID(item["id"])])
        rows.append(row)
    return rows
