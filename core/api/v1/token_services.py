"""Cryptographic helpers and persistence boundaries for mobile tokens."""

import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

import jwt
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import MobileRefreshToken, MobileSession, TenantMembership


ACCESS_TOKEN_ALGORITHM = "HS256"
ACCESS_TOKEN_REQUIRED_CLAIMS = [
    "iss",
    "aud",
    "iat",
    "nbf",
    "exp",
    "jti",
    "sub",
    "sid",
    "sg",
    "token_type",
]


class InvalidAccessToken(ValueError):
    pass


class InvalidRefreshToken(ValueError):
    pass


class RefreshTokenReuseDetected(InvalidRefreshToken):
    pass


class InvalidTenantSelection(ValueError):
    pass


_KEEP_TENANT = object()


def issue_access_token(session, *, now=None):
    issued_at = now or timezone.now()
    expires_at = min(
        issued_at + timedelta(seconds=settings.MOBILE_ACCESS_TOKEN_LIFETIME_SECONDS),
        session.expires_at,
    )
    if expires_at <= issued_at:
        raise InvalidAccessToken("The mobile session has expired.")

    payload = {
        "iss": settings.MOBILE_ACCESS_TOKEN_ISSUER,
        "aud": settings.MOBILE_ACCESS_TOKEN_AUDIENCE,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": expires_at,
        "jti": str(uuid.uuid4()),
        "sub": str(session.user_id),
        "sid": str(session.pk),
        "sg": session.auth_generation,
        "tenant_id": session.active_tenant_id,
        "token_type": "access",
    }
    encoded = jwt.encode(payload, settings.MOBILE_JWT_SIGNING_KEY, algorithm=ACCESS_TOKEN_ALGORITHM)
    return encoded, expires_at


def decode_access_token(encoded_token):
    try:
        payload = jwt.decode(
            encoded_token,
            settings.MOBILE_JWT_SIGNING_KEY,
            algorithms=[ACCESS_TOKEN_ALGORITHM],
            audience=settings.MOBILE_ACCESS_TOKEN_AUDIENCE,
            issuer=settings.MOBILE_ACCESS_TOKEN_ISSUER,
            leeway=settings.MOBILE_ACCESS_TOKEN_CLOCK_SKEW_SECONDS,
            options={"require": ACCESS_TOKEN_REQUIRED_CLAIMS},
        )
    except jwt.PyJWTError as error:
        raise InvalidAccessToken("The access token is invalid or expired.") from error

    if "tenant_id" not in payload:
        raise InvalidAccessToken("The access token tenant context is missing.")
    if payload.get("token_type") != "access":
        raise InvalidAccessToken("The access token type is invalid.")
    return payload


def hash_refresh_token(raw_token):
    token_bytes = str(raw_token or "").encode("utf-8")
    key = hmac.new(
        settings.MOBILE_REFRESH_TOKEN_HASH_KEY.encode("utf-8"),
        b"mathukai.mobile-refresh-token.v1",
        hashlib.sha256,
    ).digest()
    return hmac.new(key, token_bytes, hashlib.sha256).hexdigest()


def persist_refresh_token(*, session, raw_token, parent=None, expires_at=None):
    values = {
        "session": session,
        "parent": parent,
        "token_hash": hash_refresh_token(raw_token),
    }
    if expires_at is not None:
        values["expires_at"] = expires_at
    return MobileRefreshToken.objects.create(**values)


def issue_refresh_token(session, *, parent=None, now=None):
    issued_at = now or timezone.now()
    expires_at = min(
        issued_at + timedelta(days=settings.MOBILE_REFRESH_TOKEN_LIFETIME_DAYS),
        session.expires_at,
    )
    if expires_at <= issued_at:
        raise InvalidRefreshToken("The mobile session has expired.")
    raw_token = secrets.token_urlsafe(48)
    record = persist_refresh_token(
        session=session,
        raw_token=raw_token,
        parent=parent,
        expires_at=expires_at,
    )
    return raw_token, record


