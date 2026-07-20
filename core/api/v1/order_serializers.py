from decimal import Decimal

from rest_framework import serializers

from core.models import ShiprocketOrder, TenantMembership

from .session_services import ROLE_PERMISSIONS


class OrderListQuerySerializer(serializers.Serializer):
    search = serializers.CharField(required=False, allow_blank=True, max_length=160, trim_whitespace=True)
    status = serializers.ChoiceField(required=False, choices=ShiprocketOrder.STATUS_CHOICES)
    payment_state = serializers.ChoiceField(
        required=False,
        choices=[("pending", "Pending"), ("received", "Received")],
    )
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    updated_after = serializers.DateTimeField(required=False)

    def validate(self, attrs):
        if attrs.get("date_from") and attrs.get("date_to") and attrs["date_from"] > attrs["date_to"]:
            raise serializers.ValidationError({"date_to": "Must be on or after date_from."})
        return attrs


class OrderSummarySerializer(serializers.ModelSerializer):
    reference = serializers.SerializerMethodField()
    source = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    payment_state = serializers.SerializerMethodField()
    customer_display_name = serializers.SerializerMethodField()
    item_count = serializers.SerializerMethodField()
    total = serializers.SerializerMethodField()
    tracking_number = serializers.SerializerMethodField()
    attention_required = serializers.SerializerMethodField()
    version = serializers.SerializerMethodField()

    class Meta:
        model = ShiprocketOrder
        fields = [
            "id",
            "reference",
            "source",
            "status",
            "payment_state",
            "customer_display_name",
            "item_count",
            "total",
            "order_date",
            "tracking_number",
            "attention_required",
            "version",
            "updated_at",
        ]

    def get_reference(self, order):
        return order.source_order_reference

    def get_source(self, order):
        return {"code": order.source, "label": order.source_label}

    def get_status(self, order):
        return {"code": order.local_status, "label": order.get_local_status_display()}

    def get_payment_state(self, order):
        received = order.payment_received_at is not None
        return {
            "code": "received" if received else "pending",
            "label": "Received" if received else "Pending",
        }

    def get_customer_display_name(self, order):
        name = str(order.manual_customer_name or order.customer_name or "").strip()
        if not name:
            return None
        role = self.context.get("role")
        if role in {
            TenantMembership.ROLE_VENDOR_OWNER,
            TenantMembership.ROLE_VENDOR_OPERATOR,
        }:
            return name
        return f"{name[0]}•••"

    def get_item_count(self, order):
        items = order.order_items if isinstance(order.order_items, list) else []
        total = 0
        for item in items:
            try:
                total += max(0, int((item or {}).get("quantity") or 1))
            except (TypeError, ValueError):
                total += 1
        return total

    def get_total(self, order):
        amount = order.total if order.total is not None else Decimal("0.00")
        return {"amount": f"{amount:.2f}", "currency": "INR"}

    def get_tracking_number(self, order):
        return str(order.tracking_number or "").strip() or None

    def get_attention_required(self, order):
        return order.local_status == ShiprocketOrder.STATUS_DELIVERY_ISSUE

    def get_version(self, order):
        return str(order.version)


def _money(value):
    amount = value if value is not None else Decimal("0.00")
    return {"amount": f"{amount:.2f}", "currency": "INR"}


def _masked_name(name):
    value = str(name or "").strip()
    return f"{value[0]}•••" if value else None


