from django.db import models


def normalize_sku(value):
    return str(value or "").strip().upper()


def normalize_barcode(value):
    return str(value or "").strip()


def normalize_channel_product_id(value):
    return str(value or "").strip()


class Project(models.Model):
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ContactMessage(models.Model):
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
    enabled = models.BooleanField(default=False)
    api_base_url = models.CharField(max_length=255, blank=True)
    api_key = models.CharField(max_length=255, blank=True)
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
        return "WhatsApp Settings"

    @classmethod
    def get_default(cls):
        settings_row = cls.objects.order_by("-updated_at", "-created_at").first()
        if settings_row:
            return settings_row
        return cls.objects.create(test_message_text="Hi from Mathukai test message.")


class WhatsAppTemplate(models.Model):
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
                fields=["name", "language"],
                name="uniq_whatsapp_template_name_language",
            )
        ]

    def __str__(self):
        if self.language:
            return f"{self.name} ({self.language})"
        return self.name


class WhatsAppStatusTemplateConfig(models.Model):
    local_status = models.CharField(max_length=32, unique=True)
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

    def __str__(self):
        label = dict(ShiprocketOrder.STATUS_CHOICES).get(self.local_status, self.local_status)
        template = self.template_name or self.template_id or "No template"
        return f"{label}: {template}"

    @classmethod
    def get_or_create_for_status(cls, local_status):
        return cls.objects.get_or_create(local_status=local_status)


class ShiprocketOrder(models.Model):
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
        STATUS_ACCEPTED: [STATUS_PACKED, STATUS_CANCELLED],
        STATUS_PACKED: [STATUS_SHIPPED, STATUS_CANCELLED],
        STATUS_SHIPPED: [STATUS_DELIVERY_ISSUE, STATUS_OUT_FOR_DELIVERY, STATUS_CANCELLED],
        STATUS_DELIVERY_ISSUE: [STATUS_OUT_FOR_DELIVERY, STATUS_CANCELLED],
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

    shiprocket_order_id = models.CharField(max_length=64, unique=True)
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
    shipped_at = models.DateTimeField(null=True, blank=True)
    out_for_delivery_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
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

    def __str__(self):
        return f"{self.shiprocket_order_id} - {self.customer_name or 'Unknown customer'}"

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
        shipping = self.shipping_address or {}
        return {
            "name": self.manual_customer_name or shipping.get("name") or self.customer_name,
            "email": self.manual_customer_email or shipping.get("email") or self.customer_email,
            "phone": self.manual_customer_phone or shipping.get("phone") or self.customer_phone,
            "alternate_phone": self.manual_customer_alternate_phone or shipping.get("alternate_phone") or "",
            "address_1": self.manual_shipping_address_1 or shipping.get("address_1") or "",
            "address_2": self.manual_shipping_address_2 or shipping.get("address_2") or "",
            "city": self.manual_shipping_city or shipping.get("city") or "",
            "state": self.manual_shipping_state or shipping.get("state") or "",
            "country": self.manual_shipping_country or shipping.get("country") or "",
            "pincode": self.manual_shipping_pincode or shipping.get("pincode") or "",
            "latitude": shipping.get("latitude"),
            "longitude": shipping.get("longitude"),
        }


class ProductCategory(models.Model):
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
        return self.stock_quantity <= int(self.reorder_level or 0)

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
        super().save(*args, **kwargs)


class StockMovement(models.Model):
    TYPE_MANUAL_ADD = "manual_add"
    TYPE_MANUAL_REMOVE = "manual_remove"
    TYPE_MANUAL_SET = "manual_set"
    TYPE_ORDER_ACCEPTED = "order_accepted"
    TYPE_ORDER_CANCELLED = "order_cancelled"
    TYPE_CHOICES = [
        (TYPE_MANUAL_ADD, "Manual Add"),
        (TYPE_MANUAL_REMOVE, "Manual Remove"),
        (TYPE_MANUAL_SET, "Manual Set"),
        (TYPE_ORDER_ACCEPTED, "Order Accepted Deduction"),
        (TYPE_ORDER_CANCELLED, "Order Cancelled Restore"),
    ]

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
    TRIGGER_TEST_MESSAGE = "test_message"
    TRIGGER_TEST_TEMPLATE = "test_template"
    TRIGGER_WEBHOOK_STATUS = "webhook_status"
    TRIGGER_CHOICES = [
        (TRIGGER_STATUS_CHANGE, "Status Change"),
        (TRIGGER_RESEND, "Resend"),
        (TRIGGER_TEST_MESSAGE, "Test Message"),
        (TRIGGER_TEST_TEMPLATE, "Test Template"),
        (TRIGGER_WEBHOOK_STATUS, "Webhook Status"),
    ]

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

    def __str__(self):
        order_id = self.shiprocket_order_id or "NoOrder"
        return f"{order_id} | {self.trigger} | {self.status}"
