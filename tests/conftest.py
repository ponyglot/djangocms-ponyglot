import pytest
from cms.api import add_plugin, create_page
from cms.models import PageContent, Placeholder
from djangocms_versioning.models import Version
from ponyglot.client import Client
from ponyglot.testing import FakeCloud

from djangocms_ponyglot.contenttypes import content_types
from tests.testapp.models import Article, ArticleContent, Box, Note, NoteContent


@pytest.fixture
def cloud():
    return FakeCloud()


@pytest.fixture
def api(cloud, monkeypatch):
    monkeypatch.setattr("ponyglot.client.time.sleep", lambda seconds: None)
    return Client(transport=cloud)


@pytest.fixture
def editor(django_user_model):
    return django_user_model.objects.create_superuser("editor", "editor@example.com", "pw")


def publish(content, user):
    Version.objects.get_for_content(content).publish(user)


def placeholder_of(content, slot="content"):
    if hasattr(content, "rescan_placeholders"):
        content.rescan_placeholders()
    existing = Placeholder.objects.get_for_obj(content).filter(slot=slot).first()
    return existing or Placeholder.objects.create(slot=slot, source=content)


def fill(content, language="en"):
    """A text plugin embedding a link (its name travels inside the text), and a teaser."""
    placeholder = placeholder_of(content)
    text = add_plugin(placeholder, "TextPlugin", language, body="<p>Plans</p>")
    link = add_plugin(placeholder, "LinkPlugin", language, target=text, name="per month")
    text.body = (
        f'<p>€79 per site, <cms-plugin alt="Link" title="Link" id="{link.pk}">'
        "</cms-plugin>, cancel any time</p>"
    )
    text.save()
    add_plugin(placeholder, "TeaserPlugin", language, title="Agency", image_alt="A pony", style="b")


def ref_of(content):
    return content_types()[content._meta.label_lower].ref(content)


@pytest.fixture
def page(db, editor):
    page = create_page("Pricing", "page.html", "en", created_by=editor)
    content = PageContent.admin_manager.get(page=page, language="en")
    content.meta_description = "What Ponyglot costs."
    content.save()
    fill(content)
    publish(content, editor)
    return page


@pytest.fixture
def page_ref(page):
    return ref_of(PageContent.objects.get(page=page, language="en"))


@pytest.fixture
def article_ref(db, editor):
    article = Article.objects.create()
    content = ArticleContent.objects.with_user(editor).create(
        article=article,
        language="en",
        title="Pricing explained",
        lead="Plans for all.",
        slug="pricing-explained",
    )
    fill(content)
    publish(content, editor)
    return ref_of(content)


@pytest.fixture
def note_ref(db, editor):
    note = Note.objects.create()
    content = NoteContent.objects.with_user(editor).create(note=note, name="Pricing note")
    fill(content)
    publish(content, editor)
    return ref_of(content)


@pytest.fixture
def box_ref(db):
    box = Box.objects.create(name="Pricing box")
    fill(box)
    return ref_of(box)
