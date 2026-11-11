# WhatsApp AI Chatbot

A multi-tenant chatbot that answers customer questions from your own documents, on **WhatsApp** and on your **website**. Each tenant picks its AI provider: **OpenAI (ChatGPT)**, **Anthropic (Claude)** or **Google Gemini**.

It is a **development starter**, not a production-complete platform: read [Production gaps](#production-gaps) before going live.

The plan for turning it into a product businesses pay for:

- [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md): what to build, for whom, and in which release
- [docs/DESIGN.md](docs/DESIGN.md): technical design and delivery plan
- [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md): every outside system it connects to, and the rules connectors follow
- [docs/UX_DESIGN.md](docs/UX_DESIGN.md): user experience, conversation and visual design requirements
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md): step-by-step tasks for building it, one pull request each, written so any AI coding tool (ChatGPT/Codex, Gemini, Claude, Cursor, Copilot) can work through them. [AGENTS.md](AGENTS.md) has the repo commands and rules

- Upload PDF, DOCX, TXT, Markdown, CSV, XLSX or JSON. Text is chunked, embedded and stored in PostgreSQL + pgvector.
- Answers are grounded in the closest chunks, with numbered citations, and say "I don't know" when the documents don't cover the question.
- WhatsApp Cloud API webhook with signature checks, duplicate filtering, background replies and handoff to a human agent.
- Embeddable website widget.

## Database migrations

The API refuses to start if the database is not at the current Alembic revision. Run migrations before starting the API:

```bash
alembic upgrade head
alembic check
```

For Docker Compose, migrations run automatically before Uvicorn. **Existing databases created before Alembic** already have tables: back up the database, verify its schema matches the baseline, and run `alembic stamp 0001_baseline` once instead of replaying the initial migration. Never stamp an unknown schema without checking it first. To generate later migrations use `alembic revision --autogenerate -m "description"` and review the generated operations before applying.

## Platform administration (T7.5)

After `alembic upgrade head`, sign in with a platform-admin user and open `/platform`.
The console lists workspaces, their plans, current-month AI messages, tokens and known costs in USD.
Unpriced calls are shown separately so incomplete cost totals are visible. Each list is paginated.
It also shows terminal ingestion, WhatsApp and widget jobs, and HTTP webhook failures.
Failure history excludes message bodies, phone numbers, filenames, credentials and raw exception text.

Provision the first administrator through a trusted database operator after creating a normal user:

```sql
UPDATE users SET is_platform_admin = true WHERE email = 'admin@your-domain.example';
```

Signup, the bootstrap token and workspace API keys cannot grant platform access. Admin API routes:

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/platform/me` | Check platform-admin session access |
| `GET /api/v1/platform/tenants` | Workspace plans, status and current-month usage/cost |
| `PUT /api/v1/platform/tenants/{id}/suspension` | Set `{"suspended": true}` or `false`; session CSRF required |
| `GET /api/v1/platform/failed-jobs` | Durable terminal failures from the three message/ingestion pipelines |
| `GET /api/v1/platform/webhook-failures` | Provider, HTTP status and time for failed webhook requests |

Lists accept `limit` (1–100) and `offset`; the tenant list also accepts a literal `search` string.
Suspension blocks existing workspace sessions, API keys, public chats and queued bot/agent replies.
It blocks login when all of the user's workspaces are suspended; users with another active workspace
retain access there. Platform administrators can still sign in to resume a workspace.
Suspension and resumption are audited and preserve the previous bot configuration.
Already running external calls cannot be cancelled; generated widget and WhatsApp bot replies are
checked again before saving/sending. Webhook receipt continues, including billing events.

## Analytics overview (T8.2)

Open `/dashboard/analytics` to review conversations, messages, bot share, handoff rate,
average first response and leads by day/channel. All workspace roles can read the report.
`GET /api/v1/analytics` accepts inclusive `from_date` / `to_date` (1–90 days; defaults to
the last 30 UTC days) and an optional `channel=web|whatsapp`. It uses the authenticated
workspace; the response contains aggregate numbers and milestone counts only.

Run `alembic upgrade head`, then backfill once with `python -m app.services.analytics`.
The existing arq worker rebuilds reports daily at **01:10 UTC**, in one transaction.
Re-running the job is safe; it revisits historical cohorts and removes stale rows after deletions.
The report shows its refresh time; new activity appears after the next rebuild.

- Conversations are grouped by their first persisted customer message's UTC date.
- Bot share is the share with a delivered bot reply and no agent reply or handoff.
  Handoff history survives handing a conversation back to the bot. The migration seeds
  older history from the existing handoff/status fields and agent messages.
- First response measures the first saved customer message to the first delivered bot/agent
  reply. The average is weighted by replied conversations. Notes, queued and failed sends
  are excluded; conversations awaiting replies show no response time. Existing persisted
  timestamps may omit processing time before the customer message was first saved.
- Messages and leads use their own stored UTC creation dates, independently of the cohort date.
- Product events record sign-up attempts/completion, workspace creation, the four onboarding
  steps, WhatsApp linking, first ready knowledge/test/customer conversation/lead, go-live and
  upgrades. Names are allowlisted and first milestones are deduplicated transactionally.
  Sign-up starts mean submitted requests and are anonymous global counts; sign-up completion
  is attributed to the user's first workspace. Workspace milestones include all channels.
  There are no arbitrary properties, message text, phone numbers, email addresses or credentials.
  `campaign_sent` is reserved for T9.2; no campaign events are fabricated before that feature exists.

## Contacts and segments (T9.1)

Campaign APIs (T9.2) support immutable previews, own-number tests, confirmation,
schedules and delivery reports. Apply `0028_campaigns` and `0029_campaign_portfolio`.
The arq worker sends individual recipients with current consent/approval checks,
per-phone rate limits and a verified, shared Meta portfolio budget. Quality drops
pause campaigns and publish workspace alerts. See [campaign API and delivery rules](docs/CAMPAIGNS.md).
Owners and admins can use `/dashboard/campaigns` (T9.3) to submit/sync templates,
import CSV contacts, manage consent and segments, preview personalized drafts, test
on their own number and confirm an immediate or scheduled send. Results update
live with a polling fallback and recipient drill-down; agents/viewers cannot use
this page. Schedules use the browser's displayed time zone. No new credentials or migration.

Agents can read `/api/v1/contacts` and `/api/v1/segments`; owners/admins can import
contact CSV files, record consent and manage saved dynamic segments. Apply migration
`0027_contacts_segments` first. CSV imports require international phone numbers and
never infer consent or reverse a recorded STOP. Default upload limits are 1 MiB and
1,000 rows (`CONTACT_IMPORT_MAX_BYTES` / `CONTACT_IMPORT_MAX_ROWS`).
See [the contact API and CSV format](docs/CONTACTS.md) for examples and consent rules.
Campaign sending and its UI are separate tasks T9.2 and T9.3.

## Transactional email (T8.4)

`EMAIL_BACKEND=ses` queues encrypted transactional emails for the existing arq worker,
using Amazon SES in Mumbai and `notifications@marketingadda.com` as the launch sender.
The default `console` backend only logs a delivery request. Both API and worker need
the email settings and the same `MASTER_KEY`; run migration `0026_email_delivery` first.
Emails include English HTML and plain text. Signed SNS bounce/complaint feedback at
`POST /webhooks/email` suppresses future deliveries. Failed emails appear in the private
platform console and metrics. See [email setup and staging verification](docs/EMAIL_DELIVERY.md)
for SES, sender DNS, permissions, retries and the outstanding live-delivery check.

## Observability (T8.3)

API and worker logs are JSON with request, tenant, conversation and job IDs. Requests return
`X-Request-Id`; only UUID-shaped incoming IDs are accepted. Logs retain event codes and source
locations, excluding formatted messages, exception values, URLs, request bodies and trace locals.
Opaque job IDs correlate API enqueue logs with worker attempts; cron IDs are converted to UUIDs.

Set `SENTRY_DSN` for API, workers and the Next.js server, and `NEXT_PUBLIC_SENTRY_DSN` at web
build time for browser errors. Empty DSNs disable reporting. Error reports retain code locations
and correlation IDs where available, while removing user data, bodies, headers, cookies, query
strings, breadcrumbs, arbitrary extras and attachments. Tracing, SDK logs and SDK metrics are disabled.
The browser DSN is a public routing key; never put a private Sentry auth token in a public variable.

Set a long random `METRICS_TOKEN` to enable `GET /metrics`. Scrapes use
`Authorization: Bearer <METRICS_TOKEN>`; workspace sessions and API keys cannot authorize them.
The private endpoint exposes global operational data, including opaque tenant IDs for daily costs
and message counts. Keep the token with the monitoring operator and restrict network access.

`deploy/prometheus.yml` reads its token from `/run/secrets/metrics_token` and loads
`deploy/alerts.yml`; mount these files into your Prometheus deployment and configure Alertmanager
delivery there. The rules cover scrape/Redis outages, queue age, missing WhatsApp webhooks during
business hours, WhatsApp/API-provider error rates, webhook errors and webhook p95 latency.
No monitoring service or alert destination is deployed automatically by this change.

HTTP, integration and job attempt counters/histograms are shared through Redis across API and
worker processes; last-success times are included. Ready arq/widget queue depth and age, terminal
failures, current UTC daily messages and known AI costs come from Redis/PostgreSQL. Unpriced AI
calls are reported separately. Redis failures preserve request/job outcomes and set the scrape's
`chatbot_redis_up` gauge to zero; process-local counters do not hide worker activity.

## Getting started

1. Copy `.env.example` to `.env`. Set a long random `ADMIN_BOOTSTRAP_TOKEN` and the API key of at least one provider (see [AI providers](#ai-providers)).
2. Run `docker compose up --build` (the API service applies Alembic migrations before starting).
3. Open `http://localhost:8000/docs` for the API documentation.
4. Create a tenant:

