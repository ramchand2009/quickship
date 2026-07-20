from decimal import Decimal
from datetime import timedelta
import uuid

from django.db import models
from django.conf import settings
from django.utils import timezone

DEFAULT_TENANT_NAME = "Mathukai"
DEFAULT_TENANT_SLUG = "mathukai"


def normalize_sku(value):
    return str(value or "").strip().upper()


def normalize_barcode(value):
    return str(value or "").strip()


def normalize_channel_product_id(value):
    return str(value or "").strip()


def compact_woocommerce_address(address):
    if not isinstance(address, dict):
        return {}
    first_name = str(address.get("first_name") or "").strip()
    last_name = str(address.get("last_name") or "").strip()
    return {
        "name": " ".join(value for value in [first_name, last_name] if value).strip(),
        "email": address.get("email") or "",
        "phone": address.get("phone") or "",
        "address_1": address.get("address_1") or "",
        "address_2": address.get("address_2") or "",
        "city": address.get("city") or "",
        "state": address.get("state") or "",
        "country": address.get("country") or "",
        "pincode": address.get("postcode") or address.get("pincode") or "",
    }


def first_present(*values):
    for value in values:
        if value is not None and str(value).strip():
            return value
    return ""


def get_default_tenant():
    try:
        return Tenant.objects.only("pk").get(slug=DEFAULT_TENANT_SLUG)
    except Tenant.DoesNotExist:
        return Tenant.objects.create(slug=DEFAULT_TENANT_SLUG, name=DEFAULT_TENANT_NAME)


def get_default_tenant_pk():
    return get_default_tenant().pk


def default_mobile_session_expiry():
    return timezone.now() + timedelta(days=settings.MOBILE_SESSION_ABSOLUTE_LIFETIME_DAYS)


def default_mobile_refresh_expiry():
    return timezone.now() + timedelta(days=settings.MOBILE_REFRESH_TOKEN_LIFETIME_DAYS)


class Tenant(models.Model):
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)
    auto_approve_product_changes = models.BooleanField(default=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_tenants",
    )
    contact_name = models.CharField(max_length=160, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name or self.slug

    @classmethod
    def get_default(cls):
        return get_default_tenant()


class TenantMembership(models.Model):
    ROLE_VENDOR_OWNER = "vendor_owner"
    ROLE_VENDOR_OPERATOR = "vendor_operator"
    ROLE_VENDOR_VIEWER = "vendor_viewer"
    ROLE_WAREHOUSE_OPERATOR = "warehouse_operator"
    ROLE_CHOICES = [
        (ROLE_VENDOR_OWNER, "Vendor Owner"),
        (ROLE_VENDOR_OPERATOR, "Vendor Operator"),
        (ROLE_VENDOR_VIEWER, "Vendor Viewer"),
        (ROLE_WAREHOUSE_OPERATOR, "Warehouse Operator"),
    ]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tenant_memberships")
    role = models.CharField(max_length=32, choices=ROLE_CHOICES, default=ROLE_VENDOR_OPERATOR)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tenant__name", "user__username"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "user"], name="uniq_tenant_membership_tenant_user"),
        ]

    def __str__(self):
        return f"{self.user} @ {self.tenant} ({self.role})"


class MobileSession(models.Model):
    PLATFORM_ANDROID = "android"
    PLATFORM_CHOICES = [(PLATFORM_ANDROID, "Android")]

    STATUS_ACTIVE = "active"
    STATUS_REVOKED = "revoked"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_REVOKED, "Revoked"),
        (STATUS_EXPIRED, "Expired"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mobile_sessions",
    )
    installation_id = models.UUIDField()
    platform = models.CharField(
        max_length=16,
        choices=PLATFORM_CHOICES,
        default=PLATFORM_ANDROID,
    )
    app_version = models.CharField(max_length=32)
    active_tenant = models.ForeignKey(
        Tenant,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="active_mobile_sessions",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    auth_generation = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(default=default_mobile_session_expiry)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "installation_id"],
                name="uniq_mobile_session_user_installation",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "status", "expires_at"],
                name="mobile_sess_user_state_idx",
            ),
            models.Index(
                fields=["active_tenant", "status"],
                name="mobile_sess_tenant_state_idx",
            ),
        ]

    def __str__(self):
        return f"MobileSession {self.pk}"


class MobileRefreshToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        MobileSession,
        on_delete=models.CASCADE,
        related_name="refresh_tokens",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_mobile_refresh_expiry)
    consumed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["session", "expires_at"],
                name="mobile_rt_session_exp_idx",
            ),
            models.Index(
                fields=["expires_at", "revoked_at"],
                name="mobile_rt_exp_revoke_idx",
            ),
        ]

    def __str__(self):
        return f"MobileRefreshToken {self.pk}"


