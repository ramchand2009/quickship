# Easypanel Deployment

Use Easypanel with three services in the same project:

- `postgres`: Postgres service
- `web`: App service for Django + Gunicorn
- `worker`: App service for the WhatsApp queue worker

You do not need to run the local `docker-compose.yml` inside Easypanel. Easypanel can build the app directly from this repository's `Dockerfile`.

## 1. Create the Postgres service

In Easypanel:

1. Create a new project.
2. Add a `Postgres` service.
3. Set the database name, username, and password.
4. Copy the connection details shown by Easypanel for that database service.

Official docs:

- https://easypanel.io/docs/services/postgres

## 2. Create the `web` app service

Add an `App` service and connect it to your GitHub repo.

In the build settings:

- Source: your GitHub repository
- Build method: `Dockerfile`
- Dockerfile path: `Dockerfile`

Set the start command to:

```bash
gunicorn Ram_codex1.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120
```

Expose port:

- `8000`

Add your domain to this service in Easypanel.

Official docs:

- https://easypanel.io/docs/services/app
- https://easypanel.io/docs/quickstarts/django

## 3. Create the `worker` app service

Add a second `App` service from the same repository.

Use the same build method:

- Build method: `Dockerfile`
- Dockerfile path: `Dockerfile`

Set the start command to:

```bash
python manage.py run_whatsapp_queue_worker --interval 60 --limit 50 --worker easypanel
```

This service does not need a public domain.

## 4. Environment variables

Add the same environment variables to both `web` and `worker`.

Minimum recommended values:

```env
DJANGO_SECRET_KEY=replace-with-a-long-random-secret
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
DJANGO_SESSION_COOKIE_SECURE=true
DJANGO_CSRF_COOKIE_SECURE=true
DJANGO_SECURE_SSL_REDIRECT=true
DJANGO_USE_X_FORWARDED_PROTO=true

POSTGRES_DB=your_db_name
POSTGRES_USER=your_db_user
POSTGRES_PASSWORD=your_db_password
POSTGRES_HOST=your_postgres_host_from_easypanel
POSTGRES_PORT=5432
POSTGRES_CONN_MAX_AGE=60
```

Also add your existing app variables:

- `SHIPROCKET_EMAIL`
- `SHIPROCKET_PASSWORD`
- `WHATOMATE_*`
- `WHATSAPP_ALERT_*`
- `METRICS_TOKEN`

Note:

- Use the Postgres host/credentials shown by Easypanel for the database service.
- Easypanel makes environment variables available at build time and runtime.
- If you ever see stale database connection errors after the app sits idle, try `POSTGRES_CONN_MAX_AGE=0`. This is a Django-side adjustment based on Easypanel's container idle-connection behavior.

## 5. Migrations and static files

Before the first public release, open the Easypanel terminal for the `web` service and run:

```bash
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

You can also run:

```bash
python manage.py check
python manage.py preflight_check
```

## 6. Move existing SQLite data into Postgres

From your current project copy, export data:

```powershell
python manage.py dumpdata --exclude contenttypes --exclude auth.permission --indent 2 | Out-File -Encoding utf8 data.json
```

Upload `data.json` into the Easypanel `web` service container or project files, then run:

```bash
python manage.py loaddata data.json
```

Recommended order:

1. Create Postgres service.
2. Deploy `web`.
3. Run `migrate`.
4. Load `data.json`.
5. Start `worker`.

## 7. Domain and SSL

Attach your domain to the `web` service in Easypanel and enable SSL there.

After SSL is active, keep:

```env
DJANGO_SECURE_SSL_REDIRECT=true
DJANGO_USE_X_FORWARDED_PROTO=true
```

## 8. Notes for this repo

Relevant files already prepared for this deployment:

- `Dockerfile`
- `Ram_codex1/settings.py`
- `.env.example`

`docker-compose.yml` is still useful for local testing or plain VPS Docker deployments, but for Easypanel the recommended path is creating the three services in the UI.
