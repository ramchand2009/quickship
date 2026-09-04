from rest_framework import serializers


class MobileCustomerProfileSerializer(serializers.Serializer):
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
        value = str(value or "").strip()
        digits = "".join(character for character in value if character.isdigit())
        if len(digits) < 10:
            raise serializers.ValidationError("Enter a valid mobile number.")
        return value

    def validate_pincode(self, value):
        value = str(value or "").strip()
        if value and len(value) < 3:
            raise serializers.ValidationError("Enter a valid pincode.")
        return value
