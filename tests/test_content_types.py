"""Any frontend-editable content type, in both modes (ADR 0001).

- pages and articles: one content object per language, versioned;
- notes: all languages in one content object, versioned (published together);
- boxes: unversioned (translations wait for an editor's "apply", which writes them live).
"""

import pytest
from cms.api import add_plugin
from cms.models import PageContent
from djangocms_versioning import constants
from djangocms_versioning.models import Version
from ponyglot import exclusions, suggestions, sync
from ponyglot.adapters import registry
from ponyglot.models import Suggestion, SyncState

from djangocms_ponyglot import keys as plugin_keys
from djangocms_ponyglot.contenttypes import content_types, parse_key
from djangocms_ponyglot.delivery import texts_hash, write
from djangocms_ponyglot.extract import segments_of
from djangocms_ponyglot.models import DraftDelivery
from tests.testapp.models import ArticleContent, CardContent, NoteContent

from .conftest import placeholder_of, publish

pytestmark = pytest.mark.django_db

adapter = registry.get("djangocms")
GERMAN = {
    "Pricing": "Preise",
    "Pricing explained": "Preise erklärt",
    "Plans for all.": "Tarife für alle.",
    "What Ponyglot costs.": "Was Ponyglot kostet.",
    "Agency": "Agentur",
    "A pony": "Ein Pony",
}


def german(text):
    if "cms-plugin" in text:
        return (
            text.replace("€79 per site", "79 € pro Site")
            .replace("per month", "pro Monat")
            .replace("cancel any time", "jederzeit kündbar")
        )
    return GERMAN.get(text, f"DE {text}")


def translate(cloud, ref, language="de", only=None):
    """Results for every pushed segment of the unit (like a finished job)."""
    unit = cloud.units[ref.key]
    for segment in unit["segments"]:
        if only is None or segment["key"] in only:
            cloud.add_result(
                ref.key,
                segment["key"],
                language,
                german(segment["text"]),
                source=segment["text"],
                format=segment.get("format", "plain"),
            )


def synced(api, cloud, ref):
    sync.backfill()
    sync.run(api)
    translate(cloud, ref)
    return sync.run(api)


def state_of(content):
    return Version.objects.get_for_content(content).state


def texts_in(ref, content, language):
    return {key: s.text for key, s in segments_of(ref, content, language).items()}


def plugin_texts(content, language):
    result = []
    for plugins in plugin_keys.plugins_by_slot(content, language).values():
        for plugin in plugins:
            instance = plugin.get_plugin_instance()[0]
            result.append(
                getattr(instance, "body", None)
                or getattr(instance, "title", None)
                or getattr(instance, "name", "")
            )
    return result


# --- Discovery and declarations ------------------------------------------------------------


def test_content_types_and_modes():
    found = {
        label: (ct.versioned, ct.per_language, ct.grouper_field)
        for label, ct in content_types().items()
    }
    assert found == {
        "cms.pagecontent": (True, True, "page"),
        "testapp.articlecontent": (True, True, "article"),
        "testapp.notecontent": (True, False, "note"),
        "testapp.box": (False, False, None),
        "testapp.cardcontent": (False, True, "card"),
    }


def test_models_can_be_limited(settings):
    settings.PONYGLOT = {**settings.PONYGLOT, "DJANGOCMS": {"MODELS": ["testapp.NoteContent"]}}
    assert list(content_types()) == ["testapp.notecontent"]


def test_djangocms_translations_declarations(page_ref, settings):
    content = page_ref.published("en")
    keys = [key.rsplit(":", 1)[-1] for key in texts_in(page_ref, content, "en")]
    # The link's `name` travels inside the text; `tooltip`/`url` aren't declared.
    assert "name" not in keys and "tooltip" not in keys
    body = next(t for k, t in texts_in(page_ref, content, "en").items() if k.endswith(":body"))
    assert ">per month</cms-plugin>" in body

    # Our own PLUGINS setting wins over DJANGOCMS_TRANSLATIONS_CONF.
    settings.PONYGLOT = {
        **settings.PONYGLOT,
        "DJANGOCMS": {"PLUGINS": {"TeaserPlugin": {"excluded_fields": ["image_alt"]}}},
    }
    assert not any(k.endswith(":image_alt") for k in texts_in(page_ref, content, "en"))


def test_content_fields_are_configurable(article_ref, settings):
    content = article_ref.published("en")
    assert {"title", "lead"} <= set(texts_in(article_ref, content, "en"))
    settings.PONYGLOT = {
        **settings.PONYGLOT,
        "DJANGOCMS": {"CONTENT_FIELDS": {"testapp.ArticleContent": ["title"]}},
    }
    assert "lead" not in texts_in(article_ref, content, "en")


