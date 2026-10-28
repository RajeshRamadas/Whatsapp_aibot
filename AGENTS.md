# Working on this repo

Instructions for any AI coding agent (Codex, Claude Code, Gemini CLI, Cursor, Copilot and others) and for people. `CLAUDE.md`, `GEMINI.md` and `.github/copilot-instructions.md` point here; keep this file the only copy of these rules.

A multi-tenant AI chatbot for WhatsApp and websites. It is FastAPI + PostgreSQL/pgvector, with OpenAI, Anthropic and Gemini behind `app/llm.py`.

## Where the plan lives

- `docs/IMPLEMENTATION_PLAN.md`: ordered tasks (`T<milestone>.<n>`), conventions (§3) and definition of done (§4). When asked to implement a task, follow its §2 prompt; §2.1 explains how to use it with different AI tools.
- `docs/REQUIREMENTS.md`, `docs/DESIGN.md`, `docs/INTEGRATIONS.md` and `docs/UX_DESIGN.md`: the requirement IDs that tasks reference.

## Commands

```bash
# Test database (once)
docker run -d --name chatbot-test -p 5433:5432 -e POSTGRES_HOST_AUTH_METHOD=trust \
  -e POSTGRES_DB=chatbot_test pgvector/pgvector:pg16

pip install -r requirements.txt -r requirements-dev.txt
pytest -q                                    # all tests; needs the database above
ruff check --line-length 120 app tests       # lint
docker compose up --build                    # run the app locally (needs .env, see .env.example)
```

## Rules

- Do one task per branch and PR. Note anything out of scope in the PR description; don't fix it.
- Every tenant-scoped query filters by the tenant from the request context, never from the request body.
- Never call AI providers or other outside services while holding a database session, or inside a request handler except "test connection".
- Tests use the fakes in `tests/conftest.py` (`FakeChat`, `FakeEmbedder`, the `sent` fixture). They must not need API keys or internet access.
- Each new endpoint gets tests for: happy path, auth (401/403), tenant isolation (404) and validation (422).
- Never log secrets, phone numbers or message text.
- Update `README.md`, `.env.example` and the `docs/` status table when behaviour or settings change.
