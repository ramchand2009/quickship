from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0059_unique_tenant_woocommerce_order"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="whatsappnotificationqueue",
            constraint=models.UniqueConstraint(
                fields=("tenant", "idempotency_key"),
                condition=models.Q(status__in=["pending", "retrying", "processing"])
                & ~models.Q(idempotency_key=""),
                name="uniq_active_whatsapp_queue_idempotency",
            ),
        ),
    ]
