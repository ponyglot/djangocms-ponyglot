"""Which fields are translated: plugin fields and content model fields.

Plugin fields come from the plugin's **form**, for every plugin: what editors fill in is what
gets translated. That's the form the plugin admin shows: the plugin's own form or the model
form django CMS generates, limited to `fields`/`fieldsets`, without `exclude` and
`readonly_fields`. Entangled forms (djangocms-entangled, used by djangocms-frontend) store
fields in a JSON model field; those are addressed as `<json field>.<key>`, e.g.
`config.heading`. A form field is translated if it holds prose:

- a plain `forms.CharField` (subclasses such as URL, slug, email or icon pickers are not),
  not hidden, and not named like an identifier, code, CSS selector or class, icon or anchor;
- an `HTMLFormField` (rich text), as HTML.

Model fields map to form fields the usual way (a text field becomes a `CharField`, a URL field
a `URLField`, a field with choices a choice field), so only prose remains. If a form can't be
built, the model's text fields are used instead.

djangocms-translations' declarations win (`DJANGOCMS_TRANSLATIONS_CONF`, conf.py): `fields`
lists exactly what is translated (JSON key names or dotted paths), `excluded_fields` removes
fields, and `text_field_child_label` names the field of a plugin embedded in rich text that is
translated inside the text (in its sentence) rather than on its own.
"""

from dataclasses import dataclass

from cms.models import CMSPlugin
from django import forms
from django.db import models
from ponyglot.adapters import is_translatable_field
from ponyglot.conf import get_config

from . import conf

_BASE_FIELDS = {field.name for field in CMSPlugin._meta.get_fields()} | {"cmsplugin_ptr"}
# Name parts of form fields that hold identifiers, code or styling rather than prose.
_NON_PROSE = {
    "id",
    "ids",
    "anchor",
    "code",
    "css",
    "class",
    "classes",
    "selector",
    "siblings",
    "icon",
    "slug",
    "url",
    "href",
    "template",
    "attributes",
    "color",
    "colour",
}
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


@dataclass(frozen=True)
class FieldSpec:
    """A translated plugin field: `path` is a model field name or `<json field>.<key>`."""

    path: str
    model_field: object = None  # for plain model fields (format detection, max_length)
    html: bool = False

    @property
    def max_length(self):
        return getattr(self.model_field, "max_length", None)


def _field(model, name):
    try:
        return model._meta.get_field(name)
    except Exception:  # noqa: BLE001
        return None


def get_value(instance, path):
    name, _, key = path.partition(".")
    value = getattr(instance, name, None)
    if key:
        value = value.get(key) if isinstance(value, dict) else None
    return "" if value is None else value


def set_value(instance, path, text):
    name, _, key = path.partition(".")
    if key:
        data = dict(getattr(instance, name, None) or {})
        data[key] = text
        setattr(instance, name, data)
    else:
        setattr(instance, name, text)


def _is_html_form_field(form_field):
    return any(cls.__name__ == "HTMLFormField" for cls in type(form_field).__mro__)


def prose_kind(name, form_field):
    """ "html", "plain" or None (not prose) for a form field."""
    if _is_html_form_field(form_field):
        return "html"
    if type(form_field) is not forms.CharField:
        return None
    if isinstance(form_field.widget, forms.HiddenInput):
        return None
    if set(name.lower().split("_")) & _NON_PROSE:
        return None
    return "plain"


def _plugin_form(instance):
    """`(form class, visible field names or None, hidden field names)` as the plugin admin
    shows it: the plugin's form (django CMS generates a model form if it has none), limited to
    `fields`/`fieldsets` if set, without `exclude` and `readonly_fields`. `(None, None, ())` if
    no form can be built."""
    from django.contrib.admin.utils import flatten_fieldsets
    from django.forms.models import modelform_factory

    plugin_class = instance.get_plugin_class()
    form = getattr(plugin_class, "form", None)
    if form is None or not hasattr(form, "base_fields"):
        try:
            form = modelform_factory(type(instance), fields="__all__")
        except Exception:  # noqa: BLE001 (unusual models: fall back to model fields)
            return None, None, ()
    visible = plugin_class.fields
    if visible is None and plugin_class.fieldsets:
        visible = flatten_fieldsets(plugin_class.fieldsets)
    hidden = [*(plugin_class.exclude or ()), *(plugin_class.readonly_fields or ())]
    return form, (set(visible) if visible is not None else None), hidden


def _form_fields(instance):
    """`{path: FieldSpec}` from the plugin's form, or None if none can be built."""
    form, visible, hidden = _plugin_form(instance)
    if form is None:
        return None
    model = type(instance)
    entangled = getattr(getattr(form, "_meta", None), "entangled_fields", None) or {}
    json_keys = {key: field for field, keys in entangled.items() for key in keys}
    result = {}
    for name, form_field in form.base_fields.items():
        if name in hidden or (visible is not None and name not in visible):
            continue
        kind = prose_kind(name, form_field)
        if kind is None:
            continue
        if name in json_keys:
            path = f"{json_keys[name]}.{name}"
            result[path] = FieldSpec(path, None, kind == "html")
            continue
        model_field = _field(model, name)
        if model_field is None or name in _BASE_FIELDS or not is_translatable_field(model_field):
            continue  # not stored, or not text on the model
        result[name] = FieldSpec(name, model_field, kind == "html")
    return result


def _model_fields(instance):
    model = type(instance)
    return {
        field.name: FieldSpec(field.name, field)
        for field in model._meta.concrete_fields
        if field.name not in _BASE_FIELDS and is_translatable_field(field)
    }


def resolve_path(instance, name):
    """A declared field name as a path: a model field, else a key of an entangled JSON field."""
    if "." in name or _field(type(instance), name) is not None:
        return name
    form, _, _ = _plugin_form(instance)
    entangled = getattr(getattr(form, "_meta", None), "entangled_fields", None) or {}
    for field, keys in entangled.items():
        if name in keys:
            return f"{field}.{name}"
    return name


def _spec(instance, path, candidates):
    if path in candidates:
        return candidates[path]
    name, _, key = path.partition(".")
    model_field = _field(type(instance), name)
    if model_field is None:
        return None
    return FieldSpec(path, None if key else model_field)


def plugin_fields(instance):
    """`{path: FieldSpec}` of a bound plugin's translated fields."""
    declaration = conf.plugin_conf(instance.plugin_type)
    candidates = _form_fields(instance)
    if candidates is None:
        candidates = _model_fields(instance)
    if "fields" in declaration:
        declared = [resolve_path(instance, name) for name in declaration["fields"]]
        candidates = {
            path: spec for path in declared if (spec := _spec(instance, path, candidates))
        }
    excluded = {resolve_path(instance, name) for name in declaration.get("excluded_fields", [])}
    label = type(instance)._meta.label_lower
    excluded_settings = get_config().exclude_fields
    return {
        path: spec
        for path, spec in candidates.items()
        if path not in excluded and f"{label}.{path}" not in excluded_settings
    }


def child_label_field(instance):
    """The path a text-embedded plugin contributes to its parent's text, if declared."""
    name = conf.plugin_conf(instance.plugin_type).get("text_field_child_label")
    return resolve_path(instance, name) if name else None


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
