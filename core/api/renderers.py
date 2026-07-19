"""Response rendering shared by versioned JSON APIs."""

from django.utils import timezone
from rest_framework.renderers import JSONRenderer

from .request_ids import get_request_id


class MobileJSONRenderer(JSONRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        if isinstance(data, dict):
            data = dict(data)
            meta = data.get("meta")
            meta = dict(meta) if isinstance(meta, dict) else {}
            meta["request_id"] = get_request_id()
            meta["server_time"] = timezone.now().isoformat().replace("+00:00", "Z")
            data["meta"] = meta
        return super().render(data, accepted_media_type, renderer_context)
