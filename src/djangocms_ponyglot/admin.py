from django.contrib import admin
from django.urls import path

from . import views
from .models import DraftDelivery


@admin.register(DraftDelivery)
class PonyglotViewsAdmin(admin.ModelAdmin):
    """Hosts the toolbar dialog and the status page under the admin, so sites don't need to
    add URLs. No list or forms of its own: it doesn't show in the admin index."""

    def get_model_perms(self, request):
        return {}

    def get_urls(self):
        wrap = self.admin_site.admin_view
        return [
            path("unit/<str:key>/", wrap(views.status), name="djangocms_ponyglot_status"),
            path("unit/<str:key>/panel/", wrap(views.panel), name="djangocms_ponyglot_panel"),
            path("unit/<str:key>/fetch/", wrap(views.fetch), name="djangocms_ponyglot_fetch"),
            path(
                "unit/<str:key>/translate/",
                wrap(views.translate),
                name="djangocms_ponyglot_translate",
            ),
            path("unit/<str:key>/sync/", wrap(views.sync_now), name="djangocms_ponyglot_sync"),
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
        ]
