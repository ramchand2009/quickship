from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_shiprocketorder_status_dates"),
    ]

    operations = [
        migrations.AddField(
            model_name="shiprocketorder",
            name="completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="shiprocketorder",
            name="local_status",
            field=models.CharField(
                choices=[
                    ("new_order", "New Order"),
                    ("shipped", "Shipped"),
                    ("out_for_delivery", "Out for Delivery"),
                    ("delivered", "Delivered"),
                    ("completed", "Completed"),
                ],
                default="new_order",
                max_length=32,
            ),
        ),
    ]
