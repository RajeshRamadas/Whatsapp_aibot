# Instagram and Messenger (T10.8)

Signed text DMs from verified workspace assets enter the shared inbox and wait for
staff. Human agents can send short text replies within the conservative 24-hour
customer messaging window. OAuth account selection and delivery/read receipts
and opt-in bot text replies are available. Incoming attachments are private and
go to staff; agents can send images, PDFs, audio and MP4 video. Other formats and automatic token
refresh remain later slices.

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
Automatic token refresh and other outbound media types remain future T10.8 work.

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

## Opt-in knowledge bot (T10.8j)

Apply migration `0056_meta_bot_turns` and restart API/workers. All existing
Instagram/Messenger accounts start with `meta_bot_enabled=false`. A signed-in
owner/admin can PUT `/api/v1/meta-dm/channels/{id}/bot` with a strict boolean
`enabled`. The workspace's main bot switch must also be live. Enabling an account
affects new messages; existing staff-owned conversations need explicit hand-back.

Generation uses existing workspace AI settings, knowledge, read-only spreadsheets,
language/script policy and grounding. Bookings, payments, order lookup, custom
actions and lead capture are excluded in this slice. Every model call runs without
a SQL session. Quotas, hours, suspension, the original asset binding and the 24-hour
provider window are checked. Only confirmed prior replies enter model history.

Uncertain/blocked drafts, rate/quota limits, provider failures and oversized replies
move to staff with a localized notice. Verified text is never silently shortened.
Durable generation claims expire after ten minutes without automatic replay.
New messages supersede older queued turns/drafts. Takeover, close and per-account
disable cancel pending generations; handing back cannot revive a cancelled claim.
Disable waits for a bot send already in progress. A provider request that has
already started cannot be cancelled. Agent and bot text share the ordered sender
and receipt processing. Staff can hand back only to an enabled, connected account
with an open messaging window. No additional provider credentials are required.

Automated acceptance uses fake models and Meta delivery. Real model/Meta test-asset
checks remain deployment setup; other outbound media types and automatic token refresh remain later work.

### Bot controls

Open **Messaging accounts** as an owner/admin, connect the account, then select
**Enable bot replies**. Keep the workspace bot live in Bot settings. The account
card shows its confirmed mode; disabling routes active bot conversations to staff
and cancels queued bot work. Account setup remains available in English/Hindi.

In Inbox, **Hand back to bot** appears for an enabled account with an open window.
New staff-owned conversations stay with staff until this action is used. Taking
over again silences the bot. See [the bot and staff workflow](screenshots/t10-8-meta-bot.png).

Unconfirmed or timed-out bot delivery moves the conversation to staff. It does
not send a second message or automatically replay the uncertain send.

Explicit handoff keywords are honored at intake, before model work. A rapid
follow-up cannot supersede the request for a person.

## Incoming attachments (T10.8k)

Apply migration `0057_meta_inbound_media` and restart API/workers. Signed image,
audio and file events now enter the original account's conversation. Inbox shows
private images, audio playback and document downloads using its existing English/Hindi
media controls. Files use the existing authenticated media endpoint, workspace roles,
private object storage and `MEDIA_RETENTION_DAYS`. No new credentials are needed.

Provider download URLs are encrypted with `MASTER_KEY`, bound to the workspace and
attachment, and erased after completion. Only public HTTPS Meta CDN hosts beneath
`fbcdn.net`, `fbsbx.com` and `cdninstagram.com` are accepted. DNS is pinned with TLS
hostname verification. Redirects and compressed responses are rejected; channel
tokens/cookies are never sent to the CDN. MIME/content signatures and bounded streams
enforce JPEG/PNG up to 5 MiB and existing supported audio/documents up to 16 MiB,
subject to the lower `MEDIA_MAX_BYTES` setting. At most ten files are downloaded per
event; extra/unsupported files have an unavailable placeholder.

Every attachment message routes bot-owned conversations to staff and cancels pending
bot work, even when accompanied by a caption. Staff-owned conversations stay with
staff. This slice does not transcribe audio, interpret images or answer attachments.
The existing transcription setting is unchanged. Staff can still send text within
the messaging window, including the image/PDF replies described below.

Downloads run outside SQL sessions and check account binding, suspension and retention
before fetching and before publication. Disconnection or expired/interrupted downloads
leave unavailable placeholders; interrupted claims expire after ten minutes without
automatic replay. Worker completions publish live Inbox updates. Existing retention
cleanup deletes the private objects. Fake-provider backend/browser checks cover both
channels; real Meta attachment delivery remains a deployment check. See the
[Inbox attachment view](screenshots/t10-8-meta-media.png).

## Agent image and PDF replies (T10.8l)

In Inbox, staff can choose a supported file, review the filename and recipient,
then confirm **Send attachment**. JPEG/PNG images are limited to 5 MiB and PDFs
to 16 MiB, subject to the lower workspace `MEDIA_MAX_BYTES` setting. Send text
as a separate reply; Meta captions and other file formats are rejected explicitly.
The English/Hindi form is available only within the 24-hour messaging window.

