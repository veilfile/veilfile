"""The rows of the Files list (spec 4.8): what the list endpoint answers, made ready for components/file_list.html.

The JSON list is the API's, which clients read (drop_core/account.py), so its rows carry neither a state nor words for
their flags. This module adds both, for the page alone, and leaves the API's answer as it is.
"""
from datetime import datetime

from django.utils import timezone

from drop_core.models import Artifact

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
    ids on the page. A held file's link answers like a missing one, so the page must not offer it.
    """
    ids = [item["id"] for item in body]
    if not ids:
        return set()
    held = Artifact.objects.filter(workspace__owner=user, id__in=ids, safety_status="held")
    return {str(pk) for pk in held.values_list("id", flat=True)}


def file_rows(user, body, *, now=None):
    """The list endpoint's rows (`body`) as the file_list component takes them.

    The endpoint lists expired rows until the sweep deletes them (and a revoke whose storage delete failed leaves one),
    and does not say which are held, so the state is worked out here (state_of). Only a live row carries its url: the
    link of the others opens nothing, and the page has no use for a bearer link that does not work.
    """
    now = now or timezone.now()
    held = held_ids(user, body)
    rows = []
    for item in body:
        expires = datetime.fromisoformat(item["expires_at"])
        state = state_of(expires, item["id"] in held, now)
        rows.append({
            "id": item["id"],
            "name": item["filename"],
            "size": item["size_bytes"],
            "added": datetime.fromisoformat(item["created_at"]),
            "expires": expires,
            "flags": flag_words(item["secret_flags"]),
            "state": state,
            "url": item["url"] if state == "live" else "",
        })
    return rows
