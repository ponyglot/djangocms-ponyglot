"""Three-way merge of plugin trees: carry the source's structural changes into a translation.

Three trees, identified by plugin keys (keys.py): the **base** (the source tree at the last
delivery, stored with it), the **source** now, and the **target** (the translation) now.
Comparing base → source gives what the source changed; base → target what an editor changed in
the translation. The source's changes are carried over unless the editor changed the same thing:

- **inserted** in the source: copied into the target (delivery.add_new_plugins);
- **moved** in the source (another parent, or another order among its siblings): moved in the
  target too, unless the editor moved it there as well (the editor's place wins);
- **deleted** in the source: listed for an editor's confirmation (`removed`), never deleted
  automatically; "keep" makes them target-only plugins;
- removed or added by an editor in the target: left as they are.

A plugin counts as moved when its parent changed, or when it is not part of the longest common
order of its siblings (so one insertion or one moved sibling doesn't make every sibling look
moved).
"""

from collections import defaultdict
from typing import NamedTuple

from cms.models import Placeholder

from . import keys as plugin_keys


class Node(NamedTuple):
    slot: str
    parent: str | None  # parent plugin key, None at the placeholder's root
    index: int  # among its siblings


def structure(content, language):
    """`{key: Node}` of a plugin tree (keys are created for plugins without one)."""
    keys = plugin_keys.ensure_keys(content, language)
    result = {}
    for slot, plugins in plugin_keys.plugins_by_slot(content, language).items():
        counts = defaultdict(int)
        for plugin in plugins:  # tree order
            parent = keys.get(plugin.parent_id)
            result[keys[plugin.pk]] = Node(slot, parent, counts[plugin.parent_id])
            counts[plugin.parent_id] += 1
    return result


def as_json(tree):
    return {key: list(node) for key, node in tree.items()}


def from_json(data):
    return {key: Node(*node) for key, node in (data or {}).items()}


def _lcs(a, b):
    """Longest common subsequence of two key sequences."""
    lengths = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            lengths[i + 1][j + 1] = (
                lengths[i][j] + 1 if x == y else max(lengths[i][j + 1], lengths[i + 1][j])
            )
    result, i, j = set(), len(a), len(b)
    while i and j:
        if a[i - 1] == b[j - 1]:
            result.add(a[i - 1])
            i, j = i - 1, j - 1
        elif lengths[i - 1][j] >= lengths[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return result


def moved(base, other):
    """Keys whose place changed from `base` to `other` (both `{key: Node}`)."""
    common = base.keys() & other.keys()
    result = {
        key
        for key in common
        if (base[key].slot, base[key].parent) != (other[key].slot, other[key].parent)
    }
    siblings = defaultdict(list)
    for key in common - result:
        siblings[(base[key].slot, base[key].parent)].append(key)
    for group in siblings.values():
        before = sorted(group, key=lambda key: base[key].index)
        after = sorted(group, key=lambda key: other[key].index)
        result |= set(group) - _lcs(before, after)
    return result


def to_move(base, source, target):
    """Keys to move in the target, in source tree order: moved in the source, not by the
    editor."""
    if not base:
        return []
    wanted = (moved(base, source) - moved(base, target)) & target.keys()
    return [key for key in source if key in wanted]


def removed(base, source, target, pending=()):
    """Keys deleted from the source (since the base, or still pending) that the target has."""
    return sorted(((base.keys() - source.keys()) | set(pending)) & target.keys() - source.keys())


def _subtree_end(plugin, plugins):
    """The position of the last plugin of `plugin`'s subtree."""
    children = defaultdict(list)
    for other in plugins:
        children[other.parent_id].append(other)
    end, stack = plugin.position, [plugin]
    while stack:
        current = stack.pop()
        end = max(end, current.position)
        stack += children.get(current.pk, [])
    return end


def _placeholder(content, slot):
    existing = Placeholder.objects.get_for_obj(content).filter(slot=slot).first()
    return existing or Placeholder.objects.create(slot=slot, source=content)


def apply_moves(content, language, source, keys):
    """Move the target plugins of `keys` to their place in `source` (`{key: Node}`): under the
    counterpart of their source parent, after the nearest preceding source sibling the target
    has. Plugins whose new parent the target lacks stay. Returns the number moved."""
    count = 0
    for key in keys:
        targets = plugin_keys.plugins_by_key(content, language)
        plugin = targets.get(key)
        node = source[key]
        parent = targets.get(node.parent) if node.parent else None
        if plugin is None or (node.parent and parent is None):
            continue
        placeholder = _placeholder(content, node.slot)
        plugins = list(placeholder.get_plugins(language=language))
        preceding = [
            targets[other]
            for other, other_node in source.items()
            if other != key
            and other in targets
            and other_node.slot == node.slot
            and other_node.parent == node.parent
            and other_node.index < node.index
        ]
        if preceding:
            anchor = max(_subtree_end(p, plugins) for p in preceding)
        else:
            anchor = parent.position if parent is not None else 0
        if plugin.placeholder_id != placeholder.pk:
            placeholder_from = Placeholder.objects.get(pk=plugin.placeholder_id)
            placeholder_from.move_plugin(
                plugin, anchor + 1, target_placeholder=placeholder, target_plugin=parent
            )
        else:
            size = _subtree_end(plugin, plugins) - plugin.position  # descendants
            position = anchor + 1 if anchor < plugin.position else anchor - size
            placeholder.move_plugin(plugin, position, target_plugin=parent)
        count += 1
    return count
