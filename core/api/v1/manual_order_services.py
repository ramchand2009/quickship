"""Manual offline order creation for the mobile admin app."""

import secrets
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from core.activity import log_order_activity
from core.models import MobileCustomerProfile, MobileOrderConfirmation, OrderActivityLog, Product, ShiprocketOrder

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
        payload = _customer_payload_from_profile(profile)
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

            product_ids = [item["product_id"] for item in values["items"]]
            products = {
                product.pk: product
                for product in Product.objects.filter(tenant=tenant, pk__in=product_ids, is_active=True)
            }
            order_items = []
            total = Decimal("0.00")
            for item in values["items"]:
                product = products.get(item["product_id"])
                if product is None:
                    raise ValidationError({"items": ["One of the selected products is unavailable."]})
                quantity = int(item["quantity"])
                unit_price = _price_for_product(product)
                line_total = unit_price * quantity
                total += line_total
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

            shipping_amount = _decimal_money(values.get("shipping_base_amount"))
            if shipping_amount > 0:
                total += shipping_amount
                order_items.append(
                    {
                        "product_id": None,
                        "name": "Shipping charge",
                        "sku": "SHIPPING",
                        "quantity": 1,
                        "price": f"{shipping_amount:.2f}",
                        "unit_price": f"{shipping_amount:.2f}",
                        "total": f"{shipping_amount:.2f}",
                        "image_url": "",
                        "source": "manual_mobile_order",
                        "line_type": "shipping",
                        "shipping_base_amount": f"{shipping_amount:.2f}",
                        "shipping_gst_amount": "0.00",
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

            now = timezone.now()
            order = ShiprocketOrder.objects.create(
                tenant=tenant,
                source="manual",
                shiprocket_order_id=f"MO-{tenant.pk}-{now:%Y%m%d%H%M%S}-{secrets.token_hex(3).upper()}",
                channel_order_id="Offline order",
                customer_name=address["name"],
                customer_email=address["email"],
                customer_phone=address["phone"],
                payment_method="offline",
                total=total,
                shipping_base_amount=shipping_amount,
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
                local_status=ShiprocketOrder.STATUS_NEW,
                shipping_address=address,
                billing_address=address,
                order_items=order_items,
                raw_payload={
                    "manual_order": True,
                    "confirmation_status": "awaiting_customer_confirmation",
                    "customer_key": customer.get("key"),
                    "note": values.get("note") or "",
                    "shipping_mode": "charged" if shipping_amount > 0 else "free",
                    "shipping_gst_amount": "0.00",
                    "shipping_total_amount": f"{shipping_amount:.2f}",
                },
            )
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
