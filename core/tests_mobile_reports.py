from django.test import TestCase, override_settings
from django.utils import timezone

from core.api.v1.report_services import build_product_sales_report
from core.models import Product, ShiprocketOrder, Tenant


@override_settings(MOBILE_API_ENABLED=True, MOBILE_READ_API_ENABLED=True)
class MobileProductSalesReportTests(TestCase):
    def test_product_sales_report_groups_monthly_qty_sales_and_profit(self):
        tenant = Tenant.objects.create(name="Report Tenant", slug="report-tenant")
        other_tenant = Tenant.objects.create(name="Other Tenant", slug="other-report-tenant")
        soap = Product.objects.create(
            tenant=tenant,
            name="Herbal Soap",
            sku="SOAP-1",
            actual_price="40.00",
        )
        oil = Product.objects.create(
            tenant=tenant,
            name="Hair Oil",
            sku="OIL-1",
            actual_price="80.00",
        )
        selected_date = timezone.now().replace(day=10, hour=9, minute=0, second=0, microsecond=0)
        selected_month = selected_date.strftime("%Y-%m")
        ShiprocketOrder.objects.create(
            tenant=tenant,
            shiprocket_order_id="REPORT-1",
            local_status=ShiprocketOrder.STATUS_COMPLETED,
            order_date=selected_date,
            order_items=[
                {"sku": soap.sku, "name": soap.name, "quantity": 2, "price": "100.00"},
                {"sku": oil.sku, "name": oil.name, "quantity": 1, "price": "150.00"},
            ],
        )
        ShiprocketOrder.objects.create(
            tenant=tenant,
            shiprocket_order_id="REPORT-CANCELLED",
            local_status=ShiprocketOrder.STATUS_CANCELLED,
            order_date=selected_date,
            order_items=[{"sku": soap.sku, "name": soap.name, "quantity": 10, "price": "100.00"}],
        )
        ShiprocketOrder.objects.create(
            tenant=other_tenant,
            shiprocket_order_id="REPORT-OTHER",
            local_status=ShiprocketOrder.STATUS_COMPLETED,
            order_date=selected_date,
            order_items=[{"sku": soap.sku, "name": soap.name, "quantity": 10, "price": "100.00"}],
        )

        report = build_product_sales_report(tenant=tenant, month=selected_month)

        summary = report["data"]["summary"]
        rows = {row["sku"]: row for row in report["data"]["products"]}
        self.assertEqual(summary["product_count"], 2)
        self.assertEqual(summary["total_quantity"], 3)
        self.assertEqual(summary["total_sales"], {"amount": "350.00", "currency": "INR"})
        self.assertEqual(summary["total_profit"], {"amount": "190.00", "currency": "INR"})
        self.assertEqual(rows["SOAP-1"]["quantity"], 2)
        self.assertEqual(rows["SOAP-1"]["total_sales"], {"amount": "200.00", "currency": "INR"})
        self.assertEqual(rows["SOAP-1"]["total_profit"], {"amount": "120.00", "currency": "INR"})
        self.assertEqual(rows["OIL-1"]["quantity"], 1)
        self.assertEqual(rows["OIL-1"]["total_profit"], {"amount": "70.00", "currency": "INR"})
