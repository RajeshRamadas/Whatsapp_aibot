# Deploy on Render

This guide deploys the existing FastAPI chatbot, web chat, widget and developer admin UI on Render. **This is a protected staging deployment, not a fully production-hardened SaaS.**

## 1. Merge branches

Merge PR #1 (backend), PR #2 (chat UI), PR #3 (admin dashboard), then this Render deployment PR. The `render.yaml` Blueprint must be on the GitHub branch selected in Render.

## 2. Create a Render Blueprint

1. In [Render](https://dashboard.render.com), choose **New > Blueprint**.
2. Connect GitHub and select `RajeshRamadas/Whatsapp_aibot`.
3. Choose the branch containing `render.yaml`, review the proposed services and costs, then apply the Blueprint.
4. The Blueprint defines a Docker web service, persistent upload disk, and managed PostgreSQL database. **These are paid resources**; verify current Render pricing and plan availability before approval.
5. Provide `OPENAI_API_KEY` through Render's secret environment variable prompt. Never commit it to Git.
6. Render generates `ADMIN_BOOTSTRAP_TOKEN` and `ADMIN_DASHBOARD_PASSWORD`. Save both in a password manager; retrieve them securely from Render's environment settings. `ADMIN_DASHBOARD_USER` defaults to `admin`.
7. Verify `DATABASE_URL` is populated from the managed database connection string. The app normalizes Render's `postgres://` scheme to SQLAlchemy's psycopg3 scheme.
8. Deploy and wait for the `/health` health check to succeed. The app creates the pgvector extension and initial tables at startup. If extension creation is denied, check the managed PostgreSQL role's extension permissions.

The web service uses Render's HTTPS `onrender.com` URL. You can add a custom domain in Render's service settings; follow Render's DNS instructions to enable its managed TLS certificate.

## 3. Test the deployment

Replace `https://YOUR-SERVICE.onrender.com` with the service URL Render assigned.

- Health: `https://YOUR-SERVICE.onrender.com/health`
- Admin: `https://YOUR-SERVICE.onrender.com/admin` (HTTP Basic authentication using `ADMIN_DASHBOARD_USER` and `ADMIN_DASHBOARD_PASSWORD`)
- Standalone chat: `https://YOUR-SERVICE.onrender.com/chat?key=YOUR_PUBLIC_KEY`
- Widget script: `https://YOUR-SERVICE.onrender.com/widget.js`

Open `/admin` and authenticate. Create a tenant using the separate `ADMIN_BOOTSTRAP_TOKEN` from Render. Save the returned tenant admin key. Upload a test PDF, refresh the source status until `ready`, then open the chat URL and ask a question about the document.

If you use an existing tenant, provide both its admin key and public key. The current developer UI does not verify that those two keys belong to the same tenant.

## 4. Embed on a website

When creating a tenant, set its **allowed website origins** to full HTTPS origins, for example `https://example.com` (no trailing slash or path). The app uses these to build the standalone chat page's `Content-Security-Policy: frame-ancestors` directive. Only those origins (plus the chatbot service itself) may iframe the chat page.

Add to your site:

```html
<script src="https://YOUR-SERVICE.onrender.com/widget.js"
        data-chatbot-key="YOUR_PUBLIC_KEY"
        defer></script>
```

Inspect your browser console if embedding is blocked. Edit the tenant allowlist through an authenticated management API (not yet provided) or recreate the tenant with the correct origins during development.

## 5. Security checklist

- [x] Render-managed HTTPS endpoint (and TLS for verified custom domains).
- [x] OpenAI and admin secrets supplied through Render environment settings.
- [x] Database connection string injected from managed PostgreSQL.
- [x] Docker container runs as non-root.
- [x] Production mode hides API docs and adds common security response headers.
- [x] Developer admin page and tenant bootstrap require an extra HTTP Basic credential.
- [x] Chat iframe is restricted by per-tenant HTTPS origin allowlist via CSP.
- [x] Persistent upload disk configured for the single web service.
- [ ] **Blocker before public customer launch:** authenticated, session-bound chat conversations. The current UUID-only conversation identifiers are bearer tokens and are not tied to an end-user session.
- [ ] **Blocker before public customer launch:** IP/tenant quotas, rate limits, abuse controls and cost budgets for public LLM endpoints.
- [ ] Replace HTTP Basic admin protection with SSO/OIDC, role-based authorization, audit logs and CSRF-safe admin actions.
- [ ] Move document ingestion from process-local background tasks to a durable worker queue.
- [ ] Add migrations, automated tests, monitoring, alerting, backup restore drills and secret rotation.
- [ ] Scan and validate uploads, enforce retention/deletion and evaluate prompt-injection risks.
- [ ] Set up a secure tenant origin-management flow and validate admin/public key ownership.
- [ ] Review PostgreSQL backup retention and paid disk snapshots in your Render plan.

## 6. Operational caveats

- **Disk and scaling:** The persistent upload disk is mounted to one service instance. It is not shared across horizontal replicas. Do not scale the service horizontally until uploads move to object storage and ingestion uses a durable queue.
- **Database:** This is PostgreSQL + pgvector. The application currently uses `Base.metadata.create_all`; introduce Alembic migrations before evolving production schemas.
- **Model configuration:** The vector column is fixed at 1536 dimensions; use the default embedding model unless you migrate/reindex.
- **Cost:** Render service, persistent disk, managed PostgreSQL and OpenAI API usage are billed independently.
- **Security:** Render deployment does not make this starter production-ready. Do not distribute a public chat URL until the blockers above are addressed.
- **WhatsApp:** The channel contract is present, but Meta webhook verification, queueing and Cloud API delivery are future work.

## 7. Troubleshooting

- **Deploy fails to connect to DB:** Check `DATABASE_URL` in Render and whether the database is provisioned.
- **Extension permission denied:** Check whether pgvector is available to the selected managed PostgreSQL instance and user.
- **Health check fails:** Read Render service logs for import/config/database errors.
- **Uploads disappear:** Confirm the disk is mounted at `/srv/uploads` and the service is using a single instance.
- **Widget iframe blocked:** Check the tenant HTTPS origin allowlist and browser CSP console errors.
- **401 on admin:** Check the HTTP Basic user/password. The separate bootstrap token is required only when creating a tenant.
