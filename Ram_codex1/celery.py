import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Ram_codex1.settings")

app = Celery("Ram_codex1")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
