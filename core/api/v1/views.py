"""Views for the version 1 mobile API."""

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, NotFound, PermissionDenied, Throttled
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.access import active_tenant_memberships
from core.forms import LoginForm
from core.models import MobileDevice, MobileNotification, MobileSession, Tenant, TenantMembership

from .serializers import (
    LoginRequestSerializer,
    LogoutRequestSerializer,
    RefreshRequestSerializer,
    SelectTenantRequestSerializer,
)
from .dashboard_services import build_mobile_dashboard
from .permissions import HasActiveMobileTenant, HasMobileTenantRole
from .order_mutations import mark_payment_received, update_order_status
from .order_serializers import (
    OrderDetailSerializer,
    OrderListQuerySerializer,
    OrderStatusUpdateSerializer,
    OrderSummarySerializer,
    PaymentReceivedSerializer,
)
from .order_services import mobile_order_detail, mobile_order_queryset
from .pagination import MobileCursorPagination
from .notification_serializers import (
    MobileDeviceSerializer,
    MobileNotificationSerializer,
    NotificationListQuerySerializer,
    NotificationPreferencesUpdateSerializer,
    PushTokenRegistrationSerializer,
)
from .notification_services import effective_notification_preferences, update_notification_preferences
from .product_serializers import (
    ProductDetailSerializer,
    ProductListQuerySerializer,
    ProductSummarySerializer,
    StockMovementQuerySerializer,
    StockMovementSerializer,
)
from .product_services import (
    mobile_product_detail,
    mobile_product_queryset,
    mobile_product_routing_rules,
    mobile_stock_movement_queryset,
)
from .session_services import serialize_mobile_session, start_mobile_session
from .token_services import (
    InvalidRefreshToken,
    InvalidTenantSelection,
    issue_token_pair,
    revoke_session_with_refresh,
    rotate_refresh_token,
)


class MobileAuthEnabledMixin:
    def initial(self, request, *args, **kwargs):
        if not settings.MOBILE_API_ENABLED or not settings.MOBILE_AUTH_ENABLED:
            raise NotFound("The requested resource is unavailable.")
        return super().initial(request, *args, **kwargs)


class MobileReadEnabledMixin:
    def initial(self, request, *args, **kwargs):
        if not settings.MOBILE_API_ENABLED or not settings.MOBILE_READ_API_ENABLED:
            raise NotFound("The requested resource is unavailable.")
        return super().initial(request, *args, **kwargs)


class MobileWriteEnabledMixin:
    def initial(self, request, *args, **kwargs):
        if not settings.MOBILE_API_ENABLED or not settings.MOBILE_WRITE_API_ENABLED:
            raise NotFound("The requested resource is unavailable.")
        return super().initial(request, *args, **kwargs)

    def idempotency_key(self, request):
        key = str(request.headers.get("Idempotency-Key") or "").strip()
        if not key:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"idempotency_key": ["Send an Idempotency-Key header."]})
        if len(key) > 128:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"idempotency_key": ["Must be 128 characters or fewer."]})
        return key


