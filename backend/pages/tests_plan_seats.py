"""The Plans page states seats only while team seats are on (team seats F2; spec section 4, call 4)."""
from django.test import TestCase, override_settings
from django.urls import reverse

from pages.plan_cards import plan_cards
from pages.tests_chapters import visible_text

SEATS = {"free": "1 person", "team": "Up to 5 people", "team_plus": "Up to 10 people", "scale": "Unlimited people"}
WHO_CAN_BE_INVITED = (
    "On Team, Team Plus and Scale, the administrator invites people by email. Everyone on a team shares its uploads, "
    "storage and keys, and the administrator can set how much each person may use."
)


def plans_text():
    return visible_text(TestCase.client_class().get(reverse("plans")).content.decode())


class PlanSeatsTests(TestCase):
    @override_settings(TEAM_SEATS=True)
    def test_each_card_states_its_seats_first(self):
        cards = {card["slug"]: card for card in plan_cards()}
        self.assertEqual({slug: cards[slug]["limits"][0] for slug in SEATS}, SEATS)

    @override_settings(TEAM_SEATS=False)
    def test_the_switch_off_cards_are_todays(self):
        for card in plan_cards():
            with self.subTest(plan=card["slug"]):
                self.assertEqual(len(card["limits"]), 4)
                self.assertFalse(any("person" in limit or "people" in limit for limit in card["limits"]))

    def test_the_page_says_who_can_be_invited(self):
        with self.settings(TEAM_SEATS=True):
            self.assertIn(WHO_CAN_BE_INVITED, plans_text())
        with self.settings(TEAM_SEATS=False):
            text = plans_text()
            self.assertNotIn("invites people", text)
            self.assertNotIn("people", text)
