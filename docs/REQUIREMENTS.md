# Product requirements: AI chatbot for WhatsApp and websites

| | |
|---|---|
| Status | Draft for review |
| Last updated | 2026-10-28 |
| Companions | [DESIGN.md](DESIGN.md) (technical design), [INTEGRATIONS.md](INTEGRATIONS.md) (integrations), [UX_DESIGN.md](UX_DESIGN.md) (UX and visual design) |

## 1. Purpose

Turn the current chatbot into a product that businesses pay for every month. A business connects its WhatsApp number and website and uploads what it knows (documents, spreadsheets, web pages, its own systems). Its customers then get accurate, cited answers in their own language, day and night. Leads and hard questions go to the business's staff.

Buyers pay for outcomes, not for AI. The product has to:

1. **Win customers:** capture leads, follow up, book appointments, collect payments.
2. **Save staff time:** answer repeat questions and hand off only what needs a person.
3. **Be trustworthy:** answers come from the business's own data, with sources, and the bot says so when it doesn't know.

## 2. Where the product stands today (v0.2)

| Area | Status |
|---|---|
| WhatsApp Cloud API: verified webhook, dedupe, background replies, keyword handoff, agent replies | Done (API only) |
| Website widget | Done (basic) |
| Chat with OpenAI, Claude or Gemini, chosen per tenant | Done |
| Documents: PDF, DOCX, TXT, MD, CSV, XLSX, JSON, with structure-aware chunks | Done |
| Hybrid search, follow-up rewriting, cited answers | Done |
| Accuracy test sets and scoring | Done (API only) |
| Admin user interface, user logins, billing, durable jobs, migrations | **Missing** |

Everything is set up through the API with shared admin keys. That is the main gap between this and a product someone can buy.

## 3. Target customers

**First market:** small and mid-sized businesses in India that already sell or support customers on WhatsApp.

| Segment | Typical questions and jobs | What they pay for |
|---|---|---|
| Clinics and diagnostic labs | Timings, doctors, test prices, report status, booking | Appointment booking, fewer calls |
| Real estate and plotted layouts | Project details, prices, site visits; for residents: dues, gate passes, maintenance | Lead capture, site-visit booking, resident support |
| Coaching and education | Courses, fees, batches, admissions | Lead capture, follow-ups |
| Online shops (D2C) | Product details, order status, returns | Order status, abandoned-cart and offer campaigns |
| Agencies | Run bots for many client businesses | White-label, multi-client management |

