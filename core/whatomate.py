import json
import re
import hashlib
from urllib import error, parse, request

from django.conf import settings


class WhatomateNotificationError(Exception):
    pass


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _is_truthy(raw_value):
    return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}


def _load_runtime_config():
    config = {
        "enabled": _is_truthy(getattr(settings, "WHATOMATE_ENABLED", False)),
        "base_url": str(getattr(settings, "WHATOMATE_BASE_URL", "") or "").strip(),
        "api_key": str(getattr(settings, "WHATOMATE_API_KEY", "") or "").strip(),
        "access_token": str(getattr(settings, "WHATOMATE_ACCESS_TOKEN", "") or "").strip(),
        "default_country_code": "".join(
            ch for ch in str(getattr(settings, "WHATOMATE_DEFAULT_COUNTRY_CODE", "91") or "91") if ch.isdigit()
        ),
        "account_id": str(getattr(settings, "WHATOMATE_ACCOUNT_ID", "") or "").strip(),
        "account_name": str(getattr(settings, "WHATOMATE_ACCOUNT_NAME", "") or "").strip(),
        "use_template": _is_truthy(getattr(settings, "WHATOMATE_ORDER_ACCEPTED_USE_TEMPLATE", False)),
        "template_name": str(getattr(settings, "WHATOMATE_ORDER_ACCEPTED_TEMPLATE_NAME", "") or "").strip(),
        "accepted_text": str(
            getattr(
                settings,
                "WHATOMATE_ORDER_ACCEPTED_TEXT",
                "Hi {customer_name}, your order {order_id} has been accepted. We will share the next update soon.",
            )
            or ""
        ),
        "test_phone_number": "",
        "test_message_text": "Hi from Mathukai test message.",
    }

    try:
        from .models import WhatsAppSettings

        settings_row = WhatsAppSettings.get_default()
        config["enabled"] = settings_row.enabled
        if settings_row.api_base_url.strip():
            config["base_url"] = settings_row.api_base_url.strip()
        if settings_row.api_key.strip():
            config["api_key"] = settings_row.api_key.strip()
        if settings_row.test_phone_number.strip():
            config["test_phone_number"] = settings_row.test_phone_number.strip()
        if settings_row.test_message_text.strip():
            config["test_message_text"] = settings_row.test_message_text.strip()
    except Exception:
        # Keep environment fallback if DB/table is unavailable.
        pass
    return config


def _resolve_runtime_config(overrides=None):
    config = _load_runtime_config()
    if not overrides:
        return config

    for key, value in overrides.items():
        if value is None:
            continue
        if key in {"enabled", "use_template"}:
            config[key] = _is_truthy(value)
            continue
        config[key] = str(value).strip() if isinstance(value, str) else value
    return config


def _is_enabled(config):
    return bool(config.get("enabled"))


def _get_base_url(config):
    raw_base_url = str(config.get("base_url") or "").strip()
    if not raw_base_url:
        raise WhatomateNotificationError("WhatsApp API link is not configured.")
    normalized = raw_base_url.rstrip("/")
    if normalized.endswith("/api"):
        normalized = normalized[: -len("/api")]
    return normalized.rstrip("/")


def _get_headers(config):
    headers = {"Content-Type": "application/json"}
    api_key = str(config.get("api_key") or "").strip()
    access_token = str(config.get("access_token") or "").strip()
    if api_key:
        headers["X-API-Key"] = api_key
        return headers
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
        return headers
    raise WhatomateNotificationError("WhatsApp API key is missing.")


def _json_request(path, config, method="GET", payload=None):
    base_url = _get_base_url(config)
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    req = request.Request(
        url=f"{base_url}{path}",
        data=body,
        headers=_get_headers(config),
        method=method,
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            if isinstance(parsed, dict):
                status_text = str(parsed.get("status") or "").strip().lower()
                if status_text in {"error", "failed", "failure"}:
                    message = str(parsed.get("message") or parsed.get("error") or "Whatomate API returned an error.").strip()
                    detail = parsed.get("data")
                    if detail not in (None, "", [], {}):
                        raise WhatomateNotificationError(f"{message} | Details: {detail}")
                    raise WhatomateNotificationError(message or "Whatomate API returned an error.")
            return parsed
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="ignore")
        raise WhatomateNotificationError(
            f"Whatomate API returned HTTP {exc.code}: {response_body or exc.reason}"
        ) from exc
    except error.URLError as exc:
        raise WhatomateNotificationError(f"Unable to reach Whatomate API: {exc.reason}") from exc


