from django.db import migrations


def seed_order_accepted_template(apps, schema_editor):
    WhatsAppStatusTemplateConfig = apps.get_model("core", "WhatsAppStatusTemplateConfig")
    config, created = WhatsAppStatusTemplateConfig.objects.get_or_create(
        local_status="order_accepted",
        defaults={
            "enabled": True,
            "template_name": "order_confirm_1",
            "template_id": "",
            "template_param_mapping": {},
        },
    )
    if created:
        return

    if config.template_name or config.template_id:
        return

    config.enabled = True
    config.template_name = "order_confirm_1"
    config.template_param_mapping = {}
    config.save(update_fields=["enabled", "template_name", "template_param_mapping", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0042_alter_whatsappsettings_api_key"),
    ]

    operations = [
        migrations.RunPython(seed_order_accepted_template, migrations.RunPython.noop),
    ]
