"""Print the signup-to-first-upload activation funnel. Read-only."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from drop_core import funnel
from drop_core.models import APIKey, Artifact, Workspace

LABELS = {
    "landing_visit": "Landing visit",
    "signup_started": "Signup started",
    "signup_completed": "Signup completed",
    "key_issued": "API key issued",
    "first_upload": "First upload",
    "second_upload": "Second upload",
}


def _pct(ratio):
    return f"{ratio * 100:5.1f}%"


class Command(BaseCommand):
    help = "Print the activation funnel (first-party event counts + baseline)."

    def handle(self, *args, **options):
        rows = funnel.funnel_summary()
        self.stdout.write("Veilfile activation funnel (first-party events)")
        self.stdout.write(f"{'stage':<16}{'count':>8}  {'of landing':>10}  {'of prev':>8}")
        for i, row in enumerate(rows):
            prev = "      --" if i == 0 else _pct(row["of_prev"])
            self.stdout.write(
                f"{LABELS[row['stage']]:<16}{row['count']:>8}  "
                f"{_pct(row['of_top']):>10}  {prev:>8}"
            )

        # Baseline from existing records, so the funnel reads sensibly
        # before instrumentation has had time to accumulate events.
        User = get_user_model()
        ws_with_keys = (
            APIKey.objects.filter(is_active=True)
            .values("workspace_id")
            .distinct()
            .count()
        )
        ws_with_uploads = (
            Artifact.objects.values("workspace_id").distinct().count()
        )
        self.stdout.write("")
        self.stdout.write("Baseline (existing records, pre-instrumentation):")
        self.stdout.write(f"  signups (users):            {User.objects.count()}")
        self.stdout.write(f"  workspaces:                 {Workspace.objects.count()}")
        self.stdout.write(f"  workspaces with >=1 key:    {ws_with_keys}")
        self.stdout.write(f"  workspaces with >=1 upload:  {ws_with_uploads}")
