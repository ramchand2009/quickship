"""Cryptographic helpers and persistence boundaries for mobile tokens."""

import hashlib
import hmac
import uuid
from datetime import timedelta

import jwt
from django.conf import settings
from django.utils import timezone

from core.models import MobileRefreshToken


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
    "tenant_id",
    "token_type",
]


class InvalidAccessToken(ValueError):
    pass


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

    if payload.get("token_type") != "access":
        raise InvalidAccessToken("The access token type is invalid.")
    return payload


def hash_refresh_token(raw_token):
    token_bytes = str(raw_token or "").encode("utf-8")
    key = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
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
