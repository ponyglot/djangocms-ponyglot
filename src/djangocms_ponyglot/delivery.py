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

**A translation that exists already** (made by hand, before Ponyglot) and whose plugins differ
from the source's is never merged automatically: the translations wait until an editor chooses
(`needs_choice`) to add them to that translation (into its draft, or a new draft from it; texts
without a counterpart keep waiting) or to start from a copy of the source plugins. If its plugins
all match the source's, the translations go into it without asking.

Only the plugins whose texts changed are touched, so editors' corrections elsewhere stay.
The source tree is the version the translations were made from: the published one, or the draft
when it was translated on request. Its structural changes since the last delivery are merged
into the translation (three-way, merge.py): plugins new in the source are added at the same
place, with their children (with their translations, or right away if they have no texts);
plugins moved in the source are moved, unless an editor moved them in the translation; plugins
deleted from the source are listed for an editor to confirm (`DraftDelivery.removed`). Moves and
text-free plugins can't wait for a delivery (no text changed), so publishing the source merges
them too (`follow_source`, per-language content types). Source texts still without a
counterpart (e.g. a plugin an editor removed from the translation) are recorded as `unaligned`
and wait as pending suggestions; "Copy plugin tree from source" rebuilds the tree.
"""

import hashlib
import json
from collections import defaultdict

from cms.models import CMSPlugin, Placeholder
from cms.utils.plugins import copy_plugins_to_placeholder
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from ponyglot import publishing
from ponyglot.conf import get_config
from ponyglot.exclusions import is_excluded
from ponyglot.models import Suggestion, SuggestionStatus

from . import conf, merge
from . import keys as plugin_keys
from .adapter import align
from .extract import texts, write_values
from .fields import content_fields, get_value, plugin_fields
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


def _plugin_keys(values):
    return {key.split(":", 2)[1] for key in values if key.startswith("plugin:")}


def source_content(ref, values=()):
    """The source-language content the translations were made from: the published version,
    or the draft if the delivered plugin texts belong to it (drafts are translated on request)."""
    source_language = get_config().source_language
    published = ref.published(source_language)
    current = ref.current(source_language)
    wanted = _plugin_keys(values)
    if published is None or current is None or current.pk == published.pk or not wanted:
        return published or current

    def coverage(content):
        return len(wanted & set(plugin_keys.plugins_by_key(content, source_language)))

    return current if coverage(current) > coverage(published) else published


def needs_choice(ref, language, values=()):
    """Whether writing translations needs an editor's choice between keeping the language's
    existing translation and starting from a copy of the source plugins: it has plugins this
    connector never delivered to, and they don't all match the source's. Clear cases don't
    ask: no plugins yet (copied from the source), a translation delivered to before, or one
    whose plugins all match."""
    if DraftDelivery.objects.filter(external_key=ref.key, language=language).exists():
        return False
    content = ref.latest(language) if ref.content_type.per_language else ref.latest()
    if content is None or not _has_plugins(content, language):
        return False
    source = source_content(ref, values)
    if source is None:
        return False
    align(ref, content, language, source)
    source_keys = set(plugin_keys.plugins_by_key(source, get_config().source_language))
    return not source_keys <= set(plugin_keys.plugins_by_key(content, language))


def _known_keys(ref, language):
    """Plugin keys this language's drafts had in any delivery so far."""
    known = set()
    for keys in DraftDelivery.objects.filter(external_key=ref.key, language=language).values_list(
        "plugin_keys", flat=True
    ):
        known.update(keys)
    return known


def _subtree(plugin, children):
    result = [plugin]
    for child in children.get(plugin.pk, []):
        result += _subtree(child, children)
    return result


def _children(plugins):
    children = defaultdict(list)
    for plugin in plugins:
        children[plugin.parent_id].append(plugin)
    return children


def _has_texts(plugins):
    for plugin in plugins:
        instance = plugin.get_plugin_instance()[0]
        if instance is None:
            continue
        for name in plugin_fields(instance):
            value = get_value(instance, name)
            if isinstance(value, str) and value.strip():
                return True
    return False


