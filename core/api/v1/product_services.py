from django.db.models import F, Q

from core.models import Product, TenantWooCommerceMappingRule


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
