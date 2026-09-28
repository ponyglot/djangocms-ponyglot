from cms.toolbar_base import CMSToolbar
from cms.toolbar_pool import toolbar_pool
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .contenttypes import for_model


@toolbar_pool.register
class PonyglotToolbar(CMSToolbar):
    """ "Ponyglot" menu for the content being edited or viewed (any translated content type)."""

    def populate(self):
        from .delivery import waiting

        obj = self.toolbar.get_object()
        user = self.request.user
        ct = for_model(type(obj)) if obj is not None else None
        if ct is None or not user.is_staff:
            return
        model = ct.model
        if not user.has_perm(f"{model._meta.app_label}.change_{model._meta.model_name}"):
            return
        ref = ct.ref(obj)
        count = waiting(ref).count()
        label = _("Ponyglot") if not count else _("Ponyglot (%d waiting)") % count
        menu = self.toolbar.get_or_create_menu("ponyglot", label)
        url = reverse("admin:djangocms_ponyglot_status", args=[ref.key])
        menu.add_sideframe_item(_("Translation status and actions…"), url=url)
