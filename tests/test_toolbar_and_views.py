"""Toolbar menu and sideframe views: status, translate, apply waiting, copy tree, exclude."""

import pytest
from djangocms_versioning.models import Version
from ponyglot import sync
from ponyglot.adapters import registry
from ponyglot.client import Client
from ponyglot.models import Exclusion, HeldBack, Suggestion

from djangocms_ponyglot.adapter import latest_content

from .test_workflow import translate

pytestmark = pytest.mark.django_db

adapter = registry.get("djangocms")


@pytest.fixture
def views_cloud(cloud, monkeypatch):
    monkeypatch.setattr("djangocms_ponyglot.views._client", lambda: Client(transport=cloud))
    return cloud


@pytest.fixture
def staff(client, editor):
    client.force_login(editor)
    return client


def status_url(page):
    return f"/en/admin/djangocms_ponyglot/draftdelivery/page/{page.pk}/"


def test_toolbar_menu(staff, page, api, cloud):
    sync.backfill()
    sync.run(api)
    html = staff.get(page.get_absolute_url("en") + "?toolbar_on").content.decode()
    assert "Ponyglot" in html and status_url(page).removeprefix("/en") in html

    # Waiting translations are counted in the menu title.
    translate(cloud, page)
    sync.run(api)
    draft = latest_content(page, "de")
    draft.title = "Von Hand"
    draft.save()
    cloud.add_result(adapter.external_key(page), "title", "de", "Tarife", source="Pricing")
    sync.run(api)
    html = staff.get(page.get_absolute_url("en") + "?toolbar_on").content.decode()
    assert "Ponyglot (1 waiting)" in html


def test_status_page(staff, page, api, views_cloud):
    assert "been synced yet" in staff.get(status_url(page)).content.decode()
    sync.backfill()
    sync.run(api)
    key = adapter.external_key(page)
    views_cloud.states[(key, "title", "de")] = "ok"
    HeldBack.objects.create(
        external_key=key,
        key="meta_description",
        language="fr",
        issues=[{"message": "Too long"}],
    )
    html = staff.get(status_url(page)).content.decode()
    assert "6 of 6 segments need work" in html
    assert "Waiting for a QA review" in html and "Too long" in html
    assert "Translate what changed" in html


def test_status_needs_permission(client, django_user_model, page):
    user = django_user_model.objects.create_user("visitor", password="pw", is_staff=True)
    client.force_login(user)
    assert client.get(status_url(page)).status_code == 403


def test_translate_this_page(staff, page, views_cloud):
    response = staff.post(status_url(page) + "translate/", {"languages": ["de"]}, follow=True)
    assert "Translation requested" in response.content.decode()
    assert adapter.external_key(page) in views_cloud.units  # pushed first
    jobs = [r for r in views_cloud.requests if r[:2] == ("POST", "/jobs")]
    assert jobs == [
        (
            "POST",
            "/jobs",
            {"type": "translate", "units": [adapter.external_key(page)], "languages": ["de"]},
        )
    ]


def test_translate_the_current_draft(staff, page, editor, views_cloud):
    content = latest_content(page, "en")
    draft = Version.objects.get_for_content(content).copy(editor).content
    draft.title = "Pricing (new)"
    draft.save()
    staff.post(status_url(page) + "translate/", {"source": "draft"})
    pushed = views_cloud.units[adapter.external_key(page)]
    assert pushed["segments"][0]["text"] == "Pricing (new)"


def test_large_translations_need_confirmation(staff, page, views_cloud):
    views_cloud.confirm_above = 10
    html = staff.post(status_url(page) + "translate/", {"languages": ["de"]}).content.decode()
    assert "needs your confirmation" in html and 'name="confirm"' in html
    assert views_cloud.jobs == {}
    staff.post(status_url(page) + "translate/", {"languages": ["de"], "confirm": "1"})
    assert len(views_cloud.jobs) == 1


def test_apply_waiting_and_copy_tree(staff, page, api, cloud):
    sync.backfill()
    sync.run(api)
    translate(cloud, page)
    sync.run(api)
    draft = latest_content(page, "de")
    draft.menu_title = "Kosten"
    draft.save()
    cloud.add_result(adapter.external_key(page), "title", "de", "Tarife", source="Pricing")
    sync.run(api)

    staff.post(status_url(page) + "de/apply/")
    assert latest_content(page, "de").title == "Tarife"
    assert not Suggestion.objects.filter(status="pending").exists()

    staff.post(status_url(page) + "de/copy-tree/")
    assert latest_content(page, "de").title == "Tarife"


def test_exclude_and_include(staff, page):
    staff.post(status_url(page) + "exclude/", {"languages": ["fr"]})
    assert Exclusion.objects.get().languages == ["fr"]
    staff.post(status_url(page) + "exclude/", {})
    assert Exclusion.objects.get().all_languages
    assert "excluded from translation" in staff.get(status_url(page)).content.decode()
    staff.post(status_url(page) + "exclude/", {"include": "1"})
    assert not Exclusion.objects.exists()
