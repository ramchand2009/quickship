import base64
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib import error, parse, request

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Product, ProductCategory, ShiprocketOrder, WooCommerceSettings, normalize_sku


class WooCommerceAPIError(Exception):
    pass


DEFAULT_LOCAL_TO_WOOCOMMERCE_STATUS = {
    ShiprocketOrder.STATUS_NEW: "pending",
    ShiprocketOrder.STATUS_ACCEPTED: "processing",
    ShiprocketOrder.STATUS_PACKED: "processing",
    ShiprocketOrder.STATUS_SHIPPED: "completed",
    ShiprocketOrder.STATUS_DELIVERY_ISSUE: "processing",
    ShiprocketOrder.STATUS_OUT_FOR_DELIVERY: "processing",
    ShiprocketOrder.STATUS_DELIVERED: "completed",
    ShiprocketOrder.STATUS_COMPLETED: "completed",
    ShiprocketOrder.STATUS_CANCELLED: "cancelled",
}

ALLOWED_WOOCOMMERCE_ORDER_STATUSES = {
    "auto-draft",
    "pending",
    "whatsapp-draft",
    "processing",
    "on-hold",
    "completed",
    "cancelled",
    "refunded",
    "failed",
    "checkout-draft",
}

DEFAULT_IMPORT_STATUSES = ["pending", "processing", "on-hold", "whatsapp-draft"]
CUSTOMER_PHONE_LOOKUP_PAGE_LIMIT = 50

WOOCOMMERCE_TO_LOCAL_STATUS = {
    "pending": ShiprocketOrder.STATUS_NEW,
    "processing": ShiprocketOrder.STATUS_NEW,
    "on-hold": ShiprocketOrder.STATUS_NEW,
    "whatsapp-draft": ShiprocketOrder.STATUS_NEW,
    "wc-whatsapp-draft": ShiprocketOrder.STATUS_NEW,
    "cancelled": ShiprocketOrder.STATUS_CANCELLED,
    "refunded": ShiprocketOrder.STATUS_CANCELLED,
    "failed": ShiprocketOrder.STATUS_CANCELLED,
    "completed": ShiprocketOrder.STATUS_COMPLETED,
}


def is_configured():
    config = _get_config()
    return bool(
        config["store_url"]
        and config["consumer_key"]
        and config["consumer_secret"]
    )


def _get_config():
    row = WooCommerceSettings.objects.order_by("-updated_at", "-created_at").first()
    store_url = str(getattr(row, "store_url", "") or getattr(settings, "WOOCOMMERCE_STORE_URL", "") or "").strip()
    consumer_key = str(
        getattr(row, "consumer_key", "") or getattr(settings, "WOOCOMMERCE_CONSUMER_KEY", "") or ""
    ).strip()
    consumer_secret = str(
        getattr(row, "consumer_secret", "") or getattr(settings, "WOOCOMMERCE_CONSUMER_SECRET", "") or ""
    ).strip()
    webhook_secret = str(
        getattr(row, "webhook_secret", "") or getattr(settings, "WOOCOMMERCE_WEBHOOK_SECRET", "") or ""
    ).strip()
    import_statuses = str(
        getattr(row, "import_statuses", "") or getattr(settings, "WOOCOMMERCE_IMPORT_STATUSES", "") or ""
    ).strip()
    status_map = str(getattr(row, "status_map", "") or getattr(settings, "WOOCOMMERCE_STATUS_MAP", "") or "").strip()
    return {
        "store_url": store_url.rstrip("/"),
        "consumer_key": consumer_key,
        "consumer_secret": consumer_secret,
        "webhook_secret": webhook_secret,
        "import_statuses": import_statuses,
        "status_map": status_map,
    }


def get_webhook_secret():
    return _get_config()["webhook_secret"]


def _require_config():
    if not is_configured():
        raise WooCommerceAPIError(
            "WooCommerce credentials are missing. Set WOOCOMMERCE_STORE_URL, "
            "WOOCOMMERCE_CONSUMER_KEY, and WOOCOMMERCE_CONSUMER_SECRET."
        )


def _api_url(path, params=None):
    store_url = _get_config()["store_url"]
    path = str(path or "").strip().lstrip("/")
    query = parse.urlencode(params or {}, doseq=True)
    url = f"{store_url}/wp-json/wc/v3/{path}"
    return f"{url}?{query}" if query else url


