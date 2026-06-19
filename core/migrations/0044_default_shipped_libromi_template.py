from django.db import migrations


def seed_shipped_template(apps, schema_editor):
    WhatsAppStatusTemplateConfig = apps.get_model("core", "WhatsAppStatusTemplateConfig")
    config, created = WhatsAppStatusTemplateConfig.objects.get_or_create(
        local_status="shipped",
        defaults={
            "enabled": True,
            "template_name": "order_shipped_1",
            "template_id": "",
            "template_param_mapping": {
                "1": "channel_order_id",
                "2": "tracking_number",
            },
        },
    )
    if created:
        return

    if config.template_name or config.template_id:
        return

    config.enabled = True
    config.template_name = "order_shipped_1"
    config.template_param_mapping = {
        "1": "channel_order_id",
        "2": "tracking_number",
    }
    config.save(update_fields=["enabled", "template_name", "template_param_mapping", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0043_default_order_accepted_libromi_template"),
    ]

    operations = [
        migrations.RunPython(seed_shipped_template, migrations.RunPython.noop),
    ]