def test_keys_parse_back(page_ref, article_ref, note_ref, box_ref, card_ref):
    for ref in (page_ref, article_ref, note_ref, box_ref, card_ref):
        assert parse_key(ref.key) == ref
    assert parse_key("djangocms:unknown.model:1") is None
    assert parse_key("djangocms:cms.pagecontent:x") is None


# --- One content object per language (pages, articles) -------------------------------------


@pytest.mark.parametrize("fixture", ["page_ref", "article_ref"])
def test_per_language_first_delivery_creates_a_draft(request, api, cloud, fixture):
    ref = request.getfixturevalue(fixture)
    synced(api, cloud, ref)

    draft = ref.latest("de")
    assert state_of(draft) == constants.DRAFT
    assert draft.language == "de"
    assert ref.published("de") is None  # nothing published
    assert draft.title in ("Preise", "Preise erklärt")
    assert draft.slug in ("preise", "preise-erklart")
    assert "Agentur" in plugin_texts(draft, "de")
    body = next(t for t in plugin_texts(draft, "de") if "<p>" in t)
    link = next(p for p in placeholder_of(draft).get_plugins("de") if p.plugin_type == "LinkPlugin")
    assert f'id="{link.pk}"' in body and "79 € pro Site" in body
    assert link.get_plugin_instance()[0].name == "pro Monat"  # the label, from the sentence
    assert plugin_texts(ref.published("en"), "en")[0].startswith("<p>€79")  # source untouched
    assert set(Suggestion.objects.values_list("status", flat=True)) == {"drafted"}


def test_per_language_publish_approves_with_corrections(api, cloud, article_ref, editor):
    synced(api, cloud, article_ref)
    draft = article_ref.latest("de")
    draft.title = "Unsere Preise"
    draft.save()
    publish(draft, editor)
    sync.run(api)
    reviews = {r["id"]: r for r in cloud.reviews}
    title = Suggestion.objects.get(field="title")
    assert reviews[title.result_id]["text"] == "Unsere Preise"
    assert {r["outcome"] for r in reviews.values()} == {"approved"}


def test_edited_draft_waits_and_is_applied_on_request(api, cloud, page_ref, editor):
    synced(api, cloud, page_ref)
    draft = page_ref.latest("de")
    cloud.add_result(page_ref.key, "title", "de", "Preisliste", source="Pricing")
    sync.run(api)
    assert page_ref.latest("de").pk == draft.pk and page_ref.latest("de").title == "Preisliste"

    draft = page_ref.latest("de")
    draft.menu_title = "Kosten"
    draft.save()
    cloud.add_result(page_ref.key, "title", "de", "Tarife", source="Pricing")
    sync.run(api)
    assert page_ref.latest("de").title == "Preisliste"
    waiting = Suggestion.objects.get(status="pending")

    assert suggestions.apply([waiting], editor).applied == 1
    assert page_ref.latest("de").title == "Tarife"
    waiting.refresh_from_db()
    assert waiting.status == "drafted"


def test_published_translation_gets_a_new_draft_keeping_corrections(api, cloud, page_ref, editor):
    synced(api, cloud, page_ref)
    draft = page_ref.latest("de")
    draft.meta_description = "Was es kostet."
    draft.save()
    publish(draft, editor)
    cloud.add_result(page_ref.key, "title", "de", "Tarife", source="Pricing")
    sync.run(api)
    new = page_ref.latest("de")
    assert new.pk != draft.pk and state_of(new) == constants.DRAFT
    assert (new.title, new.meta_description) == ("Tarife", "Was es kostet.")


def test_discard_and_archive_reject(api, cloud, page_ref, article_ref, editor):
    synced(api, cloud, page_ref)
    Version.objects.get_for_content(page_ref.latest("de")).delete()
    translate(cloud, article_ref)
    sync.backfill()
    sync.run(api)
    Version.objects.get_for_content(article_ref.latest("de")).archive(editor)
    sync.run(api)
    assert {r["outcome"] for r in cloud.reviews} == {"rejected"}
    assert len(cloud.reviews) == 10


def test_source_keys_survive_new_versions(api, cloud, article_ref, editor):
    sync.backfill()
    sync.run(api)
    first = {s["key"] for s in cloud.units[article_ref.key]["segments"]}
    draft = Version.objects.get_for_content(article_ref.published("en")).copy(editor).content
    teaser = next(
        p.get_plugin_instance()[0]
        for p in placeholder_of(draft).get_plugins("en")
        if p.plugin_type == "TeaserPlugin"
    )
    teaser.title = "Agency plan"
    teaser.save()
    publish(draft, editor)
    assert SyncState.objects.get(external_key=article_ref.key).dirty
    sync.run(api)
    pushed = {s["key"]: s["text"] for s in cloud.units[article_ref.key]["segments"]}
    assert set(pushed) == first and "Agency plan" in pushed.values()


