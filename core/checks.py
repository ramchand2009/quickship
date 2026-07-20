from django.conf import settings
from django.core.checks import Error, Tags, register

from .mobile_security import mobile_secret_issues


@register(Tags.security, deploy=True)
def check_mobile_auth_secrets(app_configs, **kwargs):
    if settings.DEBUG:
        return []
    return [
        Error(issue, id=f"core.E{index:03d}")
        for index, issue in enumerate(mobile_secret_issues(settings), start=1)
    ]