def _extract_data(payload):
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _extract_items(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in ("items", "templates", "results", "contacts"):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "templates", "results", "data", "contacts"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _extract_message_id(payload):
    if isinstance(payload, dict):
        for key in ("message_id", "id"):
            value = payload.get(key)
            if value:
                return str(value)
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("message_id", "id"):
                value = data.get(key)
                if value:
                    return str(value)
            message = data.get("message")
            if isinstance(message, dict):
                for key in ("message_id", "id"):
                    value = message.get(key)
                    if value:
                        return str(value)
        message = payload.get("message")
        if isinstance(message, dict):
            for key in ("message_id", "id"):
                value = message.get(key)
                if value:
                    return str(value)
    return ""


def _extract_http_status(error_message):
    marker = "HTTP "
    index = error_message.find(marker)
    if index == -1:
        return 0
    digits = []
    for char in error_message[index + len(marker) :]:
        if char.isdigit():
            digits.append(char)
        else:
            break
    try:
        return int("".join(digits)) if digits else 0
    except ValueError:
        return 0


def _is_retryable_template_error(exc):
    status_code = _extract_http_status(str(exc))
    return status_code in {400, 404, 405, 409, 422}


def _to_component_parameters(raw_values):
    return [{"type": "text", "text": str(value or "")} for value in raw_values]


def _normalize_template_params_for_api(template_params):
    if isinstance(template_params, dict):
        normalized = {}
        for key, value in template_params.items():
            normalized[str(key)] = str(value or "")
        return normalized
    if isinstance(template_params, list):
        return {str(index + 1): str(value or "") for index, value in enumerate(template_params)}
    return {}


def _build_template_attempts(phone_number, template_name, template_params, config, template_id=""):
    normalized_params = _normalize_template_params_for_api(template_params)
    template_name = str(template_name or "").strip()
    template_id = str(template_id or "").strip()

    base_variants = []
    if template_name:
        if normalized_params:
            base_variants.append(
                {"phone_number": phone_number, "template_name": template_name, "template_params": normalized_params}
            )
        base_variants.append({"phone_number": phone_number, "template_name": template_name})
    if template_id:
        if normalized_params:
            base_variants.append(
                {"phone_number": phone_number, "template_id": template_id, "template_params": normalized_params}
            )
        base_variants.append({"phone_number": phone_number, "template_id": template_id})

    account_name = str(config.get("account_name") or "").strip()
    if account_name:
        for payload in base_variants:
            payload["account_name"] = account_name

    return [("/api/messages/template", payload) for payload in base_variants]


def _resolve_template_identifiers(template_name):
    template_name = str(template_name or "").strip()
    template_id = ""
    if not template_name:
        return template_name, template_id

    try:
        from .models import WhatsAppTemplate

        template_row = (
            WhatsAppTemplate.objects.filter(name=template_name).exclude(template_id="").order_by("-synced_at").first()
        )
        if template_row:
            template_id = str(template_row.template_id or "").strip()
    except Exception:
        template_id = ""
    return template_name, template_id


def _send_template_by_fallback(phone_number, template_name, template_params, config, template_id=""):
    last_error = None
    template_name, resolved_template_id = _resolve_template_identifiers(template_name)
    explicit_template_id = str(template_id or "").strip()
    template_id = explicit_template_id or resolved_template_id
    if not template_name and not template_id:
        raise WhatomateNotificationError("Template name or template ID is required.")
    normalized_params = _normalize_template_params_for_api(template_params)

    # Prefer existing contact_id flow when available.
    contact_id = ""
    try:
        contact_id = _find_contact_id_by_phone(phone_number, config)
    except WhatomateNotificationError as exc:
        last_error = exc

    if contact_id:
        contact_attempts = []
        if template_name:
            if normalized_params:
                contact_attempts.append(
                    (
                        "/api/messages/template",
                        {"contact_id": contact_id, "template_name": template_name, "template_params": normalized_params},
                    )
                )
            contact_attempts.append(("/api/messages/template", {"contact_id": contact_id, "template_name": template_name}))
        if template_id:
            if normalized_params:
                contact_attempts.append(
                    (
                        "/api/messages/template",
                        {"contact_id": contact_id, "template_id": template_id, "template_params": normalized_params},
                    )
                )
            contact_attempts.append(("/api/messages/template", {"contact_id": contact_id, "template_id": template_id}))
        account_name = str(config.get("account_name") or "").strip()
        if account_name:
            for _, payload in contact_attempts:
                payload["account_name"] = account_name

        for path, payload in contact_attempts:
            try:
                response_payload = _json_request(path, config=config, method="POST", payload=payload)
                return {
                    "endpoint": path,
                    "request_payload": dict(payload),
                    "response_payload": response_payload if isinstance(response_payload, (dict, list)) else {},
                    "external_message_id": _extract_message_id(response_payload),
                }
            except WhatomateNotificationError as exc:
                last_error = exc
                if _is_retryable_template_error(exc):
                    continue
                raise

    # Fallback to phone_number variants from docs and common server builds.
    attempts = _build_template_attempts(
        phone_number=phone_number,
        template_name=template_name,
        template_params=normalized_params,
        config=config,
        template_id=template_id,
    )

    for path, payload in attempts:
        try:
            response_payload = _json_request(path, config=config, method="POST", payload=payload)
            return {
                "endpoint": path,
                "request_payload": dict(payload),
                "response_payload": response_payload if isinstance(response_payload, (dict, list)) else {},
                "external_message_id": _extract_message_id(response_payload),
            }
        except WhatomateNotificationError as exc:
            last_error = exc
            if _is_retryable_template_error(exc):
                continue
            raise

    # If phone-number flow fails with duplicate/validation issues, retry with contact_id once more.
    if not contact_id:
        try:
            contact_id = _ensure_contact_id(phone_number, "Mathukai Template Test", config)
        except WhatomateNotificationError:
            contact_id = ""

    if contact_id:
        retry_contact_attempts = []
        if template_name:
            if normalized_params:
                retry_contact_attempts.append(
                    (
                        "/api/messages/template",
                        {"contact_id": contact_id, "template_name": template_name, "template_params": normalized_params},
                    )
                )
            retry_contact_attempts.append(
                ("/api/messages/template", {"contact_id": contact_id, "template_name": template_name})
            )
        if template_id:
            if normalized_params:
                retry_contact_attempts.append(
                    (
                        "/api/messages/template",
                        {"contact_id": contact_id, "template_id": template_id, "template_params": normalized_params},
                    )
                )
            retry_contact_attempts.append(
                ("/api/messages/template", {"contact_id": contact_id, "template_id": template_id})
            )
        account_name = str(config.get("account_name") or "").strip()
        if account_name:
            for _, payload in retry_contact_attempts:
                payload["account_name"] = account_name

        for path, payload in retry_contact_attempts:
            try:
                response_payload = _json_request(path, config=config, method="POST", payload=payload)
                return {
                    "endpoint": path,
                    "request_payload": dict(payload),
                    "response_payload": response_payload if isinstance(response_payload, (dict, list)) else {},
                    "external_message_id": _extract_message_id(response_payload),
                }
            except WhatomateNotificationError as exc:
                last_error = exc
                if _is_retryable_template_error(exc):
                    continue
                raise

    raise WhatomateNotificationError(f"Template API rejected all known payload formats. Last error: {last_error}")


def _normalize_phone_number(raw_phone, config):
    digits = "".join(ch for ch in str(raw_phone or "") if ch.isdigit())
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]

    country_code = str(config.get("default_country_code") or "91").strip()
    if len(digits) == 10 and country_code:
        return f"{country_code}{digits}"
    return digits


