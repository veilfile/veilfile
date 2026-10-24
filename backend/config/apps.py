from django.apps import AppConfig


class PlatformConfig(AppConfig):
    """Project-level app: registers the production-configuration checks."""

    name = "config"
    label = "veilfile_platform"
    verbose_name = "Veilfile platform"

    def ready(self):
        from . import checks  # noqa: F401  (registers the system checks)
