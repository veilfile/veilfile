from django.db import migrations
from django.db.models import OuterRef, Subquery


BATCH_SIZE = 1000


def backfill_members_again(apps, schema_editor):
    """A frozen copy of 0013's backfill that also proves the work is finished. It gives every workspace its
    owner as administrator, then gives that administrator every key, upload URL, file and safety event that
    names no member. The migration is not atomic, and the rows are updated in batches of BATCH_SIZE that each
    commit on their own, so no batch holds a row lock for long while the previous release is still serving
    requests. The update only touches a row that still names no member, so a row changed between the select
    and the update is never overwritten. It is safe to run more than once. A row whose workspace has no
    current administrator stays NULL, and the keyset moves past it, so the loop always ends. After the
    batches, any key, file or upload URL that still names no member stops the deploy.

    This migration runs once, before any code can delete a membership. Do not reuse this function after that,
    because a NULL SafetyEvent.member will then mean a deleted membership, not a row waiting to be filled."""
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
            model.objects.filter(pk__in=pks, member__isnull=True).update(member=Subquery(administrator))
            last_pk = pks[-1]
    # SafetyEvent is filled above but not counted here, because its column is SET_NULL by design.
    keys, files, uploads = (
        apps.get_model("drop_core", name).objects.filter(member__isnull=True).count()
        for name in ("APIKey", "Artifact", "PendingUpload")
    )
    if keys or files or uploads:
        raise RuntimeError(
            f"The member backfill stopped this deploy. Rows that still name no member: keys {keys}, "
            f"files {files}, upload URLs {uploads}. Each row's workspace needs a current administrator. "
            "Fix that and deploy again."
        )


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ('drop_core', '0013_backfill_members'),
    ]

    operations = [
        migrations.RunPython(backfill_members_again, migrations.RunPython.noop),
    ]
