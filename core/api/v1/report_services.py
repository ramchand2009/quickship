"""Tenant-scoped mobile reporting services."""

from collections import OrderedDict
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models.functions import Coalesce
from django.utils import timezone

from core.models import Product, ShiprocketOrder
from core.stock import find_product_for_order_item


VALUE_STATUSES = {
    ShiprocketOrder.STATUS_ACCEPTED,
    ShiprocketOrder.STATUS_PACKED,
    ShiprocketOrder.STATUS_SHIPPED,
    ShiprocketOrder.STATUS_DELIVERY_ISSUE,
    ShiprocketOrder.STATUS_OUT_FOR_DELIVERY,
    ShiprocketOrder.STATUS_DELIVERED,
    ShiprocketOrder.STATUS_COMPLETED,
}


def _money(value):
    amount = Decimal(str(value or "0.00")).quantize(Decimal("0.01"))
    return {"amount": f"{amount:.2f}", "currency": "INR"}


def _decimal(value):
    text = str(value or "").strip().replace(",", "")
    if not text:
        return Decimal("0.00")
    for prefix in ["Rs", "rs", "INR", "inr", "₹"]:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    try:
        return Decimal(text)
    except Exception:
        return Decimal("0.00")


def _month_window(month=None, now=None):
    generated_at = now or timezone.now()
    if month:
        year, month_number = (int(part) for part in month.split("-", 1))
        month_start = timezone.make_aware(
            datetime(year, month_number, 1),
            timezone.get_current_timezone(),
        )
    else:
        month_start = timezone.localtime(generated_at).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month_start = (month_start + timedelta(days=32)).replace(day=1)
    month_end = next_month_start.date() - timedelta(days=1)
    return month_start, next_month_start, month_end


def _item_quantity(item):
    try:
        quantity = int(item.get("quantity") or 0)
    except (AttributeError, TypeError, ValueError):
        quantity = 0
    return max(0, quantity)


def _item_sales_total(item, quantity):
    if item.get("total") not in (None, ""):
        return _decimal(item.get("total"))
    unit_price = _decimal(
        item.get("price")
        or item.get("unit_price")
        or item.get("sale_price")
        or item.get("regular_price")
    )
    return unit_price * quantity


def build_product_sales_report(*, tenant, month=None, now=None):
    month_start, next_month_start, month_end = _month_window(month=month, now=now)
    orders = list(
        ShiprocketOrder.objects.filter(tenant=tenant, local_status__in=VALUE_STATUSES)
        .annotate(report_order_date=Coalesce("order_date", "created_at"))
        .filter(report_order_date__gte=month_start, report_order_date__lt=next_month_start)
        .only("tenant_id", "local_status", "order_items", "order_date", "created_at")
    )
    products = list(
        Product.objects.filter(tenant=tenant).only(
            "name",
            "sku",
            "smartbiz_product_id",
            "woocommerce_product_id",
            "woocommerce_variation_id",
            "actual_price",
        )
    )

    rows = OrderedDict()
    total_quantity = 0
    total_sales = Decimal("0.00")
    total_profit = Decimal("0.00")
    missing_cost_count = 0

    for order in orders:
        items = order.order_items if isinstance(order.order_items, list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            quantity = _item_quantity(item)
            if quantity <= 0:
                continue
            sales_total = _item_sales_total(item, quantity)
            product, _ = find_product_for_order_item(item, products=products)
            row_key = f"product:{product.pk}" if product else f"unmatched:{item.get('sku') or item.get('name') or 'item'}"
            if row_key not in rows:
                rows[row_key] = {
                    "product_id": product.pk if product else None,
                    "name": product.name if product else str(item.get("name") or "Unmatched item"),
                    "sku": product.sku if product else str(item.get("sku") or "").strip() or None,
                    "quantity": 0,
                    "sales": Decimal("0.00"),
                    "profit": Decimal("0.00"),
                    "missing_cost": False,
                }
            row = rows[row_key]
            row["quantity"] += quantity
            row["sales"] += sales_total
            total_quantity += quantity
            total_sales += sales_total

            if product and product.actual_price is not None:
                profit = sales_total - ((product.actual_price or Decimal("0.00")) * quantity)
                row["profit"] += profit
                total_profit += profit
            else:
                row["missing_cost"] = True

    product_rows = sorted(
        rows.values(),
        key=lambda row: (-row["sales"], str(row["name"]).casefold()),
    )
    missing_cost_count = sum(1 for row in product_rows if row["missing_cost"])

    return {
        "data": {
            "summary": {
                "product_count": len(product_rows),
                "total_quantity": total_quantity,
                "total_sales": _money(total_sales),
                "total_profit": _money(total_profit),
                "missing_cost_count": missing_cost_count,
            },
            "products": [
                {
                    "product_id": row["product_id"],
                    "name": row["name"],
                    "sku": row["sku"],
                    "quantity": row["quantity"],
                    "total_sales": _money(row["sales"]),
                    "total_profit": _money(row["profit"]),
                    "profit_complete": not row["missing_cost"],
                }
                for row in product_rows
            ],
        },
        "meta": {
            "period": {
                "month": month_start.strftime("%Y-%m"),
                "label": month_start.strftime("%B %Y"),
                "date_from": month_start.date().isoformat(),
                "date_to": month_end.isoformat(),
            },
        },
    }
