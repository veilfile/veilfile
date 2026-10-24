from django.db import models


class Subscription(models.Model):
    """Stripe subscription state mirrored locally for one workspace.

    The canonical billing identity also lives on drop_core.Workspace
    (plan, stripe_customer_id, stripe_subscription_id); this row keeps the
    subscription lifecycle (status, current_period_end) in sync via webhooks.
    """

    workspace = models.OneToOneField(
        "drop_core.Workspace",
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    stripe_customer_id = models.CharField(max_length=128, blank=True, default="")
    stripe_subscription_id = models.CharField(
        max_length=128, blank=True, default="", db_index=True
    )
    status = models.CharField(max_length=32, blank=True, default="")
    current_period_end = models.DateTimeField(null=True, blank=True)

    def __str__(self):  # pragma: no cover - debug helper
        return f"Subscription({self.workspace_id}, {self.status})"


class ProcessedStripeEvent(models.Model):
    """A Stripe webhook event id that has already been handled.

    Stripe delivers at least once and retries for days, so the webhook
    records each event id after processing it and skips repeats. Plan state
    is re-derived from Stripe on every event, so this is an efficiency and
    audit guard rather than the only defense against stale deliveries.
    """

    event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=128, blank=True, default="")
    processed_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):  # pragma: no cover - debug helper
        return f"{self.event_type} {self.event_id}"
