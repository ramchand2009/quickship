from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.models import BusinessExpense

from .expense_serializers import ExpenseSerializer
from .order_mutations import _begin_receipt, _complete_receipt, _delete_failed_receipt, _fingerprint


def _period(month):
    year, number = (int(value) for value in month.split("-", 1))
    start = timezone.make_aware(datetime(year, number, 1), timezone.get_current_timezone())
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def monthly_expenses(*, tenant, month):
    start, end = _period(month)
    expenses = list(BusinessExpense.objects.filter(tenant=tenant, created_at__gte=start, created_at__lt=end).select_related("expense_person"))
    total = sum((expense.total_amount for expense in expenses), Decimal("0.00"))
    return {
        "data": {"expenses": ExpenseSerializer(expenses, many=True).data, "total": f"{total:.2f}"},
        "meta": {"period": {"month": month, "label": start.strftime("%B %Y")}},
    }


def create_mobile_expense(*, session, tenant, actor, idempotency_key, values):
    request_hash = _fingerprint(operation="expense_create", order_id="expense", payload=values)
    receipt, replay = _begin_receipt(session=session, tenant=tenant, idempotency_key=idempotency_key, request_hash=request_hash)
    if replay is not None:
        expense = BusinessExpense.objects.filter(tenant=tenant, pk=replay.get("expense_id")).first()
        return {"data": {"expense": ExpenseSerializer(expense).data, "replayed": True}}
    try:
        with transaction.atomic():
            expense = BusinessExpense.objects.create(tenant=tenant, created_by=actor, **values)
        _complete_receipt(receipt, {"expense_id": expense.pk})
        return {"data": {"expense": ExpenseSerializer(expense).data, "replayed": False}}
    except Exception:
        _delete_failed_receipt(receipt)
        raise
