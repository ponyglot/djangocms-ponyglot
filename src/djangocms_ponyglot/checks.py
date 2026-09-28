from django.apps import apps
from django.core.checks import Error, register


@register()
def check_installed(app_configs, **kwargs):
    messages = []
    for app, why in (
        ("djangocms_versioning", "translations are delivered as drafts"),
        ("ponyglot", "it does the sync"),
    ):
        if not apps.is_installed(app):
            messages.append(
                Error(
                    f"djangocms-ponyglot needs {app!r} in INSTALLED_APPS: {why}.",
                    id="djangocms_ponyglot.E001",
                )
            )
    return messages
