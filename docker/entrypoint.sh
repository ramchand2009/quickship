#!/bin/sh
set -eu

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

exec "$@"
