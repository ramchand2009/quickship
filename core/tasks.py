from celery import shared_task

from .system_status import write_system_heartbeat


@shared_task(name="core.tasks.celery_healthcheck")
def celery_healthcheck():
    write_system_heartbeat("celery_worker", {"source": "celery_healthcheck"})
    return {"ok": True}
