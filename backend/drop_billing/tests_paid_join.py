"""Joining a team from a paid plan (team seats PR D2): a person's own live subscriptions are ended at Stripe by their status, outside
any lock, and Stripe's replies are read back before the listing is applied once. The join then commits only when the mirror
shows no plan.

Every class sets longMessage = False (pages.tests_site_rules holds this module to it).
"""
import copy
import logging
import os
import re
from unittest import mock

import stripe
from django.db import IntegrityError, connection, transaction
from django.test import Client, override_settings
from django.utils import timezone

from drop_billing import account
from drop_billing.models import Subscription
from drop_billing.tests_fix_billing import FixBillingBase, make_event, make_sub, missing_customer_error, sub_list
from drop_core import teams
from drop_core.models import APIKey, EmailVerification, Invitation, Membership, Workspace, administrator_of
from drop_core.teams import JoinRefused, accept_invitation, cancel_invitation, invite, leave_team, remove_member
from drop_core.tests_invitations import CAN_JOIN, JOINED, NOT_VALID, OWN_PLAN, PLAN_ENDED, REFUSALS, url
from drop_core.tests_members import second_member
from drop_core.tests_teams import team_of

ACCOUNT = "backend/drop_billing/account.py"
TEAMS = "backend/drop_core/teams.py"
VIEW = "backend/drop_core/invitation_views.py"
PAGE = "backend/templates/registration/invitation.html"
NOT_APPLIED = "changed while its subscriptions were being read, so that listing was not applied"
PLAN_NOT_ENDED = REFUSALS["plan_not_ended"]
OWN_PLAN_INPUT = '<input type="hidden" name="own_plan" value="ends">'
JOIN_BUTTON = ">Join the team</button>"
LEFT = "You are no longer on a team, so this account is a Free account again."
INPUT = re.compile(r'<input type="hidden" name="([^"]+)" value="([^"]*)">')


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

    def state(self):
        self.reload()
        return (self.ws.plan, self.ws.stripe_subscription_id)

    def mirror(self):
        return Subscription.objects.get(workspace=self.ws).status


class EndingTests(PaidJoinBase):
    def end(self):
        stripe_mod, _ = account._stripe_or_503()
        return account._end_live_subscriptions(stripe_mod, self.ws)

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

    def test_a_customer_changed_meanwhile_is_not_done(self):
        self.at_stripe["sub_A"]["status"] = "canceled"
        Workspace.objects.filter(pk=self.ws.pk).update(stripe_customer_id="cus_moved")
        with self.assertLogs("drop_billing", level="INFO") as logged:
            ending = self.end()
        self.cancel.assert_not_called()
        self.void.assert_not_called()
        self.assertEqual(self.listed.call_count, 1, f"{ACCOUNT}: nothing live needs no read-back")
        self.assertEqual(ending, account.Ending(False, False, False), f"{ACCOUNT}: a listing that was not applied does not end the plan")
        self.assertEqual(sum(NOT_APPLIED in line for line in logged.output), 1, f"{ACCOUNT}: the skipped listing is logged once")
        self.assertFalse(any(line.startswith("ERROR") for line in logged.output), f"{ACCOUNT}: a customer changed meanwhile is not an error")
        self.reload()
        row = (self.ws.plan, self.ws.stripe_customer_id, self.ws.stripe_subscription_id)
        self.assertEqual(row, ("team", "cus_moved", "sub_A"), f"{ACCOUNT}: nothing is written for a customer the workspace has left")

    def test_a_workspace_with_no_customer_makes_no_call(self):
        Workspace.objects.filter(pk=self.ws.pk).update(plan="team", stripe_customer_id="", stripe_subscription_id="")
        self.reload()
        ending = self.end()
        self.listed.assert_not_called()
        self.cancel.assert_not_called()
        self.void.assert_not_called()
        self.assertEqual(ending, account.Ending(True, False, False), f"{ACCOUNT}: nothing at Stripe belongs to a workspace with no customer")
        self.reload()
        row = (self.ws.plan, self.ws.stripe_customer_id, self.ws.stripe_subscription_id)
        self.assertEqual(row, ("team", "", ""), f"{ACCOUNT}: a plan set by hand is left as it is")

    def test_an_unmarked_subscription_that_the_workspace_does_not_hold_is_not_ended(self):
        self.at_stripe["sub_M"] = make_sub("sub_M", "active", "price_team", created=2)
        with self.assertLogs("drop_billing", level="ERROR") as logged:
            ending = self.end()
        self.assertEqual(self.calls, ["cancel sub_A"], f"{ACCOUNT}: only the subscription the workspace holds is ended")
        self.assertEqual(ending, account.Ending(False, True, True), f"{ACCOUNT}: a live subscription is left, so the ending is stuck")
        errors = [line for line in logged.output if "names no workspace" in line]
        self.assertEqual(len(errors), 1, f"{ACCOUNT}: the subscription that was left is logged once")
        self.assertIn("sub_M", errors[0], f"{ACCOUNT}: the log names the subscription")

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
        self.at_stripe["sub_U"] = make_sub("sub_U", "unpaid", "price_team", created=2, metadata={"workspace_id": str(self.ws.pk)})
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


