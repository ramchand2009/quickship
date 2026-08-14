from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0070_mobiledevice_mobilenotification_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="shiprocketorder",
            name="package_weight_kg",
            field=models.DecimalField(decimal_places=3, default=0, max_digits=8),
        ),
    ]
