"""Joining a team from a paid plan (team seats PR D2): a person's own live subscriptions are ended at Stripe by their status, outside
any lock, and Stripe's replies are read back before the listing is applied once.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it).
"""
import copy
import logging
import os
from unittest import mock

import stripe
from django.db import connection
from django.test import Client, override_settings
from django.utils import timezone

from drop_billing import account
from drop_billing.models import Subscription
from drop_billing.tests_fix_billing import FixBillingBase, make_sub, missing_customer_error, sub_list
from drop_core.models import APIKey, EmailVerification, Workspace, administrator_of
from drop_core.teams import invite
from drop_core.tests_invitations import url
from drop_core.tests_teams import team_of

ACCOUNT = "backend/drop_billing/account.py"
NOT_APPLIED = "changed while its subscriptions were being read, so that listing was not applied"


def messages(logged, level):
    """The text of each captured record at exactly `level`."""
    return [record.getMessage() for record in logged.records if record.levelno == level]


@override_settings(TEAM_SEATS=True)
class PaidJoinBase(FixBillingBase):
    """The payer on Team with subscription sub_A, invited to Ada's team, and a mocked Stripe that holds `at_stripe`."""

    longMessage = False

    def setUp(self):
        super().setUp()
        EmailVerification.objects.create(user=self.user, email=self.user.email, is_verified=True, verified_at=timezone.now())
        self.team, self.admin = team_of("ada")
        sent = []
        self.invitation = invite(self.admin, "payer@example.com", build_url=lambda raw: sent.append(raw) or url(raw))
        self.raw = sent[0]
        self.set_billing("team", sub_id="sub_A")
        self.old_key, self.old_raw = APIKey.create_key(self.ws, "old", member=administrator_of(self.ws))
        self.web = Client()
        self.web.force_login(self.user)
        self.at_stripe = {"sub_A": make_sub("sub_A", "active", "price_team")}
        self.calls = []
        self.listed = self.patch_stripe("list", side_effect=self.listing)
        self.cancel = self.patch_stripe("cancel", side_effect=self.canceling)
        self.void = self.patch_stripe("void", side_effect=self.voiding)

    def listing(self, *args, **kwargs):
        """What Stripe lists now, copied, so that a listing never changes after it is returned."""
        return sub_list(*copy.deepcopy(list(self.at_stripe.values())))

    def canceling(self, sub_id, **kwargs):
        self.calls.append(f"cancel {sub_id}")
        self.at_stripe[sub_id]["status"] = "canceled"
        return copy.deepcopy(self.at_stripe[sub_id])

    def voiding(self, invoice_id, **kwargs):
        self.calls.append(f"void {invoice_id}")
        return {"id": invoice_id, "status": "void"}

    def join(self, consent=True, client=None, **extra):
        data = {"action": "accept", "own_plan": "ends"} if consent else {"action": "accept"}
        return (client or self.web).post(url(self.raw), data, **extra)