def _find_contact_id_by_phone(phone_number, config):
    lookup_queries = [
        {"search": phone_number, "limit": 100},
        {"q": phone_number, "limit": 100},
        {"phone_number": phone_number, "limit": 100},
        {"phone": phone_number, "limit": 100},
        {"limit": 200},
    ]

    for params in lookup_queries:
        query = parse.urlencode(params)
        try:
            response = _json_request(f"/api/contacts?{query}", config=config, method="GET")
        except WhatomateNotificationError:
            continue

        items = _extract_items(response)
        if not items and isinstance(response, dict):
            data = response.get("data")
            if isinstance(data, dict) and isinstance(data.get("contact"), dict):
                items = [data.get("contact")]
            elif isinstance(response.get("contact"), dict):
                items = [response.get("contact")]
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            nested_contact = item.get("contact")
            if isinstance(nested_contact, dict):
                item = nested_contact

            for key in ("phone_number", "phone", "mobile", "wa_id", "whatsapp_number"):
                existing_phone = _normalize_phone_number(item.get(key), config)
                if existing_phone == phone_number and item.get("id"):
                    return str(item["id"])
    return ""


def _create_contact(phone_number, name, config):
    payload = {"phone_number": phone_number}
    if name:
        payload["name"] = name

    account_id = str(config.get("account_id") or "").strip()
    if account_id:
        payload["account_id"] = account_id

    try:
        response = _json_request("/api/contacts", config=config, method="POST", payload=payload)
    except WhatomateNotificationError as exc:
        if _extract_http_status(str(exc)) == 409:
            existing_contact_id = _find_contact_id_by_phone(phone_number, config)
            if existing_contact_id:
                return existing_contact_id
        raise
    data = _extract_data(response)
    contact_id = data.get("id") or response.get("id")
    if not contact_id and isinstance(data.get("contact"), dict):
        contact_id = data["contact"].get("id")
    if not contact_id:
        raise WhatomateNotificationError("Whatomate contact creation succeeded but no contact id was returned.")
    return str(contact_id)


