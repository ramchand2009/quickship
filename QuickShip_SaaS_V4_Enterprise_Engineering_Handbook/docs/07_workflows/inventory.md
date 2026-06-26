# Inventory

Inventory is local and product-based.

## Product Fields

Products store name, category, SKU, barcode, external product id, image URL, description, actual cost, regular price, sale price, stock quantity, reorder level, and active flag.

## Movement Types

- Manual add.
- Manual remove.
- Manual set.
- Free/sample/complimentary special issue.
- Order accepted deduction.
- Order cancelled restore.

All movements write `StockMovement` with before/after quantities and an optional idempotent reference key.

## Automatic Stock

When an order enters `order_accepted`, stock is deducted once per matched product using reference key `order:<order_pk>:accepted:<product_pk>`. When an order is cancelled, stock is restored only if an accepted deduction exists.

Low-stock and no-stock products appear on dashboard lists.
