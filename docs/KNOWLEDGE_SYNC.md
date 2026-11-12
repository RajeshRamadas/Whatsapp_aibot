# Google Drive and Notion knowledge sync (T10.7)

One Drive account and one Notion connection per workspace. Only owners/admins
configure connections. Tokens and OAuth codes use the existing MASTER_KEY envelope
encryption. Provider requests and extraction run in workers without a database
session spanning network or model calls. APIs return metadata, never credentials.

## Setup

Google Cloud: enable Drive API, create a web OAuth client, and configure
KNOWLEDGE_GOOGLE_CLIENT_ID, KNOWLEDGE_GOOGLE_CLIENT_SECRET and
KNOWLEDGE_GOOGLE_REDIRECT_URI. Register the exact redirect URI ending in
`/api/backend/knowledge-sync/callback` on the dashboard origin. Production requires
HTTPS. Consent requests only `drive.readonly`, with PKCE and a ten-minute state
bound to the initiating signed-in administrator session and workspace membership.
This restricted Google scope may require Google verification before public use.
Calendar and lead-export authorizations are separate.

Notion: create an internal connection with Read content capability only. Share
chosen pages with it, and enter the connection token and page IDs. The adapter uses
read operations only, even if a token has broader permissions. Notion does not
provide a token-scope introspection endpoint; the administrator confirms Read only.

Connection APIs are under `/api/v1/knowledge-sync`. Drive consent callbacks only
persist encrypted exchange work; workers exchange codes. Local disconnect destroys
stored credentials and imported knowledge. Revoke the connection separately in
Google/Notion if it should lose access at the provider.

Drive uses selected folders and descendants; shortcuts and unsupported MIME types
are excluded. Notion sync is limited to explicitly selected page IDs; add child
pages separately if their content is needed. Removing access or moving content out
of selected scope removes its knowledge during the next successful metadata scan.
Polling, document limits, change-feed handling and UI acceptance are delivered in
the remaining stack. Live OAuth/Notion account verification requires tenant credentials.

Provider references:
- https://developers.google.com/workspace/drive/api/guides/manage-changes
- https://developers.google.com/workspace/drive/api/guides/manage-downloads
- https://developers.google.com/workspace/drive/api/guides/api-specific-auth
- https://developers.notion.com/reference/retrieve-page-markdown

## Worker and access boundaries

Workers poll every 15 minutes, with a durable 15-minute crash lease and bounded
backoff. Drive consumes the changes feed and also lists the entire selected folder
tree so moved, trashed and unshared files disappear. Expired feed tokens trigger a
full rescan. Metadata must be complete before any absence is interpreted as deletion.
Documents export as DOCX, Sheets as XLSX and Slides as PDF. Supported uploaded file
types in Drive are PDF, DOCX, TXT, Markdown, CSV, XLSX and JSON. Shortcuts are skipped.

Notion uses the 2026-03-11 Markdown API with v2 syntax. Selected page bodies are
polled even when the parent timestamp is unchanged; content hashes avoid repeated
embedding. Headings are kept. Truncated/unknown-block pages fail explicitly rather
than exposing partial content. Embedded databases, unsupported blocks and child
pages that were not selected/shared may need separate documents or page selections.

Limits per connection: five selected Drive roots, 50 folders, depth five, 200 files;
20 selected Notion pages; 10 MB downloads; 1 MB Notion Markdown; 2,000 chunks per
document; four-minute provider work budget. Providers can impose smaller export,
permission and rate limits. Oversized or partial scans do not infer deletions.
No stored files or external links are fetched from document text. Temporary bytes
are discarded after extraction. Credentials and provider diagnostic bodies never
reach logs. Pausing hides imported content immediately; disconnect also deletes
chunks, typed spreadsheet rows and credentials. Imported sources are managed through
sync controls, not document deletion or spreadsheet file reimport.

Changed content is hidden during re-indexing, and unverified/paused/stale connections
cannot answer. A successful refresh expires after 24 hours without another successful
sync. Permission failures remove affected knowledge. Known complete unchanged content
can remain available during transient metadata failures until that expiry. Synced
spreadsheets use the existing guarded SQL tools; each changed import starts paused
until an administrator explicitly enables its tables under Exact spreadsheet answers.
