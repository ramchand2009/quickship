# Subscriptions

Subscription commerce is not implemented in the current codebase.

The only subscription-like model is `WebPushSubscription`, which stores browser push endpoints and cryptographic keys for PWA notifications. It is unrelated to customer billing subscriptions.

If recurring order subscriptions become a product requirement, they should be modeled separately from `ShiprocketOrder` and integrated with WooCommerce subscription data or a payment provider.
