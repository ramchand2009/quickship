"""Bounded tenant-scoped queries for the mobile operational dashboard."""

import hashlib
import json
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, F, Q
from django.utils import timezone

from core.models import Product, ShiprocketOrder, TenantMembership, TenantWooCommerceMappingRule


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

    order_counts = ShiprocketOrder.objects.filter(tenant=tenant).aggregate(
        pending=Count("pk", filter=Q(local_status=ShiprocketOrder.STATUS_NEW)),
        accepted=Count("pk", filter=Q(local_status=ShiprocketOrder.STATUS_ACCEPTED)),
        attention=Count("pk", filter=Q(local_status=ShiprocketOrder.STATUS_DELIVERY_ISSUE)),
    )
    route_missing = (
        (Q(smartbiz_product_id__isnull=True) | Q(smartbiz_product_id=""))
        & Q(woocommerce_product_id="")
        & Q(woocommerce_variation_id="")
    )
    product_counts = Product.objects.filter(tenant=tenant, is_active=True).aggregate(
        low_stock=Count("pk", filter=Q(stock_quantity__lte=F("reorder_level"))),
        routing_missing=Count("pk", filter=route_missing),
    )

    pending = order_counts["pending"]
    accepted = order_counts["accepted"]
    attention = order_counts["attention"]
    low_stock = product_counts["low_stock"]
    metrics = [
        _metric("pending_orders", "Pending orders", pending, "/orders?status=new_order", "attention" if pending else "positive"),
        _metric("accepted_orders", "Accepted orders", accepted, "/orders?status=order_accepted", "neutral"),
        _metric("attention_orders", "Orders requiring attention", attention, "/orders?status=delivery_issue", "critical" if attention else "positive"),
        _metric("low_stock", "Low-stock products", low_stock, "/products?stock_state=low", "critical" if low_stock else "positive"),
    ]

    routing_issues = 0
    if role in ROUTING_ROLES:
        has_mapping_rule = TenantWooCommerceMappingRule.objects.filter(
            tenant=tenant,
            is_active=True,
        ).exists()
        routing_issues = 0 if has_mapping_rule else product_counts["routing_missing"]
        metrics.append(
            _metric(
                "routing_health",
                "Routing issues",
                routing_issues,
                "/products?routing_state=attention",
                "attention" if routing_issues else "positive",
            )
        )

    alerts = []
    if attention:
        alerts.append(_alert("orders:delivery_issue", "order_attention", "Orders need attention", f"{attention} order(s) have a delivery issue.", "/orders?status=delivery_issue", alert_time))
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
