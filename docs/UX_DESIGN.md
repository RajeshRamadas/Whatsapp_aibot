# UX and visual design requirements

| | |
|---|---|
| Status | Draft for review |
| Last updated | 2026-10-28 |
| Related | [REQUIREMENTS.md](REQUIREMENTS.md) (requirement IDs), [DESIGN.md](DESIGN.md) §11 (admin app screens) |

These are the requirements for how the product looks, reads and behaves for each person who uses it: business owners and staff in the admin web app, and end customers on WhatsApp and the website widget. They are what wireframes, the design system and acceptance tests are checked against.

## 1. Design principles

1. **Live in 30 minutes.** A non-technical owner goes from sign-up to the bot answering on WhatsApp in under 30 minutes, without reading documentation. Every screen has a clear next step.
2. **Phone first for owners, desk first for agents.** Owners mostly check leads and reply from their phone; agents work a queue on a laptop. Both layouts are designed, not just shrunk.
3. **Show why the bot said it.** Every bot answer in the inbox and test chat shows its sources. Trust comes from being able to check.
4. **Plain language.** No "RAG", "embeddings", "tokens" or "webhooks" in the main UI. Say "what your bot knows", "AI messages used", "connect to your system". Technical details sit behind "Advanced".
5. **Safe by default.** Destructive or customer-facing actions (sending a campaign, deleting knowledge, going live) show what will happen and ask for confirmation. Nothing reaches customers by accident.
6. **Respectful to customers.** The bot is polite, brief and honest about being a bot. It never pretends to be a person and always offers a way to reach one.

## 2. Users and contexts

| Persona | Context | Main jobs | Design implication |
|---|---|---|---|
| **Owner** (for example, a clinic owner) | Phone, between other work; English or Hindi; not technical | See leads, check the bot is working, reply to escalations, pay | Mobile dashboard and inbox; push notifications; very short setup flows |
| **Admin / manager** | Laptop, occasional sessions | Upload knowledge, set bot rules, run campaigns, read analytics | Clear forms, previews, bulk actions |
| **Agent** | Laptop all day, many chats | Answer waiting conversations quickly | Keyboard shortcuts, canned replies, dense but readable list, live updates |
| **Agency staff** | Laptop, many client workspaces | Switch between clients, set up new ones | Workspace switcher, templates, copy settings |
| **End customer** | WhatsApp on a mid-range Android phone, often on mobile data; any of the BOT-2 languages | Get an answer, book, pay, reach a person | Short messages, buttons over typing, works on slow networks |

## 3. Information architecture (admin app)

Primary navigation, in this order:

| Item | Contains | Roles |
|---|---|---|
| Home | Setup checklist (until live), today's numbers, conversations needing attention | All |
| Inbox | All conversations, with counts by status | Owner, Admin, Agent |
| Leads | Lead list and pipeline | Owner, Admin, Agent |
| Knowledge | Files and pages, Q&A answers, "Improve answers" | Owner, Admin |
| Bot | Persona and rules, languages, hours, test chat, AI model (Advanced) | Owner, Admin |
| Campaigns | Templates, contacts, campaigns (R2) | Owner, Admin |
| Appointments | Services, calendar, bookings (R2) | Owner, Admin, Agent |
| Analytics | Overview, questions, accuracy | All |
| Settings | Workspace, team, channels, integrations, billing and usage, API and webhooks, audit log | Owner, Admin (billing: Owner) |

- Items a role can't use are hidden, not disabled.
- **Desktop:** a left sidebar.
- **Mobile:** a bottom bar with Home, Inbox, Leads and More.
- The workspace switcher sits at the top of the sidebar, or in "More" on mobile.

## 4. Key flows

Each flow lists its steps, the target time, and what must be true.

### 4.1 Sign-up and onboarding (ACC-1, ACC-2, ACC-6)

1. Sign up (email or Google) → verify email (the code can be typed or tapped).
2. "What kind of business?" Pick an industry template, or choose "Other".
3. Business name, language(s), business hours. Pre-filled from the template.
4. Onboarding checklist on Home:
   1. Connect WhatsApp
   2. Add what your bot knows
   3. Test your bot
   4. Go live

