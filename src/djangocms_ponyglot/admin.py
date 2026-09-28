from django.contrib import admin
from django.urls import path

from . import views
from .models import DraftDelivery


@admin.register(DraftDelivery)
class DraftDeliveryAdmin(admin.ModelAdmin):
    """Deliveries into drafts (read-only); also hosts the toolbar's sideframe views, so sites
    don't need to add URLs."""

    list_display = ["external_key", "language", "delivered_at", "unaligned_count"]
    list_filter = ["language"]
    search_fields = ["external_key"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="unaligned")
    def unaligned_count(self, obj):
        return len(obj.unaligned)

    def get_urls(self):
        wrap = self.admin_site.admin_view
        return [
            path("unit/<str:key>/", wrap(views.status), name="djangocms_ponyglot_status"),
            path(
                "unit/<str:key>/translate/",
                wrap(views.translate),
                name="djangocms_ponyglot_translate",
            ),
            path(
                "unit/<str:key>/<str:language>/apply/",
                wrap(views.apply_waiting),
                name="djangocms_ponyglot_apply",
            ),
            path(
                "unit/<str:key>/<str:language>/copy-tree/",
                wrap(views.copy_tree),
                name="djangocms_ponyglot_copy_tree",
            ),
            path("unit/<str:key>/exclude/", wrap(views.exclude), name="djangocms_ponyglot_exclude"),
            *super().get_urls(),
        ]