def test_unpublish_and_delete_remove_units(api, cloud, page, page_ref, article_ref, editor):
    sync.backfill()
    sync.run(api)
    Version.objects.get_for_content(article_ref.published("en")).unpublish(editor)
    page.delete()
    sync.run(api)
    assert set(cloud.deleted) == {page_ref.key, article_ref.key}


# --- All languages in one content object (notes) -------------------------------------------


def test_shared_content_gets_target_plugins_in_the_same_draft(api, cloud, note_ref):
    synced(api, cloud, note_ref)

    draft = note_ref.latest()
    assert state_of(draft) == constants.DRAFT
    assert NoteContent.admin_manager.filter(note_id=note_ref.grouper_id).count() == 2
    assert "Agentur" in plugin_texts(draft, "de")
    assert "Agency" in plugin_texts(draft, "en")  # the source language stays
    assert not any(k in ("name",) for k in cloud.units[note_ref.key]["segments"])
    assert set(plugin_keys.plugins_by_key(draft, "de")) == set(
        plugin_keys.plugins_by_key(draft, "en")
    )


def test_shared_content_publishes_all_languages_together(api, cloud, note_ref, editor):
    synced(api, cloud, note_ref)
    translate(cloud, note_ref, language="fr")
    sync.run(api)
    draft = note_ref.latest()
    assert plugin_texts(draft, "fr")  # same draft, another language

    publish(draft, editor)
    sync.run(api)
    assert len(cloud.reviews) == 6  # 3 segments × de, fr
    assert {r["outcome"] for r in cloud.reviews} == {"approved"}
    # Publishing shared content may change the source too: it's pushed again (unchanged).
    assert SyncState.objects.get(external_key=note_ref.key).dirty is False


def test_shared_content_waits_when_a_language_was_edited(api, cloud, note_ref):
    synced(api, cloud, note_ref)
    draft = note_ref.latest()
    teaser = next(
        p.get_plugin_instance()[0]
        for p in placeholder_of(draft).get_plugins("de")
        if p.plugin_type == "TeaserPlugin"
    )
    teaser.title = "Agentur (bearbeitet)"
    teaser.save()
    key = next(k for k in texts_in(note_ref, draft, "de") if k.endswith(":title"))
    cloud.add_result(note_ref.key, key, "de", "Agenturen", source="Agency")
    sync.run(api)
    assert "Agentur (bearbeitet)" in plugin_texts(note_ref.latest(), "de")
    assert Suggestion.objects.get(status="pending").text == "Agenturen"


# --- Unversioned content (boxes) -----------------------------------------------------------


def test_unversioned_content_waits_for_apply(api, cloud, box_ref, editor):
    synced(api, cloud, box_ref)
    box = box_ref.published()
    assert plugin_texts(box, "de") == []  # nothing written automatically
    pending = list(Suggestion.objects.filter(status="pending"))
    assert len(pending) == 3

    assert suggestions.apply(pending, editor).applied == 3
    assert "Agentur" in plugin_texts(box, "de")
    assert set(Suggestion.objects.values_list("status", flat=True)) == {"applied"}
    sync.run(api)
    assert {r["outcome"] for r in cloud.reviews} == {"approved"}


def test_unversioned_source_changes_are_noticed(api, cloud, box_ref):
    sync.backfill()
    sync.run(api)
    box = box_ref.published()
    add_plugin(placeholder_of(box), "TeaserPlugin", "en", title="Enterprise")
    assert SyncState.objects.get(external_key=box_ref.key).dirty
    sync.run(api)
    assert len(cloud.units[box_ref.key]["segments"]) == 4  # + the new teaser's title
    box.delete()
    sync.run(api)
    assert cloud.deleted == [box_ref.key]


