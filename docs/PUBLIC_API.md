# Public API v1

API-1 / PAPI-1–PAPI-6. This additive API preserves the existing dashboard contracts.
Use `https://<dashboard-host>/api/backend/public`, or `/api/v1/public` on an API
server exposed directly. All examples below use `API_BASE` for that full prefix.

Owners/admins create expiring, granular keys in **Developer API**. Keep the raw key
in server-side secret settings; it appears once. Revoke it immediately if exposed.
Never put it in a website widget, browser bundle, URL or logs. Existing role-based
keys remain compatible. New explicit-scope keys cannot access dashboard routes.
Session cookies and `X-Admin-Key` do not authenticate this public API.

```sh
export API_BASE='https://marketingadda.com/api/backend/public'
# Supply API_KEY through your server's secret settings.
curl "$API_BASE/capabilities" -H "Authorization: Bearer $API_KEY"
```

`GET /capabilities` reports effective scopes and quota. Optional `X-Workspace-Id`
must match the key's workspace; request bodies never select a workspace. Keys can
combine knowledge, contacts, conversations, leads and evals `:read` / `:write`
permissions, plus `messages:send`. Reading messages requires `conversations:read`.
A write permission does not imply its corresponding read permission.

## Contract

- JSON requests and responses. Maximum request size defaults to 1 MiB.
- Every POST requires `Idempotency-Key` (1–128 characters), unique across the
  workspace. Retry the same path/body/key after an uncertain connection outcome.
  For 24 hours the same request returns its original status, body and resource
  Location, even if background work has since advanced. Read the resource to get
  current status. A changed path or body returns 409. Replays count toward quota.
- Business errors after request validation are also replayed. Authentication,
  scope and request-shape failures are rechecked; revoked/expired keys cannot
  replay. Unexpected server failures roll back the transaction and can be retried.
  The `Idempotency-Replayed` response header distinguishes a replay. After expiry,
  the same key represents a new intent; use a new key for each intentional action.
- Lists return `{items, next_cursor, limit}`. Request `?limit=30`, then pass the
  opaque `next_cursor` as `cursor` until it is null. Maximum limit is 100. Items
  sort by ID ascending, not creation time. Keep resource, workspace and filters
  unchanged. Pagination continues if the last returned record is deleted; new
  UUID records inserted behind a cursor may appear only on a new traversal.
- Quotas are shared by all keys in one tenant. A plan's integer
  `limits.api_requests_per_minute` sets the quota (1–6000); otherwise the default
  is 60. On 429 wait the `Retry-After` number of seconds. Redis outages return 503
  with `Retry-After`, without bypassing quotas. Repeated requests are still metered.
- Responses include `API-Version: 1`, `Cache-Control: no-store` and a request ID.
  Standard errors always have object-valued details:
  `{"error":{"code":"insufficient_scope","message":"…","details":{"required_scope":"contacts:read"}}}`.
  Use codes/statuses rather than matching human-readable messages. Validation
  errors are 422; absent or foreign records are 404; conflicts are 409.
- Breaking changes require a new version (`/api/v2`) and at least six months'
  notice. Additive fields/endpoints may appear in v1. Clients should ignore unknown
  response fields. Existing dashboard API representations remain unchanged.

## Resources

Paths below are relative to `API_BASE`. All listed collections support cursor
pagination; individual GETs take the resource ID. Message IDs are positive integers;
other resource IDs are UUIDs. Leads use their conversation ID.

