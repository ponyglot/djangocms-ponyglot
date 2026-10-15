"""`manage.py ponyglot_fields`: which fields of each installed plugin type are translated."""

from cms.plugin_pool import plugin_pool
from django.core.management.base import BaseCommand

from djangocms_ponyglot import conf
from djangocms_ponyglot.fields import PROSE_ATTRIBUTES, child_label_field, plugin_fields


def describe(plugin_class):
    """`(paths as shown, notes)` for a plugin type; paths are grouped per attribute field."""
    instance = plugin_class.model(plugin_type=plugin_class.__name__)
    specs = plugin_fields(instance)
    attribute_fields = {
        path.rpartition(".")[0]
        for path in specs
        if path.rpartition(".")[2] in PROSE_ATTRIBUTES
        and all(f"{path.rpartition('.')[0]}.{name}" in specs for name in PROSE_ATTRIBUTES)
    }
    shown = [
        f"{path} (html)" if spec.html else path
        for path, spec in specs.items()
        if path.rpartition(".")[0] not in attribute_fields
    ]
    shown += [f"{field} ({', '.join(PROSE_ATTRIBUTES)})" for field in sorted(attribute_fields)]
    notes = []
    if "fields" in conf.plugin_conf(plugin_class.__name__):
        notes.append("declared")
    label = child_label_field(instance)
    if label:
        notes.append(f"{label} inside the text it's embedded in")
    return shown, notes


class Command(BaseCommand):
    help = (
        "List the fields Ponyglot translates for each installed plugin type: from the plugin's "
        "form, or as declared in DJANGOCMS_TRANSLATIONS_CONF, "
        "DJANGOCMS_DEEPL_TRANSLATIONS_PLUGIN_CONFIG or PONYGLOT['DJANGOCMS']['PLUGINS']."
    )

    def add_arguments(self, parser):
        parser.add_argument("plugin_types", nargs="*", help="only these plugin types")
        parser.add_argument(
            "--translated", action="store_true", help="only plugin types with translated fields"
        )

    def handle(self, *args, plugin_types, translated, **options):
        wanted = {name.lower() for name in plugin_types}
        for plugin_class in sorted(plugin_pool.get_all_plugins(), key=lambda p: p.__name__):
            name = plugin_class.__name__
            if wanted and name.lower() not in wanted:
                continue
            try:
                shown, notes = describe(plugin_class)
            except Exception as error:  # noqa: BLE001 (report and go on)
                self.stdout.write(self.style.WARNING(f"{name}: can't inspect ({error})"))
                continue
            if translated and not shown:
                continue
            suffix = f"  [{'; '.join(notes)}]" if notes else ""
            self.stdout.write(self.style.MIGRATE_HEADING(name) + suffix)
            for line in shown or ["–"]:
                self.stdout.write(f"  {line}")
