# 0001 — Editor and review workflow

Status: accepted (2026-09-28); implemented in 0.1.0.dev0 (see *Implementation*). Items marked
*[owner]* still need the owner's confirmation.
Cloud side: ponyglot-cloud ADR 0014. Core side (exclusions): ponyglot ADR 0002.

## Context

`djangocms-ponyglot` connects django CMS 5 (with djangocms-versioning) to Ponyglot Cloud through
the `ponyglot` core (adapter prefix `djangocms:`). Promise on ponyglot.app: "Nothing is
published automatically. CMS content lands as drafts. Editors approve."

## Building blocks

- **Unit** = a page's source-language `PageContent`. **Segments**: title, menu title, meta
  description and the text fields of every plugin (`plugin:<id>:<field>`), with `position` and
  `parent_key` so the target tree can mirror the source.
- **Target** = the page's `PageContent` in another language, with versions. djangocms-versioning
  allows one draft per page and language; published versions are read-only.
- **Approval = publishing** the target version.

## Decisions (owner, 2026-09-28)

1. **Source: the published version** by default; "Translate current draft" is an explicit action.
2. **Translation on request**: toolbar "Translate this page…" (languages, estimate for large
   jobs) and "Translate everything stale" (site-wide, admins). No automatic translation on
   publish in the first version.
3. **Pages are delivered in one piece** per language: a draft is only written once every
   segment of the page for that language is ready (cloud ADR 0014).
4. **Existing drafts are never overwritten.** If a person edited the target draft after the
   last delivery, the translation waits; the toolbar says "Translation waiting" with "Apply to
   current draft".
5. **Exclusions instead of "not now"**: pages (and any other content object) can be excluded
   from translation, for all or some languages, from the toolbar (core ADR 0002). Discarding a
   delivered draft reports `rejected`; as translation only happens on request, that doesn't
   loop.
6. **Non-matching plugin trees**: when the target tree no longer mirrors the source (an editor
   added, removed or moved plugins), aligned plugins are updated, the rest is listed with a
   warning ("3 source plugins have no counterpart"), and "Copy plugin tree from source"
   rebuilds the target draft's structure (texts translated, the rest copied).

## Editor workflow

1. **Status everywhere**: the toolbar's Ponyglot menu shows the page's matrix per language
   (✓ / stale / in review / missing / waiting for QA / QA issue).
2. **Request**: editor chooses languages; only missing and stale segments are sent.
3. **Delivery** (next sync, in one piece per language):
   - no target content yet → create it, copy the source plugin tree, fill in the translations;
     title, menu title and slug (derived from the title, as in the core) set;
   - published target exists → new draft from it, only changed plugins updated (earlier
     corrections in other plugins stay);
   - human-edited draft exists → wait (decision 4).
4. **Review in the CMS**: a sideframe shows source and translation side by side per segment,
   with QA warnings and, in "Editors fix" mode, QA errors to fix. Editors edit in the draft.
5. **Publish** → the connector (versioning `post_version_operation`, `OPERATION_PUBLISH`)
   compares the published texts with the delivered ones and reports `approved` per segment,
   including corrected text (feeds the translation memory).
6. **Source changes later** → only the changed segments turn stale; the next request touches
   only those plugins.

## QA errors: two modes (cloud ADR 0014)

| | **Editors fix** | **Separate QA** |
|---|---|---|
| For | small teams, editor = QA | agencies, translators, product teams with reviewers |
| QA error | delivered in the draft, marked as an error | held back in the dashboard until fixed, overridden or discarded |
| Editor sees | the error on the segment in the sideframe, with the reason | "1 segment waiting for quality check; your QA team has been notified" |
| Safeguard | publishing warns: "1 translation still has a QA error. Publish anyway?" | nothing with an error reaches the CMS |
| Afterwards | published text is checked again in the cloud; a remaining error shows in the dashboard as "published with QA issue" | as today |

The mode comes from the plan (cloud ADR 0014). The connector learns it from the handshake and
reads held-back segments and their reasons from the API, so editors always see why something
is missing.

## Notifications

- **Editors** (known to the connector): toolbar and sideframe always; optional email "Translation
  ready for review" / "QA issue in your draft" to the page's last editor through the site's own
  email setup (`PONYGLOT["NOTIFY_EDITORS"]`, default off) *[owner: default]*.
- **QA people** (known to the cloud): email when something needs attention, immediately or as a
  daily summary, per member setting; dashboard badge.

## Consequences

- Needs cloud work before the connector can deliver pages: whole-page delivery, QA modes,
  held-back segments in the API, flagged delivery, re-checking approved text, notifications.
- Needs core work: exclusions, QA error display for suggestions, editor notifications.

## Implementation (2026-09-28)

- **Stable plugin keys** (`keys.py`): djangocms-versioning copies plugins with new ids on every
  version, so segments use `plugin:<key>:<field>`. Keys follow copies (on draft creation) and
  are shared by a translated plugin and its source plugin; existing translated pages are
  matched by structure (slot, plugin type, tree position).
- djangocms-text's embedded child plugins travel as `<cms-plugin id="<key>"></cms-plugin>` and
  are mapped back to the target's own child plugins.
- Translations are tracked as core `Suggestion`s: `drafted` once written into a draft,
  `applied` (with the published text) on publish, `rejected` on discard/archive; `pending`
  while waiting for "Apply to current draft".
- A draft counts as untouched while the hash of its texts equals the one recorded at delivery
  (`DraftDelivery`).
- The status sideframe and actions are admin views (no URL configuration needed); reading the
  status calls the cloud with a 10-second timeout.
- Not yet: JSON-field plugins (djangocms-frontend), aliases/static placeholders, per-page email
  recipients.
