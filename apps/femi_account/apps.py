from django.apps import AppConfig


class FemiAccountConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.femi_account"

    def ready(self):
        import apps.femi_account.signals  # noqa