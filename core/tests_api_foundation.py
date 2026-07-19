from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, override_settings
from django.urls import get_resolver, path
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from core.api.request_ids import get_request_id


class DefaultProtectedView(APIView):
    def get(self, request):
        return Response({"ok": True})


class MetadataSuccessView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"data": {"ok": True}})


urlpatterns = [
    path("api/v1/test-success/", MetadataSuccessView.as_view()),
]


class RestFrameworkConfigurationTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = DefaultProtectedView.as_view()

    def test_anonymous_requests_are_denied_by_default(self):
        response = self.view(self.factory.get("/api/test"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

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