The existing `POST /api/v1/inbox/conversations/{id}/media` endpoint requires an
agent or higher role and an idempotency key. Queueing takes ownership for staff
and cancels pending bot work. Reusing the same key returns the recorded outcome;
changing the payload under the same key returns 409. Queued files share FIFO
ordering with text. The worker rechecks binding, staff ownership, suspension and
the reply window after private storage I/O, without retaining SQL sessions.

Uploads use a verified **Page access token** and one multipart request to Meta's
`/me/messages` endpoint with `filedata`; public or presigned file URLs are not
needed, including with local storage. Original filenames remain in the private
Inbox; the provider upload uses an ASCII filename. See Meta's
[server upload contract](https://developers.facebook.com/documentation/business-messaging/instagram-messaging/features/attachment-upload).
Confirmed provider IDs enable the existing delivery/read receipts. Unconfirmed
requests become Unknown, with no automatic retry. Closing, handing back or
disconnecting before dispatch prevents a send; an HTTP call already in progress
cannot be cancelled. Stale claims become Unknown and temporary database bytes
are cleared. Private copies use the same authenticated downloads and retention.

No migration or new setting is needed. Automated checks use fake providers;
live image/PDF sending still needs configured Meta test assets and permissions.
Other outbound formats and automatic token refresh remain future slices. See
[the agent attachment flow](screenshots/t10-8-meta-agent-media.png).

## Agent audio replies (T10.8m)

The **Image, PDF, audio or video** picker accepts AAC, MP4/M4A and WAV recordings for
Instagram/Messenger. Selected audio has a local playback preview; it is uploaded
only after staff confirm the filename and recipient. Preview URLs are released
when the selection changes, sends successfully or the form closes. Captions remain
unsupported; send text separately. No automatic transcript is created.

The existing multipart endpoint normalizes common M4A/WAV MIME aliases, checks
content signatures and enforces `MEDIA_MAX_BYTES` up to 16 MiB. OGG/MP3 and other
unlisted formats are rejected. Manual audio sends use the existing Page token,
private storage, tenant/role checks, 24-hour window and staff takeover. They share
FIFO with other replies, post-staging eligibility checks, single-attempt delivery,
Unknown handling and temporary-byte cleanup. Confirmed sends receive the same
delivery/read receipts. Staff playback/downloads remain authenticated and expire
under the existing retention policy.

No migration or new credential is needed. This does not enable speech-to-text,
audio generation, microphone recording or WhatsApp audio uploads. Fake-provider
checks cover both channels and the supported MIME types; real audio delivery still
needs Meta test assets and permission validation. See
[the audio reply flow](screenshots/t10-8-meta-agent-audio.png).

Roll out compatible **workers first** and drain older worker instances before
releasing the API/web audio controls. Old workers cannot claim audio messages.
For rollback, remove the audio controls/API first and keep compatible workers
running until queued audio is drained before reverting them.
The API and workers require `ffprobe` and `ffmpeg` (included in the existing
container image). Missing validation tools return a setup error without queueing.
Validation uses bounded local processes, private temporary files, forced demuxers
and disabled external MOV references/network protocols. Files must have an audio
track, no video track, and a decodable opening frame. This also allows audio-only
MP4 files reported by browsers as `video/mp4`; clips with video follow the video
reply rules below.

Claims recheck in-flight/earlier replies using a fresh statement after acquiring
conversation/tenant locks. Early Messenger reads remain pending while a reply is
in flight. Read status still requires a safe acceptance bound; delayed MID-bearing
delivery evidence can apply the stored read watermark without guessing.

## Agent video replies (T10.8n)

The **Image, PDF, audio or video** picker accepts H.264 MP4 clips with optional AAC
audio, up to 1920×1920 and 16 MiB (or the lower `MEDIA_MAX_BYTES` limit). The local
preview plays before upload; staff confirm the filename and recipient to queue.
Selected MP4 files containing only audio keep the existing audio send behaviour.
Send text separately; other video containers/codecs and empty or undecodable tracks
are rejected.

Video uses the same multipart endpoint, private storage, verified Page token,
24-hour window, staff takeover, FIFO claim/staging fences, single-attempt send,
Unknown handling, cleanup and delivery/read receipts. Inbox playback and downloads
are authenticated, support byte ranges and expire under normal media retention.
Validation runs outside SQL sessions using forced demuxers, disabled external MOV
references/network protocols, bounded probes and opening-frame decoding. Video
processes also cap CPU and address space; the existing image includes `ffmpeg`,
`ffprobe` and `prlimit`. Missing tools return a setup error without queueing.

No migration, endpoint, setting or credential is added. Roll out compatible
**workers first**, draining old workers before releasing API/web video controls.
For rollback, remove API/web video controls first and retain compatible workers
until queued video drains. This does not add incoming video, WhatsApp video sends,
recording, generation, captions or transcription. Automatic transcription stays
disabled. Fake-provider and browser checks exercise both channels; live delivery
still requires configured Meta test assets and permissions. See
[the video reply flow](screenshots/t10-8-meta-agent-video.png).