class Project(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="projects",
        default=get_default_tenant_pk,
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ContactMessage(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="contact_messages",
        default=get_default_tenant_pk,
    )
    name = models.CharField(max_length=120)
    email = models.EmailField()
    subject = models.CharField(max_length=160)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name}: {self.subject}"


class SenderAddress(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="sender_addresses",
        default=get_default_tenant_pk,
    )
    name = models.CharField(max_length=160, default="Mathukai Organic")
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    address_1 = models.CharField(max_length=255, blank=True)
    address_2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=120, default="India")
    pincode = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]

    def __str__(self):
        return self.name or "Sender Address"

    @classmethod
    def get_default(cls):
        sender = cls.objects.order_by("-updated_at", "-created_at").first()
        if sender:
            return sender
        return cls.objects.create(
            name="Mathukai Organic",
            country="India",
        )


class WhatsAppSettings(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="whatsapp_settings",
        default=get_default_tenant_pk,
    )
    enabled = models.BooleanField(default=False)
    api_base_url = models.CharField(max_length=255, blank=True)
    api_key = models.TextField(blank=True)
    account_id = models.CharField(max_length=160, blank=True)
    account_name = models.CharField(max_length=160, blank=True)
    test_phone_number = models.CharField(max_length=32, blank=True)
    test_message_text = models.CharField(
        max_length=255,
        blank=True,
        default="Hi from Mathukai test message.",
    )
    test_template_name = models.CharField(max_length=160, blank=True)
    test_template_params = models.TextField(blank=True, default="{}")
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "WhatsApp Settings"
        verbose_name_plural = "WhatsApp Settings"
        ordering = ["-updated_at", "-created_at"]

    def __str__(self):
        return f"WhatsApp Settings ({self.tenant})"

    @classmethod
    def get_default(cls):
        tenant = Tenant.get_default()
        settings_row = cls.objects.filter(tenant=tenant).order_by("-updated_at", "-created_at").first()
        if settings_row:
            return settings_row
        return cls.objects.create(tenant=tenant, test_message_text="Hi from Mathukai test message.")

    @classmethod
    def get_for_tenant(cls, tenant):
        if tenant is None:
            return cls.get_default()
        settings_row = cls.objects.filter(tenant=tenant).order_by("-updated_at", "-created_at").first()
        if settings_row:
            return settings_row
        return cls.objects.create(tenant=tenant, test_message_text="Hi from Mathukai test message.")


class WooCommerceSettings(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="woocommerce_settings",
        default=get_default_tenant_pk,
    )
    store_url = models.CharField(max_length=255, blank=True)
    consumer_key = models.CharField(max_length=255, blank=True)
    consumer_secret = models.CharField(max_length=255, blank=True)
    webhook_secret = models.CharField(max_length=255, blank=True)
    import_statuses = models.CharField(max_length=255, default="pending,processing,on-hold,whatsapp-draft", blank=True)
    status_map = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "WooCommerce Settings"
        verbose_name_plural = "WooCommerce Settings"
        ordering = ["-updated_at", "-created_at"]

    def __str__(self):
        return "WooCommerce Settings"

    @classmethod
    def get_default(cls):
        settings_row = cls.objects.order_by("-updated_at", "-created_at").first()
        if settings_row:
            return settings_row
        return cls.objects.create()


class TenantWooCommerceMappingRule(models.Model):
    MATCH_CATEGORY = "category"
    MATCH_TAG = "tag"
    MATCH_SKU_PREFIX = "sku_prefix"
    MATCH_PRODUCT_ID = "product_id"
    MATCH_CHOICES = [
        (MATCH_CATEGORY, "WooCommerce Category"),
        (MATCH_TAG, "WooCommerce Tag"),
        (MATCH_SKU_PREFIX, "SKU Prefix"),
        (MATCH_PRODUCT_ID, "WooCommerce Product ID"),
    ]

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="woocommerce_mapping_rules",
    )
    match_type = models.CharField(max_length=32, choices=MATCH_CHOICES)
    match_value = models.CharField(max_length=160)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tenant__name", "match_type", "match_value"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "match_type", "match_value"],
                name="uniq_tenant_woocommerce_mapping_rule",
            )
        ]

    def __str__(self):
        return f"{self.tenant} | {self.match_type}: {self.match_value}"

    def save(self, *args, **kwargs):
        self.match_value = str(self.match_value or "").strip()
        if self.match_type == self.MATCH_SKU_PREFIX:
            self.match_value = self.match_value.upper()
        super().save(*args, **kwargs)


