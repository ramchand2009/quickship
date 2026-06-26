from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from core.models import (
    OrderActivityLog,
    ShiprocketOrder,
    StockMovement,
    WhatsAppNotificationLog,
    WhatsAppNotificationQueue,
)


def _order_related_queryset(model):
    order_pks = ShiprocketOrder.objects.values("pk")
    order_refs = ShiprocketOrder.objects.exclude(shiprocket_order_id="").values("shiprocket_order_id")
    return model.objects.filter(Q(order_id__in=order_pks) | Q(shiprocket_order_id__in=order_refs))


class Command(BaseCommand):
    help = "Delete all orders and order-linked operational rows while keeping products and inventory setup."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Actually delete order data. Without this flag the command only reports counts.",
        )

    def handle(self, *args, **options):
        confirmed = bool(options.get("confirm"))
        related_querysets = {
            "queue_jobs": _order_related_queryset(WhatsAppNotificationQueue),
            "whatsapp_logs": _order_related_queryset(WhatsAppNotificationLog),
            "activity_logs": _order_related_queryset(OrderActivityLog),
            "stock_movements": _order_related_queryset(StockMovement),
        }
        counts = {
            "orders": ShiprocketOrder.objects.count(),
            **{name: queryset.count() for name, queryset in related_querysets.items()},
        }

        if not confirmed:
            self.stdout.write(
                self.style.WARNING(
                    (
                        "Fresh orders dry run | add --confirm to delete | "
                        f"orders={counts['orders']} "
                        f"stock_movements={counts['stock_movements']} "
                        f"activity_logs={counts['activity_logs']} "
                        f"whatsapp_logs={counts['whatsapp_logs']} "
                        f"queue_jobs={counts['queue_jobs']}"
                    )
                )
            )
            return

        with transaction.atomic():
            for queryset in related_querysets.values():
                queryset.delete()
            ShiprocketOrder.objects.all().delete()

        self.stdout.write(
            self.style.SUCCESS(
                (
                    "Fresh orders complete | deleted "
                    f"orders={counts['orders']} "
                    f"stock_movements={counts['stock_movements']} "
                    f"activity_logs={counts['activity_logs']} "
                    f"whatsapp_logs={counts['whatsapp_logs']} "
                    f"queue_jobs={counts['queue_jobs']}"
                )
            )
        )
