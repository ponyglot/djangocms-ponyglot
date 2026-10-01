"""The translation status sideframe and its actions (ADR 0001), for any content type.

Reached from the toolbar's Ponyglot menu for the content being edited or viewed. Reading the
status asks the cloud (short timeout; a failure shows a message). Actions are explicit editor
decisions: request a translation, apply waiting translations, copy the plugin tree from the
source, include excluded content again (excluding is managed under Ponyglot › Exclusions).
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.options import IS_POPUP_VAR
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext
from ponyglot import exclusions, sync
from ponyglot import suggestions as suggestion_service
from ponyglot.adapters import registry
from ponyglot.client import APIError, Client, ConfigurationError
from ponyglot.conf import get_config
from ponyglot.models import HeldBack, Suggestion, SuggestionStatus, SyncState

from .conf import fetch_after
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


def panel_url(ref):
    return reverse("admin:djangocms_ponyglot_panel", args=[ref.key])


def _param(request, name):
    return request.GET.get(name) or request.POST.get(name) or ""


def _keep(request):
    """What the views keep on every link, form and redirect: django CMS' modal mode
    (`_popup=1`: admin popup, no header or sidebar) and the content being viewed
    (`content=<pk>`, set by the toolbar: the source the dialog translates)."""
    is_popup = bool(_param(request, IS_POPUP_VAR))
    params = {IS_POPUP_VAR: "1"} if is_popup else {}
    if _param(request, "content").isdigit():
        params["content"] = _param(request, "content")
    return {"is_popup": is_popup, "keep_qs": f"?{urlencode(params)}" if params else ""}


def viewed_source(request, ref):
    """The source-language content to translate: the version the editor is viewing,
    published or not (`content=<pk>` from the toolbar). Viewing a translation, its source
    language's current version. None without a viewed object (status page opened directly:
    the published version, like the sync)."""
    pk = _param(request, "content")
    if not pk.isdigit():
        return None
    ct = ref.content_type
    content = ct.manager(admin=True).filter(pk=int(pk)).first()
    if content is None or ct.ref(content).key != ref.key:
        return None
    source_language = get_config().source_language
    if ct.per_language and content.language != source_language:
        return ref.current(source_language)
    return content


def _push_source(client, ref, source):
    """Send the source to translate: the viewed version, or the published one if changed."""
    if source is None:
        sync.push(client, sync.Report(), keys=[ref.key])
        return
    unit = _adapter().snapshot(ref, content=source)
    if unit is None:
        raise APIError(0, _("There is no text to translate."))
    client.push_units([unit.as_payload(with_translations=False)])


def _source_label(ref, source):
    if source is None or not ref.content_type.versioned:
        return ""
    from djangocms_versioning.models import Version

    return Version.objects.get_for_content(source).get_state_display().lower()


def _back_url(request, ref):
    url = panel_url(ref) if request.POST.get("panel") else status_url(ref)
    return url + _keep(request)["keep_qs"]


def _back(request, ref):
    """Actions return to where they were started: the toolbar dialog or the status page."""
    return redirect(_back_url(request, ref))


ACTIVE_JOB = ("queued", "running")


def sync_overdue(last_sync):
    """No sync round (cron/worker) ran within `FETCH_AFTER_MINUTES`."""
    return last_sync is None or timezone.now() - last_sync.finished_at > fetch_after()


def _progress(client, ref):
    """Jobs covering this unit (active, or finished in the last runs) and the translations the
    cloud has ready that the site hasn't fetched yet."""
    jobs = [
        job
        for job in client.jobs()
        if job.get("units") is None or ref.key in (job.get("units") or [])
    ][:5]
    ready = sum(1 for result in client.results(unit=ref.key) if result["unit"] == ref.key)
    return jobs, ready


def _rows(cloud_status):
    if not cloud_status:
        return []
    codes = [lang["code"] for lang in cloud_status["languages"]]
    return [
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
        for segment in cloud_status["segments"]
    ]


def unpublished_draft(ref, language):
    """The draft the latest delivery wrote, while it's still unpublished (else None)."""
    delivery = DraftDelivery.objects.filter(external_key=ref.key, language=language).first()
    if delivery is None or delivery.version_id is None:
        return None
    from djangocms_versioning import constants
    from djangocms_versioning.models import Version

    version = Version.objects.filter(pk=delivery.version_id, state=constants.DRAFT).first()
    return version.content if version is not None else None


