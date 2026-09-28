"""The translation status sideframe and its actions (ADR 0001), for any content type.

Reached from the toolbar's Ponyglot menu for the content being edited or viewed. Reading the
status asks the cloud (short timeout; a failure shows a message). Actions are explicit editor
decisions: request a translation, apply waiting translations, copy the plugin tree from the
source, exclude or include.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from ponyglot import exclusions, sync
from ponyglot import suggestions as suggestion_service
from ponyglot.adapters import registry
from ponyglot.client import APIError, Client, ConfigurationError
from ponyglot.conf import get_config
from ponyglot.models import HeldBack, Suggestion, SuggestionStatus

from .contenttypes import parse_key
from .delivery import waiting, write
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


def _ref(request, key, permission=None):
    ref = parse_key(key)
    if ref is None or not ref.exists():
        raise Http404
    model = ref.content_type.model
    needed = permission or f"{model._meta.app_label}.change_{model._meta.model_name}"
    if not request.user.is_staff or not request.user.has_perm(needed):
        raise PermissionDenied
    return ref


def status_url(ref):
    return reverse("admin:djangocms_ponyglot_status", args=[ref.key])


def status(request, key):
    ref = _ref(request, key)
    source = get_config().source_language
    languages = [code for code in get_config().languages if code != source]
    cloud_status, cloud_error = None, ""
    try:
        cloud_status = _client().unit_status(ref.key)
    except (APIError, ConfigurationError) as error:
        if getattr(error, "status", None) == 404:
            cloud_error = _("This content hasn't been synced yet.")
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
        delivery = DraftDelivery.objects.filter(external_key=ref.key, language=code).first()
        per_language.append(
            {
                "code": code,
                "waiting": waiting(ref, code).count(),
                "unaligned": delivery.unaligned if delivery else [],
                "excluded": exclusions.is_excluded(ref.key, code),
            }
        )
    return render(
        request,
        "djangocms_ponyglot/status.html",
        {
            "ref": ref,
            "versioned": ref.content_type.versioned,
            "per_language": ref.content_type.per_language,
            "languages": per_language,
            "cloud_status": cloud_status,
            "cloud_error": cloud_error,
            "rows": rows,
            "symbols": SYMBOLS,
            "held_back": HeldBack.objects.filter(external_key=ref.key),
            "exclusion": exclusions.get(ref.key),
            "title": _("Translation of “%s”") % ref,
        },
    )


def translate(request, key):
    ref = _ref(request, key)
    if request.method != "POST":
        return redirect(status_url(ref))
    languages = request.POST.getlist("languages")
    try:
        client = _client()
        if request.POST.get("source") == "draft":
            unit = _adapter().snapshot(ref, draft=True)
            if unit is None:
                raise APIError(0, _("There is no text to translate."))
            client.push_units([unit.as_payload(with_translations=False)])
        else:
            sync.push(client, sync.Report())  # bring the published source up to date
        payload = {"type": "translate", "units": [ref.key]}
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
                    "ref": ref,
                    "estimate": error.data.get("estimate", {}),
                    "languages": languages,
                    "source": request.POST.get("source", ""),
                    "title": _("Confirm translation"),
                },
            )
        messages.error(request, _("Ponyglot: %s") % error)
        return redirect(status_url(ref))
    except ConfigurationError as error:
        messages.error(request, str(error))
        return redirect(status_url(ref))
    messages.success(
        request,
        _("Translation requested (%(segments)s segments). It arrives with the next sync.")
        % {"segments": job.get("estimated_segments", 0)},
    )
    return redirect(status_url(ref))


def apply_waiting(request, key, language):
    ref = _ref(request, key)
    if request.method == "POST":
        result = suggestion_service.apply(
            list(waiting(ref, language)),
            request.user,
            confirm_errors=bool(request.POST.get("confirm_errors")),
        )
        if result.applied:
            messages.success(request, _("Translations applied."))
        if result.blocked:
            messages.error(
                request,
                _("%d translations have QA errors: open them under Ponyglot › suggestions.")
                % result.blocked,
            )
        if result.outdated:
            messages.warning(request, _("Some translations are outdated: the source changed."))
    return redirect(status_url(ref))


def copy_tree(request, key, language):
    ref = _ref(request, key)
    if request.method == "POST":
        live = Suggestion.objects.filter(
            external_key=ref.key,
            language=language,
            status__in=[SuggestionStatus.DRAFTED, SuggestionStatus.PENDING],
        ).order_by("created_at")
        write(ref, language, {s.field: s.applied_text for s in live}, force=True, rebuild=True)
        messages.success(request, _("Plugin tree copied from the source."))
    return redirect(status_url(ref))


def exclude(request, key):
    ref = _ref(request, key, "ponyglot.add_exclusion")
    if request.method == "POST":
        if request.POST.get("include"):
            exclusions.include(_adapter(), ref)
            messages.success(request, _("Translated again."))
        else:
            exclusions.exclude(
                _adapter(), ref, request.POST.getlist("languages") or None, user=request.user
            )
            messages.success(request, _("Excluded from translation."))
    return redirect(status_url(ref))
