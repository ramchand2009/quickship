from django.db.models import BigIntegerField, Case, F, Q, Value, When

from core.models import Product, StockMovement, TenantWooCommerceMappingRule


def mobile_product_queryset(*, tenant, filters):
    queryset = Product.objects.filter(tenant=tenant)
    search = str(filters.get("search") or "").strip()
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search)
            | Q(sku__icontains=search)
            | Q(barcode__icontains=search)
        )
    stock_state = filters.get("stock_state")
    if stock_state == "out_of_stock":
        queryset = queryset.filter(stock_quantity__lte=0)
    elif stock_state == "low_stock":
        queryset = queryset.filter(
            stock_quantity__gt=0,
            stock_quantity__lte=F("reorder_level"),
        )
    elif stock_state == "in_stock":
        queryset = queryset.filter(stock_quantity__gt=F("reorder_level"))
    if filters.get("updated_after"):
        queryset = queryset.filter(updated_at__gt=filters["updated_after"])
    return queryset.only(
        "id",
        "name",
        "sku",
        "barcode",
        "image_url",
        "category",
        "stock_quantity",
        "reorder_level",
        "smartbiz_product_id",
        "woocommerce_product_id",
        "woocommerce_variation_id",
        "is_active",
        "created_at",
        "updated_at",
    )


def mobile_product_routing_rules(*, tenant):
    return list(
        TenantWooCommerceMappingRule.objects.filter(tenant=tenant, is_active=True)
        .only("match_type", "match_value")
        .order_by("pk")
    )


def mobile_product_detail(*, tenant, product_id):
    return Product.objects.filter(tenant=tenant, pk=product_id).only(
        "id",
        "tenant_id",
        "name",
        "sku",
        "barcode",
        "image_url",
        "category",
        "description",
        "actual_price",
        "regular_price",
        "sale_price",
        "stock_quantity",
        "reorder_level",
        "smartbiz_product_id",
        "woocommerce_product_id",
        "woocommerce_variation_id",
        "is_active",
        "created_at",
        "updated_at",
    ).first()


def mobile_stock_movement_queryset(*, tenant, filters):
    queryset = StockMovement.objects.filter(tenant=tenant, product__tenant=tenant)
    if filters.get("product_id"):
        queryset = queryset.filter(product_id=filters["product_id"])
    if filters.get("updated_after"):
        queryset = queryset.filter(created_at__gt=filters["updated_after"])
    return queryset.annotate(
        safe_order_id=Case(
            When(order__tenant=tenant, then=F("order_id")),
            default=Value(None),
            output_field=BigIntegerField(null=True),
        )
    ).only(
        "id",
        "product_id",
        "movement_type",
        "quantity_delta",
        "quantity_after",
        "notes",
        "triggered_by",
        "created_at",
    )
