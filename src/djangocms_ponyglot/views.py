"""The translation status sideframe and its actions (ADR 0001).

Reached from the toolbar's Ponyglot menu. Reading the status asks the cloud (with a short
timeout; a failure shows a message, the rest of the page still works). Actions are explicit
editor decisions: request a translation, apply waiting translations to the current draft,
copy the plugin tree from the source, exclude or include the page.
"""

from cms.models import Page
from cms.utils.i18n import get_language_list
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from ponyglot import exclusions, sync
from ponyglot import suggestions as suggestion_service
from ponyglot.adapters import registry
from ponyglot.client import APIError, Client, ConfigurationError
from ponyglot.conf import get_config
from ponyglot.models import HeldBack

from .delivery import waiting, write_into_draft
from .models import DraftDelivery

SYMBOLS = {
    "ok": "✓",
    "stale": gettext_lazy("stale"),
    "review": gettext_lazy("in review"),
    "missing": "–",
    "attention": gettext_lazy("QA"),
}


def _adapter():
    return registry.get("djangocms")


def _client():
    return Client(timeout=10, retries=0)


def _check(request, permission="cms.change_page"):
    if not request.user.is_staff or not request.user.has_perm(permission):
        raise PermissionDenied


def _status_url(page):
    return reverse("admin:djangocms_ponyglot_status", args=[page.pk])


def status(request, page_id):
    _check(request)
    page = get_object_or_404(Page, pk=page_id)
    adapter = _adapter()
    key = adapter.external_key(page)
    source = get_config().source_language
    languages = [code for code in get_language_list(page.site_id) if code != source]
    cloud_status, cloud_error = None, ""
    try:
        cloud_status = _client().unit_status(key)
    except (APIError, ConfigurationError) as error:
        if getattr(error, "status", None) == 404:
            cloud_error = _("This page hasn't been synced yet.")
        else:
            cloud_error = str(error)
    rows = []
    if cloud_status:
        codes = [lang["code"] for lang in cloud_status["languages"]]
        for segment in cloud_status["segments"]:
            rows.append(
                {
                    "key": segment["key"],
                    "cells": [
                        {
                            "code": code,
                            "state": segment["states"].get(code, "missing"),
                            "held": code in segment.get("held_back", []),
                            "issues": segment.get("qa", {}).get(code, []),
                        }
                        for code in codes
                    ],
                }
            )
    per_language = []
    for code in languages:
        delivery = DraftDelivery.objects.filter(page_id=page.pk, language=code).first()
        per_language.append(
            {
                "code": code,
                "waiting": waiting(page, code).count(),
                "unaligned": delivery.unaligned if delivery else [],
                "excluded": exclusions.is_excluded(key, code),
            }
        )
    return render(
        request,
        "djangocms_ponyglot/status.html",
        {
            "page": page,
            "key": key,
            "source": source,
            "languages": per_language,
            "cloud_status": cloud_status,
            "cloud_error": cloud_error,
            "rows": rows,
            "symbols": SYMBOLS,
            "held_back": HeldBack.objects.filter(external_key=key),
            "exclusion": exclusions.get(key),
            "title": _("Translation of “%s”") % page,
        },
    )


def translate(request, page_id):
    _check(request)
    page = get_object_or_404(Page, pk=page_id)
    if request.method != "POST":
        return redirect(_status_url(page))
    adapter = _adapter()
    languages = request.POST.getlist("languages")
    use_draft = request.POST.get("source") == "draft"
    try:
        client = _client()
        if use_draft:
            unit = adapter.snapshot(page, draft=True)
            if unit is None:
                raise APIError(0, _("The page has no text to translate."))
            client.push_units([unit.as_payload(with_translations=False)])
        else:
            sync.push(client, sync.Report())  # bring the published source up to date
        payload = {"type": "translate", "units": [adapter.external_key(page)]}
        if languages:
            payload["languages"] = languages
        if request.POST.get("confirm"):
            payload["confirm"] = True
        job = client.create_job(**payload)
    except APIError as error:
        if error.code == "confirmation_required":
            return render(
                request,
                "djangocms_ponyglot/confirm.html",
                {
                    "page": page,
                    "estimate": error.data.get("estimate", {}),
                    "languages": languages,
                    "source": request.POST.get("source", ""),
                    "title": _("Confirm translation"),
                },
            )
        messages.error(request, _("Ponyglot: %s") % error)
        return redirect(_status_url(page))
    except ConfigurationError as error:
        messages.error(request, str(error))
        return redirect(_status_url(page))
    messages.success(
        request,
        _("Translation requested (%(segments)s segments). Drafts arrive with the next sync.")
        % {"segments": job.get("estimated_segments", 0)},
    )
    return redirect(_status_url(page))


def apply_waiting(request, page_id, language):
    _check(request)
    page = get_object_or_404(Page, pk=page_id)
    if request.method == "POST":
        result = suggestion_service.apply(
            list(waiting(page, language)),
            request.user,
            confirm_errors=bool(request.POST.get("confirm_errors")),
        )
        if result.applied:
            messages.success(request, _("Translations written into the current draft."))
        if result.blocked:
            messages.error(
                request,
                _("%d translations have QA errors: open them under Ponyglot › suggestions.")
                % result.blocked,
            )
        if result.outdated:
            messages.warning(request, _("Some translations are outdated: the source changed."))
    return redirect(_status_url(page))


def copy_tree(request, page_id, language):
    _check(request)
    page = get_object_or_404(Page, pk=page_id)
    if request.method == "POST":
        from ponyglot.models import Suggestion, SuggestionStatus

        values = {
            s.field: s.applied_text
            for s in Suggestion.objects.filter(
                external_key=_adapter().external_key(page),
                language=language,
                status__in=[SuggestionStatus.DRAFTED, SuggestionStatus.PENDING],
            ).order_by("created_at")
        }
        write_into_draft(page, language, values, force=True, rebuild=True)
        messages.success(request, _("Plugin tree copied from the source into the draft."))
    return redirect(_status_url(page))


def exclude(request, page_id):
    _check(request, "ponyglot.add_exclusion")
    page = get_object_or_404(Page, pk=page_id)
    if request.method == "POST":
        adapter = _adapter()
        if request.POST.get("include"):
            exclusions.include(adapter, page)
            messages.success(request, _("The page is translated again."))
        else:
            exclusions.exclude(
                adapter, page, request.POST.getlist("languages") or None, user=request.user
            )
            messages.success(request, _("Excluded from translation."))
    return redirect(_status_url(page))
