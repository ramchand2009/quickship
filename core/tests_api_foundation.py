from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, override_settings
from django.urls import get_resolver, path
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, Throttled, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from core.api.request_ids import get_request_id
from core.api.v1.exceptions import BusinessRuleError, ConflictError


class DefaultProtectedView(APIView):
    def get(self, request):
        return Response({"ok": True})


class MetadataSuccessView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"data": {"ok": True}})


class ErrorProbeView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, error_type):
        if error_type == "validation":
            raise ValidationError({"name": ["This field is required."], "item": {"sku": ["Invalid."]}})
        if error_type == "permission":
            raise PermissionDenied()
        if error_type == "not-found":
            raise NotFound()
        if error_type == "conflict":
            raise ConflictError(code="order_version_conflict")
        if error_type == "business-rule":
            raise BusinessRuleError()
        if error_type == "throttled":
            raise Throttled(wait=5)
        raise ValueError("private implementation detail")


urlpatterns = [
    path("api/v1/test-success/", MetadataSuccessView.as_view()),
    path("api/v1/test-error/<str:error_type>/", ErrorProbeView.as_view()),
    path("api/v1/test-protected/", DefaultProtectedView.as_view()),
]


class RestFrameworkConfigurationTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = DefaultProtectedView.as_view()

    def test_anonymous_requests_are_denied_by_default(self):
        response = self.view(self.factory.get("/api/test"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_requests_are_allowed_by_default(self):
        user = get_user_model()(username="api-test-user")
        request = self.factory.get("/api/test")
        force_authenticate(request, user=user)

        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"ok": True})


class VersionedApiBoundaryTests(SimpleTestCase):
    def test_v1_namespace_is_registered(self):
        self.assertIn("mobile_api_v1", get_resolver().namespace_dict)

    def test_v1_has_no_business_endpoint_by_default(self):
        response = self.client.get("/api/v1/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(ROOT_URLCONF=__name__)
class ApiRequestIdTests(SimpleTestCase):
    def test_generated_request_id_is_returned_in_header_and_metadata(self):
        response = self.client.get("/api/v1/test-success/")

        request_id = response["X-Request-ID"]
        self.assertRegex(request_id, r"^req_[0-9a-f]{32}$")
        self.assertEqual(response.json()["meta"]["request_id"], request_id)
        self.assertIn("server_time", response.json()["meta"])
        self.assertEqual(get_request_id(), "-")

    def test_safe_inbound_request_id_is_preserved(self):
        response = self.client.get(
            "/api/v1/test-success/",
            headers={"X-Request-ID": "mobile.1234-safe"},
        )

        self.assertEqual(response["X-Request-ID"], "mobile.1234-safe")
        self.assertEqual(response.json()["meta"]["request_id"], "mobile.1234-safe")

    def test_invalid_or_oversized_request_id_is_replaced(self):
        for request_id in ("contains spaces", "x" * 65, "customer@example.com"):
            with self.subTest(request_id=request_id):
                response = self.client.get(
                    "/api/v1/test-success/",
                    headers={"X-Request-ID": request_id},
                )

                self.assertNotEqual(response["X-Request-ID"], request_id)
                self.assertRegex(response["X-Request-ID"], r"^req_[0-9a-f]{32}$")


@override_settings(ROOT_URLCONF=__name__)
class ApiErrorEnvelopeTests(SimpleTestCase):
    def assert_error(self, response, status_code, code, retryable=False):
        self.assertEqual(response.status_code, status_code)
        body = response.json()
        self.assertEqual(body["error"]["code"], code)
        self.assertEqual(body["error"]["retryable"], retryable)
        self.assertEqual(body["meta"]["request_id"], response["X-Request-ID"])
        self.assertIn("server_time", body["meta"])
        self.assertNotIn("traceback", str(body).lower())

    def test_authentication_error_is_generic(self):
        response = self.client.get("/api/v1/test-protected/")

        self.assert_error(response, 401, "authentication_required")

    def test_validation_error_has_flat_field_messages(self):
        response = self.client.get("/api/v1/test-error/validation/")

        self.assert_error(response, 400, "validation_error")
        self.assertEqual(
            response.json()["error"]["fields"],
            {"name": ["This field is required."], "item.sku": ["Invalid."]},
        )

    def test_permission_and_not_found_errors_are_generic(self):
        cases = (("permission", 403, "permission_denied"), ("not-found", 404, "not_found"))
        for path_name, status_code, code in cases:
            with self.subTest(path_name=path_name):
                response = self.client.get(f"/api/v1/test-error/{path_name}/")
                self.assert_error(response, status_code, code)

    def test_conflict_and_business_rule_errors_are_standardized(self):
        conflict = self.client.get("/api/v1/test-error/conflict/")
        business_rule = self.client.get("/api/v1/test-error/business-rule/")

        self.assert_error(conflict, 409, "order_version_conflict")
        self.assert_error(business_rule, 422, "business_rule_violation")

    def test_throttling_is_retryable_and_keeps_retry_after(self):
        response = self.client.get("/api/v1/test-error/throttled/")

        self.assert_error(response, 429, "rate_limited", retryable=True)
        self.assertEqual(response["Retry-After"], "5")

    def test_unexpected_error_hides_internal_detail(self):
        response = self.client.get("/api/v1/test-error/unexpected/")

        self.assert_error(response, 500, "server_error", retryable=True)
        self.assertNotContains(response, "private implementation detail", status_code=500)
