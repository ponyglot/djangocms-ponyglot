# djangocms-ponyglot

django CMS connector for Ponyglot: translate any frontend-editable content (pages, posts,
aliases, your own models) with glossary, translation memory and delta sync. With
djangocms-versioning, translations arrive as **drafts**; nothing is published until an editor
publishes it.

**Early access, pre-alpha.** Requires django CMS 5.1+, Django 5.2+ and the `ponyglot` core;
djangocms-versioning 2.7+ is recommended. See https://ponyglot.app.

## Install

```sh
pip install "djangocms-ponyglot[versioning]"
```

```python
INSTALLED_APPS += [
    "ponyglot",
    "djangocms_ponyglot",
    # "ponyglot.contrib.parler",   # model fields translated with django-parler
]
PONYGLOT = {
    "API_KEY": env("PONYGLOT_API_KEY"),
    "SOURCE_LANGUAGE": "en",
}
```

```sh
python manage.py migrate
python manage.py ponyglot check
python manage.py ponyglot backfill     # first push of all published content
```

Run `python manage.py ponyglot sync` periodically (cron, or `--loop 60` as a worker).

## What is translated

Every model registered for frontend editing (`cms_toolbar_enabled_models` in a `cms_config.py`),
e.g. `cms.PageContent`, djangocms-stories' `PostContent`, djangocms-alias' `AliasContent` and
your own content models. Limit them with `DJANGOCMS["MODELS"]`.

A unit is the content's **grouper** (the page, the post, …). Two kinds of content types:

| | One content object per language | All languages in one content object |
|---|---|---|
| Recognised by | `language` is a versioning grouping field | no `language` grouping |
| Examples | pages, posts, aliases | custom models whose plugins carry their language |
| Sent | content model fields (title, …) and plugin fields | plugin fields |
| Translation arrives as | a draft of that language's content (created if missing) | the target-language plugins in the object's draft |
| Published | per language | all languages together |

Content types **without versioning** get no automatic writes (writing would publish):
translations wait until an editor chooses *Apply*, which writes them live.

**Plugin fields** follow djangocms-translations' declarations, so an existing
`DJANGOCMS_TRANSLATIONS_CONF` works as is:

```python
DJANGOCMS_TRANSLATIONS_CONF = {
    "TextPlugin": {"fields": ["body"]},
    "LinkPlugin": {"fields": ["name"], "text_field_child_label": "name"},
    "PicturePlugin": {"fields": ["caption_text"]},
}
```

- `fields`: exactly these fields; `excluded_fields`: leave these out. Without a declaration:
  the plugin's text fields, except slugs, URLs, emails and fields with choices.
- `text_field_child_label`: for plugins embedded in rich text (links in djangocms-text), this
  field is translated **inside the sentence** it appears in and written back to the plugin.

**Content model fields** (one-object-per-language types): pages send title, menu title, page
title and meta description; other models their text fields except bookkeeping ones, or what
`DJANGOCMS["CONTENT_FIELDS"]` lists. A `slug` field follows the translated title.

## How it works

- **Stable plugin keys:** plugin ids change with every version, so plugins get keys that follow
  them across versions and languages. A translated plugin shares its source plugin's key.
  Content that is already translated is matched by structure (placeholder, plugin type,
  position in the tree), and its texts are imported on the first push.
- **Translate:** the toolbar's **Ponyglot** menu, on any translated content, opens the status
  (✓ / stale / in review / missing / waiting for QA) and **Translate what changed**, from the
  published version or the current draft. Large jobs ask for confirmation first.
- **Drafts:** a unit arrives in one piece per language. A draft this connector wrote and nobody
  touched is updated; a published version gets a new draft (only changed texts are updated, so
  earlier corrections stay); a draft someone edited is never overwritten. The menu shows
  "*n* waiting" and the status offers **Apply to current draft**.
- **Approve:** publishing reports the approval, including your corrections (they go into the
  translation memory). Discarding or archiving a delivered draft reports a rejection.
- **Different structure:** texts without a counterpart in the translated plugin tree are listed
  with **Copy plugin tree from source**.
- **QA:** depending on your plan, QA errors are shown next to the texts for editors to fix, or
  held back for a separate review; the status lists what is waiting and why.
- **Exclude:** keep content out of translation, for all or some languages.

## Settings

In `PONYGLOT["DJANGOCMS"]` (the core's settings apply too, see the `ponyglot` README):

| Key | Default | |
|---|---|---|
| `MODELS` | all frontend-editable models | e.g. `["cms.pagecontent", "djangocms_stories.postcontent"]` |
| `CONTENT_FIELDS` | see above | e.g. `{"djangocms_stories.postcontent": ["title", "abstract"]}` |
| `PLUGINS` | – | like `DJANGOCMS_TRANSLATIONS_CONF`; these entries win |
| `USER` | `"ponyglot"` | username of the (inactive) user that authors translation drafts |

## Not yet supported

- Text inside JSON fields (djangocms-frontend's `config`).
- Choosing email recipients per content object (the core's `NOTIFY_EDITORS` applies site-wide).

## Development

```sh
make install   # uv sync (the core from ../ponyglot)
make check     # ruff, migrations check, tests (SQLite)
```

The tests cover pages, a per-language custom model, a shared-language model and an unversioned
model. Design: [docs/decisions](docs/decisions/). License: BSD-3-Clause.