def _json_request(path, method="GET", payload=None, params=None):
    _require_config()
    config = _get_config()
    credentials = f"{config['consumer_key']}:{config['consumer_secret']}"
    auth_value = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Basic {auth_value}",
    }
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(_api_url(path, params=params), data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=30) as response:
            raw_body = response.read().decode("utf-8")
            return json.loads(raw_body) if raw_body else {}
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="ignore")
        raise WooCommerceAPIError(
            f"WooCommerce API returned HTTP {exc.code}: {response_body or exc.reason}"
        ) from exc
    except error.URLError as exc:
        raise WooCommerceAPIError(f"Unable to reach WooCommerce API: {exc.reason}") from exc


def _to_decimal(value):
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_optional_decimal(value):
    if value is None or str(value).strip() == "":
        return None
    return _to_decimal(value)


def _normalize_phone_digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _phone_match_keys(value):
    digits = _normalize_phone_digits(value)
    keys = set()
    if digits:
        keys.add(digits)
    if len(digits) > 10:
        keys.add(digits[-10:])
    return keys


def _customer_phone_values(customer):
    values = []
    if not isinstance(customer, dict):
        return values

    billing = customer.get("billing")
    if isinstance(billing, dict):
        values.append(billing.get("phone"))

    meta_data = customer.get("meta_data")
    if isinstance(meta_data, list):
        for row in meta_data:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key") or "").strip().lower()
            if key in {"billing_phone", "phone", "mobile", "customer_phone"}:
                values.append(row.get("value"))

    return values


def _customer_matches_phone(customer, phone_keys):
    for value in _customer_phone_values(customer):
        if _phone_match_keys(value) & phone_keys:
            return True
    return False


def _find_unique_customer_id_by_phone(phone):
    phone_keys = _phone_match_keys(phone)
    if not phone_keys:
        return None

    search_terms = []
    digits = _normalize_phone_digits(phone)
    if digits:
        search_terms.append(digits)
    if len(digits) > 10:
        search_terms.append(digits[-10:])

    def collect_matches(params):
        matches = {}
        customers = _json_request("customers", params=params)
        if not isinstance(customers, list):
            return matches, 0
        for customer in customers:
            if not isinstance(customer, dict):
                continue
            customer_id = _to_int(customer.get("id"))
            if customer_id and _customer_matches_phone(customer, phone_keys):
                matches[customer_id] = customer
        return matches, len(customers)

    customers_by_id = {}
    for term in dict.fromkeys(search_terms):
        term_matches, _customer_count = collect_matches({"search": term, "per_page": 100})
        customers_by_id.update(term_matches)

    if not customers_by_id:
        for page in range(1, CUSTOMER_PHONE_LOOKUP_PAGE_LIMIT + 1):
            page_matches, customer_count = collect_matches({"per_page": 100, "page": page})
            customers_by_id.update(page_matches)
            if len(customers_by_id) > 1:
                return None
            if customer_count < 100:
                break

    if len(customers_by_id) != 1:
        return None
    return next(iter(customers_by_id))


def _assign_guest_order_customer_by_phone(order_payload, phone):
    if not isinstance(order_payload, dict):
        return None
    order_id = order_payload.get("id")
    if not order_id or _to_int(order_payload.get("customer_id")):
        return None

    try:
        customer_id = _find_unique_customer_id_by_phone(phone)
        if not customer_id:
            return None
        _json_request(f"orders/{order_id}", method="PUT", payload={"customer_id": customer_id})
    except WooCommerceAPIError:
        return None

    order_payload["customer_id"] = customer_id
    return customer_id


def _assign_order_record_customer_by_phone(order):
    if getattr(order, "source", "") != ShiprocketOrder.SOURCE_WOOCOMMERCE:
        return None
    wc_order_id = str(order.woocommerce_order_id or "").strip()
    if not wc_order_id:
        return None

    payload = order.raw_payload if isinstance(order.raw_payload, dict) else {}
    order_payload = dict(payload)
    order_payload["id"] = wc_order_id
    if "customer_id" not in order_payload:
        order_payload["customer_id"] = 0

    assigned_customer_id = _assign_guest_order_customer_by_phone(order_payload, order.resolved_customer_phone)
    if not assigned_customer_id:
        return None

    order.raw_payload = order_payload
    return assigned_customer_id


