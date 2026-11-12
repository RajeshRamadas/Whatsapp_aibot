# Read-only commerce integrations (T10.6)

Open **Knowledge → Store connections**. Owners/admins connect one Shopify or WooCommerce store per workspace. Tokens are
encrypted with the existing MASTER_KEY; APIs return configuration/status only.
Delete the connection before replacing credentials or changing store. Delete also
removes generated product knowledge. No store writes, refunds or cancellations
are supported. Credentials, codes and raw order responses are never logged. Order
results are not added to shared Knowledge; selected verified answers can be saved
in the private conversation transcript under the existing retention policy.
Pending identity proofs retain only an encrypted five-minute order snapshot.


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
missing orders disclose no order details. There is no public agent lookup API. Conversation-bound email verification is described below. No provider keys are needed by automated tests.

Shopify product pages contain five products so nested variant queries stay below
the requested-cost limit. Provider throttling leaves the old snapshot intact and
uses the durable retry schedule; it never publishes an incomplete catalogue.

Provider references:
- https://shopify.dev/docs/apps/build/apis/graphql-admin/rate-limits
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

`lookup_order` supports verified WhatsApp conversations and real queued website turns. Its selected
results become immutable per-turn evidence for the independent grounding checker.
No order fields enter shared knowledge. Website customers must complete email verification before any order data is shared.
Generated catalogues are managed only under Store connections, not document deletion.
Catalogue answers cite Product catalogue; stock is a dated snapshot, not a reservation.
Real store tests and live-model calibration require merchant credentials before launch.

## One-time order proof (EC-2)

A model-provided phone cannot authorize a lookup. If the verified WhatsApp sender
does not match, or the customer is using the website, a one-time code is sent to
the **email on the order** using the existing SES transactional outbox. The customer
cannot choose the recipient. Without configured `EMAIL_BACKEND=ses`, unverified
customers receive a generic response and can ask staff for help.

Reply literally with `ORDER <reference> <six-digit-code>` in the same conversation.
A code expires after five minutes or five incorrect attempts, is single-use, and
is rejected if the connection is removed or needs testing, or staff have taken
over. Codes bypass the model and are masked in saved messages and worker payloads.
The encrypted snapshot is cleared on success/lockout or the maintenance sweep;
expired records are deleted after one day. No recipient address, phone or order
fields are returned before successful proof. Unknown orders show the same code
step without sending email, so the prompt does not reveal order existence. Order facts supplied to the model
are selected, cited evidence and still pass independent grounding verification.

Limits: 30 lookups per workspace/minute, five per conversation/minute, and three
codes per order-email/five minutes. Replaying a source turn does not resend a code.
Phone formatting separators are ignored; country codes are never guessed.
Migration 0050 adds the tenant/conversation-bound short-lived proof records.
The browser fixture uses a fake mail outbox; live SES and merchant-account checks
remain deployment setup.
