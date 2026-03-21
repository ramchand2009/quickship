import json
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone


def _heartbeat_dir():
    base_dir = Path(getattr(settings, "BASE_DIR"))
    directory = base_dir / "logs" / "heartbeats"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_system_heartbeat(name, metadata=None):
    heartbeat_name = str(name or "").strip().lower()
    if not heartbeat_name:
        return
    payload = {
        "name": heartbeat_name,
        "last_run_at": timezone.localtime(timezone.now()).isoformat(),
        "metadata": metadata if isinstance(metadata, dict) else {},
    }
    path = _heartbeat_dir() / f"{heartbeat_name}.json"
    try:
        path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    except OSError:
        return


def _parse_timestamp(raw_value):
    text = str(raw_value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return timezone.localtime(parsed)


def _read_system_heartbeat(name):
    heartbeat_name = str(name or "").strip().lower()
    if not heartbeat_name:
        return {}
    path = _heartbeat_dir() / f"{heartbeat_name}.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _build_status_entry(name, stale_after_minutes):
    payload = _read_system_heartbeat(name)
    last_run_at = _parse_timestamp(payload.get("last_run_at"))
    now = timezone.localtime(timezone.now())
    age_minutes = None
    if last_run_at:
        age_minutes = max(0, int((now - last_run_at).total_seconds() // 60))
    is_recent = bool(last_run_at and age_minutes is not None and age_minutes <= int(stale_after_minutes))
    return {
        "last_run_at": last_run_at,
        "last_run_text": last_run_at.strftime("%Y-%m-%d %H:%M:%S %Z") if last_run_at else "Never",
        "age_minutes": age_minutes,
        "is_recent": is_recent,
    }


def get_dashboard_system_status():
    return {
        "worker": _build_status_entry("queue_worker", stale_after_minutes=5),
        "alerts": _build_status_entry("queue_alerts", stale_after_minutes=30),
        "backup": _build_status_entry("nightly_backup", stale_after_minutes=60 * 36),
    }
