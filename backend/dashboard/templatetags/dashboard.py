"""Template filters for the dashboard's pages: a time in UTC for the visitor's zone to rewrite, and a size in words."""
from datetime import datetime, timezone

from django import template
from django.utils.html import format_html

register = template.Library()

# English whatever the active language, so that the text the server writes is the same on every request and in every test.
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
UNITS = ("KB", "MB", "GB")


def _moment(value):
    """`value` as an aware datetime, or None when it is not one: a datetime (a naive one is UTC), or the ISO 8601 string the JSON
    endpoints answer with (2026-10-22T13:58:55.802591+00:00, or ending in Z)."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@register.filter
def utc_time(value, words=""):
    """A time as <time datetime="2026-10-22T13:58Z" data-local>22 Oct 2026, 13:58 UTC</time>, in UTC to the minute (the seconds are cut).

    The text is what a visitor reads with no script; static/js/behaviors/local-time.js rewrites it in the visitor's own zone from the
    datetime attribute, on load and after each change in place. It is UTC whatever the site's zone (a template's own date filter would print settings.TIME_ZONE,
    with no zone named, should it ever differ from UTC), so the same moment reads the same for everyone. With no time (None, empty, or a string
    that is not one) it is `words`, which a page gives as {{ when|utc_time:"Never" }}, else an empty string, for |default.
    """
    moment = _moment(value)
    if moment is None:
        return words
    moment = moment.astimezone(timezone.utc)
    return format_html(
        '<time datetime="{}" data-local>{} {} {}, {} UTC</time>',
        moment.strftime("%Y-%m-%dT%H:%MZ"), moment.day, MONTHS[moment.month - 1], moment.year, moment.strftime("%H:%M"),
    )


@register.filter(name="bytes")
def human_bytes(value):
    """A size in words, 1024-based with one decimal: 999 B, 1.0 KB, 15.5 MB, 25.0 GB (no unit above a gigabyte). A figure that one decimal
    would round up to 1024 is written in the next unit (1.0 MB, not 1024.0 KB). Anything that is not a size of zero or more is an empty string."""
    try:
        size = int(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    if size < 0:
        return ""
    if size < 1024:
        return f"{size} B"
    amount = float(size)
    for unit in UNITS:
        amount /= 1024
        if round(amount, 1) < 1024 or unit == UNITS[-1]:
            return f"{amount:.1f} {unit}"
