# ERD

```text
User
  |-- WebPushSubscription

ShiprocketOrder
  |-- StockMovement
  |-- OrderActivityLog
  |-- WhatsAppNotificationQueue
  |-- WhatsAppNotificationLog

ProductCategory
  |-- Product

Product
  |-- StockMovement

ExpensePerson
  |-- BusinessExpense

WhatsAppTemplate
WhatsAppStatusTemplateConfig
WhatsAppSettings
WooCommerceSettings
SenderAddress
ContactMessage
Project
```

`ShiprocketOrder.order_items`, `shipping_address`, `billing_address`, and `raw_payload` are JSON fields rather than normalized child tables. This keeps imports flexible but means reporting and item-level queries are mostly implemented in Python helpers.
