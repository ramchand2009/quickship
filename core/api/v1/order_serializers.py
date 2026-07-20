from decimal import Decimal

from rest_framework import serializers

from core.models import ShiprocketOrder, TenantMembership


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