```bash
curl -X POST http://localhost:8000/api/v1/tenants \
  -H 'Content-Type: application/json' -H 'X-Bootstrap-Token: YOUR_BOOTSTRAP_TOKEN' \
  -d '{"name":"Demo Store","allowed_origins":["http://localhost:3000"]}'
```

5. Save the `admin_key` (shown only once) and the `public_key`, then upload a document:

```bash
curl -X POST http://localhost:8000/api/v1/sources \
  -H 'X-Admin-Key: YOUR_ADMIN_KEY' -F 'file=@sample.pdf'
```

6. Check ingestion with `GET /api/v1/sources`, then chat:

```bash
curl -X POST http://localhost:8000/api/v1/chat/YOUR_PUBLIC_KEY \
  -H 'Content-Type: application/json' -d '{"message":"What does the document say?"}'
```

7. Embed on a website (replace host and key):

```html
<script src="http://localhost:8000/widget.js" data-chatbot-key="YOUR_PUBLIC_KEY" defer></script>
```

## AI providers

| Provider | Key | Default chat model | Embeddings |
|---|---|---|---|
| `openai` | `OPENAI_API_KEY` | `gpt-4.1-mini` | `text-embedding-3-small` |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-opus-5-5` | none (Anthropic has no embeddings API) |
| `gemini` | `GEMINI_API_KEY` | `gemini-2.5-flash` | `gemini-embedding-001` |

**Chat** is chosen per tenant, so different customers can use different providers. `CHAT_PROVIDER` / `CHAT_MODEL` set the server default; a tenant overrides it with its admin key:

```bash
curl -X PATCH http://localhost:8000/api/v1/tenant -H 'X-Admin-Key: YOUR_ADMIN_KEY' \
  -H 'Content-Type: application/json' -d '{"chat_provider":"anthropic"}'
# or a specific model:  -d '{"chat_provider":"gemini","chat_model":"gemini-2.5-pro"}'
# back to the server default: -d '{"chat_provider":null}'
```

Changing the provider without a `chat_model` resets the model to that provider's default.

Claude requests use low effort, which suits short grounded answers, and opt in to Anthropic's server-side refusal fallback (`fallbacks: "default"`). If Claude still declines, the user gets a polite refusal message.

**Embeddings** are one setting for the whole deployment (`EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`), because all vectors in the index must come from the same model. So a Claude-only deployment still needs an OpenAI or Gemini key for embeddings. Switching the embedding model means recreating the `chunks` table and re-uploading sources.

## WhatsApp setup

1. In [Meta for Developers](https://developers.facebook.com/), create an app with the **WhatsApp** product and add a phone number.
2. Set in `.env`:
   - `WHATSAPP_ACCESS_TOKEN`: a permanent System User token with `whatsapp_business_messaging`.
   - `WHATSAPP_APP_SECRET`: *App settings → Basic → App secret*. Every webhook call is checked against it (`X-Hub-Signature-256`).
   - `WHATSAPP_VERIFY_TOKEN`: any random string you choose.
3. Expose the API over HTTPS (for local testing, a tunnel such as ngrok or cloudflared). Then under *WhatsApp → Configuration*, set the callback URL to `https://YOUR_HOST/webhooks/whatsapp` with the same verify token, and subscribe to the `messages` field.
4. Link the phone number to a tenant using its **Phone number ID** (from *WhatsApp → API Setup*):

```bash
curl -X PATCH http://localhost:8000/api/v1/tenant -H 'X-Admin-Key: YOUR_ADMIN_KEY' \
  -H 'Content-Type: application/json' -d '{"whatsapp_phone_number_id":"123456789012345"}'
```

How it behaves:

- Each WhatsApp user gets one ongoing conversation per tenant, so the bot remembers recent messages.
- Meta's retries are recognised by message ID and answered once. The webhook returns 200 straight away and replies from a background task.
- Text, quick-reply buttons and interactive list replies are answered. Other types (images, voice notes) get a "text only" reply.
- **Human handoff:** a user who sends one of `WHATSAPP_HANDOFF_KEYWORDS` (default `human`, `agent`) is told a person will reply, and the bot goes quiet for that conversation. Agents use the admin API:

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/conversations?handoff=true` | Conversations waiting for a human |
| `GET /api/v1/conversations/{id}/messages` | Transcript |
| `POST /api/v1/conversations/{id}/reply` | Send a WhatsApp message as the agent |
| `POST /api/v1/conversations/{id}/handoff?active=false` | Hand the conversation back to the bot |

WhatsApp only allows free-form replies within 24 hours of the user's last message. Reaching a user after that needs an approved template, which is not implemented.

## How answers stay accurate

1. **Structure-aware chunks.** Files are split along their structure: Markdown and Word headings, PDF pages, and table or spreadsheet rows. A chunk never spans two sections or pages, and a table row is never cut in half. Each row keeps its column names (`plan: Pro; price: 499`). Every chunk is embedded together with its document name and heading, and its citation shows them too: `policy.pdf > Refunds (page 3)`.
2. **Hybrid search.** Each question runs two searches: by meaning (pgvector embeddings) and by exact words (Postgres full-text search). The keyword search finds order numbers, product codes and names that embeddings often miss. The two result lists are merged with reciprocal rank fusion, and the top `RETRIEVAL_TOP_K` chunks go to the model.
3. **Follow-up rewriting.** In an ongoing conversation, a message like "and for shipping?" is first rewritten into a complete question using the conversation, then searched. The answer itself still sees the customer's own words. This costs one extra short model call per follow-up; set `QUERY_REWRITE=false` to turn it off.
4. **Stricter prompt.** The model must answer only from the retrieved text, say so when the text doesn't cover the question, and copy prices, dates and codes exactly.

### Measuring accuracy

Add test questions for a tenant, with what a correct answer must contain, then run them all:

```bash
curl -X POST http://localhost:8000/api/v1/evals -H 'X-Admin-Key: YOUR_ADMIN_KEY' \
  -H 'Content-Type: application/json' -d '{"cases": [
    {"question": "How long do I have to ask for a refund?", "expected_keywords": ["30 days"], "expected_source": "refunds.pdf"},
    {"question": "Do you sell cars?", "expect_no_answer": true}
  ]}'

