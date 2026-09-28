"""Writing translations into django CMS content (ADR 0001, decisions 3, 4 and 6).

Versioned content types get drafts:

- **per language** (a content object per language, e.g. pages, posts):
  - no target-language content yet: it's created as a draft (pages through django CMS' API,
    which handles the URL; others with the content type's versioning copy function), with the
    source plugin tree and the translated texts;
  - a published or older version only: a new draft is copied from it and updated;
- **shared** (one content object for all languages): the object's draft (a new one copied from
  the published version if needed) gets the target-language plugins, created from the source
  plugins if that language has none yet. Publishing it publishes all languages together.

A draft this connector wrote and nobody touched since is updated in place; a draft someone
edited isn't written: the translations wait as pending suggestions until an editor chooses
"Apply" (`force=True`). Unversioned content types are never written automatically (writing
would publish): translations wait for an editor's "Apply", which writes them live.

Only the plugins whose texts changed are touched, so editors' corrections elsewhere stay.
Source texts without a counterpart in the target plugin tree are recorded as `unaligned`.
"""

import hashlib
import json
from collections import defaultdict

from cms.models import CMSPlugin, Placeholder
from cms.utils.plugins import copy_plugins_to_placeholder
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from ponyglot.conf import get_config
from ponyglot.exclusions import is_excluded
from ponyglot.models import Suggestion, SuggestionStatus

from . import conf
from . import keys as plugin_keys
from .adapter import align
from .extract import texts, write_values
from .fields import content_fields
from .models import DraftDelivery


def service_user():
    """The user that authors drafts (djangocms-versioning needs one)."""
    User = get_user_model()
    user, created = User._default_manager.get_or_create(
        **{User.USERNAME_FIELD: conf.username()}, defaults={"is_active": False}
    )
    if created:
        user.set_unusable_password()
        user.save()
    return user


def texts_hash(ref, content, language):
    data = texts(ref, content, language)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _untouched(ref, language, content):
    """Whether nobody edited the draft since this connector wrote it.

    Per-language content: the language's last delivery is this draft, texts unchanged. Shared
    content (all languages in one draft): every language delivered into this draft is
    unchanged, and a language not delivered yet has no plugins of its own.
    """
    latest = {}
    for delivery in DraftDelivery.objects.filter(external_key=ref.key):
        latest.setdefault(delivery.language, delivery)  # newest first
    if ref.content_type.per_language:
        latest = {language: latest[language]} if language in latest else {}
    ours = {code: d for code, d in latest.items() if d.content_id == content.pk}
    if not ours:
        return False
    if any(d.texts_hash != texts_hash(ref, content, code) for code, d in ours.items()):
        return False
    return language in ours or not _has_plugins(content, language)


def _placeholder(content, slot):
    existing = Placeholder.objects.get_for_obj(content).filter(slot=slot).first()
    return existing or Placeholder.objects.create(slot=slot, source=content)


def copy_tree(ref, source, target, language):
    """Replace the target-language plugins of `target` with a copy of the source tree."""
    source_language = get_config().source_language
    if hasattr(target, "rescan_placeholders"):
        target.rescan_placeholders()
    for slot, plugins in plugin_keys.plugins_by_slot(source, source_language).items():
        placeholder = _placeholder(target, slot)
        for existing in placeholder.get_plugins(language=language):
            existing.delete()
        if plugins:
            copy_plugins_to_placeholder(plugins, placeholder, language=language)
    plugin_keys.inherit_keys(source, source_language, target, language)


def _has_plugins(content, language):
    return any(plugin_keys.plugins_by_slot(content, language).values())


def _set_fields(content, values):
    for name in content_fields(type(content)):
        if values.get(name):
            setattr(content, name, values[name])


def _new_slug(content, values):
    """New target-language content: its slug comes from the translated title, not the
    source's slug (which would otherwise look hand-written)."""
    from django.utils.text import slugify

    from .fields import slug_field

    field = slug_field(type(content))
    if field is not None:
        slug = slugify(values.get("title") or "")[: field.max_length or 50]
        setattr(content, field.name, slug)


