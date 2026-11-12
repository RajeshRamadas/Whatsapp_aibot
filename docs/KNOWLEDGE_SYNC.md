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
