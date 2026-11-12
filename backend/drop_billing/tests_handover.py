"""Billing at a transfer (team seats, billing): Stripe's email follows the new administrator after the commit, the new
administrator is asked to check the card until they open Manage billing, and the team portal configuration is passed only to a team.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it).
"""
import os
from datetime import timedelta
from io import StringIO
from unittest import mock

import stripe
from django.core.management import call_command
from django.db import connection
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from drop_billing import account as billing_account
from drop_billing.tests_fix_billing import FixBillingBase, make_sub, sub_list
from drop_core import teams
from drop_core.models import Membership, Workspace, administrator_of
from drop_core.tests_members import second_member

ACCOUNT = "backend/drop_billing/account.py"
ACCEPT = "/app/team/administrator/"
BILLING = "/app/billing/"
NOTICE = (
    "You are now this team's administrator, so its plan is yours to pay. The card on file is the one the former "
    "administrator added. Check it, and change it if you need to, under Manage billing."
)


@override_settings(TEAM_SEATS=True)
class HandoverBase(FixBillingBase):
    longMessage = False

    def setUp(self):
        super().setUp()
        self.grace = second_member(self.ws, "grace")
        self.set_billing("team", sub_id="sub_A")
        self.modify = self.patch_stripe("customer_modify", return_value={"id": "cus_A"})
        self.portal_session = self.patch_stripe("portal", return_value={"url": "https://billing.stripe.test/p/1"})

    def transfer(self):
        """Offers the role to Grace and has her accept it through the page."""
        teams.offer_administrator(administrator_of(self.ws), self.grace.pk)
        client = Client()
        client.force_login(self.grace.user)
        response = client.post(ACCEPT, {"action": "accept"})
        self.assertEqual(response.status_code, 302)
        self.reload()
        return client

    def page(self, user):
        client = Client()
        client.force_login(user)
        return client.get(BILLING).content.decode()


class MoveEmailTests(HandoverBase):
    def test_the_transfer_moves_stripes_email_after_the_commit(self):
        depths = []
        baseline = len(connection.savepoint_ids)

        def record(*args, **kwargs):
            depths.append(len(connection.savepoint_ids))
            return {"id": "cus_A"}

        self.modify.side_effect = record
        self.transfer()
        self.modify.assert_called_once_with("cus_A", email="grace@example.com")
        self.assertEqual(depths, [baseline], f"{ACCOUNT}: Customer.modify runs outside any transaction")
        self.assertIsNotNone(self.ws.billing_handed_over_at, "the marker is set with a subscription")

    def test_a_failed_email_move_keeps_the_transfer_and_the_portal_repairs_it(self):
        self.modify.side_effect = stripe.APIConnectionError("secret detail of the failure")
        with self.assertLogs("drop_billing", level="WARNING") as logged:
            self.transfer()
        self.assertEqual(administrator_of(self.ws).user, self.grace.user, "the transfer stands")
        text = "\n".join(logged.output)
        self.assertIn("APIConnectionError", text)
        self.assertNotIn("secret detail", text, f"{ACCOUNT}: the log line names the class only")
        self.assertIsNotNone(self.ws.billing_handed_over_at)
        self.modify.reset_mock(side_effect=True)
        answer = billing_account.portal(self.grace.user)
        self.assertEqual(answer.status, 200)
        self.modify.assert_called_once_with("cus_A", email="grace@example.com")
        self.assertIsNone(self.reload().billing_handed_over_at, "the portal clears the marker once the move and the session worked")

    def test_a_failed_move_in_the_portal_still_opens_it_and_keeps_the_marker(self):
        self.transfer()
        self.modify.side_effect = stripe.APIConnectionError("down")
        with self.assertLogs("drop_billing", level="WARNING"):
            self.assertEqual(billing_account.portal(self.grace.user).status, 200)
        self.assertIsNotNone(self.reload().billing_handed_over_at, "a later portal repeats the move")

    def test_without_stripe_configured_no_call_is_made_and_the_marker_stays(self):
        self.transfer()
        self.modify.reset_mock()
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": ""}):
            with self.assertLogs("drop_billing", level="WARNING"):
                self.assertFalse(billing_account.move_billing_email(self.ws))
            billing_account.portal(self.grace.user)
        self.modify.assert_not_called()
        self.assertIsNotNone(self.reload().billing_handed_over_at)

    def test_a_workspace_with_no_customer_is_moved_in_name_only(self):
        Workspace.objects.filter(pk=self.ws.pk).update(stripe_customer_id="")
        self.ws.refresh_from_db()
        self.assertTrue(billing_account.move_billing_email(self.ws))
        self.modify.assert_not_called()

    def test_a_second_transfer_keeps_its_own_marker(self):
        self.transfer()
        newer = timezone.now() + timedelta(minutes=5)

        def second_transfer(*args, **kwargs):
            Workspace.objects.filter(pk=self.ws.pk).update(billing_handed_over_at=newer)
            return {"id": "cus_A"}

        self.modify.side_effect = second_transfer
        billing_account.portal(self.grace.user)
        self.assertEqual(self.reload().billing_handed_over_at, newer, f"{ACCOUNT}: the clear filters on the value it read")