class WooCommerceSyncRun(models.Model):
    RUN_PRODUCT_SYNC = "product_sync"
    RUN_ORDER_SYNC = "order_sync"
    RUN_CHOICES = [
        (RUN_PRODUCT_SYNC, "Product Sync"),
        (RUN_ORDER_SYNC, "Order Sync"),
    ]
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
    ]

    run_type = models.CharField(max_length=32, choices=RUN_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    triggered_by = models.CharField(max_length=150, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-finished_at", "-started_at"]

    def __str__(self):
        return f"{self.get_run_type_display()} {self.status} at {self.finished_at}"


class WebPushSubscription(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="web_push_subscriptions",
        default=get_default_tenant_pk,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="web_push_subscriptions",
    )
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh_key = models.TextField()
    auth_key = models.TextField()
    user_agent = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]

    def __str__(self):
        owner = self.user.get_username() if self.user_id and self.user else "Unknown user"
        return f"Push subscription for {owner}"

    def to_subscription_info(self):
        return {
            "endpoint": self.endpoint,
            "keys": {
                "p256dh": self.p256dh_key,
                "auth": self.auth_key,
            },
        }


class WhatsAppTemplate(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="whatsapp_templates",
        default=get_default_tenant_pk,
    )
    template_id = models.CharField(max_length=128, blank=True)
    name = models.CharField(max_length=160)
    language = models.CharField(max_length=32, blank=True)
    category = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=64, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "language", "-synced_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "name", "language"],
                name="uniq_whatsapp_template_tenant_name_language",
            )
        ]

    def __str__(self):
        if self.language:
            return f"{self.name} ({self.language})"
        return self.name


class WhatsAppStatusTemplateConfig(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="whatsapp_status_template_configs",
        default=get_default_tenant_pk,
    )
    local_status = models.CharField(max_length=32)
    enabled = models.BooleanField(default=False)
    template_name = models.CharField(max_length=160, blank=True)
    template_id = models.CharField(max_length=128, blank=True)
    template_param_mapping = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["local_status"]
        verbose_name = "WhatsApp Status Template Config"
        verbose_name_plural = "WhatsApp Status Template Config"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "local_status"],
                name="uniq_whatsapp_status_config_tenant_status",
            )
        ]

    def __str__(self):
        label = dict(ShiprocketOrder.STATUS_CHOICES).get(self.local_status, self.local_status)
        template = self.template_name or self.template_id or "No template"
        return f"{label}: {template}"

    @classmethod
    def get_or_create_for_status(cls, local_status, tenant=None):
        tenant = tenant or Tenant.get_default()
        return cls.objects.get_or_create(tenant=tenant, local_status=local_status)