def add_new_plugins(ref, source, target, language, values):
    """Copy plugins new in the source (with their children) into the target tree, after their
    preceding sibling. Only plugins whose keys the language never had (plugins an editor removed
    from the translation stay removed), and only with their translations: subtrees with texts
    in this delivery, or without any texts. Children the target already has (moved there) are
    left out; the merge moves them. Returns the count."""
    source_language = get_config().source_language
    source_keys = plugin_keys.ensure_keys(source, source_language)
    new = set(source_keys.values()) - _known_keys(ref, language)
    if not new:
        return 0
    delivered = _plugin_keys(values)
    added = 0
    for slot, plugins in plugin_keys.plugins_by_slot(source, source_language).items():
        children = _children(plugins)
        by_id = {plugin.pk: plugin for plugin in plugins}
        for plugin in plugins:  # tree order: a missing parent comes before its children
            targets = plugin_keys.plugins_by_key(target, language)
            if source_keys[plugin.pk] in targets:
                continue
            parent = by_id.get(plugin.parent_id)
            if parent is not None and source_keys[parent.pk] not in targets:
                continue  # copied with its parent, or the parent isn't in the translation
            tree = _subtree(plugin, children)
            tree_keys = {source_keys[p.pk] for p in tree}
            if not new & tree_keys or (not delivered & tree_keys and _has_texts(tree)):
                continue
            tree = _without_present(tree, children, source_keys, targets)
            target_parent = targets[source_keys[parent.pk]] if parent is not None else None
            placeholder = _placeholder(target, slot)
            position = _insert_position(
                placeholder, language, target_parent, plugin, children, source_keys, targets
            )
            last = placeholder.get_last_plugin_position(language) or 0
            placeholder._shift_plugin_positions(language, start=position, offset=last + len(tree))
            copied = copy_plugins_to_placeholder(
                tree,
                placeholder,
                language=language,
                root_plugin=target_parent,
                start_positions={language: position},
            )
            plugin_keys._assign(
                [
                    (new_plugin.pk, source_keys[old.pk])
                    for new_plugin, old in zip(copied, tree, strict=True)
                ]
            )
            added += len(tree)
    return added


def _without_present(tree, children, source_keys, targets):
    """The subtree without the plugins (and their descendants) the target has already."""
    skip = set()
    for plugin in tree[1:]:
        if plugin.pk in skip or source_keys[plugin.pk] in targets:
            skip |= {p.pk for p in _subtree(plugin, children)}
    return [p for p in tree if p.pk not in skip]


def _insert_position(placeholder, language, target_parent, plugin, children, source_keys, targets):
    """Right after the subtree of the nearest preceding source sibling the target has; else
    first child of the parent, or first in the placeholder."""
    siblings = children.get(plugin.parent_id, [])
    target_plugins = list(placeholder.get_plugins(language=language))
    target_children = _children(target_plugins)
    for sibling in reversed(siblings[: siblings.index(plugin)]):
        counterpart = targets.get(source_keys[sibling.pk])
        if counterpart is not None:
            return max(p.position for p in _subtree(counterpart, target_children)) + 1
    return target_parent.position + 1 if target_parent is not None else 1


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
    source = source_content(ref, values)
    if source is None:
        return None
    if not ct.versioned and not force:
        return None
    if not force and needs_choice(ref, language, values):
        return None  # an editor chooses: keep the existing translation, or copy the source
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
        align(ref, content, language, source)
        if _known_keys(ref, language):
            # Not on the first delivery into an existing translation: what it lacks of the
            # source isn't new, the editor chose to keep it as it is.
            merge_structure(ref, source, content, language, values)
    return content


def _latest_delivery(ref, language):
    return DraftDelivery.objects.filter(external_key=ref.key, language=language).first()


