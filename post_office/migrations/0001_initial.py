from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SandboxRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=32)),
                ("status", models.CharField(max_length=16)),
                ("http_status", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("summary", models.TextField(blank=True)),
                ("response_preview", models.TextField(blank=True)),
                ("created_by", models.CharField(blank=True, max_length=150)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at", "-pk"]},
        ),
        migrations.CreateModel(
            name="PostOfficeWebhookEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fingerprint", models.CharField(max_length=64, unique=True)),
                ("article_number", models.CharField(max_length=50)),
                ("event_code", models.CharField(blank=True, max_length=100)),
                ("event_description", models.CharField(blank=True, max_length=255)),
                ("event_date", models.CharField(blank=True, max_length=30)),
                ("event_time", models.CharField(blank=True, max_length=30)),
                ("event_office_name", models.CharField(blank=True, max_length=160)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-received_at", "-pk"]},
        ),
    ]
