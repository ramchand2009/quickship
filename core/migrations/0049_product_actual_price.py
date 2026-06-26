from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0048_shiprocketorder_shipping_base_amount"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="actual_price",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
    ]
