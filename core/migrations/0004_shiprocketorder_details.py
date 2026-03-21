from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_shiprocketorder"),
    ]

    operations = [
        migrations.AddField(
            model_name="shiprocketorder",
            name="billing_address",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="shiprocketorder",
            name="order_items",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="shiprocketorder",
            name="shipping_address",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
