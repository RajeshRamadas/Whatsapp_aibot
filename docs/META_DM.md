# Instagram and Messenger (T10.8)

Signed text DMs from verified workspace assets enter the shared inbox and wait for
staff. Human agents can send short text replies within the conservative 24-hour
customer messaging window. Bot replies, attachments, receipts, OAuth onboarding
and automatic token refresh remain later slices.

## Connect an asset

Configure the Meta app's messaging permissions and subscribe its webhook to
`/webhooks/meta-dm`. Verification uses `WHATSAPP_VERIFY_TOKEN`; signatures use
`WHATSAPP_APP_SECRET`. A signed-in workspace owner/admin can call
`POST /api/v1/meta-dm/channels` with `kind` (`messenger` or `instagram`), numeric
`external_id` and a pre-provisioned `access_token`. The server tests asset access
and webhook subscription before encrypting the token. Tokens are never returned.
Only one asset of each kind can be connected per workspace, and assets cannot be
shared by workspaces. Generic channel updates cannot replace Meta tokens without
verification. Deleting `/api/v1/meta-dm/channels/{id}` disconnects locally; the
Meta administrator must revoke provider permissions separately if desired.

## Reply guarantees

- Conversations and inbound events bind to the original channel record. Replacing
  an asset cannot inherit the previous asset's recipients, history or queued sends.
  Disconnecting fails queued replies and ignores queued events for that channel.
- The signed provider timestamp determines the reply window. Delayed or older
  events cannot reopen or move it backwards. Missing, malformed or future timestamps
  may be retained as messages but never open a sending window.
- The reply endpoint and worker enforce tenant ownership, connected credentials,
  the messaging window and conservative UTF-8 text budgets: 1,000 bytes for
  Instagram and 2,000 for Messenger. Text is never silently shortened.
- Workers serialize replies per conversation, recheck current eligibility after
  claiming a message and close database sessions before Meta HTTP calls. As with
  other channels, a provider call already in progress cannot be cancelled.
- Confirmed sends become `sent` with a provider ID. Unconfirmed requests become
  `unknown`, never automatically retried. Ineligible unsent messages become
  `failed`. Stale `sending` messages become `unknown` and publish inbox updates.
- WhatsApp retains its own dispatcher; Meta replies never enter that sender.

## Migration and acceptance

Apply migrations `0052_meta_dm_events` and `0053_meta_dm_binding` before restarting
API/workers. Existing Meta records remain unbound and cannot send: guessing their
original asset could deliver to the wrong recipient. A fresh signed inbound message
creates an asset-bound conversation; old history remains available. Rollback to the
older unique identity constraint refuses conflicting histories across replacement
assets instead of deleting them; reconcile those identities before downgrading.

Automated regression checks use real Postgres/Redis and fake providers. They cover
replacement/disconnection, timestamps, eligibility, text limits, session-free HTTP,
reply ordering, unknown delivery and stale inbox events. Real Meta test assets,
production permission approval and live delivery verification are still required
before customer launch. Never paste access tokens into chat or commit them.
