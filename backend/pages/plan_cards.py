"""The four plans as the plan component takes them (spec 4.3). Every figure comes from
drop_core/plans.py; to change a price or a limit, follow AGENTS.md "Pricing changes"."""
from django.urls import reverse

from drop_core.plans import PLANS, plan_name

PLAN_ORDER = ("free", "team", "team_plus", "scale")


def plan_cards():
    cards = []
    for slug in PLAN_ORDER:
        caps, name = PLANS[slug], plan_name(slug)
        if caps["keys"] is None:
            keys = "Unlimited keys"
        else:
            keys = "1 key" if caps["keys"] == 1 else f"{caps['keys']} keys"
        cards.append({
            "slug": slug,
            "name": name,
            "price": "$0" if caps["price_usd"] == 0 else f"${caps['price_usd']} a month",
            "limits": [
                f"{caps['uploads']:,} uploads a month",
                f"Links last up to {caps['ttl_days']} days",
                f"{caps['storage_gb']} GB of storage",
                keys,
            ],
            "signup_url": f"{reverse('signup')}?plan={slug}",
            "action": "Start free" if caps["price_usd"] == 0 else f"Choose {name}",
        })
    return cards
