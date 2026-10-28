# Integration requirements

| | |
|---|---|
| Status | Draft for review |
| Last updated | 2026-10-28 |
| Related | [REQUIREMENTS.md](REQUIREMENTS.md) (requirement IDs), [DESIGN.md](DESIGN.md) (architecture) |

This document lists every outside system the product connects to: what it is for, how it authenticates, what data moves, and how failures are handled. Every connector also follows the shared rules in §2.

## 1. Overview

| # | Integration | Purpose | Direction | Release | Requirement IDs |
|---|---|---|---|---|---|
| 3.1 | Meta WhatsApp Cloud API | Customer messaging, templates, onboarding | Both | R1 (exists in part) | CH-1, CH-2, INB-4, CMP-1–4 |
| 3.2 | AI providers (OpenAI, Anthropic, Gemini) | Answers, embeddings, rewriting, transcription | Outbound | R1 (exists) | BOT-2, BOT-3, CH-5 |
| 3.3 | Razorpay | Subscriptions, GST invoices, payment links | Both | R1 (subscriptions), R2 (payment links) | BIL-2, BIL-3, PAY-1 |
| 3.4 | Email delivery | Verification, invites, alerts, lead notifications | Outbound | R1 | ACC-1, ACC-3, BIL-4, LEAD-3 |
| 3.5 | Google sign-in | Admin login | Both | R1 | ACC-1 |
| 3.6 | Object storage (S3 / R2) | Files and media | Outbound | R1 | KN-1, CH-5 |
| 3.7 | Website crawling | Knowledge from web pages | Outbound | R2 | KN-2 |
| 3.8 | OCR and speech-to-text | Scanned files, voice notes | Outbound | R2 | KN-4, CH-5 |
| 3.9 | Google Sheets | Send leads to a sheet | Outbound | R2 | LEAD-3 |
| 3.10 | Google Calendar | Free slots and bookings | Both | R2 | APT-1 |
| 3.11 | CRMs (Zoho, HubSpot, later Salesforce) | Sync leads and contacts | Outbound (R2), both (R3) | R2–R3 | LEAD-3 |
| 3.12 | E-commerce (Shopify, WooCommerce) | Order status, product data | Inbound queries | R3 | BOT-6 |
| 3.13 | Generic HTTP connector ("custom action") | Any business system as a bot tool | Outbound | R3 | BOT-6 |
| 3.14 | Google Drive, Notion | Knowledge sync | Inbound | R3 | KN-6 |
| 3.15 | Instagram, Facebook Messenger | More channels | Both | R3 | CH-4 |
| 3.16 | GreenPlot | Pilot: resident support with live dues, visitors, maintenance | Both | R2 pilot | BOT-6, PAY-1 |
| 4 | Public REST API and outgoing webhooks | Customers' own systems | Both | R3 | API-1, API-2 |
| 5 | Monitoring (Sentry, metrics) | Errors and alerts | Outbound | R1 | NFR-9 |

## 2. Rules every integration follows

