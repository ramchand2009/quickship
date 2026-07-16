import json
import re

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from .models import (
    BusinessExpense,
    ContactMessage,
    ExpensePerson,
    Product,
    ProductCategory,
    SenderAddress,
    ShiprocketOrder,
    StockMovement,
    Tenant,
    TenantMembership,
    TenantWooCommerceMappingRule,
    WhatsAppSettings,
    WhatsAppStatusTemplateConfig,
    WooCommerceSettings,
)
from .product_text import clean_product_description
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


class BusinessExpenseForm(forms.ModelForm):
    class Meta:
        model = BusinessExpense
        fields = ["expense_person", "item_name", "quantity", "unit_price", "remark"]
        labels = {
            "expense_person": "Expense Done By",
            "item_name": "Purchased Item",
            "quantity": "Qty",
            "unit_price": "Price",
            "remark": "Remark",
        }
        widgets = {
            "remark": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["expense_person"].queryset = ExpensePerson.objects.filter(is_active=True).order_by("name")
        self.fields["expense_person"].required = False
        self.fields["expense_person"].empty_label = "Select person"
        self.fields["expense_person"].widget.attrs["class"] = "form-select form-control"
        for name in ["item_name", "quantity", "unit_price", "remark"]:
            self.fields[name].widget.attrs["class"] = "form-control"
        self.fields["item_name"].widget.attrs["placeholder"] = "Example: Packing box bundle"
        self.fields["quantity"].widget.attrs["min"] = "1"
        self.fields["unit_price"].widget.attrs["min"] = "0"
        self.fields["unit_price"].widget.attrs["step"] = "0.01"
        self.fields["unit_price"].widget.attrs["placeholder"] = "0.00"
        self.fields["remark"].widget.attrs["placeholder"] = "Optional note about this purchase"


class ExpensePersonForm(forms.ModelForm):
    class Meta:
        model = ExpensePerson
        fields = ["name", "is_active"]
        labels = {
            "name": "Expense Person Name",
            "is_active": "Active",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs["class"] = "form-control"
        self.fields["name"].widget.attrs["placeholder"] = "Example: Arun"
        self.fields["is_active"].widget.attrs["class"] = "form-check-input"

    def clean_name(self):
        name = str(self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Enter an expense person name.")
        queryset = ExpensePerson.objects.filter(name__iexact=name)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError("This expense person already exists.")
        return name


class SignUpForm(UserCreationForm):
    tenant_name = forms.CharField(
        label="Business Name",
        max_length=160,
        help_text="This creates your vendor workspace.",
    )
    email = forms.EmailField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("tenant_name", "username", "email")

    def clean_tenant_name(self):
        value = str(self.cleaned_data.get("tenant_name") or "").strip()
        if not value:
            raise forms.ValidationError("Enter your business name.")
        slug = slugify(value)
        if not slug:
            raise forms.ValidationError("Enter a business name with letters or numbers.")
        if Tenant.objects.filter(slug=slug).exists():
            raise forms.ValidationError("A vendor workspace with this business name already exists.")
        return value

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if not commit:
            return user

        tenant_name = self.cleaned_data["tenant_name"]
        tenant_slug = slugify(tenant_name)
        with transaction.atomic():
            user.save()
            tenant = Tenant.objects.create(
                name=tenant_name,
                slug=tenant_slug,
                owner=user,
                contact_name=user.username,
                contact_email=user.email,
            )
            TenantMembership.objects.create(
                tenant=tenant,
                user=user,
                role=TenantMembership.ROLE_VENDOR_OWNER,
            )
            SenderAddress.objects.get_or_create(
                tenant=tenant,
                defaults={
                    "name": tenant.name,
                    "email": tenant.contact_email,
                    "phone": tenant.contact_phone,
                    "country": "India",
                },
            )
            WooCommerceSettings.objects.get_or_create(tenant=tenant)
            WhatsAppSettings.objects.get_or_create(
                tenant=tenant,
                defaults={"test_message_text": f"Hi from {tenant.name} test message."},
            )
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
        shipping = getattr(self.instance, "shipping_address", {}) or {}
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
        self.fields["manual_customer_phone"].widget.attrs.update(
            {
                "placeholder": shipping.get("phone") or "Customer mobile number",
                "inputmode": "tel",
                "autocomplete": "tel",
            }
        )
        self.fields["manual_customer_alternate_phone"].widget.attrs.update(
            {
                "placeholder": shipping.get("alternate_phone") or "Alternate mobile number",
                "inputmode": "tel",
                "autocomplete": "tel",
            }
        )
        self.fields["manual_shipping_address_1"].widget.attrs["placeholder"] = (
            shipping.get("address_1") or "House / street / area"
        )
        self.fields["manual_shipping_address_2"].widget.attrs["placeholder"] = (
            shipping.get("address_2") or "Landmark / apartment / locality"
        )
        self.fields["manual_shipping_city"].widget.attrs["placeholder"] = shipping.get("city") or "City"
        self.fields["manual_shipping_state"].widget.attrs["placeholder"] = shipping.get("state") or "State"
        self.fields["manual_shipping_country"].widget.attrs["placeholder"] = shipping.get("country") or "Country"
        self.fields["manual_shipping_pincode"].widget.attrs.update(
            {
                "placeholder": shipping.get("pincode") or "Pincode",
                "inputmode": "numeric",
                "autocomplete": "postal-code",
            }
        )


class ShiprocketOrderStatusForm(forms.ModelForm):
    courier_name = forms.CharField(required=False)

    class Meta:
        model = ShiprocketOrder
        fields = [
            "local_status",
            "manual_customer_phone",
            "courier_name",
            "tracking_number",
            "shipping_base_amount",
            "cancellation_reason",
            "cancellation_note",
        ]
        labels = {
            "local_status": "Order Status",
            "manual_customer_phone": "Customer Mobile",
            "courier_name": "Courier Partner",
            "tracking_number": "Tracking Number",
            "shipping_base_amount": "Shipping Cost",
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
        self.fields["courier_name"].widget.attrs["class"] = "form-control form-control-sm"
        self.fields["courier_name"].widget.attrs["placeholder"] = "Courier partner"
        self.fields["courier_name"].widget.attrs["style"] = "min-width: 190px;"
        self.fields["courier_name"].widget.attrs["title"] = "Courier Partner"
        if self.instance and self.instance.pk and not self.is_bound:
            self.fields["courier_name"].initial = self.instance.courier_name
        self.fields["tracking_number"].widget.attrs["class"] = "form-control form-control-sm"
        self.fields["tracking_number"].widget.attrs["placeholder"] = "Type tracking number manually (AA123456789AA)"
        self.fields["tracking_number"].widget.attrs["style"] = "min-width: 190px;"
        self.fields["tracking_number"].widget.attrs["title"] = "Tracking Number"
        self.fields["tracking_number"].widget.attrs["minlength"] = "13"
        self.fields["tracking_number"].widget.attrs["maxlength"] = "13"
        self.fields["tracking_number"].widget.attrs["autocomplete"] = "off"
        self.fields["tracking_number"].widget.attrs["autocapitalize"] = "characters"
        self.fields["tracking_number"].widget.attrs["spellcheck"] = "false"
        self.fields["shipping_base_amount"].required = False
        self.fields["shipping_base_amount"].widget.attrs["class"] = "form-control form-control-sm"
        self.fields["shipping_base_amount"].widget.attrs["placeholder"] = "Base shipping cost"
        self.fields["shipping_base_amount"].widget.attrs["style"] = "min-width: 150px;"
        self.fields["shipping_base_amount"].widget.attrs["title"] = "Shipping Cost Before Tax"
        self.fields["shipping_base_amount"].widget.attrs["inputmode"] = "decimal"
        self.fields["shipping_base_amount"].widget.attrs["min"] = "0"
        self.fields["shipping_base_amount"].widget.attrs["step"] = "0.01"
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
        selected_courier_name = (cleaned_data.get("courier_name") or "").strip()
        selected_tracking_number = (cleaned_data.get("tracking_number") or "").strip().upper()
        selected_shipping_base_amount = cleaned_data.get("shipping_base_amount")
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
            candidate_phone = (
                selected_manual_customer_phone
                or self.instance.resolved_customer_phone
                or ""
            )
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
            if not selected_courier_name:
                self.add_error("courier_name", "Select courier partner before moving to shipped.")
            else:
                cleaned_data["courier_name"] = selected_courier_name
            if not selected_tracking_number:
                self.add_error("tracking_number", "Enter tracking number before moving to shipped.")
            elif not re.fullmatch(r"[A-Z]{2}\d{9}[A-Z]{2}", selected_tracking_number):
                self.add_error("tracking_number", "Tracking number must match format AA123456789AA.")
            else:
                cleaned_data["tracking_number"] = selected_tracking_number
            if selected_shipping_base_amount is None:
                self.add_error("shipping_base_amount", "Enter shipping cost before moving to shipped.")
            elif selected_shipping_base_amount < 0:
                self.add_error("shipping_base_amount", "Shipping cost cannot be negative.")
        else:
            cleaned_data["courier_name"] = self.instance.courier_name
            cleaned_data["tracking_number"] = self.instance.tracking_number
            cleaned_data["shipping_base_amount"] = self.instance.shipping_base_amount

        if selected_status == ShiprocketOrder.STATUS_CANCELLED:
            if not selected_cancellation_reason:
                self.add_error("cancellation_reason", "Select a cancellation reason.")
        else:
            cleaned_data["cancellation_reason"] = ""
            cleaned_data["cancellation_note"] = ""

        return cleaned_data

    def save(self, commit=True):
        order = super().save(commit=False)
        order.courier_name = self.cleaned_data.get("courier_name") or ""
        if commit:
            order.save()
            self.save_m2m()
        return order


class ShiprocketOrderTrackingUpdateForm(forms.ModelForm):
    class Meta:
        model = ShiprocketOrder
        fields = ["tracking_number", "shipping_base_amount"]
        labels = {
            "tracking_number": "Tracking Number",
            "shipping_base_amount": "Shipping Cost",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tracking_number"].widget.attrs.update(
            {
                "class": "form-control",
                "placeholder": "Enter tracking number (AA123456789AA)",
                "maxlength": "13",
                "autocomplete": "off",
                "autocapitalize": "characters",
                "spellcheck": "false",
            }
        )
        self.fields["shipping_base_amount"].widget.attrs.update(
            {
                "class": "form-control",
                "placeholder": "Base shipping cost before 18% tax",
                "inputmode": "decimal",
                "min": "0",
                "step": "0.01",
            }
        )

    def clean_tracking_number(self):
        value = str(self.cleaned_data.get("tracking_number") or "").strip().upper()
        if not value:
            raise forms.ValidationError("Enter tracking number.")
        if len(value) != 13:
            raise forms.ValidationError("Tracking number must be exactly 13 characters.")
        return value

    def clean_shipping_base_amount(self):
        value = self.cleaned_data.get("shipping_base_amount")
        if value is None:
            raise forms.ValidationError("Enter shipping cost.")
        if value < 0:
            raise forms.ValidationError("Shipping cost cannot be negative.")
        return value


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "category_master",
            "sku",
            "smartbiz_product_id",
            "woocommerce_product_id",
            "woocommerce_variation_id",
            "barcode",
            "image_url",
            "stock_quantity",
            "reorder_level",
            "is_active",
        ]
        labels = {
            "category_master": "Category",
            "smartbiz_product_id": "WooCommerce Product/Variation ID",
            "woocommerce_product_id": "WooCommerce Product ID",
            "woocommerce_variation_id": "WooCommerce Variation ID",
            "image_url": "Product Image URL",
            "stock_quantity": "Opening Stock",
            "reorder_level": "Low Stock Threshold",
            "is_active": "Active",
        }
        widgets = {
            "woocommerce_product_id": forms.HiddenInput(),
            "woocommerce_variation_id": forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ["name", "sku", "smartbiz_product_id", "barcode", "image_url", "stock_quantity", "reorder_level"]:
            self.fields[name].widget.attrs["class"] = "form-control"
        self.fields["category_master"].queryset = ProductCategory.objects.filter(is_active=True).order_by("name")
        self.fields["category_master"].required = False
        self.fields["category_master"].empty_label = "Select category"
        self.fields["category_master"].widget.attrs["class"] = "form-select"
        self.fields["barcode"].widget.attrs["placeholder"] = "Scan or enter barcode"
        self.fields["sku"].widget.attrs["placeholder"] = "SKU-001"
        self.fields["smartbiz_product_id"].widget.attrs["placeholder"] = "WooCommerce product or variation ID"
        self.fields["image_url"].widget.attrs["placeholder"] = "https://your-store.com/path/product-image.jpg"
        self.fields["name"].widget.attrs["placeholder"] = "Product name"
        self.fields["is_active"].widget.attrs["class"] = "form-check-input"


class ProductDetailUpdateForm(ProductForm):
    class Meta(ProductForm.Meta):
        fields = [
            *ProductForm.Meta.fields,
            "description",
            "actual_price",
            "regular_price",
            "sale_price",
        ]
        labels = {
            **ProductForm.Meta.labels,
            "stock_quantity": "Stock quantity",
        }
        widgets = {
            **ProductForm.Meta.widgets,
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            self.initial["description"] = clean_product_description(self.instance.description)
        self.fields["description"].widget.attrs["class"] = "form-control"
        self.fields["description"].widget.attrs["placeholder"] = "Product description for WooCommerce"
        self.fields["actual_price"].widget.attrs["class"] = "form-control"
        self.fields["actual_price"].widget.attrs["placeholder"] = "320.00"
        self.fields["actual_price"].min_value = 0
        self.fields["regular_price"].widget.attrs["class"] = "form-control"
        self.fields["regular_price"].widget.attrs["placeholder"] = "300.00"
        self.fields["regular_price"].min_value = 0
        self.fields["sale_price"].widget.attrs["class"] = "form-control"
        self.fields["sale_price"].widget.attrs["placeholder"] = "240.00"
        self.fields["sale_price"].min_value = 0

    def clean_description(self):
        return clean_product_description(self.cleaned_data.get("description"))


class ProductCategoryForm(forms.ModelForm):
    class Meta:
        model = ProductCategory
        fields = ["name", "is_active"]
        labels = {
            "name": "Category Name",
            "is_active": "Active",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs["class"] = "form-control"
        self.fields["name"].widget.attrs["placeholder"] = "Soap"
        self.fields["is_active"].widget.attrs["class"] = "form-check-input"


class ProductBarcodePrintForm(forms.Form):
    manufacture_date = forms.DateField(
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    expiry_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    label_count = forms.IntegerField(
        min_value=1,
        initial=1,
        widget=forms.NumberInput(attrs={"inputmode": "numeric"}),
    )


class StockAdjustmentForm(forms.Form):
    ACTION_ADD = "add"
    ACTION_REMOVE = "remove"
    ACTION_SET = "set"
    ACTION_CHOICES = [
        (ACTION_ADD, "Add Stock"),
        (ACTION_REMOVE, "Remove Stock"),
        (ACTION_SET, "Set Exact Qty"),
    ]

    lookup_value = forms.CharField(label="Barcode, SKU, or WooCommerce ID", max_length=160)
    action = forms.ChoiceField(choices=ACTION_CHOICES)
    quantity = forms.IntegerField(min_value=0)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lookup_value"].widget.attrs["class"] = "form-control"
        self.fields["lookup_value"].widget.attrs["placeholder"] = "Scan barcode or type SKU / WooCommerce ID"
        self.fields["action"].widget.attrs["class"] = "form-select"
        self.fields["quantity"].widget.attrs["class"] = "form-control"
        self.fields["notes"].widget.attrs["class"] = "form-control"

    def clean_lookup_value(self):
        value = str(self.cleaned_data.get("lookup_value") or "").strip()
        if not value:
            raise forms.ValidationError("Scan or enter a barcode, SKU, or WooCommerce ID.")
        return value


class BulkSmartbizMappingForm(forms.Form):
    mapping_text = forms.CharField(
        label="Bulk WooCommerce ID Mapping",
        widget=forms.Textarea(attrs={"rows": 8}),
        help_text="Paste one mapping per line in SKU,WooCommerce ID format.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["mapping_text"].widget.attrs["class"] = "form-control"
        self.fields["mapping_text"].widget.attrs["placeholder"] = (
            "EN-SERUM-1,123\n"
            "MO-SOAP-1,456"
        )

    def clean_mapping_text(self):
        raw_value = str(self.cleaned_data.get("mapping_text") or "").strip()
        if not raw_value:
            raise forms.ValidationError("Paste at least one SKU to WooCommerce ID mapping.")
        return raw_value

    def parse_rows(self):
        raw_value = self.cleaned_data.get("mapping_text") or ""
        rows = []
        for index, raw_line in enumerate(str(raw_value).splitlines(), start=1):
            line = str(raw_line or "").strip()
            if not line:
                continue
            if "\t" in line:
                parts = [part.strip() for part in line.split("\t") if str(part).strip()]
            else:
                parts = [part.strip() for part in line.split(",") if str(part).strip()]
            if len(parts) < 2:
                raise forms.ValidationError(
                    f"Line {index} is invalid. Use SKU,WooCommerce ID or tab-separated values."
                )
            rows.append({"sku": parts[0], "smartbiz_product_id": parts[1]})
        if not rows:
            raise forms.ValidationError("Paste at least one valid mapping row.")
        return rows


class WhatsAppApiSettingsForm(forms.ModelForm):
    class Meta:
        model = WhatsAppSettings
        fields = ["enabled", "api_base_url", "api_key", "account_id", "account_name"]
        labels = {
            "enabled": "Enable WhatsApp Updates",
            "api_base_url": "API Link",
            "api_key": "API Key / Access Token",
            "account_id": "Account / Phone Number ID",
            "account_name": "Account Name",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["enabled"].widget.attrs["class"] = "form-check-input"
        self.fields["api_base_url"].widget.attrs["class"] = "form-control"
        self.fields["api_base_url"].widget.attrs["placeholder"] = "https://wa-api.cloud"
        self.fields["api_key"].widget = forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Paste Libromi access token",
                "spellcheck": "false",
                "autocomplete": "off",
            }
        )
        self.fields["account_id"].widget.attrs["class"] = "form-control"
        self.fields["account_id"].widget.attrs["placeholder"] = "Optional phone number ID / account ID"
        self.fields["account_name"].widget.attrs["class"] = "form-control"
        self.fields["account_name"].widget.attrs["placeholder"] = "Optional account name"


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
        known_templates = [choice[0] for choice in (template_choices or []) if choice and choice[0]]
        self.fields["test_template_name"].widget = forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Example: optin_templet",
                "list": "whatsapp-template-name-options",
            },
        )
        self.fields["test_template_name"].help_text = (
            "Enter the exact approved Libromi template name."
        )
        self.template_name_options = known_templates
        self.fields["test_template_params"].widget = forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": '{"name":"Mathukai","order_id":"SR123"}',
            }
        )


class WooCommerceSettingsForm(forms.ModelForm):
    class Meta:
        model = WooCommerceSettings
        fields = ["store_url", "consumer_key", "consumer_secret", "webhook_secret", "import_statuses", "status_map"]
        labels = {
            "store_url": "Store URL",
            "consumer_key": "Consumer Key",
            "consumer_secret": "Consumer Secret",
            "webhook_secret": "Webhook Secret",
            "import_statuses": "Import Statuses",
            "status_map": "Status Map JSON",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["store_url"].widget.attrs.update(
            {"class": "form-control", "placeholder": "https://your-store.com"}
        )
        self.fields["consumer_key"].widget.attrs.update(
            {"class": "form-control", "placeholder": "ck_xxxxxxxxxxxxxxxxx"}
        )
        self.fields["consumer_secret"].widget = forms.PasswordInput(
            render_value=True,
            attrs={"class": "form-control", "placeholder": "cs_xxxxxxxxxxxxxxxxx"},
        )
        self.fields["webhook_secret"].widget = forms.PasswordInput(
            render_value=True,
            attrs={"class": "form-control", "placeholder": "Use the same secret in WooCommerce webhook settings"},
        )
        self.fields["import_statuses"].widget.attrs.update(
            {"class": "form-control", "placeholder": "pending,processing,on-hold"}
        )
        self.fields["status_map"].widget = forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": '{"order_accepted":"processing","shipped":"completed"}',
            }
        )

    def clean_store_url(self):
        value = str(self.cleaned_data.get("store_url") or "").strip().rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            raise forms.ValidationError("Enter a full URL starting with https:// or http://.")
        return value

    def clean_import_statuses(self):
        value = str(self.cleaned_data.get("import_statuses") or "").strip()
        statuses = [item.strip() for item in value.split(",") if item.strip()]
        if not statuses:
            raise forms.ValidationError("Enter at least one WooCommerce status to import.")
        return ",".join(statuses)

    def clean_status_map(self):
        value = str(self.cleaned_data.get("status_map") or "").strip()
        if not value:
            return ""
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"Enter valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise forms.ValidationError("Status map must be a JSON object.")
        return json.dumps(parsed, separators=(",", ":"))


class TenantWooCommerceMappingRuleForm(forms.ModelForm):
    class Meta:
        model = TenantWooCommerceMappingRule
        fields = ["match_type", "match_value", "is_active"]
        labels = {
            "match_type": "Type",
            "match_value": "Value",
            "is_active": "Active",
        }

    def __init__(self, *args, **kwargs):
        self.tenant = kwargs.pop("tenant", None)
        super().__init__(*args, **kwargs)
        self.fields["match_type"].widget.attrs["class"] = "form-select"
        self.fields["match_value"].widget.attrs["class"] = "form-control"
        self.fields["match_value"].widget.attrs["placeholder"] = "Category, tag, SKU prefix, or product ID"
        self.fields["is_active"].widget.attrs["class"] = "form-check-input"

    def clean_match_value(self):
        value = str(self.cleaned_data.get("match_value") or "").strip()
        if not value:
            raise forms.ValidationError("Enter a mapping value.")
        match_type = self.cleaned_data.get("match_type")
        if match_type == TenantWooCommerceMappingRule.MATCH_SKU_PREFIX:
            value = value.upper()
        if match_type == TenantWooCommerceMappingRule.MATCH_PRODUCT_ID and not value.isdigit():
            raise forms.ValidationError("WooCommerce product ID must contain only numbers.")
        return value

    def clean(self):
        cleaned_data = super().clean()
        tenant = self.tenant or getattr(self.instance, "tenant", None)
        match_type = cleaned_data.get("match_type")
        match_value = cleaned_data.get("match_value")
        if not tenant or not match_type or not match_value:
            return cleaned_data

        queryset = TenantWooCommerceMappingRule.objects.filter(
            tenant=tenant,
            match_type=match_type,
            match_value=match_value,
        )
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            self.add_error("match_value", "This mapping rule already exists for this tenant.")
        return cleaned_data


class WhatsAppStatusTemplateConfigForm(forms.ModelForm):
    template_param_mapping = forms.CharField(required=False)
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
        self.fields["template_id"].widget.attrs["placeholder"] = "Optional provider template ID"

        known_templates = [choice[0] for choice in (template_choices or []) if choice and choice[0]]
        self.fields["template_name"].widget = forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Example: optin_templet",
                "list": "whatsapp-template-name-options",
            },
        )
        self.fields["template_name"].help_text = "Enter the exact approved Libromi template name."
        self.template_name_options = known_templates
        self.fields["template_param_mapping"].required = False
        self.fields["template_param_mapping"].widget = forms.Textarea(
            attrs={
                "class": "form-control form-control-sm",
                "rows": 2,
                "placeholder": '{"1":"customer_name","2":"order_id"}',
            }
        )
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


class VendorProfileForm(forms.ModelForm):
    class Meta:
        model = Tenant
        fields = ["name", "contact_name", "contact_email", "contact_phone"]
        labels = {
            "name": "Business Name",
            "contact_name": "Contact Name",
            "contact_email": "Contact Email",
            "contact_phone": "Contact Phone",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"


class SpecialStockIssueForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        empty_label="Select product",
        label="Product",
    )
    issue_category = forms.ChoiceField(
        choices=StockMovement.ISSUE_CATEGORY_CHOICES,
        label="Issue Type",
    )
    quantity = forms.IntegerField(min_value=1, label="Quantity")
    issue_recipient = forms.CharField(max_length=160, label="Given To")
    notes = forms.CharField(
        required=False,
        label="Remark",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.order_by("category", "name", "sku")
        for field_name, field in self.fields.items():
            css_class = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            field.widget.attrs["class"] = css_class
        self.fields["issue_recipient"].widget.attrs["placeholder"] = "Given to whom"
        self.fields["notes"].widget.attrs["placeholder"] = "Reason or remark"