def _languages(ref, cloud_status, rows, jobs):
    """Per target language: what waits locally (translations to apply, a draft to review)
    and, from the cloud status, which texts are untranslated, in review or held by QA."""
    from cms.toolbar.utils import get_object_edit_url

    source = get_config().source_language
    names = dict(settings.LANGUAGES)
    cloud_codes = {lang["code"] for lang in cloud_status["languages"]} if cloud_status else set()
    # Links to the platform only where action is needed there: translations held back by QA
    # (newer clouds only; the rest is done in the CMS).
    attention_urls = {
        lang["code"]: lang.get("attention_url", "")
        for lang in (cloud_status or {}).get("languages", [])
    }
    running = [job for job in jobs if job.get("status") in ACTIVE_JOB]
    languages = []
    for code in get_config().languages:
        if code == source:
            continue
        cells = [cell for row in rows for cell in row["cells"] if cell["code"] == code]
        held = sum(1 for c in cells if c["held"] or c["state"] == "attention")
        in_review = sum(1 for c in cells if not c["held"] and c["state"] == "review")
        untranslated = sum(1 for c in cells if not c["held"] and c["state"] in ("missing", "stale"))
        delivery = DraftDelivery.objects.filter(external_key=ref.key, language=code).first()
        draft = unpublished_draft(ref, code)
        lang = {
            "code": code,
            "name": names.get(code, code),
            "excluded": exclusions.is_excluded(ref.key, code),
            "waiting": waiting(ref, code).count(),
            "unaligned": delivery.unaligned if delivery else [],
            "draft_url": get_object_edit_url(draft, language=code) if draft else None,
            "untranslated": untranslated,
            "in_review": in_review,
            "attention_url": attention_urls.get(code, ""),
            "held": held,
            "translating": any(
                job.get("languages") is None or code in job["languages"] for job in running
            ),
        }
        lang["done"] = (
            code in cloud_codes
            and not lang["excluded"]
            and not (untranslated or in_review or held or lang["waiting"] or draft)
            and not lang["unaligned"]
        )
        languages.append(lang)
    return languages


def _todo(languages, jobs, ready, cloud_status):
    """What needs work, grouped by who acts next: the editor (translate, fetch, apply, review
    and publish) or Ponyglot (a job in progress, a review, a QA check)."""
    active = [lang for lang in languages if not lang["excluded"]]
    untranslated = [lang for lang in active if lang["untranslated"]]
    to_apply = [lang for lang in active if lang["waiting"]]
    to_publish = [lang for lang in active if lang["draft_url"] and not lang["waiting"]]
    in_review = sum(lang["in_review"] for lang in active)
    held = sum(lang["held"] for lang in active)
    translating = any(job.get("status") in ACTIVE_JOB for job in jobs)
    editor = bool((untranslated and not translating) or ready or to_apply or to_publish)
    work = bool(untranslated or in_review or held or ready or to_apply or to_publish)
    return {
        "untranslated": sum(lang["untranslated"] for lang in untranslated),
        "untranslated_languages": [lang["code"] for lang in untranslated],
        "translating": translating,
        "in_review": in_review,
        "held": held,
        "ready": ready,
        "to_apply": to_apply,
        "to_publish": to_publish,
        "editor": editor,
        "any": work,
        "all_done": bool(cloud_status) and not work and not translating,
    }


