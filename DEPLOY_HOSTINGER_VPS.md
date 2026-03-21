# Hostinger VPS Docker Deployment

This project can now run on a Hostinger VPS with:

- `web`: Django + Gunicorn
- `worker`: WhatsApp queue worker
- `db`: PostgreSQL 16

## 1. Prepare the VPS

Install Docker and the Compose plugin on the VPS, then copy this project to the server.

Recommended open ports:

- `22` for SSH
- `80` for HTTP
- `443` for HTTPS
- `8000` only if you intentionally want to expose Django directly for testing

## 2. Create the production `.env`

Copy `.env.example` to `.env` and update it for production.

Minimum values to change:

```env
DJANGO_SECRET_KEY=replace-with-a-long-random-secret
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com,SERVER_IP
DJANGO_CSRF_TRUSTED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
DJANGO_SESSION_COOKIE_SECURE=true
DJANGO_CSRF_COOKIE_SECURE=true
DJANGO_SECURE_SSL_REDIRECT=false
DJANGO_USE_X_FORWARDED_PROTO=true

POSTGRES_DB=ram_codex1
POSTGRES_USER=ram_codex1
POSTGRES_PASSWORD=replace-with-a-strong-password
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_CONN_MAX_AGE=60
```

Also fill in your existing app settings such as:

- `SHIPROCKET_EMAIL`
- `SHIPROCKET_PASSWORD`
- `WHATOMATE_*`
- `WHATSAPP_ALERT_*`

## 3. Build and start the containers

From the project root on the VPS:

```bash
docker compose up -d --build
```

This does the following automatically for the `web` container:

- waits for Postgres
- runs `python manage.py migrate`
- runs `python manage.py collectstatic`
- starts Gunicorn on port `8000`

The worker container starts:

```bash
python manage.py run_whatsapp_queue_worker --interval 60 --limit 50 --worker docker
```

## 4. Migrate existing SQLite data to Postgres

If you want to move the current local SQLite data into the new PostgreSQL container, do this before final cutover.

### On the current project copy

Create a JSON export from SQLite:

```bash
python manage.py dumpdata --exclude contenttypes --exclude auth.permission --indent 2 > data.json
```

If you are doing this on Windows PowerShell:

```powershell
python manage.py dumpdata --exclude contenttypes --exclude auth.permission --indent 2 | Out-File -Encoding utf8 data.json
```

Copy `data.json` to the VPS project folder.

### On the VPS

Start the database first if needed:

```bash
docker compose up -d db
```

Load schema and import data:

```bash
docker compose run --rm web python manage.py migrate --noinput
docker compose run --rm -v "$(pwd)/data.json:/tmp/data.json:ro" web python manage.py loaddata /tmp/data.json
```

Then bring everything up:

```bash
docker compose up -d
```

## 5. Create a Django admin user

If you do not already have one in migrated data:

```bash
docker compose run --rm web python manage.py createsuperuser
```

## 6. Reverse proxy with Nginx on the VPS

Recommended production setup is Nginx on the host forwarding traffic to `127.0.0.1:8000`.

If you want to test Django directly without Nginx, temporarily change the Compose port mapping from `127.0.0.1:8000:8000` to `8000:8000`.

Example site config:

```nginx
server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

After HTTPS is configured, set:

```env
DJANGO_SECURE_SSL_REDIRECT=true
```

## 7. Useful Docker commands

View logs:

```bash
docker compose logs -f web
docker compose logs -f worker
docker compose logs -f db
```

Restart after config changes:

```bash
docker compose up -d --build
```

Run Django checks:

```bash
docker compose run --rm web python manage.py check
docker compose run --rm web python manage.py preflight_check
```

## 8. PostgreSQL backups

The existing `backup_local_data` command is SQLite-oriented. For the Docker Postgres deployment, use `pg_dump` instead:

```bash
docker compose exec db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > postgres_backup.sql
```

Restore example:

```bash
cat postgres_backup.sql | docker compose exec -T db psql -U "$POSTGRES_USER" "$POSTGRES_DB"
```

## 9. Cutover checklist

- Domain A record points to the Hostinger VPS IP
- `.env` has production hosts and secrets
- `docker compose up -d --build` completes successfully
- `/healthz/` loads through the domain
- Admin login works
- Worker logs show queue polling without errors