class MobileLoginView(MobileAuthEnabledMixin, APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "mobile_login"

    def post(self, request):
        serializer = LoginRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data

        form = LoginForm(
            request=request._request,
            data={"username": values["username"], "password": values["password"]},
        )
        if not form.is_valid():
            errors = form.errors.as_data()
            is_locked = any(
                error.code == "too_many_attempts"
                for field_errors in errors.values()
                for error in field_errors
            )
            if is_locked:
                raise Throttled(wait=settings.LOGIN_LOCKOUT_DURATION_SECONDS)
            raise AuthenticationFailed("The username or password is incorrect.")

        user = form.get_user()
        memberships = active_tenant_memberships(user)
        if not memberships:
            raise AuthenticationFailed("The username or password is incorrect.")
        active_tenant = memberships[0].tenant if len(memberships) == 1 else None
        session = start_mobile_session(
            user=user,
            installation_id=values["installation_id"],
            app_version=values["app_version"],
            active_tenant=active_tenant,
        )
        tokens = issue_token_pair(session)
        return Response(
            {
                "data": {
                    "tokens": tokens,
                    "session": serialize_mobile_session(session, memberships),
                }
            }
        )


class MobileRefreshView(MobileAuthEnabledMixin, APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "mobile_refresh"

    def post(self, request):
        serializer = RefreshRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        try:
            tokens = rotate_refresh_token(
                raw_token=values["refresh_token"],
                installation_id=values["installation_id"],
            )
        except InvalidRefreshToken as error:
            raise AuthenticationFailed("The refresh token is invalid or expired.") from error
        return Response({"data": tokens})


class MobileLogoutView(MobileAuthEnabledMixin, APIView):
    throttle_scope = "mobile_write"

    def post(self, request):
        serializer = LogoutRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        try:
            revoke_session_with_refresh(
                session=request.auth,
                raw_token=values["refresh_token"],
                installation_id=values["installation_id"],
            )
        except InvalidRefreshToken as error:
            raise AuthenticationFailed("The refresh token is invalid or expired.") from error
        return Response(status=204)


class MobileCurrentSessionView(MobileAuthEnabledMixin, APIView):
    throttle_scope = "mobile_read"

    def get(self, request):
        memberships = active_tenant_memberships(request.user)
        return Response({"data": serialize_mobile_session(request.auth, memberships)})


class MobileSelectTenantView(MobileAuthEnabledMixin, APIView):
    throttle_scope = "mobile_write"

    def post(self, request):
        serializer = SelectTenantRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        tenant = Tenant.objects.filter(pk=values["tenant_id"], is_active=True).first()
        if tenant is None:
            raise PermissionDenied("The selected tenant is unavailable.")
        try:
            tokens = rotate_refresh_token(
                raw_token=values["refresh_token"],
                installation_id=request.auth.installation_id,
                active_tenant=tenant,
            )
        except InvalidTenantSelection as error:
            raise PermissionDenied("The selected tenant is unavailable.") from error
        except InvalidRefreshToken as error:
            raise AuthenticationFailed("The refresh token is invalid or expired.") from error

        session = MobileSession.objects.select_related("user", "active_tenant").get(pk=request.auth.pk)
        memberships = active_tenant_memberships(request.user)
        return Response(
            {
                "data": {
                    "tokens": tokens,
                    "session": serialize_mobile_session(session, memberships),
                }
            }
        )


class MobileDashboardView(MobileReadEnabledMixin, APIView):
    permission_classes = [HasActiveMobileTenant]
    throttle_scope = "mobile_read"

    def get(self, request):
        dashboard = build_mobile_dashboard(
            tenant=request.tenant,
            role=request.tenant_membership.role,
        )
        etag = dashboard.pop("etag")
        if request.headers.get("If-None-Match") == etag:
            response = Response(status=304)
        else:
            response = Response(dashboard)
        response["ETag"] = etag
        response["Cache-Control"] = f"private, max-age={settings.MOBILE_DASHBOARD_CACHE_SECONDS}"
        return response


class MobileOrderListView(MobileReadEnabledMixin, APIView):
    permission_classes = [HasActiveMobileTenant]
    throttle_scope = "mobile_read"

    def get(self, request):
        query = OrderListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        queryset = mobile_order_queryset(
            tenant=request.tenant,
            role=request.tenant_membership.role,
            filters=query.validated_data,
        )
        paginator = MobileCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = OrderSummarySerializer(
            page,
            many=True,
            context={"role": request.tenant_membership.role},
        ).data
        return paginator.get_paginated_response(data)


class MobileOrderDetailView(MobileReadEnabledMixin, APIView):
    permission_classes = [HasActiveMobileTenant]
    throttle_scope = "mobile_read"

    def get(self, request, order_id):
        order, activity = mobile_order_detail(tenant=request.tenant, order_id=order_id)
        if order is None:
            raise NotFound("The requested resource is unavailable.")
        data = OrderDetailSerializer(
            order,
            context={
                "role": request.tenant_membership.role,
                "activity": activity,
            },
        ).data
        return Response({"data": data})


class MobileOrderStatusView(MobileWriteEnabledMixin, APIView):
    permission_classes = [HasMobileTenantRole]
    mobile_allowed_roles = [
        TenantMembership.ROLE_VENDOR_OWNER,
        TenantMembership.ROLE_VENDOR_OPERATOR,
    ]
    throttle_scope = "mobile_write"

    def post(self, request, order_id):
        serializer = OrderStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = update_order_status(
            session=request.auth,
            tenant=request.tenant,
            role=request.tenant_membership.role,
            actor=request.user.get_username(),
            order_id=order_id,
            idempotency_key=self.idempotency_key(request),
            values=serializer.validated_data,
        )
        return Response(payload)


class MobileOrderPaymentReceivedView(MobileWriteEnabledMixin, APIView):
    permission_classes = [HasMobileTenantRole]
    mobile_allowed_roles = [
        TenantMembership.ROLE_VENDOR_OWNER,
        TenantMembership.ROLE_VENDOR_OPERATOR,
    ]
    throttle_scope = "mobile_write"

    def post(self, request, order_id):
        serializer = PaymentReceivedSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = mark_payment_received(
            session=request.auth,
            tenant=request.tenant,
            role=request.tenant_membership.role,
            actor=request.user.get_username(),
            order_id=order_id,
            idempotency_key=self.idempotency_key(request),
            values=serializer.validated_data,
        )
        return Response(payload)


class MobileNotificationListView(MobileReadEnabledMixin, APIView):
    permission_classes = [HasActiveMobileTenant]
    throttle_scope = "mobile_read"

    def get(self, request):
        query = NotificationListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        queryset = MobileNotification.objects.filter(
            tenant=request.tenant,
            user=request.user,
        )
        if query.validated_data["unread_only"]:
            queryset = queryset.filter(is_read=False)
        unread_count = MobileNotification.objects.filter(
            tenant=request.tenant,
            user=request.user,
            is_read=False,
        ).count()
        paginator = MobileCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        response = paginator.get_paginated_response(MobileNotificationSerializer(page, many=True).data)
        response.data["meta"] = {"unread_count": unread_count}
        return response


class MobileNotificationReadView(MobileWriteEnabledMixin, APIView):
    permission_classes = [HasActiveMobileTenant]
    throttle_scope = "mobile_write"

    def post(self, request, notification_id):
        self.idempotency_key(request)
        notification = MobileNotification.objects.filter(
            pk=notification_id,
            tenant=request.tenant,
            user=request.user,
        ).first()
        if notification is None:
            raise NotFound("The requested resource is unavailable.")
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=["is_read", "read_at"])
        return Response({"data": MobileNotificationSerializer(notification).data})


class MobileNotificationPreferencesView(APIView):
    permission_classes = [HasActiveMobileTenant]

    def initial(self, request, *args, **kwargs):
        if request.method == "PATCH":
            if not settings.MOBILE_API_ENABLED or not settings.MOBILE_WRITE_API_ENABLED:
                raise NotFound("The requested resource is unavailable.")
            self.throttle_scope = "mobile_write"
        else:
            if not settings.MOBILE_API_ENABLED or not settings.MOBILE_READ_API_ENABLED:
                raise NotFound("The requested resource is unavailable.")
            self.throttle_scope = "mobile_read"
        return super().initial(request, *args, **kwargs)

    def get(self, request):
        return Response(
            {"data": effective_notification_preferences(user=request.user, tenant=request.tenant)}
        )

    def patch(self, request):
        MobileWriteEnabledMixin.idempotency_key(self, request)
        serializer = NotificationPreferencesUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = update_notification_preferences(
            user=request.user,
            tenant=request.tenant,
            preferences=serializer.validated_data["preferences"],
        )
        return Response({"data": data})


class MobilePushTokenView(MobileWriteEnabledMixin, APIView):
    permission_classes = [HasActiveMobileTenant]
    throttle_scope = "mobile_write"

    def post(self, request):
        self.idempotency_key(request)
        serializer = PushTokenRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if values["installation_id"] != request.auth.installation_id:
            raise PermissionDenied("The installation is unavailable.")
        with transaction.atomic():
            MobileDevice.objects.filter(expo_push_token=values["expo_push_token"]).exclude(
                user=request.user,
                installation_id=values["installation_id"],
            ).delete()
            device, created = MobileDevice.objects.update_or_create(
                user=request.user,
                installation_id=values["installation_id"],
                defaults={
                    "tenant": request.tenant,
                    "platform": values["platform"],
                    "expo_push_token": values["expo_push_token"],
                    "app_version": values["app_version"],
                    "device_name": values.get("device_name") or "",
                    "enabled": True,
                    "last_seen_at": timezone.now(),
                },
            )
        return Response({"data": MobileDeviceSerializer(device).data}, status=201 if created else 200)


class MobileDeviceDetailView(MobileWriteEnabledMixin, APIView):
    permission_classes = [HasActiveMobileTenant]
    throttle_scope = "mobile_write"

    def delete(self, request, device_id):
        device = MobileDevice.objects.filter(pk=device_id, user=request.user).first()
        if device is None:
            raise NotFound("The requested resource is unavailable.")
        device.enabled = False
        device.save(update_fields=["enabled", "updated_at"])
        return Response(status=204)


class MobileProductListView(MobileReadEnabledMixin, APIView):
    permission_classes = [HasActiveMobileTenant]
    throttle_scope = "mobile_read"

    def get(self, request):
        query = ProductListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        queryset = mobile_product_queryset(
            tenant=request.tenant,
            filters=query.validated_data,
        )
        paginator = MobileCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = ProductSummarySerializer(
            page,
            many=True,
            context={"routing_rules": mobile_product_routing_rules(tenant=request.tenant)},
        ).data
        return paginator.get_paginated_response(data)


class MobileProductDetailView(MobileReadEnabledMixin, APIView):
    permission_classes = [HasActiveMobileTenant]
    throttle_scope = "mobile_read"

    def get(self, request, product_id):
        product = mobile_product_detail(tenant=request.tenant, product_id=product_id)
        if product is None:
            raise NotFound("The requested resource is unavailable.")
        data = ProductDetailSerializer(
            product,
            context={
                "role": request.tenant_membership.role,
                "routing_rules": mobile_product_routing_rules(tenant=request.tenant),
            },
        ).data
        return Response({"data": data})


class MobileStockMovementListView(MobileReadEnabledMixin, APIView):
    permission_classes = [HasActiveMobileTenant]
    throttle_scope = "mobile_read"

    def get(self, request):
        query = StockMovementQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        queryset = mobile_stock_movement_queryset(
            tenant=request.tenant,
            filters=query.validated_data,
        )
        paginator = MobileCursorPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = StockMovementSerializer(
            page,
            many=True,
            context={"role": request.tenant_membership.role},
        ).data
        return paginator.get_paginated_response(data)
