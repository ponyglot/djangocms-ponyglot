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
  (djangocms-frontend's entangled fields included); `DJANGOCMS_TRANSLATIONS_CONF`
  declarations are respected, `PONYGLOT["DJANGOCMS"]["PLUGINS"]` wins.
- Stable plugin keys across versions and languages, so plugin ids changing with every version
  don't break translations.

### Editor workflow

- Translations arrive as drafts in their language. A draft only the connector wrote is updated
  in place; a draft someone edited is never overwritten: the translations wait for "Apply".
  Unversioned content is only written on "Apply" (writing would publish).
- Publishing reports approval, with the editor's corrections; discarding or archiving a
  delivered draft reports a rejection.
- Plugins new in the source are added to the translated drafts at the same place, with their
  children; plugins an editor removed from a translation stay removed. If the trees really
  differ, "Copy plugin tree from source" rebuilds the draft.
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