The first segment to launch with is an open question (§10). [GreenPlot](https://github.com/RajeshRamadas/Greenplot) (residential layouts) is a ready pilot customer: residents already ask about dues, visitors and maintenance on WhatsApp.

## 4. Users and roles

| Role | Who | Can |
|---|---|---|
| **Platform admin** | You (the operator) | Manage all tenants, plans, usage, abuse; impersonate for support (audited) |
| **Owner** | Business owner, one per tenant at least | Everything in the tenant, including billing, users and deleting data |
| **Admin** | Manager | Knowledge, bot settings, channels, campaigns, users except owners |
| **Agent** | Support or sales staff | Inbox: read and reply to assigned or open conversations, update leads |
| **Viewer** | Anyone read-only | Dashboards and transcripts |
| **End customer** | The business's customer | Chats on WhatsApp or the website; no account |

## 5. Functional requirements

Priorities: **P0** = needed before the first paying customer. **P1** = needed to sell widely. **P2** = later or premium.

### 5.1 Accounts and onboarding

| ID | Requirement | Priority |
|---|---|---|
| ACC-1 | A business signs up with email and password (or Google), verifies email, and creates its workspace (tenant). | P0 |
| ACC-2 | An onboarding checklist guides setup: connect WhatsApp, add knowledge, set the bot's name and tone, test, go live. | P0 |
| ACC-3 | Owners and admins invite team members by email and give them a role (§4). | P0 |
| ACC-4 | Users can belong to several tenants and switch between them (needed for agencies). | P1 |
| ACC-5 | Optional two-factor authentication (TOTP) for owners and admins. | P1 |
| ACC-6 | Industry templates: choosing an industry pre-fills tone, lead questions, sample test questions and suggested documents. | P1 |

### 5.2 Channels

| ID | Requirement | Priority |
|---|---|---|
| CH-1 | Connect a WhatsApp Business number in a few clicks with Meta's Embedded Signup. The platform stores the business's own access token, encrypted. | P0 |
| CH-2 | Show the connection's health: number status, quality rating, messaging limits and the last webhook received. | P1 |
| CH-3 | The website widget can be styled (colours, logo, greeting, position) and shows sources and a "talk to a person" button. | P0 |
| CH-4 | Instagram DMs and Facebook Messenger through the same inbox. | P2 |
| CH-5 | Voice notes are transcribed and answered; images can be read when the chat model supports images. | P1 |

### 5.3 Knowledge (what the bot knows)

| ID | Requirement | Priority |
|---|---|---|
| KN-1 | Upload files in the admin UI, see each one's processing status and errors, and delete or replace files. | P0 |
| KN-2 | Add website pages by URL, or crawl a site from a starting URL, with scheduled re-crawl. | P1 |
| KN-3 | Q&A pairs: an admin writes exact answers for common questions; these take priority over document search. | P0 |
| KN-4 | Scanned PDFs and images are read with OCR. | P1 |
| KN-5 | Spreadsheets become real tables, so questions like "how many 2BHK plots under ₹40 lakh?" get exact answers from read-only queries. | P1 |
| KN-6 | Sync from Google Drive and Notion. | P2 |
| KN-7 | "Improve answers" view: unanswered and thumbs-down questions, with one-click "add a Q&A answer". | P1 |

### 5.4 Bot behaviour

| ID | Requirement | Priority |
|---|---|---|
| BOT-1 | Set the bot's name, persona, tone, greeting, business hours and rules ("never quote delivery dates"). | P0 |
| BOT-2 | Reply in the customer's language (English, Hindi, Kannada, Tamil, Telugu, Marathi, Bengali at minimum) and understand mixed-language messages such as Hinglish. | P1 |
| BOT-3 | Choose the AI provider and model per tenant (exists today), with a safe default per plan. | P0 |
| BOT-4 | Hand off to a person on request, on repeated "I don't know", or on negative sentiment, and tell the customer when someone will reply (business hours aware). | P0 |
| BOT-5 | Every factual answer cites its source. Answers whose claims aren't supported by the cited text are blocked or replaced with a handoff. | P1 |
| BOT-6 | Tools: the bot can call actions the business enables (look up order status, check slots, create a booking, send a payment link). Each call is logged. | P1 |

### 5.5 Inbox (human agents)

| ID | Requirement | Priority |
|---|---|---|
| INB-1 | Shared inbox of all conversations across channels, filterable by status (bot, waiting for agent, agent, closed), channel, assignee and label. | P0 |
| INB-2 | Agents take over, reply, add internal notes, assign to a colleague, and hand back to the bot. | P0 |
| INB-3 | New messages appear live without refreshing. Agents get browser notifications for conversations waiting for them. | P0 |
| INB-4 | Show the WhatsApp 24-hour window. Outside it, agents can only send an approved template. | P0 |
| INB-5 | Canned replies (saved responses). | P1 |
| INB-6 | Mobile-friendly inbox (installable web app), so owners can reply from their phone. | P1 |

### 5.6 Leads

| ID | Requirement | Priority |
|---|---|---|
| LEAD-1 | The bot collects lead details the business chooses (name, phone, need, budget, location, preferred time) during the conversation, naturally rather than as a rigid form. | P0 |
| LEAD-2 | Leads list with status (new, contacted, qualified, won, lost), owner and notes; export to CSV. | P0 |
| LEAD-3 | Send new leads to email, Google Sheets or a webhook; Zoho, HubSpot and Salesforce connectors later. | P1 |
| LEAD-4 | Automatic follow-up messages for leads that go quiet, using approved templates. | P2 |

### 5.7 Campaigns (WhatsApp templates)

| ID | Requirement | Priority |
|---|---|---|
| CMP-1 | Create WhatsApp message templates, submit them to Meta for approval, and track their status. | P1 |
| CMP-2 | Contacts with opt-in status; import from CSV; automatic opt-out on "STOP". | P1 |
| CMP-3 | Send a template to a segment now or at a scheduled time, within Meta's rate limits, with personalised fields. | P1 |
| CMP-4 | Campaign results: sent, delivered, read, replied, failed, opt-outs, and the cost Meta charges. | P1 |

### 5.8 Appointments and payments

| ID | Requirement | Priority |
|---|---|---|
| APT-1 | Business sets services, staff and available slots, or connects Google Calendar. The bot offers free slots, books, and sends reminders. | P1 |
| PAY-1 | The bot sends Razorpay or UPI payment links for bookings and dues, and confirms payment in the chat. | P1 |

### 5.9 Analytics

| ID | Requirement | Priority |
|---|---|---|
| ANL-1 | Dashboard: conversations, messages, share answered by the bot, handoff rate, first response time, leads captured, per day and per channel. | P0 |
| ANL-2 | Top questions and unanswered questions. | P1 |
| ANL-3 | Customer feedback (👍/👎 on answers) and a satisfaction score. | P1 |
| ANL-4 | Accuracy: run test sets from the UI and show the score over time (the API exists today). | P1 |

### 5.10 Plans and billing

| ID | Requirement | Priority |
|---|---|---|
| BIL-1 | Plans with monthly AI message quotas, channel limits, seat limits and feature flags (§7). 14-day free trial. | P0 |
| BIL-2 | Subscriptions through Razorpay (India), with Stripe for international customers later; upgrade, downgrade and cancel by the owner. | P0 |
| BIL-3 | GST-compliant invoices. | P0 |
| BIL-4 | Usage metering: AI messages, tokens and cost per tenant per day. Warnings at 80% and 100%; at the limit, either pause the bot (with handoff) or bill overage, as the owner chooses. | P0 |
| BIL-5 | WhatsApp template charges are billed by Meta to the business's own payment method; the platform shows them but doesn't resell them (v1). | P1 |

### 5.11 Platform administration

| ID | Requirement | Priority |
|---|---|---|
| ADM-1 | Platform admin console: tenants, plans, usage, costs, errors, and suspending a tenant. | P0 |
| ADM-2 | Support impersonation, time-limited and recorded in the audit log. | P1 |
| ADM-3 | White-label for agencies: own logo, domain and colours; agency-level billing across client workspaces. | P2 |

### 5.12 Developer access

| ID | Requirement | Priority |
|---|---|---|
| API-1 | Public REST API with per-tenant API keys (scoped), covering knowledge, conversations, leads and messages. | P1 |
| API-2 | Outgoing webhooks for events (new lead, handoff, message received). | P1 |

## 6. Non-functional requirements

| ID | Area | Requirement |
|---|---|---|
| NFR-1 | Response time | WhatsApp replies are sent within 8 seconds at p95 (most of this is the AI model). The admin UI responds in under 500 ms at p95. Webhooks are acknowledged in under 1 second. |
| NFR-2 | Reliability | 99.5% monthly uptime for message handling. No inbound message is lost: they are stored before acknowledgement and retried until answered or handed off. |
| NFR-3 | Scale (first year) | 500 tenants, 2 million AI messages per month, 50 messages per second at peak, on a few servers. |
| NFR-4 | Accuracy | Each launch industry template ships with a test set. The bot must score at least 90% on it, with no invented answers on "should decline" questions. |
| NFR-5 | Security | All traffic over TLS; secrets and tokens encrypted at rest; passwords hashed with Argon2; role checks on every request; tenant data isolated by `tenant_id` on every query; rate limits per tenant and per IP. |
| NFR-6 | Privacy and compliance | India's Digital Personal Data Protection Act 2023 and its rules: consent for marketing messages, data export and deletion on request, a breach response process, and a data processing agreement for customers. Meta's WhatsApp Business policies: opt-in before template messages, opt-out honoured, no prohibited industries. |
| NFR-7 | Data residency | Data stored in India (for example AWS Mumbai or a Bengaluru provider) by default. AI providers may process outside India; this is disclosed to customers. |
| NFR-8 | Retention | Conversations are kept for 12 months by default (configurable per tenant); uploaded files until deleted; audit logs for 2 years. |
| NFR-9 | Observability | Structured logs, error tracking, metrics (latency, queue depth, provider errors, cost per tenant), and alerts for webhook failures and stuck queues. |
| NFR-10 | Cost control | AI cost per tenant tracked per day; the average AI cost of a plan stays under 30% of its price. |
| NFR-11 | Accessibility | Admin UI and widget meet WCAG 2.1 AA for contrast, keyboard use and screen-reader labels. |
| NFR-12 | Localisation | Admin UI in English first, with Hindi later; the bot's languages per BOT-2. |

## 7. Plans (starting assumption, to validate with customers)

Prices exclude GST and WhatsApp template charges (billed by Meta).

| | Starter | Growth | Business |
|---|---|---|---|
| Price per month | ₹1,999 | ₹5,999 | ₹14,999 |
| AI messages per month | 1,000 | 5,000 | 20,000 |
| Channels | 1 WhatsApp number + website | 2 numbers + website | 5 numbers + all channels |
| Team seats | 2 | 5 | 15 |
| Knowledge | 50 files | 500 files, website crawl | Unlimited, Drive and Notion sync |
| Leads, inbox, analytics | ✓ | ✓ | ✓ |
| Campaigns, appointments, payments | | ✓ | ✓ |
| Tools / live data, API, webhooks | | | ✓ |
| Default AI model | Fast, low-cost model | Choice of provider | Choice, including top models |

Overage: ₹1 per extra AI message, or pause with handoff, as the owner chooses (BIL-4).

## 8. Release plan

| Release | Goal | Requirements |
|---|---|---|
| **R1: Sellable MVP** | First 5–10 paying pilot customers in one industry | ACC-1–3, CH-1, CH-3, KN-1, KN-3, BOT-1, BOT-3, BOT-4, INB-1–4, LEAD-1–2, ANL-1, BIL-1–4, ADM-1, NFR-1–6 |
| **R2: Grow revenue** | Features customers pay more for | CMP-1–4, APT-1, PAY-1, LEAD-3, BOT-2, CH-5, KN-2, KN-4, KN-7, ANL-2–4, INB-5–6, ACC-4–6 |
| **R3: Scale and partners** | Larger customers and agencies | BOT-5, BOT-6, KN-5, KN-6, API-1–2, ADM-2–3, CH-2, CH-4, LEAD-4 |

## 9. Success measures

| Measure | Target |
|---|---|
| Time from sign-up to first live answer | Under 30 minutes |
| Questions answered without a person | Over 70% |
| Accuracy on the tenant's test set | Over 90% |
| Trial to paid conversion | Over 15% |
| Monthly customer churn | Under 4% |
| AI cost as a share of plan revenue | Under 30% |

## 10. Open questions

1. **Launch industry for R1:** residential layouts (GreenPlot pilot), clinics, real estate, coaching or online shops?
2. **Pricing:** validate the plans in §7 with 5–10 target customers before building billing.
3. **WhatsApp:** become a Meta Tech Provider so businesses can connect through Embedded Signup (needs business verification and app review), or work through a Business Solution Provider (BSP) at first?
4. **Template charges:** keep Meta billing the business directly (v1 assumption), or resell them with a margin later?
5. **AI defaults per plan:** which model each plan uses by default, given cost (NFR-10).
6. **Hosting region and provider** for data residency (NFR-7).

## 11. Out of scope for now

- Voice calls and IVR.
- A drag-and-drop flow builder (the bot is driven by knowledge and tools, not scripted flows).
- Native iOS and Android apps (the inbox is an installable web app instead).
- Training or fine-tuning custom AI models.
