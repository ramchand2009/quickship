from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import SenderAddress, ShiprocketOrder


STATUS_SEQUENCE = [
    ShiprocketOrder.STATUS_NEW,
    ShiprocketOrder.STATUS_ACCEPTED,
    ShiprocketOrder.STATUS_PACKED,
    ShiprocketOrder.STATUS_PACKED,
    ShiprocketOrder.STATUS_SHIPPED,
    ShiprocketOrder.STATUS_DELIVERY_ISSUE,
    ShiprocketOrder.STATUS_OUT_FOR_DELIVERY,
    ShiprocketOrder.STATUS_DELIVERED,
    ShiprocketOrder.STATUS_COMPLETED,
    ShiprocketOrder.STATUS_CANCELLED,
]


class Command(BaseCommand):
    help = "Seed demo Shiprocket orders for local workflow and label testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=8,
            help="Number of demo orders to seed (default: 8).",
        )

    def handle(self, *args, **options):
        count = options["count"]
        if count < 1:
            raise CommandError("--count must be at least 1.")

        self._ensure_sender_address()

        created = 0
        updated = 0
        now = timezone.now()

        for index in range(1, count + 1):
            local_status = STATUS_SEQUENCE[(index - 1) % len(STATUS_SEQUENCE)]
            order_id = f"DEMO-ST4-{index:04d}"
            shipped_at = None
            out_for_delivery_at = None
            delivered_at = None
            completed_at = None

            if local_status in {
                ShiprocketOrder.STATUS_SHIPPED,
                ShiprocketOrder.STATUS_DELIVERY_ISSUE,
                ShiprocketOrder.STATUS_OUT_FOR_DELIVERY,
                ShiprocketOrder.STATUS_DELIVERED,
                ShiprocketOrder.STATUS_COMPLETED,
            }:
                shipped_at = now - timedelta(hours=index)
            if local_status in {
                ShiprocketOrder.STATUS_OUT_FOR_DELIVERY,
                ShiprocketOrder.STATUS_DELIVERED,
                ShiprocketOrder.STATUS_COMPLETED,
            }:
                out_for_delivery_at = now - timedelta(hours=max(index - 1, 0))
            if local_status in {ShiprocketOrder.STATUS_DELIVERED, ShiprocketOrder.STATUS_COMPLETED}:
                delivered_at = now - timedelta(hours=max(index - 2, 0))
            if local_status == ShiprocketOrder.STATUS_COMPLETED:
                completed_at = now - timedelta(hours=max(index - 3, 0))

            shipping_phone = f"900000{index:04d}"
            shipping_pincode = f"{600000 + index}"
            # Keep one accepted order intentionally incomplete to test packing checklist.
            if local_status == ShiprocketOrder.STATUS_ACCEPTED and index % 2 == 0:
                shipping_phone = ""
                shipping_pincode = ""

            defaults = {
                "channel_order_id": f"CHANNEL-{index:04d}",
                "customer_name": f"Demo Customer {index}",
                "customer_email": f"demo{index}@example.com",
                "customer_phone": shipping_phone or f"900000{index:04d}",
                "status": "demo",
                "payment_method": "Prepaid" if index % 2 else "COD",
                "total": Decimal("199.00") + Decimal(index),
                "order_date": now - timedelta(hours=index * 2),
                "local_status": local_status,
                "shipped_at": shipped_at,
                "out_for_delivery_at": out_for_delivery_at,
                "delivered_at": delivered_at,
                "completed_at": completed_at,
                "shipping_address": {
                    "name": f"Demo Receiver {index}",
                    "phone": shipping_phone,
                    "address_1": f"{index} Demo Street",
                    "city": "Chennai",
                    "state": "TN",
                    "country": "India",
                    "pincode": shipping_pincode,
                },
                "billing_address": {
                    "name": f"Demo Billing {index}",
                    "phone": f"988000{index:04d}",
                    "address_1": f"{index} Billing Street",
                    "city": "Chennai",
                    "state": "TN",
                    "country": "India",
                    "pincode": f"{610000 + index}",
                },
                "order_items": [
                    {
                        "name": f"Sample Product {index}",
                        "sku": f"SKU-{index:04d}",
                        "quantity": 1,
                        "price": "199.00",
                    }
                ],
                "raw_payload": {"source": "seed_demo_orders"},
            }

            if local_status == ShiprocketOrder.STATUS_CANCELLED:
                defaults["cancellation_reason"] = ShiprocketOrder.CANCEL_REASON_CUSTOMER_REQUEST
                defaults["cancellation_note"] = "Demo cancelled order"
            else:
                defaults["cancellation_reason"] = ""
                defaults["cancellation_note"] = ""

            _, was_created = ShiprocketOrder.objects.update_or_create(
                shiprocket_order_id=order_id,
                defaults=defaults,
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo orders ready. Created: {created}, Updated: {updated}, Total requested: {count}."
            )
        )

    def _ensure_sender_address(self):
        sender = SenderAddress.get_default()
        updated_fields = []
        if not sender.name:
            sender.name = "Mathukai Organic"
            updated_fields.append("name")
        if not sender.address_1:
            sender.address_1 = "Demo Warehouse Street"
            updated_fields.append("address_1")
        if not sender.city:
            sender.city = "Chennai"
            updated_fields.append("city")
        if not sender.state:
            sender.state = "TN"
            updated_fields.append("state")
        if not sender.country:
            sender.country = "India"
            updated_fields.append("country")
        if not sender.pincode:
            sender.pincode = "600001"
            updated_fields.append("pincode")
        if updated_fields:
            sender.save(update_fields=updated_fields)
