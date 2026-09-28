"""djangocms-versioning operations → keys, sync and review outcomes (ADR 0001).

- A new draft copied from a version inherits its plugin keys.
- Publishing or unpublishing the source language marks the page for the next sync.
- Publishing a target language is the approval: delivered texts are compared with what was
  published and reported (approved, with the editor's final text). Discarding or archiving a
  delivered draft reports the translations as rejected.
"""

from django.utils import timezone
from djangocms_versioning import constants
from ponyglot import state
from ponyglot.conf import get_config
from ponyglot.models import Suggestion, SuggestionStatus

from . import keys as plugin_keys
from .adapter import target_segments


def _adapter():
    from ponyglot.adapters import registry

    return registry.get("djangocms")


def _drafted(page, language):
    return Suggestion.objects.filter(
        external_key=_adapter().external_key(page),
        language=language,
        status=SuggestionStatus.DRAFTED,
    )


def version_operation(sender, operation, obj, **kwargs):
    from cms.models import PageContent

    if sender is not PageContent:
        return
    version, content = obj, obj.content
    page = content.page
    if operation == constants.OPERATION_DRAFT and version.source_id:
        plugin_keys.inherit_keys(version.source.content, content)
        return
    if content.language == get_config().source_language:
        if operation in (constants.OPERATION_PUBLISH, constants.OPERATION_UNPUBLISH):
            state.mark_dirty(_adapter(), page)
        return
    if operation == constants.OPERATION_PUBLISH:
        approve(page, content)
    elif operation == constants.OPERATION_ARCHIVE:
        reject(page, content.language)


def approve(page, content):
    """The target version was published: report what the editor approved."""
    drafted = list(_drafted(page, content.language))
    if not drafted:
        return
    published = target_segments(page, content)
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


def reject(page, language):
    _drafted(page, language).update(status=SuggestionStatus.REJECTED, decided_at=timezone.now())


def version_deleted(sender, instance, **kwargs):
    """A draft was discarded (djangocms-versioning deletes it)."""
    from cms.models import PageContent

    from .models import DraftDelivery

    if instance.content_type.model_class() is not PageContent:
        return
    delivery = DraftDelivery.objects.filter(version_id=instance.pk).first()
    if delivery is None:
        return
    latest = DraftDelivery.objects.filter(
        page_id=delivery.page_id, language=delivery.language
    ).first()
    if latest and latest.pk == delivery.pk:
        from cms.models import Page

        page = Page.objects.filter(pk=delivery.page_id).first()
        if page is not None:
            reject(page, delivery.language)
