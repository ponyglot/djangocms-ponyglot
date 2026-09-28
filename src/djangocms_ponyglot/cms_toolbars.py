from cms.toolbar_base import CMSToolbar
from cms.toolbar_pool import toolbar_pool
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .delivery import waiting


@toolbar_pool.register
class PonyglotToolbar(CMSToolbar):
    """ "Ponyglot" menu: the page's translation status and actions in the sideframe."""

    def populate(self):
        page = getattr(self.request, "current_page", None)
        user = self.request.user
        if not page or not user.is_staff or not user.has_perm("cms.change_page"):
            return
        count = waiting(page).count()
        label = _("Ponyglot") if not count else _("Ponyglot (%d waiting)") % count
        menu = self.toolbar.get_or_create_menu("ponyglot", label)
        url = reverse("admin:djangocms_ponyglot_status", args=[page.pk])
        menu.add_sideframe_item(_("Translation status and actions…"), url=url)