curl -X POST http://localhost:8000/api/v1/evals/run -H 'X-Admin-Key: YOUR_ADMIN_KEY'
# HTTP 202: {"id": "RUN_ID", "state": "queued", "total": 2, ...}
# Poll /api/v1/evals/runs/RUN_ID for progress and per-question results.
```

Each case can check three things:

- `expected_source`: that file must be among the cited sources.
- `expected_keywords`: every keyword must appear in the answer (case-insensitive).
- `expect_no_answer`: the documents don't cover the question, so the bot must explicitly decline or signal uncertainty. An uncited invented answer does not pass.

Scoring is plain text matching, with no extra model call. A good set has 30–50 real customer questions, including a few the bot should decline. Re-run it after uploading documents or changing provider, model or retrieval settings, and compare `accuracy`. Each question may make billed embedding/model calls, and a large set can take a few minutes.

T9.12 stores immutable question/bot snapshots, model and retrieval settings, knowledge and case-set
fingerprints, timestamps, scores and per-question answers. `POST /evals/run` now returns HTTP 202
with a run ID instead of a synchronous report; clients must poll `GET /evals/runs/{id}`.
`GET /evals/runs?limit=30&offset=0` paginates history; `POST /evals/runs/{id}/cancel` with
`{"confirm":true}` stops further questions (an in-flight call can still be billed). Owners/admins
manage questions and start/cancel runs; workspace readers can inspect cases/history. Only one run
is active per workspace. Optional UUID `request_key` makes a start request idempotent.
`PUT /evals/{case_id}` edits a test; older results retain their original expectations.
`EVAL_MAX_CASES_PER_RUN` defaults to 100 (maximum 200). The arq worker checkpoints each completed
case, recovers stale claims after 15 minutes, and stops if knowledge/retrieval changes mid-run.
Recovery after a crash during a provider call may repeat that unfinished call. Provider failures
are stored as safe error codes and count as non-passing cases. Scores use literal keyword/source
checks and decline signals, not semantic grading. Compare like-for-like case sets/settings.
Open **Analytics → Accuracy runs** (`/dashboard/analytics/accuracy`) to manage/select questions,
approve paid calls, inspect per-question results, cancel remaining questions and browse history in
English or Hindi. A trend shows completed runs with the same saved test set on the current page.
Live-model validation requires configured provider credentials.

## API

| Route | Auth | Purpose |
|---|---|---|
| `GET /health` | none | Liveness |
| `POST /api/v1/tenants` | `X-Bootstrap-Token` | Create a tenant |
| `GET`, `PATCH /api/v1/tenant` | `X-Admin-Key` | View or change tenant settings (name, origins, provider, model, WhatsApp number) |
| `POST`, `GET /api/v1/sources`, `DELETE /api/v1/sources/{id}` | `X-Admin-Key` | Manage knowledge sources |
| `POST /api/v1/chat/{public_key}` | public key + allowed origin | Website chat |
| `/api/v1/conversations…` | `X-Admin-Key` | Transcripts and handoff (above) |
| `POST`, `GET /api/v1/evals`, `DELETE /api/v1/evals/{id}`, `POST /api/v1/evals/run` | `X-Admin-Key` | Test questions and accuracy runs (above) |
| `GET`, `POST /webhooks/whatsapp` | Meta verify token / signature | WhatsApp webhook |
| `GET /widget.js` | none | Website widget |

## Architecture

- `app/main.py`: FastAPI routes, tenant auth and CORS.
- `app/rag.py`: hybrid retrieval, follow-up rewriting, prompt and answer, shared by web and WhatsApp, plus the rate limiter. No database connection is held during LLM calls.
- `app/evaluate.py`: scores answers to a tenant's test questions.
- `app/llm.py`: OpenAI, Anthropic and Gemini chat clients, and OpenAI and Gemini embedders.
- `app/whatsapp.py`: webhook parsing, signature check, dedupe, replies and handoff.
- `app/ingest.py`: structure-aware parsing and chunking, and embedding. A failed ingest leaves no partial chunks.
- `app/db.py`: SQLAlchemy models. Tables are created at startup, with an HNSW cosine index on embeddings and a GIN full-text index.
- `widget/widget.js`: dependency-free website widget.

## Tests

The tests use a real PostgreSQL with pgvector, and fake LLM and WhatsApp clients, so they need no API keys:

```bash
docker run -d --name chatbot-test -p 5433:5432 -e POSTGRES_HOST_AUTH_METHOD=trust \
  -e POSTGRES_DB=chatbot_test pgvector/pgvector:pg16
pip install -r requirements.txt -r requirements-dev.txt
pytest -q   # DATABASE_URL defaults to postgresql+psycopg://postgres@127.0.0.1:5433/chatbot_test
```

The tests drop and recreate every table, so never point `DATABASE_URL` at real data when running them. CI runs the same suite on every push (`.github/workflows/ci.yml`).

## Upgrading from 0.1

The schema changed: new tenant, conversation, message and chunk columns (heading, page, full-text search), the `whatsapp_inbound` and `eval_cases` tables, and the search indexes. There are no migrations yet, so recreate the development database (`docker compose down -v`) and re-upload sources.

## Production gaps

- **Durable jobs:** ingestion and WhatsApp replies run as FastAPI background tasks, which are lost if the process restarts. Move them to a queue (Redis + RQ/Celery, or similar) with retries.
- **Rate limits** are in memory and per process, keyed by client IP for the website. Behind a reverse proxy, run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy IP>` so the real IP is used. Use Redis for several workers, and add per-tenant quotas and spending caps.
- **Migrations:** add Alembic before you have data worth keeping.
- **Security:** the public key and origin allowlist identify a tenant; they don't authenticate anyone. Add session ownership for web conversations, malware scanning of uploads, audit logs and secrets management. WhatsApp access tokens are global (one Meta app); per-tenant tokens would need encrypted storage.
- **WhatsApp:** message templates (for the 24-hour window), media handling (images, voice transcription), read receipts and typing indicators are not implemented.
- **Retrieval:** add OCR for scanned PDFs and images, a reranking model, and checking of answers against their cited text. Tune `RETRIEVAL_MAX_DISTANCE` for your embedding model, using the accuracy runs above. CSV/JSON/XLSX rows are searched as text, so exact totals and aggregates need a dedicated query engine.
- **Keyword search language:** `TEXT_SEARCH_CONFIG=english` (the default) handles English word forms (refund/refunds) but not other languages. For mixed-language content, use `simple`, which matches words exactly.
- **Operations:** monitoring, backups and a deployment strategy.

Do not deploy this starter to a public-facing production service without addressing these gaps.


### Workspace membership and permissions

After signing in, create a workspace with `POST /api/v1/workspaces` and an
`X-CSRF-Token` header matching the `chatbot_csrf` cookie. For workspace-scoped
requests send `X-Workspace-Id: <tenant_id>`; unsafe requests also require
`X-CSRF-Token`. Roles: owner (all), admin (settings, knowledge and members),
agent (inbox replies and handoff), viewer (read-only transcripts).
Use `POST /api/v1/invitations` (admin+) to email a seven-day invitation;
the invited user signs up and accepts it at `POST /api/v1/invitations/accept`.
Member management is available under `/api/v1/members`.

Existing `X-Admin-Key` credentials remain supported during the T1.5 API-key
migration. The legacy bootstrap tenant endpoint remains for existing integrations;
new user onboarding should use `/api/v1/workspaces`.


### Workspace API keys (T1.5)

Authenticated workspace owners/admins can `POST /api/v1/api-keys` with
`X-Workspace-Id`, a session cookie and `X-CSRF-Token`:

```json
{"name":"Read-only integration","scope":"viewer","expires_in_days":90}
```

