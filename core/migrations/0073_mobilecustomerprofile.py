from django.db import migrations, models
import django.db.models.deletion

import core.models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0071_shiprocketorder_package_weight_kg"),
    ]

    operations = [
        migrations.CreateModel(
            name="MobileCustomerProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=160)),
                ("phone", models.CharField(blank=True, max_length=32)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("address_1", models.CharField(blank=True, max_length=255)),
                ("address_2", models.CharField(blank=True, max_length=255)),
                ("city", models.CharField(blank=True, max_length=120)),
                ("state", models.CharField(blank=True, max_length=120)),
                ("country", models.CharField(blank=True, default="India", max_length=120)),
                ("pincode", models.CharField(blank=True, max_length=20)),
                ("created_by", models.CharField(blank=True, max_length=150)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "tenant",
                    models.ForeignKey(
                        default=core.models.get_default_tenant_pk,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mobile_customer_profiles",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "ordering": ["name", "-updated_at"],
                "indexes": [
                    models.Index(fields=["tenant", "phone"], name="mobcust_tenant_phone_idx"),
                    models.Index(fields=["tenant", "name"], name="mobcust_tenant_name_idx"),
                ],
            },
        ),
    ]