class ShiprocketOrder(models.Model):
    SOURCE_SHIPROCKET = "shiprocket"
    SOURCE_WOOCOMMERCE = "woocommerce"
    SOURCE_CHOICES = [
        (SOURCE_SHIPROCKET, "Shiprocket"),
        (SOURCE_WOOCOMMERCE, "WooCommerce"),
    ]
    STATUS_NEW = "new_order"
    STATUS_ACCEPTED = "order_accepted"
    STATUS_PACKED = "order_packed"
    STATUS_SHIPPED = "shipped"
    STATUS_DELIVERY_ISSUE = "delivery_issue"
    STATUS_OUT_FOR_DELIVERY = "out_for_delivery"
    STATUS_DELIVERED = "delivered"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "order_cancelled"
    CANCEL_REASON_CUSTOMER_REQUEST = "customer_request"
    CANCEL_REASON_PAYMENT_FAILED = "payment_failed"
    CANCEL_REASON_OUT_OF_STOCK = "out_of_stock"
    CANCEL_REASON_ADDRESS_ISSUE = "address_issue"
    CANCEL_REASON_COURIER_ISSUE = "courier_issue"
    CANCEL_REASON_OTHER = "other"
    STATUS_CHOICES = [
        (STATUS_NEW, "New Order"),
        (STATUS_ACCEPTED, "Order Accepted"),
        (STATUS_PACKED, "Order Packed"),
        (STATUS_SHIPPED, "Shipped"),
        (STATUS_DELIVERY_ISSUE, "Delivery Issue"),
        (STATUS_OUT_FOR_DELIVERY, "Out for Delivery"),
        (STATUS_DELIVERED, "Delivered"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Order Cancelled"),
    ]
    CANCELLATION_REASON_CHOICES = [
        (CANCEL_REASON_CUSTOMER_REQUEST, "Customer Request"),
        (CANCEL_REASON_PAYMENT_FAILED, "Payment Failed"),
        (CANCEL_REASON_OUT_OF_STOCK, "Out of Stock"),
        (CANCEL_REASON_ADDRESS_ISSUE, "Address Issue"),
        (CANCEL_REASON_COURIER_ISSUE, "Courier Issue"),
        (CANCEL_REASON_OTHER, "Other"),
    ]
    ALLOWED_STATUS_TRANSITIONS = {
        STATUS_NEW: [STATUS_ACCEPTED, STATUS_CANCELLED],
        STATUS_ACCEPTED: [STATUS_SHIPPED, STATUS_CANCELLED],
        STATUS_PACKED: [STATUS_SHIPPED, STATUS_CANCELLED],
        STATUS_SHIPPED: [STATUS_COMPLETED, STATUS_CANCELLED],
        STATUS_DELIVERY_ISSUE: [STATUS_DELIVERED, STATUS_OUT_FOR_DELIVERY],
        STATUS_OUT_FOR_DELIVERY: [STATUS_DELIVERED],
        STATUS_DELIVERED: [STATUS_COMPLETED],
        STATUS_COMPLETED: [],
        STATUS_CANCELLED: [],
    }
    LOCKED_STATUSES = [
        STATUS_COMPLETED,
        STATUS_CANCELLED,
    ]
    MANUAL_EDIT_LOCK_STATUSES = [
        STATUS_SHIPPED,
        STATUS_DELIVERY_ISSUE,
        STATUS_OUT_FOR_DELIVERY,
        STATUS_DELIVERED,
        STATUS_COMPLETED,
    ]
    PACKING_REQUIRED_FIELDS = [
        ("name", "Name"),
        ("phone", "Phone"),
        ("address_1", "Address"),
        ("pincode", "Pincode"),
    ]

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="orders",
        default=get_default_tenant_pk,
    )
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES, default=SOURCE_SHIPROCKET)
    shiprocket_order_id = models.CharField(max_length=64, unique=True)
    woocommerce_order_id = models.CharField(max_length=64, blank=True, db_index=True)
    woocommerce_order_key = models.CharField(max_length=128, blank=True)
    woocommerce_status = models.CharField(max_length=64, blank=True)
    woocommerce_synced_at = models.DateTimeField(null=True, blank=True)
    woocommerce_status_synced_at = models.DateTimeField(null=True, blank=True)
    channel_order_id = models.CharField(max_length=128, blank=True)
    customer_name = models.CharField(max_length=160, blank=True)
    customer_email = models.EmailField(blank=True)
    customer_phone = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=64, blank=True)
    payment_method = models.CharField(max_length=64, blank=True)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    order_date = models.DateTimeField(null=True, blank=True)
    manual_customer_name = models.CharField(max_length=160, blank=True)
    manual_customer_email = models.EmailField(blank=True)
    manual_customer_phone = models.CharField(max_length=32, blank=True)
    manual_customer_alternate_phone = models.CharField(max_length=32, blank=True)
    manual_shipping_address_1 = models.CharField(max_length=255, blank=True)
    manual_shipping_address_2 = models.CharField(max_length=255, blank=True)
    manual_shipping_city = models.CharField(max_length=120, blank=True)
    manual_shipping_state = models.CharField(max_length=120, blank=True)
    manual_shipping_country = models.CharField(max_length=120, blank=True)
    manual_shipping_pincode = models.CharField(max_length=20, blank=True)
    local_status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_NEW)
    cancellation_reason = models.CharField(max_length=32, choices=CANCELLATION_REASON_CHOICES, blank=True)
    cancellation_note = models.CharField(max_length=255, blank=True)
    tracking_number = models.CharField(max_length=128, blank=True)
    shipping_base_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    packed_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    out_for_delivery_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    payment_received_at = models.DateTimeField(null=True, blank=True)
    label_print_count = models.PositiveIntegerField(default=0)
    last_label_printed_at = models.DateTimeField(null=True, blank=True)
    shipping_address = models.JSONField(default=dict, blank=True)
    billing_address = models.JSONField(default=dict, blank=True)
    order_items = models.JSONField(default=list, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-order_date", "-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "woocommerce_order_id"],
                condition=models.Q(source="woocommerce") & ~models.Q(woocommerce_order_id=""),
                name="uniq_tenant_woocommerce_order_id",
            )
        ]

    def __str__(self):
        return f"{self.shiprocket_order_id} - {self.customer_name or 'Unknown customer'}"

    @property
    def source_label(self):
        return dict(self.SOURCE_CHOICES).get(self.source, self.source or "Shiprocket")

    @property
    def courier_name(self):
        payload = self.raw_payload if isinstance(self.raw_payload, dict) else {}
        return str(payload.get("courier_name") or payload.get("courier") or "").strip()

    @courier_name.setter
    def courier_name(self, value):
        payload = self.raw_payload if isinstance(self.raw_payload, dict) else {}
        payload = {**payload, "courier_name": str(value or "").strip()}
        self.raw_payload = payload

    @property
    def source_order_reference(self):
        if self.source == self.SOURCE_WOOCOMMERCE:
            return self.channel_order_id or self.shiprocket_order_id or ""
        return self.shiprocket_order_id or ""

    def missing_fields_for_packing(self):
        shipping = self.display_shipping_address
        missing = []
        for key, label in self.PACKING_REQUIRED_FIELDS:
            value = shipping.get(key)
            if value is None or not str(value).strip():
                missing.append(label)
        return missing

    @property
    def is_manual_edit_locked(self):
        return bool(self.shipped_at) or self.local_status in self.MANUAL_EDIT_LOCK_STATUSES

    @property
    def display_shipping_address(self):
        shipping = self.shipping_address if isinstance(self.shipping_address, dict) else {}
        billing = self.billing_address if isinstance(self.billing_address, dict) else {}
        payload = self.raw_payload if isinstance(self.raw_payload, dict) else {}
        raw_shipping = compact_woocommerce_address(payload.get("shipping") or {})
        raw_billing = compact_woocommerce_address(payload.get("billing") or {})
        is_woocommerce = self.source == self.SOURCE_WOOCOMMERCE
        primary_address = billing if is_woocommerce else shipping
        primary_raw_address = raw_billing if is_woocommerce else raw_shipping
        secondary_address = shipping if is_woocommerce else billing
        secondary_raw_address = raw_shipping if is_woocommerce else raw_billing
        return {
            "name": first_present(
                self.manual_customer_name,
                primary_address.get("name"),
                primary_raw_address.get("name"),
                secondary_address.get("name"),
                secondary_raw_address.get("name"),
                self.customer_name,
            ),
            "email": first_present(
                self.manual_customer_email,
                primary_address.get("email"),
                primary_raw_address.get("email"),
                secondary_address.get("email"),
                secondary_raw_address.get("email"),
                self.customer_email,
            ),
            "phone": first_present(
                self.manual_customer_phone,
                primary_address.get("phone"),
                primary_raw_address.get("phone"),
                secondary_address.get("phone"),
                secondary_raw_address.get("phone"),
                self.customer_phone,
            ),
            "alternate_phone": first_present(self.manual_customer_alternate_phone, shipping.get("alternate_phone")),
            "address_1": first_present(
                self.manual_shipping_address_1,
                primary_address.get("address_1"),
                primary_raw_address.get("address_1"),
                secondary_address.get("address_1"),
                secondary_raw_address.get("address_1"),
            ),
            "address_2": first_present(
                self.manual_shipping_address_2,
                primary_address.get("address_2"),
                primary_raw_address.get("address_2"),
                secondary_address.get("address_2"),
                secondary_raw_address.get("address_2"),
            ),
            "city": first_present(
                self.manual_shipping_city,
                primary_address.get("city"),
                primary_raw_address.get("city"),
                secondary_address.get("city"),
                secondary_raw_address.get("city"),
            ),
            "state": first_present(
                self.manual_shipping_state,
                primary_address.get("state"),
                primary_raw_address.get("state"),
                secondary_address.get("state"),
                secondary_raw_address.get("state"),
            ),
            "country": first_present(
                self.manual_shipping_country,
                primary_address.get("country"),
                primary_raw_address.get("country"),
                secondary_address.get("country"),
                secondary_raw_address.get("country"),
            ),
            "pincode": first_present(
                self.manual_shipping_pincode,
                primary_address.get("pincode"),
                primary_raw_address.get("pincode"),
                secondary_address.get("pincode"),
                secondary_raw_address.get("pincode"),
            ),
            "latitude": first_present(shipping.get("latitude"), raw_shipping.get("latitude")),
            "longitude": first_present(shipping.get("longitude"), raw_shipping.get("longitude")),
        }

    @property
    def resolved_customer_phone(self):
        candidates = [
            self.manual_customer_phone,
            (self.shipping_address or {}).get("phone") if isinstance(self.shipping_address, dict) else "",
            (self.billing_address or {}).get("phone") if isinstance(self.billing_address, dict) else "",
            self.customer_phone,
        ]
        payload = self.raw_payload if isinstance(self.raw_payload, dict) else {}
        for section_name in ["billing", "shipping"]:
            section = payload.get(section_name)
            if isinstance(section, dict):
                candidates.append(section.get("phone"))
        candidates.extend(
            [
                payload.get("billing_phone"),
                payload.get("shipping_phone"),
                payload.get("phone"),
                payload.get("customer_phone"),
            ]
        )
        for value in candidates:
            phone = str(value or "").strip()
            if phone:
                return phone
        return ""

    @property
    def status_date_rows(self):
        return [
            {"label": "Order Date", "value": self.order_date},
            {"label": "Packed Date", "value": self.packed_at},
            {"label": "Shipped Date", "value": self.shipped_at},
            {"label": "Delivered Date", "value": self.delivered_at},
        ]

    @property
    def last_status_changed_at(self):
        candidates = [
            self.order_date,
            self.packed_at,
            self.shipped_at,
            self.out_for_delivery_at,
            self.delivered_at,
            self.completed_at,
        ]
        populated = [value for value in candidates if value]
        return max(populated) if populated else None

    @property
    def current_status_date_label(self):
        label_map = {
            self.STATUS_NEW: "Order Date",
            self.STATUS_ACCEPTED: "Order Date",
            self.STATUS_PACKED: "Packed Date",
            self.STATUS_SHIPPED: "Shipped Date",
            self.STATUS_DELIVERY_ISSUE: "Delivery Issue Date",
            self.STATUS_OUT_FOR_DELIVERY: "Out For Delivery Date",
            self.STATUS_DELIVERED: "Delivered Date",
            self.STATUS_COMPLETED: "Completed Date",
            self.STATUS_CANCELLED: "Cancelled Date",
        }
        return label_map.get(self.local_status, "Status Date")

    @property
    def current_status_date(self):
        if self.local_status in {self.STATUS_NEW, self.STATUS_ACCEPTED}:
            return self.order_date or self.created_at
        if self.local_status == self.STATUS_PACKED:
            return self.packed_at or self.updated_at
        if self.local_status == self.STATUS_SHIPPED:
            return self.shipped_at or self.updated_at
        if self.local_status == self.STATUS_DELIVERY_ISSUE:
            return self.updated_at
        if self.local_status == self.STATUS_OUT_FOR_DELIVERY:
            return self.out_for_delivery_at or self.updated_at
        if self.local_status == self.STATUS_DELIVERED:
            return self.delivered_at or self.updated_at
        if self.local_status == self.STATUS_COMPLETED:
            return self.completed_at or self.updated_at
        if self.local_status == self.STATUS_CANCELLED:
            return self.updated_at
        return self.last_status_changed_at or self.updated_at

    @property
    def shipping_tax_amount(self):
        return (self.shipping_base_amount or Decimal("0.00")) * Decimal("0.18")

    @property
    def shipping_total_amount(self):
        return (self.shipping_base_amount or Decimal("0.00")) + self.shipping_tax_amount


