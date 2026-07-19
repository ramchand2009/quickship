"""Privacy-safe correlation identifiers for API requests and logs."""

from contextvars import ContextVar
import re
import uuid


REQUEST_ID_HEADER = "X-Request-ID"
MAX_REQUEST_ID_LENGTH = 64
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_current_request_id = ContextVar("api_request_id", default="-")


def select_request_id(candidate):
    value = str(candidate or "").strip()
    if len(value) <= MAX_REQUEST_ID_LENGTH and _SAFE_REQUEST_ID.fullmatch(value):
        return value
    return f"req_{uuid.uuid4().hex}"


def bind_request_id(request_id):
    return _current_request_id.set(request_id)


def reset_request_id(token):
    _current_request_id.reset(token)


def get_request_id():
    return _current_request_id.get()


class RequestIdLogFilter:
    def filter(self, record):
        record.request_id = get_request_id()
        return True
