# Sequence Diagrams

## Accept Order

```text
Operator -> Django view: submit status=order_accepted
Django view -> ShiprocketOrderStatusForm: validate transition and phone
Django view -> ShiprocketOrder: save local_status
Django view -> core.stock: sync_stock_for_status_transition
core.stock -> Product/StockMovement: deduct stock with reference key
Django view -> core.woocommerce: update_order_status when source=woocommerce
Django view -> core.whatsapp_queue: enqueue status notification
Django view -> OrderActivityLog: write outcome
```

## Pack Order

```text
Operator -> Order detail: scan SKU/barcode values
Browser -> Django view: submit status=order_packed + scan payload
Django view -> core.stock: validate_packing_scans
core.stock -> Product: match order items to SKU/barcode/name
Django view -> ShiprocketOrder: set local_status and packed_at
Django view -> WhatsApp queue/activity: record update
```

## WooCommerce Sync

```text
Operator/command -> core.woocommerce.sync_orders
core.woocommerce -> WooCommerce API: GET orders
core.woocommerce -> ShiprocketOrder: update_or_create WC-* order
core.woocommerce -> Product: sync_products when invoked separately
```
