# Changelog

## Unreleased (0.1.0)

First release: Ponyglot for django CMS 5 (with djangocms-versioning). Depends on `ponyglot`.

### Content

- Any frontend-editable content type: page contents, blog posts or your own models, found
  through the CMS toolbar and djangocms-versioning (`PONYGLOT["DJANGOCMS"]["MODELS"]` to
  limit them).
- Both modes: one content object per language (pages, posts; published separately) and all
  languages in one object (published together); versioned and unversioned.
- Plugin fields to translate come from each plugin's admin form, for every plugin
  (djangocms-frontend's entangled fields included); `DJANGOCMS_TRANSLATIONS_CONF` and
  djangocms-deepl-translations' `DJANGOCMS_DEEPL_TRANSLATIONS_PLUGIN_CONFIG` declarations are
  respected, `PONYGLOT["DJANGOCMS"]["PLUGINS"]` wins.
- Alt texts, titles, ARIA labels and placeholders in HTML attribute fields (djangocms-frontend's
  "Advanced settings", e.g. the image plugin's alt text) are translated; other attributes
  aren't. Fields validated as slugs or by a pattern (e.g. djangocms-form-builder's field
  names) count as identifiers, not prose.
- `manage.py ponyglot_fields` lists the translated fields of every installed plugin type.
- Stable plugin keys across versions and languages, so plugin ids changing with every version
  don't break translations.

### Editor workflow

- **Publish right away** on request (cloud ADR 0022): a checkbox in the dialog and on the
  status page; the draft is published in the requesting editor's name when the translation
  arrives, for per-language content types, without QA errors, and if the draft holds only
  these translations.
- Translations arrive as drafts in their language. A draft only the connector wrote is updated
  in place; a draft someone edited is never overwritten: the translations wait for "Apply".
  Unversioned content is only written on "Apply" (writing would publish).
- Publishing reports approval, with the editor's corrections; discarding or archiving a
  delivered draft reports a rejection.
- Plugins new in the source are added to the translated drafts at the same place, with their
  children; plugins an editor removed from a translation stay removed. If the trees really
  differ, "Copy plugin tree from source" rebuilds the draft.
- Three-way merge of plugin trees: plugins added to or moved in the source are added or moved
  in the translations (moves an editor made in a translation win); plugins deleted from the
  source wait for "Delete here too" or "Keep" in the dialog. Publishing the source merges moves
  and text-free plugins right away.
- A translation that existed before Ponyglot, with plugins that differ from the source, isn't
  merged automatically: the dialog asks whether to add the translations to it or to start from
  a copy of the source plugins.
- Toolbar › *Ponyglot translations*: a dialog in the CMS modal, per language what to do next
  (translate, apply, review the draft), with the general actions in the modal footer.
  - It translates the version you're viewing, draft or published.
  - "Fetch translations now" appears when this content wasn't fetched in 15 minutes
    (`FETCH_AFTER_MINUTES`); opening the dialog then fetches by itself.
  - Links to the Ponyglot dashboard only where something needs doing there (texts held by a
    quality check); "in review" clears when the translation is published.
- A status page with the segment matrix, jobs and the plugin tree copy. When no sync ran
  lately, it offers "Sync now" (no management commands in the admin).
- CMS content comes first in the core's *Which documents need attention* (Translation
  dashboard), filterable by content type, each linking to its status page.
- Exclusions per content object and language.
- Discarding a source-language draft makes the next sync send the published version again.
