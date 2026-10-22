"""Three-way merge of plugin trees (merge.py): the source's insertions and moves are carried into
translations, deletions wait for an editor's confirmation."""

import pytest
from cms.api import add_plugin
from djangocms_versioning import constants
from djangocms_versioning.models import Version
from ponyglot import sync

from djangocms_ponyglot import keys as plugin_keys
from djangocms_ponyglot import merge
from djangocms_ponyglot.merge import Node
from djangocms_ponyglot.models import DraftDelivery

from .conftest import placeholder_of, publish
from .test_content_types import plugin_texts, state_of, synced, translate

pytestmark = pytest.mark.django_db


# --- The diff -------------------------------------------------------------------------------


def tree(*rows):
    """`tree(("a", None), ("b", None), ("c", "a"))`: keys with parents, in order."""
    counts, result = {}, {}
    for key, parent, *slot in rows:
        slot = slot[0] if slot else "content"
        index = counts.get((slot, parent), 0)
        counts[(slot, parent)] = index + 1
        result[key] = Node(slot, parent, index)
    return result


def test_insertions_dont_count_as_moves():
    base = tree(("a", None), ("b", None), ("c", None))
    assert merge.moved(base, tree(("new", None), ("a", None), ("b", None), ("c", None))) == set()


def test_one_moved_sibling_is_one_move():
    base = tree(("a", None), ("b", None), ("c", None), ("d", None))
    assert merge.moved(base, tree(("b", None), ("c", None), ("d", None), ("a", None))) == {"a"}


def test_other_parent_or_placeholder_is_a_move():
    base = tree(("a", None), ("b", None), ("c", "a"))
    assert merge.moved(base, tree(("a", None), ("b", None), ("c", "b"))) == {"c"}
    assert merge.moved(base, tree(("a", None), ("b", None), ("c", None, "sidebar"))) == {"c"}


def test_the_editors_move_wins():
    base = tree(("a", None), ("b", None), ("c", None))
    source = tree(("c", None), ("a", None), ("b", None))  # c to the front
    assert merge.to_move(base, source, base) == ["c"]
    target = tree(("a", None), ("c", None), ("b", None))  # the editor moved c already
    assert merge.to_move(base, source, target) == []
    assert merge.to_move({}, source, base) == []  # no base (older deliveries): no moves


def test_deletions_need_the_target_to_have_them():
    base = tree(("a", None), ("b", None))
    source = tree(("a", None))
    assert merge.removed(base, source, base) == ["b"]
    assert merge.removed(base, source, tree(("a", None))) == []  # the editor removed it too
    assert merge.removed(source, source, base, pending=["b"]) == ["b"]  # still waiting


# --- In content -----------------------------------------------------------------------------


def english_draft(ref, editor):
    return Version.objects.get_for_content(ref.published("en")).copy(editor).content


def by_type(content, language, plugin_type):
    return [
        p for p in placeholder_of(content).get_plugins(language) if p.plugin_type == plugin_type
    ]


def root_types(content, language):
    return [
        p.plugin_type
        for p in placeholder_of(content).get_plugins(language).order_by("position")
        if p.parent_id is None
    ]


def test_a_move_in_the_source_moves_the_translation(api, cloud, article_ref, editor):
    synced(api, cloud, article_ref)
    publish(article_ref.latest("de"), editor)
    english = english_draft(article_ref, editor)
    placeholder = placeholder_of(english)
    placeholder.move_plugin(by_type(english, "en", "TeaserPlugin")[0], 1)  # teaser first
    assert root_types(english, "en") == ["TeaserPlugin", "TextPlugin"]
    publish(english, editor)

    draft = article_ref.latest("de")  # a new German draft, without a new translation
    assert state_of(draft) == constants.DRAFT
    assert root_types(draft, "de") == ["TeaserPlugin", "TextPlugin"]
    assert "Agentur" in plugin_texts(draft, "de")
    link = by_type(draft, "de", "DeclaredLinkPlugin")[0]
    assert link.parent_id == by_type(draft, "de", "TextPlugin")[0].pk  # children move along
    positions = sorted(p.position for p in placeholder_of(draft).get_plugins("de"))
    assert positions == list(range(1, len(positions) + 1))


def test_no_structural_change_makes_no_draft(api, cloud, article_ref, editor):
    synced(api, cloud, article_ref)
    published = article_ref.latest("de")
    publish(published, editor)
    english = english_draft(article_ref, editor)
    teaser = by_type(english, "en", "TeaserPlugin")[0].get_plugin_instance()[0]
    teaser.title = "Agency plans"  # a text change: comes with the next delivery
    teaser.save()
    publish(english, editor)
    assert article_ref.latest("de").pk == published.pk


def test_new_plugins_without_texts_come_right_away_and_plugins_move_into_them(
    api, cloud, article_ref, editor
):
    synced(api, cloud, article_ref)
    english = english_draft(article_ref, editor)
    box = add_plugin(placeholder_of(english), "TeaserPlugin", "en", title="", style="b")
    publish(english, editor)  # an empty box: no texts to wait for

    draft = article_ref.latest("de")
    assert root_types(draft, "de") == ["TextPlugin", "TeaserPlugin", "TeaserPlugin"]
    assert plugin_texts(draft, "de")[-1] == ""

    english = english_draft(article_ref, editor)
    box = by_type(english, "en", "TeaserPlugin")[-1]
    teaser = by_type(english, "en", "TeaserPlugin")[0]
    placeholder_of(english).move_plugin(teaser, box.position, target_plugin=box)
    publish(english, editor)

    draft = article_ref.latest("de")
    teasers = {p.get_plugin_instance()[0].title: p for p in by_type(draft, "de", "TeaserPlugin")}
    assert teasers["Agentur"].parent_id == teasers[""].pk