| Resource | Read | Write | Required permission |
|---|---|---|---|
| Sources | GET `/sources`, `/sources/{id}` | POST `/sources`, DELETE `/sources/{id}` | `knowledge:read` / `knowledge:write` |
| Q&A | GET `/qa-pairs`, `/qa-pairs/{id}` | POST `/qa-pairs`, `/qa-pairs/{id}/updates`; DELETE `/qa-pairs/{id}` | `knowledge:read` / `knowledge:write` |
| Operations | GET `/operations/{id}` | Created by knowledge writes | `knowledge:read` |
| Contacts | GET `/contacts`, `/contacts/{id}` | POST `/contacts`, PATCH `/contacts/{id}` | `contacts:read` / `contacts:write` |
| Conversations | GET `/conversations`, `/conversations/{id}` | POST `/conversations`, PATCH `/conversations/{id}` | `conversations:read` / `conversations:write` |
| Messages | GET `/messages`, `/messages/{id}`; optional `conversation_id` filter | POST `/messages` | `conversations:read` / `messages:send` |
| Approved templates | GET `/templates` | Synchronize through dashboard channel settings | `messages:send` |
| Leads | GET `/leads`, `/leads/{id}` | POST `/leads` (capture/merge), PATCH `/leads/{id}` | `leads:read` / `leads:write` |
| Eval cases | GET `/eval-cases`, `/eval-cases/{id}` | POST `/eval-cases`, PUT/DELETE `/eval-cases/{id}` | `evals:read` / `evals:write` |
| Eval runs | GET `/eval-runs`, `/eval-runs/{id}` | POST `/eval-runs`, `/eval-runs/{id}/cancel` | `evals:read` / `evals:write` |

OpenAPI describes every input/output: [generated schema](public-api.openapi.json).
Interactive reference: `API_BASE/docs`; downloadable schema: `API_BASE/openapi.json`.
When using the dashboard proxy, select its server in the interactive reference.

```sh
curl "$API_BASE/contacts" -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: crm-contact-001' \
  -d '{"channel":"web","external_user":"customer-001","name":"Demo customer"}'
# Use the returned contact ID to POST /conversations: {"contact_id":"<uuid>"}.
# Then use the returned conversation ID:
curl "$API_BASE/messages" -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: greeting-001' \
  -d '{"type":"text","conversation_id":"<uuid>","text":"Hello"}'
```

Contacts created here have no marketing consent; existing consent workflows apply.
Profiles can update name, language and bounded attributes. Conversation changes
set status/labels; `bot` hands back control, while other statuses pause the bot.
WhatsApp text needs a connected channel and an active 24-hour customer-service
window. Outside that window, send `type: template`, an approved `template_id`
and a `values` array; valid opt-in is required. Queued sends execute in the existing
worker and may later fail eligibility checks. Poll message delivery status; do not
send again merely because it is still queued or its delivery is uncertain.

```sh
curl "$API_BASE/sources" -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: policy-001' \
  -d '{"filename":"policy.md","content":"# Refunds\nRefunds within 30 days."}'
curl "$API_BASE/qa-pairs" -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: faq-001' \
  -d '{"question":"When can I return an item?","answer":"Within 30 days."}'
```

Knowledge writes return 202 with an operation ID and allocated resource ID. Poll
`/operations/{id}` until `complete` or `failed`; Q&A creation becomes readable on
completion. Source JSON accepts `.txt`/`.md`, up to 500,000 characters within the
body limit. For PDF/media/crawling, use the existing dashboard upload workflows.
Run arq workers: public operations use three attempts and safe error codes; no
provider or object-storage work happens in handlers. Pending updates conflict with
another update/delete. Finished payloads are cleared; operation metadata is kept
30 days. Replay records expire after 24 hours. Cursors require configured master
keys; retain previous master versions during key rotation.

Create an eval case with `question` and scoring expectations (`expected_keywords`,
`expected_source`, or `expect_no_answer: true`). POST `/eval-runs` with
`{"label":"Release check","confirm_cost":true,"case_ids":["<uuid>"]}`.
Omit `case_ids` to select all within the configured run limit. Evaluations use the
configured model and consume metered usage; one run may be active per workspace.
Poll the run for scores/results; POST its `/cancel` path with `{"confirm":true}`
to stop remaining cases. Inputs/context are frozen as in dashboard accuracy runs.