def _parse_datetime(value):
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    parsed_value = parse_datetime(raw_value)
    if parsed_value:
        if timezone.is_naive(parsed_value):
            return timezone.make_aware(parsed_value, timezone.get_current_timezone())
        return parsed_value
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return timezone.make_aware(datetime.strptime(raw_value, fmt), timezone.get_current_timezone())
        except ValueError:
            continue
    return None


def _parse_woocommerce_order_date(order):
    if not isinstance(order, dict):
        return None

    gmt_value = str(order.get("date_created_gmt") or "").strip()
    if gmt_value:
        parsed_value = parse_datetime(gmt_value)
        if parsed_value:
            if timezone.is_naive(parsed_value):
                return timezone.make_aware(parsed_value, timezone.UTC)
            return parsed_value

    return _parse_datetime(order.get("date_created"))


def _compact_address(address):
    if not isinstance(address, dict):
        return {}
    first_name = str(address.get("first_name") or "").strip()
    last_name = str(address.get("last_name") or "").strip()
    return {
        "name": " ".join(value for value in [first_name, last_name] if value).strip(),
        "email": address.get("email") or "",
        "phone": address.get("phone") or "",
        "address_1": address.get("address_1") or "",
        "address_2": address.get("address_2") or "",
        "city": address.get("city") or "",
        "state": address.get("state") or "",
        "country": address.get("country") or "",
        "pincode": address.get("postcode") or address.get("pincode") or "",
    }


def _merge_billing_into_shipping(billing, shipping):
    merged = dict(shipping or {})
    billing = billing or {}

    has_billing_address = any(billing.get(key) for key in ["address_1", "address_2", "city", "state", "country", "pincode"])
    if has_billing_address:
        for contact_key in ["name", "phone", "email"]:
            merged[contact_key] = billing.get(contact_key) or merged.get(contact_key, "")
        for address_key in ["address_1", "address_2", "city", "state", "country", "pincode"]:
            merged[address_key] = billing.get(address_key) or merged.get(address_key, "")
    else:
        for contact_key in ["name", "phone", "email"]:
            if not merged.get(contact_key):
                merged[contact_key] = billing.get(contact_key, "")
    return merged


def _has_delivery_address(address):
    return any((address or {}).get(key) for key in ["address_1", "address_2", "city", "state", "country", "pincode"])


def _extract_items(order):
    normalized = []
    for item in order.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        image = item.get("image") if isinstance(item.get("image"), dict) else {}
        normalized.append(
            {
                "name": item.get("name") or "",
                "sku": item.get("sku") or "",
                "channel_sku": item.get("sku") or "",
                "channel_product_id": item.get("product_id") or "",
                "variant_id": item.get("variation_id") or "",
                "quantity": item.get("quantity") or 0,
                "price": str(_to_decimal(item.get("price") or item.get("total"))),
                "image": image.get("src") or "",
            }
        )
    return normalized


def _first_category_name(product):
    categories = product.get("categories") if isinstance(product, dict) else []
    if not isinstance(categories, list):
        return ""
    for category in categories:
        if isinstance(category, dict):
            name = str(category.get("name") or "").strip()
            if name:
                return name
    return ""


def _variation_name(parent_product, variation):
    parent_name = str(parent_product.get("name") or "").strip()
    attributes = variation.get("attributes") if isinstance(variation, dict) else []
    options = []
    if isinstance(attributes, list):
        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            option = str(attribute.get("option") or "").strip()
            if option:
                options.append(option)
    if parent_name and options:
        return f"{parent_name} - {', '.join(options)}"
    return str(variation.get("name") or parent_name or "").strip()


def _product_image_url(product, *, parent_product=None):
    if isinstance(product, dict):
        image = product.get("image")
        if isinstance(image, dict) and image.get("src"):
            return str(image.get("src") or "").strip()
        images = product.get("images")
        if isinstance(images, list):
            for image in images:
                if isinstance(image, dict) and image.get("src"):
                    return str(image.get("src") or "").strip()

    if isinstance(parent_product, dict):
        return _product_image_url(parent_product)
    return ""


