"""Forms of the dashboard: the new-key form and the one-time token that keeps a resent POST from making a second key."""
import re
import secrets
import unicodedata
from decimal import Decimal

from django import forms
from django.core.cache import cache

from drop_core.models import APIKey
from drop_core.plans import GB

# The unspent form tokens of the Keys page, newest last, in request.session. A token is not a secret (it only says "this form has not been
# sent yet"), but it must be one the page really issued, so a POST made up by hand, or sent twice, makes no key.
KEY_TOKENS = "dashboard_key_tokens"
KEPT_TOKENS = 5  # the last few, so a second tab's form still works; a sixth page in a row retires the oldest
SPENT_FOR = 24 * 60 * 60  # how long a spent token is remembered in the cache; the session forgets it at once
NAME_LENGTH = APIKey._meta.get_field("name").max_length


def issue_key_token(session):
    """A new token for a Keys page, remembered in the session with the last KEPT_TOKENS - 1 unspent ones."""
    held = session.get(KEY_TOKENS)
    kept = [other for other in held if isinstance(other, str)] if isinstance(held, list) else []
    token = secrets.token_urlsafe(16)
    session[KEY_TOKENS] = [*kept[-(KEPT_TOKENS - 1):], token]
    return token


def spend_key_token(session, token):
    """True once for an unspent token the session holds; the token is gone from the session before this returns. Anything else (a spent token,
    one of another session, none at all) is False.

    The session list alone cannot stop a double click: two requests read the same session before either saves. So the spend is also a
    cache.add of the token, which succeeds for one caller only (locmem holds a lock; the production DatabaseCache inserts on a unique key).
    """
    held = session.get(KEY_TOKENS)
    if not isinstance(token, str) or not token or not isinstance(held, list) or token not in held:
        return False
    if not cache.add(f"dashboard-key-token:{token}", 1, SPENT_FOR):
        return False
    session[KEY_TOKENS] = [other for other in held if other != token]
    return True


class KeyForm(forms.Form):
    """The new key's name: optional (the key is then "Untitled key"), one line, at most as long as the model allows. The same rules as the
    JSON endpoint's, said as sentences for a person."""

    name = forms.CharField(
        required=False,
        strip=True,
        max_length=NAME_LENGTH,
        error_messages={
            "max_length": f"Use {NAME_LENGTH} characters or fewer.",
            "null_characters_not_allowed": "Use a single line of plain text.",
        },
    )

    def clean_name(self):
        name = self.cleaned_data["name"]
        if any(unicodedata.category(char) == "Cc" for char in name):
            raise forms.ValidationError("Use a single line of plain text.")
        return name


class InviteForm(forms.Form):
    """The address to invite. drop_core.teams.invite checks it, and a bad one comes back to this field."""

    email = forms.CharField(
        strip=True,
        max_length=254,
        error_messages={
            "required": "Enter an email address.",
            "max_length": "Enter a valid email address.",
            "null_characters_not_allowed": "Enter a valid email address.",
        },
    )


MAX_COUNT = 2_147_483_647  # the largest a PositiveIntegerField holds on Postgres
MAX_BYTES = 2**63  # a PositiveBigIntegerField holds less than this
WHOLE_NUMBER = re.compile(r"[0-9]+")
GB_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?")
COUNT_ERROR = f"Enter a whole number from 0 to {MAX_COUNT:,}, or leave this empty."
GB_ERROR = f"Enter a number of GB, such as 5 or 0.5, below {MAX_BYTES // GB:,}, or leave this empty."


def gb_text(size_bytes):
    """A stored size as the number of GB the allowance form shows, or an empty string for no allowance."""
    return "" if size_bytes is None else f"{Decimal(size_bytes) / GB:f}"


class AllowanceForm(forms.Form):
    """What one person may use. An empty field is no allowance, so cleaned values are a number or None; GB are turned into bytes here."""

    uploads = forms.CharField(required=False, strip=True, max_length=40, label="Uploads a month")
    storage_gb = forms.CharField(required=False, strip=True, max_length=40, label="Storage in GB")
    keys = forms.CharField(required=False, strip=True, max_length=40, label="API keys")

    def _count(self, name):
        value = self.cleaned_data[name]
        if not value:
            return None
        if not WHOLE_NUMBER.fullmatch(value) or int(value) > MAX_COUNT:
            raise forms.ValidationError(COUNT_ERROR)
        return int(value)

    def clean_uploads(self):
        return self._count("uploads")

    def clean_keys(self):
        return self._count("keys")

    def clean_storage_gb(self):
        value = self.cleaned_data["storage_gb"]
        if not value:
            return None
        if not GB_NUMBER.fullmatch(value):
            raise forms.ValidationError(GB_ERROR)
        size = int(Decimal(value) * GB)
        if size >= MAX_BYTES:
            raise forms.ValidationError(GB_ERROR)
        return size
