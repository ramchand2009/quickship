# Payments

There is no payment gateway integration in the current codebase.

Current payment-related behavior is operational:

- `ShiprocketOrder.payment_method` stores the external payment label/method.
- `ShiprocketOrder.payment_received_at` records manual payment receipt.
- Payment reminder flow is available for accepted/packed orders when payment has not been marked received.
- Payment reminder uses WhatsApp template `order_payment` with order id and amount parameters.
- The order UI exposes mark-payment-received and payment-reminder actions.

Future gateway integrations should add explicit transaction models rather than overloading `ShiprocketOrder`.
