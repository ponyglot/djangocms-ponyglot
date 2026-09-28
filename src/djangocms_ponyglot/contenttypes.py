"""Frontend-editable django CMS content types and the units made of them.

Content types are the models registered for frontend editing (`cms_toolbar_enabled_models`):
`cms.PageContent`, djangocms-stories' `PostContent`, aliases, custom models. For each one:

- **versioned** if djangocms-versioning manages it: translations go into drafts; otherwise they
  wait as suggestions until an editor applies them (writing them publishes).
- **per language** if a content object exists per language (its `language` field is one of the
  versioning grouping fields, e.g. pages and posts): each language is edited and published on
  its own. Otherwise **shared**: one content object holds all languages (plugins carry their
  language) and is published as a whole.
- The **grouper** (page, post, …) identifies a unit across versions and languages; without one,
  the content object itself does.
"""

import re
from dataclasses import dataclass
from functools import cached_property

from django.apps import apps
from django.db import models

from . import conf

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass(frozen=True)
class ContentType:
    model: type
    versionable: object = None
    toolbar_grouper: str | None = None

    @property
    def label(self):
        return self.model._meta.label_lower

    @property
    def versioned(self):
        return self.versionable is not None

    @cached_property
    def grouper_field(self):
        if self.versionable is not None:
            return self.versionable.grouper_field_name
        return self.toolbar_grouper

    @cached_property
    def grouping_fields(self):
        """Grouping fields besides the grouper (from versioning), e.g. ("language",)."""
        if self.versionable is not None:
            return tuple(self.versionable.extra_grouping_fields)
        return ("language",) if self._has_language_field and self.grouper_field else ()

    @property
    def _has_language_field(self):
        try:
            field = self.model._meta.get_field("language")
        except Exception:  # noqa: BLE001
            return False
        return isinstance(field, models.CharField)

    @property
    def per_language(self):
        return "language" in self.grouping_fields and self._has_language_field

    @property
    def extra_fields(self):
        return tuple(field for field in self.grouping_fields if field != "language")

    def manager(self, *, admin=False):
        if admin and self.versioned:
            return self.model.admin_manager
        return self.model._default_manager if not self.versioned else self.model.objects

    def ref(self, content):
        """The unit a content object belongs to."""
        if self.grouper_field:
            grouper_id = getattr(content, f"{self.grouper_field}_id")
        else:
            grouper_id = content.pk
        extra = tuple((name, getattr(content, name)) for name in self.extra_fields)
        return UnitRef(self, grouper_id, extra)

    def ref_for_grouper(self, grouper):
        return UnitRef(self, grouper.pk, ())

    @property
    def grouper_model(self):
        if not self.grouper_field:
            return None
        return self.model._meta.get_field(self.grouper_field).related_model


@dataclass(frozen=True)
class UnitRef:
    """One unit: a grouper (plus extra grouping values) of a content type."""

    content_type: ContentType
    grouper_id: int
    extra: tuple = ()

    @property
    def pk(self):
        return self.grouper_id

    @property
    def key(self):
        values = "".join(f":{_UNSAFE.sub('-', str(value))}" for _, value in self.extra)
        return f"djangocms:{self.content_type.label}:{self.grouper_id}{values}"

    def filters(self, language=None):
        ct = self.content_type
        if ct.grouper_field:
            filters = {f"{ct.grouper_field}_id": self.grouper_id, **dict(self.extra)}
        else:
            filters = {"pk": self.grouper_id}
        if language and ct.per_language:
            filters["language"] = language
        return filters

    def published(self, language=None):
        """The published content (versioned) or the content (unversioned)."""
        return self.content_type.manager().filter(**self.filters(language)).first()

    def current(self, language=None):
        """The draft if any, else the published content (versioning's `current_content`)."""
        ct = self.content_type
        if not ct.versioned:
            return self.published(language)
        return ct.manager(admin=True).filter(**self.filters(language)).current_content().first()

    def latest(self, language=None):
        """Draft, else published, else the newest other version."""
        ct = self.content_type
        if not ct.versioned:
            return self.published(language)
        return ct.manager(admin=True).filter(**self.filters(language)).latest_content().first()

    @property
    def grouper(self):
        model = self.content_type.grouper_model
        if model is None:
            return self.content_type.model._default_manager.filter(pk=self.grouper_id).first()
        return model._default_manager.filter(pk=self.grouper_id).first()

    def exists(self):
        return self.grouper is not None

    def __str__(self):
        grouper = self.grouper
        return str(grouper) if grouper is not None else self.key


def _toolbar_models():
    try:
        extension = apps.get_app_config("cms").cms_extension
    except (LookupError, AttributeError):
        return {}, {}
    return extension.toolbar_enabled_models, getattr(extension, "model_groupers", {})


def _versionables():
    if not apps.is_installed("djangocms_versioning"):
        return {}
    try:
        return apps.get_app_config("djangocms_versioning").cms_extension.versionables_by_content
    except AttributeError:
        return {}


def content_types():
    """All content types to translate, by label."""
    toolbar_models, groupers = _toolbar_models()
    versionables = _versionables()
    selected = conf.selected_models()
    result = {}
    for model in toolbar_models:
        label = model._meta.label_lower
        if selected is not None and label not in selected:
            continue
        result[label] = ContentType(model, versionables.get(model), groupers.get(model))
    return result


def for_model(model):
    return content_types().get(model._meta.label_lower)


def parse_key(external_key):
    """The `UnitRef` for an external key, or None."""
    parts = external_key.split(":")
    if len(parts) < 3 or parts[0] != "djangocms" or not parts[2].isdigit():
        return None
    ct = content_types().get(parts[1])
    if ct is None or len(parts) - 3 != len(ct.extra_fields):
        return None
    extra = tuple(zip(ct.extra_fields, parts[3:], strict=True))
    return UnitRef(ct, int(parts[2]), extra)
