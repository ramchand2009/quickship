# Orders

Orders are managed through `ShiprocketOrder` regardless of source.

## Statuses

- `new_order`
- `order_accepted`
- `order_packed`
- `shipped`
- `delivery_issue`
- `out_for_delivery`
- `delivered`
- `completed`
- `order_cancelled`

## Allowed Transitions

- New -> accepted or cancelled.
- Accepted -> shipped or cancelled.
- Packed -> shipped or cancelled.
- Shipped -> completed or cancelled.
- Delivery issue -> delivered or out for delivery.
- Out for delivery -> delivered.
- Delivered -> completed.
- Completed/cancelled are locked.

## Key Rules

- Accepted orders require usable customer phone.
- Packing requires required address fields and valid scan verification.
- Shipping requires valid tracking and shipping base cost.
- Manual delivery edits are locked after shipped/late statuses.
- WooCommerce status sync runs after local status changes for WooCommerce orders.
- WhatsApp notifications are enqueued after status changes when configured.
