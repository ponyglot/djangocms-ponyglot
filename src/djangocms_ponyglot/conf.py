"""django CMS settings, in `PONYGLOT["DJANGOCMS"]`:

PONYGLOT = {
    "API_KEY": …,
    "DJANGOCMS": {
        # Content types (frontend-editable models) to translate; default: all.
        "MODELS": ["cms.pagecontent", "djangocms_stories.postcontent"],
        # Content model fields per model (default: its text fields, see fields.py).
        "CONTENT_FIELDS": {"djangocms_stories.postcontent": ["title", "abstract"]},
        # Plugin fields, same format as djangocms-translations' DJANGOCMS_TRANSLATIONS_CONF
        # (which is read too, as is djangocms-deepl-translations'
        # DJANGOCMS_DEEPL_TRANSLATIONS_PLUGIN_CONFIG; these entries win).
        "PLUGINS": {"LinkPlugin": {"fields": ["name"]}},
        # The (inactive) user that authors translation drafts.
        "USER": "ponyglot",
        # When no sync round ran for this long, the toolbar's translations dialog fetches the
        # translations of the content it's opened on.
        "FETCH_AFTER_MINUTES": 15,
    },
}
"""

from datetime import timedelta

from django.conf import settings


def options():
    return (getattr(settings, "PONYGLOT", {}) or {}).get("DJANGOCMS", {}) or {}


# Defaults for common text-embedded link plugins (djangocms-link, djangocms-frontend): their
# label is translated inside the sentence. Settings override these.
DEFAULT_PLUGIN_CONF = {
    "LinkPlugin": {"text_field_child_label": "name"},
    "TextLinkPlugin": {"text_field_child_label": "name"},
}


def _deepl_translations_conf(plugin_type):
    """djangocms-deepl-translations' `DJANGOCMS_DEEPL_TRANSLATIONS_PLUGIN_CONFIG` entry for a
    plugin type, in our format. Only the project's setting is read, not that package's
    built-in defaults (the plugin forms cover those). `translatable_fields` are paths into
    the plugin's model data, `"config/heading"` or `("config", "heading")`: our
    `"config.heading"`. `no_translation` translates nothing. Text-embedded plugins need no
    entry there, so `text_field_child_label` stays ours."""
    entry = getattr(settings, "DJANGOCMS_DEEPL_TRANSLATIONS_PLUGIN_CONFIG", {}).get(plugin_type)
    if not isinstance(entry, dict):
        return {}
    if entry.get("no_translation"):
        return {"fields": []}
    if "translatable_fields" not in entry:
        return {}
    paths = [
        path.split("/") if isinstance(path, str) else list(path)
        for path in entry["translatable_fields"]
    ]
    return {"fields": [".".join(key for key in path if key) for path in paths]}


def plugin_conf(plugin_type):
    """djangocms-deepl-translations' and djangocms-translations' declarations for a plugin
    type (the latter wins), overridden by ours:
    `{"fields": [...], "excluded_fields": [...], "text_field_child_label": "..."}`."""
    conf = dict(DEFAULT_PLUGIN_CONF.get(plugin_type, {}))
    conf.update(_deepl_translations_conf(plugin_type))
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


def fetch_after():
    return timedelta(minutes=options().get("FETCH_AFTER_MINUTES", 15))
