from cms.models import CMSPlugin
from cms.models.fields import PlaceholderRelationField
from django.db import models


class Teaser(CMSPlugin):
    """Text fields are translated; the URL and the style choice aren't."""

    title = models.CharField(max_length=100)
    image_alt = models.CharField(max_length=200, blank=True)
    link = models.URLField(blank=True)
    style = models.CharField(max_length=10, choices=[("a", "A"), ("b", "B")], default="a")


class Link(CMSPlugin):
    """Declared in DJANGOCMS_TRANSLATIONS_CONF: `name` only, and as a text child label."""

    name = models.CharField(max_length=100)
    tooltip = models.CharField(max_length=100, blank=True)
    url = models.CharField(max_length=200, blank=True)


# One content object per language, versioned (like pages and djangocms-stories posts).
class Article(models.Model):
    def __str__(self):
        return f"article {self.pk}"


class ArticleContent(models.Model):
    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="contents")
    language = models.CharField(max_length=15)
    title = models.CharField(max_length=200)
    lead = models.TextField(blank=True)
    slug = models.SlugField(blank=True)
    template = models.CharField(max_length=100, default="content.html")
    placeholders = PlaceholderRelationField()

    def __str__(self):
        return self.title

    def get_template(self):
        return "content.html"


# All languages in one content object, versioned: published together.
class Note(models.Model):
    def __str__(self):
        return f"note {self.pk}"


class NoteContent(models.Model):
    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="contents")
    name = models.CharField(max_length=100)
    placeholders = PlaceholderRelationField()

    def __str__(self):
        return self.name

    def get_template(self):
        return "content.html"


# Unversioned, all languages in one object: nothing is written without an editor's "apply".
class Box(models.Model):
    name = models.CharField(max_length=100)
    placeholders = PlaceholderRelationField()

    def __str__(self):
        return self.name

    def get_template(self):
        return "content.html"
