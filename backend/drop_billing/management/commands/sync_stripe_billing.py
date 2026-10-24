"""Re-derive every workspace's plan from Stripe (dry run unless --apply).

One-off reconciliation for the test-to-live cutover and for drift: a
workspace whose stored customer does not exist in the current Stripe mode
(a test-mode leftover) loses the customer id, subscription id and any paid
plan; every other workspace with a customer gets the plan its Stripe
subscriptions entitle it to, exactly as the webhook would compute it.
Paid workspaces with no Stripe customer at all (manual or test grants) are
listed, and only moved to free with --reset-unbilled. --list-orphans also
lists live subscriptions whose customer is on no workspace (paying without
a plan).

--apply refuses to run with a test-mode key unless --allow-test-key is
given: against the production database, a test key would report every live
customer as missing and unlink them all.
"""

import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from drop_billing.views import (
    LIVE_STATUSES,
    _apply_subscriptions,
    _as_list,
    _derive_billing_state,
    _forget_missing_customer,
    _list_subscriptions,
    _price_ids,
    _stripe_or_503,
    _subscription_plan,
)
from drop_core.models import Workspace


def _key_mode(key):
    if key.startswith(("sk_live_", "rk_live_")):
        return "live"
    if key.startswith(("sk_test_", "rk_test_")):
        return "test"
    return "unknown"


def _iter_all(result):
    """Every item of a Stripe list, following pagination."""
    pager = getattr(result, "auto_paging_iter", None)
    return pager() if callable(pager) else iter(_as_list(result))


class Command(BaseCommand):
    help = (
        "Re-derive workspace plans from Stripe subscriptions and clear "
        "customer ids missing in the current Stripe mode. Dry run unless "
        "--apply."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true", help="Write the changes (default: dry run)."
        )
        parser.add_argument(
            "--reset-unbilled",
            action="store_true",
            help="Also move paid workspaces that have no Stripe customer to free.",
        )
        parser.add_argument(
            "--list-orphans",
            action="store_true",
            help=(
                "Also list live Stripe subscriptions whose customer is on no "
                "workspace (read-only)."
            ),
        )
        parser.add_argument(
            "--allow-test-key",
            action="store_true",
            help="Allow --apply with a test-mode STRIPE_SECRET_KEY.",
        )

    def handle(self, *args, **options):
        stripe_mod, err = _stripe_or_503()
        if err is not None:
            raise CommandError("STRIPE_SECRET_KEY is not set.")
        apply = options["apply"]
        mode = _key_mode(os.environ.get("STRIPE_SECRET_KEY", ""))
        self.stdout.write(f"Stripe key mode: {mode}")
        if apply and mode != "live" and not options["allow_test_key"]:
            raise CommandError(
                f"Refusing --apply with a {mode}-mode STRIPE_SECRET_KEY: every "
                "customer from the other mode would look missing and be "
                "unlinked. Use the live key, or pass --allow-test-key if this "
                "database really belongs to test mode."
            )
        if not apply:
            self.stdout.write("DRY RUN: nothing is written. Re-run with --apply.")

        changes = 0
        workspaces = Workspace.objects.exclude(stripe_customer_id="").order_by(
            "created_at"
        )
        for ws in workspaces:
            customer_id = ws.stripe_customer_id
            try:
                subs = _list_subscriptions(stripe_mod, customer_id)
            except stripe_mod.error.StripeError as exc:
                self.stderr.write(f"workspace {ws.pk}: Stripe error for {customer_id}: {exc}")
                continue
            if subs is None:
                changes += 1
                self.stdout.write(
                    f"workspace {ws.pk} ({ws.owner}): customer {customer_id} not "
                    f"found in this Stripe mode (test-mode leftover): plan "
                    f"{ws.plan} -> free, customer and subscription ids cleared"
                )
                if apply:
                    _forget_missing_customer(ws, customer_id)
                continue
            live = [s for s in subs if s.get("status") in LIVE_STATUSES]
            if len(live) > 1:
                self.stdout.write(
                    f"workspace {ws.pk} ({ws.owner}): {len(live)} live "
                    f"subscriptions on {customer_id} "
                    f"({', '.join(s.get('id') or '?' for s in live)}): keep one, "
                    "cancel and refund the others"
                )
            plan, sub_id, _mirror = _derive_billing_state(ws, subs, customer_id)
            if (plan, sub_id) != (ws.plan, ws.stripe_subscription_id):
                changes += 1
                self.stdout.write(
                    f"workspace {ws.pk} ({ws.owner}): plan {ws.plan} -> {plan}, "
                    f"subscription {ws.stripe_subscription_id or '-'} -> {sub_id or '-'}"
                )
            if apply:
                with transaction.atomic():
                    locked = Workspace.objects.select_for_update().get(pk=ws.pk)
                    _apply_subscriptions(locked, subs, customer_id)

        unbilled = Workspace.objects.filter(stripe_customer_id="").exclude(plan="free")
        for ws in unbilled.order_by("created_at"):
            action = "-> free" if options["reset_unbilled"] else "(left as is; --reset-unbilled moves it to free)"
            self.stdout.write(
                f"workspace {ws.pk} ({ws.owner}): plan {ws.plan} without a Stripe "
                f"customer {action}"
            )
            if options["reset_unbilled"]:
                changes += 1
                if apply:
                    Workspace.objects.filter(pk=ws.pk).update(
                        plan="free", stripe_subscription_id=""
                    )

        verb = "applied" if apply else "would apply"
        self.stdout.write(f"{verb} {changes} change(s)")

        if options["list_orphans"]:
            self._list_orphans(stripe_mod)

    def _list_orphans(self, stripe_mod):
        """Live subscriptions on a customer no workspace stores: someone is
        paying without a plan (bill-9). Neither the webhook nor checkout can
        see these, so they are found here and fixed by hand."""
        known = set(
            Workspace.objects.exclude(stripe_customer_id="").values_list(
                "stripe_customer_id", flat=True
            )
        )
        orphans = 0
        try:
            # Without a status filter Stripe lists every non-canceled
            # subscription in the account.
            result = stripe_mod.Subscription.list(limit=100)
            for sub in _iter_all(result):
                if sub.get("status") not in LIVE_STATUSES:
                    continue
                customer = sub.get("customer")
                if isinstance(customer, dict):
                    customer = customer.get("id")
                if customer in known:
                    continue
                orphans += 1
                hint = (sub.get("metadata") or {}).get("workspace_id")
                self.stdout.write(
                    f"orphan: subscription {sub.get('id')} ({sub.get('status')}, "
                    f"plan {_subscription_plan(sub) or ', '.join(_price_ids(sub)) or '?'}) "
                    f"on customer {customer}, which no workspace uses"
                    + (f"; checkout named workspace {hint}" if hint else "")
                    + ". Attach the customer to the right workspace, or cancel "
                    "and refund."
                )
        except stripe_mod.error.StripeError as exc:
            raise CommandError(f"Stripe error while listing subscriptions: {exc}")
        self.stdout.write(f"{orphans} orphan subscription(s)")
