"""django CMS pages as Ponyglot units (ADR 0001).

A unit is a page (`djangocms:page:<id>`) in the source language: its published content by
default. Segments are the page fields (title, menu title, page title, meta description) and the
text fields of every plugin, keyed by stable plugin keys (`plugin:<key>:<field>`, keys.py).
Rich text keeps djangocms-text's embedded child plugins as `<cms-plugin id="<key>">`, so the
tags survive translation and map to the target's own child plugins.
"""

import re

from cms.models import CMSPlugin, Page, PageContent
from django.conf import settings
from django.utils import translation
from ponyglot.adapters import Adapter, Segment, Unit, detect_format, is_translatable_field, kind_for
from ponyglot.conf import get_config

from . import keys as plugin_keys

PAGE_FIELDS = {
    "title": ("title", 255),
    "menu_title": ("title", 255),
    "page_title": ("title", 255),
    "meta_description": ("meta_description", None),
}
_BASE_FIELDS = {field.name for field in CMSPlugin._meta.get_fields()} | {"cmsplugin_ptr"}
_CHILD_TAG = re.compile(r'<cms-plugin\b[^>]*?\bid="(?P<id>[^"]+)"[^>]*>.*?</cms-plugin>', re.DOTALL)


def options():
    return getattr(settings, "PONYGLOT", {}) or {}


def excluded_plugin_types():
    return set(options().get("EXCLUDE_PLUGINS", ()))


def source_content(page, *, draft=False):
    """The page's source-language content: published (default) or the current draft."""
    language = get_config().source_language
    if draft:
        return (
            PageContent.admin_manager.filter(page=page, language=language).current_content().first()
        )
    return PageContent.objects.filter(page=page, language=language).first()


def latest_content(page, language):
    """Draft if any, else published, else the newest other version (djangocms-versioning)."""
    return PageContent.admin_manager.filter(page=page, language=language).latest_content().first()


def published_content(page, language):
    return PageContent.objects.filter(page=page, language=language).first()


def plugin_fields(plugin):
    """`{field name: model field}` of a bound plugin's translatable text fields."""
    if plugin.plugin_type in excluded_plugin_types():
        return {}
    excluded = get_config().exclude_fields
    result = {}
    for model_field in type(plugin)._meta.concrete_fields:
        if model_field.name in _BASE_FIELDS or not is_translatable_field(model_field):
            continue
        if f"{plugin._meta.label_lower}.{model_field.name}" in excluded:
            continue
        result[model_field.name] = model_field
    return result


def child_tags_to_keys(text, keys_by_id):
    """`<cms-plugin … id="123">…</cms-plugin>` → `<cms-plugin id="<key>"></cms-plugin>`."""

    def replace(match):
        key = keys_by_id.get(int(match["id"])) if match["id"].isdigit() else None
        return f'<cms-plugin id="{key}"></cms-plugin>' if key else match.group(0)

    return _CHILD_TAG.sub(replace, text)


def child_keys_to_tags(text, plugins_by_key):
    """The reverse, pointing at the target's own child plugins (djangocms-text markup)."""
    try:
        from djangocms_text.utils import plugin_to_tag
    except ImportError:  # pragma: no cover (djangocms-text not installed)
        return text

    def replace(match):
        plugin = plugins_by_key.get(match["id"])
        return plugin_to_tag(plugin) if plugin is not None else ""

    return _CHILD_TAG.sub(replace, text)


def align(page, content):
    """Share keys between `content` (a target language) and the source content, by structure,
    before target plugins get keys of their own."""
    source = source_content(page) or source_content(page, draft=True)
    if source is not None and source.pk != content.pk:
        plugin_keys.ensure_keys(source)
        plugin_keys.inherit_keys(source, content)


def target_segments(page, content):
    align(page, content)
    return segments_of(content)


