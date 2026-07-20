"""Views for the version 1 mobile API."""

from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed, Throttled
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.access import active_tenant_memberships
from core.forms import LoginForm

from .serializers import LoginRequestSerializer
from .session_services import serialize_mobile_session, start_mobile_session
from .token_services import issue_token_pair


class MobileLoginView(APIView):
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
