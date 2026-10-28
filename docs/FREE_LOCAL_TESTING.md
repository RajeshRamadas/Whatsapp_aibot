# Nearly-free local testing

**Cost:** $0 for hosting and PostgreSQL on your own machine. OpenAI API usage is billed separately. Docker Desktop licensing may apply to some organizations; Docker Engine is free to use under its license.

## Start

1. Install Git and Docker with Compose.
2. Merge the preceding PRs in order (backend, chat, admin, Render config, then this fix), or check out the `feature/free-local-testing` branch to test without merging.
3. In the repository root, copy `.env.example` to `.env`. Set `OPENAI_API_KEY` to your own API key and `ADMIN_BOOTSTRAP_TOKEN` to a long random string. Never commit `.env`.
4. Run `docker compose up --build -d`.
5. Check `docker compose ps` and `curl http://localhost:8000/health`.
6. Open `http://localhost:8000/admin`, create a chatbot, upload a small text/PDF file, refresh until ready, then test `http://localhost:8000/chat?key=YOUR_PUBLIC_KEY`.
7. Stop with `docker compose down` (keeps database and upload volumes). `docker compose down -v` **deletes test data**.

The API binds to `127.0.0.1:8000` so it is not directly exposed on your LAN. The Compose file sets the application's PORT to 8000 (Render uses 10000 by default). The PostgreSQL port is not published.

## Temporary public HTTPS test (optional)

If you need to test the widget on a remote website, use a trusted temporary tunnel such as Cloudflare Tunnel (`cloudflared tunnel --url http://localhost:8000`). The tunnel makes your local API accessible on the public internet. **Do not use this for sensitive data or customer traffic**: the starter lacks production chat authentication and rate limits. Prefer a local-only test page, or add temporary tunnel access controls and immediately shut down the tunnel after testing.

To test the embed on a site, use its temporary HTTPS tunnel URL as the widget script host and set the site's HTTPS origin in the tenant's allowed origins. For localhost website development, you can use a local site and local API instead.

## Cost controls

- Start with one small text file and a few short chats.
- Set an OpenAI project budget/alerts in the OpenAI Platform; a budget alert is **not** a guaranteed hard spending cap.
- Do not publish the tunnel URL widely.
- Rotate keys if accidentally shared.
- For truly $0 AI API cost, a mock response mode would need to be implemented; this starter calls OpenAI for ingestion and chat.

## Known limitations

This is a development environment only. The app uses background tasks, UUID-only conversation identifiers, and an admin interface intended for trusted operators. No external deployment or live OpenAI tests have been performed.
