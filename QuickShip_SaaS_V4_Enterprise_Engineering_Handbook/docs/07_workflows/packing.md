# Packing

Packing is enforced mainly for accepted orders moving to packed.

## Requirements

- Order item must map to a local product.
- Product must have a local SKU; barcode is also accepted as a scan alias when present.
- Required address fields for packing are name, phone, address, and pincode.
- The scan payload must contain exactly the expected quantity for each product.

## Matching Order Items to Products

Product lookup tries SKU, channel SKU, channel product id, variation/id, SmartBiz id, and finally a unique exact product name match.

## Validation Failures

Packing is blocked for unmatched items, missing local SKU/barcode setup, unexpected scanned codes, over-scans, and missing scans. The UI shows remaining quantities and setup issues so operators can correct product mapping or scan again.
