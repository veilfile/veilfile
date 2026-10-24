from django.apps import AppConfig


class DashboardConfig(AppConfig):
    """The signed-in dashboard at /app/: five sections as Django views on layouts/app.html (spec 4.8)."""

    name = "dashboard"
    label = "dashboard"
