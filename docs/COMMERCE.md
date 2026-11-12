# Read-only commerce integrations (T10.6)

Owners/admins connect one Shopify or WooCommerce store per workspace. Tokens are
encrypted with the existing MASTER_KEY; APIs return configuration/status only.
Delete the connection before replacing credentials or changing store. Delete also
removes generated product knowledge. No store writes, refunds or cancellations
are supported. No order payloads, phone numbers or credentials are logged/stored.

* Shopify: permanent `https://your-store.myshopify.com` origin, custom app token
  with `read_orders` and `read_products`; write scopes are rejected at test time.
  Adapter uses GraphQL Admin API 2026-10. Shopify's normal order history and
  protected-customer-data access restrictions still apply.
* WooCommerce: public HTTPS origin, v3 consumer key/secret with **Read** permission.
  The API cannot introspect the caller's key permission; confirm Read when saving.
  Our adapter only uses GET regardless of key privileges. Core WooCommerce has no
  standard tracking field; tracking is empty unless a future adapter adds one.

`GET/POST /api/v1/commerce`, `POST /{id}/test`, `DELETE /{id}` are admin-only.
Test is the explicit external-IO exception; other configuration requests use DB only.
Customer lookup binds identity to the inbound WhatsApp conversation, compares the
requested phone to that identity, then compares the order's phone before releasing
selected status/items/fulfilment/tracking fields. Web identities and mismatched or
missing orders disclose no order details. There is no public agent lookup API or
OTP flow in this version. No provider keys are needed by automated tests.

Provider references:
- https://shopify.dev/docs/api/admin-graphql/2026-10/queries/orders
- https://shopify.dev/docs/apps/build/authentication-authorization/manage-access-scopes
- https://developer.woocommerce.com/docs/apis/rest-api/authentication
- https://developer.woocommerce.com/docs/apis/rest-api/v3/orders/

## Daily catalogue and bot access

A successful connection test schedules the first sync. Admins can queue one with
`POST /api/v1/commerce/{id}/sync`. The worker scans durable due rows every 30 seconds;
15-minute leases recover abandoned work. Full snapshots publish atomically into
Knowledge after provider fetch and embedding finish. Successful syncs repeat daily;
failures retry with bounded exponential delay and preserve the prior snapshot.
Disconnect deletes product knowledge and invalidates in-flight publication.

Limits: 1,000 products, 100 Shopify variants per product, fewer than 100 WooCommerce
variations per product, 2 MB catalogue text, bounded HTTPS responses and 180-second
fetch deadline. Oversized/partial catalogues fail explicitly rather than publishing
partial inventory. Descriptions are plain text capped at 4,000 characters. Product
chunks include prices, currency and stock, and carry their snapshot timestamp.
WooCommerce requires read access to the currency setting as well as orders/products.
Only published/active products are synced. Deletions disappear on the next full sync.

`lookup_order` is registered only for verified WhatsApp conversations. Its selected
results become immutable per-turn evidence for the independent grounding checker.
No order fields enter shared knowledge. Web customers must ask staff for order help.
Catalogue answers cite Product catalogue; stock is a dated snapshot, not a reservation.
Real store tests and live-model calibration require merchant credentials before launch.
