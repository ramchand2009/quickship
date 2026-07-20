"""Bearer authentication for tenant-scoped mobile sessions."""

from django.db import transaction
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from core.models import MobileSession, TenantMembership

from .token_services import InvalidAccessToken, decode_access_token, revoke_session_family


class MobileAccessTokenAuthentication(BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        authorization = get_authorization_header(request).split()
        if not authorization:
            return None
        if len(authorization) != 2 or authorization[0].decode("ascii", errors="ignore").lower() != "bearer":
            raise AuthenticationFailed("Invalid authorization header.")

        try:
            encoded_token = authorization[1].decode("ascii")
            payload = decode_access_token(encoded_token)
            session = MobileSession.objects.select_related("user", "active_tenant").get(
                pk=payload["sid"]
            )
        except (
            UnicodeDecodeError,
            InvalidAccessToken,
            MobileSession.DoesNotExist,
            DjangoValidationError,
            ValueError,
            TypeError,
        ):
            raise AuthenticationFailed("The access token is invalid or expired.")

        now = timezone.now()
        tenant_claim = payload.get("tenant_id")
        if str(session.user_id) != payload.get("sub"):
            raise AuthenticationFailed("The access token is invalid or expired.")
        if tenant_claim != session.active_tenant_id:
            raise AuthenticationFailed("The access token is invalid or expired.")

        membership = None
        invalid_reason = None
        if not session.user.is_active:
            invalid_reason = "user_inactive"
        elif session.status != MobileSession.STATUS_ACTIVE:
            raise AuthenticationFailed("The mobile session is no longer active.")
        elif session.expires_at <= now:
            invalid_reason = "session_expired"
        elif session.active_tenant_id is not None:
            membership = (
                TenantMembership.objects.select_related("tenant")
                .filter(
                    user=session.user,
                    tenant_id=session.active_tenant_id,
                    is_active=True,
                    tenant__is_active=True,
                )
                .first()
            )
            if membership is None:
                invalid_reason = "membership_removed"

        if invalid_reason:
            with transaction.atomic():
                locked_session = MobileSession.objects.select_for_update().get(pk=session.pk)
                revoke_session_family(locked_session, now=now, reason=invalid_reason)
            raise AuthenticationFailed("The mobile session is no longer active.")

        request.mobile_session = session
        request.tenant = session.active_tenant
        request.tenant_membership = membership
        return session.user, session

    def authenticate_header(self, request):
        return self.keyword
