from rest_framework import serializers


class ManualOrderCustomerSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=160, trim_whitespace=True)
    phone = serializers.CharField(max_length=32, trim_whitespace=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    address_1 = serializers.CharField(required=False, allow_blank=True, max_length=255, trim_whitespace=True)
    address_2 = serializers.CharField(required=False, allow_blank=True, max_length=255, trim_whitespace=True)
    city = serializers.CharField(required=False, allow_blank=True, max_length=120, trim_whitespace=True)
    state = serializers.CharField(required=False, allow_blank=True, max_length=120, trim_whitespace=True)
    pincode = serializers.CharField(required=False, allow_blank=True, max_length=20, trim_whitespace=True)
    country = serializers.CharField(required=False, allow_blank=True, max_length=120, trim_whitespace=True)

    def validate_phone(self, value):
        digits = "".join(character for character in str(value or "") if character.isdigit())
        if len(digits) < 10:
            raise serializers.ValidationError("Enter a valid mobile number.")
        return value


class ManualOrderItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1, max_value=999)


class ManualOrderCreateSerializer(serializers.Serializer):
    customer_key = serializers.CharField(required=False, allow_blank=True, max_length=120, trim_whitespace=True)
    customer = ManualOrderCustomerSerializer(required=False)
    items = ManualOrderItemSerializer(many=True)
    shipping_mode = serializers.ChoiceField(
        choices=["free", "charged"],
        required=False,
        default="free",
    )
    shipping_base_amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0,
        required=False,
        default=0,
    )
    note = serializers.CharField(required=False, allow_blank=True, max_length=255, trim_whitespace=True)

    def validate(self, attrs):
        if not attrs.get("customer_key") and not attrs.get("customer"):
            raise serializers.ValidationError({"customer": ["Select a customer or enter new customer details."]})
        if not attrs.get("items"):
            raise serializers.ValidationError({"items": ["Select at least one product."]})
        if attrs.get("shipping_mode") == "charged" and not attrs.get("shipping_base_amount"):
            raise serializers.ValidationError({"shipping_base_amount": ["Enter the shipping charge, or choose free shipping."]})
        if attrs.get("shipping_mode") == "free":
            attrs["shipping_base_amount"] = 0
        return attrs