def merge_structure(ref, source, content, language, values=()):
    """Carry the source's insertions and moves since the last delivery into the target."""
    add_new_plugins(ref, source, content, language, values)
    delivery = _latest_delivery(ref, language)
    base = merge.from_json(delivery.structure) if delivery else {}
    source_tree = merge.structure(source, get_config().source_language)
    target_tree = merge.structure(content, language)
    merge.apply_moves(content, language, source_tree, merge.to_move(base, source_tree, target_tree))


def _structure_changes(ref, language):
    """`(delivery, needs writing, removed keys)`: whether the source's structure changed since
    the last delivery in a way that is carried over without asking (insertions without texts,
    moves), and which deleted source plugins the translation still has."""
    delivery = _latest_delivery(ref, language)
    if delivery is None or not delivery.structure:
        return delivery, False, []
    source_language = get_config().source_language
    source = ref.published(source_language)
    content = ref.latest(language) if ref.content_type.per_language else ref.latest()
    if source is None or content is None:
        return delivery, False, []
    base = merge.from_json(delivery.structure)
    source_tree = merge.structure(source, source_language)
    target_tree = merge.structure(content, language)
    removed = merge.removed(base, source_tree, target_tree, delivery.removed)
    if merge.to_move(base, source_tree, target_tree):
        return delivery, True, removed
    source_keys = plugin_keys.ensure_keys(source, source_language)
    new = set(source_keys.values()) - set(delivery.plugin_keys)
    for plugins in plugin_keys.plugins_by_slot(source, source_language).values():
        children = _children(plugins)
        for plugin in plugins:
            if source_keys[plugin.pk] in new and not _has_texts(_subtree(plugin, children)):
                return delivery, True, removed
    return delivery, False, removed


def follow_source(ref):
    """The source was published: carry its moves and text-free new plugins into each
    translation's draft now (they bring no texts, so no delivery would), and list deleted
    plugins for confirmation. Translations with an editor's draft wait for the next "Apply"."""
    for language in sorted(
        set(DraftDelivery.objects.filter(external_key=ref.key).values_list("language", flat=True))
    ):
        if is_excluded(ref.key, language):
            continue
        delivery, writing, removed = _structure_changes(ref, language)
        if writing and write(ref, language, {}) is not None:
            continue
        if delivery is not None and removed != delivery.removed:
            delivery.removed = removed
            delivery.save(update_fields=["removed"])


@transaction.atomic
def write(ref, language, values, *, force=False, rebuild=False, remove=False):
    """Write translations; returns the `DraftDelivery`, or None if nothing was written.
    `remove`: also delete the plugins deleted from the source (an editor confirmed)."""
    known = _known_keys(ref, language)
    previous = _latest_delivery(ref, language)
    content = target_content(ref, language, values, force=force, rebuild=rebuild)
    if content is None:
        return None
    unaligned = write_values(ref, content, language, values)
    source = source_content(ref, values)
    source_tree = merge.structure(source, get_config().source_language)
    removed = []
    if previous is not None and not rebuild:
        target_tree = merge.structure(content, language)
        removed = merge.removed(
            merge.from_json(previous.structure), source_tree, target_tree, previous.removed
        )
    if remove and removed:
        _delete_plugins(content, language, removed)
        removed = []
    present = set(plugin_keys.plugins_by_key(content, language))
    if not known:
        # The first delivery accepts the translation's tree as it is: source plugins it lacks
        # count as removed, and only plugins added to the source later are added to it.
        present |= set(source_tree)
    version_id = None
    if ref.content_type.versioned:
        from djangocms_versioning.models import Version

        version_id = Version.objects.get_for_content(content).pk
    delivery = DraftDelivery.objects.create(
        external_key=ref.key,
        language=language,
        content_id=content.pk,
        version_id=version_id,
        texts_hash=texts_hash(ref, content, language),
        unaligned=unaligned,
        plugin_keys=sorted(known | present),
        structure=merge.as_json(source_tree),
        removed=removed,
    )
    # Only the latest delivery per unit and language is needed: it carries the keys of all.
    DraftDelivery.objects.filter(external_key=ref.key, language=language).exclude(
        pk=delivery.pk
    ).delete()
    return delivery


