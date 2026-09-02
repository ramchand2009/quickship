"""Customer history summaries for the mobile API."""

import hashlib
import re
from collections import OrderedDict
from decimal import Decimal

from django.db.models.functions import Coalesce

from core.models import ShiprocketOrder

from .order_serializers import OrderDetailSerializer, OrderSummarySerializer
from .order_services import mobile_order_detail


def _money(value):
    amount = value if value is not None else Decimal("0.00")
    return {"amount": f"{amount:.2f}", "currency": "INR"}


def _normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _normalize_phone(value):
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) > 10 and digits.startswith("91"):
        digits = digits[-10:]
    return digits


def _address_line(address):
    return ", ".join(
        str(address.get(key) or "").strip()
        for key in ["address_1", "address_2", "city", "state", "pincode", "country"]
        if str(address.get(key) or "").strip()
    )


def _customer_key(order):
    address = order.display_shipping_address
    phone = _normalize_phone(address.get("phone") or order.customer_phone)
    if phone:
        return f"phone:{phone}"
    email = _normalize_text(address.get("email") or order.customer_email)
    if email:
        return f"email:{email}"
    name = _normalize_text(address.get("name") or order.customer_name)
    if name:
        return f"name:{hashlib.sha1(name.encode('utf-8')).hexdigest()[:16]}"
    return f"order:{order.pk}"


def _customer_payload_from_order(order, key):
    address = order.display_shipping_address
    name = str(address.get("name") or order.customer_name or "Customer").strip()
    return {
        "key": key,
        "name": name,
        "phone": str(address.get("phone") or "").strip() or None,
        "email": str(address.get("email") or "").strip() or None,
        "address": _address_line(address) or None,
        "last_order_at": order.order_date or order.created_at,
        "order_count": 0,
        "total_spent": _money(Decimal("0.00")),
        "latest_order_reference": order.source_order_reference,
    }


def _base_customer_orders(tenant):
    return (
        ShiprocketOrder.objects.filter(tenant=tenant)
        .annotate(effective_order_date=Coalesce("order_date", "created_at"))
        .order_by("-effective_order_date", "-updated_at", "-pk")
        .only(
            "id",
            "tenant_id",
            "source",
            "shiprocket_order_id",
            "woocommerce_order_id",
            "channel_order_id",
            "customer_name",
            "customer_email",
            "customer_phone",
            "manual_customer_name",
            "manual_customer_email",
            "manual_customer_phone",
            "manual_customer_alternate_phone",
            "manual_shipping_address_1",
            "manual_shipping_address_2",
            "manual_shipping_city",
            "manual_shipping_state",
            "manual_shipping_country",
            "manual_shipping_pincode",
            "shipping_address",
            "billing_address",
            "raw_payload",
            "local_status",
            "payment_received_at",
            "order_items",
            "total",
            "order_date",
            "tracking_number",
            "version",
            "created_at",
            "updated_at",
        )
    )


def mobile_customer_list(*, tenant, role, search=""):
    search_text = _normalize_text(search)
    customers = OrderedDict()
    for order in _base_customer_orders(tenant):
        key = _customer_key(order)
        if key not in customers:
            customers[key] = _customer_payload_from_order(order, key)
            customers[key]["_total"] = Decimal("0.00")
        customer = customers[key]
        customer["order_count"] += 1
        customer["_total"] += order.total or Decimal("0.00")

    rows = []
    for customer in customers.values():
        customer["total_spent"] = _money(customer.pop("_total"))
        haystack = _normalize_text(" ".join(
            str(customer.get(key) or "")
            for key in ["name", "phone", "email", "address", "latest_order_reference"]
        ))
        if search_text and search_text not in haystack:
            continue
        rows.append(customer)
        if len(rows) >= 150:
            break
    return {"data": rows, "meta": {"count": len(rows)}}


def mobile_customer_detail(*, tenant, role, customer_key):
    matching_orders = [order for order in _base_customer_orders(tenant) if _customer_key(order) == customer_key]
    if not matching_orders:
        return None

    first_order = matching_orders[0]
    customer = _customer_payload_from_order(first_order, customer_key)
    total = sum((order.total or Decimal("0.00") for order in matching_orders), Decimal("0.00"))
    customer["order_count"] = len(matching_orders)
    customer["total_spent"] = _money(total)

    orders = OrderSummarySerializer(matching_orders[:100], many=True, context={"role": role}).data
    return {"data": {"customer": customer, "orders": orders}}


def mobile_customer_order_detail(*, tenant, role, customer_key, order_id):
    order, activity = mobile_order_detail(tenant=tenant, order_id=order_id)
    if order is None or _customer_key(order) != customer_key:
        return None
    return {
        "data": OrderDetailSerializer(
            order,
            context={"role": role, "activity": activity, "tenant": tenant},
        ).data
    }