def _ensure_contact_id(phone_number, name, config):
    contact_id = _find_contact_id_by_phone(phone_number, config)
    if contact_id:
        return contact_id
    return _create_contact(phone_number, name, config)


def _format_order_message(template_text, order):
    context = _SafeFormatDict(
        {
            "customer_name": order.display_shipping_address.get("name") or order.customer_name or "Customer",
            "order_id": order.shiprocket_order_id,
            "channel_order_id": order.channel_order_id or "",
            "status": order.get_local_status_display(),
            "tracking_number": order.tracking_number or "",
        }
    )
    try:
        return str(template_text).format_map(context).strip()
    except (ValueError, KeyError):
        return (
            f"Hi {context['customer_name']}, your order {context['order_id']} has been accepted. "
            "We will share further updates soon."
        )


def _send_text_message_to_phone(phone_number, contact_name, message_text, config):
    contact_id = _ensure_contact_id(
        phone_number=phone_number,
        name=contact_name,
        config=config,
    )
    payload = {"type": "text", "text": str(message_text or "").strip()}
    if not payload["text"]:
        raise WhatomateNotificationError("WhatsApp message text is empty.")

    endpoint = f"/api/contacts/{parse.quote(contact_id)}/messages"
    response_payload = _json_request(
        endpoint,
        config=config,
        method="POST",
        payload=payload,
    )
    return {
        "endpoint": endpoint,
        "request_payload": dict(payload),
        "response_payload": response_payload if isinstance(response_payload, (dict, list)) else {},
        "contact_id": contact_id,
        "external_message_id": _extract_message_id(response_payload),
    }


def _send_template_message(order, phone_number, config):
    template_name = str(config.get("template_name") or "").strip()
    if not template_name:
        raise WhatomateNotificationError(
            "Set WHATOMATE_ORDER_ACCEPTED_TEMPLATE_NAME when WHATOMATE_ORDER_ACCEPTED_USE_TEMPLATE is enabled."
        )

    payload = {
        "phone_number": phone_number,
        "template_name": template_name,
        "template_params": {
            "name": order.display_shipping_address.get("name") or order.customer_name or "Customer",
            "order_id": order.shiprocket_order_id,
            "status": order.get_local_status_display(),
        },
    }
    account_name = str(config.get("account_name") or "").strip()
    if account_name:
        payload["account_name"] = account_name

    return _send_template_by_fallback(
        phone_number=phone_number,
        template_name=template_name,
        template_params=payload["template_params"],
        config=config,
    )


def _send_text_message(order, phone_number, config):
    message_template = str(config.get("accepted_text") or "")
    message_text = _format_order_message(message_template, order)
    return _send_text_message_to_phone(
        phone_number=phone_number,
        contact_name=order.display_shipping_address.get("name") or order.customer_name or "",
        message_text=message_text,
        config=config,
    )


