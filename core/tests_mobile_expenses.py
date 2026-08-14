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

    def test_update_and_delete_are_tenant_scoped_and_idempotent(self):
        expense = BusinessExpense.objects.create(
            tenant=self.tenant, item_name="Old item", quantity=1, unit_price="10.00"
        )
        update_key = str(uuid.uuid4())
        payload = {"item_name": "Updated item", "quantity": 3, "unit_price": "20.00", "remark": "Corrected"}
        updated = self.client.patch(
            f"/api/v1/expenses/{expense.pk}", payload, content_type="application/json",
            headers={**self.headers, "Idempotency-Key": update_key},
        )
        replay = self.client.patch(
            f"/api/v1/expenses/{expense.pk}", payload, content_type="application/json",
            headers={**self.headers, "Idempotency-Key": update_key},
        )
        expense.refresh_from_db()
        self.assertEqual(updated.status_code, 200)
        self.assertTrue(replay.json()["data"]["replayed"])
        self.assertEqual(expense.item_name, "Updated item")
        self.assertEqual(expense.total_amount, Decimal("60.00"))

        delete_key = str(uuid.uuid4())
        deleted = self.client.delete(
            f"/api/v1/expenses/{expense.pk}", headers={**self.headers, "Idempotency-Key": delete_key}
        )
        delete_replay = self.client.delete(
            f"/api/v1/expenses/{expense.pk}", headers={**self.headers, "Idempotency-Key": delete_key}
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(delete_replay.json()["data"]["replayed"])
        self.assertFalse(BusinessExpense.objects.filter(pk=expense.pk).exists())

        other_tenant = Tenant.objects.create(name="Other Expense", slug="other-expense")
        other = BusinessExpense.objects.create(tenant=other_tenant, item_name="Private", quantity=1, unit_price="5")
        forbidden = self.client.delete(
            f"/api/v1/expenses/{other.pk}", headers={**self.headers, "Idempotency-Key": str(uuid.uuid4())}
        )
        self.assertEqual(forbidden.status_code, 404)