def segments_of(content):
    """`{segment key: Segment}` of a page content (source or target language)."""
    result = {}
    position = 0
    for name, (kind, max_length) in PAGE_FIELDS.items():
        value = getattr(content, name, "") or ""
        if value.strip():
            result[name] = Segment(
                key=name,
                text=value,
                field=name,
                kind=kind,
                max_length=max_length,
                position=position,
            )
            position += 1
    keys = plugin_keys.ensure_keys(content)
    for slot, plugins in plugin_keys.plugins_by_slot(content).items():
        for plugin in plugins:
            bound, _ = plugin.get_plugin_instance()
            if bound is None:
                continue
            for name, model_field in plugin_fields(bound).items():
                value = getattr(bound, name, "") or ""
                if not str(value).strip():
                    continue
                value = str(value)
                label = f"{bound._meta.label_lower}.{name}"
                segment_format = detect_format(model_field, value, label)
                if segment_format in ("html", "rich_text"):
                    value = child_tags_to_keys(value, keys)
                segment = Segment(
                    key=f"plugin:{keys[plugin.pk]}:{name}",
                    text=value,
                    format=segment_format,
                    field=name,
                    kind=kind_for(name) or "body",
                    max_length=getattr(model_field, "max_length", None),
                )
                segment.parent_key = (
                    f"plugin:{keys[plugin.parent_id]}"
                    if plugin.parent_id in keys
                    else f"placeholder:{slot}"
                )
                segment.position = position
                position += 1
                result[segment.key] = segment
    return result


class DjangoCMSAdapter(Adapter):
    name = "djangocms"
    capabilities = frozenset({"qa_errors", "whole_unit"})
    applied_status = "drafted"  # approved when the draft is published (signals.py)

    def watched_models(self):
        # Content changes are noticed through versioning operations (signals.py); deleting a
        # page deletes its unit.
        return {Page: lambda page: None}

    def owner_models(self):
        return [Page]

    def iter_objects(self):
        source = get_config().source_language
        page_ids = PageContent.objects.filter(language=source).values_list("page_id", flat=True)
        return Page.objects.filter(pk__in=page_ids).order_by("pk").iterator()

    def external_key(self, page):
        return f"{self.name}:page:{page.pk}"

    def get_object(self, external_key):
        prefix, _, rest = external_key.partition(":page:")
        if prefix != self.name or not rest.isdigit():
            return None
        return Page.objects.filter(pk=int(rest)).first()

    def snapshot(self, page, *, draft=False):
        content = source_content(page, draft=draft)
        if content is None:
            return None
        segments = list(segments_of(content).values())
        if not segments:
            return None
        self._add_existing_translations(page, content, segments)
        return Unit(
            external_key=self.external_key(page),
            adapter=self.name,
            source_language=content.language,
            segments=segments,
            label=content.title,
            path=self.path(page, content.language),
            metadata={"template": content.template, "page": page.pk},
        )

    def _add_existing_translations(self, page, source, segments):
        """Published target-language texts, sent on the first push only (imported as
        approved). Target plugins are matched to source plugins by structure first."""
        for language in get_config().languages:
            if language == source.language:
                continue
            target = published_content(page, language)
            if target is None:
                continue
            existing = target_segments(page, target)
            for segment in segments:
                if segment.key in existing:
                    segment.translations = segment.translations or {}
                    segment.translations[language] = existing[segment.key].text

    def path(self, page, language):
        try:
            with translation.override(language):
                return page.get_absolute_url(language) or ""
        except Exception:  # noqa: BLE001 (a broken URL must not stop the sync)
            return ""

    # --- Used by the core's suggestion service ("apply to current draft") ------------------

    def source_value(self, page, key):
        content = source_content(page)
        segment = segments_of(content).get(key) if content else None
        return segment.text if segment else None

    def field_format(self, page, key, value=None):
        content = source_content(page)
        segment = segments_of(content).get(key) if content else None
        return segment.format if segment else "plain"

    def target_value(self, page, key, language):
        content = latest_content(page, language)
        segment = target_segments(page, content).get(key) if content else None
        return segment.text if segment else None

    def deliver(self, results):
        from .delivery import deliver

        return deliver(self, results)

    def write(self, page, language, values):
        """Write `{segment key: text}` into the current draft (an editor's explicit choice,
        so an edited draft is written too)."""
        from .delivery import write_into_draft

        write_into_draft(page, language, values, force=True)