def _build_customer_enquiry_message(order):
    customer_name = order.display_shipping_address.get("name") or order.customer_name or "Customer"
    order_id = str(order.shiprocket_order_id or "").strip() or "-"
    status_label = str(order.get_local_status_display() or order.local_status or "").strip() or "Pending"
    tracking_number = str(order.tracking_number or "").strip()

    lines = [
        f"Hi {customer_name},",
        f"Your order {order_id} is currently {status_label}.",
    ]
    if tracking_number:
        lines.append(f"Tracking number: {tracking_number}.")
    else:
        lines.append("Tracking number is not assigned yet.")
    lines.append("We will share the next update soon.")
    return " ".join(lines).strip()


def send_order_enquiry_reply(order, incoming_phone_number="", inbound_message_text="", config_overrides=None):
    config = _resolve_runtime_config(config_overrides)
    if not _is_enabled(config):
        return {"sent": False, "reason": "disabled"}

    raw_phone = incoming_phone_number or (
        order.display_shipping_address.get("phone")
        or order.manual_customer_phone
        or order.customer_phone
    )
    phone_number = _normalize_phone_number(raw_phone, config)
    if len(phone_number) < 10:
        raise WhatomateNotificationError("Customer mobile is missing or invalid for WhatsApp enquiry reply.")

    message_text = _build_customer_enquiry_message(order)
    send_result = _send_text_message_to_phone(
        phone_number=phone_number,
        contact_name=order.display_shipping_address.get("name") or order.customer_name or "",
        message_text=message_text,
        config=config,
    )
    return {
        "sent": True,
        "phone_number": phone_number,
        "mode": "text",
        "message_text": message_text,
        "incoming_message_text": str(inbound_message_text or "").strip(),
        "request_payload": send_result.get("request_payload", {}) if isinstance(send_result, dict) else {},
        "response_payload": send_result.get("response_payload", {}) if isinstance(send_result, dict) else {},
        "endpoint": send_result.get("endpoint", "") if isinstance(send_result, dict) else "",
        "external_message_id": send_result.get("external_message_id", "") if isinstance(send_result, dict) else "",
    }


def check_api_connection(config_overrides=None):
    config = _resolve_runtime_config(config_overrides)
    probe_paths = [
        "/api/contacts?limit=1",
        "/api/templates?limit=1",
        "/api/webhook",
        "/api/health",
        "/health",
    ]
    last_error = None
    for path in probe_paths:
        try:
            _json_request(path, config=config, method="GET")
            return {"ok": True, "endpoint": path}
        except WhatomateNotificationError as exc:
            last_error = exc
            if "HTTP 404" in str(exc):
                continue
            raise
    if last_error:
        raise last_error
    return {"ok": False}


def sync_templates_from_api(config_overrides=None):
    config = _resolve_runtime_config(config_overrides)
    response = _json_request("/api/templates?limit=200", config=config, method="GET")
    items = _extract_items(response)
    if not items:
        return {"synced_count": 0}

    from .models import WhatsAppTemplate

    synced_count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("template_name") or "").strip()
        if not name:
            continue
        language = str(item.get("language") or item.get("lang_code") or "").strip()
        defaults = {
            "template_id": str(item.get("id") or item.get("template_id") or "").strip(),
            "category": str(item.get("category") or "").strip(),
            "status": str(item.get("status") or "").strip(),
            "raw_payload": item,
        }
        WhatsAppTemplate.objects.update_or_create(
            name=name,
            language=language,
            defaults=defaults,
        )
        synced_count += 1
    return {"synced_count": synced_count}


def send_test_whatsapp_message(phone_number, message_text, config_overrides=None):
    config = _resolve_runtime_config(config_overrides)
    normalized_phone = _normalize_phone_number(phone_number, config)
    if len(normalized_phone) < 10:
        raise WhatomateNotificationError("Enter a valid test phone number.")

    final_message = str(message_text or "").strip() or str(config.get("test_message_text") or "").strip()
    if not final_message:
        raise WhatomateNotificationError("Test message cannot be empty.")

    contact_id = _ensure_contact_id(normalized_phone, "Mathukai Test", config)
    payload = {"type": "text", "text": final_message}
    endpoint = f"/api/contacts/{parse.quote(contact_id)}/messages"
    response_payload = _json_request(
        endpoint,
        config=config,
        method="POST",
        payload=payload,
    )
    return {
        "sent": True,
        "phone_number": normalized_phone,
        "mode": "text",
        "request_payload": payload,
        "response_payload": response_payload if isinstance(response_payload, (dict, list)) else {},
        "endpoint": endpoint,
        "external_message_id": _extract_message_id(response_payload),
    }


