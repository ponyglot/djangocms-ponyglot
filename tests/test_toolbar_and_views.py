"""Toolbar menu and sideframe views for any content type."""

import re
from html.parser import HTMLParser

import pytest
from cms.models import PageContent
from djangocms_versioning.models import Version
from ponyglot import exclusions, sync
from ponyglot.adapters import registry
from ponyglot.client import Client
from ponyglot.models import Exclusion, HeldBack, Suggestion

from .test_content_types import synced

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


def status_url(ref):
    return f"/en/admin/djangocms_ponyglot/draftdelivery/unit/{ref.key}/"


def panel_url(ref):
    return status_url(ref) + "panel/"


def test_toolbar_menu_on_a_page(staff, page, page_ref, api, cloud):
    html = staff.get(page.get_absolute_url("en") + "?toolbar_on").content.decode()
    assert "Ponyglot translations" in html and panel_url(page_ref).removeprefix("/en") in html


@pytest.mark.parametrize("fixture", ["page_ref", "article_ref", "note_ref", "box_ref", "card_ref"])
def test_status_page_for_every_content_type(request, staff, views_cloud, api, fixture):
    ref = request.getfixturevalue(fixture)
    assert "been synced yet" in staff.get(status_url(ref)).content.decode()
    sync.backfill()
    sync.run(api)
    html = staff.get(status_url(ref)).content.decode()
    assert "segments need work" in html
    assert "missing or outdated" in html and 'name="languages" value="de"' in html
    if fixture == "note_ref":
        assert "published together" in html
    if fixture == "box_ref":
        assert "applying publishes them" in html