class EndingTests(PaidJoinBase):
    def end(self):
        stripe_mod, _ = account._stripe_or_503()
        return account._end_live_subscriptions(stripe_mod, self.ws)

    def state(self):
        self.reload()
        return (self.ws.plan, self.ws.stripe_subscription_id)

    def mirror(self):
        return Subscription.objects.get(workspace=self.ws).status

    def test_each_live_state_is_ended_by_its_rule(self):
        pending = {"expires_at": 1790000000, "subscription_items": [{"id": "si_1", "price": "price_teamplus"}]}
        cases = {
            "active": ("active", {"latest_invoice": "in_A"}, True, ["cancel sub_A"]),
            "active_ending_at_period_end": ("active", {"latest_invoice": "in_A", "cancel_at_period_end": True}, True, ["cancel sub_A"]),
            "active_with_pending_update": ("active", {"latest_invoice": "in_A", "pending_update": pending}, True,
                                           ["void in_A", "cancel sub_A"]),
            "past_due": ("past_due", {"latest_invoice": "in_A"}, False, ["void in_A", "cancel sub_A"]),
            "past_due_without_an_invoice": ("past_due", {}, False, ["cancel sub_A"]),
            "unpaid": ("unpaid", {"latest_invoice": "in_A"}, False, ["void in_A", "cancel sub_A"]),
            "trialing": ("trialing", {"latest_invoice": "in_A"}, False, ["cancel sub_A"]),
            "paused": ("paused", {"latest_invoice": "in_A"}, False, ["cancel sub_A"]),
            "incomplete": ("incomplete", {"latest_invoice": "in_A"}, False, ["cancel sub_A"]),
        }
        for case, (status, extra, prorate, calls) in cases.items():
            with self.subTest(case=case):
                self.set_billing("team", sub_id="sub_A")
                sub = make_sub("sub_A", status, "price_team")
                sub.update(extra)
                self.at_stripe = {"sub_A": sub}
                self.calls = []
                for patched in (self.listed, self.cancel, self.void):
                    patched.reset_mock()
                ending = self.end()
                self.assertEqual(self.cancel.call_args,
                                 mock.call("sub_A", prorate=prorate, invoice_now=prorate, max_network_retries=0),
                                 f"{ACCOUNT}: _how_to_end gives {case} prorate={prorate}, with invoice_now the same")
                self.assertEqual(self.calls, calls, f"{ACCOUNT}: {case} sends {calls}, each void just before its cancel")
                self.assertEqual(self.state(), ("free", ""), f"{ACCOUNT}: the read-back of {case} is applied")
                self.assertEqual(ending, account.Ending(True, True, False), f"{ACCOUNT}: the ending of {case} is done and cut")

    def test_the_cancel_sends_exactly_its_parameters(self):
        self.end()
        self.assertEqual(set(self.cancel.call_args.kwargs), {"prorate", "invoice_now", "max_network_retries"},
                         f"{ACCOUNT}: the cancel names both flags and no retries, and nothing else")

    def test_a_failed_void_sends_no_cancel_and_still_reads_back(self):
        self.at_stripe["sub_A"].update(status="past_due", latest_invoice="in_A")
        self.void.side_effect = stripe.InvalidRequestError("secret detail", "invoice")
        with self.assertLogs("drop_billing", level="WARNING") as logged:
            ending = self.end()
        self.cancel.assert_not_called()
        self.assertEqual(self.listed.call_count, 2, f"{ACCOUNT}: a failed void is still read back")
        self.assertEqual(ending, account.Ending(False, False, True), f"{ACCOUNT}: a failed void leaves the plan live, so it is stuck")
        warnings = messages(logged, logging.WARNING)
        self.assertEqual(len(warnings), 1, f"{ACCOUNT}: the failed void logs one WARNING")
        self.assertIn("InvalidRequestError", warnings[0], f"{ACCOUNT}: the WARNING names the class")
        self.assertNotIn("secret detail", "\n".join(logged.output), f"{ACCOUNT}: no line carries Stripe's text")
        errors = messages(logged, logging.ERROR)
        self.assertEqual(len(errors), 1, f"{ACCOUNT}: one ERROR names the subscription that is still live")
        self.assertIn("sub_A", errors[0], f"{ACCOUNT}: the ERROR names the subscription")
        self.assertIn("past_due", errors[0], f"{ACCOUNT}: the ERROR names the subscription's status")

    def test_a_cancel_whose_answer_was_lost_is_read_back_as_ended(self):
        def lost(sub_id, **kwargs):
            self.calls.append(f"cancel {sub_id}")
            self.at_stripe[sub_id]["status"] = "canceled"
            raise stripe.APIConnectionError("secret detail")

        self.cancel.side_effect = lost
        with self.assertLogs("drop_billing", level="WARNING") as logged:
            ending = self.end()
        self.assertEqual(ending, account.Ending(True, True, False), f"{ACCOUNT}: the read-back shows the lost cancel took effect")
        self.assertEqual(self.listed.call_count, 2, f"{ACCOUNT}: a lost answer is read back")
        self.assertEqual(self.state(), ("free", ""), f"{ACCOUNT}: the read-back is applied")
        self.assertEqual(self.mirror(), "canceled", f"{ACCOUNT}: the mirror shows the read-back")
        warnings = messages(logged, logging.WARNING)
        self.assertEqual(len(warnings), 1, f"{ACCOUNT}: the lost answer logs one WARNING")
        self.assertIn("APIConnectionError", warnings[0], f"{ACCOUNT}: the WARNING names the class")
        self.assertNotIn("secret detail", "\n".join(logged.output), f"{ACCOUNT}: no line carries Stripe's text")
        self.assertEqual(messages(logged, logging.ERROR), [], f"{ACCOUNT}: a plan the read-back shows ended logs no ERROR")

    def test_the_cancel_reply_replaces_a_stale_listing(self):
        def reply_only(sub_id, **kwargs):
            self.calls.append(f"cancel {sub_id}")
            answer = copy.deepcopy(self.at_stripe[sub_id])
            answer["status"] = "canceled"
            return answer

        self.cancel.side_effect = reply_only
        ending = self.end()
        self.assertEqual(ending, account.Ending(True, True, False), f"{ACCOUNT}: the cancel's reply replaces its id in the read-back")
        self.assertEqual(self.state(), ("free", ""), f"{ACCOUNT}: the reply, not the stale listing, is applied")

    def test_a_refused_cancel_writes_what_stripe_still_holds(self):
        self.cancel.side_effect = stripe.InvalidRequestError("secret detail", "subscription")
        with self.assertLogs("drop_billing", level="WARNING") as logged:
            ending = self.end()
        self.assertEqual(ending, account.Ending(False, False, True), f"{ACCOUNT}: a refused cancel is stuck")
        self.assertEqual(self.state(), ("team", "sub_A"), f"{ACCOUNT}: the read-back still shows the plan")
        self.assertEqual(self.mirror(), "active", f"{ACCOUNT}: the mirror shows the read-back")
        errors = messages(logged, logging.ERROR)
        self.assertEqual(len(errors), 1, f"{ACCOUNT}: one ERROR names the subscription that is still live")
        self.assertIn("sub_A", errors[0], f"{ACCOUNT}: the ERROR names the subscription")

    def test_a_new_subscription_seen_by_the_read_back_is_not_ended(self):
        def cancel_and_buy(sub_id, **kwargs):
            answer = self.canceling(sub_id, **kwargs)
            self.at_stripe["sub_B"] = make_sub("sub_B", "active", "price_teamplus", created=2)
            return answer

        self.cancel.side_effect = cancel_and_buy
        with self.assertNoLogs("drop_billing", level="ERROR"):
            ending = self.end()
        self.assertEqual(ending, account.Ending(False, True, False),
                         f"{ACCOUNT}: a plan the first listing did not show is not ended and is not stuck")
        self.assertEqual(self.cancel.call_count, 1, f"{ACCOUNT}: only what the first listing showed is canceled")
        self.assertEqual(self.state(), ("team_plus", "sub_B"), f"{ACCOUNT}: the read-back is applied")

    def test_a_subscription_a_webhook_wrote_after_the_read_back_listing_is_kept(self):
        listings = []

        def listing_then_webhook(*args, **kwargs):
            answer = self.listing()
            listings.append(answer)
            if len(listings) == 2:
                Workspace.objects.filter(pk=self.ws.pk).update(plan="team_plus", stripe_subscription_id="sub_B")
            return answer

        self.listed.side_effect = listing_then_webhook
        with self.assertLogs("drop_billing", level="INFO") as logged:
            ending = self.end()
        self.assertEqual(ending, account.Ending(False, True, False), f"{ACCOUNT}: a listing older than the row is not done")
        self.assertEqual(self.state(), ("team_plus", "sub_B"), f"{ACCOUNT}: known_ids keeps the row the webhook wrote")
        self.assertIn(f"billing: workspace {self.ws.pk} {NOT_APPLIED}", messages(logged, logging.INFO),
                      f"{ACCOUNT}: the skipped listing is logged")
        self.assertEqual(messages(logged, logging.ERROR), [], f"{ACCOUNT}: a skipped listing logs no ERROR")

    def test_an_id_that_stripe_does_not_list_is_applied_as_today(self):
        Workspace.objects.filter(pk=self.ws.pk).update(plan="team", stripe_subscription_id="sub_gone")
        self.reload()
        self.at_stripe["sub_A"]["status"] = "canceled"
        with self.assertLogs("drop_billing", level="INFO") as logged:
            ending = self.end()
        self.cancel.assert_not_called()
        self.assertEqual(self.listed.call_count, 1, f"{ACCOUNT}: nothing live needs no read-back")
        self.assertEqual(self.state(), ("free", ""), f"{ACCOUNT}: the id the row held when the ending began is known")
        self.assertEqual(ending, account.Ending(True, False, False), f"{ACCOUNT}: the listing was applied, so the ending is done")
        self.assertFalse(any(NOT_APPLIED in line for line in logged.output), f"{ACCOUNT}: that listing is applied")

    def test_a_subscription_that_names_this_workspace_is_ended(self):
        self.at_stripe = {"sub_A": make_sub("sub_A", "active", "price_team", metadata={"workspace_id": str(self.ws.pk)})}
        ending = self.end()
        self.assertEqual(self.calls, ["cancel sub_A"], f"{ACCOUNT}: a subscription Checkout made for this workspace is ended")
        self.assertEqual(ending, account.Ending(True, True, False), f"{ACCOUNT}: the ending is done and cut")
        self.assertEqual(self.state(), ("free", ""), f"{ACCOUNT}: the read-back is applied")

    def test_a_subscription_of_another_workspace_on_the_same_customer_is_not_ended(self):
        other, _ = team_of("lin")
        Workspace.objects.filter(pk=other.pk).update(stripe_customer_id="cus_A")
        self.at_stripe["sub_X"] = make_sub("sub_X", "active", "price_teamplus", created=2, metadata={"workspace_id": str(other.pk)})
        with self.assertLogs("drop_billing", level="ERROR") as logged:
            ending = self.end()
        self.assertEqual(self.calls, ["cancel sub_A"], f"{ACCOUNT}: another workspace's subscription gets no void and no cancel")
        self.assertEqual(ending, account.Ending(False, True, True), f"{ACCOUNT}: the subscription left alone makes the ending stuck")
        self.assertEqual(self.state(), ("team_plus", "sub_X"), f"{ACCOUNT}: the plan derives from every subscription on the customer")
        self.assertEqual(
            messages(logged, logging.ERROR),
            [f"billing: subscription sub_X on the customer of workspace {self.ws.pk} names workspace {other.pk}, so it was not ended"],
            f"{ACCOUNT}: exactly one ERROR, the line that names both workspaces",
        )

    def test_a_failed_read_back_writes_nothing(self):
        self.listed.side_effect = [self.listing(), stripe.APIConnectionError("secret detail")]
        with self.assertLogs("drop_billing", level="ERROR") as logged:
            ending = self.end()
        self.assertEqual(ending, account.Ending(False, True, True), f"{ACCOUNT}: the cancel's reply showed canceled")
        self.assertEqual(self.state(), ("team", "sub_A"), f"{ACCOUNT}: a failed read-back writes nothing")
        self.assertEqual(self.mirror(), "active", f"{ACCOUNT}: a failed read-back leaves the mirror alone")
        self.assertEqual(messages(logged, logging.ERROR),
                         [f"billing: the subscriptions of workspace {self.ws.pk} could not be read: APIConnectionError"],
                         f"{ACCOUNT}: the ERROR names the class only")
        self.assertNotIn("secret detail", "\n".join(logged.output), f"{ACCOUNT}: no line carries Stripe's text")

    def test_a_failed_first_listing_sends_nothing_and_writes_nothing(self):
        self.listed.side_effect = stripe.APIConnectionError("secret detail")
        with self.assertLogs("drop_billing", level="ERROR") as logged:
            ending = self.end()
        self.void.assert_not_called()
        self.cancel.assert_not_called()
        self.assertEqual(ending, account.Ending(False, False, True), f"{ACCOUNT}: a failed listing is stuck")
        self.assertEqual(self.state(), ("team", "sub_A"), f"{ACCOUNT}: a failed listing writes nothing")
        self.assertEqual(messages(logged, logging.ERROR),
                         [f"billing: the subscriptions of workspace {self.ws.pk} could not be read: APIConnectionError"],
                         f"{ACCOUNT}: the ERROR names the class only")
        self.assertNotIn("secret detail", "\n".join(logged.output), f"{ACCOUNT}: no line carries Stripe's text")

    def test_two_live_subscriptions_are_both_ended_and_applied_once(self):
        self.at_stripe["sub_U"] = make_sub("sub_U", "unpaid", "price_team", created=2)
        self.at_stripe["sub_U"]["latest_invoice"] = "in_U"
        with mock.patch.object(account, "_sync_from_subscriptions", wraps=account._sync_from_subscriptions) as synced:
            ending = self.end()
        self.assertEqual(self.calls, ["cancel sub_A", "void in_U", "cancel sub_U"],
                         f"{ACCOUNT}: each subscription in the first listing's order, each void just before its cancel")
        self.assertEqual(ending, account.Ending(True, True, False), f"{ACCOUNT}: both ended, so the ending is done")
        self.assertEqual(synced.call_count, 1, f"{ACCOUNT}: the result is applied once, at the end")

    def test_nothing_live_is_applied_from_the_first_listing(self):
        self.at_stripe["sub_A"]["status"] = "canceled"
        ending = self.end()
        self.cancel.assert_not_called()
        self.assertEqual(self.listed.call_count, 1, f"{ACCOUNT}: nothing live needs no read-back")
        self.assertEqual(self.state(), ("free", ""), f"{ACCOUNT}: the first listing is applied")
        self.assertEqual(ending, account.Ending(True, False, False), f"{ACCOUNT}: nothing live is done, and nothing was cut")

    def test_a_missing_customer_is_forgotten(self):
        self.listed.side_effect = missing_customer_error("cus_A")
        ending = self.end()
        self.assertEqual(ending, account.Ending(True, False, False), f"{ACCOUNT}: a missing customer counts as done")
        self.assertEqual(self.reload().stripe_customer_id, "", f"{ACCOUNT}: a missing customer goes through _forget_missing_customer")

    def test_every_stripe_call_runs_outside_any_transaction(self):
        self.at_stripe["sub_A"].update(status="past_due", latest_invoice="in_A")
        depths = []

        def recorded(real):
            def record(*args, **kwargs):
                depths.append(len(connection.savepoint_ids))
                return real(*args, **kwargs)
            return record

        for patched in (self.listed, self.void, self.cancel):
            patched.side_effect = recorded(patched.side_effect)
        baseline = len(connection.savepoint_ids)
        self.end()
        self.assertEqual(self.calls, ["void in_A", "cancel sub_A"], f"{ACCOUNT}: past_due voids, then cancels")
        self.assertEqual(depths, [baseline] * 4, f"{ACCOUNT}: the listings, the void and the cancel run outside any transaction")

    def test_the_ending_stops_at_the_first_workspace_that_does_not_end(self):
        other = Workspace.objects.create(owner=self.user, name="payer's second workspace", plan="team",
                                         stripe_customer_id="cus_B", stripe_subscription_id="sub_B")
        self.assertEqual(administrator_of(other).user, self.user, "the payer administers the second workspace")
        self.cancel.side_effect = stripe.InvalidRequestError("secret detail", "subscription")
        with self.assertLogs("drop_billing", level="ERROR"):
            ending = account.end_plans_before_joining([self.ws, other])
        self.assertEqual(ending, account.Ending(False, False, True), f"{ACCOUNT}: the first workspace's ending is stuck")
        listed = [call.kwargs.get("customer") for call in self.listed.call_args_list]
        self.assertIn("cus_A", listed, f"{ACCOUNT}: the first workspace's customer is listed")
        self.assertNotIn("cus_B", listed, f"{ACCOUNT}: the ending stops at the first workspace whose ending is not done")

    def test_without_stripe_configured_nothing_is_called(self):
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": ""}):
            with self.assertLogs("drop_billing", level="ERROR") as logged:
                ending = account.end_plans_before_joining([self.ws])
        self.assertEqual(ending, account.Ending(False, False, True), f"{ACCOUNT}: an ending without Stripe is stuck")
        self.listed.assert_not_called()
        self.cancel.assert_not_called()
        self.assertIn(f"billing: the plan of workspace {self.ws.pk} was not ended: billing is not configured",
                      messages(logged, logging.ERROR), f"{ACCOUNT}: the not-configured ERROR is logged")

    def test_with_the_switch_off_the_join_helper_asks_nothing(self):
        with override_settings(TEAM_SEATS=False), self.assertNumQueries(0):
            ending = account.end_plans_before_joining([self.ws])
        self.assertEqual(ending, account.Ending(False, False, False), f"{ACCOUNT}: with the switch off nothing is ended")
        self.listed.assert_not_called()
        self.void.assert_not_called()
        self.cancel.assert_not_called()

    def test_no_refund_or_credit_note_is_made(self):
        with mock.patch.object(stripe.Refund, "create") as refund, mock.patch.object(stripe.CreditNote, "create") as credit_note:
            ending = self.end()
        self.assertTrue(ending.done, f"{ACCOUNT}: the active plan ends")
        refund.assert_not_called()
        credit_note.assert_not_called()