def _overview(ref, *, refresh=False, source=None):
    """What the status page and the toolbar dialog show. `refresh`: if no sync round ran
    lately, first push this content if it changed and fetch its translations. `source`: the
    viewed version, sent first so the status describes it."""
    cloud_status, cloud_error, jobs, ready, fetched, unsynced = None, "", [], 0, None, False
    last_sync = sync.last_run()
    overdue = sync_overdue(last_sync)
    try:
        client = _client()
        if refresh and overdue:
            fetched = sync.refresh(client, ref.key)
        if source is not None:
            _push_source(client, ref, source)
            unsynced = False
        cloud_status = client.unit_status(ref.key)
        jobs, ready = _progress(client, ref)
    except (APIError, ConfigurationError) as error:
        if getattr(error, "status", None) == 404:
            cloud_error = _("This content hasn't been synced yet.")
            unsynced = True
        else:
            cloud_error = str(error)
    if fetched is not None:
        last_sync = sync.last_run()
        overdue = sync_overdue(last_sync)
    rows = _rows(cloud_status)
    languages = _languages(ref, cloud_status, rows, jobs)
    todo = _todo(languages, jobs, ready, cloud_status)
    unpushed = SyncState.objects.filter(external_key=ref.key, dirty=True).exists()
    return {
        "ref": ref,
        "versioned": ref.content_type.versioned,
        "per_language": ref.content_type.per_language,
        "languages": languages,
        "cloud_status": cloud_status,
        "cloud_error": cloud_error,
        "rows": rows,
        "todo": todo,
        # "Translate what changed" only when something did: texts missing or stale, a source
        # change not sent yet, or content the cloud doesn't know yet.
        "translate_due": bool(todo["untranslated_languages"]) or unpushed or unsynced,
        "jobs": jobs,
        "ready": ready,
        "fetched": fetched,
        "last_sync": last_sync,
        "sync_overdue": overdue,
        # "Fetch translations now" only when this content's translations weren't fetched
        # lately (by the schedule, the dialog or the button).
        "fetch_due": sync_overdue(sync.last_fetch(ref.key)),
        "source_label": _source_label(ref, source),
    }


def status(request, key):
    ref = _ref(request, key)
    context = _overview(ref, source=viewed_source(request, ref))
    context.update(
        symbols=SYMBOLS,
        held_back=HeldBack.objects.filter(external_key=ref.key),
        exclusion=exclusions.get(ref.key),
        title=_("Translation of “%s”") % ref,
    )
    context.update(_keep(request))
    return render(request, "djangocms_ponyglot/status.html", context)


def panel(request, key):
    """The toolbar dialog: this content's translations per language, each with its next step.
    Fetches this content's translations first when no sync round ran lately."""
    ref = _ref(request, key)
    context = _overview(ref, refresh=True, source=viewed_source(request, ref))
    context.update(status_url=status_url(ref), title=_("Translations of “%s”") % ref)
    context.update(_keep(request))
    return render(request, "djangocms_ponyglot/panel.html", context)


def fetch(request, key):
    """Fetch this content's finished translations (and push it if it changed)."""
    ref = _ref(request, key)
    if request.method == "POST":
        try:
            report = sync.refresh(_client(), ref.key)
        except (APIError, ConfigurationError) as error:
            messages.error(request, _("Ponyglot: %s") % error)
        else:
            messages.success(
                request,
                ngettext(
                    "%(n)s translation received.", "%(n)s translations received.", report.delivered
                )
                % {"n": report.delivered},
            )
    return _back(request, ref)


def translate(request, key):
    ref = _ref(request, key)
    if request.method != "POST":
        return _back(request, ref)
    languages = request.POST.getlist("languages")
    try:
        client = _client()
        _push_source(client, ref, viewed_source(request, ref))
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
                    "panel": request.POST.get("panel"),
                    "back_url": _back_url(request, ref),
                    **_keep(request),
                    "title": _("Confirm translation"),
                },
            )
        messages.error(request, _("Ponyglot: %s") % error)
        return _back(request, ref)
    except ConfigurationError as error:
        messages.error(request, str(error))
        return _back(request, ref)
    messages.success(
        request,
        _(
            "Translation requested (%(segments)s segments). When the job is done, “Fetch "
            "translations now” brings it into your drafts (or wait for the next scheduled sync)."
        )
        % {"segments": job.get("estimated_segments", 0)},
    )
    return _back(request, ref)


def sync_now(request, key):
    """Run a sync round now: push changes, report reviews, fetch finished translations."""
    ref = _ref(request, key)
    if request.method == "POST":
        try:
            report = sync.run(_client())
        except (APIError, ConfigurationError) as error:
            messages.error(request, _("Ponyglot: %s") % error)
        else:
            messages.success(
                request,
                _("Synced: %(delivered)s translation(s) received, %(pushed)s change(s) sent.")
                % {"delivered": report.delivered, "pushed": report.pushed},
            )
    return _back(request, ref)


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
    return _back(request, ref)


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
    return _back(request, ref)


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
    return _back(request, ref)
