from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from .account_deletion import REFUSALS, DeletionRefused, delete_account, deletion_summary, why_not_delete
from .holds import hold_files, release_files
from .models import APIKey, Artifact, EmailVerification, Invitation, MemberUsageCounter, Membership, UsageCounter, Workspace


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "plan", "suspended_at", "created_at")
    list_filter = ("plan",)
    # Changed only by the actions below. A full form save would write back a stale plan and stale Stripe values.
    readonly_fields = ("owner", "suspended_at", "file_visibility")
    actions = ("suspend_workspaces", "lift_suspensions")

    def has_add_permission(self, request):
        # A workspace comes from signup, and its owner moves only through an accepted administrator offer.
        return False

    def has_delete_permission(self, request, obj=None):
        # An account's workspace goes with the account, through the user admin's delete.
        return False

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
    # safety_status changes only through the actions below, so that no hold skips the notice to the administrator.
    readonly_fields = ("token", "safety_status")
    raw_id_fields = ("member",)
    actions = ("hold_selected_files", "release_selected_files")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["member"].required = True
        return form

    @admin.action(description="Hold the selected files", permissions=["change"])
    def hold_selected_files(self, request, queryset):
        chosen = list(queryset.exclude(safety_status="held"))
        result = hold_files([artifact.pk for artifact in chosen])
        for artifact in chosen:
            if artifact.pk in result.held_ids:
                self.log_change(request, artifact, "Held.")
        told = result.told
        self.message_user(
            request,
            f"{result.held} {'file' if result.held == 1 else 'files'} held. "
            f"{told} {'administrator was' if told == 1 else 'administrators were'} told.",
        )
        if result.not_told:
            self.message_user(
                request,
                f"The notice to {result.not_told} {'administrator' if result.not_told == 1 else 'administrators'} "
                "could not be sent, so tell them another way.",
                messages.WARNING,
            )

    @admin.action(description="Release the selected files", permissions=["change"])
    def release_selected_files(self, request, queryset):
        chosen = list(queryset.filter(safety_status="held"))
        released_ids = release_files([artifact.pk for artifact in chosen])
        released = len(released_ids)
        for artifact in chosen:
            if artifact.pk in released_ids:
                self.log_change(request, artifact, "Released.")
        self.message_user(
            request,
            f"{released} {'file' if released == 1 else 'files'} released. The keys that uploaded them stay stopped.",
        )


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
    # The person, team, role, closing and offer each carry rules of their own, so staff change allowances only.
    readonly_fields = ("workspace", "user", "role", "removed_at", "offered_administrator_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # A row is closed, never deleted; it goes with its account.
        return False


@admin.register(MemberUsageCounter)
class MemberUsageCounterAdmin(admin.ModelAdmin):
    list_display = ("member", "year_month", "uploads")
    raw_id_fields = ("member",)


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "workspace", "created_at", "expires_at", "accepted_at", "declined_at", "revoked_at")
    exclude = ("token_hash",)
    raw_id_fields = ("workspace", "invited_by")


User = get_user_model()
admin.site.unregister(User)


@admin.register(User)
class AccountAdmin(UserAdmin):
    """The user admin with a delete of its own. It never opens a transaction and never runs Django's collector, because
    delete_account takes its steps one at a time and removes each batch of stored bytes after its commit."""

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def delete_view(self, request, object_id, extra_context=None):
        user = self.get_object(request, unquote(object_id))
        if user is None:
            return self._get_obj_does_not_exist_redirect(request, self.opts, object_id)
        if not self.has_delete_permission(request, user):
            raise PermissionDenied
        change_url = reverse("admin:auth_user_change", args=[user.pk])
        code = why_not_delete(user)
        if code is not None:
            messages.error(request, REFUSALS[code])
            return redirect(change_url)
        if request.method == "POST" and request.POST.get("post") == "yes":
            pk = user.pk
            try:
                delete_account(user)
            except DeletionRefused as refused:
                messages.error(request, REFUSALS[refused.code])
                return redirect(change_url)
            user.pk = pk
            self.log_deletions(request, [user])
            messages.success(request, "The account is deleted, with its files and keys.")
            return redirect(reverse("admin:auth_user_changelist"))
        context = {
            **self.admin_site.each_context(request),
            "title": f"Delete {user.email or user.get_username()}?",
            "subject": user.email or user.get_username(),
            "opts": self.opts,
            "object": user,
            "summary": deletion_summary(user),
            "change_url": change_url,
            **(extra_context or {}),
        }
        return TemplateResponse(request, "admin/auth/user/delete_account.html", context)
