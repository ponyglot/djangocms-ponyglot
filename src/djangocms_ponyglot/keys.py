"""Stable plugin keys across versions and languages.

djangocms-versioning copies every plugin when it creates a version, so plugin ids change on
every edit. Segments use a key instead:

- a new plugin gets a fresh key when first seen (`ensure_keys`),
- a copied version inherits its original's keys (`inherit_keys` on draft creation),
- a target-language plugin shares the key of the source plugin it translates: it was either
  copied from it by the connector, or matched by structure when content was already translated.

A plugin tree is identified by a content object and a language: per-language content types
have one language per object, shared ones hold all languages in the same placeholders.
Structure means: same placeholder slot, same plugin type, same position in the tree.
"""

import uuid
from collections import defaultdict

from cms.models import Placeholder

from .models import PluginKey


def placeholders(content):
    return list(Placeholder.objects.get_for_obj(content))


def plugins_by_slot(content, language):
    """`{slot: [plugins in tree order]}` (plain CMSPlugin instances)."""
    return {
        placeholder.slot: list(
            placeholder.get_plugins(language=language).order_by("position", "pk")
        )
        for placeholder in placeholders(content)
    }


def signatures(content, language):
    """`{plugin id: (slot, plugin type, path)}`; path = sibling indexes from the root."""
    result = {}
    for slot, plugins in plugins_by_slot(content, language).items():
        children = defaultdict(list)
        for plugin in plugins:
            children[plugin.parent_id].append(plugin)

        def walk(parent_id, prefix, slot=slot, children=children):
            for index, plugin in enumerate(children.get(parent_id, [])):
                path = (*prefix, index)
                result[plugin.pk] = (slot, plugin.plugin_type, path)
                walk(plugin.pk, path)

        walk(None, ())
    return result


def keys_for(plugin_ids):
    return dict(
        PluginKey.objects.filter(plugin_id__in=list(plugin_ids)).values_list("plugin_id", "key")
    )


def _assign(pairs):
    PluginKey.objects.bulk_create(
        [PluginKey(plugin_id=plugin_id, key=key) for plugin_id, key in pairs],
        ignore_conflicts=True,
    )


def inherit_keys(original, original_language, copy, copy_language):
    """Give plugins of (`copy`, `copy_language`) without a key the key of their structural
    counterpart in (`original`, `original_language`)."""
    original_signatures = signatures(original, original_language)
    original_keys = keys_for(original_signatures)
    by_signature = {
        signature: original_keys[plugin_id]
        for plugin_id, signature in original_signatures.items()
        if plugin_id in original_keys
    }
    copy_signatures = signatures(copy, copy_language)
    existing = keys_for(copy_signatures)
    taken = set(existing.values())
    pairs = []
    for plugin_id, signature in copy_signatures.items():
        key = by_signature.get(signature)
        if plugin_id not in existing and key and key not in taken:
            pairs.append((plugin_id, key))
            taken.add(key)
    _assign(pairs)
    return len(pairs)


def ensure_keys(content, language):
    """Keys for all plugins of the tree, creating missing ones. `{plugin id: key}`."""
    plugin_ids = list(signatures(content, language))
    keys = keys_for(plugin_ids)
    missing = [
        (plugin_id, uuid.uuid4().hex[:16]) for plugin_id in plugin_ids if plugin_id not in keys
    ]
    _assign(missing)
    keys.update(missing)
    return keys


def plugins_by_key(content, language):
    """`{key: plugin}` for the plugins of the tree that have a key."""
    plugins = {p.pk: p for group in plugins_by_slot(content, language).values() for p in group}
    return {key: plugins[plugin_id] for plugin_id, key in keys_for(plugins).items()}
