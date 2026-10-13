import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import PostOfficeWebhookEvent, SandboxRun


class SandboxTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("postadmin", "postadmin@example.com", "password")

    def test_admin_access_and_token_is_not_saved(self):
        url = reverse("post_office_sandbox")
        self.assertEqual(self.client.get(url).status_code, 302)
        ordinary = get_user_model().objects.create_user("ordinary", password="password")
        self.client.force_login(ordinary)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)
        with patch("post_office.views.client.access_token", return_value=(
            "secret-access-token", 200, {"success": True, "data": {"access_token": "secret-access-token", "expires_in": 900}}
        )):
            self.assertEqual(self.client.post(url, {"action": "token"}).status_code, 302)
        run = SandboxRun.objects.get()
        self.assertEqual(run.status, "passed")
        self.assertNotIn("secret-access-token", run.response_preview)

    def test_booking_partial_response_records_article_errors(self):
        self.client.force_login(self.admin)
        payload = {"articles": [{"bulk_customer_id": "3000064781", "contract_id": "41585456",
                                 "pickup_dropoff_office_id": 21260024, "barcode_no": "ET21433001XIN"}]}
        with patch.dict("os.environ", {"INDIA_POST_SANDBOX_CUSTOMER_ID": "3000064781"}), \
             patch("post_office.views.client.access_token", return_value=("token", 200, {})), \
             patch("post_office.views.client.booking", return_value=(200, {
                 "success": True, "summary": {"success_count": 0, "error_count": 1},
                 "error_articles": [{"barcode_no": "ET21433001XIN", "errors": ["Invalid PIN"]}],
             }, "application/json")):
            self.client.post(reverse("post_office_sandbox"), {
                "action": "booking", "booking_payload": json.dumps(payload), "confirm_booking": "yes",
            })
        run = SandboxRun.objects.get()
        self.assertEqual(run.status, "failed")
        self.assertIn("Invalid PIN", run.response_preview)

    def test_webhook_requires_token_and_deduplicates(self):
        url = reverse("post_office_webhook")
        body = json.dumps({"article_number": "ET21433001XIN", "event_code": "BAG_CLOSE",
                           "event_date": "2026-10-13", "event_time": "08:37:52"})
        with patch.dict("os.environ", {"INDIA_POST_WEBHOOK_TOKEN": "test-secret"}):
            self.assertEqual(self.client.post(url, body, content_type="application/json").status_code, 401)
            headers = {"HTTP_X_INDIA_POST_WEBHOOK_TOKEN": "test-secret"}
            self.assertEqual(self.client.post(url, body, content_type="application/json", **headers).status_code, 202)
            self.assertEqual(self.client.post(url, body, content_type="application/json", **headers).status_code, 200)
        self.assertEqual(PostOfficeWebhookEvent.objects.count(), 1)

    def test_evidence_has_pending_checklist(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("post_office_evidence"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checklist"]["tracking"], "not_tested")
        report = self.client.get(reverse("post_office_report"))
        self.assertContains(report, "Not tested")
