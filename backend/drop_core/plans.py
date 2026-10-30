"""Veilfile plan definitions. ``keys: None`` means unlimited.

``storage_gb`` is a hard cap on live (non-expired) artifact bytes per
workspace. Worst-case storage cost is bounded by the cap alone, which is
what keeps every plan profitable regardless of upload counts.
"""

GB = 1024**3

PLANS = {
    "free": {"uploads": 100, "ttl_days": 7, "keys": 1, "price_usd": 0, "storage_gb": 1},
    "team": {"uploads": 2000, "ttl_days": 90, "keys": 5, "price_usd": 4, "storage_gb": 25},
    "team_plus": {"uploads": 4000, "ttl_days": 90, "keys": 10, "price_usd": 6, "storage_gb": 40},
    "scale": {"uploads": 10000, "ttl_days": 365, "keys": None, "price_usd": 12, "storage_gb": 100},
}


def plan_name(slug):
    """A plan as the pages name it: its slug capitalised, with an underscore read as a space ("team_plus" is "Team Plus")."""
    return str(slug).replace("_", " ").title()