class OrderDetailSerializer(OrderSummarySerializer):
    customer = serializers.SerializerMethodField()
    items = serializers.SerializerMethodField()
    courier_name = serializers.SerializerMethodField()
    shipping_cost = serializers.SerializerMethodField()
    payment_received_at = serializers.DateTimeField(allow_null=True)
    cancellation_reason = serializers.SerializerMethodField()
    cancellation_note = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()
    activity = serializers.SerializerMethodField()

    class Meta(OrderSummarySerializer.Meta):
        fields = OrderSummarySerializer.Meta.fields + [
            "customer",
            "items",
            "courier_name",
            "shipping_cost",
            "payment_received_at",
            "cancellation_reason",
            "cancellation_note",
            "allowed_actions",
            "activity",
        ]

    def get_customer(self, order):
        role = self.context.get("role")
        address = order.display_shipping_address
        name = address.get("name") or None
        phone = address.get("phone") or None
        email = address.get("email") or None
        delivery_address = ", ".join(
            str(address.get(key) or "").strip()
            for key in ["address_1", "address_2", "city", "state", "pincode", "country"]
            if str(address.get(key) or "").strip()
        ) or None
        if role in {
            TenantMembership.ROLE_VENDOR_OWNER,
            TenantMembership.ROLE_VENDOR_OPERATOR,
        }:
            return {
                "name": name,
                "phone": phone,
                "email": email,
                "delivery_address": delivery_address,
                "fields_masked": [],
            }
        if role == TenantMembership.ROLE_WAREHOUSE_OPERATOR:
            return {
                "name": name,
                "phone": phone,
                "email": None,
                "delivery_address": delivery_address,
                "fields_masked": ["email"],
            }
        return {
            "name": _masked_name(name),
            "phone": None,
            "email": None,
            "delivery_address": None,
            "fields_masked": ["name", "phone", "email", "delivery_address"],
        }

    def get_items(self, order):
        serialized = []
        for raw_item in order.order_items if isinstance(order.order_items, list) else []:
            item = raw_item if isinstance(raw_item, dict) else {}
            try:
                quantity = max(1, int(item.get("quantity") or 1))
            except (TypeError, ValueError):
                quantity = 1
            try:
                unit_price = Decimal(str(item.get("price") or item.get("unit_price") or "0"))
            except Exception:
                unit_price = Decimal("0.00")
            try:
                total = Decimal(str(item.get("total"))) if item.get("total") is not None else unit_price * quantity
            except Exception:
                total = unit_price * quantity
            product_id = item.get("product_id")
            try:
                product_id = int(product_id) if product_id is not None else None
            except (TypeError, ValueError):
                product_id = None
            image_url = str(item.get("image_url") or item.get("image") or "").strip()
            if not image_url.startswith(("https://", "http://")):
                image_url = None
            serialized.append(
                {
                    "product_id": product_id,
                    "name": str(item.get("name") or item.get("item_name") or "Item"),
                    "sku": str(item.get("sku") or "").strip() or None,
                    "quantity": quantity,
                    "total": _money(total),
                    "image_url": image_url,
                }
            )
        return serialized

    def get_courier_name(self, order):
        return order.courier_name or None

    def get_shipping_cost(self, order):
        return _money(order.shipping_base_amount)

    def get_cancellation_reason(self, order):
        return order.cancellation_reason or None

    def get_cancellation_note(self, order):
        return order.cancellation_note or None

    def get_allowed_actions(self, order):
        role = self.context.get("role")
        permissions = set(ROLE_PERMISSIONS.get(role, []))
        actions = []
        if "orders.update_status" in permissions:
            for target in ShiprocketOrder.ALLOWED_STATUS_TRANSITIONS.get(order.local_status, []):
                if target == ShiprocketOrder.STATUS_PACKED:
                    continue
                required_fields = []
                if target == ShiprocketOrder.STATUS_ACCEPTED and not order.resolved_customer_phone:
                    required_fields = ["customer_phone"]
                elif target == ShiprocketOrder.STATUS_SHIPPED:
                    required_fields = ["courier_name", "tracking_number", "shipping_base_amount"]
                actions.append(
                    {
                        "code": "update_status",
                        "label": f"Move to {dict(ShiprocketOrder.STATUS_CHOICES)[target]}",
                        "target_status": target,
                        "confirmation_required": target == ShiprocketOrder.STATUS_CANCELLED,
                        "reason_required": target == ShiprocketOrder.STATUS_CANCELLED,
                        "required_fields": required_fields,
                    }
                )
        if "orders.mark_payment_received" in permissions and order.payment_received_at is None:
            actions.append(
                {
                    "code": "mark_payment_received",
                    "label": "Mark payment received",
                    "target_status": None,
                    "confirmation_required": True,
                    "reason_required": False,
                    "required_fields": [],
                }
            )
        return actions

    def get_activity(self, order):
        role = self.context.get("role")
        show_actor = role in {
            TenantMembership.ROLE_VENDOR_OWNER,
            TenantMembership.ROLE_VENDOR_OPERATOR,
        }
        return [
            {
                "id": entry.pk,
                "title": entry.title or "Order activity",
                "description": entry.description or None,
                "actor_display_name": entry.triggered_by or None if show_actor else None,
                "previous_status": entry.previous_status or None,
                "current_status": entry.current_status or None,
                "created_at": entry.created_at,
            }
            for entry in self.context.get("activity", [])
        ]
