"""The four plans as the plan component takes them (spec 4.3). Every figure comes from
drop_core/plans.py; to change a price or a limit, follow AGENTS.md "Pricing changes"."""
from django.conf import settings
from django.urls import reverse

from drop_core.plans import ANNUAL_PRICE_USD, PLANS, plan_name

PLAN_ORDER = ("free", "team", "team_plus", "scale")


def seats_phrase(seats):
    """How many people a plan holds, as its card says it. ``None`` is unlimited."""
    if seats is None:
        return "Unlimited people"
    return "1 person" if seats == 1 else f"Up to {seats} people"


def plan_cards():
    cards = []
    for slug in PLAN_ORDER:
        caps, name = PLANS[slug], plan_name(slug)
        if caps["keys"] is None:
            keys = "Unlimited keys"
        else:
            keys = "1 key" if caps["keys"] == 1 else f"{caps['keys']} keys"
        limits = [
            f"{caps['uploads']:,} uploads a month",
            f"New links last up to {caps['ttl_days']} days",
            f"{caps['storage_gb']} GB of storage",
            keys,
        ]
        if settings.TEAM_SEATS:
            # Seats are stated only once team seats are live (spec section 4, call 4).
            limits.insert(0, seats_phrase(caps["seats"]))
        cards.append({
            "slug": slug,
            "name": name,
            "price": "$0" if caps["price_usd"] == 0 else f"${caps['price_usd']} a month",
            "limits": limits,
            "signup_url": f"{reverse('signup')}?plan={slug}",
            "action": "Start free" if caps["price_usd"] == 0 else f"Choose {name}",
            # A paid plan can also be billed yearly, at ten months' price: the same plan and limits, through the same sign-up.
            "annual_price": f"${ANNUAL_PRICE_USD[slug]} a year, two months free" if slug in ANNUAL_PRICE_USD else "",
            "annual_signup_url": f"{reverse('signup')}?plan={slug}&interval=year" if slug in ANNUAL_PRICE_USD else "",
        })
    return cards
