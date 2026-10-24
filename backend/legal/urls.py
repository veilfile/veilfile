"""Legal URL routes."""

from django.urls import path

from legal import views

urlpatterns = [
    path("terms/", views.terms, name="terms"),
    path("privacy/", views.privacy, name="privacy"),
    path("report-abuse/", views.report_abuse, name="report-abuse"),
]