class FollowTests(HandoverBase):
    def test_billing_follows_the_new_administrator(self):
        self.transfer()
        before = (self.ws.stripe_customer_id, self.ws.stripe_subscription_id)
        self.assertEqual(billing_account.portal(self.grace.user).status, 200)
        self.patch_stripe("list", return_value=sub_list(make_sub("sub_A", "active", "price_team")))
        self.patch_stripe("modify", return_value=make_sub("sub_A", "active", "price_scale"))
        with self.assertLogs("drop_billing", level="INFO"):
            self.assertEqual(billing_account.switch(self.grace.user, "scale").status, 200)
        former = billing_account.portal(self.user)
        self.assertEqual((former.status, former.body), (403, {"error": "administrator_only"}))
        self.assertEqual(billing_account.switch(self.user, "team").status, 403)
        self.reload()
        self.assertEqual(before, (self.ws.stripe_customer_id, self.ws.stripe_subscription_id))
        self.assertEqual(self.ws.subscription.stripe_subscription_id, "sub_A")

    def test_a_team_with_no_subscription_moves_nothing_and_shows_no_notice(self):
        self.set_billing("free", customer="", sub_id="")
        self.transfer()
        self.modify.assert_not_called()
        self.assertIsNone(self.ws.billing_handed_over_at)
        self.assertNotIn(NOTICE, self.page(self.grace.user))

    def test_the_new_administrator_is_asked_to_check_the_card_until_manage_billing(self):
        self.transfer()
        self.assertIn(NOTICE, self.page(self.grace.user).replace("&#x27;", "'").replace("&#39;", "'"))
        former = self.page(self.user)
        self.assertIn("Your administrator manages billing for this team.", former)
        self.assertNotIn("check the card", former.lower())
        self.assertEqual(billing_account.portal(self.grace.user).status, 200)
        self.assertNotIn("The card on file", self.page(self.grace.user))

    def test_a_missing_customer_takes_the_notice_with_it(self):
        self.transfer()
        with self.assertLogs("drop_billing", level="WARNING"):
            billing_account._forget_missing_customer(self.ws, "cus_A")
        self.assertIsNone(self.reload().billing_handed_over_at)
        self.assertNotIn("The card on file", self.page(self.grace.user))

    def test_the_admin_shows_the_marker_read_only(self):
        from django.contrib import admin

        self.assertIn("billing_handed_over_at", admin.site._registry[Workspace].readonly_fields)


class PortalConfigurationTests(HandoverBase):
    def configuration(self, user):
        self.portal_session.reset_mock()
        billing_account.portal(user)
        return self.portal_session.call_args.kwargs

    def test_the_team_portal_configuration_is_passed_only_for_a_team(self):
        with override_settings(STRIPE_TEAM_PORTAL_CONFIGURATION="bpc_team"):
            kwargs = self.configuration(self.user)
            self.assertEqual(kwargs["configuration"], "bpc_team")
            with override_settings(TEAM_SEATS=False):
                self.assertNotIn("configuration", self.configuration(self.user))
        with override_settings(STRIPE_TEAM_PORTAL_CONFIGURATION=""):
            self.assertNotIn("configuration", self.configuration(self.user))

    def test_a_one_person_portal_makes_the_same_call_as_on_main(self):
        Membership.objects.filter(pk=self.grace.pk).update(removed_at=timezone.now())
        with override_settings(STRIPE_TEAM_PORTAL_CONFIGURATION="bpc_team"):
            kwargs = self.configuration(self.user)
        self.assertEqual(kwargs, {"customer": "cus_A", "return_url": "https://veilfile.test/app/"})


class MigrationTests(TransactionTestCase):
    longMessage = False

    def test_the_migration_adds_a_nullable_column_only(self):
        out = StringIO()
        call_command("sqlmigrate", "drop_core", "0021", stdout=out)
        sql = out.getvalue()
        self.assertEqual(sql.upper().count("ADD COLUMN"), 1, sql)
        self.assertIn("billing_handed_over_at", sql)
        self.assertNotIn("DEFAULT", sql.upper(), "a column with no default is safe on a live table")
        self.assertNotIn("NOT NULL", sql.upper())
