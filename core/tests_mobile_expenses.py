import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.api.v1.session_services import create_mobile_session
from core.api.v1.token_services import issue_access_token
from core.models import BusinessExpense, Tenant, TenantMembership


@override_settings(MOBILE_API_ENABLED=True, MOBILE_READ_API_ENABLED=True, MOBILE_WRITE_API_ENABLED=True)
class MobileExpenseApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="expense-owner")
        self.tenant = Tenant.objects.create(name="Expense Tenant", slug="expense-tenant")
        TenantMembership.objects.create(user=self.user, tenant=self.tenant, role=TenantMembership.ROLE_VENDOR_OWNER)
        session = create_mobile_session(user=self.user, installation_id=uuid.uuid4(), app_version="1.0.0", active_tenant=self.tenant)
        token, _ = issue_access_token(session)
        self.headers = {"Authorization": f"Bearer {token}"}

    def test_create_and_monthly_list(self):
        key = str(uuid.uuid4())
        payload = {"item_name": "Packing tape", "quantity": 2, "unit_price": "75.50", "remark": "Office purchase"}
        created = self.client.post("/api/v1/expenses", payload, content_type="application/json", headers={**self.headers, "Idempotency-Key": key})
        replay = self.client.post("/api/v1/expenses", payload, content_type="application/json", headers={**self.headers, "Idempotency-Key": key})
        expense = BusinessExpense.objects.get()
        month = expense.created_at.strftime("%Y-%m")
        listed = self.client.get("/api/v1/expenses", {"month": month}, headers=self.headers)

        self.assertEqual(created.status_code, 201)
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(BusinessExpense.objects.count(), 1)
        self.assertEqual(expense.total_amount, Decimal("151.00"))
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["data"]["total"], "151.00")
        self.assertEqual(listed.json()["data"]["expenses"][0]["item_name"], "Packing tape")
