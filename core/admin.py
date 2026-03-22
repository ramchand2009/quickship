from django.contrib import admin

from .models import (
    ContactMessage,
    OrderActivityLog,
    Product,
    Project,
    SenderAddress,
    ShiprocketOrder,
    StockMovement,
    WhatsAppNotificationLog,
    WhatsAppNotificationQueue,
    WhatsAppSettings,
    WhatsAppStatusTemplateConfig,
    WhatsAppTemplate,
)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name", "description")


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "subject", "created_at")
    search_fields = ("name", "email", "subject", "message")
    readonly_fields = ("created_at",)


@admin.register(ShiprocketOrder)
class ShiprocketOrderAdmin(admin.ModelAdmin):
    list_display = (
        "shiprocket_order_id",
        "channel_order_id",
        "customer_name",
        "local_status",
        "status",
        "total",
        "order_date",
        "cancellation_reason",
        "label_print_count",
        "last_label_printed_at",
        "completed_at",
        "updated_at",
    )
    search_fields = (
        "shiprocket_order_id",
        "channel_order_id",
        "customer_name",
        "customer_email",
        "customer_phone",
    )
    list_filter = ("local_status", "status", "payment_method")
    readonly_fields = ("raw_payload", "created_at", "updated_at")


@admin.register(SenderAddress)
class SenderAddressAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "city", "state", "country", "updated_at")
    search_fields = ("name", "email", "phone", "city", "state", "country", "pincode")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "sku", "barcode", "stock_quantity", "reorder_level", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "sku", "barcode")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "product",
        "movement_type",
        "quantity_delta",
        "quantity_before",
        "quantity_after",
        "shiprocket_order_id",
        "triggered_by",
    )
    list_filter = ("movement_type",)
    search_fields = ("product__name", "product__sku", "shiprocket_order_id", "triggered_by", "reference_key", "notes")
    readonly_fields = ("created_at",)


@admin.register(WhatsAppSettings)
class WhatsAppSettingsAdmin(admin.ModelAdmin):
    list_display = ("enabled", "api_base_url", "test_phone_number", "updated_at")
    search_fields = ("api_base_url",)


@admin.register(WhatsAppTemplate)
class WhatsAppTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "language", "category", "status", "synced_at")
    search_fields = ("name", "language", "category", "status", "template_id")
    readonly_fields = ("raw_payload", "synced_at", "created_at")


@admin.register(WhatsAppStatusTemplateConfig)
class WhatsAppStatusTemplateConfigAdmin(admin.ModelAdmin):
    list_display = ("local_status", "enabled", "template_name", "template_id", "updated_at")
    list_filter = ("enabled", "local_status")
    search_fields = ("local_status", "template_name", "template_id")


@admin.register(WhatsAppNotificationLog)
class WhatsAppNotificationLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "shiprocket_order_id",
        "trigger",
        "delivery_status",
        "external_message_id",
        "current_status",
        "phone_number",
        "mode",
        "template_name",
        "idempotency_key",
        "is_success",
    )
    list_filter = ("trigger", "is_success", "current_status", "mode", "delivery_status")
    search_fields = (
        "shiprocket_order_id",
        "phone_number",
        "template_name",
        "external_message_id",
        "webhook_event_id",
        "error_message",
    )
    readonly_fields = ("request_payload", "response_payload", "created_at")


@admin.register(WhatsAppNotificationQueue)
class WhatsAppNotificationQueueAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "shiprocket_order_id",
        "trigger",
        "phone_number",
        "template_name",
        "status",
        "attempt_count",
        "max_attempts",
        "next_retry_at",
        "processed_at",
    )
    list_filter = ("status", "trigger", "current_status")
    search_fields = ("shiprocket_order_id", "phone_number", "template_name", "idempotency_key", "last_error", "initiated_by")
    readonly_fields = ("payload", "result_payload", "created_at", "updated_at", "locked_at")


@admin.register(OrderActivityLog)
class OrderActivityLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "shiprocket_order_id",
        "event_type",
        "title",
        "previous_status",
        "current_status",
        "is_success",
        "triggered_by",
    )
    list_filter = ("event_type", "is_success", "current_status")
    search_fields = ("shiprocket_order_id", "title", "description", "triggered_by")
    readonly_fields = ("metadata", "created_at")