def _delete_plugins(content, language, keys):
    targets = plugin_keys.plugins_by_key(content, language)
    by_placeholder = defaultdict(list)
    for key in keys:
        if key in targets:
            by_placeholder[targets[key].placeholder_id].append(targets[key])
    for placeholder_id, plugins in by_placeholder.items():
        placeholder = Placeholder.objects.get(pk=placeholder_id)
        ids = {p.pk for p in plugins}
        # Descendants go with their ancestor.
        top = [p for p in plugins if not _has_ancestor_in(p, ids)]
        for plugin in top:
            placeholder.delete_plugin(CMSPlugin.objects.get(pk=plugin.pk))


def _has_ancestor_in(plugin, ids):
    parent_id = plugin.parent_id
    while parent_id:
        if parent_id in ids:
            return True
        parent_id = CMSPlugin.objects.filter(pk=parent_id).values_list("parent_id", flat=True)[0]
    return False


def keep_removed(ref, language):
    """An editor keeps the plugins deleted from the source: they become translation-only (out
    of the base too, so the next merge doesn't see them deleted again)."""
    delivery = _latest_delivery(ref, language)
    if delivery is None:
        return
    delivery.structure = {
        key: node for key, node in delivery.structure.items() if key not in delivery.removed
    }
    delivery.removed = []
    delivery.save(update_fields=["structure", "removed"])


def deliver(adapter, results):
    """Store results as suggestions; write each unit × language into its draft."""
    handled, groups = [], defaultdict(list)
    for result in results:
        ref = adapter.get_object(result["unit"])
        if ref is None or is_excluded(result["unit"], result["language"]):
            handled.append(result["id"])
        else:
            groups[(ref.key, result["language"])].append((ref, result))
    for (_key, language), items in groups.items():
        ref = items[0][0]
        with transaction.atomic():
            fresh = [s for s in (_store(adapter, result) for _ref, result in items) if s]
            delivery = None
            if fresh and ref.content_type.versioned:
                delivery = write(ref, language, {s.field: s.text for s in fresh})
                if delivery is not None:
                    # Texts without a place in the draft keep waiting (shown as "waiting").
                    placed = [s.pk for s in fresh if s.field not in delivery.unaligned]
                    Suggestion.objects.filter(pk__in=placed).update(status=SuggestionStatus.DRAFTED)
            if fresh:
                results = [result for _ref, result in items]
                publish_requested(adapter, ref, language, results, delivery)
        handled += [result["id"] for _ref, result in items]
    return handled


def publish_requested(adapter, ref, language, results, delivery):
    """Publish the draft as the editor who asked for it (ponyglot.publishing), if it holds only
    these translations; otherwise it stays a draft and the sync report says why."""
    user, reason = publishing.editor(results)
    if user is None:
        if reason:
            publishing.not_published(ref.key, language, reason)
        return
    if delivery is None:
        reason = _("the draft has an editor's changes, or the translation needs a choice")
    elif not adapter.can_publish(user, ref, language):
        reason = _("the editor may not publish it (any more), or it's shared by all languages")
    elif publishing.others_waiting(ref.key, language, [result["id"] for result in results]):
        reason = _("the draft holds other translations waiting for review")
    if reason:
        publishing.not_published(ref.key, language, reason)
        return
    from djangocms_versioning.models import Version

    version = Version.objects.get(pk=delivery.version_id)
    drafted = Suggestion.objects.filter(
        result_id__in=[result["id"] for result in results], status=SuggestionStatus.DRAFTED
    )
    try:
        with transaction.atomic():
            # Before publishing: publishing a translation reports its drafted texts as approved.
            publishing.mark(drafted, user)
            version.publish(user)
    except Exception as error:  # noqa: BLE001 (e.g. a URL collision; the draft stays)
        publishing.not_published(ref.key, language, str(error) or type(error).__name__)


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
