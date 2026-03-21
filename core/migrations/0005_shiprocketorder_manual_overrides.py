from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_shiprocketorder_details"),
    ]

    operations = [
        migrations.AddField(
            model_name="shiprocketorder",
            name="manual_customer_alternate_phone",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="shiprocketorder",
            name="manual_customer_email",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="shiprocketorder",
            name="manual_customer_name",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="shiprocketorder",
            name="manual_customer_phone",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="shiprocketorder",
            name="manual_shipping_address_1",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="shiprocketorder",
            name="manual_shipping_address_2",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="shiprocketorder",
            name="manual_shipping_city",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="shiprocketorder",
            name="manual_shipping_country",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="shiprocketorder",
            name="manual_shipping_pincode",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="shiprocketorder",
            name="manual_shipping_state",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
