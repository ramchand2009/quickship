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
- WooCommerce sync/webhook/product import behavior assigns tenants from shared-store mapping rules.
- WhatsApp templates/status configs can share names across tenants.
- WhatsApp queue jobs/logs are assigned to the order tenant.
- WhatsApp queue processing can be limited to one tenant.
- Non-default vendor WhatsApp jobs/logs stay tenant-owned while sends use shared Libromi settings.
- Super admin tenant list/detail pages render tenant summaries and block vendor users.
- Super admin tenant detail pages can create/edit WooCommerce mapping rules, reject duplicates, and block vendor mutation attempts.

Remaining tenant test coverage should be added for labels/export edge cases, admin tenant mutation actions, sender address/label config, web push, non-WooCommerce integration workers, and the remaining tenant-aware uniqueness constraints.
