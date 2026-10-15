"""Plugin fields from plugin forms, for every plugin (djangocms-frontend's entangled JSON
fields included), and declarations on top."""

import pytest
from cms.api import add_plugin
from django import forms
from django.core.validators import RegexValidator, validate_slug
from djangocms_versioning.models import Version
from ponyglot import sync

from djangocms_ponyglot.fields import (
    PROSE_ATTRIBUTES,
    child_label_field,
    get_value,
    plugin_fields,
    prose_kind,
    set_value,
)

from .conftest import placeholder_of, publish
from .test_content_types import texts_in

pytestmark = pytest.mark.django_db

# Every djangocms-frontend plugin has an attribute field ("Advanced settings").
ATTRIBUTES = {f"config.attributes.{name}": False for name in PROSE_ATTRIBUTES}

FRONTEND = [
    (
        "HeadingPlugin",
        {
            "heading": "Our plans",
            "heading_level": "h2",
            "heading_id": "plans",
            "attributes": {"title": "Plans and prices", "class": "lead"},
        },
    ),
    ("BlockquotePlugin", {"quote_content": "<p>Great tool</p>", "quote_origin": "<p>A user</p>"}),
    ("CodePlugin", {"code_content": "print('hello')", "code_type": "code"}),
    ("FigurePlugin", {"figure_caption": "<p>A pony</p>"}),
]


def bound(plugin):
    return plugin.get_plugin_instance()[0]


@pytest.fixture
def frontend_article(article_ref, editor):
    """An article with djangocms-frontend plugins and a text embedding a frontend link."""
    draft = Version.objects.get_for_content(article_ref.published("en")).copy(editor).content
    placeholder = placeholder_of(draft)
    for plugin_type, config in FRONTEND:
        add_plugin(placeholder, plugin_type, "en", config=config)
    text = add_plugin(placeholder, "TextPlugin", "en", body="<p>x</p>")
    link = add_plugin(
        placeholder,
        "TextLinkPlugin",
        "en",
        target=text,
        config={"name": "our plans", "link": {"external_link": "https://example.test"}},
    )
    text.body = f'<p>See <cms-plugin id="{link.pk}"></cms-plugin> for details.</p>'
    text.save()
    publish(draft, editor)
    return article_ref


def by_type(content, language, plugin_type):
    return next(
        bound(p)
        for p in placeholder_of(content).get_plugins(language)
        if p.plugin_type == plugin_type
    )


def test_fields_come_from_the_forms(frontend_article):
    content = frontend_article.published("en")
    found = {
        plugin_type: {
            path: spec.html
            for path, spec in plugin_fields(by_type(content, "en", plugin_type)).items()
        }
        for plugin_type, _ in FRONTEND
    }
    assert found == {
        "HeadingPlugin": {"config.heading": False, **ATTRIBUTES},  # not heading_id, level
        "BlockquotePlugin": {
            "config.quote_content": True,
            "config.quote_origin": True,
            **ATTRIBUTES,
        },
        "CodePlugin": ATTRIBUTES,  # code isn't prose
        "FigurePlugin": {"config.figure_caption": True, **ATTRIBUTES},
    }
    link = by_type(content, "en", "TextLinkPlugin")
    assert child_label_field(link) == "config.name"  # translated inside the sentence
    # Model-form plugins too: the teaser's URL and choice fields drop out as form fields.
    assert set(plugin_fields(by_type(content, "en", "TeaserPlugin"))) == {"title", "image_alt"}


def test_frontend_round_trip(api, cloud, frontend_article):
    sync.backfill()
    sync.run(api)
    unit = cloud.units[frontend_article.key]
    texts = {
        s["key"].split(":", 2)[2]: s["text"]
        for s in unit["segments"]
        if s["key"].startswith("plugin:")
    }
    assert texts["config.heading"] == "Our plans"
    assert texts["config.attributes.title"] == "Plans and prices"
    assert "lead" not in texts.values()  # the class attribute isn't prose
    assert "print(" not in " ".join(texts.values())
    see = next(t for t in texts.values() if t.startswith("<p>See"))
    assert ">our plans</cms-plugin>" in see

    german = {
        "Our plans": "Unsere Tarife",
        "<p>Great tool</p>": "<p>Tolles Werkzeug</p>",
        "<p>A pony</p>": "<p>Ein Pony</p>",
        "Plans and prices": "Tarife und Preise",
    }
    for segment in unit["segments"]:
        text = segment["text"]
        if text.startswith("<p>See"):
            text = (
                text.replace("See", "Siehe")
                .replace("our plans", "unsere Tarife")
                .replace("for details.", "für Details.")
            )
        cloud.add_result(
            unit["external_key"],
            segment["key"],
            "de",
            german.get(text, text),
            source=segment["text"],
            format=segment.get("format", "plain"),
        )
    sync.run(api)

    draft = frontend_article.latest("de")
    heading = by_type(draft, "de", "HeadingPlugin")
    assert heading.config["heading"] == "Unsere Tarife"
    assert (heading.config["heading_id"], heading.config["heading_level"]) == ("plans", "h2")
    assert heading.config["attributes"] == {"title": "Tarife und Preise", "class": "lead"}
    assert (
        by_type(draft, "de", "BlockquotePlugin").config["quote_content"] == "<p>Tolles Werkzeug</p>"
    )
    assert by_type(draft, "de", "CodePlugin").config["code_content"] == "print('hello')"
    link = by_type(draft, "de", "TextLinkPlugin")
    assert link.config["name"] == "unsere Tarife"
    assert link.config["link"] == {"external_link": "https://example.test"}
    body = next(t for k, t in texts_in(frontend_article, draft, "de").items() if "Siehe" in t)
    assert "für Details" in body


