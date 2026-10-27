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