def send_test_template_message(phone_number, template_name, template_params, config_overrides=None):
    config = _resolve_runtime_config(config_overrides)
    normalized_phone = _normalize_phone_number(phone_number, config)
    if len(normalized_phone) < 10:
        raise WhatomateNotificationError("Enter a valid test phone number.")

    final_template_name = str(template_name or "").strip()
    if not final_template_name:
        raise WhatomateNotificationError("Select a template before sending template test message.")

    parsed_params = template_params
    if isinstance(parsed_params, str):
        raw_params = parsed_params.strip()
        if not raw_params:
            parsed_params = {}
        else:
            try:
                parsed_params = json.loads(raw_params)
            except json.JSONDecodeError as exc:
                raise WhatomateNotificationError("Template placeholders must be valid JSON.") from exc
    if not isinstance(parsed_params, (dict, list)):
        raise WhatomateNotificationError("Template placeholders must be a JSON object or JSON array.")

    template_result = _send_template_by_fallback(
        phone_number=normalized_phone,
        template_name=final_template_name,
        template_params=parsed_params,
        config=config,
    )
    return {
        "sent": True,
        "phone_number": normalized_phone,
        "mode": "template",
        "template_name": final_template_name,
        "request_payload": template_result.get("request_payload", {}) if isinstance(template_result, dict) else {},
        "response_payload": template_result.get("response_payload", {}) if isinstance(template_result, dict) else {},
        "endpoint": template_result.get("endpoint", "") if isinstance(template_result, dict) else "",
        "external_message_id": template_result.get("external_message_id", "") if isinstance(template_result, dict) else "",
    }


_TEMPLATE_TOKEN_PATTERN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_POSITIONAL_ORDER_KEYS = [
    "customer_name",
    "order_id",
    "tracking_number",
    "status",
    "order_date",
    "phone",
    "channel_order_id",
    "total",
]
ORDER_TEMPLATE_FIELD_CHOICES = [
    ("customer_name", "Customer Name"),
    ("order_id", "Order ID"),
    ("shiprocket_order_id", "Shiprocket Order ID"),
    ("channel_order_id", "Channel Order ID"),
    ("tracking_number", "Tracking Number"),
    ("status", "Order Status (Label)"),
    ("local_status", "Order Status (Key)"),
    ("phone", "Customer Mobile"),
    ("customer_phone", "Customer Mobile (Raw)"),
    ("order_date", "Order Date"),
    ("total", "Order Total"),
    ("amount", "Order Amount"),
]


def _collect_strings_from_payload(value):
    strings = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            strings.extend(_collect_strings_from_payload(item))
    elif isinstance(value, list):
        for item in value:
            strings.extend(_collect_strings_from_payload(item))
    return strings


def _extract_template_placeholders_from_payload(payload):
    tokens = []
    seen = set()
    for text in _collect_strings_from_payload(payload or {}):
        for match in _TEMPLATE_TOKEN_PATTERN.findall(text):
            token = str(match).strip()
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)
    return tokens


def _build_order_template_context(order):
    shipping = order.display_shipping_address or {}
    order_date_text = order.order_date.strftime("%d-%b-%Y") if order.order_date else ""
    context = {
        "name": shipping.get("name") or order.customer_name or "",
        "customer_name": shipping.get("name") or order.customer_name or "",
        "order_id": order.shiprocket_order_id or "",
        "shiprocket_order_id": order.shiprocket_order_id or "",
        "channel_order_id": order.channel_order_id or "",
        "tracking_number": order.tracking_number or "",
        "tracking": order.tracking_number or "",
        "status": order.get_local_status_display(),
        "local_status": order.local_status or "",
        "phone": shipping.get("phone") or order.customer_phone or "",
        "customer_phone": shipping.get("phone") or order.customer_phone or "",
        "order_date": order_date_text,
        "total": str(order.total or ""),
        "amount": str(order.total or ""),
    }
    return context


