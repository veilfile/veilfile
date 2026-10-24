"""The dashboard's addresses (spec 4.8), mounted at /app/ by config/urls.py, after /app/components/ (the staff catalog).

Each route is named in pages.registry.DASHBOARD_SECTIONS as well, which says which menu link it marks (a test holds the two together).
Anything else under /app/ is the shell's 404.
"""
from django.urls import path

from dashboard import keys, views

urlpatterns = [
    path("", views.overview, name="app"),
    path("files/", views.files, name="app-files"),
    path("files/<uuid:artifact_id>/revoke/", views.file_revoke, name="app-file-revoke"),
    path("keys/", keys.keys, name="app-keys"),
    path("keys/<int:key_id>/revoke/", keys.key_revoke, name="app-key-revoke"),
    path("apps/", keys.apps, name="app-apps"),
    path("apps/<int:app_id>/disconnect/", keys.app_disconnect, name="app-app-disconnect"),
    path("billing/", views.billing, name="app-billing"),
    path("billing/switch/<slug:plan>/", views.billing_switch, name="app-billing-switch"),
    path("billing/checkout/", views.billing_checkout, name="app-billing-checkout"),
    path("billing/portal/", views.billing_portal, name="app-billing-portal"),
]
