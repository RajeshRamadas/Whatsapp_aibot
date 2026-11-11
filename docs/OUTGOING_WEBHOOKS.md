# Outgoing workspace events

T10.2 implements API-2 and OWH-1–OWH-5. Owners/admins configure destinations in
**Developer API → Outgoing events**. Choose subscriptions, save the one-time
`whsec_…` secret in the receiving server, configure signature verification and
approve metadata sharing before enabling. New destinations start paused.

Destinations use public HTTPS on port 443 without embedded credentials/fragments.
DNS answers are checked again for every delivery and connections are pinned to a
validated address while preserving the hostname for TLS verification. Redirects
are rejected. No receiver response body is read or retained. To change a URL,
subscriptions or a lost secret, delete the destination and register it again.
Deleting a destination also deletes its secret and delivery history.

## Event contract

```json
{
  "id": "evt_<uuid>",
  "type": "lead.created",
  "created_at": "2026-11-11T12:00:00+00:00",
  "tenant_id": "<workspace UUID>",
  "data": {"conversation_id": "<uuid>", "status": "new", "version": 1}
}
```

Payloads contain IDs and status metadata. They exclude message text, customer names,
phone numbers, payment descriptions, credentials, signature headers and raw provider
errors. Fetch authorized details separately through the appropriate API, for example
public `/messages/{id}` with `conversations:read` or `/leads/{conversation_id}` with
`leads:read`. Booking/payment/campaign endpoints retain their existing role rules.

| Subscription | Trigger / data |
|---|---|
| `message.received` | A customer message is persisted; message/conversation IDs, channel and kind. |
| `message.sent` | A send is confirmed, or a website reply becomes available. Campaign sends carry campaign/recipient IDs and an `is_test` flag instead of a local message ID. |
| `conversation.handoff` | A conversation moves from bot control to the human team. |
| `conversation.closed` | A conversation becomes closed. |
| `lead.created` | A new lead is captured; conversation ID, status and version. |
| `lead.updated` | Lead fields/status/notes actually change; no values or notes are sent. |
| `booking.created` | A booking becomes confirmed; temporary holds do not emit this event. |
| `payment.completed` | A payment becomes verified as paid. |
| `campaign.completed` | All campaign recipients finish processing; completion does not imply every send succeeded. |

Internal notes, no-op assignments and repeated delivery receipts do not produce
completion events. Source changes and delivery rows commit together; rolling back
the source also removes its event. Only currently enabled/subscribed destinations
receive a new event. Registering a destination does not backfill old activity.

## Signature verification

Each request is JSON with `X-Event-Id`, `User-Agent: MarketingAddaWebhooks/1.0` and:

```text
X-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256>
```

The signed bytes are `<timestamp>.<raw request body>`. Verify the original bytes
before parsing JSON; reserializing JSON changes the signature. Compare in constant
time and reject signatures more than five minutes old. For example:

```python
import hashlib
import hmac
import json
import re
import time


def verify_event(raw_body: bytes, signature_header: str, secret: str):
    match = re.fullmatch(r't=([0-9]{1,12}),v1=([a-f0-9]{64})', signature_header)
    if not match or abs(time.time() - int(match[1])) > 300:
        raise ValueError('Invalid or stale signature')
    signed = match[1].encode() + b'.' + raw_body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, match[2]):
        raise ValueError('Invalid signature')
    return json.loads(raw_body)
```

Persist the event ID with your receiving work transaction, accept each ID once,
return 2xx promptly, and process slow work asynchronously. Delivery is at least
once; ordering is not guaranteed. A timeout can happen after your server accepted
an event, so duplicate IDs must be harmless. Each retry/resend gets a fresh
signature timestamp but preserves the payload and original event ID.

## Delivery policy and operations

The arq dispatcher gives each request a 10-second total budget, including DNS.
Any 2xx is success. Network failures, 408, 429 and 5xx retry with jitter and backoff
(approximately 5 s, 30 s, 2 min, 10 min, 30 min, then hourly), respecting Retry-After
within the original 24-hour window. Other non-2xx responses fail immediately.
This specific OWH-4 policy supersedes the generic five-attempt integration default.
Worker crashes release a two-minute lease; retries retain the same event identity.

All destinations share the tenant's `OUTGOING_WEBHOOK_REQUESTS_PER_MINUTE` quota
(default 60). Redis outages postpone delivery without bypassing quotas. Destination
count is limited by `OUTGOING_WEBHOOK_MAX_ENDPOINTS` (default 10). Configure
`MASTER_KEY` and retain old key versions while rotating encryption keys.

A successful delivery clears the failure streak. After three days without a
success following a failure, the endpoint is disabled, pending requests fail and
an owner/admin notification is staged through the existing transactional email
backend. An unavailable email configuration leaves notification pending and does
not block other deliveries. Actual email delivery follows the configured backend.
Paused/deleted destinations stop queued work; already in-flight requests may finish.
Suspended workspaces do not dispatch new requests. Re-enabling requires approval
again; cancelled or expired events are not automatically replayed.

The delivery history contains the last 30 days of metadata-only request bodies,
response codes, safe error codes and attempt timestamps. Resend explicitly creates
a new delivery window for the same event ID. Failed jobs also appear in platform
operations. Response bodies and signing headers are excluded from the log.

Administrative endpoints (owner/admin role, existing cookie/CSRF or role-key auth):
`GET/POST /api/v1/webhook-endpoints`, `PATCH/DELETE /{endpoint_id}`,
`GET /{endpoint_id}/deliveries?limit=30&cursor=<id>`, and
`POST /{endpoint_id}/deliveries/{delivery_id}/resend` with
`{"confirm":true,"request_key":"<unique UUID>"}`. Reusing that request key for the
same event returns the previously created delivery; using it for another event
conflicts. The prefix is `/api/backend/webhook-endpoints` through the dashboard
proxy. Fine-grained public resource keys do not manage webhook destinations.
