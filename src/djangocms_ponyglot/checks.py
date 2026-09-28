from django.apps import apps
from django.core.checks import Error, Info, register


@register()
def check_installed(app_configs, **kwargs):
    messages = []
    if not apps.is_installed("ponyglot"):
        messages.append(
            Error(
                "djangocms-ponyglot needs 'ponyglot' in INSTALLED_APPS: it does the sync.",
                id="djangocms_ponyglot.E001",
            )
        )
    if not apps.is_installed("djangocms_versioning"):
        messages.append(
            Info(
                "djangocms-versioning isn't installed: translations wait as suggestions until "
                "an editor applies them, which writes (and so publishes) them directly.",
                id="djangocms_ponyglot.I001",
            )
        )
    return messages
