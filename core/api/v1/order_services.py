from django.db.models import Q

from core.models import ShiprocketOrder, TenantMembership


CUSTOMER_SEARCH_ROLES = {
    TenantMembership.ROLE_VENDOR_OWNER,
    TenantMembership.ROLE_VENDOR_OPERATOR,
}


def mobile_order_queryset(*, tenant, role, filters):
    queryset = ShiprocketOrder.objects.filter(tenant=tenant)
    status = filters.get("status")
    if status:
        queryset = queryset.filter(local_status=status)
    payment_state = filters.get("payment_state")
    if payment_state == "received":
        queryset = queryset.filter(payment_received_at__isnull=False)
    elif payment_state == "pending":
        queryset = queryset.filter(payment_received_at__isnull=True)
    if filters.get("date_from"):
        queryset = queryset.filter(order_date__date__gte=filters["date_from"])
    if filters.get("date_to"):
        queryset = queryset.filter(order_date__date__lte=filters["date_to"])
    if filters.get("updated_after"):
        queryset = queryset.filter(updated_at__gt=filters["updated_after"])

    search = str(filters.get("search") or "").strip()
    if search:
        permitted_search = (
            Q(shiprocket_order_id__icontains=search)
            | Q(channel_order_id__icontains=search)
            | Q(woocommerce_order_id__icontains=search)
            | Q(tracking_number__icontains=search)
        )
        if role in CUSTOMER_SEARCH_ROLES:
            permitted_search |= (
                Q(customer_name__icontains=search)
                | Q(manual_customer_name__icontains=search)
                | Q(customer_email__icontains=search)
                | Q(manual_customer_email__icontains=search)
                | Q(customer_phone__icontains=search)
                | Q(manual_customer_phone__icontains=search)
            )
        queryset = queryset.filter(permitted_search)

    return queryset.only(
        "id",
        "source",
        "shiprocket_order_id",
        "woocommerce_order_id",
        "channel_order_id",
        "customer_name",
        "manual_customer_name",
        "local_status",
        "payment_received_at",
        "order_items",
        "total",
        "order_date",
        "tracking_number",
        "version",
        "created_at",
        "updated_at",
    )
