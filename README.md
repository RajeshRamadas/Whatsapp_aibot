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
# {"total": 2, "passed": 2, "accuracy": 1.0, "source_hit_rate": 1.0, "results": [...]}
```

Each case can check three things:

- `expected_source`: that file must be among the cited sources.
- `expected_keywords`: every keyword must appear in the answer (case-insensitive).
- `expect_no_answer`: the documents don't cover the question, so the bot must find no sources or say it doesn't know.

Scoring is plain text matching, with no extra model call. A good set has 30–50 real customer questions, including a few the bot should decline. Re-run it after uploading documents or changing provider, model or retrieval settings, and compare `accuracy`. Each question is a real, billed provider call, and a large set can take a few minutes.

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
