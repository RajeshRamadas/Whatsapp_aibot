# Campaign API and delivery

T9.2a prepares durable jobs; the paired T9.2b worker is required for sending.
Apply migration `0028_campaigns`. Routes require workspace admin/owner credentials
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
