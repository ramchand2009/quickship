import json

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.cache import cache

from .models import (
    ContactMessage,
    Product,
    SenderAddress,
    ShiprocketOrder,
    WhatsAppSettings,
    WhatsAppStatusTemplateConfig,
)
from .whatomate import ORDER_TEMPLATE_FIELD_CHOICES


class ContactForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    class Meta:
        model = ContactMessage
        fields = ["name", "email", "subject", "message"]
        widgets = {
            "message": forms.Textarea(attrs={"rows": 5}),
        }


class SignUpForm(UserCreationForm):
    email = forms.EmailField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user


class LoginForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        "too_many_attempts": "Too many failed login attempts. Please try again later.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def _get_client_ip(self):
        request = getattr(self, "request", None)
        if not request:
            return "unknown"
        forwarded_for = str(request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
        if forwarded_for:
            return forwarded_for.split(",")[0].strip() or "unknown"
        return str(request.META.get("REMOTE_ADDR") or "").strip() or "unknown"

    def _get_login_rate_limit_config(self):
        attempts = int(getattr(settings, "LOGIN_LOCKOUT_ATTEMPTS", 5) or 5)
        window_seconds = int(getattr(settings, "LOGIN_LOCKOUT_WINDOW_SECONDS", 900) or 900)
        lock_seconds = int(getattr(settings, "LOGIN_LOCKOUT_DURATION_SECONDS", 900) or 900)
        return max(1, attempts), max(60, window_seconds), max(60, lock_seconds)

    def _get_rate_limit_keys(self):
        username = str(self.data.get("username") or "").strip().lower()
        ip = self._get_client_ip()
        identity = f"{username}|{ip}" if username else ip
        safe_identity = "".join(char if char.isalnum() or char in {"-", "_", "|", "."} else "_" for char in identity)
        return f"login_fail:{safe_identity}", f"login_lock:{safe_identity}"

    def clean(self):
        attempts_limit, window_seconds, lock_seconds = self._get_login_rate_limit_config()
        if attempts_limit <= 0:
            return super().clean()

        fail_key, lock_key = self._get_rate_limit_keys()
        if cache.get(lock_key):
            raise forms.ValidationError(
                self.error_messages["too_many_attempts"],
                code="too_many_attempts",
            )

        try:
            cleaned_data = super().clean()
        except forms.ValidationError as exc:
            failures = int(cache.get(fail_key, 0) or 0) + 1
            cache.set(fail_key, failures, timeout=window_seconds)
            if failures >= attempts_limit:
                cache.set(lock_key, "1", timeout=lock_seconds)
                raise forms.ValidationError(
                    self.error_messages["too_many_attempts"],
                    code="too_many_attempts",
                ) from exc
            raise

        cache.delete(fail_key)
        cache.delete(lock_key)
        return cleaned_data


class ShiprocketOrderManualUpdateForm(forms.ModelForm):
    class Meta:
        model = ShiprocketOrder
        fields = [
            "manual_customer_name",
            "manual_customer_email",
            "manual_customer_phone",
            "manual_customer_alternate_phone",
            "manual_shipping_address_1",
            "manual_shipping_address_2",
            "manual_shipping_city",
            "manual_shipping_state",
            "manual_shipping_country",
            "manual_shipping_pincode",
        ]
        labels = {
            "manual_customer_name": "Customer Name",
            "manual_customer_email": "Customer Email",
            "manual_customer_phone": "Customer Mobile",
            "manual_customer_alternate_phone": "Alternate Mobile",
            "manual_shipping_address_1": "Address Line 1",
            "manual_shipping_address_2": "Address Line 2",
            "manual_shipping_city": "City",
            "manual_shipping_state": "State",
            "manual_shipping_country": "Country",
            "manual_shipping_pincode": "Pincode",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"


class ShiprocketOrderStatusForm(forms.ModelForm):
    class Meta:
        model = ShiprocketOrder
        fields = ["local_status", "manual_customer_phone", "tracking_number", "cancellation_reason", "cancellation_note"]
        labels = {
            "local_status": "Order Status",
            "manual_customer_phone": "Customer Mobile",
            "tracking_number": "Tracking Number",
            "cancellation_reason": "Cancel Reason",
            "cancellation_note": "Cancel Note",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["local_status"].widget.attrs["class"] = "form-select form-select-sm form-control form-control-sm"
        self.fields["manual_customer_phone"].widget.attrs["class"] = "form-control form-control-sm"
        self.fields["manual_customer_phone"].widget.attrs["placeholder"] = "Customer mobile number"
        self.fields["manual_customer_phone"].widget.attrs["style"] = "min-width: 190px;"
        self.fields["manual_customer_phone"].widget.attrs["title"] = "Customer Mobile"
        self.fields["manual_customer_phone"].widget.attrs["inputmode"] = "tel"
        self.fields["manual_customer_phone"].widget.attrs["minlength"] = "10"
        self.fields["tracking_number"].widget.attrs["class"] = "form-control form-control-sm"
        self.fields["tracking_number"].widget.attrs["placeholder"] = "Tracking number"
        self.fields["tracking_number"].widget.attrs["style"] = "min-width: 190px;"
        self.fields["tracking_number"].widget.attrs["title"] = "Tracking Number"
        self.fields["tracking_number"].widget.attrs["minlength"] = "13"
        self.fields["tracking_number"].widget.attrs["maxlength"] = "13"
        self.fields["cancellation_reason"].widget.attrs["class"] = "form-select form-select-sm form-control form-control-sm"
        self.fields["cancellation_reason"].widget.attrs["style"] = "min-width: 170px;"
        self.fields["cancellation_reason"].widget.attrs["title"] = "Cancel Reason"
        self.fields["cancellation_note"].widget.attrs["class"] = "form-control form-control-sm"
        self.fields["cancellation_note"].widget.attrs["placeholder"] = "Cancel note (optional)"
        self.fields["cancellation_note"].widget.attrs["style"] = "min-width: 190px;"

        if self.instance and self.instance.pk:
            current_status = self.instance.local_status
            label_lookup = dict(ShiprocketOrder.STATUS_CHOICES)
            next_statuses = ShiprocketOrder.ALLOWED_STATUS_TRANSITIONS.get(current_status, [])
            self.fields["local_status"].choices = [
                (status, label_lookup[status])
                for status in next_statuses
                if status in label_lookup
            ]

    def clean(self):
        cleaned_data = super().clean()
        selected_status = cleaned_data.get("local_status")
        selected_manual_customer_phone = (cleaned_data.get("manual_customer_phone") or "").strip()
        selected_tracking_number = (cleaned_data.get("tracking_number") or "").strip()
        selected_cancellation_reason = (cleaned_data.get("cancellation_reason") or "").strip()

        if not selected_status:
            return cleaned_data

        if not self.instance or not self.instance.pk:
            return cleaned_data

        current_status = self.instance.local_status
        if current_status in ShiprocketOrder.LOCKED_STATUSES:
            self.add_error("local_status", "This order status is locked and cannot be updated.")
            return cleaned_data

        allowed_statuses = ShiprocketOrder.ALLOWED_STATUS_TRANSITIONS.get(current_status, [])
        if selected_status not in allowed_statuses:
            self.add_error("local_status", "Invalid order status selected.")
            return cleaned_data

        if selected_status == ShiprocketOrder.STATUS_PACKED:
            missing_fields = self.instance.missing_fields_for_packing()
            if missing_fields:
                self.add_error(
                    "local_status",
                    f"Cannot move to Order Packed. Missing: {', '.join(missing_fields)}.",
                )

        if selected_status == ShiprocketOrder.STATUS_ACCEPTED and current_status == ShiprocketOrder.STATUS_NEW:
            candidate_phone = selected_manual_customer_phone or self.instance.manual_customer_phone or ""
            if not candidate_phone:
                self.add_error("manual_customer_phone", "Enter customer mobile before accepting the order.")
            else:
                digit_count = sum(char.isdigit() for char in candidate_phone)
                if digit_count < 10:
                    self.add_error("manual_customer_phone", "Enter a valid customer mobile number.")
                else:
                    cleaned_data["manual_customer_phone"] = candidate_phone
        else:
            cleaned_data["manual_customer_phone"] = self.instance.manual_customer_phone

        if selected_status == ShiprocketOrder.STATUS_SHIPPED:
            if not selected_tracking_number:
                self.add_error("tracking_number", "Enter tracking number before moving to shipped.")
            elif len(selected_tracking_number) != 13:
                self.add_error("tracking_number", "Tracking number must be exactly 13 characters.")
            else:
                cleaned_data["tracking_number"] = selected_tracking_number
        else:
            cleaned_data["tracking_number"] = self.instance.tracking_number

        if selected_status == ShiprocketOrder.STATUS_CANCELLED:
            if not selected_cancellation_reason:
                self.add_error("cancellation_reason", "Select a cancellation reason.")
        else:
            cleaned_data["cancellation_reason"] = ""
            cleaned_data["cancellation_note"] = ""

        return cleaned_data


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "sku", "barcode", "stock_quantity", "reorder_level", "is_active"]
        labels = {
            "stock_quantity": "Opening Stock",
            "reorder_level": "Low Stock Threshold",
            "is_active": "Active",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ["name", "sku", "barcode", "stock_quantity", "reorder_level"]:
            self.fields[name].widget.attrs["class"] = "form-control"
        self.fields["barcode"].widget.attrs["placeholder"] = "Scan or enter barcode"
        self.fields["sku"].widget.attrs["placeholder"] = "SKU-001"
        self.fields["name"].widget.attrs["placeholder"] = "Product name"
        self.fields["is_active"].widget.attrs["class"] = "form-check-input"


class StockAdjustmentForm(forms.Form):
    ACTION_ADD = "add"
    ACTION_REMOVE = "remove"
    ACTION_SET = "set"
    ACTION_CHOICES = [
        (ACTION_ADD, "Add Stock"),
        (ACTION_REMOVE, "Remove Stock"),
        (ACTION_SET, "Set Exact Qty"),
    ]

    lookup_value = forms.CharField(label="Barcode or SKU", max_length=120)
    action = forms.ChoiceField(choices=ACTION_CHOICES)
    quantity = forms.IntegerField(min_value=0)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lookup_value"].widget.attrs["class"] = "form-control"
        self.fields["lookup_value"].widget.attrs["placeholder"] = "Scan barcode or type SKU"
        self.fields["action"].widget.attrs["class"] = "form-select"
        self.fields["quantity"].widget.attrs["class"] = "form-control"
        self.fields["notes"].widget.attrs["class"] = "form-control"

    def clean_lookup_value(self):
        value = str(self.cleaned_data.get("lookup_value") or "").strip()
        if not value:
            raise forms.ValidationError("Scan or enter a barcode/SKU.")
        return value


class WhatsAppApiSettingsForm(forms.ModelForm):
    class Meta:
        model = WhatsAppSettings
        fields = ["enabled", "api_base_url", "api_key"]
        labels = {
            "enabled": "Enable WhatsApp Updates",
            "api_base_url": "API Link",
            "api_key": "API Key",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["enabled"].widget.attrs["class"] = "form-check-input"
        self.fields["api_base_url"].widget.attrs["class"] = "form-control"
        self.fields["api_base_url"].widget.attrs["placeholder"] = "http://127.0.0.1:8080"
        self.fields["api_key"].widget.attrs["class"] = "form-control"
        self.fields["api_key"].widget.attrs["placeholder"] = "whm_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


class WhatsAppMessageTestForm(forms.ModelForm):
    class Meta:
        model = WhatsAppSettings
        fields = [
            "test_phone_number",
            "test_message_text",
            "test_template_name",
            "test_template_params",
        ]
        labels = {
            "test_phone_number": "Test Phone Number",
            "test_message_text": "Test Message",
            "test_template_name": "Template",
            "test_template_params": "Template Placeholders (JSON)",
        }

    def __init__(self, *args, **kwargs):
        template_choices = kwargs.pop("template_choices", None)
        super().__init__(*args, **kwargs)
        self.fields["test_phone_number"].widget.attrs["class"] = "form-control"
        self.fields["test_phone_number"].widget.attrs["placeholder"] = "919876543210"
        self.fields["test_message_text"].widget = forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Hi from Mathukai test message.",
            }
        )
        choice_items = [("", "Select template")]
        if template_choices:
            choice_items.extend(template_choices)
        self.fields["test_template_name"].widget = forms.Select(
            choices=choice_items,
            attrs={"class": "form-select"},
        )
        self.fields["test_template_params"].widget = forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": '{"name":"Mathukai","order_id":"SR123"}',
            }
        )


