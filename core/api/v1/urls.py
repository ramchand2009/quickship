"""URL boundary for the version 1 mobile API."""

from django.urls import path

from .views import (
    MobileCurrentSessionView,
    MobileDashboardView,
    MobileLoginView,
    MobileLogoutView,
    MobileRefreshView,
    MobileSelectTenantView,
)

app_name = "mobile_api_v1"

urlpatterns = [
    path("auth/login", MobileLoginView.as_view(), name="auth_login"),
    path("auth/refresh", MobileRefreshView.as_view(), name="auth_refresh"),
    path("auth/logout", MobileLogoutView.as_view(), name="auth_logout"),
    path("auth/me", MobileCurrentSessionView.as_view(), name="auth_me"),
    path("auth/select-tenant", MobileSelectTenantView.as_view(), name="auth_select_tenant"),
    path("dashboard", MobileDashboardView.as_view(), name="dashboard"),
]