class ProductCategory(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="product_categories",
        default=get_default_tenant_pk,
    )
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Product Category"
        verbose_name_plural = "Product Categories"

    def __str__(self):
        return self.name


class Product(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="products",
        default=get_default_tenant_pk,
    )
    name = models.CharField(max_length=160)
    category = models.CharField(max_length=120, blank=True)
    category_master = models.ForeignKey(
        ProductCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
    )
    sku = models.CharField(max_length=120, unique=True)
    barcode = models.CharField(max_length=120, blank=True, null=True, unique=True)
    smartbiz_product_id = models.CharField(max_length=160, blank=True, null=True, unique=True)
    woocommerce_product_id = models.CharField(max_length=160, blank=True)
    woocommerce_variation_id = models.CharField(max_length=160, blank=True)
    image_url = models.URLField(max_length=1000, blank=True)
    description = models.TextField(blank=True)
    actual_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    regular_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    sale_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    stock_quantity = models.IntegerField(default=0)
    reorder_level = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "sku"]

    def __str__(self):
        return f"{self.name} ({self.sku})"

    @property
    def is_low_stock(self):
        return self.stock_quantity > 0 and self.stock_quantity <= int(self.reorder_level or 0)

    @property
    def is_no_stock(self):
        return self.stock_quantity <= 0

    @property
    def category_label(self):
        if self.category_master_id and self.category_master:
            return self.category_master.name
        return str(self.category or "").strip()

    def save(self, *args, **kwargs):
        if self.category_master_id and self.category_master:
            self.category = self.category_master.name
        self.category = str(self.category or "").strip()
        self.sku = normalize_sku(self.sku)
        barcode_value = normalize_barcode(self.barcode)
        self.barcode = barcode_value or None
        smartbiz_product_id = normalize_channel_product_id(self.smartbiz_product_id)
        self.smartbiz_product_id = smartbiz_product_id or None
        self.woocommerce_product_id = normalize_channel_product_id(self.woocommerce_product_id)
        self.woocommerce_variation_id = normalize_channel_product_id(self.woocommerce_variation_id)
        super().save(*args, **kwargs)


