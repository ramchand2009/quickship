"""Views for the version 1 mobile API."""

from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed, NotFound, PermissionDenied, Throttled
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.access import active_tenant_memberships
from core.forms import LoginForm
from core.models import MobileSession, Tenant

from .serializers import (
    LoginRequestSerializer,
    LogoutRequestSerializer,
    RefreshRequestSerializer,
    SelectTenantRequestSerializer,
)
from .dashboard_services import build_mobile_dashboard
from .permissions import HasActiveMobileTenant
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
