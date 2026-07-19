"""Named rate-limit policies for mobile API endpoint groups."""

from django.core.exceptions import ImproperlyConfigured
from rest_framework.settings import api_settings
from rest_framework.throttling import ScopedRateThrottle


class MobileScopedRateThrottle(ScopedRateThrottle):
    def get_rate(self):
        rates = api_settings.DEFAULT_THROTTLE_RATES
        try:
            return rates[self.scope]
        except KeyError as error:
            raise ImproperlyConfigured(
                f"No default throttle rate set for mobile scope '{self.scope}'"
            ) from error
