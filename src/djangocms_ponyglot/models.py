from django.db import models
from django.utils.translation import gettext_lazy as _


class PluginKey(models.Model):
    """A stable key for a plugin across versions and languages.

    Plugin ids change whenever djangocms-versioning copies a version, so segments are keyed by
    this instead (`plugin:<key>:<field>`). A copy inherits the key of its original, and a target
    language plugin shares the key of the source plugin it translates (`keys.py`).
    """

    plugin_id = models.PositiveBigIntegerField(unique=True)
    key = models.CharField(max_length=32, db_index=True)

    class Meta:
        verbose_name = _("plugin key")

    def __str__(self):
        return f"{self.plugin_id} → {self.key}"


class DraftDelivery(models.Model):
    """Translations written into a target-language draft (one row per delivery).

    `texts_hash` is the hash of the draft's translatable texts right after writing: if the
    draft still has it, nobody edited it, and the next delivery may update it in place.
    `unaligned` lists source segments that had no counterpart in the draft's plugin tree.
    """

    page_id = models.PositiveBigIntegerField(db_index=True)
    language = models.CharField(max_length=15)
    content_id = models.PositiveBigIntegerField()
    version_id = models.PositiveBigIntegerField(null=True)
    texts_hash = models.CharField(max_length=64)
    unaligned = models.JSONField(default=list, blank=True)
    delivered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-delivered_at", "-pk"]
        verbose_name = _("draft delivery")

    def __str__(self):
        return f"page {self.page_id} [{self.language}] {self.delivered_at:%Y-%m-%d %H:%M}"
