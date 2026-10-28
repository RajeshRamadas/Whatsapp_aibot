# Universal AI Chatbot — runnable foundation

A **development starter**, not a production-complete platform. Supports tenant-scoped uploads (PDF, DOCX, TXT, Markdown, CSV, XLSX, JSON), OpenAI embeddings, pgvector retrieval, grounded chat and an embeddable widget.

## Getting started

1. Copy `.env.example` to `.env`; set `OPENAI_API_KEY` and a long random `ADMIN_BOOTSTRAP_TOKEN`.
2. Run `docker compose up --build`.
3. Open `http://localhost:8000/docs` for the API documentation.
4. Create a tenant:

```bash
curl -X POST http://localhost:8000/api/v1/tenants \
  -H 'Content-Type: application/json' -H 'X-Bootstrap-Token: YOUR_BOOTSTRAP_TOKEN' \
  -d '{"name":"Demo Store","allowed_origins":["http://localhost:3000"]}'
```

5. Save the generated `admin_key` and `public_key`, then upload a document:

```bash
curl -X POST http://localhost:8000/api/v1/sources \
  -H 'X-Admin-Key: YOUR_ADMIN_KEY' -F 'file=@sample.pdf'
```

6. Use `GET /api/v1/sources` with your admin key to check ingestion, then chat:

```bash
curl -X POST http://localhost:8000/api/v1/chat/YOUR_PUBLIC_KEY \
  -H 'Content-Type: application/json' -d '{"message":"What does the document say?"}'
```

7. Embed on a website (replace host and key):

```html
<script src="http://localhost:8000/widget.js" data-chatbot-key="YOUR_PUBLIC_KEY" defer></script>
```

## Architecture

- `app/main.py`: FastAPI, tenancy, parsing, ingestion, embedding, retrieval and chat.
- `widget/widget.js`: dependency-free, embeddable website widget.
- PostgreSQL + pgvector: tenant-scoped vector storage.
- Docker Compose: local development environment.

## Production gaps

- **Security:** Origin allowlisting is not authentication. Add session ownership, strong user auth, tenant quotas, rate limits, abuse prevention, file validation, malware scanning, audit logging and secrets management.
- **Structured data:** CSV/JSON/XLSX are turned into text for retrieval. Exact aggregates, SQL analysis and numerical reasoning require a dedicated read-only query engine.
- **Ingestion:** FastAPI BackgroundTasks are not durable. Replace with Redis/Celery or equivalent queue, idempotent processing, retries and versioning.
- **Retrieval:** Add OCR, table parsing, hybrid search, reranking, citation verification, relevance thresholds and evaluation tests.
- **Operations:** Add database migrations, CI, tests, monitoring, backups and deployment strategy.
- **WhatsApp:** Not yet implemented. Build a Meta Cloud API adapter with verified webhooks, deduplication, async delivery, templates and agent handoff.
- **Embeddings:** VECTOR(1536) assumes text-embedding-3-small's default dimensionality; switching dimensions requires migration and reindexing.

Do not deploy this starter to a public-facing production service without addressing these gaps.

## API

Routes: `/health`, `/api/v1/tenants`, `/api/v1/sources`, `/api/v1/chat/{public_key}`, `/widget.js`.


## Customer chat interfaces

Both interfaces use the existing `POST /api/v1/chat/{public_key}` endpoint and the same knowledge base.

### Standalone chat page

Start Docker Compose, create a tenant and upload documents as described above. Open:

`http://localhost:8000/chat?key=YOUR_PUBLIC_KEY`

The page supports a responsive layout, conversation context during the current page session, source filenames, a new-chat button, and error states. It intentionally does not persist customer conversation credentials in browser storage.

### Embeddable widget

Insert this script into a website:

```html
<script src="https://YOUR_API_HOST/widget.js"
        data-chatbot-key="YOUR_PUBLIC_KEY"
        data-label="Ask us anything"
        defer></script>
```

The widget uses an isolated Shadow DOM launcher and an iframe that loads the same standalone chat page. It does not require the host website to install JavaScript dependencies. For local testing, use `http://localhost:8000/widget.js`.

**Browser origin policy:** The iframe's chat API requests originate from the chatbot service's own origin. The existing tenant `allowed_origins` setting therefore does **not** restrict which third-party sites can embed the widget. Before public deployment, enforce allowed embedding domains using a per-tenant `Content-Security-Policy: frame-ancestors` policy, together with session security, quotas and rate limiting. The current code is a development starter and should not be exposed publicly.

### Future WhatsApp adapter

`app/channels.py` defines channel-neutral inbound/outbound message contracts. WhatsApp integration is **not yet implemented**. The next backend refactor should extract the chat orchestration from the HTTP route into a shared service; the Meta webhook adapter will then authenticate, deduplicate and enqueue messages, invoke that service and send replies through the WhatsApp Cloud API.

### Known limitations

- Conversations are currently identified by UUID only; add session-bound credentials before production.
- The standalone chat and widget are not yet deployed to a public host.
- The chatbot API performs synchronous LLM requests; use workers and concurrency controls at scale.
- No automated browser/integration tests have been run against live services.


## Development admin dashboard

Open `http://localhost:8000/admin` after starting Docker Compose.

- Create a tenant with the bootstrap token from your local `.env`, or connect an existing tenant with its admin key and public key.
- Upload PDF, DOCX, TXT, Markdown, CSV, XLSX or JSON files and refresh ingestion statuses.
- Copy the standalone chat URL or website embed snippet; open a chat preview.
- The dashboard holds credentials in **tab memory only** and does not persist them to local storage. Save generated admin keys in a secure password manager; they are shown only once.

**Security warning:** This is a developer-oriented dashboard, not a production admin console. Do not deploy `/admin` to the public internet without authenticated operator sessions, RBAC, CSRF protections, audit logging, TLS, and disabling or restricting bootstrap tenant creation. Avoid pasting production secrets into an untrusted browser. The public key entered for an existing tenant is not currently verified as belonging to the provided admin key; validate the pairing server-side before production use.

## Render staging deployment

A Render Blueprint is provided in [`render.yaml`](render.yaml), with managed PostgreSQL (pgvector), HTTPS web hosting, an upload disk, and secret environment variables. See **[Render deployment instructions](docs/RENDER_DEPLOYMENT.md)** for costs, setup, validation, security controls and public-launch blockers. Do not expose this development chatbot to public customers before adding authenticated chat sessions, rate limits and other controls described there.

## Free local test environment

See [free local testing](docs/FREE_LOCAL_TESTING.md). Docker Compose runs the API and pgvector on your own machine with no hosting fees; OpenAI API calls remain billable.

## Render free-tier trial

For a disposable online test, see [`render-free.yaml`](render-free.yaml) and [free-tier deployment instructions](docs/RENDER_FREE_TESTING.md). The existing `render.yaml` provisions **paid** infrastructure; select the free Blueprint explicitly. OpenAI API calls remain billable.
