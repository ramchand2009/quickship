"""Bounded tenant-scoped queries for the mobile operational dashboard."""

import hashlib
import json
from collections import Counter
from decimal import Decimal
from datetime import timedelta

from django.conf import settings
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.models import Product, ShiprocketOrder, TenantMembership, TenantWooCommerceMappingRule
from core.stock import summarize_order_profit


ROUTING_ROLES = {
    TenantMembership.ROLE_VENDOR_OWNER,
    TenantMembership.ROLE_VENDOR_OPERATOR,
}


def _metric(key, label, value, destination, tone="neutral"):
    return {
        "key": key,
        "label": label,
        "value": max(0, int(value or 0)),
        "destination": destination,
        "tone": tone,
    }


def _money_metric(key, label, value, destination, tone="neutral"):
    amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    return {
        "key": key,
        "label": label,
        "value": {"amount": f"{amount:.2f}", "currency": "INR"},
        "destination": destination,
        "tone": tone,
    }


def _alert(identifier, alert_type, title, message, destination, created_at):
    return {
        "id": identifier,
        "type": alert_type,
        "title": title,
        "message": message,
        "destination": destination,
        "created_at": created_at,
    }


def build_mobile_dashboard(*, tenant, role, now=None):
    generated_at = now or timezone.now()
    cache_seconds = settings.MOBILE_DASHBOARD_CACHE_SECONDS
    bucket_epoch = int(generated_at.timestamp()) // cache_seconds * cache_seconds
    bucket_time = timezone.datetime.fromtimestamp(bucket_epoch, tz=timezone.get_current_timezone())
    cache_expires_at = bucket_time + timedelta(seconds=cache_seconds)
    alert_time = bucket_time.isoformat().replace("+00:00", "Z")

    local_generated_at = timezone.localtime(generated_at)
    month_start = local_generated_at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month_start = (month_start + timedelta(days=32)).replace(day=1)
    month_end = next_month_start.date() - timedelta(days=1)
    month_query = f"date_from={month_start.date().isoformat()}&date_to={month_end.isoformat()}"

    def order_destination(status=None):
        status_query = f"status={status}&" if status else ""
        return f"/orders?{status_query}{month_query}"

    monthly_orders = ShiprocketOrder.objects.filter(tenant=tenant).annotate(
        dashboard_order_date=Coalesce("order_date", "created_at")
    ).filter(
        dashboard_order_date__gte=month_start,
        dashboard_order_date__lt=next_month_start,
    )
    monthly_order_rows = list(
        monthly_orders.only("tenant_id", "local_status", "total", "order_items")
    )
    status_counts = Counter(order.local_status for order in monthly_order_rows)
    monthly_value_statuses = {
        ShiprocketOrder.STATUS_ACCEPTED,
        ShiprocketOrder.STATUS_PACKED,
        ShiprocketOrder.STATUS_SHIPPED,
        ShiprocketOrder.STATUS_DELIVERY_ISSUE,
        ShiprocketOrder.STATUS_OUT_FOR_DELIVERY,
        ShiprocketOrder.STATUS_DELIVERED,
        ShiprocketOrder.STATUS_COMPLETED,
    }
    monthly_value_orders = [
        order for order in monthly_order_rows if order.local_status in monthly_value_statuses
    ]
    products = list(
        Product.objects.filter(tenant=tenant).only(
            "name",
            "sku",
            "smartbiz_product_id",
            "woocommerce_product_id",
            "woocommerce_variation_id",
            "actual_price",
            "stock_quantity",
            "reorder_level",
            "is_active",
        )
    )
    monthly_sales_total = sum(
        (order.total or Decimal("0.00") for order in monthly_value_orders),
        Decimal("0.00"),
    )
    monthly_profit_total = sum(
        (
            summarize_order_profit(order, products=products)["profit_amount"]
            for order in monthly_value_orders
        ),
        Decimal("0.00"),
    )
    active_products = [product for product in products if product.is_active]
    low_stock = sum(
        1 for product in active_products if product.stock_quantity <= product.reorder_level
    )
    routing_missing = sum(
        1
        for product in active_products
        if not product.smartbiz_product_id
        and not product.woocommerce_product_id
        and not product.woocommerce_variation_id
    )

    pending = status_counts[ShiprocketOrder.STATUS_NEW]
    accepted = status_counts[ShiprocketOrder.STATUS_ACCEPTED]
    attention = status_counts[ShiprocketOrder.STATUS_DELIVERY_ISSUE]
    metrics = [
        _metric("total_orders", "Total orders", len(monthly_order_rows), order_destination()),
        _metric("pending_orders", "Pending", pending, order_destination(ShiprocketOrder.STATUS_NEW), "attention" if pending else "positive"),
        _metric("accepted_orders", "Accepted", accepted, order_destination(ShiprocketOrder.STATUS_ACCEPTED)),
        _metric("shipped_orders", "Shipped", status_counts[ShiprocketOrder.STATUS_SHIPPED], order_destination(ShiprocketOrder.STATUS_SHIPPED)),
        _metric("completed_orders", "Completed", status_counts[ShiprocketOrder.STATUS_COMPLETED], order_destination(ShiprocketOrder.STATUS_COMPLETED), "positive"),
        _metric("cancelled_orders", "Cancelled", status_counts[ShiprocketOrder.STATUS_CANCELLED], order_destination(ShiprocketOrder.STATUS_CANCELLED), "critical" if status_counts[ShiprocketOrder.STATUS_CANCELLED] else "positive"),
        _money_metric("total_sales", "Total sales", monthly_sales_total, order_destination(), "positive"),
        _money_metric("total_profit", "Total profit", monthly_profit_total, order_destination(), "positive" if monthly_profit_total >= 0 else "critical"),
    ]

    routing_issues = 0
    if role in ROUTING_ROLES:
        has_mapping_rule = TenantWooCommerceMappingRule.objects.filter(
            tenant=tenant,
            is_active=True,
        ).exists()
        routing_issues = 0 if has_mapping_rule else routing_missing

    alerts = []
    if attention:
        alerts.append(_alert("orders:delivery_issue", "order_attention", "Orders need attention", f"{attention} order(s) have a delivery issue.", order_destination(ShiprocketOrder.STATUS_DELIVERY_ISSUE), alert_time))
    if low_stock:
        alerts.append(_alert("stock:low", "stock_attention", "Stock needs attention", f"{low_stock} active product(s) are at or below reorder level.", "/products?stock_state=low", alert_time))
    if routing_issues:
        alerts.append(_alert("routing:missing", "routing_attention", "Routing setup incomplete", f"{routing_issues} product(s) have no routing identifier or active tenant rule.", "/products?routing_state=attention", alert_time))

    data = {"metrics": metrics, "alerts": alerts}
    etag_digest = hashlib.sha256(
        json.dumps(
            {"tenant_id": tenant.pk, "role": role, "bucket": bucket_epoch, "data": data},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "data": data,
        "meta": {"cache_expires_at": cache_expires_at.isoformat().replace("+00:00", "Z")},
        "etag": f'"{etag_digest}"',
    }