class WhatsAppStatusTemplateConfigForm(forms.ModelForm):
    template_param_mapping = forms.CharField(required=False, widget=forms.HiddenInput())
    ORDER_FIELD_CHOICES = ORDER_TEMPLATE_FIELD_CHOICES
    ORDER_FIELD_KEYS = {key for key, _ in ORDER_FIELD_CHOICES}

    class Meta:
        model = WhatsAppStatusTemplateConfig
        fields = ["enabled", "template_name", "template_id", "template_param_mapping"]
        labels = {
            "enabled": "Enable",
            "template_name": "Template",
            "template_id": "Template ID",
            "template_param_mapping": "Template Param Mapping",
        }

    def __init__(self, *args, **kwargs):
        template_choices = kwargs.pop("template_choices", None)
        super().__init__(*args, **kwargs)
        self.fields["enabled"].widget.attrs["class"] = "form-check-input"
        self.fields["template_id"].widget.attrs["class"] = "form-control"
        self.fields["template_id"].widget.attrs["placeholder"] = "Optional template UUID override"

        choice_items = [("", "No template")]
        if template_choices:
            choice_items.extend(template_choices)
        self.fields["template_name"].widget = forms.Select(
            choices=choice_items,
            attrs={"class": "form-select"},
        )
        self.fields["template_param_mapping"].required = False
        if not self.is_bound:
            raw_mapping = self.initial.get("template_param_mapping")
            if isinstance(raw_mapping, dict):
                self.initial["template_param_mapping"] = json.dumps(raw_mapping)

    def clean(self):
        cleaned_data = super().clean()
        has_template = (cleaned_data.get("template_name") or "").strip() or (cleaned_data.get("template_id") or "").strip()
        if has_template and not cleaned_data.get("enabled"):
            cleaned_data["enabled"] = True

        if cleaned_data.get("enabled") and not (
            (cleaned_data.get("template_name") or "").strip() or (cleaned_data.get("template_id") or "").strip()
        ):
            self.add_error("template_name", "Select a template name or provide template ID when enabled.")
        return cleaned_data

    def clean_template_param_mapping(self):
        raw_mapping = self.cleaned_data.get("template_param_mapping")
        if isinstance(raw_mapping, dict):
            mapping = raw_mapping
        else:
            raw_text = str(raw_mapping or "").strip()
            if not raw_text:
                return {}
            try:
                mapping = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raise forms.ValidationError("Template parameter mapping must be valid JSON.") from exc

        if not isinstance(mapping, dict):
            raise forms.ValidationError("Template parameter mapping must be a JSON object.")

        sanitized = {}
        for token, field_key in mapping.items():
            token_text = str(token or "").strip()
            mapped_field = str(field_key or "").strip()
            if not token_text or not mapped_field:
                continue
            if mapped_field not in self.ORDER_FIELD_KEYS:
                raise forms.ValidationError(f"Unsupported mapped field: {mapped_field}")
            sanitized[token_text] = mapped_field
        return sanitized


class SenderAddressForm(forms.ModelForm):
    class Meta:
        model = SenderAddress
        fields = [
            "name",
            "email",
            "phone",
            "address_1",
            "address_2",
            "city",
            "state",
            "country",
            "pincode",
        ]
        labels = {
            "name": "Sender Name",
            "email": "Sender Email",
            "phone": "Sender Phone",
            "address_1": "Address Line 1",
            "address_2": "Address Line 2",
            "city": "City",
            "state": "State",
            "country": "Country",
            "pincode": "Pincode",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
