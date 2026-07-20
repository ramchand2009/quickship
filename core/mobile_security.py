"""Production security validation for mobile authentication secrets."""


def mobile_secret_issues(settings_obj):
    signing_key = str(getattr(settings_obj, "MOBILE_JWT_SIGNING_KEY", "") or "")
    hash_key = str(getattr(settings_obj, "MOBILE_REFRESH_TOKEN_HASH_KEY", "") or "")
    django_key = str(getattr(settings_obj, "SECRET_KEY", "") or "")
    issues = []
    if not getattr(settings_obj, "MOBILE_JWT_SIGNING_KEY_EXPLICIT", False):
        issues.append("MOBILE_JWT_SIGNING_KEY must be explicitly configured")
    if signing_key == django_key:
        issues.append("MOBILE_JWT_SIGNING_KEY must differ from DJANGO_SECRET_KEY")
    if len(signing_key) < 32 or "change-me" in signing_key.lower():
        issues.append("MOBILE_JWT_SIGNING_KEY must be a non-placeholder secret of at least 32 characters")
    if len(hash_key) < 32 or "change-me" in hash_key.lower():
        issues.append("MOBILE_REFRESH_TOKEN_HASH_KEY must be a non-placeholder secret of at least 32 characters")
    return issues
