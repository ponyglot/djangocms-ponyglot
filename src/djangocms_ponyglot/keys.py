"""Stable plugin keys across versions and languages.

djangocms-versioning copies every plugin when it creates a version, so plugin ids change on
every edit. Segments use a key instead:

- a new plugin gets a fresh key when first seen (`ensure_keys`),
- a copied version inherits its original's keys (`inherit_keys` on draft creation),
- a target-language plugin shares the key of the source plugin it translates: it was either
  copied from it by the connector, or matched by structure when a site already had translated
  pages (`inherit_keys` from the source content).

Structure means: same placeholder slot, same plugin type, same position in the plugin tree.
"""

import uuid
from collections import defaultdict

from .models import PluginKey


def plugins_by_slot(content):
    """`{slot: [plugins in tree order]}` for a page content (plain CMSPlugin instances)."""
    result = {}
    for placeholder in content.get_placeholders():
        result[placeholder.slot] = list(
            placeholder.get_plugins(language=content.language).order_by("position", "pk")
        )
    return result


def signatures(content):
    """`{plugin id: (slot, plugin type, path)}`; path = sibling indexes from the root."""
    result = {}
    for slot, plugins in plugins_by_slot(content).items():
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


def inherit_keys(original, copy):
    """Give plugins of `copy` without a key the key of their structural counterpart in
    `original` (a version it was copied from, or the source-language content)."""
    by_signature = {}
    original_keys = keys_for(sig for sig in signatures(original))
    for plugin_id, signature in signatures(original).items():
        if plugin_id in original_keys:
            by_signature[signature] = original_keys[plugin_id]
    copy_signatures = signatures(copy)
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


def ensure_keys(content):
    """Keys for all plugins of `content`, creating missing ones. `{plugin id: key}`."""
    plugin_ids = list(signatures(content))
    keys = keys_for(plugin_ids)
    missing = [
        (plugin_id, uuid.uuid4().hex[:16]) for plugin_id in plugin_ids if plugin_id not in keys
    ]
    _assign(missing)
    keys.update(missing)
    return keys


def plugins_by_key(content):
    """`{key: plugin}` for the plugins of `content` that have a key."""
    plugins = {p.pk: p for group in plugins_by_slot(content).values() for p in group}
    return {key: plugins[plugin_id] for plugin_id, key in keys_for(plugins).items()}
