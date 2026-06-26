# Tenant

Tenant foundation tests live in `core.tests.TenantFoundationTests`.

Current coverage includes:

- Default Mathukai tenant creation and model defaults.
- Tenant membership permissions and inactive tenant/membership blocking.
- Active tenant middleware behavior.
- Vendor/super-admin login routing.
- Vendor signup workspace creation.
- Vendor A cannot list, open, or update vendor B orders through vendor-facing order screens.
- Vendor A cannot list or adjust vendor B products through stock management.
- Vendor A cannot see vendor B expenses and new expenses are assigned to the active tenant.
- WooCommerce sync/webhook/product import behavior is tenant-scoped for non-default vendors.
- WhatsApp templates/status configs can share names across tenants.
- WhatsApp queue jobs/logs are assigned to the order tenant.
- WhatsApp queue processing can be limited to one tenant.
- Non-default vendor WhatsApp settings do not fall back to Mathukai/global environment credentials.

Remaining tenant test coverage should be added for labels/export edge cases, admin tenant management, sender address/label config, web push, non-WooCommerce integration workers, and the remaining tenant-aware uniqueness constraints.
