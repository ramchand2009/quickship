from collections import defaultdict

from django.db import transaction

from .activity import log_order_activity
from .models import (
    OrderActivityLog,
    Product,
    ShiprocketOrder,
    StockMovement,
    normalize_barcode,
    normalize_channel_product_id,
    normalize_sku,
)

def summarize_order_items_by_product(order):
    product_totals = defaultdict(int)
    matched_identifiers = defaultdict(list)
    missing_identifiers = []
    items = order.order_items if isinstance(order.order_items, list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            quantity = int(item.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        if quantity <= 0:
            continue
        product, matched_identifier = find_product_for_order_item(item)
        if not product:
            missing_identifier = (
                normalize_channel_product_id(
                    item.get("smartbiz_product_id")
                    or item.get("channel_product_id")
                    or item.get("product_id")
                    or item.get("variant_id")
                    or item.get("id")
                    or item.get("sku")
                    or item.get("channel_sku")
                )
                or item.get("name")
                or "Unknown item"
            )
            missing_identifiers.append(str(missing_identifier))
            continue
        product_totals[product.pk] += quantity
        if matched_identifier and matched_identifier not in matched_identifiers[product.pk]:
            matched_identifiers[product.pk].append(matched_identifier)
    return {
        "products": dict(product_totals),
        "matched_identifiers": dict(matched_identifiers),
        "missing_identifiers": missing_identifiers,
    }


def find_product_by_lookup(raw_value):
    lookup = str(raw_value or "").strip()
    if not lookup:
        return None

    barcode = normalize_barcode(lookup)
    if barcode:
        product = Product.objects.filter(barcode=barcode).first()
        if product:
            return product

    sku = normalize_sku(lookup)
    if sku:
        product = Product.objects.filter(sku=sku).first()
        if product:
            return product

    smartbiz_product_id = normalize_channel_product_id(lookup)
    if smartbiz_product_id:
        return Product.objects.filter(smartbiz_product_id__iexact=smartbiz_product_id).first()
    return None


def find_product_for_order_item(item):
    if not isinstance(item, dict):
        return None, ""

    sku = normalize_sku(item.get("sku"))
    if sku:
        product = Product.objects.filter(sku=sku).first()
        if product:
            return product, sku

    channel_sku = normalize_sku(item.get("channel_sku"))
    if channel_sku:
        product = Product.objects.filter(sku=channel_sku).first()
        if product:
            return product, channel_sku

    smartbiz_product_id = normalize_channel_product_id(
        item.get("smartbiz_product_id")
        or item.get("channel_product_id")
        or item.get("product_id")
        or item.get("variant_id")
        or item.get("id")
    )
    if smartbiz_product_id:
        product = Product.objects.filter(smartbiz_product_id__iexact=smartbiz_product_id).first()
        if product:
            return product, smartbiz_product_id

    if sku:
        product = Product.objects.filter(smartbiz_product_id__iexact=sku).first()
        if product:
            return product, sku

    return None, ""


def apply_manual_stock_movement(*, product, movement_type, quantity, actor="", notes=""):
    quantity = int(quantity or 0)
    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")

    if movement_type == StockMovement.TYPE_MANUAL_ADD:
        delta = quantity
    elif movement_type == StockMovement.TYPE_MANUAL_REMOVE:
        delta = -quantity
    else:
        raise ValueError("Unsupported manual movement type.")

    return _create_stock_movement(
        product=product,
        quantity_delta=delta,
        movement_type=movement_type,
        actor=actor,
        notes=notes,
    )


def set_manual_stock_quantity(*, product, target_quantity, actor="", notes=""):
    target_quantity = int(target_quantity or 0)
    with transaction.atomic():
        locked_product = Product.objects.select_for_update().get(pk=product.pk)
        delta = target_quantity - int(locked_product.stock_quantity or 0)
    if delta == 0:
        return None, False
    return _create_stock_movement(
        product=product,
        quantity_delta=delta,
        movement_type=StockMovement.TYPE_MANUAL_SET,
        actor=actor,
        notes=notes,
    )


def sync_stock_for_status_transition(*, order, previous_status, current_status, actor=""):
    previous_status = str(previous_status or "").strip()
    current_status = str(current_status or "").strip()
    result = {
        "mode": "",
        "changed": False,
        "movement_count": 0,
        "missing_skus": [],
        "skipped_skus": [],
        "duplicate_refs": [],
    }
    if previous_status == current_status:
        return result

    if current_status == ShiprocketOrder.STATUS_ACCEPTED:
        result["mode"] = "deduct"
        return _apply_order_stock_deduction(order=order, actor=actor, result=result)
    if current_status == ShiprocketOrder.STATUS_CANCELLED:
        result["mode"] = "restore"
        return _apply_order_stock_restore(order=order, actor=actor, result=result)
    return result


def reconcile_missed_stock_deductions(*, actor=""):
    eligible_statuses = [
        ShiprocketOrder.STATUS_ACCEPTED,
        ShiprocketOrder.STATUS_PACKED,
        ShiprocketOrder.STATUS_SHIPPED,
        ShiprocketOrder.STATUS_DELIVERY_ISSUE,
        ShiprocketOrder.STATUS_OUT_FOR_DELIVERY,
        ShiprocketOrder.STATUS_DELIVERED,
        ShiprocketOrder.STATUS_COMPLETED,
    ]
    summary = {
        "orders_scanned": 0,
        "orders_changed": 0,
        "movement_count": 0,
        "missing_skus": [],
    }
    missing_identifiers = set()
    orders = ShiprocketOrder.objects.filter(local_status__in=eligible_statuses).order_by("-updated_at")
    for order in orders:
        summary["orders_scanned"] += 1
        result = {
            "mode": "deduct",
            "changed": False,
            "movement_count": 0,
            "missing_skus": [],
            "skipped_skus": [],
            "duplicate_refs": [],
        }
        result = _apply_order_stock_deduction(order=order, actor=actor, result=result)
        if result["changed"]:
            summary["orders_changed"] += 1
            summary["movement_count"] += int(result["movement_count"] or 0)
        missing_identifiers.update(result.get("missing_skus") or [])
    summary["missing_skus"] = sorted(missing_identifiers)
    return summary


def _apply_order_stock_deduction(*, order, actor, result):
    product_summary = summarize_order_items_by_product(order)
    product_totals = product_summary["products"]
    if not product_totals:
        result["missing_skus"] = product_summary["missing_identifiers"]
        return result

    for product_id, quantity in product_totals.items():
        product = Product.objects.filter(pk=product_id).first()
        if not product:
            continue
        identifier_list = product_summary["matched_identifiers"].get(product_id) or [product.sku]
        identifier_label = ", ".join(identifier_list)

        reference_key = f"order:{order.pk}:accepted:{product.pk}"
        movement, created = _create_stock_movement(
            product=product,
            order=order,
            quantity_delta=-int(quantity),
            movement_type=StockMovement.TYPE_ORDER_ACCEPTED,
            actor=actor,
            notes=f"Automatic deduction for order {order.shiprocket_order_id}",
            reference_key=reference_key,
        )
        if created:
            result["changed"] = True
            result["movement_count"] += 1
            log_order_activity(
                order=order,
                event_type=OrderActivityLog.EVENT_STOCK_DEDUCTED,
                title=f"Stock deducted for {product.sku}",
                description=(
                    f"Deducted {quantity} unit(s) from {product.name}. "
                    f"Stock: {movement.quantity_before} -> {movement.quantity_after}. "
                    f"Matched by {identifier_label}."
                ),
                previous_status=order.local_status,
                current_status=order.local_status,
                metadata={
                    "sku": product.sku,
                    "matched_identifiers": identifier_list,
                    "product_id": product.pk,
                    "quantity_delta": movement.quantity_delta,
                    "quantity_before": movement.quantity_before,
                    "quantity_after": movement.quantity_after,
                },
                is_success=True,
                triggered_by=actor,
            )
        else:
            result["duplicate_refs"].append(product.sku)

    result["missing_skus"].extend(product_summary["missing_identifiers"])
    if result["missing_skus"]:
        log_order_activity(
            order=order,
            event_type=OrderActivityLog.EVENT_STOCK_WARNING,
            title="Stock deduction skipped for missing product mappings",
            description=f"No product mapping found for: {', '.join(result['missing_skus'])}.",
            previous_status=order.local_status,
            current_status=order.local_status,
            metadata={"missing_skus": result["missing_skus"]},
            is_success=False,
            triggered_by=actor,
        )
    return result


def _apply_order_stock_restore(*, order, actor, result):
    product_summary = summarize_order_items_by_product(order)
    product_totals = product_summary["products"]
    if not product_totals:
        result["missing_skus"] = product_summary["missing_identifiers"]
        return result

    for product_id, quantity in product_totals.items():
        product = Product.objects.filter(pk=product_id).first()
        if not product:
            continue
        identifier_list = product_summary["matched_identifiers"].get(product_id) or [product.sku]
        identifier_label = ", ".join(identifier_list)

        accepted_reference = f"order:{order.pk}:accepted:{product.pk}"
        if not StockMovement.objects.filter(reference_key=accepted_reference).exists():
            result["skipped_skus"].append(product.sku)
            continue

        reference_key = f"order:{order.pk}:cancelled:{product.pk}"
        movement, created = _create_stock_movement(
            product=product,
            order=order,
            quantity_delta=int(quantity),
            movement_type=StockMovement.TYPE_ORDER_CANCELLED,
            actor=actor,
            notes=f"Automatic restore for cancelled order {order.shiprocket_order_id}",
            reference_key=reference_key,
        )
        if created:
            result["changed"] = True
            result["movement_count"] += 1
            log_order_activity(
                order=order,
                event_type=OrderActivityLog.EVENT_STOCK_RESTORED,
                title=f"Stock restored for {product.sku}",
                description=(
                    f"Restored {quantity} unit(s) to {product.name}. "
                    f"Stock: {movement.quantity_before} -> {movement.quantity_after}. "
                    f"Matched by {identifier_label}."
                ),
                previous_status=order.local_status,
                current_status=order.local_status,
                metadata={
                    "sku": product.sku,
                    "matched_identifiers": identifier_list,
                    "product_id": product.pk,
                    "quantity_delta": movement.quantity_delta,
                    "quantity_before": movement.quantity_before,
                    "quantity_after": movement.quantity_after,
                },
                is_success=True,
                triggered_by=actor,
            )
        else:
            result["duplicate_refs"].append(product.sku)

    result["missing_skus"].extend(product_summary["missing_identifiers"])
    if result["missing_skus"]:
        log_order_activity(
            order=order,
            event_type=OrderActivityLog.EVENT_STOCK_WARNING,
            title="Stock restore skipped for missing product mappings",
            description=f"No product mapping found for: {', '.join(result['missing_skus'])}.",
            previous_status=order.local_status,
            current_status=order.local_status,
            metadata={"missing_skus": result["missing_skus"]},
            is_success=False,
            triggered_by=actor,
        )
    return result


def _create_stock_movement(
    *,
    product,
    quantity_delta,
    movement_type,
    actor="",
    notes="",
    order=None,
    reference_key=None,
):
    quantity_delta = int(quantity_delta or 0)
    if quantity_delta == 0:
        return None, False

    reference_key = str(reference_key or "").strip() or None
    with transaction.atomic():
        if reference_key:
            existing = StockMovement.objects.select_for_update().filter(reference_key=reference_key).first()
            if existing:
                return existing, False

        locked_product = Product.objects.select_for_update().get(pk=product.pk)
        quantity_before = int(locked_product.stock_quantity or 0)
        quantity_after = quantity_before + quantity_delta
        locked_product.stock_quantity = quantity_after
        locked_product.save(update_fields=["stock_quantity", "updated_at"])

        movement = StockMovement.objects.create(
            product=locked_product,
            order=order,
            shiprocket_order_id=str(getattr(order, "shiprocket_order_id", "") or "").strip(),
            movement_type=movement_type,
            quantity_delta=quantity_delta,
            quantity_before=quantity_before,
            quantity_after=quantity_after,
            sku_snapshot=locked_product.sku,
            barcode_snapshot=normalize_barcode(locked_product.barcode),
            reference_key=reference_key,
            notes=str(notes or "").strip(),
            triggered_by=str(actor or "").strip(),
        )
    return movement, True