def issue_token_pair(session, *, now=None):
    access_token, access_expires_at = issue_access_token(session, now=now)
    refresh_token, refresh_record = issue_refresh_token(session, now=now)
    return {
        "access_token": access_token,
        "access_expires_at": access_expires_at,
        "refresh_token": refresh_token,
        "refresh_expires_at": refresh_record.expires_at,
    }


def revoke_session_family(session, *, now, reason):
    session.status = MobileSession.STATUS_REVOKED
    session.revoked_at = now
    session.revocation_reason = reason
    session.save(update_fields=["status", "revoked_at", "revocation_reason"])
    MobileRefreshToken.objects.filter(session=session, revoked_at__isnull=True).update(revoked_at=now)


def _session_is_eligible(session, now):
    if not session.user.is_active or session.status != MobileSession.STATUS_ACTIVE:
        return False
    if session.expires_at <= now:
        return False
    if session.active_tenant_id is None:
        return True
    return TenantMembership.objects.filter(
        user=session.user,
        tenant_id=session.active_tenant_id,
        is_active=True,
        tenant__is_active=True,
    ).exists()


def rotate_refresh_token(*, raw_token, installation_id, now=None, active_tenant=_KEEP_TENANT):
    rotated_at = now or timezone.now()
    terminal_error = None
    result = None

    with transaction.atomic():
        try:
            token = (
                MobileRefreshToken.objects.select_for_update()
                .select_related("session__user")
                .get(token_hash=hash_refresh_token(raw_token))
            )
        except MobileRefreshToken.DoesNotExist as error:
            raise InvalidRefreshToken("The refresh token is invalid or expired.") from error

        session = (
            MobileSession.objects.select_for_update()
            .select_related("user")
            .get(pk=token.session_id)
        )
        if str(session.installation_id) != str(installation_id):
            raise InvalidRefreshToken("The refresh token is invalid or expired.")

        if token.consumed_at is not None:
            revoke_session_family(session, now=rotated_at, reason="refresh_token_reuse")
            terminal_error = RefreshTokenReuseDetected(
                "Refresh token reuse revoked the mobile session."
            )
        elif token.revoked_at is not None or token.expires_at <= rotated_at:
            raise InvalidRefreshToken("The refresh token is invalid or expired.")
        elif not _session_is_eligible(session, rotated_at):
            revoke_session_family(session, now=rotated_at, reason="session_ineligible")
            terminal_error = InvalidRefreshToken("The refresh token is invalid or expired.")
        else:
            if active_tenant is not _KEEP_TENANT:
                membership_exists = TenantMembership.objects.filter(
                    user=session.user,
                    tenant=active_tenant,
                    is_active=True,
                    tenant__is_active=True,
                ).exists()
                if not membership_exists:
                    raise InvalidTenantSelection("The selected tenant is unavailable.")
                session.active_tenant = active_tenant
                session.save(update_fields=["active_tenant"])
            token.consumed_at = rotated_at
            token.save(update_fields=["consumed_at"])
            child_raw_token, child = issue_refresh_token(
                session,
                parent=token,
                now=rotated_at,
            )
            access_token, access_expires_at = issue_access_token(session, now=rotated_at)
            result = {
                "access_token": access_token,
                "access_expires_at": access_expires_at,
                "refresh_token": child_raw_token,
                "refresh_expires_at": child.expires_at,
            }

    if terminal_error is not None:
        raise terminal_error
    return result


def revoke_session_with_refresh(*, session, raw_token, installation_id, now=None):
    revoked_at = now or timezone.now()
    with transaction.atomic():
        locked_session = MobileSession.objects.select_for_update().get(pk=session.pk)
        if str(locked_session.installation_id) != str(installation_id):
            raise InvalidRefreshToken("The refresh token is invalid or expired.")
        try:
            MobileRefreshToken.objects.select_for_update().get(
                session=locked_session,
                token_hash=hash_refresh_token(raw_token),
            )
        except MobileRefreshToken.DoesNotExist as error:
            raise InvalidRefreshToken("The refresh token is invalid or expired.") from error
        if locked_session.status != MobileSession.STATUS_REVOKED:
            revoke_session_family(locked_session, now=revoked_at, reason="logout")
