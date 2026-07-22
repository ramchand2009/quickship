"""URL boundary for the version 1 mobile API."""

from django.urls import path

from .views import (
    MobileCurrentSessionView,
    MobileDashboardView,
    MobileDeviceDetailView,
    MobileLoginView,
    MobileLogoutView,
    MobileOrderListView,
    MobileOrderDetailView,
    MobileOrderPaymentReceivedView,
    MobileOrderStatusView,
    MobileNotificationListView,
    MobileNotificationPreferencesView,
    MobileNotificationReadView,
    MobilePushTokenView,
    MobileProductListView,
    MobileProductDetailView,
    MobileStockMovementListView,
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
    path("orders", MobileOrderListView.as_view(), name="orders"),
    path("orders/<int:order_id>", MobileOrderDetailView.as_view(), name="order_detail"),
    path("orders/<int:order_id>/status", MobileOrderStatusView.as_view(), name="order_status"),
    path(
        "orders/<int:order_id>/payment-received",
        MobileOrderPaymentReceivedView.as_view(),
        name="order_payment_received",
    ),
    path("products", MobileProductListView.as_view(), name="products"),
    path("products/<int:product_id>", MobileProductDetailView.as_view(), name="product_detail"),
    path("stock/movements", MobileStockMovementListView.as_view(), name="stock_movements"),
    path("notifications", MobileNotificationListView.as_view(), name="notifications"),
    path(
        "notifications/<int:notification_id>/read",
        MobileNotificationReadView.as_view(),
        name="notification_read",
    ),
    path(
        "notification-preferences",
        MobileNotificationPreferencesView.as_view(),
        name="notification_preferences",
    ),
    path("devices/push-token", MobilePushTokenView.as_view(), name="device_push_token"),
    path("devices/<uuid:device_id>", MobileDeviceDetailView.as_view(), name="device_detail"),
]
