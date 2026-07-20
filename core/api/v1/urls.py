"""URL boundary for the version 1 mobile API."""

from django.urls import path

from .views import MobileLoginView, MobileLogoutView, MobileRefreshView

app_name = "mobile_api_v1"

urlpatterns = [
    path("auth/login", MobileLoginView.as_view(), name="auth_login"),
    path("auth/refresh", MobileRefreshView.as_view(), name="auth_refresh"),
    path("auth/logout", MobileLogoutView.as_view(), name="auth_logout"),
]