def test_declarations_use_names_or_paths(frontend_article, settings):
    content = frontend_article.published("en")
    heading = by_type(content, "en", "HeadingPlugin")
    settings.DJANGOCMS_TRANSLATIONS_CONF = {
        **settings.DJANGOCMS_TRANSLATIONS_CONF,
        "HeadingPlugin": {"excluded_fields": ["heading"]},  # resolved to config.heading
    }
    assert set(plugin_fields(heading)) == set(ATTRIBUTES)
    settings.DJANGOCMS_TRANSLATIONS_CONF["HeadingPlugin"] = {"fields": ["config.heading_id"]}
    assert list(plugin_fields(heading)) == ["config.heading_id"]  # declared: taken as is
    # Paths into an entangled field resolve like names.
    settings.DJANGOCMS_TRANSLATIONS_CONF["HeadingPlugin"] = {"fields": ["attributes.title"]}
    assert list(plugin_fields(heading)) == ["config.attributes.title"]


def test_djangocms_deepl_translations_config(frontend_article, settings):
    content = frontend_article.published("en")
    heading = by_type(content, "en", "HeadingPlugin")
    code = by_type(content, "en", "CodePlugin")
    settings.DJANGOCMS_DEEPL_TRANSLATIONS_PLUGIN_CONFIG = {
        # "/" paths or key tuples into the plugin's data
        "HeadingPlugin": {
            "translatable_fields": ("config/heading", ("config", "attributes", "alt")),
        },
        "CodePlugin": {"no_translation": True},
    }
    assert list(plugin_fields(heading)) == ["config.heading", "config.attributes.alt"]
    assert plugin_fields(code) == {}
    # djangocms-translations' declaration wins.
    settings.DJANGOCMS_TRANSLATIONS_CONF = {
        **settings.DJANGOCMS_TRANSLATIONS_CONF,
        "HeadingPlugin": {"fields": ["heading"]},
    }
    assert list(plugin_fields(heading)) == ["config.heading"]


def test_identifiers_are_not_prose():
    assert prose_kind("label", forms.CharField()) == "plain"
    # djangocms-form-builder's field_name: validated as a slug.
    assert prose_kind("field_name", forms.CharField(validators=[validate_slug])) is None
    assert prose_kind("label", forms.CharField(validators=[RegexValidator(r"^[a-z]+$")])) is None
    assert prose_kind("value", forms.CharField()) is None  # a choice's stored value


def test_nested_paths():
    class Plugin:
        config = {"heading": "Hi", "attributes": {"class": "lead"}}

    plugin = Plugin()
    original = plugin.config
    set_value(plugin, "config.attributes.alt", "A pony")
    set_value(plugin, "config.link.title", "More")  # missing levels are created
    assert plugin.config == {
        "heading": "Hi",
        "attributes": {"class": "lead", "alt": "A pony"},
        "link": {"title": "More"},
    }
    assert original == {"heading": "Hi", "attributes": {"class": "lead"}}  # copied, not changed
    assert get_value(plugin, "config.attributes.alt") == "A pony"
    assert get_value(plugin, "config.heading.alt") == ""  # not a dict on the way


def test_the_plugin_admins_exclude_is_respected(frontend_article, monkeypatch):
    from tests.testapp.cms_plugins import TeaserPlugin

    monkeypatch.setattr(TeaserPlugin, "exclude", ["image_alt"])
    teaser = by_type(frontend_article.published("en"), "en", "TeaserPlugin")
    assert set(plugin_fields(teaser)) == {"title"}
    monkeypatch.setattr(TeaserPlugin, "exclude", None)
    monkeypatch.setattr(TeaserPlugin, "fieldsets", [(None, {"fields": ["image_alt"]})])
    assert set(plugin_fields(teaser)) == {"image_alt"}


def test_fields_command():
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("ponyglot_fields", "HeadingPlugin", "CodePlugin", "TextLinkPlugin", stdout=out)
    report = out.getvalue()
    assert "config.heading\n" in report and "heading_id" not in report
    assert "config.attributes (alt, title," in report
    assert "config.name inside the text it's embedded in" in report
    assert "TextPlugin" not in report.replace("TextLinkPlugin", "")

    out = StringIO()
    call_command(
        "ponyglot_fields", "EditorNotePlugin", "HeadingPlugin", translated=True, stdout=out
    )
    assert "EditorNotePlugin" not in out.getvalue()
