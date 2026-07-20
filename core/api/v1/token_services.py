"""Cryptographic helpers and persistence boundaries for mobile tokens."""

import hashlib
import hmac

from django.conf import settings

from core.models import MobileRefreshToken


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