def build_order_template_context(order):
    return _build_order_template_context(order)


def _resolve_context_value(context, token):
    token_key = str(token or "").strip().lower().replace("-", "_").replace(" ", "_")
    if token_key in context:
        return str(context[token_key] or "")

    aliases = {
        "customer": "customer_name",
        "mobile": "phone",
        "awb": "tracking_number",
        "waybill": "tracking_number",
        "order_no": "order_id",
        "order_number": "order_id",
        "date": "order_date",
    }
    if token_key in aliases:
        return str(context.get(aliases[token_key], "") or "")
    return ""


def _build_template_params_for_status(placeholders, order, field_mapping=None):
    context = _build_order_template_context(order)
    mapping = field_mapping if isinstance(field_mapping, dict) else {}
    if not placeholders:
        return {}

    params = {}
    for token in placeholders:
        token_key = str(token)
        mapped_field = str(mapping.get(token_key) or "").strip()
        if mapped_field:
            params[token_key] = str(context.get(mapped_field, "") or "")
            continue

        if token_key.isdigit():
            index = int(token_key) - 1
            value = context.get(_POSITIONAL_ORDER_KEYS[index], "") if 0 <= index < len(_POSITIONAL_ORDER_KEYS) else ""
            params[token_key] = str(value or "")
            continue

        params[token_key] = _resolve_context_value(context, token_key)
    return params


def _get_status_template_config(local_status):
    try:
        from .models import WhatsAppStatusTemplateConfig

        return WhatsAppStatusTemplateConfig.objects.filter(local_status=local_status, enabled=True).first()
    except Exception:
        return None


def _resolve_template_details_for_status(config_row):
    template_name = str(getattr(config_row, "template_name", "") or "").strip()
    template_id = str(getattr(config_row, "template_id", "") or "").strip()
    placeholders = []
    try:
        from .models import WhatsAppTemplate

        template_qs = WhatsAppTemplate.objects.all()
        if template_id:
            template_qs = template_qs.filter(template_id=template_id)
        if template_name:
            template_qs = template_qs.filter(name=template_name)
        template_row = template_qs.order_by("-synced_at").first()
        if template_row:
            if not template_name:
                template_name = template_row.name
            if not template_id:
                template_id = template_row.template_id or ""
            placeholders = _extract_template_placeholders_from_payload(template_row.raw_payload)
    except Exception:
        placeholders = []

    return template_name, template_id, placeholders


def _build_status_notification_plan(order):
    config = _resolve_runtime_config()
    if not _is_enabled(config):
        return {"sendable": False, "reason": "disabled", "status": order.local_status, "config": config}

    raw_phone = (
        order.display_shipping_address.get("phone")
        or order.manual_customer_phone
        or order.customer_phone
    )
    phone_number = _normalize_phone_number(raw_phone, config)
    if len(phone_number) < 10:
        raise WhatomateNotificationError("Customer mobile is missing or invalid for WhatsApp update.")

    status_config = _get_status_template_config(order.local_status)
    if status_config:
        template_name, template_id, placeholders = _resolve_template_details_for_status(status_config)
        if template_name or template_id:
            configured_mapping = getattr(status_config, "template_param_mapping", {}) or {}
            template_params = _build_template_params_for_status(placeholders, order, field_mapping=configured_mapping)
            return {
                "sendable": True,
                "mode": "template",
                "status": order.local_status,
                "phone_number": phone_number,
                "template_name": template_name,
                "template_id": template_id,
                "template_params": template_params,
                "config": config,
            }

    if order.local_status == "order_accepted":
        use_template = bool(config.get("use_template"))
        if use_template:
            template_name = str(config.get("template_name") or "").strip()
            if not template_name:
                raise WhatomateNotificationError(
                    "Set WHATOMATE_ORDER_ACCEPTED_TEMPLATE_NAME when WHATOMATE_ORDER_ACCEPTED_USE_TEMPLATE is enabled."
                )
            template_params = {
                "name": order.display_shipping_address.get("name") or order.customer_name or "Customer",
                "order_id": order.shiprocket_order_id,
                "status": order.get_local_status_display(),
            }
            return {
                "sendable": True,
                "mode": "template",
                "status": order.local_status,
                "phone_number": phone_number,
                "template_name": template_name,
                "template_id": "",
                "template_params": template_params,
                "config": config,
            }
        return {
            "sendable": True,
            "mode": "text",
            "status": order.local_status,
            "phone_number": phone_number,
            "template_name": "",
            "template_id": "",
            "template_params": {},
            "config": config,
        }

    return {"sendable": False, "reason": "not_configured", "status": order.local_status, "config": config}


