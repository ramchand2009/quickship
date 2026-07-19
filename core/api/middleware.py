"""Middleware shared by versioned API requests."""

from .request_ids import (
    REQUEST_ID_HEADER,
    bind_request_id,
    reset_request_id,
    select_request_id,
)


class ApiRequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith("/api/"):
            return self.get_response(request)

        request_id = select_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.request_id = request_id
        token = bind_request_id(request_id)
        try:
            response = self.get_response(request)
            response[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            reset_request_id(token)