def _normalized_product_row(product, *, parent_product=None):
    if not isinstance(product, dict):
        return None

    parent = parent_product if isinstance(parent_product, dict) else {}
    sku = normalize_sku(product.get("sku"))
    if not sku:
        return None

    product_id = product.get("id")
    parent_id = parent.get("id")
    external_id = str(product_id or "").strip()
    if not external_id:
        return None

    category_name = _first_category_name(parent or product)
    stock_quantity = _to_int(product.get("stock_quantity"), default=0)
    name = _variation_name(parent, product) if parent else str(product.get("name") or "").strip()
    if not name:
        name = sku

    status = str(product.get("status") or parent.get("status") or "").strip().lower()
    return {
        "name": name,
        "sku": sku,
        "stock_quantity": stock_quantity,
        "category": category_name,
        "image_url": _product_image_url(product, parent_product=parent),
        "description": str((parent or product).get("description") or product.get("description") or "").strip(),
        "regular_price": _to_optional_decimal(product.get("regular_price") or parent.get("regular_price")),
        "sale_price": _to_optional_decimal(product.get("sale_price") or parent.get("sale_price")),
        "smartbiz_product_id": external_id,
        "is_active": status not in {"trash", "deleted"},
        "woocommerce_product_id": str(parent_id or product_id or "").strip(),
        "woocommerce_variation_id": str(product_id or "").strip() if parent_id else "",
    }


def _apply_product_row(product, row):
    category = _get_or_create_product_category(row.get("category"))
    defaults = {
        "name": row.get("name") or product.sku,
        "category": row.get("category") or "",
        "category_master": category,
        "sku": normalize_sku(row.get("sku")) or product.sku,
        "smartbiz_product_id": str(row.get("smartbiz_product_id") or product.smartbiz_product_id or "").strip(),
        "image_url": row.get("image_url") or "",
        "description": row.get("description") or "",
        "regular_price": row.get("regular_price"),
        "sale_price": row.get("sale_price"),
        "stock_quantity": _to_int(row.get("stock_quantity"), default=0),
        "is_active": bool(row.get("is_active", True)),
    }
    changed_fields = []
    for field_name, value in defaults.items():
        if getattr(product, field_name) != value:
            setattr(product, field_name, value)
            changed_fields.append(field_name)
    if changed_fields:
        product.save(update_fields=[*changed_fields, "updated_at"])
    return bool(changed_fields)


def _get_or_create_product_category(name):
    category_name = str(name or "").strip()
    if not category_name:
        return None
    existing = ProductCategory.objects.filter(name__iexact=category_name).first()
    if existing:
        return existing
    return ProductCategory.objects.create(name=category_name, is_active=True)


def _sync_product_row(row):
    sku = normalize_sku(row.get("sku"))
    external_id = str(row.get("smartbiz_product_id") or "").strip()
    if not sku or not external_id:
        return "skipped"

    product = Product.objects.filter(sku=sku).first()
    if not product:
        product = Product.objects.filter(smartbiz_product_id__iexact=external_id).first()

    if product and product.sku != sku and Product.objects.filter(sku=sku).exclude(pk=product.pk).exists():
        return "skipped"
    if product and Product.objects.filter(smartbiz_product_id__iexact=external_id).exclude(pk=product.pk).exists():
        return "skipped"

    if not product:
        category = _get_or_create_product_category(row.get("category"))
        Product.objects.create(
            name=row.get("name") or sku,
            category=row.get("category") or "",
            category_master=category,
            sku=sku,
            smartbiz_product_id=external_id,
            image_url=row.get("image_url") or "",
            description=row.get("description") or "",
            regular_price=row.get("regular_price"),
            sale_price=row.get("sale_price"),
            stock_quantity=_to_int(row.get("stock_quantity"), default=0),
            is_active=bool(row.get("is_active", True)),
        )
        return "created"

    if _apply_product_row(product, row):
        return "updated"
    return "unchanged"


def _fetch_paginated(path, params=None, *, per_page=100):
    rows = []
    page = 1
    while True:
        page_params = {**(params or {}), "per_page": per_page, "page": page}
        response = _json_request(path, params=page_params)
        if not isinstance(response, list):
            raise WooCommerceAPIError(f"WooCommerce {path} response was not a list.")
        rows.extend(response)
        if len(response) < per_page:
            break
        page += 1
    return rows


def sync_products():
    products = _fetch_paginated(
        "products",
        params={"status": "any", "orderby": "id", "order": "asc"},
    )
    summary = {
        "products_seen": len(products),
        "variations_seen": 0,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
    }

    for product in products:
        row = _normalized_product_row(product)
        if row:
            summary[_sync_product_row(row)] += 1
        elif normalize_sku(product.get("sku")):
            summary["skipped"] += 1

        variation_ids = product.get("variations")
        should_fetch_variations = str(product.get("type") or "").lower() == "variable" or bool(variation_ids)
        if not should_fetch_variations:
            continue

        variations = _fetch_paginated(
            f"products/{product.get('id')}/variations",
            params={"status": "any", "orderby": "id", "order": "asc"},
        )
        summary["variations_seen"] += len(variations)
        for variation in variations:
            row = _normalized_product_row(variation, parent_product=product)
            if not row:
                summary["skipped"] += 1
                continue
            summary[_sync_product_row(row)] += 1

    return summary