class ProductChangeRequest(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="product_change_requests",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="change_requests",
    )
    requested_by = models.CharField(max_length=150, blank=True)
    reviewed_by = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    old_values = models.JSONField(default=dict, blank=True)
    new_values = models.JSONField(default=dict, blank=True)
    review_note = models.CharField(max_length=255, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status", "-created_at"]),
            models.Index(fields=["product", "status"]),
        ]

    def __str__(self):
        return f"{self.product} change request ({self.status})"


class BusinessExpense(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="business_expenses",
        default=get_default_tenant_pk,
    )
    expense_person = models.ForeignKey(
        "ExpensePerson",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
    )
    item_name = models.CharField(max_length=160)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    remark = models.CharField(max_length=255, blank=True)
    created_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.item_name} x {self.quantity}"

    @property
    def total_amount(self):
        return (self.unit_price or 0) * (self.quantity or 0)


class VendorSettlement(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="settlements",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    is_paid = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.CharField(max_length=150, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_start", "tenant__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "period_start", "period_end"],
                name="unique_vendor_settlement_period",
            )
        ]

    def __str__(self):
        return f"{self.tenant} settlement {self.period_start} to {self.period_end}"


class ExpensePerson(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="expense_people",
        default=get_default_tenant_pk,
    )
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class StockMovement(models.Model):
    TYPE_MANUAL_ADD = "manual_add"
    TYPE_MANUAL_REMOVE = "manual_remove"
    TYPE_MANUAL_SET = "manual_set"
    TYPE_SPECIAL_ISSUE = "special_issue"
    TYPE_ORDER_ACCEPTED = "order_accepted"
    TYPE_ORDER_CANCELLED = "order_cancelled"
    ISSUE_CATEGORY_FREE = "free"
    ISSUE_CATEGORY_SAMPLE = "sample"
    ISSUE_CATEGORY_COMPLIMENTARY = "complimentary"
    TYPE_CHOICES = [
        (TYPE_MANUAL_ADD, "Manual Add"),
        (TYPE_MANUAL_REMOVE, "Manual Remove"),
        (TYPE_MANUAL_SET, "Manual Set"),
        (TYPE_SPECIAL_ISSUE, "Free / Sample Issue"),
        (TYPE_ORDER_ACCEPTED, "Order Accepted Deduction"),
        (TYPE_ORDER_CANCELLED, "Order Cancelled Restore"),
    ]
    ISSUE_CATEGORY_CHOICES = [
        (ISSUE_CATEGORY_FREE, "Free"),
        (ISSUE_CATEGORY_SAMPLE, "Sample"),
        (ISSUE_CATEGORY_COMPLIMENTARY, "Complimentary"),
    ]

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="stock_movements",
        default=get_default_tenant_pk,
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="stock_movements",
    )
    order = models.ForeignKey(
        ShiprocketOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
    )
    shiprocket_order_id = models.CharField(max_length=64, blank=True)
    movement_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    quantity_delta = models.IntegerField()
    quantity_before = models.IntegerField(default=0)
    quantity_after = models.IntegerField(default=0)
    sku_snapshot = models.CharField(max_length=120, blank=True)
    barcode_snapshot = models.CharField(max_length=120, blank=True)
    reference_key = models.CharField(max_length=255, blank=True, null=True, unique=True)
    issue_category = models.CharField(max_length=32, choices=ISSUE_CATEGORY_CHOICES, blank=True)
    issue_recipient = models.CharField(max_length=160, blank=True)
    notes = models.TextField(blank=True)
    triggered_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        direction = "+" if self.quantity_delta >= 0 else ""
        return f"{self.product.sku} | {direction}{self.quantity_delta} | {self.movement_type}"


