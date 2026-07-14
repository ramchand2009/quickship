#!/bin/sh
set -eu

verify_static_files() {
  python - <<'PY'
import os
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Ram_codex1.settings")

from django.conf import settings

static_root = Path(settings.STATIC_ROOT)
required_files = [
    "assets/css/style.css",
    "css/site.css",
    "assets/images/mathukai-logo.png",
    "assets/js/pcoded.js",
]

missing = [str(static_root / rel_path) for rel_path in required_files if not (static_root / rel_path).exists()]

if missing:
    raise SystemExit(
        "Collected static files are missing from STATIC_ROOT. "
        "Missing: " + ", ".join(missing)
    )

print(f"Verified collected static files in {static_root}")
PY
}

if [ -n "${POSTGRES_HOST:-}" ]; then
  python -c "import os, socket, time; host=os.environ['POSTGRES_HOST']; port=int(os.environ.get('POSTGRES_PORT', '5432')); deadline=time.time()+60
while True:
    try:
        with socket.create_connection((host, port), timeout=2):
            break
    except OSError:
        if time.time() >= deadline:
            raise SystemExit('Postgres is not reachable within 60 seconds.')
        time.sleep(1)"
fi

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  python manage.py migrate --noinput
fi

if [ "${COLLECT_STATIC:-1}" = "1" ]; then
  python manage.py collectstatic --noinput
fi

if [ "${VERIFY_STATICFILES:-1}" = "1" ]; then
  verify_static_files
fi

exec "$@"
