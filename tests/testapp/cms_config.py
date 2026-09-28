from cms.app_base import CMSAppConfig
from django.template.response import TemplateResponse
from djangocms_versioning.datastructures import VersionableItem

from .models import ArticleContent, Box, NoteContent


def render(request, obj):
    return TemplateResponse(request, "content.html", {"object": obj})


class TestAppConfig(CMSAppConfig):
    cms_enabled = True
    cms_toolbar_enabled_models = [
        (ArticleContent, render, "article"),
        (NoteContent, render, "note"),
        (Box, render),
    ]
    djangocms_versioning_enabled = True
    versioning = [
        VersionableItem(
            content_model=ArticleContent,
            grouper_field_name="article",
            extra_grouping_fields=["language"],
            version_list_filter_lookups={"language": lambda request: [("en", "en")]},
        ),
        VersionableItem(content_model=NoteContent, grouper_field_name="note"),
    ]