def refresh_product_from_woocommerce(product):
    if not is_configured():
        return False
    path = _product_update_path(product)
    response = _json_request(path)
    row = _normalized_product_row(response)
    if not row:
        return False
    return _apply_product_row(product, row)


def _product_update_path(product):
    external_id = str(getattr(product, "smartbiz_product_id", "") or "").strip()
    if not external_id:
        raise WooCommerceAPIError("This product is missing a WooCommerce product ID.")
    if not external_id.isdigit():
        raise WooCommerceAPIError("WooCommerce product updates require a numeric product ID.")
    return f"products/{external_id}"


def _woocommerce_category_id(category_name):
    category_name = str(category_name or "").strip()
    if not category_name:
        return None

    categories = _json_request("products/categories", params={"search": category_name, "per_page": 100})
    if isinstance(categories, list):
        for category in categories:
            if not isinstance(category, dict):
                continue
            if str(category.get("name") or "").strip().lower() == category_name.lower():
                return _to_int(category.get("id"), default=None)

    category = _json_request("products/categories", method="POST", payload={"name": category_name})
    if isinstance(category, dict):
        return _to_int(category.get("id"), default=None)
    return None


def update_product(product, extra_fields=None):
    payload = {
        "name": product.name,
        "sku": product.sku,
        "manage_stock": True,
        "stock_quantity": int(product.stock_quantity or 0),
        "status": "publish" if product.is_active else "draft",
    }
    if product.image_url:
        payload["images"] = [{"src": product.image_url}]
    category_id = _woocommerce_category_id(product.category_label)
    if category_id:
        payload["categories"] = [{"id": category_id}]

    extra_fields = {
        "description": product.description,
        "regular_price": product.regular_price,
        "sale_price": product.sale_price,
        **(extra_fields or {}),
    }
    for field_name in ["description", "regular_price", "sale_price"]:
        value = str(extra_fields.get(field_name) or "").strip()
        if value:
            payload[field_name] = value
        elif field_name in extra_fields:
            payload[field_name] = ""

    return _json_request(_product_update_path(product), method="PUT", payload=payload)


def _import_statuses():
    configured = _get_config()["import_statuses"]
    statuses = [status.strip() for status in configured.split(",") if status.strip()]
    normalized_statuses = {status.lower() for status in statuses}
    for status in DEFAULT_IMPORT_STATUSES:
        if status.lower() not in normalized_statuses:
            statuses.append(status)
            normalized_statuses.add(status.lower())
    return statuses


def _local_status_for_woocommerce(status):
    return WOOCOMMERCE_TO_LOCAL_STATUS.get(str(status or "").strip().lower(), ShiprocketOrder.STATUS_NEW)


def _status_map():
    mapping = dict(DEFAULT_LOCAL_TO_WOOCOMMERCE_STATUS)
    raw_mapping = _get_config()["status_map"]
    if raw_mapping:
        try:
            configured = json.loads(raw_mapping)
        except json.JSONDecodeError as exc:
            raise WooCommerceAPIError(f"Invalid WOOCOMMERCE_STATUS_MAP JSON: {exc}") from exc
        if isinstance(configured, dict):
            mapping.update({str(key): str(value) for key, value in configured.items() if value})
    return mapping


def _normalize_woocommerce_status_value(value, local_status):
    status = str(value or "").strip().lower()
    status = status.removeprefix("wc-")
    if status in DEFAULT_LOCAL_TO_WOOCOMMERCE_STATUS:
        status = DEFAULT_LOCAL_TO_WOOCOMMERCE_STATUS[status]
    if status in ALLOWED_WOOCOMMERCE_ORDER_STATUSES:
        return status
    return DEFAULT_LOCAL_TO_WOOCOMMERCE_STATUS.get(str(local_status or "").strip(), "")


def check_connection():
    response = _json_request("orders", params={"per_page": 1, "page": 1, "orderby": "date", "order": "desc"})
    if not isinstance(response, list):
        raise WooCommerceAPIError("WooCommerce connection succeeded but orders response was not a list.")
    return {"ok": True, "sample_count": len(response)}


