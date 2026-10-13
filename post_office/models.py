from django.db import models


class SandboxRun(models.Model):
    action = models.CharField(max_length=32)
    status = models.CharField(max_length=16)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    summary = models.TextField(blank=True)
    response_preview = models.TextField(blank=True)
    created_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]


class PostOfficeWebhookEvent(models.Model):
    fingerprint = models.CharField(max_length=64, unique=True)
    article_number = models.CharField(max_length=50)
    event_code = models.CharField(max_length=100, blank=True)
    event_description = models.CharField(max_length=255, blank=True)
    event_date = models.CharField(max_length=30, blank=True)
    event_time = models.CharField(max_length=30, blank=True)
    event_office_name = models.CharField(max_length=160, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at", "-pk"]
