from django.contrib import admin

from .models import APIKey, Artifact, EmailVerification, Membership, UsageCounter, Workspace


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "plan", "created_at")
    list_filter = ("plan",)


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
    list_display = ("workspace", "user", "role", "created_at", "removed_at")
    list_filter = ("role",)
    raw_id_fields = ("workspace", "user")
