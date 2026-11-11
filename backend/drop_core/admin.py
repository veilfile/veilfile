from django.contrib import admin
from django.utils import timezone

from .models import APIKey, Artifact, EmailVerification, Invitation, MemberUsageCounter, Membership, UsageCounter, Workspace


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "plan", "suspended_at", "created_at")
    list_filter = ("plan",)
    # Changed only by the actions below. A full form save would write back a stale plan and stale Stripe values.
    readonly_fields = ("suspended_at", "file_visibility")
    actions = ("suspend_workspaces", "lift_suspensions")

    @admin.action(description="Suspend the selected workspaces", permissions=["change"])
    def suspend_workspaces(self, request, queryset):
        chosen = list(queryset.filter(suspended_at__isnull=True))
        Workspace.objects.filter(pk__in=[w.pk for w in chosen]).update(suspended_at=timezone.now())
        for workspace in chosen:
            self.log_change(request, workspace, "Suspended.")
        self.message_user(request, f"Suspended {len(chosen)} workspace(s).")

    @admin.action(description="Lift the suspension of the selected workspaces", permissions=["change"])
    def lift_suspensions(self, request, queryset):
        chosen = list(queryset.filter(suspended_at__isnull=False))
        Workspace.objects.filter(pk__in=[w.pk for w in chosen]).update(suspended_at=None)
        for workspace in chosen:
            self.log_change(request, workspace, "Lifted the suspension.")
        self.message_user(request, f"Lifted the suspension of {len(chosen)} workspace(s).")


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "workspace", "member", "key_prefix", "is_active", "created_at", "last_used_at")
    list_filter = ("is_active",)
    readonly_fields = ("key_hash", "key_prefix")
    raw_id_fields = ("member",)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["member"].required = True
        return form


@admin.register(Artifact)
class ArtifactAdmin(admin.ModelAdmin):
    list_display = ("filename", "workspace", "member", "size_bytes", "expires_at", "created_at")
    list_filter = ("mime_type",)
    readonly_fields = ("token",)
    raw_id_fields = ("member",)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["member"].required = True
        return form


@admin.register(UsageCounter)
class UsageCounterAdmin(admin.ModelAdmin):
    list_display = ("workspace", "year_month", "uploads")


@admin.register(EmailVerification)
class EmailVerificationAdmin(admin.ModelAdmin):
    list_display = ("email", "user", "is_verified", "verified_at", "last_sent_at")
    list_filter = ("is_verified",)
    readonly_fields = ("user", "email", "verified_at", "last_sent_at", "created_at")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = (
        "workspace",
        "user",
        "role",
        "created_at",
        "removed_at",
        "upload_allowance",
        "storage_allowance_bytes",
        "key_allowance",
    )
    list_filter = ("role",)
    raw_id_fields = ("workspace", "user")


@admin.register(MemberUsageCounter)
class MemberUsageCounterAdmin(admin.ModelAdmin):
    list_display = ("member", "year_month", "uploads")
    raw_id_fields = ("member",)


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "workspace", "created_at", "expires_at", "accepted_at", "declined_at", "revoked_at")
    exclude = ("token_hash",)
    raw_id_fields = ("workspace", "invited_by")
