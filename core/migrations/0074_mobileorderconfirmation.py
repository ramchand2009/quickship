from django.db import migrations, models
import django.db.models.deletion

import core.models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0073_mobilecustomerprofile"),
    ]

    operations = [
        migrations.CreateModel(
            name="MobileOrderConfirmation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.CharField(max_length=96, unique=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("awaiting", "Awaiting Confirmation"),
                            ("confirmed", "Confirmed"),
                            ("change_requested", "Change Requested"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="awaiting",
                        max_length=24,
                    ),
                ),
                ("customer_name", models.CharField(blank=True, max_length=160)),
                ("customer_phone", models.CharField(blank=True, max_length=32)),
                ("address_1", models.CharField(blank=True, max_length=255)),
                ("address_2", models.CharField(blank=True, max_length=255)),
                ("city", models.CharField(blank=True, max_length=120)),
                ("state", models.CharField(blank=True, max_length=120)),
                ("pincode", models.CharField(blank=True, max_length=20)),
                ("country", models.CharField(blank=True, default="India", max_length=120)),
                ("change_note", models.TextField(blank=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("change_requested_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "order",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mobile_confirmation",
                        to="core.shiprocketorder",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        default=core.models.get_default_tenant_pk,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mobile_order_confirmations",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["tenant", "status", "-created_at"], name="mobconfirm_status_idx"),
                    models.Index(fields=["token"], name="mobconfirm_token_idx"),
                ],
            },
        ),
    ]
