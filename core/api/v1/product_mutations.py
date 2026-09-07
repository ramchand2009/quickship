"""Transactional product mutations for the Android API."""

from django.db import transaction
from rest_framework.exceptions import NotFound

from core.models import Product
from core.stock import set_manual_stock_quantity
from core.woocommerce import WooCommerceAPIError, update_product as update_woocommerce_product

from .exceptions import ConflictError
from .order_mutations import _begin_receipt, _complete_receipt, _delete_failed_receipt, _fingerprint
from .product_serializers import ProductDetailSerializer, StockMovementSerializer
from .product_services import mobile_product_detail, mobile_product_routing_rules


def update_mobile_product(
    *, session, tenant, role, product_id, idempotency_key, values
):
    request_hash = _fingerprint(
        operation="product_update",
        order_id=f"product:{product_id}",
        payload=values,
    )
    receipt, replay = _begin_receipt(
        session=session,
        tenant=tenant,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        product = mobile_product_detail(tenant=tenant, product_id=product_id)
        if product is None:
            raise NotFound("The requested resource is unavailable.")
        return _serialize_result(
            tenant=tenant,
            role=role,
            product=product,
            movement=None,
            replayed=True,
            effects=replay.get("effects") if isinstance(replay, dict) else [],
        )

    try:
        with transaction.atomic():
            product = Product.objects.select_for_update().filter(tenant=tenant, pk=product_id).first()
            if product is None:
                raise NotFound("The requested resource is unavailable.")
            if product.updated_at.isoformat() != values["expected_updated_at"].isoformat():
                raise ConflictError(fields={"expected_updated_at": ["Refresh the product and try again."]})

            sku = str(values["sku"] or "").strip().upper()
            barcode = str(values.get("barcode") or "").strip() or None
            if Product.objects.filter(sku__iexact=sku).exclude(pk=product.pk).exists():
                raise ConflictError(fields={"sku": ["This SKU is already used by another product."]})
            if barcode and Product.objects.filter(barcode__iexact=barcode).exclude(pk=product.pk).exists():
                raise ConflictError(fields={"barcode": ["This barcode is already used by another product."]})

            product.name = values["name"]
            product.sku = sku
            product.barcode = barcode
            product.category = values.get("category") or ""
            product.category_master = None
            product.description = values.get("description") or ""
            product.actual_price = values.get("actual_price")
            product.regular_price = values.get("regular_price")
            product.sale_price = values.get("sale_price")
            product.reorder_level = values["reorder_level"]
            product.is_active = values.get("is_active", product.is_active)
            product.save(
                update_fields=[
                    "name",
                    "sku",
                    "barcode",
                    "category",
                    "category_master",
                    "description",
                    "actual_price",
                    "regular_price",
                    "sale_price",
                    "reorder_level",
                    "is_active",
                    "updated_at",
                ]
            )

        effects = []
        try:
            update_woocommerce_product(product)
            effects.append({
                "code": "woocommerce_sync",
                "state": "completed",
                "message": "WooCommerce product details updated.",
            })
        except WooCommerceAPIError as exc:
            effects.append({
                "code": "woocommerce_sync",
                "state": "warning",
                "message": f"Saved locally, but WooCommerce product sync failed: {exc}",
            })

        _complete_receipt(
            receipt,
            {
                "product_id": product_id,
                "movement_id": None,
                "effects": effects,
            },
        )
        return _serialize_result(
            tenant=tenant,
            role=role,
            product=product,
            movement=None,
            replayed=False,
            effects=effects,
        )
    except Exception:
        _delete_failed_receipt(receipt)
        raise


def set_mobile_stock_quantity(
    *, session, tenant, role, actor, product_id, idempotency_key, values
):
    request_hash = _fingerprint(
        operation="stock_quantity",
        order_id=f"product:{product_id}",
        payload=values,
    )
    receipt, replay = _begin_receipt(
        session=session,
        tenant=tenant,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        product = mobile_product_detail(tenant=tenant, product_id=product_id)
        if product is None:
            raise NotFound("The requested resource is unavailable.")
        return _serialize_result(
            tenant=tenant,
            role=role,
            product=product,
            movement=None,
            replayed=True,
        )

    try:
        with transaction.atomic():
            product = Product.objects.select_for_update().filter(tenant=tenant, pk=product_id).first()
            if product is None:
                raise NotFound("The requested resource is unavailable.")
            if int(product.stock_quantity or 0) != values["expected_quantity"]:
                raise ConflictError(fields={"expected_quantity": ["Refresh the product and try again."]})

            movement, _ = set_manual_stock_quantity(
                product=product,
                target_quantity=values["target_quantity"],
                actor=actor,
                notes=values.get("note") or "",
            )
            product.refresh_from_db()

        effects = []
        try:
            update_woocommerce_product(product, extra_fields={"stock_quantity": product.stock_quantity})
            effects.append({
                "code": "woocommerce_sync",
                "state": "completed",
                "message": "WooCommerce stock quantity updated.",
            })
        except WooCommerceAPIError as exc:
            effects.append({
                "code": "woocommerce_sync",
                "state": "warning",
                "message": f"Saved locally, but WooCommerce stock sync failed: {exc}",
            })

        _complete_receipt(
            receipt,
            {
                "product_id": product_id,
                "movement_id": movement.pk if movement else None,
                "effects": effects,
            },
        )
        return _serialize_result(
            tenant=tenant,
            role=role,
            product=product,
            movement=movement,
            replayed=False,
            effects=effects,
        )
    except Exception:
        _delete_failed_receipt(receipt)
        raise


def _serialize_result(*, tenant, role, product, movement, replayed, effects=None):
    product_data = ProductDetailSerializer(
        product,
        context={
            "role": role,
            "routing_rules": mobile_product_routing_rules(tenant=tenant),
        },
    ).data
    movement_data = (
        StockMovementSerializer(movement, context={"role": role}).data
        if movement is not None
        else None
    )
    return {
        "data": {
            "product": product_data,
            "movement": movement_data,
            "replayed": replayed,
            "effects": effects or [],
        }
    }
