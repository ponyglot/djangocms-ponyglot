"""django CMS settings, in `PONYGLOT["DJANGOCMS"]`:

PONYGLOT = {
    "API_KEY": …,
    "DJANGOCMS": {
        # Content types (frontend-editable models) to translate; default: all.
        "MODELS": ["cms.pagecontent", "djangocms_stories.postcontent"],
        # Content model fields per model (default: its text fields, see fields.py).
        "CONTENT_FIELDS": {"djangocms_stories.postcontent": ["title", "abstract"]},
        # Plugin fields, same format as djangocms-translations' DJANGOCMS_TRANSLATIONS_CONF
        # (which is read too; these entries win).
        "PLUGINS": {"LinkPlugin": {"fields": ["name"]}},
        # The (inactive) user that authors translation drafts.
        "USER": "ponyglot",
    },
}
"""

from django.conf import settings


def options():
    return (getattr(settings, "PONYGLOT", {}) or {}).get("DJANGOCMS", {}) or {}


# Defaults for common text-embedded link plugins (djangocms-link, djangocms-frontend): their
# label is translated inside the sentence. Settings override these.
DEFAULT_PLUGIN_CONF = {
    "LinkPlugin": {"text_field_child_label": "name"},
    "TextLinkPlugin": {"text_field_child_label": "name"},
}


def plugin_conf(plugin_type):
    """djangocms-translations' declaration for a plugin type, overridden by ours:
    `{"fields": [...], "excluded_fields": [...], "text_field_child_label": "..."}`."""
    conf = dict(DEFAULT_PLUGIN_CONF.get(plugin_type, {}))
    conf.update(getattr(settings, "DJANGOCMS_TRANSLATIONS_CONF", {}).get(plugin_type, {}))
    conf.update(options().get("PLUGINS", {}).get(plugin_type, {}))
    return conf


def content_fields(label):
    fields = options().get("CONTENT_FIELDS", {})
    return {key.lower(): value for key, value in fields.items()}.get(label)


def selected_models():
    models = options().get("MODELS")
    return {label.lower() for label in models} if models is not None else None


def username():
    return options().get("USER", "ponyglot")
