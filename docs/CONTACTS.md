# Contacts and saved segments

T9.1 implements CMP-2 and WA-8. All routes use the authenticated workspace context
(session plus `X-Workspace-Id`, or a scoped API key). Agents can read; owners/admins
can import, change consent and manage segments. Foreign record IDs return 404.

| Method | Route under `/api/v1` | Behavior |
|---|---|---|
| GET | `/contacts` | Paginated WhatsApp contacts, including current marketing eligibility |
| POST | `/contacts/import` | Multipart `file` CSV; atomic insert/update and counts |
| PUT | `/contacts/{id}/consent` | Record explicit consent: `{"opted_in":true,"source":"signed-form-reference"}` |
| GET / POST | `/segments` | Paginated list / create a saved dynamic segment |
| PUT / DELETE | `/segments/{id}` | Replace segment name/filters / delete the saved segment |

Lists support `limit` (1–200) and `offset`. Contacts also support literal `search`,
`opted_in=true|false` and `segment_id`. Filters combine; query parameters do not
override a saved segment's eligibility restriction. Responses include `items`,
`total`, `limit` and `offset`. Empty lists return zero counts.

## CSV format

Use UTF-8 (a BOM is accepted), with required `phone` and optional `name`, `language`,
`attributes`, `opt_in_at`, `opt_in_source`. Unknown or duplicate headers, duplicate
normalized phones, malformed rows and invalid fields reject the whole file with 422.
Oversized files return 413. Default limits are 1 MiB / 1,000 rows; settings permit
up to 10 MiB / 10,000 rows. Files and original filenames are not retained.

```csv
phone,name,language,attributes,opt_in_at,opt_in_source
+919876543210,Sample Contact,en,"{""city"":""Pune""}",2026-01-01T10:00:00Z,website-form
+919876543211,Another Contact,en,,,
```

Phones must contain 7–15 international digits, with an optional leading `+` and
no national-format punctuation. Stored WhatsApp IDs omit `+`. Nonempty name and
language fields update profiles; blank fields preserve existing values. Attributes
merge by key: at most 10 string pairs per row, identifier keys up to 50 characters,
values up to 200 characters. Names allow 200 characters; language codes use forms
such as `en`, `hi` or `en_US`. Re-imports update the same workspace/channel/phone.

Consent is never implied by import or conversation creation. To import an opt-in,
provide both a timezone-aware ISO timestamp (not in the future) and a nonempty
source/reference (up to 120 characters). The importing business must hold the
underlying permission. Re-imports accept only newer consent and never change a
contact with a recorded opt-out. Use a new explicit operator consent record or the
customer's WhatsApp START to opt back in. STOP, Hindi `बंद`, and configured opt-out
keywords record opt-out and confirm it to the customer, even while the bot is paused.

Consent changes store the latest source and append audit metadata with actor,
origin and timestamp; audit metadata/logs omit numbers, names, file contents and
the user-supplied source text. Existing template dispatch rechecks current consent,
so an opt-out blocks previously queued template replies too.

## Dynamic segments

Create/replace with `{"name":"Pune opt-ins","filters":{"language":"en","attributes":{"city":"Pune"}}}`.
Allowed filters: `channel` (`whatsapp` default, or `web`), exact `language`, exact
attribute pairs and `eligible_only` (default true). Filters combine with AND and
always apply the authenticated tenant. Use `eligible_only:false` for non-sendable
profile review, including web contacts. Membership is evaluated when contacts are
listed and changes immediately with profiles/consent; no recipient snapshot is kept.
The saved segment itself is not permission to send. Campaign sending must enforce
current opt-in again (T9.2); campaign/contact UI belongs to T9.3.
