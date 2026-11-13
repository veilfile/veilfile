"""The sentence a refusal at a plan's cap carries.

A plain member of a team cannot change the team's plan, so while the team seats switch is on they read that their
administrator can. Everyone else, the administrator of the same team included, reads the sentence they read before
team seats. The JSON codes do not change, because published clients key on them.
"""

from django.conf import settings

from .models import Membership

MEMBER_CAP_MESSAGES = {
    "monthly_cap_reached": (
        "Your team has reached its plan's monthly upload cap. Uploads work again next month, or you can ask your "
        "administrator to choose a larger plan under Billing."
    ),
    "storage_cap_reached": (
        "Your team has reached its plan's storage cap. Revoke files you no longer need, or ask your administrator to "
        "choose a larger plan under Billing."
    ),
    "key_limit_reached": (
        "Your team has reached its plan's API key limit. Revoke a key, or ask your administrator to choose a larger "
        "plan under Billing."
    ),
}


def cap_message(code, member, message):
    """The member's sentence for ``code`` when ``member`` is a member row of a team and the switch is on, and
    ``message``, the sentence everyone else reads, otherwise."""
    if settings.TEAM_SEATS and member is not None and member.role == Membership.MEMBER and code in MEMBER_CAP_MESSAGES:
        return MEMBER_CAP_MESSAGES[code]
    return message
