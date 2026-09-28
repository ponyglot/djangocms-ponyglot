import pytest
from cms.api import add_plugin, create_page
from cms.models import PageContent
from djangocms_versioning.models import Version
from ponyglot.client import Client
from ponyglot.testing import FakeCloud


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
    content.rescan_placeholders()
    return content.get_placeholders().get(slot=slot)


@pytest.fixture
def page(db, editor):
    """A published English page: a text plugin with an embedded teaser, and a teaser."""
    page = create_page("Pricing", "page.html", "en", created_by=editor)
    content = PageContent.admin_manager.get(page=page, language="en")
    content.meta_description = "What Ponyglot costs."
    content.save()
    placeholder = placeholder_of(content)
    text = add_plugin(placeholder, "TextPlugin", "en", body="<p>Plans</p>")
    inline = add_plugin(
        placeholder, "TeaserPlugin", "en", target=text, title="Pro plan", link="https://x.test"
    )
    text.body = (
        f'<p>€79 per site and <cms-plugin alt="Teaser" title="Teaser" id="{inline.pk}">'
        "</cms-plugin> month</p>"
    )
    text.save()
    add_plugin(placeholder, "TeaserPlugin", "en", title="Agency", image_alt="A pony", style="b")
    publish(content, editor)
    return page
