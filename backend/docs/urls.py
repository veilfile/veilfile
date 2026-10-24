"""Docs URL routes."""

from django.urls import path

from docs import views

urlpatterns = [
    path("", views.docs_index, name="docs-index"),
    path("<slug:slug>/", views.docs_page, name="docs-page"),
]
