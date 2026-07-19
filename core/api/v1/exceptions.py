"""Client-safe exception handling for the version 1 mobile API."""

import logging

from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    Throttled,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


logger = logging.getLogger(__name__)


class MobileApiException(APIException):
    error_code = "api_error"
    retryable = False

    def __init__(self, detail=None, code=None, fields=None, retryable=None):
        super().__init__(detail=detail, code=code)
        self.error_code = code or self.error_code
        self.fields = fields or {}
        if retryable is not None:
            self.retryable = bool(retryable)


class ConflictError(MobileApiException):
    status_code = 409
    default_detail = "The resource changed. Refresh and try again."
    default_code = "conflict"
    error_code = "conflict"


class BusinessRuleError(MobileApiException):
    status_code = 422
    default_detail = "This action is not available in the current state."
    default_code = "business_rule_violation"
    error_code = "business_rule_violation"


def _message_list(detail):
    if isinstance(detail, dict):
        messages = []
        for value in detail.values():
            messages.extend(_message_list(value))
        return messages
    if isinstance(detail, (list, tuple)):
        messages = []
        for value in detail:
            messages.extend(_message_list(value))
        return messages
    return [str(detail)]


def _validation_fields(detail, prefix=""):
    if not isinstance(detail, dict):
        return {"non_field_errors": _message_list(detail)}

    fields = {}
    for key, value in detail.items():
        field_name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            fields.update(_validation_fields(value, field_name))
        else:
            fields[field_name] = _message_list(value)
    return fields


def _error_payload(code, message, fields=None, retryable=False):
    return {
        "error": {
            "code": code,
            "message": message,
            "fields": fields or {},
            "retryable": bool(retryable),
        }
    }


def mobile_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    headers = response.headers if response is not None else {}

    if isinstance(exc, ValidationError):
        return Response(
            _error_payload(
                "validation_error",
                "Check the highlighted fields.",
                _validation_fields(exc.detail),
            ),
            status=400,
            headers=headers,
        )
    if isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
        return Response(
            _error_payload("authentication_required", "Sign in again to continue."),
            status=401,
            headers=headers,
        )
    if isinstance(exc, PermissionDenied):
        return Response(
            _error_payload("permission_denied", "You cannot perform this action."),
            status=403,
            headers=headers,
        )
    if isinstance(exc, NotFound):
        return Response(
            _error_payload("not_found", "The requested resource is unavailable."),
            status=404,
            headers=headers,
        )
    if isinstance(exc, Throttled):
        return Response(
            _error_payload("rate_limited", "Too many requests. Try again later.", retryable=True),
            status=429,
            headers=headers,
        )
    if isinstance(exc, MobileApiException):
        return Response(
            _error_payload(
                exc.error_code,
                str(exc.detail),
                exc.fields,
                exc.retryable,
            ),
            status=exc.status_code,
            headers=headers,
        )

    logger.exception("Unhandled mobile API exception")
    return Response(
        _error_payload(
            "server_error",
            "Something went wrong. Use the request ID when contacting support.",
            retryable=True,
        ),
        status=500,
    )
