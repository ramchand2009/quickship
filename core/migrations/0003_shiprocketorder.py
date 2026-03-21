from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_contactmessage"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShiprocketOrder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("shiprocket_order_id", models.CharField(max_length=64, unique=True)),
                ("channel_order_id", models.CharField(blank=True, max_length=128)),
                ("customer_name", models.CharField(blank=True, max_length=160)),
                ("customer_email", models.EmailField(blank=True, max_length=254)),
                ("customer_phone", models.CharField(blank=True, max_length=32)),
                ("status", models.CharField(blank=True, max_length=64)),
                ("payment_method", models.CharField(blank=True, max_length=64)),
                ("total", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("order_date", models.DateTimeField(blank=True, null=True)),
                ("raw_payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-order_date", "-updated_at"],
            },
        ),
    ]
