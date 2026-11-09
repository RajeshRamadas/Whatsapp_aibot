# Campaign API and delivery

T9.2a prepares durable jobs; T9.2b provides the recipient worker and feedback.
Deploy both and apply `0028_campaigns` plus `0029_campaign_portfolio`.
Routes require workspace admin/owner credentials
and use the principal's tenant. Foreign IDs return 404. Screens belong to T9.3.

1. `POST /api/v1/campaigns` with `name`, `segment_id`, approved `template_id`,
   `variables`, optional `unit_price` and `currency` (`INR` or `USD`). Variables use
   `{"field":"name"}`, `{"field":"external_user"}`, `{"field":"attribute","value":"city"}`
   or `{"field":"literal","value":"Hello"}`. Bind every numbered variable.
2. Review the returned preview, eligible/excluded counts and estimate. Audience IDs,
   template content and personalized values freeze at creation; profile/segment edits
   do not silently change the confirmed content. Consent and approval are rechecked.
3. `POST /campaigns/{id}/test` with `contact_id` and `own_number:true`. The caller
   attests this is their own number; the workspace contact must have opt-in. One test
   job is allowed per draft. This API does not verify phone ownership. Recreate a
   draft if its test permanently fails.
4. Once that test is accepted by Meta, `POST /campaigns/{id}/start` with
   `confirm:true` and optional future, timezone-aware `scheduled_at`. Omit the time
   to send now. Repeated confirmation does not create duplicate recipients.
5. `GET /campaigns` (limit/offset), `GET /campaigns/{id}` and
   `GET /campaigns/{id}/recipients` show progress and recipient outcomes.
   `POST /campaigns/{id}/cancel` stops future sends; in-flight sends cannot be recalled.

All paths after the first share `/api/v1`. Recipient limits are 1–200; campaign
limits are 1–100, with offset pagination. `CAMPAIGN_MAX_RECIPIENTS` defaults to
1,000 (maximum 10,000), counting eligible and excluded matches. CSV audiences first
use contact import and a saved segment.

Pricing is an **estimate**, never an actual charge from Meta. Supply a per-message
price in the chosen currency, considering country/category differences.
`estimated_max_cost` multiplies it by the audience; `estimated_billable_cost`
includes only receipts explicitly marked billable. Null means no price was supplied.
`pricing_unconfirmed` counts accepted messages with an unknown billing flag.
Test jobs are excluded. Meta bills the business directly; reconcile charges there.

## Worker safeguards and results

The arq scheduler dispatches individual durable recipient jobs. No Meta HTTP call
runs in an API handler or across a DB session. Pending jobs recover after Redis
outages. The worker rechecks suspension, opt-in and the exact approved template
before each send; changes can suppress or pause recipients after confirmation.

Every five minutes as needed, workers verify phone quality and the current
`whatsapp_business_manager_messaging_limit`, plus the WABA's `owner_business_info`.
Configure a supported Graph version and token with business-management access.
Unknown fields, tier or portfolio identity block sending. See
[Meta's current messaging limits](https://developers.facebook.com/documentation/business-messaging/whatsapp/messaging-limits).
The deprecated `messaging_limit_tier` field is not used.

`CAMPAIGN_SENDS_PER_SECOND` (default 1) is shared per phone across workers. A DB
advisory lock serializes rolling 24-hour portfolio reservations across phones and
workspaces; the cap is the smaller of verified tier and `CAMPAIGN_DAILY_LIMIT`
(default 250). Repeated sends to the same customer consume one unique-recipient
slot. Tests and failed/ambiguous attempts reserve capacity conservatively.
Other tools or manual sends outside this platform can also consume Meta's budget;
Meta remains authoritative and rate/limit rejections back off. Configure a lower
local budget when sharing a portfolio with other senders.

Signed quality/capability downgrade callbacks pause campaigns and publish
`campaign.updated` on the tenant's authenticated event stream; the report and
audit log retain the reason. Recovery never silently resumes a paused campaign:
confirm `/start` again after resolving the cause. Channels expose cached quality,
tier and verified portfolio identity. Template changes also pause campaigns.

Acknowledged transient HTTP/rate errors retry up to five attempts with backoff,
jitter and numeric Retry-After. Permanent errors fail immediately. Lost responses,
malformed success responses and expired two-minute sending leases become `unknown`
and are not automatically retried. Inspect these in recipient results. Meta does
not provide an idempotency guarantee for sends; in-flight sends cannot be recalled.

Signed receipts use phone/tenant identity and opaque recipient IDs, tolerate early
callbacks, deduplicate and retain the highest known delivery state. Results include
sent/delivered/read, failed, suppressed, unknown, replied and opt-outs. Replies use
explicit message context when supplied, otherwise the latest accepted campaign
to that contact within seven days. Inbound STOP also records an attributed opt-out.
Product milestone `campaign_sent` is recorded once per campaign after acceptance.
Reports aggregate counters in SQL and fetch only three previews per campaign.
No raw provider error text, message bodies or recipient numbers enter logs/events.
