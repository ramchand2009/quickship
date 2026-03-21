from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_shiprocketorder_manual_overrides"),
    ]

    operations = [
        migrations.AddField(
            model_name="shiprocketorder",
            name="local_status",
            field=models.CharField(
                choices=[
                    ("new_order", "New Order"),
                    ("shipped", "Shipped"),
                    ("out_for_delivery", "Out for Delivery"),
                    ("delivered", "Delivered"),
                ],
                default="new_order",
                max_length=32,
            ),
        ),
    ]