class OrderActivityLog(models.Model):
    EVENT_STATUS_CHANGE = "status_change"
    EVENT_MANUAL_UPDATE = "manual_update"
    EVENT_WHATSAPP_QUEUED = "whatsapp_queued"
    EVENT_WHATSAPP_QUEUE_SUCCESS = "whatsapp_queue_success"
    EVENT_WHATSAPP_QUEUE_RETRY = "whatsapp_queue_retry"
    EVENT_WHATSAPP_QUEUE_FAILED = "whatsapp_queue_failed"
    EVENT_WHATSAPP_QUEUE_SKIPPED = "whatsapp_queue_skipped"
    EVENT_WHATSAPP_WEBHOOK = "whatsapp_webhook"
    EVENT_LABEL_PRINTED = "label_printed"
    EVENT_STOCK_DEDUCTED = "stock_deducted"
    EVENT_STOCK_RESTORED = "stock_restored"
    EVENT_STOCK_WARNING = "stock_warning"
    EVENT_CHOICES = [
        (EVENT_STATUS_CHANGE, "Status Change"),
        (EVENT_MANUAL_UPDATE, "Manual Update"),
        (EVENT_WHATSAPP_QUEUED, "WhatsApp Queued"),
        (EVENT_WHATSAPP_QUEUE_SUCCESS, "WhatsApp Sent"),
        (EVENT_WHATSAPP_QUEUE_RETRY, "WhatsApp Retry"),
        (EVENT_WHATSAPP_QUEUE_FAILED, "WhatsApp Failed"),
        (EVENT_WHATSAPP_QUEUE_SKIPPED, "WhatsApp Skipped"),
        (EVENT_WHATSAPP_WEBHOOK, "WhatsApp Webhook"),
        (EVENT_LABEL_PRINTED, "Label Printed"),
        (EVENT_STOCK_DEDUCTED, "Stock Deducted"),
        (EVENT_STOCK_RESTORED, "Stock Restored"),
        (EVENT_STOCK_WARNING, "Stock Warning"),
    ]

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="order_activity_logs",
        default=get_default_tenant_pk,
    )
    order = models.ForeignKey(
        ShiprocketOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_logs",
    )
    shiprocket_order_id = models.CharField(max_length=64, blank=True)
    event_type = models.CharField(max_length=48, choices=EVENT_CHOICES, default=EVENT_STATUS_CHANGE)
    title = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    previous_status = models.CharField(max_length=32, blank=True)
    current_status = models.CharField(max_length=32, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_success = models.BooleanField(default=True)
    triggered_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        order_id = self.shiprocket_order_id or "NoOrder"
        return f"{order_id} | {self.event_type} | {self.created_at:%Y-%m-%d %H:%M:%S}"


class WhatsAppNotificationLog(models.Model):
    TRIGGER_STATUS_CHANGE = "status_change"
    TRIGGER_RESEND = "resend"
    TRIGGER_PAYMENT_REMINDER = "payment_reminder"
    TRIGGER_TEST_MESSAGE = "test_message"
    TRIGGER_TEST_TEMPLATE = "test_template"
    TRIGGER_QUEUE_ALERT = "queue_alert"
    TRIGGER_WEBHOOK_STATUS = "webhook_status"
    TRIGGER_WEBHOOK_INCOMING = "webhook_incoming"
    TRIGGER_CHOICES = [
        (TRIGGER_STATUS_CHANGE, "Status Change"),
        (TRIGGER_RESEND, "Resend"),
        (TRIGGER_PAYMENT_REMINDER, "Payment Reminder"),
        (TRIGGER_TEST_MESSAGE, "Test Message"),
        (TRIGGER_TEST_TEMPLATE, "Test Template"),
        (TRIGGER_QUEUE_ALERT, "Queue Alert"),
        (TRIGGER_WEBHOOK_STATUS, "Webhook Status"),
        (TRIGGER_WEBHOOK_INCOMING, "Webhook Incoming"),
    ]

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="whatsapp_notification_logs",
        default=get_default_tenant_pk,
    )
    order = models.ForeignKey(
        ShiprocketOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_logs",
    )
    shiprocket_order_id = models.CharField(max_length=64, blank=True)
    trigger = models.CharField(max_length=32, choices=TRIGGER_CHOICES, default=TRIGGER_STATUS_CHANGE)
    previous_status = models.CharField(max_length=32, blank=True)
    current_status = models.CharField(max_length=32, blank=True)
    phone_number = models.CharField(max_length=32, blank=True)
    mode = models.CharField(max_length=32, blank=True)
    template_name = models.CharField(max_length=160, blank=True)
    template_id = models.CharField(max_length=128, blank=True)
    idempotency_key = models.CharField(max_length=64, blank=True)
    external_message_id = models.CharField(max_length=160, blank=True)
    delivery_status = models.CharField(max_length=64, blank=True)
    webhook_event_id = models.CharField(max_length=160, blank=True)
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    is_success = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)
    triggered_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        order_id = self.shiprocket_order_id or "NoOrder"
        outcome = "OK" if self.is_success else "FAIL"
        return f"{order_id} | {self.trigger} | {outcome}"


