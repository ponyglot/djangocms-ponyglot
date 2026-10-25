"""Which fields are translated: plugin fields and content model fields.

Plugin fields come from the plugin's **form**, for every plugin: what editors fill in is what
gets translated. That's the form the plugin admin shows: the plugin's own form or the model
form django CMS generates, limited to `fields`/`fieldsets`, without `exclude` and
`readonly_fields`. Entangled forms (djangocms-entangled, used by djangocms-frontend) store
fields in a JSON model field; those are addressed as `<json field>.<key>`, e.g.
`config.heading`. A form field is translated if it holds prose:

- a plain `forms.CharField` (subclasses such as URL, slug, email or icon pickers are not),
  not hidden, not validated as a slug or by a pattern (identifiers such as a form field's
  name), and not named like an identifier, code, CSS selector or class, icon or anchor;
- an `HTMLFormField` (rich text), as HTML;
- in HTML attribute fields (djangocms-attributes-field, e.g. djangocms-frontend's
  `attributes`), the values of the attributes that hold prose: `alt`, `title`, `aria-label`,
  `aria-description` and `placeholder` (`config.attributes.alt`). The frontend image plugin
  keeps its alt text there.

Model fields map to form fields the usual way (a text field becomes a `CharField`, a URL field
a `URLField`, a field with choices a choice field), so only prose remains. If a form can't be
built, the model's text fields are used instead.

djangocms-translations' declarations win (`DJANGOCMS_TRANSLATIONS_CONF`, and
djangocms-deepl-translations' `DJANGOCMS_DEEPL_TRANSLATIONS_PLUGIN_CONFIG`, conf.py): `fields`
lists exactly what is translated (JSON key names or dotted paths), `excluded_fields` removes
fields, and `text_field_child_label` names the field of a plugin embedded in rich text that is
translated inside the text (in its sentence) rather than on its own.
"""

from dataclasses import dataclass

from cms.models import CMSPlugin
from django import forms
from django.core.validators import RegexValidator
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
    "value",  # a choice's stored value (djangocms-form-builder), not its label
}
# HTML attributes whose values are prose, in attribute fields (see the module docstring).
PROSE_ATTRIBUTES = ("alt", "title", "aria-label", "aria-description", "placeholder")
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
    """A translated plugin field: `path` is a model field name, or a path into a JSON field
    (`<json field>.<key>`, `<json field>.<key>.<key>`)."""

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


def plugin_instance(plugin):
    """The plugin's instance, or None: if it has none, or if its plugin type isn't registered
    (left behind by a removed plugin package; django CMS doesn't render those either)."""
    try:
        plugin.get_plugin_class()
    except KeyError:
        return None
    return plugin.get_plugin_instance()[0]


def get_value(instance, path):
    name, *keys = path.split(".")
    value = getattr(instance, name, None)
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return "" if value is None else value


def _set_in(data, keys, text):
    """A copy of the dict `data` with `text` at `keys`, creating missing levels."""
    data = dict(data) if isinstance(data, dict) else {}
    key, *rest = keys
    data[key] = _set_in(data.get(key), rest, text) if rest else text
    return data


def set_value(instance, path, text):
    name, *keys = path.split(".")
    if keys:
        setattr(instance, name, _set_in(getattr(instance, name, None), keys, text))
    else:
        setattr(instance, name, text)


def _class_names(form_field):
    return {cls.__name__ for cls in type(form_field).__mro__}


def _is_identifier(form_field):
    """Validated by a pattern (slugs included): an identifier, not prose."""
    return any(isinstance(validator, RegexValidator) for validator in form_field.validators)


def prose_kind(name, form_field):
    """ "html", "plain", "attributes" (an attribute field, see PROSE_ATTRIBUTES) or None (not
    prose) for a form field."""
    names = _class_names(form_field)
    if "HTMLFormField" in names:
        return "html"
    if "AttributesFormField" in names:
        # Choices fields (djangocms-frontend) reuse the widget for a list of pairs.
        return None if "ChoicesFormField" in names else "attributes"
    if type(form_field) is not forms.CharField:
        return None
    if isinstance(form_field.widget, forms.HiddenInput):
        return None
    if _is_identifier(form_field) or set(name.lower().split("_")) & _NON_PROSE:
        return None
    return "plain"


def _fieldsets(plugin_class, instance):
    """The plugin admin's fieldsets. Some plugins add fields at request time in
    `get_fieldsets()` (djangocms-frontend: "Advanced settings" with `attributes`); that's asked
    without a request, falling back to the static `fieldsets`."""
    from cms.plugin_base import CMSPluginBase
    from django.contrib import admin

    if plugin_class.get_fieldsets is CMSPluginBase.get_fieldsets:
        return plugin_class.fieldsets
    try:
        return plugin_class(type(instance), admin.site).get_fieldsets(None, instance)
    except Exception:  # noqa: BLE001 (needs a request: the static fieldsets then)
        return plugin_class.fieldsets


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
    if visible is None:
        fieldsets = _fieldsets(plugin_class, instance)
        if fieldsets:
            visible = flatten_fieldsets(fieldsets)
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
            base = f"{json_keys[name]}.{name}"
        elif kind == "attributes" and _field(model, name) is not None:
            base = name  # a JSON model field
        else:
            base = None
        if kind == "attributes":
            if base is not None:
                for attribute in PROSE_ATTRIBUTES:
                    result[f"{base}.{attribute}"] = FieldSpec(f"{base}.{attribute}")
            continue
        if base is not None:
            result[base] = FieldSpec(base, None, kind == "html")
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
    """A declared field name as a path: a model field, else a key of an entangled JSON field
    (`heading` → `config.heading`, `attributes.alt` → `config.attributes.alt`)."""
    first, _, rest = name.partition(".")
    if _field(type(instance), first) is not None:
        return name
    form, _, _ = _plugin_form(instance)
    entangled = getattr(getattr(form, "_meta", None), "entangled_fields", None) or {}
    for field, keys in entangled.items():
        if first in keys:
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
