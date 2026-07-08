from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0058_tenant_auto_approve_product_changes"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="shiprocketorder",
            constraint=models.UniqueConstraint(
                fields=("tenant", "woocommerce_order_id"),
                condition=models.Q(source="woocommerce") & ~models.Q(woocommerce_order_id=""),
                name="uniq_tenant_woocommerce_order_id",
            ),
        ),
    ]
