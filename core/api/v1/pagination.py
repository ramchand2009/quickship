"""Signed, tenant-bound cursor pagination for the version 1 mobile API."""

from urllib.parse import parse_qs, urlparse

from django.core import signing
from rest_framework.exceptions import NotFound
from rest_framework.pagination import Cursor, CursorPagination
from rest_framework.response import Response
from rest_framework.utils.urls import replace_query_param


class MobileCursorPagination(CursorPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = ("-created_at", "-pk")
    cursor_query_param = "cursor"
    invalid_cursor_message = "Invalid or expired cursor."
    signing_salt = "mathukai.mobile-api.cursor.v1"

    def _tenant_scope(self, request):
        tenant = getattr(request, "tenant", None)
        tenant_id = getattr(tenant, "pk", None)
        return str(tenant_id or "")

    def encode_cursor(self, cursor):
        payload = {
            "offset": cursor.offset,
            "reverse": cursor.reverse,
            "position": cursor.position,
            "tenant": self._tenant_scope(self.request),
        }
        encoded = signing.dumps(payload, salt=self.signing_salt, compress=True)
        return replace_query_param(self.base_url, self.cursor_query_param, encoded)

    def decode_cursor(self, request):
        encoded = request.query_params.get(self.cursor_query_param)
        if encoded is None:
            return None
        if len(encoded) > 512:
            raise NotFound(self.invalid_cursor_message)

        try:
            payload = signing.loads(encoded, salt=self.signing_salt)
            if not isinstance(payload, dict):
                raise ValueError
            if payload.get("tenant", "") != self._tenant_scope(request):
                raise ValueError
            offset = int(payload.get("offset", 0))
            if offset < 0 or offset > self.offset_cutoff:
                raise ValueError
            reverse = payload.get("reverse", False)
            if not isinstance(reverse, bool):
                raise ValueError
            position = payload.get("position")
            if position is not None and not isinstance(position, str):
                raise ValueError
        except (signing.BadSignature, TypeError, ValueError):
            raise NotFound(self.invalid_cursor_message)

        return Cursor(offset=offset, reverse=reverse, position=position)

    def get_paginated_response(self, data):
        next_link = self.get_next_link()
        next_cursor = None
        if next_link:
            next_cursor = parse_qs(urlparse(next_link).query).get(self.cursor_query_param, [None])[0]
        return Response(
            {
                "data": data,
                "pagination": {
                    "next_cursor": next_cursor,
                    "has_more": next_cursor is not None,
                },
            }
        )