def build_order_status_idempotency_payload(order):
    plan = _build_status_notification_plan(order)
    if not plan.get("sendable"):
        return plan

    payload = {
        "order_id": str(order.shiprocket_order_id or "").strip(),
        "status": str(order.local_status or "").strip(),
        "phone_number": str(plan.get("phone_number") or "").strip(),
        "mode": str(plan.get("mode") or "").strip(),
        "template_name": str(plan.get("template_name") or "").strip(),
        "template_id": str(plan.get("template_id") or "").strip(),
    }
    signature = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    plan["idempotency_key"] = hashlib.sha256(signature.encode("utf-8")).hexdigest()
    return plan


def send_order_status_update(order, previous_status=None):
    plan = _build_status_notification_plan(order)
    if not plan.get("sendable"):
        return {"sent": False, "reason": plan.get("reason", "not_configured"), "status": order.local_status}

    config = plan.get("config") or _resolve_runtime_config()
    mode = str(plan.get("mode") or "").strip()
    phone_number = str(plan.get("phone_number") or "").strip()
    template_name = str(plan.get("template_name") or "").strip()
    template_id = str(plan.get("template_id") or "").strip()

    if mode == "template":
        template_result = _send_template_by_fallback(
            phone_number=phone_number,
            template_name=template_name,
            template_params=plan.get("template_params") or {},
            config=config,
            template_id=template_id,
        )
        return {
            "sent": True,
            "mode": "template",
            "status": order.local_status,
            "phone_number": phone_number,
            "template_name": template_name,
            "template_id": template_id,
            "request_payload": template_result.get("request_payload", {}) if isinstance(template_result, dict) else {},
            "response_payload": template_result.get("response_payload", {}) if isinstance(template_result, dict) else {},
            "endpoint": template_result.get("endpoint", "") if isinstance(template_result, dict) else "",
            "external_message_id": template_result.get("external_message_id", "") if isinstance(template_result, dict) else "",
        }

    send_result = _send_text_message(order, phone_number, config)
    return {
        "sent": True,
        "mode": "text",
        "status": order.local_status,
        "phone_number": phone_number,
        "request_payload": send_result.get("request_payload", {}) if isinstance(send_result, dict) else {},
        "response_payload": send_result.get("response_payload", {}) if isinstance(send_result, dict) else {},
        "endpoint": send_result.get("endpoint", "") if isinstance(send_result, dict) else "",
        "external_message_id": send_result.get("external_message_id", "") if isinstance(send_result, dict) else "",
    }


def send_order_accepted_status_update(order):
    config = _resolve_runtime_config()
    if not _is_enabled(config):
        return {"sent": False, "reason": "disabled"}

    raw_phone = (
        order.display_shipping_address.get("phone")
        or order.manual_customer_phone
        or order.customer_phone
    )
    phone_number = _normalize_phone_number(raw_phone, config)
    if len(phone_number) < 10:
        raise WhatomateNotificationError("Customer mobile is missing or invalid for WhatsApp update.")

    use_template = bool(config.get("use_template"))
    if use_template:
        send_result = _send_template_message(order, phone_number, config)
        mode = "template"
    else:
        send_result = _send_text_message(order, phone_number, config)
        mode = "text"
    return {
        "sent": True,
        "phone_number": phone_number,
        "mode": mode,
        "request_payload": send_result.get("request_payload", {}) if isinstance(send_result, dict) else {},
        "response_payload": send_result.get("response_payload", {}) if isinstance(send_result, dict) else {},
        "endpoint": send_result.get("endpoint", "") if isinstance(send_result, dict) else "",
        "external_message_id": send_result.get("external_message_id", "") if isinstance(send_result, dict) else "",
    }
