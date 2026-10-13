import hashlib
import hmac
import json
import os
import re
from xml.etree import ElementTree

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache

from core.access import is_super_admin

from . import client
from .models import PostOfficeWebhookEvent, SandboxRun


PIN = re.compile(r"^\d{6}$")
ARTICLE = re.compile(r"^[A-Za-z0-9]{13}$")
SENSITIVE_PARTS = ("token", "password", "secret", "mobile", "email", "address", "name")
BOOKING_TEMPLATE = json.dumps({"articles": [{
    "bulk_customer_id": "YOUR_SANDBOX_CUSTOMER_ID",
    "contract_id": "YOUR_SANDBOX_CONTRACT_ID",
    "barcode_no": "YOUR_ASSIGNED_AWB",
    "pickup_or_dropoff": "DROPOFF",
    "pickup_dropoff_office_id": "YOUR_ASSIGNED_OFFICE_ID",
    "article_type": "SP_INLAND_PARCEL",
    "physical_weight": 550,
    "shape_of_article": "NROL",
    "length": 20,
    "breadth_diameter": 12,
    "height": 9,
    "sender_name": "Sample Sender",
    "sender_add_line_1": "Sample street",
    "sender_city": "Chennai",
    "sender_pincode": "600001",
    "sender_mobile_no": "9000000000",
    "receiver_name": "Sample Receiver",
    "receiver_add_line_1": "Sample street",
    "receiver_city": "New Delhi",
    "receiver_pincode": "110001",
    "receiver_mobile_no": "9000000000",
    "alt_address_flag": "FALSE",
    "pickup_address_flag": "FALSE",
    "drop_off_pincode": "600001",
}]}, indent=2)


