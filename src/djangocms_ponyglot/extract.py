"""Segments of a content object in one language, and writing translated values back.

Segment keys: content model fields by name (per-language content types only; in shared
content types those fields are common to all languages), plugin fields as
`plugin:<key>:<field>` with stable plugin keys (keys.py).

Plugins embedded in rich text (djangocms-text's `<cms-plugin>` tags) travel as
`<cms-plugin id="<key>">…</cms-plugin>`. If the child plugin type declares a
`text_field_child_label` (djangocms-translations), that field's text is carried inside the tag,
so it is translated in its sentence, and it isn't sent as a segment of its own.
"""

import re
from html import escape, unescape

from django.utils.html import strip_tags
from django.utils.text import slugify
from ponyglot.adapters import Segment, detect_format, kind_for

from . import keys as plugin_keys
from .fields import (
    child_label_field,
    content_fields,
    get_value,
    plugin_fields,
    set_value,
    slug_field,
)

_CHILD_TAG = re.compile(
    r'<cms-plugin\b[^>]*?\bid="(?P<id>[^"]+)"[^>]*>(?P<content>.*?)</cms-plugin>', re.DOTALL
)
_HTML = ("html", "rich_text")
_CONTENT_KINDS = {"meta_description": "meta_description"}


def _bound_plugins(content, language):
    bound = {}
    for slot, plugins in plugin_keys.plugins_by_slot(content, language).items():
        for plugin in plugins:
            instance, _ = plugin.get_plugin_instance()
            if instance is not None:
                bound[plugin.pk] = (slot, plugin, instance)
    return bound


def _embedded_labels(bound):
    """`{child plugin id: label field}` for text-embedded children that declare one."""
    embedded = {}
    for _, plugin, instance in bound.values():
        if not plugin.parent_id or plugin.parent_id not in bound:
            continue
        label = child_label_field(instance)
        if label:
            embedded[plugin.pk] = label
    return embedded


def tags_to_keys(text, keys, bound, embedded):
    """`<cms-plugin … id="123">…</cms-plugin>` → `<cms-plugin id="<key>">label</cms-plugin>`."""

    def replace(match):
        plugin_id = int(match["id"]) if match["id"].isdigit() else None
        key = keys.get(plugin_id)
        if key is None:
            return match.group(0)
        label = ""
        if plugin_id in embedded:
            label = escape(str(get_value(bound[plugin_id][2], embedded[plugin_id]) or ""))
        return f'<cms-plugin id="{key}">{label}</cms-plugin>'

    return _CHILD_TAG.sub(replace, text)


def keys_to_tags(text, targets):
    """The reverse: tags point at the target's own children; embedded labels are written into
    them. `targets`: `{key: plugin}`. Returns the text."""
    try:
        from djangocms_text.utils import plugin_to_tag
    except ImportError:  # pragma: no cover (djangocms-text not installed)
        return text

    def replace(match):
        plugin = targets.get(match["id"])
        if plugin is None:
            return ""
        instance, _ = plugin.get_plugin_instance()
        label = child_label_field(instance) if instance is not None else None
        if label and match["content"].strip():
            set_value(instance, label, unescape(strip_tags(match["content"])).strip())
            instance.save()
        return plugin_to_tag(plugin)

    return _CHILD_TAG.sub(replace, text)


def segments_of(ref, content, language):
    """`{segment key: Segment}` of `content` in `language`."""
    result = {}
    position = 0
    if ref.content_type.per_language:
        for name, model_field in content_fields(type(content)).items():
            value = getattr(content, name, "") or ""
            if not str(value).strip():
                continue
            result[name] = Segment(
                key=name,
                text=str(value),
                format=detect_format(model_field, str(value), f"{ref.content_type.label}.{name}"),
                field=name,
                kind=_CONTENT_KINDS.get(name) or kind_for(name),
                max_length=getattr(model_field, "max_length", None),
                position=position,
            )
            position += 1
    keys = plugin_keys.ensure_keys(content, language)
    bound = _bound_plugins(content, language)
    embedded = _embedded_labels(bound)
    for plugin_id, (slot, plugin, instance) in bound.items():
        for name, spec in plugin_fields(instance).items():
            if embedded.get(plugin_id) == name:
                continue  # translated inside its parent's text
            value = get_value(instance, name)
            if not isinstance(value, str) or not value.strip():
                continue
            if spec.html:
                segment_format = "html"
            else:
                label = f"{instance._meta.label_lower}.{name}"
                segment_format = detect_format(spec.model_field, value, label)
            if segment_format in _HTML:
                value = tags_to_keys(value, keys, bound, embedded)
            result[f"plugin:{keys[plugin_id]}:{name}"] = Segment(
                key=f"plugin:{keys[plugin_id]}:{name}",
                text=value,
                format=segment_format,
                field=name,
                kind=kind_for(name.rpartition(".")[2]) or "body",
                max_length=spec.max_length,
                parent_key=(
                    f"plugin:{keys[plugin.parent_id]}"
                    if plugin.parent_id in keys
                    else f"placeholder:{slot}"
                ),
                position=position,
            )
            position += 1
    return result


def texts(ref, content, language):
    """What an editor could change (for "untouched" checks): by field and plugin id."""
    data = {}
    if ref.content_type.per_language:
        for name in content_fields(type(content)):
            data[name] = str(getattr(content, name, "") or "")
    for plugin_id, (_, _, instance) in _bound_plugins(content, language).items():
        for name in plugin_fields(instance):
            data[f"{plugin_id}:{name}"] = str(get_value(instance, name) or "")
    return data


def _follow_slug(content, old_title, new_title):
    field = slug_field(type(content))
    if field is None or not new_title:
        return
    current = getattr(content, field.name, "") or ""
    if current and current != slugify(old_title or ""):
        return  # written by hand
    setattr(content, field.name, slugify(new_title)[: field.max_length or 50] or current)


def write_values(ref, content, language, values):
    """Write `{segment key: text}`; return the keys without a counterpart (unaligned)."""
    unaligned = []
    if ref.content_type.per_language:
        own = content_fields(type(content))
        content_values = {key: text for key, text in values.items() if key in own}
        if content_values:
            if "title" in content_values:
                _follow_slug(content, getattr(content, "title", ""), content_values["title"])
            for name, text in content_values.items():
                setattr(content, name, text)
            content.save()
    by_plugin = {}
    for key, text in values.items():
        if key.startswith("plugin:"):
            _, plugin_key, name = key.split(":", 2)
            by_plugin.setdefault(plugin_key, {})[name] = text
    targets = plugin_keys.plugins_by_key(content, language)
    for plugin_key, fields in by_plugin.items():
        plugin = targets.get(plugin_key)
        instance = plugin.get_plugin_instance()[0] if plugin is not None else None
        if instance is None:
            unaligned += [f"plugin:{plugin_key}:{name}" for name in fields]
            continue
        allowed = plugin_fields(instance)
        for name, text in fields.items():
            if name not in allowed:
                unaligned.append(f"plugin:{plugin_key}:{name}")
                continue
            if "<cms-plugin" in text:
                text = keys_to_tags(text, targets)
            set_value(instance, name, text)
        instance.save()
    return unaligned