| ID | Rule |
|---|---|
| INT-1 | **One connector interface.** Each integration is a module under `app/integrations/<name>/`, with `connect()`, `test()`, `disconnect()` and its actions. The UI shows each connector's status (connected, needs attention, disconnected), its last successful call and its last error. |
| INT-2 | **Credentials are encrypted.** OAuth tokens, API keys and webhook secrets are stored with per-tenant envelope encryption (DESIGN D6). They are never logged, never returned by the API after saving, and are shown only as the last 4 characters. |
| INT-3 | **Least access.** OAuth connectors request the narrowest scopes that work (for example read-only for Drive sync, append-only for Sheets where available). Requested scopes are listed in the connect screen. |
| INT-4 | **Token refresh.** Access tokens are refreshed before expiry. A failed refresh marks the connector "needs attention" and emails the tenant's admins; dependent features degrade gracefully (for example leads stay in the product and are synced later). |
| INT-5 | **Inbound webhooks are verified.** Every inbound webhook is signature-checked (HMAC with the provider's secret, compared in constant time) before it is parsed, and rejected with 401 otherwise. Raw payloads are stored for 14 days for debugging. |
| INT-6 | **Process each event once.** Inbound events are deduplicated by the provider's event or message ID. Outbound calls that create something send an idempotency key when the provider supports one. |
| INT-7 | **Work in the background.** Calls to third parties run in workers (DESIGN §3), never in a user's request, except a connector's "Test connection" button. |
| INT-8 | **Retry with backoff.** Network errors, timeouts, 429 and 5xx responses are retried with exponential backoff and jitter (5 attempts over about 10 minutes, honouring `Retry-After`). 4xx errors other than 408 and 429 are not retried. After the last attempt, the job goes to a dead-letter list visible in platform admin, and the tenant sees the failure in the connector's status. |
| INT-9 | **Respect rate limits.** Each connector has a per-tenant rate limit in Redis matching the provider's published limit, so one tenant can't exhaust a shared app's quota. |
| INT-10 | **Timeouts.** Default 10 s connect / 30 s read for third-party calls; 60 s for AI calls (`LLM_TIMEOUT_SECONDS`). Bot tool calls that serve a live customer have a 8 s budget, after which the bot says it will follow up and hands off. |
| INT-11 | **Audit.** Connecting, disconnecting, scope changes and every bot tool call (inputs, result status, duration) go to the audit log. |
| INT-12 | **Data minimisation.** Only the fields an integration needs are sent. Customer phone numbers and names go to a CRM or sheet only when the tenant enables that sync. |
| INT-13 | **Test before live.** Each connector has a sandbox or test mode where the provider offers one, and contract tests in CI against recorded responses. |
| INT-14 | **Versioned.** Provider API versions are pinned in config (for example `WHATSAPP_GRAPH_VERSION`) and reviewed each quarter against the providers' deprecation schedules. |

## 3. Integrations

### 3.1 Meta WhatsApp Cloud API

**Purpose:** send and receive customer messages, connect business numbers, manage templates and campaigns.

**Today:**
- One platform-wide access token.
- A webhook with signature check and dedupe (`app/whatsapp.py`).
- Text, button and list replies answered.
- Free-form text replies sent.

**Requirements:**

| ID | Requirement | Release |
|---|---|---|
| WA-1 | **Embedded Signup.** The web app opens Meta's signup flow. The API exchanges the returned code for a business integration token, stores it encrypted per channel, subscribes the app to the business's WhatsApp Business Account, and registers the phone number. | R1 |
| WA-2 | **Webhook routing.** One endpoint for all tenants, routed by `metadata.phone_number_id`. Subscribed fields: `messages` (messages and statuses), `message_template_status_update`, `phone_number_quality_update`, `account_update`. | R1 |
| WA-3 | **Message types out.** Text, reply buttons (up to 3), list messages (up to 10 rows), images, documents, location, and templates with variables. | R1 (text, buttons, lists, templates), R2 (media) |
| WA-4 | **Message types in.** Text, button and list replies (exists); images, documents, audio (voice notes), location and contacts are stored, and audio is transcribed (§3.8). | R2 |
| WA-5 | **Delivery status.** `sent`, `delivered`, `read` and `failed` statuses update `messages.delivery_status`; failures show the error code and reason in the inbox. | R1 |
| WA-6 | **24-hour window.** Free-form messages only within 24 hours of the customer's last message; outside it, only approved templates (INB-4). | R1 |
| WA-7 | **Templates.** Create, submit, list and delete templates through the Graph API. Template status updates (approved, rejected with reason, paused) are shown to the tenant. | R2 |
| WA-8 | **Opt-in and opt-out.** Template and campaign sends go only to contacts with recorded opt-in. "STOP" (and localised equivalents the tenant configures) records opt-out and confirms it to the customer. | R2 |
| WA-9 | **Throughput.** Sends are throttled per phone number to its messaging limit tier and quality rating. A drop in quality rating pauses running campaigns and alerts the tenant. | R2 |
| WA-10 | **Mark as read and typing indicator** on inbound messages the bot is answering, so customers see that it's working. | R2 |
| WA-11 | **Media handling.** Inbound media is downloaded with the channel token and stored in object storage. Outbound media is uploaded to Meta or sent by HTTPS link. | R2 |

- **Auth:** per-channel business token (Bearer). The app secret is used for webhook signatures, and the verify token for the subscription handshake.
- **Failure handling:**
  - A send failure with a permanent error code (for example, re-engagement required, or the recipient not on WhatsApp) is not retried, and is shown in the inbox.
  - Temporary errors are retried (INT-8).
- **Dependency:** WhatsApp onboarding needs Meta Tech Provider status and app review, or a BSP partner (REQUIREMENTS §10, DESIGN D11).
- **Cost:** Meta charges the business for template messages (by category and country). Per REQUIREMENTS BIL-5, these are billed by Meta to the business directly, and the platform shows estimated costs per campaign.

### 3.2 AI providers

**Purpose:**
- answers, follow-up rewriting and tool use (chat models)
- embeddings
- transcription and image reading (R2)

**Today:**
- OpenAI, Anthropic and Gemini chat behind `ChatProvider`.
- OpenAI and Gemini embeddings (`app/llm.py`).
- Claude calls opt in to Anthropic's server-side refusal fallback.

| ID | Requirement | Release |
|---|---|---|
| AI-1 | **Tool use** through a provider-neutral `run(model, system, messages, tools)` mapped to each provider's function-calling format (DESIGN §6). | R1 (first tool: `capture_lead`) |
| AI-2 | **Usage capture.** Every call records provider, model, input and output tokens, latency and cost as a `usage_event` (BIL-4). | R1 |
| AI-3 | **Fallback.** On provider outage (repeated 5xx or timeouts), retry, then fall back to the tenant's configured secondary provider if set, else hand off. A safety refusal returns a polite refusal (exists for Claude). | R1 |
| AI-4 | **Platform keys by default.** The platform's own keys are used and the cost is included in the plan. Business-plan tenants may bring their own key (stored encrypted), in which case their usage isn't counted against quota. | R1 (platform keys), R2 (own keys) |
| AI-5 | **Data handling.** Use each provider's business/API terms under which inputs aren't used for training, and disclose the providers as sub-processors in the privacy policy. | R1 |
| AI-6 | **Speech-to-text and vision** for voice notes and images (§3.8). | R2 |

- **Auth:** API keys in the environment, or encrypted per tenant for bring-your-own-key.
- **Testing:** fake providers in CI (exists), plus a nightly accuracy run against real models (DESIGN §15).

### 3.3 Razorpay

**Purpose:** collect subscription payments from tenants (R1) and let the bot send payment links to customers (R2).

| ID | Requirement | Release |
|---|---|---|
| RZ-1 | **Plans and subscriptions** created through the Razorpay Subscriptions API; checkout in the web app for cards, UPI Autopay and netbanking mandates. | R1 |
| RZ-2 | **Webhooks:** `subscription.activated`, `subscription.charged`, `subscription.pending`, `subscription.halted`, `subscription.cancelled`, `payment.failed`. Verified with `X-Razorpay-Signature` (HMAC-SHA256 of the raw body with the webhook secret), deduplicated by event ID, and used to update `subscriptions` (DESIGN §9). | R1 |
| RZ-3 | **GST invoices.** Generated by the platform from subscription charges, with the tenant's GSTIN and billing address collected at checkout. | R1 |
| RZ-4 | **Payment links for tenants' customers.** The bot creates a payment link (amount, description, customer contact, expiry) on the *tenant's own* Razorpay account, connected with the tenant's API key. A `payment_link.paid` webhook confirms payment in the chat. | R2 |
| RZ-5 | **Reconciliation.** A daily job compares Razorpay settlements with local records and flags mismatches in platform admin. | R1 |

- **Auth:**
  - Platform key ID and secret for subscriptions.
  - Each tenant's own key ID and secret for payment links, stored encrypted.
- **Testing:** Razorpay test mode in staging; recorded webhook payloads in CI.
- **Later:** Stripe for customers outside India, behind the same `BillingProvider` interface (DESIGN D10).

### 3.4 Email delivery

**Purpose:** transactional email, such as verification, invites, password reset, quota warnings, handoff and lead alerts, and invoices.

| ID | Requirement | Release |
|---|---|---|
| EM-1 | Send through a transactional email service (for example Amazon SES Mumbai, Postmark or Resend) behind an `EmailSender` interface. | R1 |
| EM-2 | SPF, DKIM and DMARC configured on the sending domain. | R1 |
| EM-3 | Bounces and complaints, received by webhook, suppress further email to that address. | R1 |
| EM-4 | Templates for every email, in English (Hindi in R2), with plain-text versions. | R1 |
| EM-5 | Users choose which notification emails they get (handoffs, leads, daily summary). | R2 |

### 3.5 Google sign-in

| ID | Requirement | Release |
|---|---|---|
| GS-1 | OpenID Connect with Google, using the `openid email profile` scopes only. Accounts are matched by verified email; the first login with Google on an existing email asks for that account's password once to link them. | R1 |

### 3.6 Object storage

| ID | Requirement | Release |
|---|---|---|
| OS-1 | S3-compatible API (AWS S3 Mumbai or Cloudflare R2; MinIO in development). Private bucket; keys `tenant/<tenant_id>/<kind>/<id>`. | R1 |
| OS-2 | Uploads go straight from the browser to storage through short-lived presigned URLs (max 15 minutes), then the API verifies size, type and checksum. Downloads use presigned URLs valid for 5 minutes. | R1 |
| OS-3 | Server-side encryption on; lifecycle rules delete media past the tenant's retention period (NFR-8). | R1 |

### 3.7 Website crawling

| ID | Requirement | Release |
|---|---|---|
| WEB-1 | Fetch a single URL, or crawl from a start URL within the same domain, limited by page count (plan limit) and depth. | R2 |
| WEB-2 | Obey `robots.txt`; identify with a clear user agent; at most 1 request per second per site. | R2 |
| WEB-3 | Extract the main content (strip navigation, footers and scripts), keeping headings for chunking. | R2 |
| WEB-4 | Re-crawl daily, weekly or manually; re-index only pages whose content checksum changed; remove pages that now return 404. | R2 |
| WEB-5 | Refuse private and internal addresses (localhost, private IP ranges, cloud metadata endpoints), to prevent server-side request forgery. | R2 |

### 3.8 OCR and speech-to-text

| ID | Requirement | Release |
|---|---|---|
| OCR-1 | Pages of PDFs with no text layer, and uploaded images, are read with OCR (a vision-capable model, or a dedicated OCR service). The result is chunked like any other text, with page numbers. | R2 |
| STT-1 | Voice notes are transcribed with automatic language detection, covering at least the languages in BOT-2. Candidates: OpenAI or Gemini transcription, or an Indian-language specialist such as Sarvam AI; choose by measured accuracy on Indian-language samples. | R2 |
| STT-2 | The transcript is stored with the message and shown in the inbox; the bot answers the transcript. Audio over 5 minutes is not transcribed and goes to an agent. | R2 |

OCR pages and audio minutes are metered as `usage_event`s (BIL-4).

### 3.9 Google Sheets (lead export)

| ID | Requirement | Release |
|---|---|---|
| GSH-1 | The tenant connects a Google account (OAuth) and picks a spreadsheet and tab. The narrowest Sheets scope that allows appending is used. | R2 |
| GSH-2 | Each new or updated lead is appended as a row with the tenant's lead fields, conversation link and timestamp. Updates append a new row (no in-place edits) to avoid overwriting the tenant's own changes. | R2 |
| GSH-3 | Failed writes are retried (INT-8). Leads always stay in the product, so nothing is lost if the sheet is unavailable. | R2 |

### 3.10 Google Calendar (appointments)

| ID | Requirement | Release |
|---|---|---|
| GCAL-1 | Connect one or more calendars per service or staff member (OAuth, calendar events scope). | R2 |
| GCAL-2 | Free slots = the tenant's working hours and slot length, minus busy times from the free/busy query, minus bookings held in the product. | R2 |
| GCAL-3 | Booking creates an event with the customer's name and phone and a note linking to the conversation. Cancelling or rescheduling in the chat updates the event. | R2 |
| GCAL-4 | A slot is held for 5 minutes while the customer confirms, so two customers can't book it. Before creating the event, free/busy is re-checked. | R2 |
| GCAL-5 | Without a connected calendar, bookings use the product's own slots (APT-1). | R2 |

### 3.11 CRMs

| ID | Requirement | Release |
|---|---|---|
| CRM-1 | Zoho CRM and HubSpot: create or update a lead or contact (matched by phone, then email) when a lead is captured; map lead fields to CRM fields in the connect screen. | R2 |
| CRM-2 | Add the conversation summary and link as a note or activity. | R2 |
| CRM-3 | Two-way status sync (CRM status changes update the product's lead status) by CRM webhooks where available. Salesforce added. | R3 |
| CRM-4 | Before the native connectors exist, the generic webhook (§4) covers any CRM through tools such as Zapier or Make. | R1 |

### 3.12 E-commerce (order status)

| ID | Requirement | Release |
|---|---|---|
| EC-1 | Shopify (custom app, read-only orders and products scopes) and WooCommerce (REST API keys, read-only). | R3 |
| EC-2 | Tool `lookup_order(order_number, phone)` returns status, items, fulfilment and tracking, **only when the phone number on the order matches the WhatsApp number asking** (or a one-time code sent to the order's phone or email is confirmed). | R3 |
| EC-3 | Product catalogue sync into knowledge (titles, descriptions, prices, stock), re-synced daily or on product webhooks. | R3 |

### 3.13 Generic HTTP connector ("custom action")

Lets a business expose any of its own systems to the bot as a tool, without code. This is what makes the product work with "any data".

| ID | Requirement | Release |
|---|---|---|
| HTTP-1 | The tenant defines an action with: a name and a description for the model; input parameters (name, type, description, required, validation pattern); HTTP method and URL template; headers; auth (none, API key header, Bearer token, Basic, or OAuth client credentials); and which response fields the bot may use, as JSON paths. | R3 |
| HTTP-2 | **Identity check per action:** "no check", "phone must match a response field", or "one-time code to the customer". Required for any action that returns personal data. | R3 |
| HTTP-3 | Actions are read-only by default. Actions that change data (POST, PUT or DELETE) need an explicit "allow changes" setting and, optionally, the customer's confirmation in the chat before the call. | R3 |
| HTTP-4 | URLs must be HTTPS, on public addresses (same SSRF rules as WEB-5). Responses are capped at 100 KB and 8 s (INT-10). | R3 |
| HTTP-5 | A "Test action" screen runs the action with sample inputs and shows the raw response and what the bot would see. | R3 |
| HTTP-6 | Response data passed to the model is marked as untrusted data, and only the fields selected in HTTP-1 are passed. | R3 |

### 3.14 Google Drive and Notion (knowledge sync)

| ID | Requirement | Release |
|---|---|---|
| SYNC-1 | Drive: the tenant picks folders. Files are re-indexed when they change (Drive changes feed). Google Docs, Sheets and Slides are exported to supported formats. Read-only scope. | R3 |
| SYNC-2 | Notion: the tenant shares chosen pages with the integration. Pages are converted to Markdown with headings kept. Changes are polled. | R3 |
| SYNC-3 | Removing a file or page from the shared folder removes it from knowledge at the next sync. | R3 |

### 3.15 Instagram and Facebook Messenger

| ID | Requirement | Release |
|---|---|---|
| META-1 | Connect through the same Meta app; receive and reply to DMs within each channel's messaging window; route into the same inbox with the channel shown. | R3 |

### 3.16 GreenPlot (pilot customer)

GreenPlot ([repo](https://github.com/RajeshRamadas/Greenplot)) manages residential plotted layouts and already has a FastAPI API with residents, dues, visitors and maintenance. It is the first real use of tools with live data.

| ID | Requirement | Release |
|---|---|---|
| GP-1 | Configure GreenPlot as one tenant per layout association, with GreenPlot's notices, rules and FAQs as knowledge. | R2 pilot |
| GP-2 | Tools, built with the generic HTTP connector (§3.13) against GreenPlot's `/api/v1`: `my_dues` (balance and due dates), `visitor_preapprove` (name, date), `raise_maintenance_request` (category, description, photo), `request_status` (by ticket number). | R2 pilot |
| GP-3 | Identity: the resident's WhatsApp number must match a resident record in GreenPlot (HTTP-2), using a service account with only these permissions. | R2 pilot |
| GP-4 | Dues payment links use GreenPlot's payment flow, or Razorpay payment links (RZ-4) if GreenPlot doesn't expose one. | R2 pilot |
| GP-5 | GreenPlot notices can be sent as WhatsApp templates to opted-in residents (CMP), replacing GreenPlot's current logging-only WhatsApp adapter. | R2 pilot |

## 4. Public API and outgoing webhooks

**Public REST API (API-1)**

| ID | Requirement |
|---|---|
| PAPI-1 | Base path `/api/v1`, JSON, documented with OpenAPI. Breaking changes only in a new version (`/api/v2`), with 6 months' notice. |
| PAPI-2 | Auth with `Authorization: Bearer <api key>`. Keys are scoped (for example `knowledge:write`, `conversations:read`, `leads:read`, `messages:send`) and limited per tenant (plan-based rate limits, `429` with `Retry-After`). |
| PAPI-3 | Cursor pagination (`?cursor=…&limit=…`, max 100). |
| PAPI-4 | Errors in one format: `{"error": {"code": "…", "message": "…", "details": {…}}}`. |
| PAPI-5 | `Idempotency-Key` header supported on all POST requests; repeats within 24 hours return the original result. |
| PAPI-6 | Resources: sources, Q&A pairs, conversations, messages (including sending a message or template), contacts, leads, eval cases and runs. |

**Outgoing webhooks (API-2)**

| ID | Requirement |
|---|---|
| OWH-1 | Tenants register HTTPS endpoints and choose events: `message.received`, `message.sent`, `conversation.handoff`, `conversation.closed`, `lead.created`, `lead.updated`, `booking.created`, `payment.completed`, `campaign.completed`. |
| OWH-2 | Payload: `{"id": "evt_…", "type": "lead.created", "created_at": "…", "tenant_id": "…", "data": {…}}`. |
| OWH-3 | Signed: `X-Signature: t=<unix time>,v1=<HMAC-SHA256 of "<t>.<raw body>" with the endpoint secret>`. Receivers should reject timestamps older than 5 minutes. |
| OWH-4 | Delivery: 10 s timeout; any 2xx is success; retries with backoff for up to 24 hours. Endpoints failing continuously for 3 days are disabled, and the tenant is emailed. |
| OWH-5 | A delivery log (last 30 days) with request, response code and a "resend" button. |

## 5. Monitoring

| ID | Requirement | Release |
|---|---|---|
| MON-1 | Sentry for the API, workers and web app, with personal data scrubbed before sending. | R1 |
| MON-2 | Metrics and alerts per DESIGN §13, including per-integration error rate and last-success time. | R1 |
| MON-3 | Status page for customers showing message handling, the WhatsApp API and AI providers. | R2 |

## 6. Open questions

1. WhatsApp: Meta Tech Provider application now, or a BSP partner for the pilots (affects WA-1)?
2. Transcription provider for Indian languages: measure candidates on real voice notes before choosing (STT-1).
3. Which CRM first: Zoho (common with Indian SMBs) or HubSpot?
4. GreenPlot pilot: which layout association, and does GreenPlot need a service-account API key feature first (GP-3)?
5. Bring-your-own AI key on which plans (AI-4)?
