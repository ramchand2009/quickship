"""Manual offline order creation for the mobile admin app."""

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import secrets

from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from core.activity import log_order_activity
from core.models import MobileCustomerProfile, MobileOrderConfirmation, OrderActivityLog, Product, ShiprocketOrder

from .exceptions import BusinessRuleError, ConflictError
from .customer_services import _customer_payload_from_profile, _sender_payload, mobile_customer_detail
from .order_mutations import _begin_receipt, _complete_receipt, _delete_failed_receipt, _fingerprint, _serialize_result


def _money(value):
    amount = value if value is not None else Decimal("0.00")
    return {"amount": f"{amount:.2f}", "currency": "INR"}


def _price_for_product(product):
    return product.sale_price or product.regular_price or Decimal("0.00")


def _decimal_money(value):
    try:
        return Decimal(str(value or "0")).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def _split_inclusive_gst(total_amount, *, rate=Decimal("0.18")):
    total = _decimal_money(total_amount)
    if total <= 0:
        return Decimal("0.00"), Decimal("0.00"), Decimal("0.00")
    gst = (total * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    base = (total - gst).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return base, gst, total


def _normalize_address(values):
    return {
        "name": str(values.get("name") or "").strip(),
        "phone": str(values.get("phone") or "").strip(),
        "email": str(values.get("email") or "").strip(),
        "address_1": str(values.get("address_1") or "").strip(),
        "address_2": str(values.get("address_2") or "").strip(),
        "city": str(values.get("city") or "").strip(),
        "state": str(values.get("state") or "").strip(),
        "pincode": str(values.get("pincode") or "").strip(),
        "country": str(values.get("country") or "India").strip() or "India",
    }


def _address_line(address):
    return ", ".join(
        str(address.get(key) or "").strip()
        for key in ["address_1", "address_2", "city", "state", "pincode", "country"]
        if str(address.get(key) or "").strip()
    )


def _customer_from_values(*, tenant, actor, values):
    address = _normalize_address(values)
    profile = MobileCustomerProfile.objects.create(
        tenant=tenant,
        created_by=actor,
        name=address["name"],
        phone=address["phone"],
        email=address["email"],
        address_1=address["address_1"],
        address_2=address["address_2"],
        city=address["city"],
        state=address["state"],
        pincode=address["pincode"],
        country=address["country"],
    )
    payload = _customer_payload_from_profile(profile)
    return payload, address


def _customer_from_key(*, tenant, role, customer_key):
    if str(customer_key or "").startswith("saved:"):
        profile_id = str(customer_key).split(":", 1)[1]
        profile = MobileCustomerProfile.objects.filter(tenant=tenant, pk=profile_id).first()
        if profile is None:
            return None, None
        detail = mobile_customer_detail(tenant=tenant, role=role, customer_key=customer_key)
        payload = detail["data"]["customer"] if detail else _customer_payload_from_profile(profile)
        return payload, _normalize_address(payload["shipping_address"])
    detail = mobile_customer_detail(tenant=tenant, role=role, customer_key=customer_key)
    if not detail:
        return None, None
    payload = detail["data"]["customer"]
    shipping = payload.get("shipping_address") or {}
    return payload, _normalize_address(
        {
            "name": shipping.get("name") or payload.get("name") or "",
            "phone": shipping.get("phone") or payload.get("phone") or "",
            "email": shipping.get("email") or payload.get("email") or "",
            "address_1": shipping.get("address_1") or "",
            "address_2": shipping.get("address_2") or "",
            "city": shipping.get("city") or "",
            "state": shipping.get("state") or "",
            "pincode": shipping.get("pincode") or "",
            "country": shipping.get("country") or "India",
        }
    )


def _build_whatsapp_message(*, tenant, order, address, sender, confirmation_url):
    sender_name = sender.get("name") or tenant.name or "Mathukai Organic"
    return "\n".join(
        [
            f"Hi {address['name']},",
            "",
            f"Please review and confirm your {sender_name} order.",
            f"Order total: ₹ {order.total:.2f}",
            "",
            confirmation_url,
            "",
            "You can confirm the order, request changes, or cancel from this link.",
        ]
    )


def _new_confirmation_token():
    for _attempt in range(10):
        token = secrets.token_urlsafe(32)
        if not MobileOrderConfirmation.objects.filter(token=token).exists():
            return token
    return secrets.token_urlsafe(48)


def _confirmation_url(*, base_url, token):
    path = reverse("manual_order_confirmation", kwargs={"token": token})
    base = str(base_url or "").rstrip("/")
    return f"{base}{path}"


def _build_manual_order_totals(*, tenant, values):
    product_ids = [item["product_id"] for item in values["items"]]
    products = {
        product.pk: product
        for product in Product.objects.filter(tenant=tenant, pk__in=product_ids, is_active=True)
    }
    order_items = []
    products_total = Decimal("0.00")
    for item in values["items"]:
        product = products.get(item["product_id"])
        if product is None:
            raise ValidationError({"items": ["One of the selected products is unavailable."]})
        quantity = int(item["quantity"])
        unit_price = _price_for_product(product)
        line_total = unit_price * quantity
        products_total += line_total
        order_items.append(
            {
                "product_id": product.pk,
                "name": product.name,
                "sku": product.sku,
                "quantity": quantity,
                "price": f"{unit_price:.2f}",
                "unit_price": f"{unit_price:.2f}",
                "total": f"{line_total:.2f}",
                "image_url": product.image_url,
                "source": "manual_mobile_order",
            }
        )

    shipping_base_amount, shipping_gst_amount, shipping_total_amount = _split_inclusive_gst(
        values.get("shipping_base_amount")
    )
    total_before_discount = products_total + shipping_total_amount
    if shipping_total_amount > 0:
        order_items.append(
            {
                "product_id": None,
                "name": "Shipping charge",
                "sku": "SHIPPING",
                "quantity": 1,
                "price": f"{shipping_total_amount:.2f}",
                "unit_price": f"{shipping_total_amount:.2f}",
                "total": f"{shipping_total_amount:.2f}",
                "image_url": "",
                "source": "manual_mobile_order",
                "line_type": "shipping",
                "shipping_base_amount": f"{shipping_base_amount:.2f}",
                "shipping_gst_amount": f"{shipping_gst_amount:.2f}",
                "shipping_label": "Shipping charge",
            }
        )
    else:
        order_items.append(
            {
                "product_id": None,
                "name": "Shipping",
                "sku": "FREE-SHIPPING",
                "quantity": 1,
                "price": "0.00",
                "unit_price": "0.00",
                "total": "0.00",
                "image_url": "",
                "source": "manual_mobile_order",
                "line_type": "shipping",
                "shipping_base_amount": "0.00",
                "shipping_gst_amount": "0.00",
                "shipping_label": "Free shipping",
            }
        )
    discount_amount = _decimal_money(values.get("discount_amount"))
    if discount_amount > total_before_discount:
        raise ValidationError({"discount_amount": ["Discount cannot be more than the order total."]})
    if discount_amount > 0:
        order_items.append(
            {
                "product_id": None,
                "name": "Discount applied",
                "sku": "DISCOUNT",
                "quantity": 1,
                "price": f"-{discount_amount:.2f}",
                "unit_price": f"-{discount_amount:.2f}",
                "total": f"-{discount_amount:.2f}",
                "image_url": "",
                "source": "manual_mobile_order",
                "line_type": "discount",
                "discount_amount": f"{discount_amount:.2f}",
                "discount_label": "Discount applied",
            }
        )
    total = total_before_discount - discount_amount
    return {
        "order_items": order_items,
        "total": total,
        "products_total": products_total,
        "discount_amount": discount_amount,
        "shipping_base_amount": shipping_base_amount,
        "shipping_gst_amount": shipping_gst_amount,
        "shipping_total_amount": shipping_total_amount,
    }


def create_manual_mobile_order(*, session, tenant, role, actor, idempotency_key, values, base_url=""):
    request_hash = _fingerprint(operation="manual_order", order_id="new", payload=values)
    receipt, replay_payload = _begin_receipt(
        session=session,
        tenant=tenant,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay_payload:
        return replay_payload

    try:
        with transaction.atomic():
            if values.get("customer"):
                customer, address = _customer_from_values(tenant=tenant, actor=actor, values=values["customer"])
            else:
                customer, address = _customer_from_key(
                    tenant=tenant,
                    role=role,
                    customer_key=values.get("customer_key"),
                )
            if not customer or not address:
                raise NotFound("The selected customer is unavailable.")

            totals = _build_manual_order_totals(tenant=tenant, values=values)

            now = timezone.now()
            order = ShiprocketOrder.objects.create(
                tenant=tenant,
                source="manual",
                shiprocket_order_id=f"MO-TMP-{tenant.pk}-{secrets.token_hex(8).upper()}",
                channel_order_id="Offline order",
                customer_name=address["name"],
                customer_email=address["email"],
                customer_phone=address["phone"],
                payment_method="offline",
                total=totals["total"],
                shipping_base_amount=totals["shipping_base_amount"],
                order_date=now,
                manual_customer_name=address["name"],
                manual_customer_email=address["email"],
                manual_customer_phone=address["phone"],
                manual_shipping_address_1=address["address_1"],
                manual_shipping_address_2=address["address_2"],
                manual_shipping_city=address["city"],
                manual_shipping_state=address["state"],
                manual_shipping_country=address["country"],
                manual_shipping_pincode=address["pincode"],
                local_status=ShiprocketOrder.STATUS_WAITING,
                shipping_address=address,
                billing_address=address,
                order_items=totals["order_items"],
                raw_payload={
                    "manual_order": True,
                    "confirmation_status": "awaiting_customer_confirmation",
                    "customer_key": customer.get("key"),
                    "note": values.get("note") or "",
                    "shipping_mode": "charged" if totals["shipping_total_amount"] > 0 else "free",
                    "discount_amount": f"{totals['discount_amount']:.2f}",
                    "shipping_gst_amount": f"{totals['shipping_gst_amount']:.2f}",
                    "shipping_total_amount": f"{totals['shipping_total_amount']:.2f}",
                },
            )
            order.shiprocket_order_id = f"MO-{order.pk}"
            order.save(update_fields=["shiprocket_order_id", "updated_at"])
            sender = _sender_payload(tenant)
            token = _new_confirmation_token()
            MobileOrderConfirmation.objects.create(
                tenant=tenant,
                order=order,
                token=token,
                customer_name=address["name"],
                customer_phone=address["phone"],
                address_1=address["address_1"],
                address_2=address["address_2"],
                city=address["city"],
                state=address["state"],
                pincode=address["pincode"],
                country=address["country"],
                expires_at=now + timedelta(days=7),
            )
            confirmation_url = _confirmation_url(base_url=base_url, token=token)
            whatsapp_message = _build_whatsapp_message(
                tenant=tenant,
                order=order,
                address=address,
                sender=sender,
                confirmation_url=confirmation_url,
            )
            log_order_activity(
                order=order,
                event_type=OrderActivityLog.EVENT_MANUAL_UPDATE,
                title="Manual offline order created",
                description="Customer confirmation link is ready to send on WhatsApp.",
                current_status=order.local_status,
                metadata={
                    "source": "mobile_api",
                    "action": "manual_order_created",
                    "confirmation_status": "awaiting_customer_confirmation",
                    "confirmation_url": confirmation_url,
                },
                is_success=True,
                triggered_by=actor,
            )

        payload = _serialize_result(
            tenant=tenant,
            order_id=order.pk,
            role=role,
            effects=[
                {
                    "code": "whatsapp_notification",
                    "state": "queued",
                    "message": "WhatsApp confirmation message is ready.",
                }
            ],
        )
        payload["data"]["customer"] = customer
        payload["data"]["whatsapp"] = {
            "phone": address["phone"],
            "message": whatsapp_message,
            "confirmation_url": confirmation_url,
        }
        payload["data"]["order"]["source"] = {"code": "manual", "label": "Manual"}
        _complete_receipt(receipt, payload)
        return payload
    except Exception:
        _delete_failed_receipt(receipt)
        raise


def update_manual_mobile_order(*, session, tenant, role, actor, order_id, idempotency_key, values, base_url=""):
    request_hash = _fingerprint(operation="manual_order_update", order_id=order_id, payload=values)
    receipt, replay_payload = _begin_receipt(
        session=session,
        tenant=tenant,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay_payload:
        return replay_payload

    try:
        with transaction.atomic():
            order = ShiprocketOrder.objects.select_for_update().filter(tenant=tenant, pk=order_id).first()
            if order is None:
                raise NotFound("The requested resource is unavailable.")
            if order.source != "manual" or not (isinstance(order.raw_payload, dict) and order.raw_payload.get("manual_order")):
                raise BusinessRuleError("Only manual orders can be edited here.")
            if order.local_status != ShiprocketOrder.STATUS_WAITING:
                raise BusinessRuleError("Manual orders can be edited only while they are waiting for customer confirmation.")
            if str(order.version) != values["expected_version"]:
                raise ConflictError(fields={"expected_version": ["Refresh the order and try again."]})

            confirmation = MobileOrderConfirmation.objects.select_for_update().filter(order=order).first()
            if confirmation is None:
                raise BusinessRuleError("This manual order does not have a confirmation link.")

            totals = _build_manual_order_totals(tenant=tenant, values=values)
            previous_total = order.total
            payload = order.raw_payload if isinstance(order.raw_payload, dict) else {}
            payload = {
                **payload,
                "confirmation_status": "awaiting_customer_confirmation",
                "manual_order_updated_at": timezone.now().isoformat(),
                "note": values.get("note") or payload.get("note") or "",
                "shipping_mode": "charged" if totals["shipping_total_amount"] > 0 else "free",
                "discount_amount": f"{totals['discount_amount']:.2f}",
                "shipping_gst_amount": f"{totals['shipping_gst_amount']:.2f}",
                "shipping_total_amount": f"{totals['shipping_total_amount']:.2f}",
            }

            order.total = totals["total"]
            order.shipping_base_amount = totals["shipping_base_amount"]
            order.order_items = totals["order_items"]
            order.raw_payload = payload
            order.version += 1
            order.save(update_fields=["total", "shipping_base_amount", "order_items", "raw_payload", "version", "updated_at"])

            confirmation.status = MobileOrderConfirmation.STATUS_AWAITING
            confirmation.change_note = ""
            confirmation.confirmed_at = None
            confirmation.change_requested_at = None
            confirmation.cancelled_at = None
            confirmation.expires_at = timezone.now() + timedelta(days=7)
            confirmation.save(
                update_fields=[
                    "status",
                    "change_note",
                    "confirmed_at",
                    "change_requested_at",
                    "cancelled_at",
                    "expires_at",
                    "updated_at",
                ]
            )
            confirmation_url = _confirmation_url(base_url=base_url, token=confirmation.token)
            log_order_activity(
                order=order,
                event_type=OrderActivityLog.EVENT_MANUAL_UPDATE,
                title="Manual offline order updated",
                description="Manual order items or shipping charge were updated. Customer confirmation link is ready to share again.",
                previous_status=ShiprocketOrder.STATUS_WAITING,
                current_status=ShiprocketOrder.STATUS_WAITING,
                metadata={
                    "source": "mobile_api",
                    "action": "manual_order_updated",
                    "confirmation_status": "awaiting_customer_confirmation",
                    "confirmation_url": confirmation_url,
                    "previous_total": str(previous_total or "0.00"),
                    "new_total": str(order.total or "0.00"),
                },
                is_success=True,
                triggered_by=actor,
            )

        payload = _serialize_result(tenant=tenant, order_id=order.pk, role=role, effects=[])
        payload["data"]["whatsapp"] = {
            "phone": order.resolved_customer_phone,
            "message": "",
            "confirmation_url": confirmation_url,
        }
        _complete_receipt(receipt, payload)
        return payload
    except Exception:
        _delete_failed_receipt(receipt)
        raise
