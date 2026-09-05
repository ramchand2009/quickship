from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0074_mobileorderconfirmation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="shiprocketorder",
            name="local_status",
            field=models.CharField(
                choices=[
                    ("waiting_order", "Waiting"),
                    ("new_order", "New Order"),
                    ("order_accepted", "Order Accepted"),
                    ("order_packed", "Order Packed"),
                    ("shipped", "Shipped"),
                    ("delivery_issue", "Delivery Issue"),
                    ("out_for_delivery", "Out for Delivery"),
                    ("delivered", "Delivered"),
                    ("completed", "Completed"),
                    ("order_cancelled", "Order Cancelled"),
                ],
                default="new_order",
                max_length=32,
            ),
        ),
    ]
