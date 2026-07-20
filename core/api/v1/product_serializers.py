from rest_framework import serializers

from core.models import Product, StockMovement, TenantMembership, TenantWooCommerceMappingRule


PRICE_VISIBILITY = {
    TenantMembership.ROLE_VENDOR_OWNER: {"actual", "regular", "sale"},
    TenantMembership.ROLE_VENDOR_OPERATOR: {"regular", "sale"},
    TenantMembership.ROLE_VENDOR_VIEWER: {"regular", "sale"},
    TenantMembership.ROLE_WAREHOUSE_OPERATOR: set(),
}


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


def _price(value):
    if value is None:
        return None
    return {"amount": f"{value:.2f}", "currency": "INR"}


class ProductDetailSerializer(ProductSummarySerializer):
    description = serializers.SerializerMethodField()
    prices = serializers.SerializerMethodField()
    routing = serializers.SerializerMethodField()

    class Meta(ProductSummarySerializer.Meta):
        fields = ProductSummarySerializer.Meta.fields + ["description", "prices", "routing"]

    def get_description(self, product):
        return str(product.description or "").strip() or None

    def get_prices(self, product):
        visible = PRICE_VISIBILITY.get(self.context.get("role"), set())
        return {
            "actual": _price(product.actual_price) if "actual" in visible else None,
            "regular": _price(product.regular_price) if "regular" in visible else None,
            "sale": _price(product.sale_price) if "sale" in visible else None,
        }

    def get_routing(self, product):
        show_identifiers = self.context.get("role") == TenantMembership.ROLE_VENDOR_OWNER
        return {
            "ready": product_route_ready(product, self.context.get("routing_rules", [])),
            "woocommerce_product_id": (
                str(product.woocommerce_product_id or "").strip() or None
                if show_identifiers
                else None
            ),
            "woocommerce_variation_id": (
                str(product.woocommerce_variation_id or "").strip() or None
                if show_identifiers
                else None
            ),
        }


class StockMovementQuerySerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=False, min_value=1)
    updated_after = serializers.DateTimeField(required=False)


class StockMovementSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField()
    order_id = serializers.SerializerMethodField()
    movement_type = serializers.SerializerMethodField()
    note = serializers.SerializerMethodField()
    actor_display_name = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "product_id",
            "order_id",
            "movement_type",
            "quantity_delta",
            "quantity_after",
            "note",
            "actor_display_name",
            "created_at",
        ]

    def get_order_id(self, movement):
        return getattr(movement, "safe_order_id", None)

    def get_movement_type(self, movement):
        return {
            "code": movement.movement_type,
            "label": movement.get_movement_type_display(),
        }

    def get_note(self, movement):
        role = self.context.get("role")
        if role == TenantMembership.ROLE_VENDOR_VIEWER:
            return None
        return str(movement.notes or "").strip() or None

    def get_actor_display_name(self, movement):
        if self.context.get("role") not in {
            TenantMembership.ROLE_VENDOR_OWNER,
            TenantMembership.ROLE_VENDOR_OPERATOR,
        }:
            return None
        return str(movement.triggered_by or "").strip() or None
