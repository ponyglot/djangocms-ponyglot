# djangocms-ponyglot

django CMS connector for Ponyglot: translate pages and plugins with glossary, translation
memory and delta sync. Translations arrive as **djangocms-versioning drafts**; nothing is
published until an editor publishes the draft.

**Early access, pre-alpha.** Requires django CMS 5.1+, djangocms-versioning 2.7+, Django 5.2+
and the `ponyglot` core. See https://ponyglot.app.

## Install

```sh
pip install djangocms-ponyglot
```

```python
INSTALLED_APPS += [
    "ponyglot",
    "djangocms_ponyglot",
    # "ponyglot.contrib.parler",   # e.g. for djangocms-stories / blog models
]
PONYGLOT = {
    "API_KEY": env("PONYGLOT_API_KEY"),
    "SOURCE_LANGUAGE": "en",
}
```

```sh
python manage.py migrate
python manage.py ponyglot check
python manage.py ponyglot backfill     # first push of all published pages
```

Run `python manage.py ponyglot sync` periodically (cron or `--loop 60` as a worker): it pushes
changed pages, fetches translations into drafts and reports your decisions.

## How it works

- **What is sent:** the published source-language version of each page: title, menu title,
  page title, meta description and the text fields of every plugin (URLs, slugs and choice
  fields are skipped). Publishing the source again sends only what changed.
- **Stable plugin keys:** plugin ids change with every version, so plugins get keys that
  follow them across versions and languages. A translated plugin shares its source plugin's
  key. Existing translated pages are matched by structure (placeholder, plugin type, position
  in the tree), and their texts are imported on the first push.
- **Translate:** the toolbar's **Ponyglot** menu opens the page's status (✓ / stale / in review
  / missing / waiting for QA) and **Translate what changed** for the languages you choose, from
  the published version or the current draft. Large jobs ask for confirmation first.
- **Drafts:** a page arrives in one piece per language:
  - no content in that language yet: a draft is created with the source's plugin tree, the
    translated texts and a slug derived from the translated title;
  - a published translation: a new draft, updating only the texts that changed (your earlier
    corrections elsewhere stay);
  - a draft someone edited: nothing is overwritten. The menu shows "*n* waiting" and the status
    offers **Apply to current draft**.
- **Approve:** publishing the draft reports your approval, including your corrections, which
  go into the translation memory. Discarding or archiving the draft reports a rejection.
- **Different structure:** if an editor added, removed or moved plugins in a translation,
  texts without a counterpart are listed with **Copy plugin tree from source**.
- **QA:** depending on your plan, QA errors are shown next to the texts for editors to fix, or
  held back for a separate review; the status lists what is waiting and why.
- **Exclude:** keep a page out of translation, for all or some languages, from the status page.

## Settings

The core's settings apply (`ponyglot` README). In addition:

| Key | Default | |
|---|---|---|
| `EXCLUDE_PLUGINS` | `[]` | plugin types never translated, e.g. `["SnippetPlugin"]` |
| `DJANGOCMS_USER` | `"ponyglot"` | username of the (inactive) user that authors translation drafts |

Plugin fields can be excluded with the core's `EXCLUDE_FIELDS` (`"app_label.model.field"`).

## Not yet supported

- Text inside JSON fields (djangocms-frontend's `config`), aliases and static placeholders.
- Choosing email recipients per page (the core's `NOTIFY_EDITORS` applies site-wide).

## Development

```sh
make install   # uv sync (the core from ../ponyglot)
make check     # ruff, migrations check, tests (SQLite)
```

Design: [docs/decisions](docs/decisions/). License: BSD-3-Clause. Contact: hello@ponyglot.app