def test_unversioned_per_language_waits_and_apply_creates_the_language(
    api, cloud, card_ref, editor
):
    synced(api, cloud, card_ref)
    assert not CardContent.objects.filter(language="de").exists()  # nothing written
    pending = list(Suggestion.objects.filter(status="pending"))
    assert {s.field for s in pending} >= {"title"} and len(pending) == 4

    assert suggestions.apply(pending, editor).applied == 4

    german = CardContent.objects.get(card_id=card_ref.grouper_id, language="de")
    assert (german.title, german.slug) == ("DE Pricing card", "de-pricing-card")
    assert "Agentur" in plugin_texts(german, "de")
    link = next(
        p for p in placeholder_of(german).get_plugins("de") if p.plugin_type == "LinkPlugin"
    )
    assert link.get_plugin_instance()[0].name == "pro Monat"
    assert plugin_texts(card_ref.published("en"), "en")[0].startswith("<p>€79")  # source intact
    assert set(Suggestion.objects.values_list("status", flat=True)) == {"applied"}
    sync.run(api)
    assert len(cloud.reviews) == 4 and {r["outcome"] for r in cloud.reviews} == {"approved"}

    # A later translation is applied to the existing German content, live.
    cloud.add_result(card_ref.key, "title", "de", "Preiskarte", source="Pricing card")
    sync.run(api)
    suggestions.apply(Suggestion.objects.filter(status="pending"), editor)
    german.refresh_from_db()
    assert german.title == "Preiskarte"
    assert CardContent.objects.filter(language="de").count() == 1


def test_unversioned_per_language_only_source_changes_count(api, cloud, card_ref, editor):
    sync.backfill()
    sync.run(api)
    state = SyncState.objects.get(external_key=card_ref.key)
    assert not state.dirty
    german = CardContent.objects.create(card_id=card_ref.grouper_id, language="de", title="Karte")
    add_plugin(placeholder_of(german), "TeaserPlugin", "de", title="Agentur")
    state.refresh_from_db()
    assert not state.dirty  # target-language edits don't touch the source

    source = card_ref.published("en")
    source.title = "Pricing card (new)"
    source.save()
    state.refresh_from_db()
    assert state.dirty
    sync.run(api)
    assert cloud.units[card_ref.key]["segments"][0]["text"] == "Pricing card (new)"

    add_plugin(placeholder_of(source), "TeaserPlugin", "en", title="Enterprise")
    assert SyncState.objects.get(external_key=card_ref.key).dirty
    sync.run(api)

    card_ref.grouper.delete()  # the grouper: the unit is removed
    sync.run(api)
    assert cloud.deleted == [card_ref.key]


# --- Structure, exclusion, existing translations -------------------------------------------


def test_unaligned_segments_and_rebuilding_the_tree(api, cloud, article_ref):
    synced(api, cloud, article_ref)
    draft = article_ref.latest("de")
    teaser = next(
        p for p in placeholder_of(draft).get_plugins("de") if p.plugin_type == "TeaserPlugin"
    )
    teaser.delete()
    DraftDelivery.objects.update(texts_hash=texts_hash(article_ref, draft, "de"))
    alt = next(
        s["key"] for s in cloud.units[article_ref.key]["segments"] if s["key"].endswith("image_alt")
    )
    translate(cloud, article_ref, only=[alt])
    sync.run(api)
    assert DraftDelivery.objects.first().unaligned == [alt]

    live = Suggestion.objects.filter(status__in=["pending", "drafted"])
    delivery = write(article_ref, "de", {s.field: s.text for s in live}, force=True, rebuild=True)
    assert delivery.unaligned == []
    assert "Ein Pony" in texts_in(article_ref, article_ref.latest("de"), "de").values()


def test_exclusion(api, cloud, article_ref, editor):
    exclusions.exclude(adapter, article_ref, ["de"], user=editor)
    sync.run(api)
    assert cloud.units[article_ref.key]["writable_languages"] == ["fr"]
    translate(cloud, article_ref)
    sync.run(api)
    assert article_ref.latest("de") is None


def test_existing_translations_are_imported(api, cloud, page, page_ref, editor):
    from cms.api import create_page_content
    from cms.utils.plugins import copy_plugins_to_placeholder

    source = PageContent.objects.get(page=page, language="en")
    target = create_page_content("de", "Preise", page, created_by=editor)
    copy_plugins_to_placeholder(
        list(placeholder_of(source).get_plugins("en").order_by("position")),
        placeholder_of(target),
        language="de",
    )
    for plugin in placeholder_of(target).get_plugins("de"):
        instance = plugin.get_plugin_instance()[0]
        if plugin.plugin_type == "TeaserPlugin":
            instance.title = "Agentur"
            instance.save()
    publish(target, editor)
    sync.backfill()
    sync.run(api)
    imported = {
        s["key"]: (s.get("translations") or {}).get("de")
        for s in cloud.units[page_ref.key]["segments"]
    }
    assert imported["title"] == "Preise" and "Agentur" in imported.values()


def test_handshake_capabilities(api, cloud, page_ref):
    sync.run(api)
    assert cloud.handshakes[0]["capabilities"] == ["qa_errors", "whole_unit"]


def test_article_contents_use_the_versioning_copy(api, cloud, article_ref):
    synced(api, cloud, article_ref)
    assert ArticleContent.admin_manager.filter(language="de").count() == 1
