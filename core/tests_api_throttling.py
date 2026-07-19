from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import path
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class ThrottleProbeView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"data": {"ok": True}})


class LoginThrottleProbe(ThrottleProbeView):
    throttle_scope = "mobile_login"


class RefreshThrottleProbe(ThrottleProbeView):
    throttle_scope = "mobile_refresh"


class WriteThrottleProbe(ThrottleProbeView):
    throttle_scope = "mobile_write"


class DeviceThrottleProbe(ThrottleProbeView):
    throttle_scope = "mobile_device"


class ReadThrottleProbe(ThrottleProbeView):
    throttle_scope = "mobile_read"


urlpatterns = [
    path("api/v1/throttle/login/", LoginThrottleProbe.as_view()),
    path("api/v1/throttle/refresh/", RefreshThrottleProbe.as_view()),
    path("api/v1/throttle/write/", WriteThrottleProbe.as_view()),
    path("api/v1/throttle/device/", DeviceThrottleProbe.as_view()),
    path("api/v1/throttle/read/", ReadThrottleProbe.as_view()),
]


TEST_REST_FRAMEWORK = {
    **settings.REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {
        "mobile_login": "1/min",
        "mobile_refresh": "1/min",
        "mobile_write": "1/min",
        "mobile_device": "1/min",
        "mobile_read": "1/min",
    },
}


@override_settings(ROOT_URLCONF=__name__, REST_FRAMEWORK=TEST_REST_FRAMEWORK)
class MobileApiThrottleTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_each_mobile_scope_has_an_independent_policy(self):
        scopes = ("login", "refresh", "write", "device", "read")

        for scope in scopes:
            with self.subTest(scope=scope):
                first = self.client.get(f"/api/v1/throttle/{scope}/")
                limited = self.client.get(f"/api/v1/throttle/{scope}/")

                self.assertEqual(first.status_code, 200)
                self.assertEqual(limited.status_code, 429)
                self.assertEqual(limited.json()["error"]["code"], "rate_limited")
                self.assertTrue(limited.json()["error"]["retryable"])
                self.assertIn("Retry-After", limited)

    def test_production_scope_names_are_all_configured(self):
        self.assertEqual(
            set(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]),
            {
                "mobile_login",
                "mobile_refresh",
                "mobile_write",
                "mobile_device",
                "mobile_read",
            },
        )
