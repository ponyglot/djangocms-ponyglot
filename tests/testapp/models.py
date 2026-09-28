from cms.models import CMSPlugin
from django.db import models


class Teaser(CMSPlugin):
    """Text fields are translated; the URL and the style choice aren't."""

    title = models.CharField(max_length=100)
    image_alt = models.CharField(max_length=200, blank=True)
    link = models.URLField(blank=True)
    style = models.CharField(max_length=10, choices=[("a", "A"), ("b", "B")], default="a")
