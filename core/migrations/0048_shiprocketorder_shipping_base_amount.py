from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0047_product_description_prices"),
    ]

    operations = [
        migrations.AddField(
            model_name="shiprocketorder",
            name="shipping_base_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
    ]
