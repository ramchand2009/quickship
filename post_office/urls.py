from django.urls import path

from .views import evidence, report, sandbox, webhook

urlpatterns = [
    path("sandbox/", sandbox, name="post_office_sandbox"),
    path("sandbox/evidence.json", evidence, name="post_office_evidence"),
    path("sandbox/report/", report, name="post_office_report"),
    path("webhook/", webhook, name="post_office_webhook"),
]