The response shows the `sk_live_` secret **once**. Save it securely.
`GET /api/v1/api-keys` lists metadata without secrets, and
`DELETE /api/v1/api-keys/{id}` revokes a key immediately. Supported scopes
are `viewer`, `agent`, and `admin`; keys cannot manage other keys.
Call workspace APIs with `Authorization: Bearer sk_live_...` and optionally
`X-Workspace-Id` (if supplied, it must match the key's workspace).
Existing `X-Admin-Key` credentials remain accepted during migration and
return a `Warning` deprecation header. Run `alembic upgrade head` before
starting the server.


### Audit log (T1.6)

After `alembic upgrade head`, workspace owners and admins can request
`GET /api/v1/audit-log?limit=50&offset=0` with their session and
`X-Workspace-Id`. Limits range from 1 to 100. Events are restricted to the
selected workspace and contain action, target, actor, timestamp and sanitized
metadata; API secrets and message contents are never stored. The log records
successful logins for existing workspace members, failed logins for known
accounts with memberships, invitations, membership changes, API-key creation
and revocation, knowledge deletion, and workspace settings changes. Failed
logins for unknown email addresses have no tenant to attribute and are not
written to this tenant-scoped log.


### Object storage uploads (T1.7)

Set `STORAGE_BACKEND=local` (default) or `s3`. S3-compatible storage uses
`STORAGE_BUCKET`, `STORAGE_REGION`, optional `STORAGE_ENDPOINT_URL`, and
standard AWS credentials. Compose includes a MinIO service on port 9000; create
the bucket before uploading. For MinIO inside Compose use
`STORAGE_ENDPOINT_URL=http://minio:9000` and set AWS credentials to the
MinIO root credentials. Existing multipart `POST /api/v1/sources` still works.

For two-step uploads, an admin sends `POST /api/v1/sources/uploads` with
`filename`, `content_type`, byte `size`, and lowercase hex `sha256`.
PUT the bytes to the returned `upload_url` (for S3, include the exact
`Content-Type` header); then `POST /api/v1/sources/{source_id}/complete`
with `size` and `sha256`. The server verifies the uploaded object before
queuing ingestion. Local upload URLs are API paths requiring the same workspace
credentials and CSRF header; S3 URLs expire after 15 minutes. Object keys are
scoped to a workspace. Existing local files remain readable during migration.


### Tenant secret encryption (T1.8)

The `app.services.crypto` module provides AES-256-GCM envelope encryption:
`encrypt(tenant_id, plaintext)` returns authenticated bytes, and
`decrypt(tenant_id, ciphertext)` returns plaintext bytes. Each tenant has a
random 256-bit data key, stored only encrypted under the master key in the
`tenant_data_keys` table. Ciphertexts bind the tenant and data-key version
as authenticated associated data. Set `MASTER_KEY` to a base64-encoded 32-byte
secret (required for `ENVIRONMENT=production`); store it in a secret manager,
not source control. Generate one with:

```bash
python -c 'import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())'
```

`rotate_data_key(tenant_id)` creates a new version for future secrets while
older ciphertext remains readable. For master rotation, retain the old key in
`MASTER_KEY_PREVIOUS` (JSON mapping version to base64 key), increment
`MASTER_KEY_VERSION`, set the new `MASTER_KEY`, and run
`rewrap_master_keys(tenant_id)` for every tenant. Verify decryption before
removing previous keys. Never discard a master key before all wrapped data
keys have been migrated. This service is infrastructure for future channel and
integration credentials; existing API-key hashes remain one-way hashes.


### Durable Redis jobs (T2.1)

Start the API, Redis and arq worker with `docker compose up --build`.
Set `REDIS_URL=redis://redis:6379/0` for Compose (the default localhost URL
is for host development). The worker runs with `python -m app.jobs.worker`.

An admin can enqueue the sample job using `POST /api/v1/jobs/sample` with
workspace credentials; the API returns HTTP 202 and a Redis job ID. The worker
writes a tenant-scoped `jobs.sample.completed` event to the audit log. This
proves the queue/worker round-trip; moving ingestion into arq is T2.2.

The `app.jobs.queue.enqueue(name, **kwargs)` helper is the shared enqueue
interface. Add each new job name to its allowlist and the function to
`WorkerSettings.functions`. Redis AOF is enabled in Compose; production
should use persistent managed Redis and monitor failed jobs.


### Durable document ingestion (T2.2)

Both multipart source uploads and two-step upload completion now enqueue
`ingest_source` in Redis rather than using FastAPI in-process background tasks.
The API returns HTTP 202 once the job is accepted. The worker processes
queued uploads after restart, retries transient embedding/storage failures up
to three attempts, and records a terminal `failed` status and error when
retries are exhausted. Empty/unparseable documents fail immediately.
Reprocessing atomically replaces old chunks, avoiding duplicate search results.
The worker and API must share the same configured object-storage backend.

### Scanned knowledge files (T9.5)

Knowledge uploads now accept PNG, JPEG, TIFF (including multiple frames) and WebP,
and PDFs can mix selectable text and scanned pages. Pages without text use local
Tesseract OCR; text pages retain their existing content. Both paths produce normal
search chunks with original page numbers for citations. Blank pages add no chunks;
an entirely unreadable file fails with recovery advice. OCR does not send documents
to a cloud recognition service or require a new API key. Embeddings use the existing
configured provider. Storage, recognition and embedding run without open DB sessions,
and chunks plus the ready status are committed together.

Worker hosts need `tesseract-ocr`, its configured language data, `poppler-utils` and
`util-linux` (`prlimit`). The Docker image includes English and Hindi language data.
`OCR_LANGUAGES=eng+hin` can be changed after installing corresponding language packs
(for example `tesseract-ocr-tam` for `tam`). Missing language data is a visible failure,
not a silent fallback. Defaults allow 20 recognized pages per file, 20 million image
pixels and 30 seconds per native operation (`OCR_MAX_PAGES`, `OCR_MAX_PIXELS`,
`OCR_TIMEOUT_SECONDS`). Native processes are limited to one thread and 512 MiB of
address space; raster images are scaled to a 2400px longest edge, and recognition
output is limited to 32,000 characters per page. Oversized or timed-out scans should
be split or replaced with clearer copies. Ingestion jobs allow up to 30 minutes.

Migration `0032_ocr_usage` adds `usage_events.ocr_pages`. Each successfully recognized
page (including a blank result) is recorded once per source/content/language/page,
so embedding retries do not duplicate OCR usage. Local OCR has no external provider
charge (`cost_usd=0`); embedding usage is metered separately. Transcription remains
disabled while the required real voice-note benchmark is pending.


### Durable WhatsApp inbound processing (T2.3)

Signed inbound webhooks persist deduplicated messages in `inbound_events` and
queue an arq `handle_message` job; no WhatsApp answer is generated during the
HTTP request. Workers debounce for two seconds, serialize per-contact work via
PostgreSQL advisory locks, and combine pending texts into one answer. Transient
provider failures retry with exponential backoff (up to five attempts); final
failure records a handoff and internal agent note, then attempts an apology.
Run the Redis worker alongside the API. Existing `whatsapp_inbound` IDs are
migrated to processed events to preserve deduplication.


### Installable Inbox and push notifications (T4.4)

The Inbox can be installed from the browser's install menu over HTTPS. It includes
192px and 512px PNG icons. Agents can enable or disable notifications independently
for each workspace and browser. Owners, admins, and agents are eligible.

Generate a VAPID key pair once:

```bash
python -m scripts.generate_vapid --subject mailto:ops@example.com
```

This creates a new, owner-readable `.env.push` file and refuses to overwrite an
existing file. Copy its three settings into the API and worker's environment or
deployment secret store. Keep the same key pair across restarts. Changing it
requires browsers to unsubscribe from the old PushManager subscription and opt in
again. The private key is never returned by the API. Configure HTTPS and run
`alembic upgrade head`, the API, the web app, and `python -m app.jobs.worker`.

The worker checks opted-in agents in small batches every ten seconds. Delivery
state and temporary claims live in PostgreSQL, so Redis restarts and older waiting
conversations do not erase pending alerts. Each device receives one generic alert
for newly waiting or updated conversations. Successful deliveries are deduplicated;
transient failures retry after a minute and 404/410 responses remove expired
subscriptions. A crash after the provider accepts an alert can cause a duplicate
after the two-minute claim expires. Alerts coalesce under a workspace-specific tag.
Provider endpoints are limited to Chrome/FCM, Firefox/Mozilla and Apple Web Push.
A notification opens the corresponding workspace; API membership checks still apply.
New-lead alerts are deferred to T5.2, which introduces lead capture.

The last-loaded conversation list and open transcript are available read-only
offline in this browser for 15 minutes. The offline copy is cleared on workspace
switch and logout. Reconnect to send replies or refresh data.

Automated checks cover subscription authentication, CSRF, role and tenant
boundaries, repeated opt-ins, per-device opt-out, unsupported endpoints, delivery
deduplication, retry, expired subscriptions, and browser install/offline behavior.

Manual Android Chrome acceptance (requires a deployed HTTPS origin and real VAPID
keys):
1. Sign in as an agent, open Inbox, and use Chrome's Install app menu.
2. Open the installed app and choose Enable notifications. Allow the browser prompt.
3. Put the app in the background. Cause a conversation to enter Waiting for agent.
4. Confirm a generic notification arrives, tap it, and verify the correct workspace opens.
5. Disable notifications for that workspace and verify a subsequent waiting message
   produces no notification on this device.

Record the device, Chrome version, origin, time, and outcome in PR #35. A headless
browser test exercises the UI and stored opt-in with a fake PushManager; receipt
through a real Android push service remains a manual acceptance step.


### Curated Q&A

Workspace owners and admins can add, edit, or delete entries under Knowledge → Curated Q&A. A matching entry supplies the sole factual source for the reply and appears as FAQ in citations. Questions below the similarity threshold continue through document retrieval. Set `QA_MIN_SIMILARITY` between 0 and 1 to adjust matching. The default is 0.9. FAQ embeddings use the same embedding provider and dimensions as documents.


### Workspace WhatsApp channels

Existing WhatsApp number IDs are backfilled into channel records by migration
`0017_channels`. Link a number with `PATCH /api/v1/tenant`, then use
`GET /api/v1/channels` to find the channel ID. Workspace admins can set
`access_token` with `PATCH /api/v1/channels/{channel_id}`. The API encrypts
the token using the workspace data key and returns only whether a token exists.
Set `MASTER_KEY` before adding credentials. Passing `null` removes the
channel token and restores the shared `WHATSAPP_ACCESS_TOKEN` fallback.

Set `WHATSAPP_VERIFY_TOKEN` and `WHATSAPP_APP_SECRET` to accept signed
webhooks. Channel status reports local configuration and the last webhook
received. Meta Embedded Signup is tracked separately as T6.2.


### Meta Embedded Signup

Configure a Meta Tech Provider or BSP app for Embedded Signup and add the
dashboard HTTPS origin to its allowed domains. Set `WHATSAPP_APP_ID`,
`WHATSAPP_SIGNUP_CONFIG_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`,
and `MASTER_KEY` on the API. Configure the Meta webhook callback at
`/webhooks/whatsapp` and subscribe to WhatsApp message events.

From the workspace dashboard, an owner or admin can enter a six-digit
registration PIN and launch **Connect with Meta**. The API exchanges the
authorization code, verifies that the phone belongs to the selected WABA,
subscribes the app, registers the number, and stores the encrypted channel
credential. Keep the PIN in your business password manager. If a Meta step
fails, the dashboard shows an error and you can start a fresh signup.

For live acceptance, use a Meta test business and verify that the dashboard
shows the connected number. Send a test message to your own opted-in number
with the dashboard button. The sender must have the `hello_world` template
available and Meta must permit the recipient. Check delivery on the phone and
send a reply to verify the signed inbound webhook reaches the workspace.


### WhatsApp conversation behavior

The first automated answer introduces the bot and explains how to reach the team.
Replies use WhatsApp formatting and render citations in a short source line.
When a reply is long, the bot offers the remaining detail in a follow-up.
The bot can show reply buttons or lists and offer a handoff when it lacks an answer.

System messages use English or Hindi according to the workspace default language.
Handoff messages show the next opening time in the workspace time zone when the
business is closed. Waiting conversations suppress automated answers and may
receive a reminder after an hour of inactivity.

`STOP` and `बंद` record marketing opt-out. Add workspace-specific equivalents
through `PATCH /api/v1/tenant` using `whatsapp_opt_out_keywords`. `START` records
renewed consent. Opted-out contacts can still ask support questions.


### WhatsApp templates outside the reply window

Embedded Signup records the WhatsApp Business Account ID for new channels.
For an existing channel, an admin can bind it with
`PUT /api/v1/whatsapp/templates/account` and a `waba_id`. The API verifies
that the connected phone belongs to that account.

Admins can sync templates with `POST /api/v1/whatsapp/templates/sync` or
submit a text template with `POST /api/v1/whatsapp/templates`. Provide
`name`, `language`, `category`, `body`, and ordered `examples` for any
numbered variables such as `{{1}}`. Subscribe the Meta app to
`message_template_status_update` for approval changes. Sync reconciles the
local catalog with Meta.

The Inbox picker supports approved templates with text bodies and numbered
variables. Templates with headers, media, buttons or named variables can be
tracked in the catalog. Sending requires recorded contact opt-in. The worker
rechecks approval and consent before dispatch.


### Private WhatsApp media (T9.4a)

Inbound images, documents and audio are downloaded by the worker with the channel's
token and saved under `tenant/<tenant_id>/media/<id>` in the configured private storage.
Locations and contact cards are retained as structured message attachments. Agents can
queue images/documents with multipart `POST /api/v1/inbox/conversations/{id}/media`
(`file`, optional `caption`, required `Idempotency-Key`). The worker rechecks the 24-hour
window and uploads to Meta; an uncertain send is marked `unknown` and is not retried.
Authenticated viewers download through `/api/v1/conversations/{id}/media/{attachment_id}`.
S3 links expire after five minutes; local downloads remain authenticated and uncached.
Types, signatures, checksums and streamed size are checked. Images are limited to 5 MiB;
audio/documents to `MEDIA_MAX_BYTES` (16 MiB maximum). Media expires after
`MEDIA_RETENTION_DAYS` (365 by default), with worker cleanup every ten minutes. Enable
bucket default encryption and an equivalent lifecycle for orphaned objects. Migration
`0030_message_media` adds attachment metadata and temporary outbound bytes. Inbox controls,
image interpretation and voice transcription are described below; unread attachments go to an agent.

### Inbox attachments and voice notes (T9.4b)

The inbox displays private images, voice playback, transcripts, image descriptions and
location/contact cards. Owners, admins and agents can send an image/document after
confirming its filename and recipient; viewers have read-only access. Media is not
cached for offline playback. Worker hosts need `ffmpeg` (included in the Docker image).
Audio is decoded locally with network/file protocols disabled and a bounded output:
over five minutes, invalid, unavailable or rate-limited media goes to an agent. At most
20 interpretation requests per workspace per minute are allowed, within subscription/trial entitlement. Successful transcripts
become the customer's message for answering, handoff and opt-out matching. Checkpoints
avoid repeating provider calls after an interrupted interpretation or answer retry.
`usage_events.audio_seconds` meters attempted speech duration separately from tokens;
cost remains unknown rather than inventing a per-token charge (migration `0031_audio_usage`).

`TRANSCRIPTION_PROVIDER=disabled` is the default. The first adapter is OpenAI with
automatic language detection, `TRANSCRIPTION_MODEL=gpt-4o-transcribe` and `OPENAI_API_KEY`.
This is a candidate adapter, **not a measured production-provider selection**. Before
enabling it, compare representative English, Hindi, Kannada, Tamil, Telugu, Marathi,
Bengali and Hinglish voice notes against human transcripts. Keep recordings outside git.
Run `TRANSCRIPTION_PROVIDER=openai python -m scripts.evaluate_speech /private/samples.json`
with a JSON list such as `[{"file":"clip.wav","language":"hi","reference":"मानव प्रतिलेख"}]`.
It prints aggregate word error rates and missing languages without printing transcripts.
Review domain names, numbers, background noise and mixed-language errors as well as WER;
the real-sample evaluation remains pending without recordings and provider credentials.
`MEDIA_VISION_ENABLED=true` sends images to the workspace's configured vision-capable
OpenAI, Anthropic or Gemini model. Descriptions are treated as customer input for the
existing grounded answer flow; unsupported models and provider failures go to an agent.

### Website widget v2

Workspace owners and admins can open **Website widget** on the dashboard to
preview and save its theme, launcher, avatar and greeting. Add the site's
origin to the workspace allowlist, then paste the dashboard's script tag
with the public HTTPS API host. Use an asynchronous script tag.

The widget persists the current chat and one pending message in local storage.
It reconnects and sends that message when the browser returns online. The API
queues each turn in PostgreSQL and the worker claims it. Replies arrive over
SSE with a polling fallback. Run the worker alongside the API. Agents can
respond to web handoffs from the Inbox. **Start a new chat** clears the local
chat and pending message.

The checked-in script is built from `widget/widget.ts`. Run
`node widget/build.mjs` on Node 22.13 or newer after edits. CI verifies the
checked-in bundle, its TypeScript types and its 30 KiB gzip budget.


### AI usage and workspace quotas

Migration `0021_usage` seeds Starter, Growth and Business from the product plan.
Existing and new workspaces start on Starter with pause-and-handoff behavior.
Workspace owners can read `GET /api/v1/usage` and set the limit behavior with
`PUT /api/v1/usage/quota-policy` using `{"behavior":"pause"}` or
`{"behavior":"overage"}`. Overage turns are recorded for future billing.

A live AI answer attempt reserves one message for the UTC month. Worker retries
of the same widget or WhatsApp turn reuse the reservation. Human replies and
handoff confirmations do not use quota. Preview, evaluation and ingestion calls
record provider usage without reserving customer messages. Token counts use
provider-reported values. Configure `AI_PRICES_USD` as a JSON map from
`provider:model` to `[input, output]` USD per million tokens. Costs use those
base rates. Unavailable token counts or rates are retained as unpriced calls.

Run the worker for nightly UTC daily rollups and Redis reconciliation. The usage
ledger is authoritative during Redis outages. Alerts at 80% and 100% use the
configured email sender. The default sender records a delivery request in logs.


### Razorpay subscriptions

Run the database migrations, then set `RAZORPAY_KEY_ID`,
`RAZORPAY_KEY_SECRET` and `RAZORPAY_WEBHOOK_SECRET` on the API and worker.
In test mode, provision each monthly plan with
`python -m scripts.create_razorpay_plan starter` (repeat for `growth` and
`business`) and enter the resulting provider IDs in `RAZORPAY_PLAN_IDS`
as a JSON map keyed by the local plan names. The command creates a remote
plan each time it runs. Reuse the IDs after provisioning.

Configure `/webhooks/razorpay` as the webhook URL. Enable the subscription
activated, charged, pending, halted and cancelled events and payment.failed.
Workspace owners can start hosted checkout and manage subscriptions from
**Billing**. Upgrades request an immediate prorated change from Razorpay.
Downgrades and active cancellations take effect at the end of the cycle.

When Razorpay is configured, a 14-day trial covers new workspaces. Existing
workspaces receive a 14-day trial from migration. Failed renewals have a
seven-day grace period. Expired trials and grace periods route new automated
turns to the Inbox. The worker reconciles subscriptions and recent settlements
daily and exposes discrepancies in the Billing screen.

Manual acceptance requires a Razorpay test account. Complete a hosted mandate,
confirm the signed activation and charge events update the workspace, and
check a plan change and cancellation. If checkout times out, retry the same
plan to recover the existing reference. If Razorpay never confirms it, an
operator must verify the remote account before clearing the local reservation.


### GST invoice issuance

Set `GST_INVOICE_ISSUER` after an accountant approves the supplier details,
service accounting code, rate, and PDF layout. The JSON object accepts
`name`, `address`, `state` (two-digit GST state code), `postal_code`,
`gstin`, `sac`, and `tax_rate` (percent). The default is disabled.
The current workflow covers domestic INR subscription charges from a single
registered supplier.

Owners enter their legal billing name, address and optional GSTIN in **Billing**.
With invoice issuance enabled, the profile is required before checkout. Future
captured INR charges issue one immutable invoice per Razorpay payment. The PDF
shows the supplier and customer snapshot, SAC, place of supply, and the
applicable central and state/union-territory GST or integrated GST. Sequence
numbers reset on April 1 in India time. Owners can list and download PDFs from
Billing. Issued invoices retain the original details when a profile is edited.

The Razorpay plan creation script adds configured GST to the plan's base price.
Existing provider plans keep their configured charge. Invoices calculate GST
within the actual captured total, so review and map the intended tax-inclusive
provider plan IDs before enabling checkout. Historical charges require a
separate accounting review. Have an accountant confirm any e-invoicing,
signature, credit note and retention requirements before production use.

### Website crawling backend (T9.6a)

Migration `0033_websites` stores website crawl jobs, schedules and page checksums.
The worker supports single-page or same-host public HTTPS crawling on port 443,
with up to 50 pages per website and depth 0–3. Plan defaults reserve 0/50/200 pages
for Starter/Growth/Business across all websites; `CRAWL_PAGE_CAP` caps each workspace.
Existing custom `crawl_pages` plan limits are preserved by the migration.

The crawler identifies itself as `MarketingAddaKnowledgeBot/1.0`, honors robots.txt
and noindex, and waits at least one second between requests to a host across all
workers/workspaces. Robots delays above 30 seconds require another source. Redis
is required for this shared pacing. DNS answers and every redirect must remain
public; connections pin the validated IP while retaining hostname TLS verification
and configured environment proxies. Downloads are limited to 1 MiB and extracted
text to 100,000 characters per page. This fetcher executes no JavaScript.

Main text retains headings and removes navigation, scripts and footers. Only
changed text is embedded; missing/private/noindex pages lose their indexed chunks.
Transient errors retry five times with backoff, jitter and Retry-After. Publication
checks workspace state and a run token, so deleted or replaced runs cannot publish.
Schedules record the next daily/weekly run. No live website or model call is required by the offline tests.

### Website controls and scheduled dispatch (T9.6b)

Owners/admins use `/api/v1/websites` to list page budgets and POST a website
(`url`, `max_pages` 1–50, `depth` 0–3, `schedule` manual/daily/weekly).
`POST /{id}/refresh` queues a manual refresh, `PATCH /{id}` changes schedule,
`GET /{id}/pages` lists indexed URLs and `DELETE /{id}` removes the website and
all its indexed pages. Creation reserves the requested page count under a
workspace lock; duplicate URLs and exhausted budgets return 409. File-source
endpoints exclude website pages. These handlers perform no external fetches.

Run the normal arq worker: its ten-second dispatcher uses database due times as
an outbox, recovers interrupted runs after 70 minutes and retries queue outages.
Only one crawl runs per workspace at a time. Manual refreshes preserve host
Retry-After cooldowns. Terminal crawl failures appear in the platform console
without URLs or error payloads; workspace admins can retry through refresh.
No additional environment variables.

### Website knowledge UI (T9.6c)

In **Knowledge → Websites**, owners/admins can add a single public HTTPS page or
follow links on its exact host, choose page/depth limits, and set a manual, daily
or weekly refresh. The screen shows workspace page reservations, indexed pages,
current progress, recovery advice and last/next refresh times in IST. It polls
every five seconds. Expand indexed pages to open the public source URLs.

Refresh queues work for the crawler; schedule changes apply after an active run.
Deletion confirms the starting URL and removes all its indexed knowledge. Viewers
and agents cannot manage website knowledge. Loading, empty and recoverable error
states, budget upgrade links and narrow-screen layouts are included.

Browser coverage uses local static website/model fakes and checks crawl → citation
→ refresh → schedule → delete, reader permissions, 320-pixel layout and axe.
Run `python -m scripts.seed_websites_e2e` after the other browser seed scripts in
an isolated test database. [Website controls screenshot](docs/screenshots/t9-6-websites.png).

### Improve answers backend (T9.7a)

Migration `0034_answer_reviews` records live AI replies from WhatsApp, the widget
and the legacy web chat. Bot previews, human/quota notices and failed generation
are excluded. A reply needs attention when it has no retrieved knowledge, the
model explicitly offered a handoff, or its widget vote is down. This is a review
signal rather than a measured accuracy score. Historical replies are not guessed
or backfilled because older turns lack a reliable message association.

Owner/admin-only `GET /api/v1/improvements` returns paginated reviews, top ten
questions (case/whitespace normalized), unanswered counts and current widget votes.
Satisfaction is positive / (positive + negative) × 100, or null without votes.
One current vote counts per answer; changing it replaces the previous rating.
Date/channel filters cover 1–90 UTC days; triage filters do not change the summary.
Dismissal/resolution preserves historical metrics. Only recorded AI replies enter
these reports; no customer identifiers are included. Question/reply text is private
to owners/admins, unlike the existing aggregate analytics overview.

`PATCH /{id}` reopens/dismisses a review. `POST /{id}/answer` queues an explicitly
human-written question (≤1,000 characters) and answer (≤6,000). The existing arq
worker embeds it outside API requests/DB sessions, then publishes to curated Q&A
atomically. Retries use bounded backoff; interrupted jobs recover after 20 minutes.
Repeat submissions are idempotent; editing a prior correction updates its Q&A.
Deleting a conversation removes its reviews. Drafts contain private text and are
never logged; audits contain IDs only. No new provider/settings.

### Improve answers UI (T9.7b)

Owners/admins open **Knowledge → Improve answers**, or use the link from Analytics.
Filter by UTC date, channel, open/resolved/dismissed status and review reason.
The summary shows recorded AI replies, unanswered signals, current votes, satisfaction
and top questions. Triage filters apply to the queue; date/channel filters also
apply to the summary. Reply timestamps use IST. The view polls every five seconds.

Expand a bot reply to inspect its answer and sources. **Add a Q&A answer** opens
an editable question and blank approved-answer form. Saving queues publication;
queued/processing states prevent conflicting edits. Failed jobs retain the draft
for retry, and resolved items can edit their existing Q&A. Dismiss/reopen changes
triage status while preserving vote history. Publishing changes future bot knowledge
and sends no customer message. All text is scoped to owners/admins; viewers/agents
cannot enter this view. Models can report missing coverage on web replies as well
as WhatsApp, even when retrieval returned sources.

The synthetic browser fixture uses generic refund/hours questions, never customer
history. Seed it with `python -m scripts.seed_improvements_e2e` after the other
browser fixtures in an isolated test database. Browser coverage checks analytics
entry, metrics, publication, FAQ retrieval, filtering/dismissal, reader protection,
320px layout and axe. [Demo screenshot](docs/screenshots/t9-7-improve-answers.png).

### Multilingual bot replies (T9.8a)

The reply policy now applies on web and WhatsApp: use the latest customer's
language and script, switch with the customer, keep Latin-script Hinglish in Latin,
and preserve facts, amounts, names, IDs, URLs and citation markers. Tenant rules
cannot override this policy. Supported default notices cover English, Hindi,
Hinglish, Kannada, Tamil, Telugu, Marathi and Bengali. Script/word hints select
notices without network calls; ambiguous text falls back to the workspace default.
These hints are heuristic, especially for Marathi/Hindi and transliteration.
Attachment captions do not determine the customer's notice language.

Migration `0035_bot_messages` adds tenant-owned notice overrides. Owners/admins
read `GET /api/v1/bot/messages` and replace one language's overrides with
`PUT` (`language`, `messages`). An empty object restores that language's defaults.
Greeting templates retain `{bot}` and `{business}`; closed-hours templates retain
`{opening}`. Unknown placeholders, attribute access, conversions, formatting specs
and oversized/empty values return 422. Audits store only the language code.

The English/Hindi admin interface is independent of the workspace's
customer-facing default language. No new credentials or settings.
Automated tests use provider fakes: they verify language-policy contracts and
notice handling, not real-model language accuracy. Native-speaker review of
conversation scripts and live multilingual accuracy checks remain pilot gates
(UX_DESIGN §12); no such review is claimed by these automated checks.

### Interface language (T9.8b)

Choose English or हिन्दी in the page header. The browser remembers this preference
in a validated `chatbot_locale` cookie for one year; it does not change the
workspace language, customer messages or unsaved form content. Server rendering,
error boundaries and date/number formatting use the selected locale. All admin
catalogs include Hindi copy, covering authentication, navigation, bot settings,
knowledge, Inbox, leads, analytics, campaigns, billing and platform operations.
The interface uses a bundled
OFL-licensed Noto Sans Devanagari font, generous line spacing and mobile wrapping.
Screenshot: [Hindi core interface](docs/screenshots/t9-8-hindi-core.png).

### Localized controls and customer notices (T9.8c)

Inbox controls, offline snapshots, push notifications, widget settings and missing
page recovery use the interface catalog. Customer text and widget greetings keep
their original content. In Bot settings, expand Customer-facing default messages
to edit common notices for one customer language, save them, or confirm a reset to
its defaults. Only workspace owners/admins can use the editor. Interface language
changes preserve drafts; workspace changes remount the editor.
Screenshot: [Hindi customer notices](docs/screenshots/t9-8-hindi-notices.png).

### Complete Hindi catalogs (T9.8d)

All 17 admin message namespaces now include English and Hindi copy. Catalog tests
require identical keys and substitution placeholders. Product/provider names,
protocol examples, currency codes and customer-authored content retain their
original values. Browser checks cover Hindi operational screens at 320px and
desktop, accessible controls, error/retry/empty states and unchanged URLs/CSV examples.
Screenshot: [Hindi knowledge controls](docs/screenshots/t9-8-hindi-operations.png).
Native-speaker script review and live-model language evaluation are still required
before a pilot; automated tests verify contracts and UI behavior using fakes.

### Product-managed appointments (T9.9a)

Migration `0036_appointments` adds services, staff, explicit availability slots and
bookings. Owners/admins configure resources under `/api/v1/appointments`; agents
can read the catalog, availability and bookings, create five-minute holds, confirm,
cancel, reschedule, and retry blocked reminders. Viewers have no appointment access.
Catalogs are capped at 100 services/staff; date lists return up to 100 records in a
range of at most 60 days. Dates must include an offset; times are stored in UTC.
Each booking keeps its customer language, independently of the admin interface.
Slot length is fixed when created, so later service-duration edits affect new slots.

Staff-row locks reject overlapping availability; slot-row locks and a PostgreSQL
partial unique index prevent competing holds/bookings. Expired holds are reclaimed
when reserving again. Retries reuse the same reservation without extending its
expiry. Rescheduling keeps the original booking until the replacement is confirmed
and cancels it atomically. Resource IDs and conversations are checked within the
request principal's workspace. Audits/events exclude customer names and phone numbers.

Reminder settings are stored now; worker delivery and chat tools follow in T9.9b,
with English/Hindi administration in T9.9c. Google Calendar OAuth and free/busy
integration remain the separately ordered T9.9d follow-up. No new credentials.

### Appointment chat and reminders (T9.9b)

The bot can list live availability, hold one slot per turn, and list only the
current customer's bookings. Holds return a short reference and explicit actions:
`BOOK <reference>` confirms; `REMIND <reference>` confirms and opts into a reminder;
`CANCEL <reference>` cancels. These commands are handled directly from the customer's
message, never from model arguments or retrieved knowledge. WhatsApp shows reply
buttons; web customers can send the same commands. Commands and reminder copy
preserve the booking's customer language across all BOT-2 languages and Hinglish.
Bot previews never receive booking tools. A replacement hold leaves the original
booking intact until confirmation. Provider fakes verify contracts, not model accuracy.

The worker checks due reminders every 15 seconds. Web reminders appear through the
widget's existing team-reply polling while it is open. WhatsApp sends text within
its 24-hour window; outside it, an existing opt-in and an approved UTILITY template
in the booking's language are required. Configure exactly three variables: service,
staff and local appointment time. No marketing consent is created by booking.
Missing permission/configuration is recorded as `blocked` and can be explicitly
retried after correction. `sent` means accepted by the provider (or saved for web),
not proof of delivery. Uncertain sends and stale claims become `unknown` and are
never retried automatically. Cancellation suppresses pending reminders; messages
already in flight cannot be recalled. Suspended workspaces and opted-out contacts
receive no reminders. Native-speaker script review remains required before pilot.

### Appointments administration (T9.9c)

Open Appointments from the workspace navigation. Owners/admins configure services,
staff and explicit availability; agents can hold, confirm, reschedule and cancel
bookings. Viewers cannot access appointment data. A hold requires a recent customer
conversation and customer-provided contact details; reminder consent is a separate
checkbox. Confirmation uses a review dialog. Rescheduling holds a replacement while
the original booking stays confirmed. Reminder states and permission/configuration
problems are visible beside bookings; only blocked reminders can be retried.

Filters and new slot inputs use the device's timezone; appointment lists and
customer notices show the workspace timezone. The screen displays both zones.
Lists are bounded to 100 records and date ranges of at most 60 days. All controls
have English/Hindi copy; interface changes preserve drafts and do not change the
customer language. Staff can choose each customer's language on the hold form.
Screenshot: [Appointments](docs/screenshots/t9-9-appointments.png).
Google Calendar connection is the separately ordered T9.9d task. No new credentials.

### Google Calendar (T9.9d)

Optional Google OAuth uses Calendar v3 with calendar-list read, event write and event free/busy scopes.
Create a Google Cloud OAuth web app, enable Calendar API, and register
`https://marketingadda.com/api/backend/google-calendar/callback` exactly. Set the three
`GOOGLE_CALENDAR_*` OAuth settings and `MASTER_KEY` through environment settings.
Only a signed-in owner/admin can authorize an account; access and refresh tokens are encrypted.
The arq worker exchanges codes, refreshes tokens early and lists writable calendars.
Failures retry up to five times, honor Retry-After and expose safe status codes.
Disconnect revokes credentials; previously created Google events remain until explicitly cancelled.
No Google app exists yet: live OAuth verification remains pending. Availability uses a worker-refreshed free/busy cache (90-second freshness), workspace hours,
service duration and local reservations. Up to five calendar bindings may target a service,
a staff member or their combination. Matching calendars all constrain availability.
Linked resources generate slots up to 60 days ahead; unlinked resources retain manual slots.
Customer-data sharing needs explicit administrator consent when binding. Confirming a linked booking queues a fresh free/busy check and deterministic Google events.
The customer receives a pending notice first, then confirmation after all calendars accept.
Rescheduling creates replacement events before cancelling the original booking and removing old events.
Cancellation and failed partial creates queue cleanup; retries reuse event IDs. External edits with
changed ownership are preserved and surfaced for administrator attention. Provider conflicts or
exhausted retries appear in platform failed jobs. Token refresh failures email workspace admins.
WhatsApp completion notices require an open 24-hour customer-service window and channel consent;
uncertain sends are never automatically repeated. Owners/admins manage connections and bindings from **Appointments → Google Calendar**
in English or Hindi. Agents can view booking synchronization status. A disconnected linked
resource remains paused until its link is explicitly removed; removing all links restores manual slots.
The browser suite uses a fake OAuth redirect and fake Google calls, with accessibility and 320 px checks.
The Python `tzdata` dependency supplies legacy IANA aliases such as `Asia/Calcutta` on minimal images.

Google can accept unrelated external events between a free/busy check and insertion; Google offers
no atomic reservation API. This integration serializes local holds and checks Google immediately
before inserting, but calendar owners should review externally added conflicting events.

API references: [OAuth web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server),
[free/busy](https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query),
and [event insertion](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert).

### Customer payment links (T9.10)

Merchant payment links use each workspace's own Razorpay test/live API keys, independently
of platform subscription billing. Owners/admins save an encrypted merchant connection and
test it. Live keys require explicit live-mode approval. Agents approve a customer conversation,
amount in INR paise (₹1–₹10 lakh), purpose and expiry (5 minutes–30 days); approval keys prevent
accidental duplicate dues. The bot will only send approved amounts. Payment does not change
an appointment's booking state. The worker creates links with a stable unique reference, disables Razorpay SMS/email notifications
and never accepts partial payments. After an uncertain POST, it reconciles by reference without
posting again. A crash before the actual POST can therefore require merchant review; this avoids
creating duplicate links. Five retries honor Retry-After and use backoff with jitter. Exhausted retries create one
localized customer follow-up and move bot-handled conversations to the team’s waiting queue.

Register `/api/backend/payments/webhook/<connection-id>` in that merchant account for
`payment_link.paid`, `payment_link.cancelled`, `payment_link.expired` and `payment_link.partially_paid`.
Use the same webhook secret entered in the connection. Signatures are checked before JSON parsing.
Verified captured payments must match the approved amount, INR currency, reference and workspace.
Duplicate and out-of-order events cannot send a second receipt or undo a paid state.
Raw verified payloads are encrypted and removed after 14 days; deduplication records remain.
Disconnect removes API credentials; the encrypted webhook secret remains to recognize payments
on existing links. Revoke old API keys in the merchant dashboard when appropriate.
The bot can list and send only agent-approved dues in its current conversation.
Payment links and verified receipts use deterministic copy in the eight supported customer languages.
WhatsApp links require an open 24-hour window. Outside it, receipts require an approved utility
template of the matching language and the contact’s existing opt-in; otherwise delivery is visibly
blocked for team follow-up. Uncertain WhatsApp sends are never automatically repeated.
Use **Customer payments** in the English/Hindi dashboard to connect/test the merchant,
copy its webhook URL, approve dues, send links, refresh provider status and cancel unpaid links.
Agents see payment and delivery status; only owners/admins manage merchant credentials.
The browser suite uses fake merchant API responses and a test-only signed capture flow,
including accessibility and 320 px checks. The test-only capture endpoint is never registered
by the production ASGI app.

[Create standard payment links](https://razorpay.com/docs/api/payments/payment-links/create-standard)
and [payment-link webhooks](https://razorpay.com/docs/webhooks/payment-links) describe the provider contract.
Standard Razorpay checkout offers the methods enabled on the merchant account, including UPI.

`MASTER_KEY` is required for credential storage. `PAYMENT_LINK_REQUESTS_PER_MINUTE` defaults
to 60 per workspace. Optional WhatsApp utility receipt templates must have three variables
(description, amount, payment reference). Provider API is Razorpay Payment Links v1.
Live test-mode payment verification is pending; the environment has no merchant keys configured.

### Lead sync (T9.11)

Lead sync adds explicit sharing approval, encrypted HubSpot/Zoho credentials
and a transactional outbox for each new or updated lead version. Captures and team updates
remain local even when a connector is unavailable. Optional email capture supports CRM
identity matching. Disabling/disconnecting stops future sharing. Existing leads are exported
only through an explicitly confirmed backfill; retries keep the same immutable snapshot.
Google Sheets uses a separate, session-bound OAuth flow with PKCE and the narrow `drive.file` scope.
Only app-accessible spreadsheets can be selected; users can create a dedicated export spreadsheet.
Calendar credentials and account profile scopes are not requested. The arq worker exchanges
authorization codes and refreshes access tokens early. Register
`https://marketingadda.com/api/backend/lead-sync/google/callback` exactly in the Google OAuth app.
Disconnect removes stored credentials; revoke provider access from the provider’s account settings
if desired. The worker discovers app-accessible spreadsheets, creates dedicated export files and appends
lead versions as literal strings, preserving formulas as text and leaving existing rows untouched.
It validates the export header and reconciles uncertain appends by stable event ID; uncertain
writes are never blindly repeated. Use a fresh export tab after 50,000 rows. Successful delivery
clears its extra customer-data snapshot; local leads remain authoritative. Five retries use
backoff/jitter and Retry-After; terminal failures appear in platform administration.
Credential failures notify workspace admins. HubSpot Contacts (CRM v3) and Zoho Leads (CRM v8)
match exact normalized phone first, then email, and update only mapped fields. Ambiguous matches
or missing mandatory values fail safely. Each version adds a mapped-field summary and conversation
link as a CRM note. Private team notes are excluded unless enabled. Uncertain creates and notes
reconcile before retrying. Owners/admins configure field mapping, select spreadsheet tabs, approve sharing, backfill the latest 100 existing leads, and inspect/retry deliveries from `/dashboard/lead-sync` in English or Hindi. The connection remains paused until sharing is explicitly approved.

For HubSpot, create a private-app token with Contacts read/write and contact-property read access
(`crm.objects.contacts.read`, `crm.objects.contacts.write`, `crm.schemas.contacts.read`); ensure the
app can also read/create associated notes. Zoho self-client OAuth credentials need
`ZohoCRM.modules.leads.READ,ZohoCRM.modules.leads.CREATE,ZohoCRM.modules.leads.UPDATE,ZohoCRM.modules.notes.READ,ZohoCRM.modules.notes.CREATE,ZohoCRM.settings.fields.READ,ZohoSearch.securesearch.READ`.
Choose the account's data centre (India by default; US, EU and Australia supported). Refresh tokens
and cached access tokens remain encrypted. Required Zoho fields such as Last Name or Company must
map to actual lead values; no placeholder customer data is invented.
Provider references: [HubSpot notes](https://developers.hubspot.com/docs/api-reference/legacy/crm/activities/notes/guide),
[Zoho record search](https://www.zoho.com/crm/developer/docs/api/v8/search-records.html),
[Zoho notes](https://www.zoho.com/crm/developer/docs/api/v8/create-notes.html).

`LEAD_SYNC_GOOGLE_CLIENT_ID`, `LEAD_SYNC_GOOGLE_CLIENT_SECRET` and
`LEAD_SYNC_GOOGLE_REDIRECT_URI` configure the separate Google lead-export consent flow.
`LEAD_SYNC_REQUESTS_PER_MINUTE` defaults to 60 per provider/workspace. Merchant and Calendar
credentials are not reused. Live provider checks remain pending until account settings exist.

Lead-sync API versions are pinned with `LEAD_SYNC_HUBSPOT_API_VERSION=v3`,
`LEAD_SYNC_ZOHO_API_VERSION=v8` and `LEAD_SYNC_SHEETS_API_VERSION=v4`; review provider
deprecations quarterly before updating the adapters.

### Account two-factor authentication (T9.13)

T9.13 adds account-wide authenticator support for verified owners/admins. Core storage uses
versioned, user-bound encryption through `MASTER_KEY`; authenticator keys are never plaintext in
the database. `TOTP_ISSUER` defaults to `MarketingAdda`. Retain prior master-key versions for
existing authenticator ciphertext when rotating the master key. TOTP uses RFC 6238 SHA-1, six digits,
30-second steps and one adjacent step of clock tolerance. A used time step cannot be replayed.
Recovery codes are random 128-bit values, stored only as user-bound hashes and consumed atomically.
Enrollment is bound to the current session and password, expires after ten minutes and permits
five proof attempts. Login challenges expire after five minutes and permit five attempts.
Security changes rotate the current session, revoke other sessions and invalidate pending logins.
The account endpoints are cookie-session only: `GET /api/v1/auth/mfa`, `POST /auth/mfa/setup`
(password), `POST /auth/mfa/enable` (authenticator code), `DELETE /auth/mfa/setup`, and
`POST /auth/mfa/recovery-codes` or `/auth/mfa/disable` (password plus authenticator/recovery code).
Secret keys and recovery codes appear only in their creation response, with `Cache-Control: no-store`.

An enrolled account's `POST /auth/login` returns `{"mfa_required":true,"challenge":"...","expires_in":300}`
and an HttpOnly challenge cookie, without a full session. Submit that body nonce and a code to
`POST /auth/mfa/verify`; only success issues a full session. Five attempts per IP/account in fifteen
minutes apply independently to sign-in, management and verification, with a separate durable
five-attempt bound per challenge. Password reset revokes sessions/challenges and retains 2FA.
Accounts keep their protection after role demotion and may manage it using their password and
second factor. Existing API keys are independent credentials; account 2FA does not apply to them.
Open **Account security** (`/account/security`) from the sidebar/mobile navigation to enroll with
a locally rendered QR code or manual key, save/download one-time recovery codes, regenerate codes,
or disable protection after reauthentication. The sign-in form handles the second-factor challenge
and recovery-code alternative in English/Hindi. It never treats password verification alone as a
successful sign-in for an enrolled account.

Users can create and switch between their own workspaces. A new workspace becomes the selected
workspace immediately. Switching hides and resets the old workspace's page, waits for private
inbox cache removal, then loads the selected workspace's role and data. Private content is also
unmounted before logout is sent, preventing late snapshot writes during sign-out. Workspace APIs
continue to validate membership and return 404 for another workspace's records.

Public integrations use the additive `/api/v1/public` API; `/api/v1/public/docs`
serves its OpenAPI reference. Explicit resource scopes are accepted when creating
workspace API keys (`scopes` instead of the legacy `scope`). These keys cannot
access dashboard endpoints. Quotas use the plan's `api_requests_per_minute` limit,
falling back to `PUBLIC_API_DEFAULT_REQUESTS_PER_MINUTE`; Redis outages fail closed.
`PUBLIC_API_MAX_BODY_BYTES` bounds public JSON requests. Configure `MASTER_KEY`
for authenticated pagination cursors.
Public source creation accepts JSON text/Markdown documents; Q&A creation and
updates also return `202` operation records. Poll `GET /api/v1/public/operations/{id}`
for completion. The arq worker must run: its durable public-operation dispatcher
handles storage/embeddings with three attempts, clears finished payloads, expires
24-hour replay records and removes finished operation metadata after 30 days.
