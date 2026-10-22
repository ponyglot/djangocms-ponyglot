"""Publishing right away on the requesting editor's behalf (cloud ADR 0022)."""

import pytest
from django.contrib.auth.models import Permission
from djangocms_versioning import constants
from ponyglot import sync
from ponyglot.models import Suggestion

from .test_content_types import german, state_of

pytestmark = pytest.mark.django_db


def translate(cloud, ref, publish_as, language="de", only=None):
    for segment in cloud.units[ref.key]["segments"]:
        if only is None or segment["key"] in only:
            cloud.add_result(
                ref.key,
                segment["key"],
                language,
                german(segment["text"]),
                source=segment["text"],
                format=segment.get("format", "plain"),
                publish_as=publish_as,
            )


def pushed(api):
    sync.backfill()
    sync.run(api)


@pytest.mark.parametrize("fixture", ["page_ref", "article_ref"])
def test_published_as_the_editor(request, api, cloud, editor, fixture):
    ref = request.getfixturevalue(fixture)
    pushed(api)
    translate(cloud, ref, editor.pk)
    report = sync.run(api)

    published = ref.published("de")
    assert published is not None and published.title in ("Preise", "Preise erklärt")
    assert state_of(published) == constants.PUBLISHED
    assert set(Suggestion.objects.values_list("status", "decided_by")) == {("published", editor.pk)}
    assert not report.warnings
    sync.run(api)
    assert {(r["outcome"], r["reviewer"]) for r in cloud.reviews} == {("published", "editor")}


def test_shared_content_stays_a_draft(api, cloud, editor, note_ref):
    pushed(api)
    translate(cloud, note_ref, editor.pk)
    report = sync.run(api)
    assert state_of(note_ref.latest()) == constants.DRAFT
    assert set(Suggestion.objects.values_list("status", flat=True)) == {"drafted"}
    assert "shared by all languages" in report.warnings[0]


def test_earlier_unreviewed_translations_keep_the_draft(api, cloud, editor, page_ref):
    pushed(api)
    cloud.add_result(page_ref.key, "title", "de", "Preise", source="Pricing")  # no request
    sync.run(api)
    translate(cloud, page_ref, editor.pk, only={"meta_description"})
    report = sync.run(api)
    assert page_ref.published("de") is None
    assert "other translations waiting for review" in report.warnings[0]


def test_an_editor_who_may_not_publish(api, cloud, page_ref, django_user_model):
    author = django_user_model.objects.create_user("author", is_staff=True)
    author.user_permissions.add(Permission.objects.get(codename="change_page"))
    pushed(api)
    translate(cloud, page_ref, author.pk)
    report = sync.run(api)
    assert page_ref.published("de") is None
    assert state_of(page_ref.latest("de")) == constants.DRAFT
    assert "may not publish" in report.warnings[0]
