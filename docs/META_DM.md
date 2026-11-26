# Instagram and Messenger (T10.8)

Signed text DMs from verified workspace assets enter the shared inbox and wait for
staff. Human agents can send short text replies within the conservative 24-hour
customer messaging window. OAuth account selection and delivery/read receipts
are available. Bot replies, attachments and automatic token refresh remain later slices.

## Connect an asset

Configure the Meta app's messaging permissions and subscribe its webhook to
`/webhooks/meta-dm`. Set `WHATSAPP_APP_ID` to the app that issued account tokens.
Verification uses `WHATSAPP_VERIFY_TOKEN`; signatures use
`WHATSAPP_APP_SECRET`. A signed-in workspace owner/admin can call
`POST /api/v1/meta-dm/channels` with `kind` (`messenger` or `instagram`), numeric
`external_id` and a pre-provisioned `access_token`. The server tests asset access
and webhook subscription before encrypting the token. Tokens are never returned.
Only one asset of each kind can be connected per workspace, and assets cannot be
shared by workspaces. Generic channel updates cannot replace Meta tokens without
verification. Deleting `/api/v1/meta-dm/channels/{id}` disconnects locally; the
Meta administrator must revoke provider permissions separately if desired.

Connection verifies token validity and app ownership plus a type-specific provider
field (`ig_id` for Instagram, `category` for Pages). Encryption is checked before
changing Meta subscriptions. A per-asset enrollment lease prevents concurrent
connections from undoing one another's subscription. Failed saves remove only a
new subscription; existing subscriptions and committed connections are preserved.
If cleanup cannot be confirmed, HTTP 424 asks the administrator to reconcile the
account's subscription before retrying. Provider details and tokens stay private.

## Reply guarantees

- Conversations and inbound events bind to the original channel record. Replacing
  an asset cannot inherit the previous asset's recipients, history or queued sends.
  Disconnecting fails queued replies and ignores queued events for that channel.
  While a reply is in flight, disconnect returns HTTP 409 and leaves the account
  connected. Claiming a reply and disconnecting share the tenant row lock, so a
  disconnect cannot succeed just before a new provider call starts.
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

## Dashboard controls (T10.8e)

Workspace owners/admins can open **Messaging accounts** to verify and connect a
pre-provisioned Meta account token, inspect the connected account and disconnect
it. The English/Hindi screen clears token fields after every attempt, shows
loading/empty/error states and clears private state when switching workspaces.
This uses the existing verified connection API; it does not implement OAuth.
Localized errors distinguish conflicts, in-flight replies, invalid account details,
provider permission failures, unavailable setup and unconfirmed cleanup.

Inbox filters include Instagram and Messenger. Eligible conversations permit text
replies; expired or disconnected conversations explain why sending is unavailable.
Oversized drafts are blocked before submission. Meta conversations do not offer
the unsupported hand-back-to-bot action. A live provider name is retained from the
verification result instead of being lost when subscription succeeds.

Browser acceptance covers permission failures, signed inbound messages, confirmed
staff replies, byte limits, expired windows, disconnection, viewer restrictions,
English/Hindi, axe and 320 px layout. See the
[account screen](screenshots/t10-8-meta-accounts.png).

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

## Meta authorization (T10.8g)

Apply migration `0054_meta_authorizations` and restart API/workers. Set
`META_DM_OAUTH_REDIRECT_URI` to
`https://marketingadda.com/api/backend/meta-dm/oauth/callback` and register that
exact URI in the same app identified by `WHATSAPP_APP_ID`/`WHATSAPP_APP_SECRET`.
Use a Meta app configured for Facebook Login with the Page/Instagram permissions
listed in `app/integrations/meta_oauth`. Optional `META_DM_OAUTH_CONFIG_ID` must
issue **user tokens**; system-user token configurations are unsupported. HTTPS is
required except for localhost development. Existing manual-token setup remains.

Owners/admins start a ten-minute attempt bound to their workspace, user and browser
session. The callback consumes a random one-use state and queues encrypted work.
Workers exchange the code, obtain a long-lived user token and discover up to 100
Page/Instagram accounts. Only account names and IDs reach the picker. Selection
uses that encrypted snapshot, then repeats app/type verification and enrollment.
Provider HTTP calls hold no SQL session. Role/session revocation, expiry,
cancellation and interrupted work stop enrollment; uncertain code exchange is
never retried. Terminal attempts erase their secrets; logout cascades deletion.

Register the callback and permissions, enable the worker, and check a real grant,
denial, account selection and signed inbound/staff reply with a Meta test account
before launch. Automated checks use fakes; live grants remain pending app setup.
Automatic token refresh, bot replies, media and delivery-status support remain
future T10.8 work.

### Account picker

Open **Messaging accounts → Authorize with Meta**, choose Instagram/Messenger,
grant access at Meta, then select the returned account. Authorization resumes only
in the initiating browser session/workspace. A cancelled or expired attempt needs
a new authorization. The screen displays safe permission, setup and account errors
and clears the return marker after cancellation or successful connection.

English/Hindi browser acceptance exercises denial, cancellation, account selection,
viewer restrictions, secret-free responses, axe and 320 px layout with fake Meta
providers. See [the account picker](screenshots/t10-8-meta-oauth.png).

## Delivery and read receipts (T10.8i)

Apply migration `0055_meta_receipts` before restarting API/workers. It also widens
provider message IDs to 255 characters for Instagram; downgrade refuses stored IDs
longer than the previous 128-character limit instead of truncating them. New connections
subscribe Messenger to `messages,message_deliveries,message_reads` and Instagram
to `messages,messaging_seen`. Enable the corresponding app webhook fields at Meta.
For existing assets, update their subscribed fields at Meta as well; reconnecting
creates a new asset binding and does not transfer old receipts/history.

Instagram seen events identify the message (`read.mid`). Messenger delivery events
identify messages (`delivery.mids`) or provide a watermark; reads use
`read.watermark`. Only signed, bounded metadata for connected assets is queued.
Receipt identity includes the original channel record and customer. The worker
updates confirmed outbound text only, in order `sent → delivered → read`, and
publishes the existing inbox update event. Receipts never create customer messages,
reopen the 24-hour reply window or retry a send whose outcome is unknown.

Messenger watermarks use an acceptance upper bound recorded after the send
response, or earlier MID-bearing delivery evidence. Queue creation time is never
used. A read watermark is retained per conversation so delayed delivery evidence
can confirm a read without claiming newer messages were read. Legacy messages
with no acceptance bound require MID-bearing evidence. Missing evidence can leave
a real read displayed as Sent/Delivered; the service does not guess.

Receipts that precede send persistence retry correlation for up to ten minutes;
receipt metadata is retained for seven days. Disconnect deletes that asset's receipt
queue. No additional credentials or settings are required. Automated checks use
fake signed callbacks; live field subscriptions and provider receipts still need
verification against a Meta test asset. See [inbox receipt states](screenshots/t10-8-meta-receipts.png).
