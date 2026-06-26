# Shipping

Shipping starts after an order is accepted or packed.

## Tracking and Cost

- Tracking number is stored in `ShiprocketOrder.tracking_number`.
- Shipping base cost is stored in `shipping_base_amount`.
- Tax is calculated as 18 percent through `shipping_tax_amount`.
- Total shipping cost is base plus tax through `shipping_total_amount`.
- Moving to shipped requires a valid tracking number and shipping cost.

## Labels

- Individual 4x6 label: `/orders/<pk>/label-4x6/`.
- Individual PDF label: `/orders/<pk>/label-4x6/pdf/`.
- Bulk labels are available for packed orders.
- Print tracking increments `label_print_count` and `last_label_printed_at`.
- Sender address comes from the latest `SenderAddress` row or default Mathukai Organic fallback.