def _create_content(ref, source, language, values, user):
    """A new target-language content object (per-language content types)."""
    from cms.models import PageContent

    ct = ref.content_type
    if ct.model is PageContent:
        from cms.api import create_page_content

        content = create_page_content(
            language,
            values.get("title") or source.title,
            source.page,
            menu_title=values.get("menu_title"),
            page_title=values.get("page_title"),
            meta_description=values.get("meta_description"),
            template=source.template,
            created_by=user,
        )
        copy_tree(ref, source, content, language)
        return content
    if ct.versioned:
        from djangocms_versioning.models import Version

        content = ct.versionable.copy_function(source)  # fields, placeholders and plugins
        content.language = language
        _set_fields(content, values)
        _new_slug(content, values)
        content.save()
        CMSPlugin.objects.filter(
            placeholder__in=plugin_keys.placeholders(content), language=source.language
        ).update(language=language)
        Version.objects.create(content=content, created_by=user)  # a draft
        plugin_keys.inherit_keys(source, source.language, content, language)
        return content
    fields = {
        field.name: getattr(source, field.name)
        for field in type(source)._meta.concrete_fields
        if not field.primary_key
    }
    content = type(source)._default_manager.create(**{**fields, "language": language})
    _set_fields(content, values)
    _new_slug(content, values)
    content.save()
    copy_tree(ref, source, content, language)
    return content


def target_content(ref, language, values, *, force=False, rebuild=False):
    """The content to write into, or None if it must not be written (an editor's draft, or
    unversioned content without an explicit "apply")."""
    ct = ref.content_type
    source_language = get_config().source_language
    source = ref.published(source_language) or ref.current(source_language)
    if source is None:
        return None
    if not ct.versioned and not force:
        return None
    user = service_user()
    content = ref.latest(language) if ct.per_language else ref.latest()
    if content is None:
        if not ct.per_language:
            return None
        return _create_content(ref, source, language, values, user)
    if ct.versioned:
        from djangocms_versioning import constants
        from djangocms_versioning.models import Version

        version = Version.objects.get_for_content(content)
        if version.state != constants.DRAFT:
            content = version.copy(user).content  # keys follow (signals.py)
        elif not (force or _untouched(ref, language, content)):
            return None
    if rebuild or not _has_plugins(content, language):
        copy_tree(ref, source, content, language)
    else:
        align(ref, content, language)
    return content


@transaction.atomic
def write(ref, language, values, *, force=False, rebuild=False):
    """Write translations; returns the `DraftDelivery`, or None if nothing was written."""
    content = target_content(ref, language, values, force=force, rebuild=rebuild)
    if content is None:
        return None
    unaligned = write_values(ref, content, language, values)
    version_id = None
    if ref.content_type.versioned:
        from djangocms_versioning.models import Version

        version_id = Version.objects.get_for_content(content).pk
    return DraftDelivery.objects.create(
        external_key=ref.key,
        language=language,
        content_id=content.pk,
        version_id=version_id,
        texts_hash=texts_hash(ref, content, language),
        unaligned=unaligned,
    )


def deliver(adapter, results):
    """Store results as suggestions; write each unit × language into its draft."""
    handled, groups = [], defaultdict(list)
    for result in results:
        ref = adapter.get_object(result["unit"])
        if ref is None or is_excluded(result["unit"], result["language"]):
            handled.append(result["id"])
        else:
            groups[(ref.key, result["language"])].append((ref, result))
    for (_, language), items in groups.items():
        ref = items[0][0]
        with transaction.atomic():
            fresh = [s for s in (_store(adapter, result) for _, result in items) if s]
            if fresh and ref.content_type.versioned:
                delivery = write(ref, language, {s.field: s.text for s in fresh})
                if delivery is not None:
                    Suggestion.objects.filter(pk__in=[s.pk for s in fresh]).update(
                        status=SuggestionStatus.DRAFTED
                    )
        handled += [result["id"] for _, result in items]
    return handled


def _store(adapter, result):
    """A pending suggestion for the result (superseding older ones), or None if known."""
    if Suggestion.objects.filter(result_id=result["id"]).exists():
        return None
    Suggestion.objects.filter(
        external_key=result["unit"],
        field=result["key"],
        language=result["language"],
        status__in=[SuggestionStatus.PENDING, SuggestionStatus.DRAFTED],
    ).update(status=SuggestionStatus.SUPERSEDED, decided_at=timezone.now())
    return Suggestion.objects.create(
        result_id=result["id"],
        adapter=adapter.name,
        external_key=result["unit"],
        field=result["key"],
        language=result["language"],
        text=result["text"],
        format=result.get("format") or "plain",
        source_fingerprint=result.get("source_fingerprint", ""),
        origin=result.get("origin", ""),
        qa=result.get("qa") or [],
    )


def waiting(ref, language=None):
    """Pending suggestions of a unit: waiting for an editor's "Apply"."""
    pending = Suggestion.objects.filter(external_key=ref.key, status=SuggestionStatus.PENDING)
    return pending.filter(language=language) if language else pending
