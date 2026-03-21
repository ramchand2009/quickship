from .models import OrderActivityLog, ShiprocketOrder


def _as_json_payload(value):
    if isinstance(value, (dict, list)):
        return value
    if value in (None, "", ()):
        return {}
    return {"value": str(value)}


def _status_label(status_key):
    status_map = dict(ShiprocketOrder.STATUS_CHOICES)
    return status_map.get(status_key, status_key or "")


def log_order_activity(
    *,
    event_type,
    order=None,
    shiprocket_order_id="",
    title="",
    description="",
    previous_status="",
    current_status="",
    metadata=None,
    is_success=True,
    triggered_by="",
):
    resolved_order = order
    resolved_order_id = str(shiprocket_order_id or "").strip()

    if resolved_order and not resolved_order_id:
        resolved_order_id = str(resolved_order.shiprocket_order_id or "").strip()

    if not resolved_order and resolved_order_id:
        resolved_order = ShiprocketOrder.objects.filter(shiprocket_order_id=resolved_order_id).first()

    if resolved_order and not title and event_type == OrderActivityLog.EVENT_STATUS_CHANGE:
        prev_label = _status_label(previous_status)
        curr_label = _status_label(current_status)
        title = f"Status changed from {prev_label or '-'} to {curr_label or '-'}"

    return OrderActivityLog.objects.create(
        order=resolved_order,
        shiprocket_order_id=resolved_order_id,
        event_type=event_type,
        title=str(title or "").strip(),
        description=str(description or "").strip(),
        previous_status=str(previous_status or "").strip(),
        current_status=str(current_status or "").strip(),
        metadata=_as_json_payload(metadata),
        is_success=bool(is_success),
        triggered_by=str(triggered_by or "").strip(),
    )
