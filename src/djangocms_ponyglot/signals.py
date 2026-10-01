"""Content changes → keys, sync and review outcomes (ADR 0001).

Versioned content types (djangocms-versioning operations):

- a new draft copied from a version inherits its plugin keys (every language it holds);
- publishing or unpublishing source content marks the unit for the next sync (per-language
  content types: the source language's content; shared ones: any publication, as the source
  plugins may have changed);
- publishing target-language content is the approval: delivered texts are compared with what
  was published and reported (approved, with the editor's final text). Shared content types
  publish all languages at once, so every language with drafted translations is approved.
  Discarding or archiving a delivered draft reports the translations as rejected.

Unversioned content types: saving the content (core signal handling, adapter.py) or one of
its source-language plugins marks the unit for the next sync.
"""

from cms.models import CMSPlugin
from django.utils import timezone
from ponyglot import state
from ponyglot.conf import get_config
from ponyglot.models import Suggestion, SuggestionStatus

from . import keys as plugin_keys
from .contenttypes import for_model


def _adapter():
    from ponyglot.adapters import registry

    return registry.get("djangocms")


def _drafted(ref, language=None):
    drafted = Suggestion.objects.filter(external_key=ref.key, status=SuggestionStatus.DRAFTED)
    return drafted.filter(language=language) if language else drafted


def _languages(content, ct):
    if ct.per_language:
        return [content.language]
    return sorted(
        set(
            CMSPlugin.objects.filter(placeholder__in=plugin_keys.placeholders(content)).values_list(
                "language", flat=True
            )
        )
    )


def version_operation(sender, operation, obj, **kwargs):
    from djangocms_versioning import constants

    ct = for_model(sender)
    if ct is None:
        return
    version, content = obj, obj.content
    ref = ct.ref(content)
    source_language = get_config().source_language
    if operation == constants.OPERATION_DRAFT:
        if version.source_id:
            for language in _languages(content, ct):
                plugin_keys.inherit_keys(version.source.content, language, content, language)
        return
    is_source = not ct.per_language or content.language == source_language
    if is_source and operation in (constants.OPERATION_PUBLISH, constants.OPERATION_UNPUBLISH):
        state.mark_dirty(_adapter(), ref)
    targets = (
        [content.language]
        if ct.per_language and content.language != source_language
        else ([] if ct.per_language else sorted({s.language for s in _drafted(ref)}))
    )
    if operation == constants.OPERATION_PUBLISH:
        for language in targets:
            approve(ref, content, language)
    elif operation == constants.OPERATION_ARCHIVE:
        for language in targets:
            reject(ref, language)


def approve(ref, content, language):
    """Published: report what the editor approved (with corrections)."""
    from .adapter import target_segments

    drafted = list(_drafted(ref, language))
    if not drafted:
        return
    published = target_segments(ref, content, language)
    now = timezone.now()
    for suggestion in drafted:
        segment = published.get(suggestion.field)
        if segment is None:  # the editor removed it
            suggestion.status = SuggestionStatus.REJECTED
        else:
            suggestion.status = SuggestionStatus.APPLIED
            if segment.text != suggestion.text:
                suggestion.final_text = segment.text
        suggestion.decided_at = now
        suggestion.save(update_fields=["status", "final_text", "decided_at"])


def reject(ref, language=None):
    _drafted(ref, language).update(status=SuggestionStatus.REJECTED, decided_at=timezone.now())


def version_deleted(sender, instance, **kwargs):
    """A draft was discarded (djangocms-versioning deletes it)."""
    from .contenttypes import parse_key
    from .models import DraftDelivery

    deliveries = DraftDelivery.objects.filter(version_id=instance.pk)
    for delivery in deliveries:
        latest = DraftDelivery.objects.filter(
            external_key=delivery.external_key, language=delivery.language
        ).first()
        ref = parse_key(delivery.external_key)
        if ref is not None and latest and latest.pk == delivery.pk:
            reject(ref, delivery.language)


def plugin_changed(sender, instance, **kwargs):
    """Unversioned content: a source-language plugin changed."""
    if not isinstance(instance, CMSPlugin) or kwargs.get("raw"):
        return
    if instance.language != get_config().source_language:
        return
    from cms.models import Placeholder

    placeholder = Placeholder.objects.filter(pk=instance.placeholder_id).first()
    try:
        source = placeholder.source if placeholder is not None else None
    except Exception:  # noqa: BLE001 (the source is being deleted)
        return
    ct = for_model(type(source)) if source is not None else None
    if ct is not None and not ct.versioned:
        state.mark_dirty(_adapter(), ct.ref(source))


def source_draft_discarding(sender, instance, **kwargs):
    """A source-language draft is about to be discarded: the cloud may have its texts (the
    dialog translates the version being viewed), so the next sync sends the published one."""
    from djangocms_versioning import constants

    if instance.state != constants.DRAFT:
        return
    try:
        content = instance.content
    except Exception:  # noqa: BLE001 (already gone)
        return
    ct = for_model(type(content)) if content is not None else None
    if ct is None:
        return
    if not ct.per_language or content.language == get_config().source_language:
        state.mark_dirty(_adapter(), ct.ref(content))
