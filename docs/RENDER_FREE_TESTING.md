# Render free-tier testing (disposable)

This is a **temporary test environment**. It is not intended for real customer data or public launch.

## Create the services

1. Merge the earlier PRs in order, then merge this PR into the resulting branch. Alternatively, select `feature/render-free-testing` for an isolated test.
2. In Render, create a **Blueprint** from this GitHub repository. Render normally reads `render.yaml` by default, so **select `render-free.yaml` as the Blueprint file path if the UI offers this option**. If it does not, use a separate testing branch with the free Blueprint saved as `render.yaml` instead. **Do not apply the existing `render.yaml` accidentally: it creates paid services.**
3. Review all proposed resources and ensure **both the web service and PostgreSQL show Free** before approving. If Render no longer offers a free database or rejects this Blueprint, stop rather than accepting paid resources.
4. Supply `OPENAI_API_KEY` as a secret in Render. The admin password and bootstrap token are generated; retrieve them from Render environment settings and store securely.
5. After deployment, open `https://YOUR-SERVICE.onrender.com/health`, then `/admin` using the Basic auth username `admin` and the generated password.
6. Create a test tenant using the separate bootstrap token, upload a small TXT/PDF, wait for ingestion to finish, then test the chat page and widget.

## What is free and what is not

- Render web service: free plan where available, subject to sleep and usage limits.
- Render Postgres: free where available, often time-limited. Confirm availability and expiry in your Render dashboard.
- HTTPS `onrender.com` hostname: included.
- **OpenAI API: NOT free by default.** Set a low project budget/alerts. These may not enforce a hard cutoff.
- There is no persistent disk on the free web service. The application removes uploaded source files after ingestion, but vectors and chat records remain in PostgreSQL until the database expires. In-flight ingestion can fail on restart/sleep.
- The `vector` extension must be available on the selected PostgreSQL plan. If database creation or extension setup fails, review the Render logs; don't pay for an upgrade without deciding first.
- Free instances have memory and inactivity limits. Upload only tiny test files; large PDFs may exceed resources.

## Security

The developer admin interface and tenant bootstrap are behind HTTP Basic auth in production mode. **Public chat remains vulnerable to abuse**: conversation IDs are not session-bound, and there is no rate limiting. Do not share the URL publicly or use private customer documents. Stop the free service when not testing. This configuration is only for a small controlled trial.

## Clean up

Delete both the test web service and database in Render when done. Deleting them destroys test data. Never rely on free-tier PostgreSQL for durable storage.