def woocommerce_status_for_local_status(local_status):
    local_status = str(local_status or "").strip()
    return _normalize_woocommerce_status_value(_status_map().get(local_status), local_status)


def sync_orders():
    statuses = _import_statuses()
    response = _json_request(
        "orders",
        params={
            "per_page": 50,
            "page": 1,
            "status": ",".join(statuses),
            "orderby": "date",
            "order": "desc",
        },
    )
    if not isinstance(response, list):
        raise WooCommerceAPIError("WooCommerce orders response was not a list.")

    synced = 0
    for item in response:
        order, created = import_order_payload(item)
        if order:
            synced += 1
    return synced


def import_order_payload(item):
    if not isinstance(item, dict):
        return None, False
    order_id = item.get("id")
    if not order_id:
        return None, False

    wc_status = str(item.get("status") or "").strip()
    billing = _compact_address(item.get("billing") or {})
    shipping = _compact_address(item.get("shipping") or {})
    has_billing_address = _has_delivery_address(billing)

    order_number = str(item.get("number") or order_id)
    source_order_id = f"WC-{order_id}"
    existing = ShiprocketOrder.objects.filter(shiprocket_order_id=source_order_id).first()
    if not existing and not has_billing_address:
        return None, False

    if has_billing_address:
        shipping = _merge_billing_into_shipping(billing, shipping)
    elif existing:
        billing = existing.billing_address if isinstance(existing.billing_address, dict) else {}
        shipping = existing.shipping_address if isinstance(existing.shipping_address, dict) else {}

    defaults = {
        "source": ShiprocketOrder.SOURCE_WOOCOMMERCE,
        "woocommerce_order_id": str(order_id),
        "woocommerce_order_key": str(item.get("order_key") or ""),
        "woocommerce_status": wc_status,
        "woocommerce_synced_at": timezone.now(),
        "channel_order_id": order_number,
        "customer_name": billing.get("name") or shipping.get("name") or "",
        "customer_email": billing.get("email") or shipping.get("email") or "",
        "customer_phone": billing.get("phone") or shipping.get("phone") or "",
        "status": wc_status,
        "payment_method": item.get("payment_method_title") or item.get("payment_method") or "",
        "total": _to_decimal(item.get("total")),
        "order_date": _parse_woocommerce_order_date(item),
        "shipping_address": shipping,
        "billing_address": billing,
        "order_items": _extract_items(item),
        "raw_payload": item,
    }
    assigned_customer_id = _assign_guest_order_customer_by_phone(item, defaults["customer_phone"])
    if assigned_customer_id:
        defaults["raw_payload"] = item
    if not existing:
        defaults["local_status"] = _local_status_for_woocommerce(wc_status)
    return ShiprocketOrder.objects.update_or_create(
        shiprocket_order_id=source_order_id,
        defaults=defaults,
    )


def update_order_status(order):
    if getattr(order, "source", "") != ShiprocketOrder.SOURCE_WOOCOMMERCE:
        return {"skipped": True, "reason": "not_woocommerce"}
    wc_order_id = str(order.woocommerce_order_id or "").strip()
    if not wc_order_id:
        return {"skipped": True, "reason": "missing_woocommerce_order_id"}
    assigned_customer_id = _assign_order_record_customer_by_phone(order)
    target_status = woocommerce_status_for_local_status(order.local_status)
    if not target_status:
        return {"skipped": True, "reason": "no_status_mapping"}
    if str(order.woocommerce_status or "").strip().lower() == target_status.lower():
        if assigned_customer_id:
            order.save(update_fields=["raw_payload", "updated_at"])
        return {"skipped": True, "reason": "already_synced", "status": target_status}

    response = _json_request(f"orders/{wc_order_id}", method="PUT", payload={"status": target_status})
    updated_status = str(response.get("status") or target_status).strip()
    order.woocommerce_status = updated_status
    order.status = updated_status
    order.woocommerce_status_synced_at = timezone.now()
    update_fields = ["woocommerce_status", "status", "woocommerce_status_synced_at", "updated_at"]
    if assigned_customer_id:
        update_fields.append("raw_payload")
    order.save(update_fields=update_fields)
    return {
        "skipped": False,
        "status": updated_status,
        "response": response,
        "assigned_customer_id": assigned_customer_id,
    }
