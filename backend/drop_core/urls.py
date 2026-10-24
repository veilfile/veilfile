from django.urls import path

from . import device as device_views
from . import views

app_name = "drop_core"

urlpatterns = [
    # Trailing slashes are optional on every endpoint: agents follow
    # llms.txt examples verbatim, and a 301 on a POST would otherwise
    # silently drop the upload body.
    path("artifacts", views.ArtifactListCreateView.as_view()),
    path("artifacts/", views.ArtifactListCreateView.as_view(), name="artifact-list-create"),
    path(
        "artifacts/<uuid:artifact_id>",
        views.ArtifactDeleteView.as_view(),
    ),
    path(
        "artifacts/<uuid:artifact_id>/",
        views.ArtifactDeleteView.as_view(),
        name="artifact-delete",
    ),
    path("keys", views.KeyListCreateView.as_view()),
    path("keys/", views.KeyListCreateView.as_view(), name="key-list-create"),
    path("keys/<int:key_id>", views.KeyDeleteView.as_view()),
    path("keys/<int:key_id>/", views.KeyDeleteView.as_view(), name="key-delete"),
    path("usage", views.UsageView.as_view()),
    path("usage/", views.UsageView.as_view(), name="usage"),
    # One-time upload URLs minted by the MCP create_upload_url tool.
    # Trailing slash optional like every other endpoint.
    path("uploads/<str:token>", views.PendingUploadView.as_view()),
    path(
        "uploads/<str:token>/",
        views.PendingUploadView.as_view(),
        name="pending-upload",
    ),
    # Device authorization flow (RFC 8628) for `veilfile login`.
    # Trailing slash optional like every other endpoint.
    path("device/code", device_views.DeviceCodeView.as_view()),
    path(
        "device/code/",
        device_views.DeviceCodeView.as_view(),
        name="device-code",
    ),
    path("device/token", device_views.DeviceTokenView.as_view()),
    path(
        "device/token/",
        device_views.DeviceTokenView.as_view(),
        name="device-token",
    ),
    # OAuth grant revocation (Phase 2: MCP-spec OAuth). Trailing slash
    # optional like every other endpoint.
    path("oauth/revoke-grants", views.OAuthGrantRevokeView.as_view()),
    path(
        "oauth/revoke-grants/",
        views.OAuthGrantRevokeView.as_view(),
        name="oauth-revoke-grants",
    ),
    # Connected apps (dashboard, session only): list OAuth grants and
    # disconnect one app.
    path("oauth/grants", views.OAuthGrantListView.as_view()),
    path(
        "oauth/grants/",
        views.OAuthGrantListView.as_view(),
        name="oauth-grant-list",
    ),
    path("oauth/grants/<int:app_id>", views.OAuthGrantDeleteView.as_view()),
    path(
        "oauth/grants/<int:app_id>/",
        views.OAuthGrantDeleteView.as_view(),
        name="oauth-grant-delete",
    ),
]
