from django.urls import path

from . import views

urlpatterns = [
    path("checkout", views.CheckoutView.as_view(), name="billing-checkout"),
    path("switch", views.SwitchPlanView.as_view(), name="billing-switch"),
    path("portal", views.PortalView.as_view(), name="billing-portal"),
    path("status", views.StatusView.as_view(), name="billing-status"),
    path("webhook", views.webhook, name="billing-webhook"),
]
