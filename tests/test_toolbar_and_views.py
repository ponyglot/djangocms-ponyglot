"""Toolbar menu and sideframe views for any content type."""

import pytest
from ponyglot import sync
from ponyglot.client import Client
from ponyglot.models import Exclusion, HeldBack, Suggestion

from .test_content_types import synced

pytestmark = pytest.mark.django_db


@pytest.fixture
def views_cloud(cloud, monkeypatch):
    monkeypatch.setattr("djangocms_ponyglot.views._client", lambda: Client(transport=cloud))
    return cloud


@pytest.fixture
def staff(client, editor):
    client.force_login(editor)
    return client


def status_url(ref):
    return f"/en/admin/djangocms_ponyglot/draftdelivery/unit/{ref.key}/"


def test_toolbar_menu_on_a_page(staff, page, page_ref, api, cloud):
    html = staff.get(page.get_absolute_url("en") + "?toolbar_on").content.decode()
    assert "Ponyglot" in html and status_url(page_ref).removeprefix("/en") in html


@pytest.mark.parametrize("fixture", ["page_ref", "article_ref", "note_ref", "box_ref"])
def test_status_page_for_every_content_type(request, staff, views_cloud, api, fixture):
    ref = request.getfixturevalue(fixture)
    assert "been synced yet" in staff.get(status_url(ref)).content.decode()
    sync.backfill()
    sync.run(api)
    html = staff.get(status_url(ref)).content.decode()
    assert "segments need work" in html
    if fixture == "note_ref":
        assert "published together" in html
    if fixture == "box_ref":
        assert "applying publishes them" in html


def test_status_shows_held_back(staff, views_cloud, api, page_ref):
    sync.backfill()
    sync.run(api)
    HeldBack.objects.create(
        external_key=page_ref.key, key="title", language="fr", issues=[{"message": "Too long"}]
    )
    html = staff.get(status_url(page_ref)).content.decode()
    assert "Waiting for a QA review" in html and "Too long" in html


def test_permission_needed(client, django_user_model, page_ref):
    user = django_user_model.objects.create_user("visitor", password="pw", is_staff=True)
    client.force_login(user)
    assert client.get(status_url(page_ref)).status_code == 403
    assert client.get(status_url(page_ref).replace(":1/", ":999/")).status_code in (403, 404)


def test_translate(staff, views_cloud, article_ref):
    response = staff.post(
        status_url(article_ref) + "translate/", {"languages": ["de"]}, follow=True
    )
    assert "Translation requested" in response.content.decode()
    jobs = [r for r in views_cloud.requests if r[:2] == ("POST", "/jobs")]
    assert jobs[0][2] == {"type": "translate", "units": [article_ref.key], "languages": ["de"]}


def test_translate_needs_confirmation(staff, views_cloud, article_ref):
    views_cloud.confirm_above = 10
    html = staff.post(
        status_url(article_ref) + "translate/", {"languages": ["de"]}
    ).content.decode()
    assert "needs your confirmation" in html
    staff.post(status_url(article_ref) + "translate/", {"languages": ["de"], "confirm": "1"})
    assert len(views_cloud.jobs) == 1


def test_apply_unversioned_from_the_status_page(staff, api, cloud, box_ref):
    synced(api, cloud, box_ref)
    staff.post(status_url(box_ref) + "de/apply/")
    assert not Suggestion.objects.filter(status="pending").exists()


def test_exclude_and_include(staff, note_ref):
    staff.post(status_url(note_ref) + "exclude/", {"languages": ["fr"]})
    assert Exclusion.objects.get().languages == ["fr"]
    staff.post(status_url(note_ref) + "exclude/", {"include": "1"})
    assert not Exclusion.objects.exists()