@pytest.mark.parametrize("fixture", ["article_ref", "note_ref", "box_ref", "card_ref"])
def test_toolbar_menu_on_other_content_types(request, staff, fixture):
    """Rendered through django CMS' own preview endpoint for any toolbar-enabled model
    (published versioned content can't be opened for editing without creating a draft)."""
    from cms.toolbar.utils import get_object_preview_url

    ref = request.getfixturevalue(fixture)
    content = ref.current("en") if ref.content_type.per_language else ref.current()
    response = staff.get(get_object_preview_url(content, language="en"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Ponyglot translations" in html
    assert panel_url(ref).removeprefix("/en") in html


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
    html = staff.get(status_url(box_ref)).content.decode()
    assert "DE: " in html and "waiting to be applied.</strong>" in html
    staff.post(status_url(box_ref) + "de/apply/")
    assert not Suggestion.objects.filter(status="pending").exists()


def test_exclude_and_include(staff, note_ref):
    staff.post(status_url(note_ref) + "exclude/", {"languages": ["fr"]})
    assert Exclusion.objects.get().languages == ["fr"]
    staff.post(status_url(note_ref) + "exclude/", {"include": "1"})
    assert not Exclusion.objects.exists()


def test_progress_ready_translations_and_fetching_now(staff, views_cloud, api, article_ref):
    from ponyglot.models import SyncRun

    from .test_content_types import translate

    sync.backfill()
    sync.run(api)
    SyncRun.objects.all().delete()  # as if no sync schedule ran yet
    views_cloud.job_status = "running"
    staff.post(status_url(article_ref) + "translate/", {"languages": ["de"]})
    html = staff.get(status_url(article_ref)).content.decode()
    assert "Jobs" in html and "running" in html  # the job, in progress
    assert "A translation job is running" in html
    assert "No sync ran lately" in html

    translate(views_cloud, article_ref)  # the job finished: results wait in the cloud
    html = staff.get(status_url(article_ref)).content.decode()
    assert "5 translations are ready" in html
    assert "Fetch translations now" in footer(html)  # no sync schedule: fetch by hand

    response = staff.post(status_url(article_ref) + "sync/", follow=True)
    html = response.content.decode()
    assert "5 translation(s) received" in html
    assert "translations are ready" not in html
    assert "Fetch translations now" not in footer(html)  # a sync just ran
    assert "DE: the translated draft isn't published yet." in html
    assert article_ref.latest("de") is not None  # the German draft is there
    assert SyncRun.objects.get().ok


def test_todo_says_who_acts_next(staff, views_cloud, api, article_ref):
    sync.backfill()
    sync.run(api)
    for segment in views_cloud.units[article_ref.key]["segments"]:
        for code in ("de", "fr"):
            views_cloud.states[(article_ref.key, segment["key"], code)] = "review"
    html = staff.get(status_url(article_ref)).content.decode()
    assert "in review in Ponyglot" in html
    assert "missing or outdated" not in html
    assert "Nothing for you to do right now" in html


def test_toolbar_entry_comes_first_in_the_language_menu(staff, page, page_ref, api, cloud):
    html = staff.get(page.get_absolute_url("en") + "?toolbar_on").content.decode()
    menu = html[html.index("<span>Language<") :]
    assert menu.index("Ponyglot translations") < menu.index("<span>English<")


def test_dialog_fetches_this_contents_translations_when_no_sync_ran_lately(
    staff, views_cloud, api, article_ref, page_ref
):
    from ponyglot.models import SyncRun

    from .test_content_types import translate

    sync.backfill()
    sync.run(api)
    translate(views_cloud, article_ref)
    translate(views_cloud, page_ref)
    SyncRun.objects.all().delete()  # no sync schedule

    html = staff.get(panel_url(article_ref)).content.decode()

    assert "5 new translations were fetched." in html
    assert "Review draft" in html  # the German draft
    acked = [r for r in views_cloud.results if r["id"] in views_cloud.acked]
    assert {r["unit"] for r in acked} == {article_ref.key}  # the page's wait for the next sync
    assert sync.last_run() is None  # not a sync round: the schedule is still missing
    assert sync.last_fetch(article_ref.key) is not None  # but this content was just fetched


def test_fetching_is_offered_only_when_the_last_sync_is_old(staff, views_cloud, api, article_ref):
    from datetime import timedelta

    from django.utils import timezone
    from ponyglot.models import SyncRun

    from .test_content_types import translate

    sync.backfill()
    sync.run(api)
    translate(views_cloud, article_ref)

    html = staff.get(panel_url(article_ref)).content.decode()
    assert "5 translations are ready at Ponyglot. It arrives with the next sync." in html
    assert "Fetch translations now" not in footer(html)  # the schedule brings them

    SyncRun.objects.update(finished_at=timezone.now() - timedelta(minutes=16))
    html = staff.get(status_url(article_ref)).content.decode()
    assert "Fetch translations now" in footer(html)

    response = staff.post(status_url(article_ref) + "fetch/", {"panel": "1"}, follow=True)
    assert response.redirect_chain[-1][0].endswith("/panel/")
    html = response.content.decode()
    assert "5 translations received." in html and "Review draft" in html


def test_dialog_translates_and_returns(staff, views_cloud, api, article_ref):
    sync.backfill()
    sync.run(api)
    html = staff.get(panel_url(article_ref)).content.decode()
    assert "missing or outdated" in html and "Translate all missing" in html

    response = staff.post(
        status_url(article_ref) + "translate/",
        {"languages": ["fr"], "panel": "1"},
        follow=True,
    )
    assert response.redirect_chain[-1][0].endswith("/panel/")
    assert "Translation requested" in response.content.decode()


def test_dialog_cheers_when_everything_is_up_to_date(staff, views_cloud, api, note_ref):
    sync.backfill()
    sync.run(api)
    for segment in views_cloud.units[note_ref.key]["segments"]:
        for code in ("de", "fr"):
            views_cloud.states[(note_ref.key, segment["key"], code)] = "ok"
    html = staff.get(panel_url(note_ref)).content.decode()
    assert "All translations are up to date." in html
    assert "Review draft" not in html and "Translate" not in html.split("<table>")[1]


def test_dialog_tells_shared_content_publishes_all_languages(
    staff, views_cloud, note_ref, article_ref
):
    notice = "All languages of this content are published together"
    assert notice in staff.get(panel_url(note_ref)).content.decode()
    assert notice not in staff.get(panel_url(article_ref)).content.decode()


def test_dialog_shows_texts_that_could_not_be_placed(staff, views_cloud, api, page_ref):
    from djangocms_ponyglot.models import DraftDelivery

    from .test_content_types import synced

    synced(api, views_cloud, page_ref)
    DraftDelivery.objects.filter(external_key=page_ref.key, language="de").update(
        unaligned=["plugin:abc:body"]
    )
    html = staff.get(panel_url(page_ref)).content.decode()
    assert "1 text couldn't be placed" in html
    assert f"{status_url(page_ref).removeprefix('/en')}#lang-de" in html
    assert 'id="lang-de"' in staff.get(status_url(page_ref)).content.decode()


def english_draft(article_ref, editor, title="Agency plan"):
    from .conftest import placeholder_of

    draft = Version.objects.get_for_content(article_ref.published("en")).copy(editor).content
    teaser = next(
        p.get_plugin_instance()[0]
        for p in placeholder_of(draft).get_plugins("en")
        if p.plugin_type == "TeaserPlugin"
    )
    teaser.title = title
    teaser.save()
    return draft


def pushed_texts(cloud, ref):
    return {s["text"] for s in cloud.units[ref.key]["segments"]}


def test_dialog_translates_the_viewed_version(staff, views_cloud, api, article_ref, editor):
    """Locality of behavior: the dialog works on the version the editor is looking at."""
    synced(api, views_cloud, article_ref)
    published = article_ref.published("en")
    draft = english_draft(article_ref, editor)

    html = staff.get(panel_url(article_ref) + f"?content={draft.pk}").content.decode()
    assert "Translates the version you're viewing (draft)." in html
    assert "Agency plan" in pushed_texts(views_cloud, article_ref)  # its texts, status included
    assert f"content={draft.pk}" in footer(html)["Translate all missing"]["action"]

    html = staff.get(panel_url(article_ref) + f"?content={published.pk}").content.decode()
    assert "Translates the version you're viewing (published)." in html
    assert "Agency plan" not in pushed_texts(views_cloud, article_ref)

    # Viewing a translation: its source language's current version.
    german = article_ref.latest("de")
    staff.get(panel_url(article_ref) + f"?content={german.pk}")
    assert "Agency plan" in pushed_texts(views_cloud, article_ref)

    # Translating from the dialog sends the viewed version, kept through the redirect.
    staff.get(panel_url(article_ref) + f"?content={published.pk}")
    response = staff.post(
        status_url(article_ref) + f"translate/?content={draft.pk}&_popup=1",
        {"languages": ["de"], "panel": "1"},
        follow=True,
    )
    assert "Translation requested" in response.content.decode()
    assert "Agency plan" in pushed_texts(views_cloud, article_ref)
    assert response.redirect_chain[-1][0].endswith(f"/panel/?_popup=1&content={draft.pk}")


def test_toolbar_passes_the_viewed_object(staff, page, page_ref):
    content = PageContent.admin_manager.get(page=page, language="en")
    html = staff.get(page.get_absolute_url("en") + "?toolbar_on").content.decode()
    assert f"panel/?content={content.pk}" in html


def test_discarding_a_source_draft_sends_the_published_version_again(
    api, views_cloud, article_ref, editor
):
    from ponyglot.models import SyncState

    synced(api, views_cloud, article_ref)
    draft = english_draft(article_ref, editor)
    assert not SyncState.objects.get(external_key=article_ref.key).dirty
    Version.objects.get_for_content(draft).delete()  # what "Discard" does
    assert SyncState.objects.get(external_key=article_ref.key).dirty
    sync.run(api)
    assert "Agency plan" not in pushed_texts(views_cloud, article_ref)


GENERAL = [
    "Translate what changed",
    "Translate all missing",
    "Fetch translations now",
    "Translate it again",
]


class ModalFooter(HTMLParser):
    """What django CMS' modal (cms.modal.js `_setButtons`) makes of a page: the `input`, `a`
    and `button` elements of the first `.submit-row` become footer buttons; it stops at the
    first hidden input; buttons submit their form with `form.submit()` (own fields only)."""

    def __init__(self, html):
        super().__init__()
        self.depth, self.done, self.items, self.forms, self.form = 0, False, [], {}, None
        self.before_row = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.form = attrs.get("id")
            self.forms.setdefault(self.form, {"action": attrs.get("action", "")})
        elif tag == "input" and self.form and attrs.get("type") == "hidden":
            self.forms[self.form].setdefault(attrs["name"], []).append(attrs.get("value"))
        if tag == "div" and "submit-row" in (attrs.get("class") or "") and not self.done:
            self.depth = 1
            return
        if self.depth:
            if tag == "div":
                self.depth += 1
            if tag in ("input", "a", "button"):
                self.items.append([tag, attrs, ""])
        elif tag == "button" and not self.done:
            self.before_row.append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self.form = None
        if self.depth and tag == "div":
            self.depth -= 1
            self.done = self.done or not self.depth

    def handle_data(self, data):
        if self.depth and self.items:
            self.items[-1][2] += data.strip()

    def buttons(self):
        """`{label: fields the click submits}`, asserting the CMS can use every item."""
        result = {}
        for tag, attrs, label in self.items:
            assert not (tag == "input" and attrs.get("type") == "hidden"), "stops the CMS"
            if tag == "a":
                result[label] = {"href": attrs["href"]}
                continue
            assert tag == "button" and "name" not in attrs, f"{label}: name/value isn't sent"
            result[label] = self.forms[attrs["form"]]
        return result


def footer(html):
    return ModalFooter(html).buttons()


@pytest.mark.parametrize("view", [status_url, panel_url])
def test_modal_has_general_actions_only_in_its_footer(
    view, staff, views_cloud, api, article_ref, editor
):
    sync.backfill()
    sync.run(api)
    draft = english_draft(article_ref, editor)
    exclusions.exclude(adapter, article_ref, ["de"], user=editor)
    if view is status_url:  # every action offered: no sync ran lately
        from ponyglot.models import SyncRun

        SyncRun.objects.all().delete()
    html = staff.get(view(article_ref) + f"?content={draft.pk}").content.decode()
    page = ModalFooter(html)
    buttons = page.buttons()
    general = [b for b in page.before_row if any(label in str(b) for label in GENERAL)]
    assert general == []
    body = html.partition('<div class="submit-row">')[0]
    assert not [label for label in GENERAL if f">{label}" in body]
    # What each footer button sends, as django CMS submits it.
    translate_button = "Translate what changed" if view is status_url else "Send for translation"
    if translate_button in buttons:
        assert buttons[translate_button]["languages"] == ["fr"]  # DE is excluded
    assert all(
        f"content={draft.pk}" in fields.get("action", fields.get("href"))
        for fields in buttons.values()
    )
    if view is status_url:
        assert "Exclude" not in html  # managed under Ponyglot › Exclusions
    else:
        forms = [fields for fields in buttons.values() if "href" not in fields]
        assert all(fields.get("panel") == ["1"] for fields in forms)


def test_excluded_content_offers_only_translating_it_again(staff, views_cloud, api, article_ref):
    sync.backfill()
    sync.run(api)
    exclusions.exclude(adapter, article_ref, None)
    buttons = footer(staff.get(status_url(article_ref)).content.decode())
    assert list(buttons) == ["Translate it again", "Back to overview"]
    assert buttons["Translate it again"]["include"] == ["1"]


def test_confirm_page_footer(staff, views_cloud, api, article_ref):
    sync.backfill()
    sync.run(api)
    views_cloud.confirm_above = 0
    response = staff.post(
        status_url(article_ref) + "translate/",
        {"languages": ["de"], "panel": "1"},
        QUERY_STRING="content=1",
    )
    buttons = footer(response.content.decode())
    assert buttons["Translate"]["confirm"] == ["1"]
    assert buttons["Translate"]["action"].endswith("translate/?content=1")
    assert buttons["Back"]["href"].endswith("/panel/?content=1")


def test_only_texts_needing_action_on_the_platform_link_there(staff, views_cloud, api, article_ref):
    sync.backfill()
    sync.run(api)
    first, second = views_cloud.units[article_ref.key]["segments"][:2]
    views_cloud.states[(article_ref.key, first["key"], "de")] = "review"  # editors: in the CMS
    views_cloud.states[(article_ref.key, second["key"], "fr")] = "attention"  # held by QA
    attention = f"{views_cloud.DASHBOARD}/attention/?unit={article_ref.key}&amp;language=fr"
    for url in (panel_url, status_url):
        html = staff.get(url(article_ref)).content.decode()
        assert f'<a href="{attention}" target="_blank" rel="noopener">' in html, url
        assert html.count('target="_blank"') == 1, url  # nothing for texts in review


@pytest.mark.parametrize("view", [status_url, panel_url])
def test_modal_popup_mode_is_kept(view, staff, views_cloud, api, article_ref):
    sync.backfill()
    sync.run(api)
    assert 'id="header"' in staff.get(view(article_ref)).content.decode()  # plain admin page

    html = staff.get(view(article_ref) + "?_popup=1").content.decode()
    assert 'id="header"' not in html  # django CMS' modal: admin popup mode
    forms = ModalFooter(html).forms
    actions = re.findall(r'<form[^>]* action="([^"]+)"', html)
    assert actions and all(action.endswith("?_popup=1") for action in actions)
    assert forms  # every form posts back in popup mode

    response = staff.post(
        status_url(article_ref) + "translate/?_popup=1",
        {"languages": ["fr"], "source": "published", "panel": "1"},
        follow=True,
    )
    assert response.redirect_chain[-1][0].endswith("/panel/?_popup=1")
    assert 'id="header"' not in response.content.decode()
    if view is panel_url:
        assert f'href="{status_url(article_ref)}?_popup=1"' in html  # "Details" stays in it


def test_fetch_button_hides_after_any_recent_fetch(staff, views_cloud, api, article_ref):
    from datetime import timedelta

    from django.utils import timezone
    from ponyglot.models import SyncRun

    sync.backfill()
    sync.run(api)
    SyncRun.objects.update(finished_at=timezone.now() - timedelta(minutes=16))
    assert "Fetch translations now" in footer(staff.get(status_url(article_ref)).content.decode())

    # Opening the dialog fetched this content's translations: recent again.
    html = staff.get(panel_url(article_ref)).content.decode()
    assert "Fetch translations now" not in footer(html)
    assert "Fetch translations now" not in footer(
        staff.get(status_url(article_ref)).content.decode()
    )
    assert SyncRun.objects.filter(unit=article_ref.key).exists()


def test_translate_what_changed_only_when_something_changed(staff, views_cloud, api, note_ref):
    from ponyglot.models import SyncState

    sync.backfill()
    sync.run(api)
    for segment in views_cloud.units[note_ref.key]["segments"]:
        for code in ("de", "fr"):
            views_cloud.states[(note_ref.key, segment["key"], code)] = "ok"
    assert "Translate what changed" not in footer(staff.get(status_url(note_ref)).content.decode())

    SyncState.objects.filter(external_key=note_ref.key).update(dirty=True)  # an edit, not sent
    assert "Translate what changed" in footer(staff.get(status_url(note_ref)).content.decode())

    SyncState.objects.filter(external_key=note_ref.key).update(dirty=False)
    key = views_cloud.units[note_ref.key]["segments"][0]["key"]
    views_cloud.states[(note_ref.key, key, "fr")] = "stale"
    buttons = footer(staff.get(status_url(note_ref)).content.decode())
    assert buttons["Translate what changed"]["languages"] == ["fr"]


@pytest.mark.parametrize("view", [panel_url, status_url])
def test_in_review_explains_that_publishing_clears_it(view, staff, views_cloud, api, article_ref):
    sync.backfill()
    sync.run(api)
    hint = "clears as soon as the translated content is published"
    assert hint not in staff.get(view(article_ref)).content.decode()  # nothing in review
    first = views_cloud.units[article_ref.key]["segments"][0]
    views_cloud.states[(article_ref.key, first["key"], "de")] = "review"
    assert hint in staff.get(view(article_ref)).content.decode()


def test_no_admin_list_for_deliveries(staff, admin_client):
    assert "draftdelivery" not in admin_client.get("/en/admin/").content.decode()
    assert admin_client.get("/en/admin/djangocms_ponyglot/draftdelivery/").status_code == 404


@pytest.mark.parametrize("view", [panel_url, status_url])
def test_modal_footer_is_never_empty(view, staff, views_cloud, api, article_ref):
    """With an empty submit row, django CMS moves the first form's button (a language's
    "Translate") into its footer: there is always a link to the other view."""
    sync.backfill()
    sync.run(api)
    for segment in views_cloud.units[article_ref.key]["segments"]:
        views_cloud.states[(article_ref.key, segment["key"], "de")] = "ok"  # only FR missing
    html = staff.get(view(article_ref)).content.decode()
    buttons = footer(html)
    assert "Translate" not in buttons  # a language's own button stays in its row
    assert {"Details", "Back to overview"} & set(buttons)
