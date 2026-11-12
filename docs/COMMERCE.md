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
