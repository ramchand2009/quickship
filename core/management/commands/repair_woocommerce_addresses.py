from django.core.management.base import BaseCommand

from core.models import ShiprocketOrder, compact_woocommerce_address


ADDRESS_FIELDS = ["address_1", "address_2", "city", "state", "country", "pincode"]
CONTACT_FIELDS = ["name", "email", "phone"]


def _has_address(address):
    return any(str((address or {}).get(field) or "").strip() for field in ADDRESS_FIELDS)


def _merged_address(order):
    payload = order.raw_payload if isinstance(order.raw_payload, dict) else {}
    billing = order.billing_address if isinstance(order.billing_address, dict) else {}
    shipping = order.shipping_address if isinstance(order.shipping_address, dict) else {}

    raw_billing = compact_woocommerce_address(payload.get("billing") or {})
    raw_shipping = compact_woocommerce_address(payload.get("shipping") or {})

    if not _has_address(billing) and _has_address(raw_billing):
        billing = {**billing, **raw_billing}

    if not shipping and raw_shipping:
        shipping = raw_shipping

    if _has_address(billing):
        merged_shipping = dict(shipping)
        for field in CONTACT_FIELDS:
            if not merged_shipping.get(field):
                merged_shipping[field] = billing.get(field, "")
        for field in ADDRESS_FIELDS:
            merged_shipping[field] = billing.get(field) or merged_shipping.get(field, "")
    else:
        merged_shipping = shipping

    return billing, merged_shipping


class Command(BaseCommand):
    help = "Backfill WooCommerce billing addresses into Quickship delivery addresses."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Save the repaired addresses. Without this flag, only prints what would change.",
        )

    def handle(self, *args, **options):
        confirm = options["confirm"]
        checked = 0
        changed = 0

        queryset = ShiprocketOrder.objects.filter(source=ShiprocketOrder.SOURCE_WOOCOMMERCE).order_by("id")
        for order in queryset:
            checked += 1
            billing, shipping = _merged_address(order)
            update_fields = []
            if billing != (order.billing_address or {}):
                order.billing_address = billing
                update_fields.append("billing_address")
            if shipping != (order.shipping_address or {}):
                order.shipping_address = shipping
                update_fields.append("shipping_address")

            if update_fields:
                changed += 1
                self.stdout.write(f"{order.shiprocket_order_id}: repair {', '.join(update_fields)}")
                if confirm:
                    order.save(update_fields=update_fields + ["updated_at"])

        mode = "updated" if confirm else "would update"
        self.stdout.write(self.style.SUCCESS(f"Checked {checked} WooCommerce orders; {mode} {changed}."))