class WhatsAppNotificationQueue(models.Model):
    STATUS_PENDING = "pending"
    STATUS_RETRYING = "retrying"
    STATUS_PROCESSING = "processing"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RETRYING, "Retrying"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
    ]

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="whatsapp_notification_queue_jobs",
        default=get_default_tenant_pk,
    )
    order = models.ForeignKey(
        ShiprocketOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_queue_jobs",
    )
    shiprocket_order_id = models.CharField(max_length=64, blank=True)
    trigger = models.CharField(max_length=32, choices=WhatsAppNotificationLog.TRIGGER_CHOICES)
    previous_status = models.CharField(max_length=32, blank=True)
    current_status = models.CharField(max_length=32, blank=True)
    phone_number = models.CharField(max_length=32, blank=True)
    mode = models.CharField(max_length=32, blank=True)
    template_name = models.CharField(max_length=160, blank=True)
    template_id = models.CharField(max_length=128, blank=True)
    idempotency_key = models.CharField(max_length=64, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    result_payload = models.JSONField(default=dict, blank=True)
    initiated_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "next_retry_at", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "idempotency_key"],
                condition=(
                    models.Q(status__in=["pending", "retrying", "processing"])
                    & ~models.Q(idempotency_key="")
                ),
                name="uniq_active_whatsapp_queue_idempotency",
            )
        ]

    def __str__(self):
        order_id = self.shiprocket_order_id or "NoOrder"
        return f"{order_id} | {self.trigger} | {self.status}"
