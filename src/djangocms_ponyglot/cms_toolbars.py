from cms.cms_toolbars import LANGUAGE_MENU_IDENTIFIER
from cms.toolbar_base import CMSToolbar
from cms.toolbar_pool import toolbar_pool
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from ponyglot.conf import get_config

from .contenttypes import for_model

PONYGLOT_BREAK = "ponyglot-break"


@toolbar_pool.register
class PonyglotToolbar(CMSToolbar):
    """ "Ponyglot translations" for the content being edited or viewed (any translated content
    type): the first entry of the language menu, opening the translations dialog. Without a
    language menu, it gets a "Ponyglot" menu of its own."""

    def post_template_populate(self):
        # After populate: the language menu is created by other toolbars.
        from .delivery import waiting
        from .views import unpublished_draft

        obj = self.toolbar.get_object()
        user = self.request.user
        ct = for_model(type(obj)) if obj is not None else None
        if ct is None or not user.is_staff:
            return
        model = ct.model
        if not user.has_perm(f"{model._meta.app_label}.change_{model._meta.model_name}"):
            return
        ref = ct.ref(obj)
        to_review = set(waiting(ref).values_list("language", flat=True))
        if ct.versioned:
            source = get_config().source_language
            to_review.update(
                code
                for code in get_config().languages
                if code != source and unpublished_draft(ref, code) is not None
            )
        label = _("Ponyglot translations")
        if to_review:
            label = _("Ponyglot translations (%d to review)") % len(to_review)
        # The viewed version is what the dialog translates (published or not).
        url = reverse("admin:djangocms_ponyglot_panel", args=[ref.key]) + f"?content={obj.pk}"
        menu = self.toolbar.get_menu(LANGUAGE_MENU_IDENTIFIER)
        if menu:
            menu.add_modal_item(label, url=url, position=0)
            menu.add_break(PONYGLOT_BREAK, position=1)
        else:
            menu = self.toolbar.get_or_create_menu("ponyglot", _("Ponyglot"))
            menu.add_modal_item(label, url=url)
