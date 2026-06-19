from django.db import migrations


def seed_completed_template(apps, schema_editor):
    WhatsAppStatusTemplateConfig = apps.get_model("core", "WhatsAppStatusTemplateConfig")
    config, created = WhatsAppStatusTemplateConfig.objects.get_or_create(
        local_status="completed",
        defaults={
            "enabled": True,
            "template_name": "order_delivered",
            "template_id": "",
            "template_param_mapping": {
                "1": "channel_order_id",
            },
        },
    )
    if created:
        return

    if config.template_name or config.template_id:
        return

    config.enabled = True
    config.template_name = "order_delivered"
    config.template_param_mapping = {
        "1": "channel_order_id",
    }
    config.save(update_fields=["enabled", "template_name", "template_param_mapping", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0045_shiprocketorder_payment_received_at_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_completed_template, migrations.RunPython.noop),
    ]
