# Instagram and Messenger (T10.8a foundation)

This phase receives signed text DMs and records them in the shared inbox. It does **not**
connect a Meta Page/Instagram account through OAuth, send replies, or run bot responses.

- Subscribe the Meta app to the appropriate Messenger/Instagram messaging webhook events.
- Webhook URL: `/webhooks/meta-dm`. Verification uses `WHATSAPP_VERIFY_TOKEN`;
  signatures use `WHATSAPP_APP_SECRET` from the same Meta app.
- An inbound recipient ID must already match a workspace-owned `channels` record
  with kind `messenger` or `instagram`. Unknown IDs are acknowledged and ignored.
- Duplicate provider message IDs are recorded once. The worker publishes new inbound
  text messages to the tenant's inbox and SSE stream. Echoes, attachments and
  delivery receipts are ignored in this phase.
- This is not ready for live Meta connection: T10.8b must add authenticated Meta
  connection/disconnection, token encryption/refresh, subscription verification,
  outbound sends, messaging-window enforcement, and attachment/status handling.
  T10.8c should add channel setup and inbox UX with full browser acceptance.

No extra environment variables are introduced; deploy migration
`0052_meta_dm_events` before restarting API and workers.

## T10.8b: Verified connection API (first slice)

Signed-in workspace admins can call `POST /api/v1/meta-dm/channels` with
`kind` (`messenger` or `instagram`), numeric `external_id`, and `access_token`.
The server checks the asset ID and webhook subscription through Meta Graph before
encrypting and storing the token. The token is never returned in the API response.
One asset of each kind is allowed per workspace; an asset cannot be claimed by
multiple workspaces. `DELETE /api/v1/meta-dm/channels/{id}` disconnects it locally
and immediately stops webhook routing. It does not revoke the token at Meta or
unsubscribe the app: the Meta administrator should revoke permissions if desired.

This first slice does not implement OAuth code exchange or automatic token refresh;
administrators supply a pre-provisioned Page/Instagram access token with the
required messaging and subscription permissions. The next PR adds delivery and
messaging-window checks; UI onboarding and production Meta permission review
remain outstanding. Use a real Meta test asset before production enablement.

## T10.8c: Agent text replies

The existing inbox reply endpoint accepts Messenger and Instagram conversations
only when a channel is connected and the last customer inbound message is less
than 24 hours old. A dedicated worker rechecks channel ownership, tenant
suspension, human handoff, and the 24-hour window immediately before sending
via Meta Graph. Accepted messages are marked `sent` with the provider ID;
unconfirmed sends become `unknown` and are never automatically retried, to
avoid duplicate customer messages. Expired/disconnected sends become `failed`.
WhatsApp delivery remains on its existing dedicated dispatcher.

This release supports **human-agent text replies only**, not bot replies,
attachments, templates, or extended messaging windows. Production Meta
permissions, live Meta integration tests, and channel onboarding UX are still
required before enabling it for customers.