def test_new_plugins_with_texts_wait_for_their_translation(api, cloud, article_ref, editor):
    synced(api, cloud, article_ref)
    publish(article_ref.latest("de"), editor)
    english = english_draft(article_ref, editor)
    add_plugin(placeholder_of(english), "TeaserPlugin", "en", title="Enterprise", style="b")
    publish(english, editor)
    assert "Enterprise" not in plugin_texts(article_ref.latest("de"), "de")  # no English text

    sync.run(api)
    translate(cloud, article_ref)
    sync.run(api)
    assert "DE Enterprise" in plugin_texts(article_ref.latest("de"), "de")


@pytest.fixture
def deleted_teaser(api, cloud, article_ref, editor):
    """German translated and published, then the teaser deleted from English."""
    synced(api, cloud, article_ref)
    publish(article_ref.latest("de"), editor)
    english = english_draft(article_ref, editor)
    teaser = by_type(english, "en", "TeaserPlugin")[0]
    key = plugin_keys.keys_for([teaser.pk])[teaser.pk]
    placeholder_of(english).delete_plugin(teaser)
    publish(english, editor)
    return key


def test_a_deletion_in_the_source_waits_for_confirmation(article_ref, deleted_teaser):
    delivery = DraftDelivery.objects.get(language="de")
    assert delivery.removed == [deleted_teaser]
    assert "Agentur" in plugin_texts(article_ref.latest("de"), "de")  # still there
    assert state_of(article_ref.latest("de")) == constants.PUBLISHED  # no draft for it


def test_confirming_a_deletion(client, editor, article_ref, deleted_teaser):
    client.force_login(editor)
    url = f"/en/admin/djangocms_ponyglot/draftdelivery/unit/{article_ref.key}/"
    html = client.get(url + "panel/").content.decode()
    assert "1 plugin was deleted from English" in html and "Delete here too" in html

    client.post(url + "de/removed/", {"panel": "1"})
    draft = article_ref.latest("de")
    assert state_of(draft) == constants.DRAFT
    assert "Agentur" not in plugin_texts(draft, "de")
    assert "Agentur" in plugin_texts(article_ref.published("de"), "de")  # until published
    assert DraftDelivery.objects.get(language="de").removed == []
    assert "deleted from English" not in client.get(url + "panel/").content.decode()


def test_keeping_a_deleted_plugin(client, editor, api, cloud, article_ref, deleted_teaser):
    client.force_login(editor)
    url = f"/en/admin/djangocms_ponyglot/draftdelivery/unit/{article_ref.key}/"
    client.post(url + "de/removed/", {"keep": "1", "panel": "1"})
    assert DraftDelivery.objects.get(language="de").removed == []
    # Later deliveries don't bring the question back, and don't delete it.
    english = english_draft(article_ref, editor)
    text = by_type(english, "en", "TextPlugin")[0].get_plugin_instance()[0]
    text.body = text.body.replace("cancel any time", "no strings")
    text.save()
    publish(english, editor)
    sync.run(api)
    translate(cloud, article_ref)
    sync.run(api)
    assert DraftDelivery.objects.get(language="de").removed == []
    assert "Agentur" in plugin_texts(article_ref.latest("de"), "de")


def test_an_edited_translation_draft_isnt_touched(api, cloud, article_ref, editor):
    synced(api, cloud, article_ref)
    draft = article_ref.latest("de")
    teaser = by_type(draft, "de", "TeaserPlugin")[0].get_plugin_instance()[0]
    teaser.title = "Agentur (geprüft)"  # an editor's correction
    teaser.save()
    english = english_draft(article_ref, editor)
    placeholder_of(english).move_plugin(by_type(english, "en", "TeaserPlugin")[0], 1)
    text = by_type(english, "en", "TextPlugin")[0]
    placeholder_of(english).delete_plugin(text)
    publish(english, editor)

    assert article_ref.latest("de").pk == draft.pk
    assert root_types(draft, "de") == ["TextPlugin", "TeaserPlugin"]  # not moved: waits
    assert len(DraftDelivery.objects.get(language="de").removed) == 2  # text and its link


def test_shared_content_merges_with_the_next_delivery(api, cloud, note_ref, editor):
    synced(api, cloud, note_ref)
    publish(note_ref.latest(), editor)  # all languages
    english = Version.objects.get_for_content(note_ref.published()).copy(editor).content
    placeholder_of(english).move_plugin(by_type(english, "en", "TeaserPlugin")[0], 1)
    teaser = by_type(english, "en", "TeaserPlugin")[0].get_plugin_instance()[0]
    teaser.title = "Agency plans"
    teaser.save()
    publish(english, editor)
    assert root_types(note_ref.latest(), "de") == ["TextPlugin", "TeaserPlugin"]  # not yet

    sync.run(api)
    translate(cloud, note_ref)
    sync.run(api)
    draft = note_ref.latest()
    assert root_types(draft, "de") == ["TeaserPlugin", "TextPlugin"]
    assert root_types(draft, "en") == ["TeaserPlugin", "TextPlugin"]
    assert "DE Agency plans" in plugin_texts(draft, "de")
