"""Bounded tenant-scoped queries for the mobile operational dashboard."""

import hashlib
import json
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, F, Q
from django.db.models.functions import Coalesce
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
    order_counts = monthly_orders.aggregate(
        total=Count("pk"),
        pending=Count("pk", filter=Q(local_status=ShiprocketOrder.STATUS_NEW)),
        accepted=Count("pk", filter=Q(local_status=ShiprocketOrder.STATUS_ACCEPTED)),
        shipped=Count("pk", filter=Q(local_status=ShiprocketOrder.STATUS_SHIPPED)),
        completed=Count("pk", filter=Q(local_status=ShiprocketOrder.STATUS_COMPLETED)),
        cancelled=Count("pk", filter=Q(local_status=ShiprocketOrder.STATUS_CANCELLED)),
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
        _metric("total_orders", "Total orders", order_counts["total"], order_destination()),
        _metric("pending_orders", "Pending", pending, order_destination(ShiprocketOrder.STATUS_NEW), "attention" if pending else "positive"),
        _metric("accepted_orders", "Accepted", accepted, order_destination(ShiprocketOrder.STATUS_ACCEPTED)),
        _metric("shipped_orders", "Shipped", order_counts["shipped"], order_destination(ShiprocketOrder.STATUS_SHIPPED)),
        _metric("completed_orders", "Completed", order_counts["completed"], order_destination(ShiprocketOrder.STATUS_COMPLETED), "positive"),
        _metric("cancelled_orders", "Cancelled", order_counts["cancelled"], order_destination(ShiprocketOrder.STATUS_CANCELLED), "critical" if order_counts["cancelled"] else "positive"),
    ]

    routing_issues = 0
    if role in ROUTING_ROLES:
        has_mapping_rule = TenantWooCommerceMappingRule.objects.filter(
            tenant=tenant,
            is_active=True,
        ).exists()
        routing_issues = 0 if has_mapping_rule else product_counts["routing_missing"]

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
