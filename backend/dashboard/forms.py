"""Forms of the dashboard: the new-key form and the one-time token that keeps a resent POST from making a second key."""
import secrets
import unicodedata

from django import forms
from django.core.cache import cache

from drop_core.models import APIKey

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
