"""The editor workflow against a fake cloud (ADR 0001): sync, drafts, approval, rejection."""

import pytest
from cms.api import add_plugin, create_page_content
from cms.models import PageContent
from djangocms_versioning import constants
from djangocms_versioning.models import Version
from ponyglot import exclusions, suggestions, sync
from ponyglot.adapters import registry
from ponyglot.models import Suggestion, SyncState

from djangocms_ponyglot import keys as plugin_keys
from djangocms_ponyglot.adapter import latest_content, segments_of
from djangocms_ponyglot.delivery import texts_hash, write_into_draft
from djangocms_ponyglot.models import DraftDelivery

from .conftest import placeholder_of, publish

pytestmark = pytest.mark.django_db

adapter = registry.get("djangocms")
GERMAN = {
    "Pricing": "Preise",
    "What Ponyglot costs.": "Was Ponyglot kostet.",
    "Pro plan": "Pro-Tarif",
    "Agency": "Agentur",
    "A pony": "Ein Pony",
}


def german(text):
    if text.startswith("<p>€79"):
        return text.replace("€79 per site and", "79 € pro Site und").replace("month", "Monat")
    return GERMAN.get(text, f"DE {text}")


def translate(cloud, page, language="de", only=None, qa=None):
    """Results for every pushed segment of the page (like a finished job)."""
    unit = cloud.units[adapter.external_key(page)]
    for segment in unit["segments"]:
        if only and segment["key"] not in only:
            continue
        cloud.add_result(
            unit["external_key"],
            segment["key"],
            language,
            german(segment["text"]),
            source=segment["text"],
            format=segment.get("format", "plain"),
            qa=qa,
        )


def de_draft(page):
    content = latest_content(page, "de")
    assert Version.objects.get_for_content(content).state == constants.DRAFT
    return content


def texts(content):
    return {key: segment.text for key, segment in segments_of(content).items()}


# --- Push ----------------------------------------------------------------------------------


def test_handshake_declares_capabilities(api, cloud, page):
    sync.run(api)
    assert cloud.handshakes[0]["capabilities"] == ["qa_errors", "whole_unit"]


def test_keys_survive_new_versions(api, cloud, page, editor):
    sync.backfill()
    sync.run(api)
    first = {s["key"] for s in cloud.pushes[-1]["segments"]}

    # The editor changes the teaser in a new version and publishes it.
    draft = Version.objects.get_for_content(PageContent.objects.get(page=page)).copy(editor)
    teaser = next(
        p.get_plugin_instance()[0]
        for p in placeholder_of(draft.content).get_plugins("en")
        if p.plugin_type == "TeaserPlugin" and p.parent_id is None
    )
    teaser.title = "Agency plan"
    teaser.save()
    publish(draft.content, editor)

    assert SyncState.objects.get(external_key=adapter.external_key(page)).dirty
    sync.run(api)
    pushed = {s["key"]: s["text"] for s in cloud.pushes[-1]["segments"]}
    assert set(pushed) == first  # same keys: only the changed text becomes stale
    assert "Agency plan" in pushed.values()


def test_existing_translations_are_imported_by_structure(api, cloud, page, editor):
    source = PageContent.objects.get(page=page, language="en")
    target = create_page_content("de", "Preise", page, created_by=editor)
    from cms.utils.plugins import copy_plugins_to_placeholder

    copy_plugins_to_placeholder(
        list(placeholder_of(source).get_plugins("en").order_by("position")),
        placeholder_of(target),
        language="de",
    )
    for plugin in placeholder_of(target).get_plugins("de"):
        bound = plugin.get_plugin_instance()[0]
        if bound.plugin_type == "TeaserPlugin" and bound.title == "Agency":
            bound.title = "Agentur"
            bound.save()
    publish(target, editor)

    sync.backfill()
    sync.run(api)

    translations = {
        s["key"]: s.get("translations", {}).get("de") for s in cloud.pushes[-1]["segments"]
    }
    assert translations["title"] == "Preise"
    assert "Agentur" in translations.values()


# --- Delivery ------------------------------------------------------------------------------


def test_first_delivery_creates_a_german_draft(api, cloud, page):
    sync.backfill()
    sync.run(api)
    translate(cloud, page)

    report = sync.run(api)

    assert report.delivered == 6
    draft = de_draft(page)
    assert (draft.title, draft.meta_description, draft.slug) == (
        "Preise",
        "Was Ponyglot kostet.",
        "preise",
    )
    assert PageContent.objects.filter(page=page, language="de").count() == 0  # not published
    body = next(
        p.get_plugin_instance()[0]
        for p in placeholder_of(draft).get_plugins("de")
        if p.plugin_type == "TextPlugin"
    )
    inline = next(p for p in body.get_children())  # the embedded teaser, copied
    assert f'id="{inline.pk}"' in body.body and "79 € pro Site und" in body.body
    assert inline.get_plugin_instance()[0].title == "Pro-Tarif"
    assert set(Suggestion.objects.values_list("status", flat=True)) == {"drafted"}
    # The same keys as the source: the next delivery finds its plugins.
    source_keys = set(plugin_keys.plugins_by_key(PageContent.objects.get(page=page)))
    assert set(plugin_keys.plugins_by_key(draft)) == source_keys


