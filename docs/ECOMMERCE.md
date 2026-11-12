# Shopify and WooCommerce

T10.6 is delivered in three parts: connections/catalogue, verified order lookup,
and administration/browser acceptance. This first part supplies EC-1 and EC-3.

## Merchant setup

Configure `MASTER_KEY` and run `alembic upgrade head` (migration 0049).
Create a Shopify custom-app token with **read_orders** and **read_products**.
Use the shop's `https://name.myshopify.com` origin. Tokens with write scopes are
rejected. Orders older than 60 days need Shopify's additional approved
`read_all_orders` scope. Protected customer-data access must also be approved.

For WooCommerce, enable pretty permalinks and create a REST API key with **Read**
permission. Supply the public HTTPS origin, consumer key and consumer secret.
The connector makes only GET requests and never sends keys in query strings.
WooCommerce does not expose the authenticated key's permission through these
read endpoints; the merchant must select Read when issuing it.

Credentials are encrypted per workspace and are never returned. A maximum of
five stores may be connected per workspace. To rotate a credential, disconnect
and add a new connection. Connections start paused and require a successful
test before enablement. Only owners/admins can manage them.

## API and catalogue lifecycle

`GET/POST /api/v1/stores` lists/adds stores. For a store ID, `POST /test`
checks credentials, `PATCH` with `{"enabled":true|false}` enables/pauses,
`POST /sync` durably requests refresh, and `DELETE` disconnects and removes its
knowledge. All routes use the authenticated workspace and existing CSRF rules.

The worker checks due stores every five minutes. An enabled store receives a
complete daily snapshot of product titles, descriptions, prices, currency and
stock, including up to 20 variants per product. The default/maximum catalogue
limit is 1,000 products (`ECOMMERCE_MAX_PRODUCTS` may lower it). Larger,
unsupported or incomplete catalogues fail safely rather than publish partial
product data. WooCommerce grouped products are currently unsupported.

Snapshots replace the previous chunks atomically after all requests and
embeddings finish. Leases prevent concurrent publication; pausing/deleting
invalidates in-flight work. Failed syncs retry hourly, while credential failures
disable the connection. Failed, paused or older-than-24-hour snapshots are
excluded from retrieval. Prices and stock are snapshots, not checkout promises.
Sync never imports orders, customer details or raw provider replies.

All outbound requests use the existing pinned public HTTPS transport, rejecting
private addresses, redirects, oversized/encoded responses and requests exceeding
eight seconds. Catalogue collection has a 120-second aggregate deadline.
No provider request or embedding call holds a database session.

## Provider references

- [Shopify GraphQL Admin API](https://shopify.dev/docs/api/admin-graphql/2026-10)
  (fixed 2026-10 queries; no mutations).
- [Shopify access scopes](https://shopify.dev/docs/api/usage/access-scopes).
- [WooCommerce REST v3](https://woocommerce.github.io/woocommerce-rest-api-docs/).
- [WooCommerce current currency](https://developer.woocommerce.com/docs/apis/rest-api/v3/data).

Automated acceptance uses fake stores. A merchant's live read-only credentials,
Shopify protected-data approval, and a staging refresh remain deployment checks.
