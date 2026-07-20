from rest_framework import serializers

from core.models import Product, TenantWooCommerceMappingRule


class ProductListQuerySerializer(serializers.Serializer):
    search = serializers.CharField(required=False, allow_blank=True, max_length=160, trim_whitespace=True)
    stock_state = serializers.ChoiceField(
        required=False,
        choices=[
            ("in_stock", "In stock"),
            ("low_stock", "Low stock"),
            ("out_of_stock", "Out of stock"),
        ],
    )
    updated_after = serializers.DateTimeField(required=False)


def product_route_ready(product, rules):
    if any(
        str(value or "").strip()
        for value in [
            product.smartbiz_product_id,
            product.woocommerce_product_id,
            product.woocommerce_variation_id,
        ]
    ):
        return True
    sku = str(product.sku or "").strip().upper()
    category = str(product.category or "").strip().casefold()
    product_ids = {
        str(product.woocommerce_product_id or "").strip(),
        str(product.woocommerce_variation_id or "").strip(),
    }
    for rule in rules:
        value = str(rule.match_value or "").strip()
        if rule.match_type == TenantWooCommerceMappingRule.MATCH_SKU_PREFIX and sku.startswith(value.upper()):
            return True
        if rule.match_type == TenantWooCommerceMappingRule.MATCH_CATEGORY and category == value.casefold():
            return True
        if rule.match_type == TenantWooCommerceMappingRule.MATCH_PRODUCT_ID and value in product_ids:
            return True
    return False


class ProductSummarySerializer(serializers.ModelSerializer):
    barcode = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    stock_state = serializers.SerializerMethodField()
    route_ready = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "sku",
            "barcode",
            "image_url",
            "category",
            "stock_quantity",
            "reorder_level",
            "stock_state",
            "route_ready",
            "is_active",
            "updated_at",
        ]

    def get_barcode(self, product):
        return str(product.barcode or "").strip() or None

    def get_image_url(self, product):
        value = str(product.image_url or "").strip()
        return value if value.startswith(("https://", "http://")) else None

    def get_category(self, product):
        return str(product.category or "").strip() or None

    def get_stock_state(self, product):
        if product.stock_quantity <= 0:
            return "out_of_stock"
        if product.stock_quantity <= product.reorder_level:
            return "low_stock"
        return "in_stock"

    def get_route_ready(self, product):
        return product_route_ready(product, self.context.get("routing_rules", []))
