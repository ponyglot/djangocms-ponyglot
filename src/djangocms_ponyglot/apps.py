from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class DjangoCMSPonyglotConfig(AppConfig):
    name = "djangocms_ponyglot"
    verbose_name = _("Ponyglot: django CMS")
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from django.db.models.signals import post_delete
        from djangocms_versioning.models import Version
        from djangocms_versioning.signals import post_version_operation
        from ponyglot.adapters import registry
        from ponyglot.state import connect_signals

        from . import checks  # noqa: F401 (registers system checks)
        from .adapter import DjangoCMSAdapter
        from .signals import version_deleted, version_operation

        connect_signals(registry.register(DjangoCMSAdapter()))
        post_version_operation.connect(
            version_operation, dispatch_uid="djangocms_ponyglot.version_operation"
        )
        post_delete.connect(
            version_deleted, sender=Version, dispatch_uid="djangocms_ponyglot.version_deleted"
        )