**Requirements:**
- Steps 1–3 take at most 3 screens and 2 minutes.
- The checklist stays on Home until all steps are done.
- Each item shows its state (not started, in progress, done, needs attention).
- The user can stop at any step and resume later, with progress saved.

### 4.2 Connect WhatsApp (CH-1)

1. Explain what's needed, before starting: a phone number not already used on the WhatsApp app (or a willingness to move it), a Facebook account, and the business's details.
2. Open Meta's signup. While it is open, the app shows "Finish in the Meta window".
3. On success: the connected number, the display name and the status. On failure: the reason in plain words and a retry button.

**Requirements:**
- Plain-language help for common failures: the number is already on the WhatsApp app, display name rejected, or business not verified.
- A "Send me a test message" button sends a template to the owner's own phone.

### 4.3 Add knowledge (KN-1, KN-2, KN-3)

1. Drag and drop files, paste a website address, or "Write an answer" (Q&A).
2. Each item shows its progress: Uploading → Reading → Ready, or Failed with the reason and a fix ("This PDF is a scan. Turn on OCR" or "Re-upload as PDF").
3. Suggestions from the industry template: "Clinics usually add timings, doctors and fees".

**Requirements:**
- Multiple files at once.
- Status updates live, without refreshing.
- Deleting an item shows how many answers relied on it in the last 30 days.

### 4.4 Test and go live (BOT-1)

1. The test chat looks like WhatsApp. Each bot reply shows its sources, which expand to the quoted text.
2. Suggested test questions come from the template and the tenant's test set.
3. "Go live" shows a summary: channel, hours, handoff rule and greeting. Confirm.

**Requirements:**
- The test chat never sends to WhatsApp.
- Going live can be undone ("Pause bot"), and a paused bot hands every message to agents.

### 4.5 Handle a conversation in the inbox (INB-1 to INB-5, BOT-4)

1. The list shows contact name or number, channel icon, last message preview, time, status badge, assignee and unread dot. Conversations waiting for an agent sort first.
2. Open a conversation: messages with the bot's sources, internal notes styled differently, and a contact and lead panel (desktop: right side; mobile: a tab).
3. Take over: the composer is enabled, and the bot stops for this conversation. Reply with text, a canned reply (`/` shortcut) or an attachment. "Hand back to bot" and "Close" are the explicit next actions.
4. Outside WhatsApp's 24-hour window, the composer is replaced by "Send a template" with a template picker and a preview.

**Requirements:**
- New messages appear within 2 seconds.
- An unsent draft survives switching conversations.
- Two agents opening the same conversation see who is replying ("Priya is typing"), and taking over shows who owns it.
- Keyboard: `j`/`k` to move, `Enter` to open, `r` to reply, `e` to close, `/` for canned replies.

### 4.6 Leads (LEAD-1, LEAD-2)

- Lead card: name, phone (tap to call or WhatsApp), the fields captured, source conversation, status, owner and notes.
- Views: list (default) and board by status. Filters for status, owner, date and source.
- New-lead notification on phone (push) and email, with an option to turn it off (EM-5).

### 4.7 Campaigns (CMP, R2)

1. Choose or create a template. The preview looks like a WhatsApp message, with sample values for variables. The Meta approval status is shown.
2. Choose an audience: a segment, or a CSV upload. Show the count, how many are excluded (no opt-in, opted out), and the estimated Meta cost.
3. Send now or schedule. The confirmation repeats the count, cost and time.
4. Results: sent, delivered, read, replied, failed and opt-outs, with drill-down.

**Requirements:** a test send to the owner's own number before the real send.

### 4.8 Billing and usage (BIL)

- Usage meter on Home and in Billing: "AI messages: 3,420 of 5,000 this month", with a projection ("At this rate you'll reach your limit on 24 Nov").
- At 80% and 100%: a banner with "Upgrade", or "Choose what happens at the limit" (pause with handoff, or pay for extra).
- Upgrade: compare plans → Razorpay checkout → confirmation. Downgrade explains what stops working first.
- Invoices list with PDF download; GSTIN can be edited before the next invoice.