class PaidJoinTests(PaidJoinBase):
    """Joining from the invitation page: the plan is ended at Stripe first, outside every lock, and the join commits only when
    the mirror shows no plan."""

    def on_the_team(self):
        return Membership.objects.filter(
            workspace=self.team, user=self.user, role=Membership.MEMBER, removed_at__isnull=True
        ).exists()

    def administrator_row_open(self):
        return Membership.objects.filter(
            workspace=self.ws, user=self.user, role=Membership.ADMINISTRATOR, removed_at__isnull=True
        ).exists()

    def old_key_active(self):
        return APIKey.objects.get(pk=self.old_key.pk).is_active

    def buy_team_plus(self):
        """A Checkout for Team Plus completes at Stripe."""
        self.at_stripe["sub_B"] = make_sub("sub_B", "active", "price_teamplus", created=2)

    def post_subscription_event(self, event_type, sub_id):
        """Sign and post an event for the subscription as Stripe holds it now."""
        response = self.post_event(make_event(event_type, copy.deepcopy(self.at_stripe[sub_id])))
        self.assertEqual(response.status_code, 200, f"the {event_type} webhook is processed")

    def assert_offered_again(self, response):
        """The offer, its consent field, Join and the plan-ended sentence, at 400."""
        self.assertEqual(response.status_code, 400, f"{VIEW}: an offer made again answers 400")
        html = response.content.decode()
        for sentence in OWN_PLAN:
            self.assertIn(sentence, html, f"{PAGE}: the offer is made again")
        self.assertIn(OWN_PLAN_INPUT, html, f"{PAGE}: the Join form carries the consent field again")
        self.assertIn(JOIN_BUTTON, html, f"{PAGE}: the offer shows Join")
        self.assertIn(PLAN_ENDED, html, f"{PAGE}: the page says that the old plan ended")
        self.assertNotIn(PLAN_NOT_ENDED, html, f"{PAGE}: an offer is not plan_not_ended")

    def test_joining_ends_an_active_plan_with_a_credit_and_then_joins(self):
        response = self.join(follow=True)
        self.assertEqual(response.redirect_chain[-1][0], "/app/", f"{VIEW}: a join goes to Overview")
        self.assertEqual(response.content.decode().count(JOINED), 1, f"{VIEW}: the notice shows once")
        self.cancel.assert_called_once_with("sub_A", prorate=True, invoice_now=True, max_network_retries=0)
        self.assertEqual(self.listed.call_count, 2, f"{TEAMS}: the first listing and the read-back")
        self.assertEqual(self.state(), ("free", ""), f"{TEAMS}: the read-back is applied before the join")
        self.assertEqual(self.mirror(), "canceled", f"{TEAMS}: the mirror shows the cancel")
        self.assertTrue(self.on_the_team(), f"{TEAMS}: the payer is a member of the team")
        self.assertFalse(self.old_key_active(), f"{TEAMS}: the old account's keys stop")

    def test_the_plan_is_free_before_the_administrator_row_closes(self):
        real = teams._close_administrator_row
        plans = []
        at_cancel = []

        def record(row, now):
            plans.append(Workspace.objects.get(pk=self.ws.pk).plan)
            return real(row, now)

        def cancel_and_look(sub_id, **kwargs):
            at_cancel.append((self.administrator_row_open(), len(connection.savepoint_ids)))
            return self.canceling(sub_id, **kwargs)

        self.cancel.side_effect = cancel_and_look
        baseline = len(connection.savepoint_ids)
        with mock.patch.object(teams, "_close_administrator_row", side_effect=record):
            response = self.join()
        self.assertEqual(response.status_code, 302, f"{VIEW}: the join goes through")
        self.assertEqual(plans, ["free"], f"{TEAMS}: the mirror shows Free before the administrator row closes")
        self.assertEqual(at_cancel, [(True, baseline)],
                         f"{TEAMS}: the cancel runs while the administrator row is open, outside any transaction")

    def test_the_consent_field_is_needed_for_a_stripe_call(self):
        response = self.join(consent=False)
        self.assertEqual(response.status_code, 400, f"{VIEW}: an accept without consent is the offer again")
        html = response.content.decode()
        for sentence in OWN_PLAN:
            self.assertIn(sentence, html, f"{PAGE}: the offer is shown")
        self.assertIn(OWN_PLAN_INPUT, html, f"{PAGE}: the Join form carries the consent field")
        self.listed.assert_not_called()
        self.cancel.assert_not_called()
        self.assertEqual(self.state(), ("team", "sub_A"), f"{TEAMS}: without consent nothing changes")

    def test_the_offer_form_as_the_page_renders_it_ends_the_plan_and_joins(self):
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.user)
        html = browser.get(url(self.raw)).content.decode()
        form = next(part for part in re.findall(r"<form\b.*?</form>", html, re.S) if JOIN_BUTTON in part)
        fields = dict(INPUT.findall(form))
        self.assertEqual(len(fields), form.count("<input"), f"{PAGE}: every input of the Join form is read")
        self.assertIn("csrfmiddlewaretoken", fields, f"{PAGE}: the Join form carries the CSRF token")
        response = browser.post(url(self.raw), fields)
        self.assertEqual(response.status_code, 302, f"{PAGE}: the Join form as rendered ends the plan and joins")
        self.cancel.assert_called_once()

    def test_an_unpaid_plan_that_shows_free_is_not_touched(self):
        self.set_billing("free", customer="cus_A")
        self.at_stripe["sub_A"]["status"] = "unpaid"
        response = self.join(consent=False, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], "/app/", f"{VIEW}: a person whose mirror shows Free joins")
        self.assertEqual(response.content.decode().count(JOINED), 1, f"{VIEW}: the notice shows once")
        self.listed.assert_not_called()
        self.cancel.assert_not_called()

    def test_a_refused_cancel_leaves_the_person_as_they_were(self):
        self.cancel.side_effect = stripe.InvalidRequestError("secret detail", "id")
        with self.assertLogs("drop_billing", level="WARNING") as logged:
            response = self.join()
        self.assertEqual(response.status_code, 400, f"{VIEW}: a plan that did not end refuses the join")
        html = response.content.decode()
        self.assertIn(PLAN_NOT_ENDED, html, f"{PAGE}: the plan_not_ended sentence")
        self.assertNotIn(PLAN_ENDED, html, f"{PAGE}: plan_not_ended never says the plan ended")
        self.assertNotIn(JOIN_BUTTON, html, f"{PAGE}: plan_not_ended shows no Join")
        self.assertFalse(Membership.objects.filter(workspace=self.team, user=self.user).exists(), f"{TEAMS}: no member row")
        self.assertEqual(self.state(), ("team", "sub_A"), f"{TEAMS}: the plan stays as Stripe holds it")
        self.assertTrue(self.old_key_active(), f"{TEAMS}: the old key keeps working")
        warnings = messages(logged, logging.WARNING)
        self.assertTrue(any("InvalidRequestError" in line for line in warnings), f"{ACCOUNT}: a WARNING names the class")
        self.assertNotIn("secret detail", "\n".join(logged.output), f"{ACCOUNT}: no line carries Stripe's text")
        errors = messages(logged, logging.ERROR)
        self.assertEqual(len(errors), 1, f"{ACCOUNT}: one ERROR")
        self.assertIn("sub_A", errors[0], f"{ACCOUNT}: the ERROR names the subscription that is still live")

    def test_a_cancel_that_failed_after_the_webhook_wrote_free_still_joins(self):
        listings = []

        def cancel_then_webhook(sub_id, **kwargs):
            self.calls.append(f"cancel {sub_id}")
            self.at_stripe[sub_id]["status"] = "canceled"
            self.post_subscription_event("customer.subscription.deleted", sub_id)
            raise stripe.APIConnectionError("secret detail")

        def read_back_fails(*args, **kwargs):
            listings.append(1)
            if len(listings) == 3:
                raise stripe.APIConnectionError("secret detail")
            return self.listing()

        self.cancel.side_effect = cancel_then_webhook
        self.listed.side_effect = read_back_fails
        with self.assertLogs("drop_billing", level="WARNING"):
            response = self.join()
        self.assertEqual(self.listed.call_count, 3, "the join's first listing, the webhook's and the failed read-back")
        self.assertEqual((response.status_code, response["Location"]), (302, "/app/"),
                         f"{TEAMS}: the check under the locks decides on the mirror the webhook wrote")
        self.assertTrue(self.on_the_team(), f"{TEAMS}: the payer is a member of the team")
        self.assertEqual(self.state(), ("free", ""), "the webhook wrote Free")

    def test_a_second_press_after_a_failure_joins_without_a_second_cancel(self):
        listings = []

        def read_back_fails_once(*args, **kwargs):
            listings.append(1)
            if len(listings) == 2:
                raise stripe.APIConnectionError("secret detail")
            return self.listing()

        self.listed.side_effect = read_back_fails_once
        with self.assertLogs("drop_billing", level="ERROR"):
            first = self.join()
        self.assertEqual(first.status_code, 400, f"{VIEW}: a failed read-back refuses the join")
        self.assertIn(PLAN_NOT_ENDED, first.content.decode(), f"{PAGE}: the plan_not_ended sentence")
        self.assertNotIn(PLAN_ENDED, first.content.decode(), f"{PAGE}: plan_not_ended never says the plan ended")
        self.assertFalse(self.on_the_team(), f"{TEAMS}: the first press joins nobody")
        second = self.join()
        self.assertEqual((second.status_code, second["Location"]), (302, "/app/"), f"{TEAMS}: the second press joins")
        self.assertEqual(self.cancel.call_count, 1, f"{ACCOUNT}: the second press finds the plan ended and cancels nothing")
        self.assertTrue(self.on_the_team(), f"{TEAMS}: the payer is a member of the team")

    def test_a_leftover_that_grants_nothing_does_not_stop_the_join(self):
        self.at_stripe["sub_U"] = make_sub("sub_U", "unpaid", "price_team", created=2, metadata={"workspace_id": str(self.ws.pk)})
        self.at_stripe["sub_U"]["latest_invoice"] = "in_U"

        def void_refused(invoice_id, **kwargs):
            self.calls.append(f"void {invoice_id}")
            raise stripe.InvalidRequestError("secret detail", "invoice")

        self.void.side_effect = void_refused
        with self.assertLogs("drop_billing", level="WARNING") as logged:
            response = self.join(follow=True)
        self.assertEqual(response.redirect_chain[-1][0], "/app/", f"{TEAMS}: a leftover that grants nothing leaves the mirror Free")
        self.assertIn(JOINED, response.content.decode(), f"{VIEW}: the notice shows")
        self.assertEqual(self.calls, ["cancel sub_A", "void in_U"], f"{ACCOUNT}: a failed void sends no cancel")
        errors = messages(logged, logging.ERROR)
        self.assertEqual(len(errors), 1, f"{ACCOUNT}: one ERROR")
        self.assertIn("sub_U", errors[0], f"{ACCOUNT}: the ERROR names the leftover subscription")

    def test_no_refusal_reaches_stripe(self):
        other, _ = team_of("lin")
        cases = {
            "a wrong person": lambda: Invitation.objects.filter(pk=self.invitation.pk).update(email="someone@example.com"),
            "an unverified address": lambda: EmailVerification.objects.filter(user=self.user).update(is_verified=False),
            "a member of another team": lambda: Membership.objects.create(workspace=other, user=self.user, role=Membership.MEMBER),
            "an administrator of people": lambda: second_member(self.ws, "friend"),
            "a suspended account": lambda: Workspace.objects.filter(pk=self.ws.pk).update(suspended_at=timezone.now()),
            "a plan with no customer": lambda: Workspace.objects.filter(pk=self.ws.pk).update(stripe_customer_id=""),
            "a full team": lambda: [second_member(self.team, name) for name in ("a", "b", "c", "d")],
        }
        for case, arrange in cases.items():
            with self.subTest(case=case):
                with transaction.atomic():
                    arrange()
                    response = self.join()
                    transaction.set_rollback(True)
                self.assertEqual(response.status_code, 400, f"{VIEW}: {case} is refused")
                self.assertNotIn(JOIN_BUTTON, response.content.decode(), f"{PAGE}: {case} is a refusal, not an offer")
                self.listed.assert_not_called()
                self.cancel.assert_not_called()
        self.assertEqual(self.state(), ("team", "sub_A"), f"{TEAMS}: no refusal changes the plan")

    def refused_after_the_ending(self, change):
        """Join, with `change` made inside the cancel, after the plan has ended at Stripe. Returns the status and the page, once
        it has checked that the person is on Free and otherwise as they were."""

        def cancel_then_change(sub_id, **kwargs):
            answer = self.canceling(sub_id, **kwargs)
            change()
            return answer

        self.cancel.side_effect = cancel_then_change
        response = self.join()
        self.cancel.assert_called_once()
        self.assertEqual(self.state(), ("free", ""), f"{TEAMS}: the plan ended at Stripe and the read-back was applied")
        self.assertTrue(self.administrator_row_open(), f"{TEAMS}: a refused join closes no row")
        self.assertTrue(self.old_key_active(), f"{TEAMS}: a refused join stops no key")
        self.assertFalse(self.on_the_team(), f"{TEAMS}: a refused join makes no member row")
        return response.status_code, response.content.decode()

    def test_a_refusal_after_the_plan_ended_says_so_on_the_not_valid_page(self):
        status, html = self.refused_after_the_ending(lambda: cancel_invitation(self.admin, self.invitation.pk))
        self.assertEqual(status, 404, f"{VIEW}: a withdrawn invitation is not valid")
        self.assertIn(NOT_VALID, html, f"{VIEW}: the not-valid page")
        self.assertIn(PLAN_ENDED, html, "backend/templates/registration/invitation_invalid.html: the plan-ended sentence")

    def test_a_refusal_after_the_plan_ended_says_so_with_no_free_seat(self):
        status, html = self.refused_after_the_ending(lambda: [second_member(self.team, name) for name in ("a", "b", "c", "d")])
        self.assertEqual(status, 400, f"{VIEW}: a full team refuses the join")
        self.assertIn(REFUSALS["no_free_seat"], html, f"{PAGE}: the no_free_seat sentence")
        self.assertIn(PLAN_ENDED, html, f"{PAGE}: the plan-ended sentence")
        self.assertNotIn(JOIN_BUTTON, html, f"{PAGE}: a refusal shows no Join")

    def test_a_refusal_after_the_plan_ended_says_so_when_the_account_is_suspended(self):
        status, html = self.refused_after_the_ending(
            lambda: Workspace.objects.filter(pk=self.ws.pk).update(suspended_at=timezone.now()))
        self.assertEqual(status, 400, f"{VIEW}: a suspended account cannot join")
        self.assertIn(REFUSALS["account_suspended"], html, f"{PAGE}: the account_suspended sentence")
        self.assertIn(PLAN_ENDED, html, f"{PAGE}: the plan-ended sentence")
        self.assertNotIn(JOIN_BUTTON, html, f"{PAGE}: a refusal shows no Join")

    def test_a_refusal_after_the_plan_ended_says_so_when_the_team_is_suspended(self):
        status, html = self.refused_after_the_ending(
            lambda: Workspace.objects.filter(pk=self.team.pk).update(suspended_at=timezone.now()))
        self.assertEqual(status, 404, f"{VIEW}: a suspended team's link is not valid")
        self.assertIn(NOT_VALID, html, f"{VIEW}: the not-valid page")
        self.assertIn(PLAN_ENDED, html, "backend/templates/registration/invitation_invalid.html: the plan-ended sentence")

    def test_a_refusal_after_the_plan_ended_says_so_after_an_integrity_error(self):
        refused = mock.patch.object(Membership.objects, "create", side_effect=IntegrityError("membership_one_team"))
        self.addCleanup(refused.stop)
        status, html = self.refused_after_the_ending(refused.start)
        self.assertEqual(status, 400, f"{TEAMS}: an IntegrityError is a refusal, not a 500")
        self.assertIn(REFUSALS["on_another_team"], html, f"{PAGE}: the on_another_team sentence")
        self.assertIn(PLAN_ENDED, html, f"{PAGE}: the plan-ended sentence")
        self.assertNotIn(JOIN_BUTTON, html, f"{PAGE}: a refusal shows no Join")

    def test_a_checkout_seen_by_the_read_back_is_offered_again_and_says_the_old_plan_ended(self):
        def cancel_and_buy(sub_id, **kwargs):
            answer = self.canceling(sub_id, **kwargs)
            self.buy_team_plus()
            return answer

        self.cancel.side_effect = cancel_and_buy
        response = self.join()
        self.assert_offered_again(response)
        self.assertEqual(self.state(), ("team_plus", "sub_B"), f"{ACCOUNT}: the read-back shows the new plan")
        self.assertFalse(self.on_the_team(), f"{TEAMS}: nobody joined")

    def test_a_checkout_that_lands_after_the_read_back_is_offered_again(self):
        real = account._sync_from_subscriptions

        def then_a_checkout(*args, **kwargs):
            row = real(*args, **kwargs)
            if "sub_B" not in self.at_stripe:
                self.buy_team_plus()
                self.post_subscription_event("customer.subscription.created", "sub_B")
            return row

        with mock.patch.object(account, "_sync_from_subscriptions", side_effect=then_a_checkout):
            response = self.join()
        self.assert_offered_again(response)
        self.assertEqual(self.state(), ("team_plus", "sub_B"), "the webhook wrote the new plan")
        self.assertFalse(self.on_the_team(), f"{TEAMS}: nobody joined")
        second = self.join()
        self.assertEqual((second.status_code, second["Location"]), (302, "/app/"), f"{TEAMS}: the second press joins")
        self.assertEqual(self.calls, ["cancel sub_A", "cancel sub_B"], f"{ACCOUNT}: the second press ends sub_B")
        self.assertTrue(self.on_the_team(), f"{TEAMS}: the payer is a member of the team")

    def test_a_checkout_that_lands_between_the_read_back_and_its_apply_is_offered_again(self):
        listings = []

        def listing_then_a_checkout(*args, **kwargs):
            answer = self.listing()
            listings.append(answer)
            if len(listings) == 2:
                self.buy_team_plus()
                self.post_subscription_event("customer.subscription.created", "sub_B")
            return answer

        self.listed.side_effect = listing_then_a_checkout
        response = self.join()
        self.assert_offered_again(response)
        self.assertEqual(self.state(), ("team_plus", "sub_B"), f"{ACCOUNT}: the older listing is not applied over the webhook's row")
        self.assertFalse(Membership.objects.filter(workspace=self.team, user=self.user).exists(), f"{TEAMS}: no membership was made")
        second = self.join()
        self.assertEqual((second.status_code, second["Location"]), (302, "/app/"), f"{TEAMS}: the second press joins")
        self.assertEqual(self.calls, ["cancel sub_A", "cancel sub_B"], f"{ACCOUNT}: the second press ends sub_B")
        self.assertTrue(self.on_the_team(), f"{TEAMS}: the payer is a member of the team")

    def test_a_double_press_lands_on_overview(self):
        self.assertEqual(self.join().status_code, 302, f"{VIEW}: the first press joins")
        calls = (self.listed.call_count, self.cancel.call_count, self.void.call_count)
        second = self.join()
        self.assertEqual((second.status_code, second["Location"]), (302, "/app/"), f"{VIEW}: a second press goes to Overview")
        self.assertEqual((self.listed.call_count, self.cancel.call_count, self.void.call_count), calls,
                         f"{TEAMS}: a second press makes no Stripe call")
        page = self.web.get("/app/").content.decode()
        self.assertEqual(page.count(JOINED), 1, f"{VIEW}: the notice shows once")
        self.assertNotIn(PLAN_ENDED, page, f"{VIEW}: a person on the team is not told that a plan ended")

    def test_stripe_not_configured_refuses_without_a_call(self):
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": ""}):
            with self.assertLogs("drop_billing", level="ERROR"):
                response = self.join()
        self.assertEqual(response.status_code, 400, f"{VIEW}: an ending without Stripe refuses the join")
        self.assertIn(PLAN_NOT_ENDED, response.content.decode(), f"{PAGE}: the plan_not_ended sentence")
        self.listed.assert_not_called()
        self.cancel.assert_not_called()
        self.void.assert_not_called()

    def test_a_missing_customer_is_forgotten_and_the_join_goes_on(self):
        self.listed.side_effect = missing_customer_error("cus_A")
        with self.assertLogs("drop_billing", level="WARNING"):
            response = self.join()
        self.assertEqual((response.status_code, response["Location"]), (302, "/app/"), f"{TEAMS}: the join goes on")
        self.cancel.assert_not_called()
        self.assertEqual(self.reload().stripe_customer_id, "", f"{ACCOUNT}: a missing customer is forgotten")

    def assert_free_again_with_the_same_customer(self):
        page = self.web.get("/app/").content.decode()
        self.assertIn(LEFT, page, "backend/templates/dashboard/overview.html: Overview says the account is Free again")
        self.assertEqual(self.state(), ("free", ""), f"{TEAMS}: the person's own workspace is Free")
        session = self.patch_stripe("session", return_value={"id": "cs_1", "url": "https://checkout.stripe.com/c/cs_1"})
        response = self.api.post("/api/v1/billing/checkout", {"plan": "team"}, format="json")
        self.assertEqual(response.status_code, 200, f"{ACCOUNT}: a checkout of Team opens")
        session.assert_called_once()
        self.assertEqual(session.call_args.kwargs["customer"], "cus_A", f"{ACCOUNT}: the checkout reuses the customer and its credit")

    def test_leaving_after_a_paid_join_gives_a_free_account_and_checkout_reuses_the_customer(self):
        self.assertEqual(self.join().status_code, 302, f"{VIEW}: the join goes through")
        leave_team(Membership.objects.get(workspace=self.team, user=self.user, removed_at__isnull=True))
        self.assert_free_again_with_the_same_customer()

    def test_removal_after_a_paid_join_gives_a_free_account_and_checkout_reuses_the_customer(self):
        self.assertEqual(self.join().status_code, 302, f"{VIEW}: the join goes through")
        row = Membership.objects.get(workspace=self.team, user=self.user, removed_at__isnull=True)
        remove_member(self.admin, row.pk, revoke_files=False)
        self.assert_free_again_with_the_same_customer()

    def test_with_the_switch_off_the_join_asks_nothing(self):
        with override_settings(TEAM_SEATS=False), self.assertNumQueries(0):
            with self.assertRaises(JoinRefused) as caught:
                accept_invitation(self.user, self.raw, own_plan_agreed=True)
        self.assertEqual(caught.exception.code, "not_valid", f"{TEAMS}: with the switch off a link is not valid")
        self.assertFalse(caught.exception.plan_ended, f"{TEAMS}: with the switch off no plan ended")
        self.listed.assert_not_called()


class PaidJoinPageTests(PaidJoinBase):
    def rows(self):
        return [list(model.objects.order_by("pk").values()) for model in (Membership, Workspace, Invitation)]

    def test_the_page_offers_the_end_of_the_plan_and_makes_no_stripe_call(self):
        before = self.rows()
        response = self.web.get(url(self.raw))
        self.assertEqual(response.status_code, 200, f"{VIEW}: the page opens")
        html = response.content.decode()
        for sentence in OWN_PLAN + CAN_JOIN:
            self.assertIn(sentence, html, f"{PAGE}: the offer and the Can join row are on the page")
        self.assertIn(JOIN_BUTTON, html, f"{PAGE}: the offer shows Join")
        self.listed.assert_not_called()
        self.assertEqual(self.rows(), before, f"{VIEW}: a GET writes nothing")
