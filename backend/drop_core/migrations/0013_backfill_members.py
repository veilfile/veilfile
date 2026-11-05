from django.db import migrations
from django.db.models import OuterRef, Subquery


def backfill_members(apps, schema_editor):
    """Gives every workspace its owner as administrator, then gives that administrator every key, upload URL,
    file and safety event that names no member. It is safe to run more than once, and the NULL-member fallbacks cover rows that
    the previous release writes after it runs."""
    Workspace = apps.get_model("drop_core", "Workspace")
    Membership = apps.get_model("drop_core", "Membership")
    for workspace in Workspace.objects.filter(memberships__isnull=True).only("id", "owner_id").iterator():
        Membership.objects.get_or_create(
            workspace_id=workspace.id, user_id=workspace.owner_id, defaults={"role": "administrator"}
        )
    administrator = Membership.objects.filter(
        workspace=OuterRef("workspace"), role="administrator", removed_at__isnull=True
    ).values("pk")[:1]
    for name in ("APIKey", "Artifact", "PendingUpload", "SafetyEvent"):
        apps.get_model("drop_core", name).objects.filter(member__isnull=True).update(member=Subquery(administrator))


class Migration(migrations.Migration):

    dependencies = [
        ('drop_core', '0012_member_columns'),
    ]

    operations = [
        migrations.RunPython(backfill_members, migrations.RunPython.noop),
    ]
