"""Bounded retention cleanup for mobile authentication records."""

from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from core.models import MobileRefreshToken, MobileSession


def _limited_ids(queryset, batch_size):
    return list(queryset.order_by("pk").values_list("pk", flat=True)[:batch_size])


def cleanup_mobile_auth(*, batch_size=None, retention_days=None, dry_run=False, now=None):
    """Expire stale records and delete only terminal history past retention."""
    cleaned_at = now or timezone.now()
    limit = max(1, int(batch_size or settings.MOBILE_AUTH_CLEANUP_BATCH_SIZE))
    retention = max(0, int(
        settings.MOBILE_AUTH_RETENTION_DAYS if retention_days is None else retention_days
    ))
    cutoff = cleaned_at - timedelta(days=retention)

    expired_session_ids = _limited_ids(
        MobileSession.objects.filter(
            status=MobileSession.STATUS_ACTIVE,
            expires_at__lte=cleaned_at,
        ),
        limit,
    )
    expired_token_ids = _limited_ids(
        MobileRefreshToken.objects.filter(
            expires_at__lte=cleaned_at,
            revoked_at__isnull=True,
        ),
        limit,
    )
    token_history_ids = _limited_ids(
        MobileRefreshToken.objects.filter(expires_at__lt=cutoff).filter(
            Q(revoked_at__isnull=False) | Q(consumed_at__isnull=False)
        ),
        limit,
    )
    session_history_ids = _limited_ids(
        MobileSession.objects.filter(
            status__in=[MobileSession.STATUS_EXPIRED, MobileSession.STATUS_REVOKED],
            expires_at__lt=cutoff,
        ),
        limit,
    )

    summary = {
        "dry_run": bool(dry_run),
        "sessions_expired": len(expired_session_ids),
        "refresh_tokens_revoked": len(expired_token_ids),
        "refresh_tokens_deleted": len(token_history_ids),
        "sessions_deleted": len(session_history_ids),
    }
    if dry_run:
        return summary

    summary.update(
        sessions_expired=0,
        refresh_tokens_revoked=0,
        refresh_tokens_deleted=0,
        sessions_deleted=0,
    )
    if expired_session_ids:
        summary["sessions_expired"] = MobileSession.objects.filter(
            pk__in=expired_session_ids,
            status=MobileSession.STATUS_ACTIVE,
            expires_at__lte=cleaned_at,
        ).update(
            status=MobileSession.STATUS_EXPIRED,
            revoked_at=cleaned_at,
            revocation_reason="session_expired",
        )
    if expired_token_ids:
        summary["refresh_tokens_revoked"] = MobileRefreshToken.objects.filter(
            pk__in=expired_token_ids,
            expires_at__lte=cleaned_at,
            revoked_at__isnull=True,
        ).update(revoked_at=cleaned_at)
    if token_history_ids:
        _, deleted = MobileRefreshToken.objects.filter(
            pk__in=token_history_ids,
            expires_at__lt=cutoff,
        ).filter(
            Q(revoked_at__isnull=False) | Q(consumed_at__isnull=False)
        ).delete()
        summary["refresh_tokens_deleted"] = deleted.get("core.MobileRefreshToken", 0)
    if session_history_ids:
        _, deleted = MobileSession.objects.filter(
            pk__in=session_history_ids,
            status__in=[MobileSession.STATUS_EXPIRED, MobileSession.STATUS_REVOKED],
            expires_at__lt=cutoff,
        ).delete()
        summary["sessions_deleted"] = deleted.get("core.MobileSession", 0)
    return summary
