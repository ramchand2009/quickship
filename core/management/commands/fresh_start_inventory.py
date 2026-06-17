from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import (
    OrderActivityLog,
    Product,
    ProductCategory,
    ShiprocketOrder,
    StockMovement,
    WhatsAppNotificationLog,
    WhatsAppNotificationQueue,
)
from core.woocommerce import WooCommerceAPIError, sync_products as sync_woocommerce_products


class Command(BaseCommand):
    help = "Delete all order and product inventory data so Quickship can start fresh."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Actually delete data. Without this flag the command only reports counts.",
        )
        parser.add_argument(
            "--sync-woocommerce-products",
            action="store_true",
            help="After deleting local products/orders, pull products from WooCommerce.",
        )

    def handle(self, *args, **options):
        confirmed = bool(options.get("confirm"))
        should_sync = bool(options.get("sync_woocommerce_products"))
        counts = {
            "orders": ShiprocketOrder.objects.count(),
            "products": Product.objects.count(),
            "categories": ProductCategory.objects.count(),
            "stock_movements": StockMovement.objects.count(),
            "activity_logs": OrderActivityLog.objects.count(),
            "whatsapp_logs": WhatsAppNotificationLog.objects.count(),
            "queue_jobs": WhatsAppNotificationQueue.objects.count(),
        }

        if not confirmed:
            self.stdout.write(
                self.style.WARNING(
                    (
                        "Fresh start dry run | add --confirm to delete | "
                        f"orders={counts['orders']} products={counts['products']} "
                        f"categories={counts['categories']} stock_movements={counts['stock_movements']} "
                        f"activity_logs={counts['activity_logs']} whatsapp_logs={counts['whatsapp_logs']} "
                        f"queue_jobs={counts['queue_jobs']}"
                    )
                )
            )
            return

        with transaction.atomic():
            WhatsAppNotificationQueue.objects.all().delete()
            WhatsAppNotificationLog.objects.all().delete()
            OrderActivityLog.objects.all().delete()
            StockMovement.objects.all().delete()
            ShiprocketOrder.objects.all().delete()
            Product.objects.all().delete()
            ProductCategory.objects.all().delete()

        self.stdout.write(
            self.style.SUCCESS(
                (
                    "Fresh start complete | deleted "
                    f"orders={counts['orders']} products={counts['products']} "
                    f"categories={counts['categories']} stock_movements={counts['stock_movements']} "
                    f"activity_logs={counts['activity_logs']} whatsapp_logs={counts['whatsapp_logs']} "
                    f"queue_jobs={counts['queue_jobs']}"
                )
            )
        )

        if should_sync:
            try:
                summary = sync_woocommerce_products()
            except WooCommerceAPIError as exc:
                raise CommandError(f"Fresh start completed, but WooCommerce product sync failed: {exc}") from exc
            self.stdout.write(
                self.style.SUCCESS(
                    (
                        "WooCommerce products synced | "
                        f"created={summary['created']} updated={summary['updated']} "
                        f"unchanged={summary['unchanged']} skipped={summary['skipped']} "
                        f"variations={summary['variations_seen']}"
                    )
                )
            )