## 5. Screen requirements (all screens)

| ID | Requirement |
|---|---|
| UI-1 | **Every list or data screen has four designed states:** loading (skeletons, not spinners, for over 300 ms), empty (what this is, and the first action to take), error (what failed and a retry), and filled. |
| UI-2 | **Errors** say what happened and what to do, in plain words, next to the field or action that failed. No raw error codes, except in an expandable "Details" for support. |
| UI-3 | **Confirmation** for destructive or customer-facing actions names the object and the effect ("Delete *fees.pdf*? The bot will stop using it right away"). Delete of important objects (a channel, the workspace) requires typing its name. |
| UI-4 | **Undo** for quick reversible actions (archive, close conversation, remove label) via a 5-second toast, instead of a confirmation dialog. |
| UI-5 | **Saving:** forms show unsaved changes and warn before leaving. Settings save explicitly ("Save changes"); toggles take effect immediately and say so. |
| UI-6 | **Live data** (inbox, knowledge status, campaign progress) updates without refresh and shows "Reconnecting…" if the live connection drops. |
| UI-7 | **Help in context:** a short line of help under complex settings, and a "?" link to a help article. No separate manual is needed for R1 flows. |
| UI-8 | **Times and numbers** in the workspace timezone and Indian formatting (₹1,23,456; 28 Oct 2026, 3:45 pm). Relative times in lists ("5 min ago") with the exact time on hover or long-press. |
| UI-9 | **Search** at the top of Inbox, Leads and Knowledge, matching names, phone numbers and message text. |

## 6. Bot conversation design (WhatsApp and widget)

