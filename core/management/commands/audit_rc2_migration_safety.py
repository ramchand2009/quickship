from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Max, Min

from core.models import ShiprocketOrder, WhatsAppNotificationQueue


ACTIVE_QUEUE_STATUSES = [
    WhatsAppNotificationQueue.STATUS_PENDING,
    WhatsAppNotificationQueue.STATUS_RETRYING,
    WhatsAppNotificationQueue.STATUS_PROCESSING,
]


def find_duplicate_woocommerce_orders():
    return list(
        ShiprocketOrder.objects.filter(
            source=ShiprocketOrder.SOURCE_WOOCOMMERCE,
        )
        .exclude(woocommerce_order_id="")
        .values("tenant_id", "tenant__name", "woocommerce_order_id")
        .annotate(count=Count("id"), first_pk=Min("id"), last_pk=Max("id"))
        .filter(count__gt=1)
        .order_by("tenant_id", "woocommerce_order_id")
    )


def find_duplicate_active_whatsapp_queue_jobs():
    return list(
        WhatsAppNotificationQueue.objects.filter(
            status__in=ACTIVE_QUEUE_STATUSES,
        )
        .exclude(idempotency_key="")
        .values("tenant_id", "tenant__name", "idempotency_key")
        .annotate(count=Count("id"), first_pk=Min("id"), last_pk=Max("id"))
        .filter(count__gt=1)
        .order_by("tenant_id", "idempotency_key")
    )


class Command(BaseCommand):
    help = "Audit existing data before applying RC2 uniqueness/idempotency constraints."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit non-zero when duplicate rows are found.",
        )

    def handle(self, *args, **options):
        strict = bool(options.get("strict"))
        duplicate_orders = find_duplicate_woocommerce_orders()
        duplicate_queue_jobs = find_duplicate_active_whatsapp_queue_jobs()

        if duplicate_orders:
            self.stdout.write(
                self.style.ERROR(
                    f"FAIL duplicate_woocommerce_orders={len(duplicate_orders)}"
                )
            )
            for row in duplicate_orders:
                self.stdout.write(
                    self.style.ERROR(
                        "  tenant_id={tenant_id} tenant={tenant} woocommerce_order_id={woo_id} "
                        "count={count} pk_range={first_pk}-{last_pk}".format(
                            tenant_id=row["tenant_id"],
                            tenant=row.get("tenant__name") or "",
                            woo_id=row["woocommerce_order_id"],
                            count=row["count"],
                            first_pk=row["first_pk"],
                            last_pk=row["last_pk"],
                        )
                    )
                )
        else:
            self.stdout.write(self.style.SUCCESS("OK duplicate_woocommerce_orders=0"))

        if duplicate_queue_jobs:
            self.stdout.write(
                self.style.ERROR(
                    f"FAIL duplicate_active_whatsapp_queue_jobs={len(duplicate_queue_jobs)}"
                )
            )
            for row in duplicate_queue_jobs:
                self.stdout.write(
                    self.style.ERROR(
                        "  tenant_id={tenant_id} tenant={tenant} idempotency_key={key} "
                        "count={count} pk_range={first_pk}-{last_pk}".format(
                            tenant_id=row["tenant_id"],
                            tenant=row.get("tenant__name") or "",
                            key=row["idempotency_key"],
                            count=row["count"],
                            first_pk=row["first_pk"],
                            last_pk=row["last_pk"],
                        )
                    )
                )
        else:
            self.stdout.write(self.style.SUCCESS("OK duplicate_active_whatsapp_queue_jobs=0"))

        issue_count = len(duplicate_orders) + len(duplicate_queue_jobs)
        if issue_count:
            self.stdout.write(
                self.style.WARNING(
                    "Audit found duplicate data. Review and clean these rows before applying RC2 migrations."
                )
            )
            if strict:
                raise CommandError(f"RC2 migration safety audit failed with {issue_count} issue group(s).")
            return

        self.stdout.write(self.style.SUCCESS("RC2 migration safety audit passed."))