def _redact(value):
    if isinstance(value, dict):
        return {key: ("[redacted]" if any(part in key.lower() for part in SENSITIVE_PARTS) else _redact(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value[:20]]
    return value


def _preview(payload):
    if isinstance(payload, str):
        if payload.lstrip().startswith("<"):
            return "XML/HTML response received; body withheld from the test log."
        preview = payload[:2000]
        for secret_name in ("INDIA_POST_SANDBOX_PASSWORD", "INDIA_POST_WEBHOOK_TOKEN"):
            secret = os.environ.get(secret_name, "")
            if secret:
                preview = preview.replace(secret, "[redacted]")
        return preview
    return json.dumps(_redact(payload), indent=2, ensure_ascii=False)[:6000]


def _positive_int(value, label):
    try:
        number = int(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label} must be a whole number.") from exc
    if not 1 <= number <= 35000:
        raise ValueError(f"{label} must be between 1 and 35000 grams.")
    return number


def _perform(action, post):
    token, token_status, token_payload = client.access_token()
    if action == "token":
        token_data = token_payload.get("data") or {}
        return token_status, "Token generated successfully. It expires in about 15 minutes.", _preview({
            "success": token_payload.get("success"),
            "expires_in": token_data.get("expires_in"),
        })
    if action == "tariff":
        source = post.get("source_pincode", "").strip()
        destination = post.get("destination_pincode", "").strip()
        service = post.get("service", "SP")
        if not PIN.fullmatch(source) or not PIN.fullmatch(destination):
            raise ValueError("Both source and destination PIN codes must have six digits.")
        if service not in ("SP", "BP"):
            raise ValueError("Choose Speed Post or Business Parcel.")
        values = {"service": service, "weight": _positive_int(post.get("weight"), "Weight"),
                  "source_pincode": source, "destination_pincode": destination}
        for dimension in ("length", "width", "height"):
            raw = post.get(dimension, "0").strip() or "0"
            try:
                value = float(raw)
            except ValueError as exc:
                raise ValueError(f"{dimension.title()} must be a number.") from exc
            if not 0 <= value <= 150:
                raise ValueError(f"{dimension.title()} must be between 0 and 150 cm.")
            values[dimension] = raw
        status, payload, _ = client.tariff(token, values)
        if isinstance(payload, dict) and payload.get("success") is False:
            raise client.PostOfficeError("Tariff request was not accepted.", status, payload)
        amount = payload.get("final_amount") if isinstance(payload, dict) else None
        if amount is None:
            raise client.PostOfficeError("Tariff response did not include final_amount.", status, payload)
        return status, f"Tariff calculated. Final amount: {amount}.", _preview(payload)
    if action == "booking":
        customer_id = os.environ.get("INDIA_POST_SANDBOX_CUSTOMER_ID", "").strip()
        if not customer_id.isdigit():
            raise ValueError("Configure INDIA_POST_SANDBOX_CUSTOMER_ID before booking.")
        if post.get("confirm_booking") != "yes":
            raise ValueError("Confirm that this booking uses assigned sandbox article numbers.")
        try:
            payload = json.loads(post.get("booking_payload", ""))
        except ValueError as exc:
            raise ValueError("Booking payload must be valid JSON.") from exc
        articles = payload.get("articles") if isinstance(payload, dict) else None
        if not isinstance(articles, list) or not 1 <= len(articles) <= 100:
            raise ValueError("Booking payload needs 1 to 100 articles.")
        for article in articles:
            if not isinstance(article, dict) or str(article.get("bulk_customer_id", "")) != customer_id:
                raise ValueError("Each article must use the configured sandbox customer ID.")
            if not ARTICLE.fullmatch(str(article.get("barcode_no", ""))):
                raise ValueError("Each article needs a 13-character assigned barcode.")
            if not str(article.get("contract_id", "")).isdigit():
                raise ValueError("Each article needs its assigned sandbox contract ID.")
            if not str(article.get("pickup_dropoff_office_id", "")).isdigit() or int(article["pickup_dropoff_office_id"]) <= 0:
                raise ValueError("Each article needs its assigned pickup or dropoff office ID.")
        status, result, _ = client.booking(token, customer_id, payload)
        if not isinstance(result, dict) or result.get("success") is False:
            raise client.PostOfficeError("Booking response did not confirm success.", status, result)
        summary = result.get("summary", {})
        errors = summary.get("error_count", len(result.get("error_articles", [])))
        valid = summary.get("success_count", len(result.get("valid_articles", [])))
        outcome = "failed" if not valid else "partial" if errors else "passed"
        return status, f"{outcome.title()}: {valid} accepted, {errors} rejected. Review article errors below.", _preview(result), outcome
    if action == "tracking":
        numbers = [part.strip().upper() for part in re.split(r"[\s,]+", post.get("article_numbers", "")) if part.strip()]
        if not 1 <= len(numbers) <= 500 or any(not ARTICLE.fullmatch(number) for number in numbers):
            raise ValueError("Enter 1 to 500 article numbers, each 13 letters or digits.")
        status, payload, _ = client.tracking(token, numbers)
        if isinstance(payload, dict) and payload.get("success") is False:
            raise client.PostOfficeError("Tracking request was not accepted.", status, payload)
        count = len(payload.get("data", [])) if isinstance(payload, dict) and isinstance(payload.get("data"), list) else 0
        if not count:
            raise client.PostOfficeError("Tracking returned no article records.", status, payload)
        result_status = "partial" if count < len(set(numbers)) else "passed"
        return status, f"Tracking returned {count} article record(s) for {len(set(numbers))} requested.", _preview(payload), result_status
    if action == "events":
        method = post.get("event_method", "GET")
        body = None
        if method == "POST" and post.get("event_payload", "").strip():
            try:
                body = json.loads(post["event_payload"])
            except ValueError as exc:
                raise ValueError("Event request body must be valid JSON.") from exc
        status, payload, content_type = client.event_download(token, method, body)
        if isinstance(payload, dict) and payload.get("success") is False:
            raise client.PostOfficeError("Event download was not accepted.", status, payload)
        if isinstance(payload, str):
            if not payload.strip() or ("xml" not in content_type.lower() and not payload.lstrip().startswith("<?xml")):
                raise client.PostOfficeError("Event response was not XML as documented.", status)
            try:
                ElementTree.fromstring(payload)
            except ElementTree.ParseError as exc:
                raise client.PostOfficeError("Event response contained malformed XML.", status) from exc
            return status, f"Event XML received ({len(payload.encode('utf-8'))} bytes).", "XML received; event content withheld from the log."
        raise client.PostOfficeError("Event response was JSON, but the document specifies XML.", status, payload)
    raise ValueError("Unknown sandbox check.")


@login_required
@require_http_methods(["GET", "POST"])
@never_cache
def sandbox(request):
    if not is_super_admin(request.user):
        raise PermissionDenied
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action not in ("token", "tariff", "booking", "tracking", "events"):
            raise PermissionDenied
        try:
            result = _perform(action, request.POST)
            http_status, summary, preview = result[:3]
            status = result[3] if len(result) > 3 else "passed"
        except (ValueError, client.PostOfficeError) as exc:
            http_status = getattr(exc, "http_status", None)
            summary = str(exc)
            preview = _preview(getattr(exc, "payload", None)) if getattr(exc, "payload", None) is not None else ""
            status = "failed"
        SandboxRun.objects.create(action=action, status=status, http_status=http_status,
                                  summary=summary, response_preview=preview,
                                  created_by=request.user.get_username())
        return redirect("post_office_sandbox")
    runs = list(SandboxRun.objects.all()[:30])
    events = list(PostOfficeWebhookEvent.objects.all()[:30])
    latest = {action: row.status for action in ("token", "tariff", "booking", "events", "tracking")
              if (row := SandboxRun.objects.filter(action=action).first()) is not None}
    return render(request, "post_office/sandbox.html", {
        "runs": runs,
        "events": events,
        "checklist": [(label, latest.get(action, "not tested")) for action, label in (
            ("token", "Token generation"), ("tariff", "Tariff calculation"),
            ("booking", "Booking API"), ("events", "Event download"),
            ("tracking", "Tracking API"))],
        "configured": {
            "credentials": bool(os.environ.get("INDIA_POST_SANDBOX_USERNAME") and os.environ.get("INDIA_POST_SANDBOX_PASSWORD")),
            "customer_id": bool(os.environ.get("INDIA_POST_SANDBOX_CUSTOMER_ID")),
            "webhook_token": bool(os.environ.get("INDIA_POST_WEBHOOK_TOKEN")),
        },
        "event_path": os.environ.get("INDIA_POST_SANDBOX_EVENT_PATH", "/event/download"),
        "booking_template": BOOKING_TEMPLATE,
    })


@login_required
@require_GET
def evidence(request):
    if not is_super_admin(request.user):
        raise PermissionDenied
    runs = list(SandboxRun.objects.all()[:200])
    latest = {}
    for run in runs:
        latest.setdefault(run.action, run.status)
    result = JsonResponse({
        "environment": "India Post UAT",
        "endpoint_host": client.BASE_URL,
        "checklist": {action: latest.get(action, "not_tested") for action in
                      ("token", "tariff", "booking", "events", "tracking")},
        "runs": [{
            "action": run.action,
            "status": run.status,
            "http_status": run.http_status,
            "summary": run.summary,
            "response_preview": run.response_preview,
            "created_by": run.created_by,
            "created_at": run.created_at.isoformat(),
        } for run in runs],
        "webhook_events": [{
            "article_number": event.article_number,
            "event_code": event.event_code,
            "event_description": event.event_description,
            "event_date": event.event_date,
            "event_time": event.event_time,
            "event_office_name": event.event_office_name,
            "received_at": event.received_at.isoformat(),
        } for event in PostOfficeWebhookEvent.objects.all()[:200]],
    }, json_dumps_params={"indent": 2})
    result["Content-Disposition"] = 'attachment; filename="india_post_sandbox_evidence.json"'
    result["Cache-Control"] = "no-store"
    return result


@login_required
@require_GET
@never_cache
def report(request):
    if not is_super_admin(request.user):
        raise PermissionDenied
    checks = []
    for action, label in (("token", "Token generation"), ("tariff", "Tariff calculation"),
                          ("booking", "Booking API"), ("events", "Event download"),
                          ("tracking", "Tracking API")):
        checks.append({"label": label, "run": SandboxRun.objects.filter(action=action).first()})
    return render(request, "post_office/report.html", {
        "checks": checks,
        "webhook_events": PostOfficeWebhookEvent.objects.all()[:20],
        "generated_at": timezone.now(),
        "sandbox_host": client.BASE_URL,
        "webhook_url": request.build_absolute_uri("/post-office/webhook/"),
    })


@csrf_exempt
@require_POST
def webhook(request):
    expected = os.environ.get("INDIA_POST_WEBHOOK_TOKEN", "")
    if not expected:
        return JsonResponse({"error": "Webhook is not configured."}, status=503)
    supplied = request.headers.get("X-India-Post-Webhook-Token", "")
    if not hmac.compare_digest(supplied, expected):
        return JsonResponse({"error": "Unauthorized."}, status=401)
    if len(request.body) > 65536:
        return JsonResponse({"error": "Payload too large."}, status=413)
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Expected an event object."}, status=400)
    article = str(payload.get("article_number", "")).strip().upper()
    event_code = str(payload.get("event_code", "")).strip()
    event_date = str(payload.get("event_date", "")).strip()
    event_time = str(payload.get("event_time", "")).strip()
    if not ARTICLE.fullmatch(article) or not event_code or not event_date:
        return JsonResponse({"error": "article_number, event_code and event_date are required."}, status=400)
    fingerprint = hashlib.sha256("|".join((article, event_code, event_date, event_time,
        str(payload.get("event_office_facility_id", "")))).encode("utf-8")).hexdigest()
    event, created = PostOfficeWebhookEvent.objects.get_or_create(fingerprint=fingerprint, defaults={
        "article_number": article,
        "event_code": event_code[:100],
        "event_description": str(payload.get("event_description", ""))[:255],
        "event_date": event_date[:30],
        "event_time": event_time[:30],
        "event_office_name": str(payload.get("event_office_name", ""))[:160],
    })
    return JsonResponse({"received": True, "duplicate": not created, "event_id": event.pk}, status=202 if created else 200)
