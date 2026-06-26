# Returns

Dedicated return/RTO workflows are not implemented yet.

Current related behavior:

- Orders can move into `delivery_issue`.
- Delivery issue can transition to `delivered` or `out_for_delivery`.
- Cancelled orders restore stock if stock had been deducted during acceptance.
- Activity logs can record manual updates and status changes around delivery exceptions.

Future return support should add explicit return status, reason, received quantity, refund/payment handling, and stock disposition.
