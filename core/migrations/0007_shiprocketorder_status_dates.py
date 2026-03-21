from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_shiprocketorder_local_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="shiprocketorder",
            name="delivered_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="shiprocketorder",
            name="out_for_delivery_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="shiprocketorder",
            name="shipped_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
