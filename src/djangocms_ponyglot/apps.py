from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class DjangoCMSPonyglotConfig(AppConfig):
    name = "djangocms_ponyglot"
    verbose_name = _("Ponyglot: django CMS")
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from django.apps import apps
        from django.db.models.signals import post_delete, post_save, pre_delete
        from ponyglot.adapters import registry
        from ponyglot.state import connect_signals

        from . import checks  # noqa: F401 (registers system checks)
        from .adapter import DjangoCMSAdapter
        from .signals import (
            plugin_changed,
            source_draft_discarding,
            version_deleted,
            version_operation,
        )

        connect_signals(registry.register(DjangoCMSAdapter()))
        post_save.connect(plugin_changed, dispatch_uid="djangocms_ponyglot.plugin_saved")
        post_delete.connect(plugin_changed, dispatch_uid="djangocms_ponyglot.plugin_deleted")
        if apps.is_installed("djangocms_versioning"):
            from djangocms_versioning.models import Version
            from djangocms_versioning.signals import post_version_operation

            post_version_operation.connect(
                version_operation, dispatch_uid="djangocms_ponyglot.version_operation"
            )
            post_delete.connect(
                version_deleted,
                sender=Version,
                dispatch_uid="djangocms_ponyglot.version_deleted",
            )
            pre_delete.connect(
                source_draft_discarding,
                sender=Version,
                dispatch_uid="djangocms_ponyglot.source_draft_discarding",
            )
