"""django CMS content as Ponyglot units (ADR 0001).

A unit is a grouper of a frontend-editable content type (contenttypes.py): a page, a post, an
alias, a custom object. `djangocms:<content model>:<grouper id>`. The source is its published
source-language content (or, on request, the current draft). Segments are the content model's
translated fields (per-language content types) and the translated plugin fields (extract.py).
"""

from django.utils import translation
from ponyglot.adapters import Adapter, Unit
from ponyglot.conf import get_config

from . import keys as plugin_keys
from .contenttypes import UnitRef, content_types, for_model, parse_key
from .extract import segments_of


def align(ref, content, language, source=None):
    """Share keys between a target-language tree and the source tree, by structure, before the
    target plugins get keys of their own."""
    source_language = get_config().source_language
    source = source or ref.published(source_language) or ref.current(source_language)
    if source is None or (source.pk == content.pk and language == source_language):
        return
    plugin_keys.ensure_keys(source, source_language)
    plugin_keys.inherit_keys(source, source_language, content, language)


def target_segments(ref, content, language):
    align(ref, content, language)
    return segments_of(ref, content, language)


class DjangoCMSAdapter(Adapter):
    name = "djangocms"
    capabilities = frozenset({"qa_errors", "whole_unit"})
    applied_status = "drafted"

    def status_after_write(self, ref):
        # Versioned content: written into a draft, approved on publishing (signals.py).
        # Unversioned content: an editor applied it, and it is live.
        return "drafted" if ref.content_type.versioned else "applied"

    # --- Change detection (see also signals.py) ------------------------------------------

    def watched_models(self):
        watched = {}
        for ct in content_types().values():
            if ct.grouper_model is not None:
                watched[ct.grouper_model] = _no_owner  # deletion only
            if not ct.versioned:
                watched[ct.model] = _unversioned_owner(ct)
        return watched

    def owner_models(self):
        return [ct.grouper_model or ct.model for ct in content_types().values()]

    def iter_objects(self):
        source = get_config().source_language
        for ct in content_types().values():
            filters = {"language": source} if ct.per_language else {}
            grouper = f"{ct.grouper_field}_id" if ct.grouper_field else "pk"
            rows = (
                ct.manager()
                .filter(**filters)
                .values_list(grouper, *ct.extra_fields)
                .distinct()
                .order_by(grouper)
            )
            for grouper_id, *extra in rows:
                yield UnitRef(ct, grouper_id, tuple(zip(ct.extra_fields, extra, strict=True)))

    # --- Identity ---------------------------------------------------------------------------

    def external_key(self, obj):
        return self.ref(obj).key

    def ref(self, obj):
        """The unit of a `UnitRef`, a content object or a grouper."""
        if isinstance(obj, UnitRef):
            return obj
        ct = for_model(type(obj))
        if ct is not None:
            return ct.ref(obj)
        for ct in content_types().values():
            if ct.grouper_model is type(obj):
                return ct.ref_for_grouper(obj)
        raise ValueError(f"{obj!r} isn't django CMS content Ponyglot translates")

    def get_object(self, external_key):
        ref = parse_key(external_key)
        return ref if ref is not None and ref.exists() else None

    def content_kinds(self):
        return [
            (f"{self.name}:{ct.label}:", str(ct.model._meta.verbose_name_plural))
            for ct in content_types().values()
        ]

    def translation_url(self, external_key):
        """The unit's Ponyglot status page in the admin."""
        from django.urls import reverse

        ref = parse_key(external_key)
        if ref is None:
            return ""
        return reverse("admin:djangocms_ponyglot_status", args=[ref.key])

    # --- Snapshots --------------------------------------------------------------------------

    def snapshot(self, ref, *, draft=False, content=None):
        """The unit from the published source (the sync), the current draft (`draft`), or a
        given source-language version (`content`: the one an editor is viewing)."""
        source = get_config().source_language
        if content is None:
            content = ref.current(source) if draft else ref.published(source)
        if content is None:
            return None
        segments = list(segments_of(ref, content, source).values())
        if not segments:
            return None
        self._add_existing_translations(ref, content, segments)
        ct = ref.content_type
        return Unit(
            external_key=ref.key,
            adapter=self.name,
            source_language=source,
            segments=segments,
            label=str(content)[:500],
            path=self.path(ref, content, source),
            metadata={
                "content_type": ct.label,
                "mode": "per_language" if ct.per_language else "shared",
                "versioned": ct.versioned,
            },
        )

    def _add_existing_translations(self, ref, source_content, segments):
        """Published target-language texts, sent on the first push only (imported as
        approved). Target plugins are matched to source plugins by structure first."""
        source = get_config().source_language
        for language in get_config().languages:
            if language == source:
                continue
            # Per-language content: its own object; shared: the same one, other plugins.
            per_language = ref.content_type.per_language
            target = ref.published(language) if per_language else source_content
            if target is None:
                continue
            existing = target_segments(ref, target, language)
            for segment in segments:
                if segment.key in existing:
                    segment.translations = segment.translations or {}
                    segment.translations[language] = existing[segment.key].text

    def path(self, ref, content, language):
        for obj in (content, ref.grouper):
            get_url = getattr(obj, "get_absolute_url", None)
            if get_url is None:
                continue
            try:
                with translation.override(language):
                    try:
                        return get_url(language) or ""
                    except TypeError:
                        return get_url() or ""
            except Exception:  # noqa: BLE001, S112 (a broken URL must not stop the sync)
                continue
        return ""

    # --- Used by the core's suggestion service ("apply") -----------------------------------

    def _source_segment(self, ref, key):
        source = get_config().source_language
        content = ref.published(source)
        return segments_of(ref, content, source).get(key) if content is not None else None

    def source_value(self, ref, key):
        segment = self._source_segment(ref, key)
        return segment.text if segment else None

    def field_format(self, ref, key, value=None):
        segment = self._source_segment(ref, key)
        return segment.format if segment else "plain"

    def target_value(self, ref, key, language):
        content = ref.latest(language)
        if content is None:
            return None
        segment = target_segments(ref, content, language).get(key)
        return segment.text if segment else None

    def deliver(self, results):
        from .delivery import deliver

        return deliver(self, results)

    def write(self, ref, language, values):
        """An editor's explicit "apply": into the current draft even if edited (versioned), or
        live (unversioned)."""
        from .delivery import write

        write(ref, language, values, force=True)


def _no_owner(grouper):
    return None


def _unversioned_owner(ct):
    def owner(content):
        if ct.per_language and content.language != get_config().source_language:
            return None
        return ct.ref(content)

    return owner
