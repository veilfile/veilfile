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

# Yearly billing of the paid plans: ten months' price for a year, so two months are free. Only the price and the billing
# interval differ. A yearly subscriber is on the same plan, with the same caps, as a monthly one: every cap above is
# enforced from PLANS whichever way the plan is paid. The Stripe prices are separate objects (drop_billing/account.py).
ANNUAL_PRICE_USD = {"team": 40, "team_plus": 60, "scale": 120}


def plan_name(slug):
    """A plan as the pages name it: its slug capitalised, with an underscore read as a space ("team_plus" is "Team Plus")."""
    return str(slug).replace("_", " ").title()