def test_untouched_draft_is_updated_edited_draft_waits(api, cloud, page, editor):
    sync.backfill()
    sync.run(api)
    translate(cloud, page)
    sync.run(api)
    draft = de_draft(page)

    # A newer translation of the title: the untouched draft is updated in place.
    cloud.add_result(adapter.external_key(page), "title", "de", "Preisliste", source="Pricing")
    sync.run(api)
    assert de_draft(page).pk == draft.pk and de_draft(page).title == "Preisliste"

    # An editor changes the draft: the next translation waits.
    draft = de_draft(page)
    draft.menu_title = "Kosten"
    draft.save()
    cloud.add_result(adapter.external_key(page), "title", "de", "Tarife", source="Pricing")
    sync.run(api)
    assert de_draft(page).title == "Preisliste"
    waiting = Suggestion.objects.get(status="pending")
    assert waiting.text == "Tarife"

    # "Apply to current draft" (the core's suggestion service, as in the admin/toolbar).
    result = suggestions.apply([waiting], editor)
    assert result.applied == 1
    assert de_draft(page).title == "Tarife"
    waiting.refresh_from_db()
    assert waiting.status == "drafted"


def test_publishing_approves_with_the_editors_corrections(api, cloud, page, editor):
    sync.backfill()
    sync.run(api)
    translate(cloud, page)
    sync.run(api)
    draft = de_draft(page)
    draft.title = "Unsere Preise"
    draft.save()

    publish(draft, editor)
    sync.run(api)

    reviews = {review["id"]: review for review in cloud.reviews}
    title = Suggestion.objects.get(field="title")
    assert reviews[title.result_id] == {
        "id": title.result_id,
        "outcome": "approved",
        "text": "Unsere Preise",
    }
    assert len(reviews) == 6
    assert all(r["outcome"] == "approved" for r in reviews.values())


def test_a_published_translation_gets_a_new_draft_keeping_corrections(api, cloud, page, editor):
    sync.backfill()
    sync.run(api)
    translate(cloud, page)
    sync.run(api)
    draft = de_draft(page)
    draft.meta_description = "Was es kostet."  # the editor's correction
    draft.save()
    publish(draft, editor)
    sync.run(api)

    # Only the title is retranslated later: a new draft, other texts stay as published.
    cloud.add_result(adapter.external_key(page), "title", "de", "Tarife", source="Pricing")
    sync.run(api)
    new_draft = de_draft(page)
    assert new_draft.pk != draft.pk
    assert (new_draft.title, new_draft.meta_description) == ("Tarife", "Was es kostet.")


def test_discarding_the_draft_rejects(api, cloud, page, editor):
    sync.backfill()
    sync.run(api)
    translate(cloud, page)
    sync.run(api)
    version = Version.objects.get_for_content(de_draft(page))

    version.delete()
    sync.run(api)

    assert {r["outcome"] for r in cloud.reviews} == {"rejected"}
    assert len(cloud.reviews) == 6


def test_archiving_the_draft_rejects(api, cloud, page, editor):
    sync.backfill()
    sync.run(api)
    translate(cloud, page)
    sync.run(api)
    Version.objects.get_for_content(de_draft(page)).archive(editor)
    assert set(Suggestion.objects.values_list("status", flat=True)) == {"rejected"}


def test_unaligned_segments_are_recorded_and_the_tree_can_be_rebuilt(api, cloud, page, editor):
    sync.backfill()
    sync.run(api)
    translate(cloud, page)
    sync.run(api)
    draft = de_draft(page)
    agency = next(
        p
        for p in placeholder_of(draft).get_plugins("de")
        if p.plugin_type == "TeaserPlugin" and p.parent_id is None
    )
    agency.delete()  # the editor removes a plugin; the draft counts as untouched
    DraftDelivery.objects.update(texts_hash=texts_hash(draft))

    # A new translation of the removed teaser's alt text has nowhere to go.
    alt_key = next(
        s["key"]
        for s in cloud.units[adapter.external_key(page)]["segments"]
        if s["key"].endswith(":image_alt")
    )
    translate(cloud, page, only=[alt_key])
    sync.run(api)
    delivery = DraftDelivery.objects.first()
    assert any(key.endswith(":image_alt") for key in delivery.unaligned)

    live = Suggestion.objects.filter(status__in=["pending", "drafted"])
    values = {suggestion.field: suggestion.text for suggestion in live}
    delivery = write_into_draft(page, "de", values, force=True, rebuild=True)
    assert delivery.unaligned == []
    assert "Ein Pony" in texts(de_draft(page)).values()


# --- Source changes, deletion, exclusion ---------------------------------------------------


def test_unpublishing_the_source_removes_the_unit(api, cloud, page, editor):
    sync.backfill()
    sync.run(api)
    Version.objects.get_for_content(PageContent.objects.get(page=page)).unpublish(editor)
    sync.run(api)
    assert cloud.deleted == [adapter.external_key(page)]


def test_deleting_the_page_deletes_the_unit(api, cloud, page):
    sync.backfill()
    sync.run(api)
    key = adapter.external_key(page)
    page.delete()
    sync.run(api)
    assert cloud.deleted == [key]


def test_excluded_languages_get_nothing(api, cloud, page, editor):
    exclusions.exclude(adapter, page, ["de"], user=editor)
    sync.run(api)
    assert cloud.units[adapter.external_key(page)]["writable_languages"] == ["fr"]
    translate(cloud, page)
    sync.run(api)
    assert latest_content(page, "de") is None
    assert not Suggestion.objects.exists()


def test_additional_plugins_in_the_source_get_new_keys(api, cloud, page, editor):
    sync.backfill()
    sync.run(api)
    draft = Version.objects.get_for_content(PageContent.objects.get(page=page)).copy(editor)
    add_plugin(placeholder_of(draft.content), "TeaserPlugin", "en", title="Enterprise")
    publish(draft.content, editor)
    sync.run(api)
    pushed = cloud.pushes[-1]["segments"]
    assert len(pushed) == 7
    assert len({s["key"] for s in pushed}) == 7