| ID | Requirement |
|---|---|
| CONV-1 | **Greeting** on a customer's first message: the bot's name, that it is an automated assistant, what it can help with (2–3 examples, or quick-reply buttons), and how to reach a person. At most 3 short lines. |
| CONV-2 | **Length:** answers under about 600 characters by default (WhatsApp shows long messages collapsed). Longer content is split into at most 2 messages or offered as "Want the full details?". |
| CONV-3 | **Formatting:** WhatsApp formatting only (`*bold*`, `_italic_`, simple lists with "•" or numbers). No Markdown headings, tables or links in brackets. Prices, times and phone numbers in their own lines when there are several. |
| CONV-4 | **Citations on WhatsApp:** no bracket numbers like [1]. Instead, when helpful, a short source line at the end ("From: Fee schedule, page 2"), or a document attachment if the tenant allows sharing that file. The widget shows sources as small expandable chips. |
| CONV-5 | **Choices as buttons:** when the next step is one of a few options (book, talk to a person, see prices), use reply buttons (up to 3) or a list (up to 10), not "Type 1 for…". |
| CONV-6 | **Not sure:** when the bot can't answer from the business's data, it says so plainly, never guesses, and offers to connect a person ("I don't have that information. Shall I connect you to our team?" with Yes/No buttons). |
| CONV-7 | **Handoff:** confirm the handoff and set expectations using business hours ("Our team usually replies within 1 hour" or "We're closed now; we'll reply after 9 am tomorrow"). While waiting, the bot doesn't answer, except to repeat that a person will reply if the customer writes again after a set time. |
| CONV-8 | **Language:** reply in the language and script the customer uses, including Hinglish written in Latin script. Switch if the customer switches. Fall back to the workspace default language when unsure. |
| CONV-9 | **Tone:** polite and warm, matching the tenant's tone setting (formal, friendly or neutral). Use respectful forms in Indian languages (for example *aap*, not *tum*, in Hindi). No jokes, emojis only if the tenant's tone allows them (default: at most one per message). |
| CONV-10 | **Errors:** if something fails, say sorry briefly and offer the next step; never show technical errors to customers. |
| CONV-11 | **Collecting details (leads, bookings):** ask one question at a time, explain why ("so our team can call you back"), accept answers in any order, confirm the collected details before submitting, and let the customer skip optional fields. |
| CONV-12 | **Opt-out:** "STOP" (and the tenant's localised keywords) gets a confirmation that marketing messages are stopped and how to restart; the customer can still ask questions. |

### 6.1 Example messages (English defaults, to be translated and tone-adjusted)

| Moment | Message |
|---|---|
| Greeting | "Hi, I'm Asha, the virtual assistant for Sunrise Clinic. I can help with timings, doctors and fees, or book an appointment. Type *agent* anytime to talk to our team." |
| Not sure | "I don't have that information. Shall I connect you to our team?" [Yes, connect me] [No, thanks] |
| Handoff (open) | "Thanks, I've passed this to our team. Someone usually replies within an hour." |
| Handoff (closed) | "Thanks, I've passed this to our team. We're closed now and will reply after 9 am tomorrow." |
| Unsupported media | "Sorry, I can't open that file. Could you type your question?" |
| Rate limited | "You're sending messages quickly. Please wait a minute and I'll reply." |
| Error | "Sorry, something went wrong on our side. Please try again in a moment, or type *agent* to reach our team." |

## 7. Website widget (CH-3)

| ID | Requirement |
|---|---|
| WID-1 | Launcher button with the tenant's colour and icon or logo, bottom-right by default (bottom-left option), with an accessible label. The greeting bubble is shown once per visitor, and can be turned off. |
| WID-2 | Panel: header (bot name, avatar, "Usually replies instantly"), messages, quick-reply chips, composer, "Talk to a person" button, and a "Powered by" line that can be removed on higher plans. |
| WID-3 | Size: 380 × 600 px on desktop; full screen on phones under 480 px wide, with a clear close button. It must not block the host site's own buttons while closed. |
| WID-4 | Typing indicator while the bot is working. Sources as expandable chips under answers. 👍/👎 on bot answers (ANL-3). |
| WID-5 | The conversation is kept across page loads on the same site (stored in the visitor's browser) and can be cleared with "Start a new chat". |
| WID-6 | Performance: script under 30 KB gzipped, loaded asynchronously, with no effect on the host page's layout or scripts (isolated styles, for example a shadow DOM). |
| WID-7 | Theming options in admin: colour, position, launcher icon, greeting, bot avatar, and light, dark or automatic mode, with a live preview. |
| WID-8 | Offline or failed request: a clear message and a retry; messages typed while offline are sent when the connection returns. |

## 8. Visual design system

| ID | Requirement |
|---|---|
| VDS-1 | **Tokens** for colour, type, spacing, radius, shadow and motion, defined once and used by the admin app and widget. Light and dark themes for the admin app. |
| VDS-2 | **Colour:** a neutral base with one brand accent. Status colours used consistently: green (live, done, delivered), amber (needs attention, waiting), red (failed, error), blue (information, bot), grey (closed, inactive). Colour is never the only signal: badges have text and icons. |
| VDS-3 | **Type:** a system or open font with full Devanagari and other Indic script support (for example Inter for Latin with Noto Sans for Indic scripts). 16 px minimum body text on mobile; line height 1.5 for message text. |
| VDS-4 | **Spacing** on a 4 px base grid. Touch targets at least 44 × 44 px. |
| VDS-5 | **Components:** buttons (primary, secondary, ghost, destructive), inputs, selects, toggles, tabs, tables, cards, badges, toasts, dialogs, drawers, empty states, skeletons, chat bubbles (customer, bot, agent, note), source chips and usage meters. One component library (for example shadcn/ui on Radix), documented in a component gallery. |
| VDS-6 | **Iconography:** one outline icon set (for example Lucide). Channel icons follow each brand's usage rules (WhatsApp, Instagram). |
| VDS-7 | **Chat bubbles** distinguish senders at a glance: customer (left, neutral), bot (right, light accent, with a small bot marker), agent (right, accent, with the agent's initials), internal note (full width, amber background, "Only your team sees this"). |
| VDS-8 | **Motion** is short (150–250 ms) and purposeful (opening drawers, new messages). It respects the "reduce motion" system setting. |
| VDS-9 | **Brand:** the product name, logo and colours are placeholders until branding is decided. White-label (ADM-3) replaces logo, colours and domain from tokens without code changes. |

## 9. Responsive layout

| Width | Layout |
|---|---|
| Under 640 px (phones) | Single column; bottom navigation; inbox list and conversation as separate screens; contact panel as a tab; sticky composer above the keyboard |
| 640–1024 px (tablets) | Collapsible sidebar; list and conversation side by side in landscape |
| Over 1024 px (laptops) | Sidebar, conversation list, conversation, and a contact and lead panel (three panes in the inbox) |

The admin app is an installable PWA (INB-6) with a home-screen icon, push notifications for waiting conversations and new leads, and offline viewing of the last-loaded inbox.

## 10. Accessibility (NFR-11)

| ID | Requirement |
|---|---|
| A11Y-1 | WCAG 2.1 AA for the admin app and widget: text contrast at least 4.5:1 (3:1 for large text and UI parts), in both themes. |
| A11Y-2 | Everything works by keyboard, with a visible focus ring, a logical tab order and a "skip to content" link. Dialogs trap focus and return it on close. |
| A11Y-3 | Screen readers: labelled controls, landmark regions, live regions announcing new messages in the open conversation and toasts. |
| A11Y-4 | Text can be zoomed to 200% without loss of content; no horizontal scrolling at 320 px wide. |
| A11Y-5 | Every automated check (axe) passes in CI on key screens; a manual screen-reader check (NVDA or TalkBack) before each release. |

## 11. Localisation (NFR-12, BOT-2)

| ID | Requirement |
|---|---|
| L10N-1 | All UI text is in message files from the start (no hard-coded strings), so Hindi and other languages can be added without code changes. English in R1, Hindi in R2. |
| L10N-2 | Layouts allow text to be 40% longer than English, and Indic scripts' taller line heights. |
| L10N-3 | Bot default messages (§6.1) are provided in English and Hindi in R1, and in the other BOT-2 languages in R2. Tenants can edit them. |
| L10N-4 | Customers' messages and names are shown in their original script; search works across scripts where possible. |

## 12. Content style (UI text)

- Sentence case for headings and buttons ("Connect WhatsApp", not "Connect WhatsApp Account Now").
- Buttons say what they do ("Send campaign", not "OK").
- Address the user as "you"; refer to the bot by the name the tenant gave it.
- Use the same word for the same thing everywhere: "conversation" (not chat, thread, ticket); "knowledge" (not documents, sources, data); "AI messages" for the quota unit.
- Numbers as digits; currency as ₹ with Indian grouping.

## 13. Performance and quality targets

| Measure | Target |
|---|---|
| Admin app first load on a mid-range phone over 4G | Under 3 s to interactive |
| Inbox: open a conversation | Under 500 ms |
| New message appears in the inbox | Under 2 s after it arrives |
| Widget script | Under 30 KB gzipped; no layout shift on the host page |
| Lighthouse (admin app, key screens) | Performance and accessibility at least 90 |

## 14. Product analytics (for improving the UX)

Track these events, without message content: sign-up started and completed, each onboarding step completed, WhatsApp connected, first knowledge ready, first test message, went live, first real customer conversation, first lead, upgrade started and completed, campaign sent. They feed the "time to live" and conversion measures in REQUIREMENTS §9.

## 15. Design deliverables and acceptance

| Deliverable | When |
|---|---|
| User flows for §4.1–4.5 and low-fidelity wireframes for every R1 screen | Before M3 (DESIGN §17) |
| Design tokens, component gallery, and high-fidelity designs for R1 screens in light and dark, phone and desktop | During M3 |
| Bot conversation scripts (§6) in English and Hindi, reviewed by a native speaker | Before M8 (pilot) |
| Usability test: 5 target users (owners and agents) complete onboarding and an inbox reply unaided | Before the pilot launch |

**R1 acceptance:**
- In usability tests, at least 4 of 5 owners go live in under 30 minutes without help, and at least 4 of 5 agents take over and reply to a waiting conversation within 1 minute.
- All of §5 and §10 are met on R1 screens.
