from django.db import migrations
from django.db.models import OuterRef, Subquery


BATCH_SIZE = 1000


def backfill_members(apps, schema_editor):
    """Gives every workspace its owner as administrator, then gives that administrator every key, upload URL,
    file and safety event that names no member. The migration is not atomic, and the rows are updated in
    batches of BATCH_SIZE that each commit on their own, so no batch holds a row lock for long while the
    previous release is still serving requests. It is safe to run more than once. It is also safe to resume
    after being killed halfway, because a NULL member already means the administrator, so the rows that
    were not reached yet keep working until the next run fills them. A row whose workspace has no current
    administrator stays NULL, and the keyset moves past it, so the loop always ends. The NULL-member fallbacks
    cover rows that the previous release writes after it runs."""
    Workspace = apps.get_model("drop_core", "Workspace")
    Membership = apps.get_model("drop_core", "Membership")
    # A list, not .iterator(): this migration runs in autocommit, where a server-side cursor cannot be used.
    workspaces = list(Workspace.objects.filter(memberships__isnull=True).only("id", "owner_id"))
    for workspace in workspaces:
        Membership.objects.get_or_create(
            workspace_id=workspace.id, user_id=workspace.owner_id, defaults={"role": "administrator"}
        )
    administrator = Membership.objects.filter(
        workspace=OuterRef("workspace"), role="administrator", removed_at__isnull=True
    ).values("pk")[:1]
    for name in ("APIKey", "Artifact", "PendingUpload", "SafetyEvent"):
        model = apps.get_model("drop_core", name)
        last_pk = 0
        while True:
            pks = list(
                model.objects.filter(member__isnull=True, pk__gt=last_pk)
                .order_by("pk")
                .values_list("pk", flat=True)[:BATCH_SIZE]
            )
            if not pks:
                break
            model.objects.filter(pk__in=pks).update(member=Subquery(administrator))
            last_pk = pks[-1]


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ('drop_core', '0012_member_columns'),
    ]

    operations = [
        migrations.RunPython(backfill_members, migrations.RunPython.noop),
    ]
