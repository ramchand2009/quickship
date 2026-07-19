from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from django.urls import get_resolver
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView


class DefaultProtectedView(APIView):
    def get(self, request):
        return Response({"ok": True})


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
