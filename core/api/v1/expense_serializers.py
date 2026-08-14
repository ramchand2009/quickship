from rest_framework import serializers

from core.models import BusinessExpense


class ExpenseQuerySerializer(serializers.Serializer):
    month = serializers.RegexField(regex=r"^\d{4}-(0[1-9]|1[0-2])$")


class ExpenseCreateSerializer(serializers.Serializer):
    item_name = serializers.CharField(max_length=160)
    quantity = serializers.IntegerField(min_value=1, max_value=999999)
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    remark = serializers.CharField(max_length=255, required=False, allow_blank=True)


class ExpenseSerializer(serializers.ModelSerializer):
    total_amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)

    class Meta:
        model = BusinessExpense
        fields = ["id", "item_name", "quantity", "unit_price", "total_amount", "remark", "created_by", "created_at"]
