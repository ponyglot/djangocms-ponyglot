"""Which fields are translated: plugin fields and content model fields.

Plugins follow djangocms-translations' declarations (`DJANGOCMS_TRANSLATIONS_CONF`, see
conf.py): `fields` lists exactly what is translated, `excluded_fields` removes fields, and
`text_field_child_label` names the field of a plugin embedded in a text plugin that is
translated inside the text (in its sentence) rather than on its own. Without a declaration,
text fields are translated, except slugs, URLs, emails and fields with choices.
"""

from cms.models import CMSPlugin
from django.db import models
from ponyglot.adapters import is_translatable_field
from ponyglot.conf import get_config

from . import conf

_BASE_FIELDS = {field.name for field in CMSPlugin._meta.get_fields()} | {"cmsplugin_ptr"}
# Bookkeeping text fields on content models that are never content.
_CONTENT_BOOKKEEPING = {
    "language",
    "template",
    "redirect",
    "created_by",
    "changed_by",
    "xframe_options",
    "overwrite_url",
    "soft_root",
    "limit_visibility_in_menu",
}
PAGE_CONTENT_FIELDS = ["title", "menu_title", "page_title", "meta_description"]


def _field(model, name):
    try:
        return model._meta.get_field(name)
    except Exception:  # noqa: BLE001
        return None


def _declared_plugin_fields(model, plugin_type):
    declaration = conf.plugin_conf(plugin_type)
    if "fields" in declaration:
        names = list(declaration["fields"])
    else:
        names = [
            field.name
            for field in model._meta.concrete_fields
            if field.name not in _BASE_FIELDS and is_translatable_field(field)
        ]
    excluded = set(declaration.get("excluded_fields", []))
    return tuple(name for name in names if name not in excluded and _field(model, name))


def plugin_fields(plugin):
    """`{name: model field}` of a bound plugin's translated fields (declaration order)."""
    model = type(plugin)
    excluded = get_config().exclude_fields
    return {
        name: _field(model, name)
        for name in _declared_plugin_fields(model, plugin.plugin_type)
        if f"{model._meta.label_lower}.{name}" not in excluded
    }


def child_label_field(plugin_type):
    """The field a text-embedded plugin of this type contributes to its parent's text."""
    return conf.plugin_conf(plugin_type).get("text_field_child_label")


def content_fields(model):
    """`{name: model field}` translated on a content model (one content object per language)."""
    label = model._meta.label_lower
    declared = conf.content_fields(label)
    if declared is None:
        if label == "cms.pagecontent":
            declared = PAGE_CONTENT_FIELDS
        else:
            declared = [
                field.name
                for field in model._meta.concrete_fields
                if field.name not in _CONTENT_BOOKKEEPING and is_translatable_field(field)
            ]
    excluded = get_config().exclude_fields
    return {
        name: _field(model, name)
        for name in declared
        if _field(model, name) is not None and f"{label}.{name}" not in excluded
    }


def slug_field(model):
    """The content model's slug field (derived from the translated title), if any."""
    field = _field(model, "slug")
    return field if isinstance(field, models.SlugField) else None
