from django.apps import AppConfig


class DropCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "drop_core"

    def ready(self):
        # Registers the post_delete receiver that removes stored bytes.
        from . import signals  # noqa: F401
