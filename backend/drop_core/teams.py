"""Team seats: the switch, and what hangs off it."""
from functools import wraps

from django.conf import settings

from pages.errors import not_found


def team_seats_only(view):
    """`view`, but the shell's 404 while the switch is off, before anything else runs."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not settings.TEAM_SEATS:
            return not_found(request)
        return view(request, *args, **kwargs)

    return wrapper
