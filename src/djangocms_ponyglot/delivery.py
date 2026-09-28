"""Writing translations into target-language drafts (ADR 0001, decisions 3, 4 and 6).

- No target content yet: create it (djangocms-versioning makes it a draft), copy the source
  plugin tree and fill in the translations.
- A published (or older) version only: a new draft is copied from it and updated.
- A draft this connector wrote that nobody touched since: updated in place.
- A draft someone edited: nothing is written. The translations wait as pending suggestions
  until an editor chooses "Apply to current draft" (then `force=True`).

Only the plugins whose texts changed are touched, so editors' earlier corrections elsewhere
stay. Source segments without a counterpart in the draft's plugin tree are recorded as
`unaligned` (the toolbar warns and offers to copy the plugin tree from the source).
"""

import hashlib
import json
from collections import defaultdict

from cms.api import create_page_content
from cms.utils.plugins import copy_plugins_to_placeholder
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from djangocms_versioning import constants
from djangocms_versioning.models import Version
from ponyglot.exclusions import is_excluded
from ponyglot.models import Suggestion, SuggestionStatus

from . import keys as plugin_keys
from .adapter import (
    PAGE_FIELDS,
    child_keys_to_tags,
    latest_content,
    options,
    plugin_fields,
    source_content,
)
from .models import DraftDelivery


def service_user():
    """The user that creates drafts (djangocms-versioning needs an author)."""
    User = get_user_model()
    name = options().get("DJANGOCMS_USER", "ponyglot")
    user, created = User._default_manager.get_or_create(
        **{User.USERNAME_FIELD: name}, defaults={"is_active": False}
    )
    if created:
        user.set_unusable_password()
        user.save()
    return user


def texts_hash(content):
    """What an editor could have changed: page fields and plugin texts, by plugin id."""
    data = {name: getattr(content, name, "") or "" for name in PAGE_FIELDS}
    for plugins in plugin_keys.plugins_by_slot(content).values():
        for plugin in plugins:
            bound, _ = plugin.get_plugin_instance()
            if bound is None:
                continue
            for name in plugin_fields(bound):
                data[f"{plugin.pk}:{name}"] = str(getattr(bound, name, "") or "")
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _untouched(page, language, content):
    last = DraftDelivery.objects.filter(page_id=page.pk, language=language).first()
    return (
        last is not None
        and last.content_id == content.pk
        and last.texts_hash == texts_hash(content)
    )


def _copy_tree(source, target):
    """Copy the source's plugins into the target (per placeholder slot) and share keys."""
    target.rescan_placeholders()
    targets = {placeholder.slot: placeholder for placeholder in target.get_placeholders()}
    for slot, plugins in plugin_keys.plugins_by_slot(source).items():
        placeholder = targets.get(slot)
        if placeholder is None or not plugins:
            continue
        for existing in placeholder.get_plugins(language=target.language):
            existing.delete()
        copy_plugins_to_placeholder(plugins, placeholder, language=target.language)
    target._placeholder_cache = target.placeholders.all()
    plugin_keys.inherit_keys(source, target)


def target_draft(page, language, values, *, force=False, rebuild=False):
    """The draft to write into, or None if an editor's draft must not be touched."""
    source = source_content(page)
    user = service_user()
    content = latest_content(page, language)
    if content is None:
        content = create_page_content(
            language,
            values.get("title") or source.title,
            page,
            menu_title=values.get("menu_title"),
            page_title=values.get("page_title"),
            meta_description=values.get("meta_description"),
            template=source.template,
            created_by=user,
        )
        _copy_tree(source, content)
        return content
    version = Version.objects.get_for_content(content)
    if version.state != constants.DRAFT:
        content = version.copy(user).content  # keys follow (signals.py)
    elif not (force or _untouched(page, language, content)):
        return None
    if rebuild:
        _copy_tree(source, content)
    return content


def _set_slug(content, old_title, new_title):
    """The slug follows the title while it was derived from it (as in the core)."""
    if not new_title or getattr(content, "slug", None) is None:
        return
    if content.slug and content.slug != slugify(old_title or ""):
        return  # written by hand
    content.slug = slugify(new_title)[:255] or content.slug


def apply_values(content, values):
    """Write `{segment key: text}` into `content`; return the keys without a counterpart."""
    unaligned = []
    page_values = {key: text for key, text in values.items() if key in PAGE_FIELDS}
    if page_values:
        if "title" in page_values:
            _set_slug(content, content.title, page_values["title"])
        for name, text in page_values.items():
            setattr(content, name, text)
        content.save()
    by_plugin = defaultdict(dict)
    for key, text in values.items():
        if key.startswith("plugin:"):
            _, plugin_key, field = key.split(":", 2)
            by_plugin[plugin_key][field] = text
    from .adapter import align

    align(content.page, content)
    targets = plugin_keys.plugins_by_key(content)
    for plugin_key, fields in by_plugin.items():
        plugin = targets.get(plugin_key)
        bound = plugin.get_plugin_instance()[0] if plugin is not None else None
        if bound is None:
            unaligned += [f"plugin:{plugin_key}:{field}" for field in fields]
            continue
        allowed = plugin_fields(bound)
        for field, text in fields.items():
            if field not in allowed:
                unaligned.append(f"plugin:{plugin_key}:{field}")
                continue
            if "<cms-plugin" in text:
                text = child_keys_to_tags(text, targets)
            setattr(bound, field, text)
        bound.save()
    return unaligned


@transaction.atomic
def write_into_draft(page, language, values, *, force=False, rebuild=False):
    """Write translations; returns the `DraftDelivery`, or None if the draft must wait."""
    content = target_draft(page, language, values, force=force, rebuild=rebuild)
    if content is None:
        return None
    unaligned = apply_values(content, values)
    version = Version.objects.get_for_content(content)
    return DraftDelivery.objects.create(
        page_id=page.pk,
        language=language,
        content_id=content.pk,
        version_id=version.pk,
        texts_hash=texts_hash(content),
        unaligned=unaligned,
    )


def deliver(adapter, results):
    """Store results as suggestions and write each page × language into its draft."""
    handled, groups = [], defaultdict(list)
    for result in results:
        page = adapter.get_object(result["unit"])
        if page is None or is_excluded(result["unit"], result["language"]):
            handled.append(result["id"])
        else:
            groups[(page.pk, result["language"])].append((page, result))
    for (_, language), items in groups.items():
        page = items[0][0]
        with transaction.atomic():
            fresh = [_store(adapter, result) for _, result in items]
            fresh = [s for s in fresh if s is not None]
            if fresh:
                delivery = write_into_draft(
                    page, language, {s.field: s.text for s in fresh}, force=False
                )
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


def waiting(page, language=None):
    """Pending suggestions for a page (translations waiting for "Apply to current draft")."""
    from .adapter import DjangoCMSAdapter

    pending = Suggestion.objects.filter(
        external_key=DjangoCMSAdapter().external_key(page), status=SuggestionStatus.PENDING
    )
    return pending.filter(language=language) if language else pending


def source_is_current(page):
    return source_content(page) is not None
