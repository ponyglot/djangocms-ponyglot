# djangocms-ponyglot

django CMS connector for Ponyglot: translate any frontend-editable content (pages, posts,
aliases, your own models) with glossary, translation memory and delta sync. With
djangocms-versioning, translations arrive as **drafts**; nothing is published until an editor
publishes it.

**Early access, alpha.** Requires Python 3.10+, django CMS 5.1+, Django 5.2+ and the
`ponyglot` core; djangocms-versioning 2.7+ is recommended. See https://ponyglot.app.

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

**Plugin fields** come from each plugin's **form**, as editors see it in the plugin admin (its
own form or the generated model form, limited by `fields`/`fieldsets`, without `exclude` and
read-only fields). What editors type into is what gets translated:

- plain text fields (`forms.CharField`), except hidden ones, those validated as a slug or by a
  pattern (identifiers, such as djangocms-form-builder's field names) and those named like
  identifiers, code, CSS selectors or classes, icons or anchors;
- rich text fields (`HTMLFormField`) as HTML;
- in HTML attribute fields (djangocms-attributes-field), the attributes that hold prose: `alt`,
  `title`, `aria-label`, `aria-description` and `placeholder`.

URL, slug, email, choice, link and attribute fields drop out by their form field type. This
covers **djangocms-frontend**: its entangled forms store texts in the `config` JSON field
(`config.heading`, `config.quote_content`, `config.name`, …), which are read and written in
place, as are the alt texts and titles in each plugin's "Advanced settings" attributes
(`config.attributes.alt`; the image plugin's alt text lives there). Code blocks, heading ids,
CSS classes and link targets aren't touched. Links embedded in rich text
(djangocms-link's `LinkPlugin`, djangocms-frontend's `TextLinkPlugin`) are translated inside
their sentence.

djangocms-translations' declarations win, so an existing `DJANGOCMS_TRANSLATIONS_CONF` works as
is:

```python
DJANGOCMS_TRANSLATIONS_CONF = {
    "TextPlugin": {"fields": ["body"]},
    "LinkPlugin": {"fields": ["name"], "text_field_child_label": "name"},
    "PicturePlugin": {"fields": ["caption_text"]},
}
```

- `fields`: exactly these fields; `excluded_fields`: leave these out. Names of JSON keys work
  (`"heading"` means `config.heading` for djangocms-frontend, `"attributes.title"` means
  `config.attributes.title`), or dotted paths.
- `text_field_child_label`: for plugins embedded in rich text (links in djangocms-text), this
  field is translated **inside the sentence** it appears in and written back to the plugin.

A project already set up for
[djangocms-deepl-translations](https://gitlab.com/kapt/open-source/djangocms-deepl-translations)
needs no second configuration: its `DJANGOCMS_DEEPL_TRANSLATIONS_PLUGIN_CONFIG` is read too.
`translatable_fields` (`"config/accordion_item_header"` or key tuples) become `fields`
(`config.accordion_item_header`), and `no_translation: True` translates nothing in that plugin.
Only the setting in your project is read; for plugins it doesn't list, the plugin forms decide.
If both packages' settings name a plugin type, `DJANGOCMS_TRANSLATIONS_CONF` wins.

To see what is translated, run `python manage.py ponyglot_fields` (optionally with plugin
type names, or `--translated` to leave out plugins without translated fields):

```text
HeadingPlugin
  config.heading
  config.attributes (alt, title, aria-label, aria-description, placeholder)
TextLinkPlugin  [config.name inside the text it's embedded in]
  config.name
  config.attributes (alt, title, aria-label, aria-description, placeholder)
```

**Content model fields** (one-object-per-language types): pages send title, menu title, page
title and meta description; other models their text fields except bookkeeping ones, or what
`DJANGOCMS["CONTENT_FIELDS"]` lists. A `slug` field follows the translated title.

## How it works

- **Stable plugin keys:** plugin ids change with every version, so plugins get keys that follow
  them across versions and languages. A translated plugin shares its source plugin's key.
  Content that is already translated is matched by structure (placeholder, plugin type,
  position in the tree), and its texts are imported on the first push.
- **Translate:** **Ponyglot translations** is the first entry of the toolbar's **Language**
  menu on any translated content. It opens a dialog with this content's state per language and
  the next step for each: **Translate**, **Apply to draft**, **Review draft** (opens the
  translated draft to check and publish), or nothing when Ponyglot is reviewing it. When
  everything is translated it says so. If no sync round ran within `FETCH_AFTER_MINUTES`,
  opening the dialog fetches this content's translations first (and pushes it if it changed).
  Large jobs ask for confirmation first.
- **Status details:** the dialog links to the full status page: per segment (✓ / stale / in
  review / missing / waiting for QA), jobs and the plugin tree copy.
- **Source:** the dialog translates the version you're viewing, draft or published; it says
  which.
- **Drafts:** a unit arrives in one piece per language. A draft this connector wrote and nobody
  touched is updated; a published version gets a new draft (only changed texts are updated, so
  earlier corrections stay); a draft someone edited is never overwritten. The menu entry
  shows "*n* to review" for languages with a draft or translations waiting.
- **Approve:** publishing reports the approval, including your corrections (they go into the
  translation memory). Discarding or archiving a delivered draft reports a rejection.
- **Source structure changes** are merged into the translations (three-way: the source tree
  at the last delivery, the source now, the translation now):
  - **added** plugins are copied in at the same place, with their children, together with
    their translations (right away if they have no texts, e.g. an image);
  - **moved** plugins (to another parent or another order) are moved, unless an editor moved
    them in the translation too: the editor's place wins;
  - **deleted** plugins are listed in the dialog, *Delete here too* or *Keep*; never deleted
    without confirmation;
  - plugins an editor removed from or added to a translation stay as they are.

  Moves and plugins without texts bring no new texts to translate, so publishing the source
  merges them right away (a new draft of each translation that has none an editor edited);
  content with all languages in one object merges with the next delivery.
- **Existing translations:** a language translated before Ponyglot is matched plugin by
  plugin: same placeholder, plugin type and position in the tree. If all source plugins have a
  counterpart, translations go into it (a new draft of it). If not, nothing is written until an
  editor chooses in the dialog: **Add to existing *language*** (keeps its plugins, fills in the
  translations where plugins match, in its draft or a new draft of it; texts without a
  counterpart keep waiting) or **Start from *source language*** (a draft with a translated copy
  of the source plugins). Either way, the published version stays until the draft is published.
- **Different structure:** texts without a counterpart in the translated plugin tree (e.g. a
  plugin an editor removed from the translation) keep waiting and are listed with **Copy plugin
  tree from source**.
- **QA:** depending on your plan, QA errors are shown next to the texts for editors to fix, or
  held back for a separate review; the status lists what is waiting and why.
- **Exclude:** keep content out of translation, for all or some languages.

## Publish right away

In the toolbar dialog and the status page, editors who may publish the content (djangocms-
versioning's publish permission) can tick **Publish the translation right away** before
translating (`PONYGLOT["PUBLISH_BY_DEFAULT"]` pre-ticks it). The translation is then written
into a draft as usual and published in the editor's name, if no QA error was found and the
draft holds only these translations. Only for content types with one content object per
language (pages, posts): shared content would publish every language at once. Otherwise the
draft waits for review, and the sync report says why.

## Settings

In `PONYGLOT["DJANGOCMS"]` (the core's settings apply too, see the `ponyglot` README):

| Key | Default | |
|---|---|---|
| `MODELS` | all frontend-editable models | e.g. `["cms.pagecontent", "djangocms_stories.postcontent"]` |
| `CONTENT_FIELDS` | see above | e.g. `{"djangocms_stories.postcontent": ["title", "abstract"]}` |
| `PLUGINS` | – | like `DJANGOCMS_TRANSLATIONS_CONF`; these entries win over it and `DJANGOCMS_DEEPL_TRANSLATIONS_PLUGIN_CONFIG` |
| `USER` | `"ponyglot"` | username of the (inactive) user that authors translation drafts |
| `FETCH_AFTER_MINUTES` | `15` | without a sync round this long, the dialog fetches its content's translations itself |

## Not yet supported

- Choosing email recipients per content object (the core's `NOTIFY_EDITORS` applies site-wide).

## Development

```sh
make install   # uv sync (the core from ../ponyglot)
make check     # ruff, migrations check, tests (SQLite)
```

The tests cover every combination: versioned and unversioned content types, each with one
content object per language and with all languages in one object (pages and a custom model;
a shared-language model; an unversioned per-language model; an unversioned model without a
grouper), including the toolbar on each. Design: [docs/decisions](docs/decisions/). License: BSD-3-Clause.
