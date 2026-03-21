import json
from urllib import error, request

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import resolve, reverse

from core.shiprocket import ShiprocketAPIError, _get_auth_token
from core.whatomate import WhatomateNotificationError, check_api_connection


class Command(BaseCommand):
    help = "Run integration smoke checks for Shiprocket, Whatomate, and webhook endpoint wiring."

    def add_arguments(self, parser):
        parser.add_argument(
            "--base-url",
            type=str,
            default="",
            help="Optional public base URL (e.g. https://ops.example.com) to probe GET /webhooks/whatomate/.",
        )
        parser.add_argument("--skip-shiprocket", action="store_true", help="Skip Shiprocket auth smoke check.")
        parser.add_argument("--skip-whatomate", action="store_true", help="Skip Whatomate API smoke check.")
        parser.add_argument("--skip-webhook-http", action="store_true", help="Skip HTTP GET webhook probe.")
        parser.add_argument(
            "--allow-fail",
            action="store_true",
            help="Exit with success even if one or more checks fail.",
        )

    def handle(self, *args, **options):
        failures = []
        base_url = str(options.get("base_url") or "").strip().rstrip("/")

        if not options.get("skip_shiprocket"):
            self._run_shiprocket_check(failures)
        else:
            self.stdout.write(self.style.WARNING("SKIP shiprocket_auth"))

        if not options.get("skip_whatomate"):
            self._run_whatomate_check(failures)
        else:
            self.stdout.write(self.style.WARNING("SKIP whatomate_connection"))

        self._run_webhook_route_check(failures)
        if base_url and not options.get("skip_webhook_http"):
            self._run_webhook_http_check(base_url=base_url, failures=failures)
        elif options.get("skip_webhook_http"):
            self.stdout.write(self.style.WARNING("SKIP webhook_http_probe"))
        else:
            self.stdout.write(self.style.WARNING("SKIP webhook_http_probe (no --base-url provided)"))

        webhook_token_configured = bool(str(getattr(settings, "WHATOMATE_WEBHOOK_TOKEN", "") or "").strip())
        if webhook_token_configured:
            self.stdout.write(self.style.SUCCESS("OK webhook_token_configured"))
        else:
            self.stdout.write(self.style.WARNING("WARN webhook_token_configured: missing WHATOMATE_WEBHOOK_TOKEN"))

        if failures:
            summary = f"Integration smoke failed ({len(failures)}): " + "; ".join(failures)
            if options.get("allow_fail"):
                self.stdout.write(self.style.WARNING(summary))
                return
            raise CommandError(summary)

        self.stdout.write(self.style.SUCCESS("Integration smoke passed."))

    def _run_shiprocket_check(self, failures):
        email = str(getattr(settings, "SHIPROCKET_EMAIL", "") or "").strip()
        password = str(getattr(settings, "SHIPROCKET_PASSWORD", "") or "").strip()
        if not email or not password:
            message = "Shiprocket credentials missing (SHIPROCKET_EMAIL / SHIPROCKET_PASSWORD)."
            failures.append(message)
            self.stdout.write(self.style.ERROR(f"FAIL shiprocket_auth: {message}"))
            return
        try:
            token = _get_auth_token()
        except ShiprocketAPIError as exc:
            failures.append(f"shiprocket_auth: {exc}")
            self.stdout.write(self.style.ERROR(f"FAIL shiprocket_auth: {exc}"))
            return
        self.stdout.write(self.style.SUCCESS(f"OK shiprocket_auth: token_received={bool(token)}"))

    def _run_whatomate_check(self, failures):
        try:
            result = check_api_connection()
        except WhatomateNotificationError as exc:
            failures.append(f"whatomate_connection: {exc}")
            self.stdout.write(self.style.ERROR(f"FAIL whatomate_connection: {exc}"))
            return
        endpoint = ""
        if isinstance(result, dict):
            endpoint = str(result.get("endpoint") or "").strip()
        self.stdout.write(self.style.SUCCESS(f"OK whatomate_connection: endpoint={endpoint or '-'}"))

    def _run_webhook_route_check(self, failures):
        try:
            webhook_path = reverse("whatomate_webhook")
            match = resolve(webhook_path)
            resolved_name = str(match.url_name or "").strip()
        except Exception as exc:
            failures.append(f"webhook_route: {exc}")
            self.stdout.write(self.style.ERROR(f"FAIL webhook_route: {exc}"))
            return
        self.stdout.write(self.style.SUCCESS(f"OK webhook_route: path={webhook_path} name={resolved_name}"))

    def _run_webhook_http_check(self, *, base_url, failures):
        webhook_path = reverse("whatomate_webhook")
        url = f"{base_url}{webhook_path}"
        try:
            with request.urlopen(url, timeout=10) as response:
                raw_body = response.read().decode("utf-8", errors="ignore")
                status_code = int(getattr(response, "status", 0) or 0)
                parsed = json.loads(raw_body) if raw_body else {}
        except (error.URLError, error.HTTPError, json.JSONDecodeError) as exc:
            failures.append(f"webhook_http_probe: {exc}")
            self.stdout.write(self.style.ERROR(f"FAIL webhook_http_probe: {exc}"))
            return

        ok_flag = bool(parsed.get("ok")) if isinstance(parsed, dict) else False
        if status_code == 200 and ok_flag:
            self.stdout.write(self.style.SUCCESS(f"OK webhook_http_probe: {url}"))
            return
        message = f"Unexpected response status={status_code} body={parsed}"
        failures.append(f"webhook_http_probe: {message}")
        self.stdout.write(self.style.ERROR(f"FAIL webhook_http_probe: {message}"))
