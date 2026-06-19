from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0044_default_shipped_libromi_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="shiprocketorder",
            name="payment_received_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="whatsappnotificationlog",
            name="trigger",
            field=models.CharField(
                choices=[
                    ("status_change", "Status Change"),
                    ("resend", "Resend"),
                    ("payment_reminder", "Payment Reminder"),
                    ("test_message", "Test Message"),
                    ("test_template", "Test Template"),
                    ("webhook_status", "Webhook Status"),
                    ("webhook_incoming", "Webhook Incoming"),
                ],
                default="status_change",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="whatsappnotificationqueue",
            name="trigger",
            field=models.CharField(
                choices=[
                    ("status_change", "Status Change"),
                    ("resend", "Resend"),
                    ("payment_reminder", "Payment Reminder"),
                    ("test_message", "Test Message"),
                    ("test_template", "Test Template"),
                    ("webhook_status", "Webhook Status"),
                    ("webhook_incoming", "Webhook Incoming"),
                ],
                max_length=32,
            ),
        ),
    ]
