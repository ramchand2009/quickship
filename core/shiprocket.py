import json
from decimal import Decimal, InvalidOperation
from urllib import error, parse, request

from django.conf import settings
from django.utils.dateparse import parse_datetime

from .models import ShiprocketOrder


class ShiprocketAPIError(Exception):
    pass


def _json_request(url, method="GET", payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="ignore")
        raise ShiprocketAPIError(
            f"Shiprocket API returned HTTP {exc.code}: {response_body or exc.reason}"
        ) from exc
    except error.URLError as exc:
        raise ShiprocketAPIError(f"Unable to reach Shiprocket API: {exc.reason}") from exc


def _get_auth_token():
    email = getattr(settings, "SHIPROCKET_EMAIL", "")
    password = getattr(settings, "SHIPROCKET_PASSWORD", "")
    if not email or not password:
        raise ShiprocketAPIError(
            "Shiprocket credentials are missing. Set SHIPROCKET_EMAIL and SHIPROCKET_PASSWORD."
        )

    response = _json_request(
        f"{settings.SHIPROCKET_BASE_URL}/auth/login",
        method="POST",
        payload={"email": email, "password": password},
    )
    token = response.get("token")
    if not token:
        raise ShiprocketAPIError("Shiprocket authentication succeeded but no token was returned.")
    return token


def _to_decimal(value):
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _compact_address(source):
    if not isinstance(source, dict):
        return {}

    return {
        "name": source.get("customer_name") or source.get("name") or "",
        "email": source.get("customer_email") or source.get("email") or "",
        "phone": source.get("customer_phone") or source.get("phone") or "",
        "alternate_phone": source.get("customer_alternate_phone") or source.get("alternate_phone") or "",
        "address_1": (
            source.get("customer_address")
            or source.get("address")
            or source.get("address_1")
            or source.get("shipping_address")
            or ""
        ),
        "address_2": source.get("customer_address_2") or source.get("address_2") or "",
        "city": source.get("customer_city") or source.get("city") or "",
        "state": source.get("customer_state") or source.get("state") or "",
        "country": source.get("customer_country") or source.get("country") or "",
        "pincode": source.get("customer_pincode") or source.get("pin_code") or source.get("pincode") or "",
        "latitude": source.get("customer_latitude") or source.get("latitude"),
        "longitude": source.get("customer_longitude") or source.get("longitude"),
    }


def _extract_shipping_address(item):
    return _compact_address(item.get("shipping_address_details") or item.get("shipping_address") or item)


def _extract_billing_address(item):
    fallback = item.get("billing_address_details") or item.get("billing_address")
    return _compact_address(fallback or item.get("shipping_address_details") or item)


def _extract_items(item):
    raw_items = (
        item.get("products")
        or item.get("items")
        or item.get("order_items")
        or item.get("line_items")
        or []
    )
    normalized = []
    for product in raw_items:
        if not isinstance(product, dict):
            continue
        normalized.append(
            {
                "name": product.get("name") or product.get("product_name") or "",
                "sku": product.get("sku") or product.get("channel_sku") or "",
                "channel_sku": product.get("channel_sku") or "",
                "channel_product_id": (
                    product.get("channel_product_id")
                    or product.get("product_id")
                    or product.get("variant_id")
                    or product.get("id")
                    or ""
                ),
                "quantity": product.get("units") or product.get("quantity") or 0,
                "price": str(_to_decimal(product.get("selling_price") or product.get("price"))),
            }
        )
    return normalized


def sync_orders():
    token = _get_auth_token()
    params = parse.urlencode({"per_page": 20, "page": 1})
    response = _json_request(
        f"{settings.SHIPROCKET_BASE_URL}/orders?{params}",
        token=token,
    )
    orders = response.get("data") or response.get("orders") or []

    synced = 0
    for item in orders:
        order_id = item.get("id") or item.get("order_id")
        if not order_id:
            continue

        ShiprocketOrder.objects.update_or_create(
            shiprocket_order_id=str(order_id),
            defaults={
                "channel_order_id": str(item.get("channel_order_id") or ""),
                "customer_name": item.get("customer_name") or "",
                "customer_email": item.get("customer_email") or "",
                "customer_phone": item.get("customer_phone") or "",
                "status": item.get("status") or item.get("current_status") or "",
                "payment_method": item.get("payment_method") or "",
                "total": _to_decimal(item.get("total") or item.get("sub_total")),
                "order_date": parse_datetime(item.get("created_at") or item.get("order_date") or ""),
                "shipping_address": _extract_shipping_address(item),
                "billing_address": _extract_billing_address(item),
                "order_items": _extract_items(item),
                "raw_payload": item,
            },
        )
        synced += 1

    return synced
