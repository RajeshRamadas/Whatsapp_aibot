# Custom HTTP actions

Owners and admins manage actions at **Dashboard → Custom actions**. Actions let the
assistant query a business system or request a change from background WhatsApp and
website-widget turns. Preview chats, accuracy evaluations and legacy inline chat
routes never execute these actions. There is no production integration configured
by this feature; each workspace supplies and tests its own endpoint.

## Define and enable an action

1. Give the action a lowercase identifier and a precise description of when the bot
   should use it. Add up to 12 scalar inputs: text, integer, number or boolean,
   description, required flag and optional validation pattern.
2. Choose a fixed public HTTPS origin and URL template, such as
   `https://api.example.com/stock/{sku}`. Named substitutions in paths/queries are
   percent encoded. Body templates are JSON objects; a whole value like
   `"quantity": "{quantity}"` preserves the scalar type. Optional inputs still need
   values if referenced by a template. `{customer_phone}` is server supplied from
   the verified WhatsApp conversation, never from model arguments.
3. Select up to 12 response aliases and exact JSON paths, for example
   `stock: $.stock` or `status: $.orders[0].status`. Wildcards, scripts and the whole
   response root are unsupported. The model receives only these selected fields,
   marked **UNTRUSTED DATA**, and must ignore instructions inside them.
4. Select authentication: none, dedicated API-key header, Bearer, Basic, or OAuth
   client credentials. OAuth uses HTTP Basic client authentication with
   `grant_type=client_credentials`, optional scope and a Bearer token response.
   Tokens are fetched for each request. Static headers and all credentials are
   encrypted with the workspace key and are write-only. Re-enter them on edits.
   Do not put credentials in URLs, descriptions, inputs or request body templates.
5. Select the identity policy below. Personal data cannot use “no check”. GET is
   the default. POST/PUT/PATCH/DELETE require explicit permission to change data;
   customer confirmation defaults on and can be disabled by the administrator.
6. Save the paused action, open **Test action and call history**, and supply
   synthetic sample inputs as a JSON object. A changing test requires a separate
   explicit acknowledgement and can change the connected system. The test shows
   the redacted raw JSON response and the bot projection. A successful test of the
   current revision is required before enabling it. Editing pauses the action and
   invalidates the previous test.

The administrative API is `/api/v1/http-actions`: POST create, GET list, PUT
`/{id}` replace, PATCH `/{id}` with `{"enabled": true|false}`, DELETE `/{id}`,
POST `/{id}/test` with `values`, UUID `request_key`, optional `sample_phone` and
`confirm_changes`, and GET `/{id}/calls`. Each operation uses authenticated
workspace context and owner/admin permissions. Test responses are `no-store`;
list/history never return stored credentials or customer input/result payloads.

Example public read definition (send credentials through protected settings):

```json
{
  "name": "stock",
  "description": "Check public stock for a product code",
  "parameters": [{"name": "sku", "type": "string", "description": "Product code", "required": true, "pattern": "[A-Z0-9-]{1,20}"}],
  "method": "GET",
  "url_template": "https://api.example.com/stock/{sku}",
  "auth": {"kind": "none"},
  "outputs": {"stock": "$.stock"},
  "personal_data": false,
  "identity": "none"
}
```

## Verify customer identity and changes

- **Phone match** compares the chosen response field to the phone from the
  verified WhatsApp conversation. Widget users cannot claim a verified phone.
- **Email code** sends a six-digit code to the email found at the selected response
  path. The model/customer cannot choose the recipient. Transactional SES delivery
  must be configured (`EMAIL_BACKEND=ses`, `MASTER_KEY`, sender and SNS settings;
  see the email guide). No code or personal result is returned to the model before
  verification. The code lasts five minutes; five wrong attempts lock it. Delivery
  is limited to three challenges per recipient/workspace per five minutes.
- **Personal-data changes** need a separate read-only `verification_url` on the
  same origin. It must bind every URL input used by the write. Make it identify
  the exact record being changed. The connector verifies identity before mutation;
  after email verification it re-reads the lookup and checks the recipient again.
- **Customer confirmation** requires the literal message `ACTION <reference>`.
  Email verification requires `VERIFY <reference> <six-digit-code>`. Both are
  interpreted before the model and bound to the conversation, action revision and
  stored inputs; approvals expire after five minutes. Instructions in tool results
  or invented model arguments cannot confirm a call. English/Hindi approval notices
  are available; other languages fall back to English.

Admin tests inspect phone identity using an explicit sample phone. Email-code
admin tests show the raw response and `code_required` with no bot projection;
these tests do not send a code or prove live email delivery. A changing test can
exercise the mutation under the administrator's explicit acknowledgement.

## Delivery, failures and retention

All network operations in an execution—including OAuth and identity lookups—share
an eight-second budget. Every destination is validated and DNS-pinned to a public
address on HTTPS port 443. Redirects, private/mixed DNS destinations and compressed
responses are rejected. Each JSON response is capped at 100,000 bytes; selected
fields are capped at 16 KB. Validation patterns have a 50 ms execution deadline.

Calls are durable and replayed by source turn. A source-turn retry cannot repeat a
write, even if model arguments differ. Writes carry
`Idempotency-Key: http-action-<call-id>`; the receiver should honor it too. HTTP
failures do not trigger automatic mutation retries. A timeout, interrupted worker,
5xx response or mapping failure after a write may mean the mutation happened;
these calls are marked **unknown**. Check the connected system before requesting
another change. Failed customer calls hand off to the team; authentication failure
pauses the action. Pausing/editing invalidates pending execution. An in-flight
request may already have reached the receiver.

Call history contains state, timestamps (displayed in Asia/Calcutta), HTTP status,
duration and safe error codes, without customer payloads. Inputs and cached results
are encrypted; pending proofs last five minutes and finished payloads at most 15
minutes. The worker clears expired payloads/codes and removes call metadata after
30 days. Verification commands are masked from message/model history and finished
widget/inbound records. Approved selected data can appear in the normal customer
conversation and follows its existing retention policy. Authorized raw test results
can contain personal data; clear them after inspection.

Run the arq worker for live customer turns, email delivery and retention. Configure
`MASTER_KEY`, `HTTP_ACTION_LIMIT` (default 20 per workspace), and
`HTTP_ACTION_REQUESTS_PER_MINUTE` (default 60 shared requests per workspace).
Quotas require Redis and fail closed. Tests use fake receivers/providers only;
verify a workspace's live endpoint and email delivery in staging before enabling
personal-data actions.
