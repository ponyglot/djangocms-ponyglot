from django.db import migrations


def keep_latest(apps, schema_editor):
    """One delivery per unit and language: the latest, with the plugin keys of all."""
    DraftDelivery = apps.get_model("djangocms_ponyglot", "DraftDelivery")
    latest = {}
    for delivery in DraftDelivery.objects.order_by("-delivered_at", "-pk"):
        group = (delivery.external_key, delivery.language)
        if group not in latest:
            latest[group] = (delivery, set(delivery.plugin_keys))
        else:
            latest[group][1].update(delivery.plugin_keys)
            delivery.delete()
    for delivery, keys in latest.values():
        if keys != set(delivery.plugin_keys):
            delivery.plugin_keys = sorted(keys)
            delivery.save(update_fields=["plugin_keys"])


class Migration(migrations.Migration):
    dependencies = [
        ("djangocms_ponyglot", "0002_delivery_plugin_keys"),
    ]

    operations = [migrations.RunPython(keep_latest, migrations.RunPython.noop)]
