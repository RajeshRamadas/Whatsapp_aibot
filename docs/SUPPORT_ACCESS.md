# Platform support access

In **Platform administration**, choose **Open read-only support** on an active
workspace. The banner identifies the workspace and shows when access ends.
Use **End support session** to return to the platform console early.

Support access lasts up to 15 minutes. Each grant is bound to the original
platform administrator's signed-in session and a single workspace. It grants
viewer access for diagnostics and blocks writes, live streams, attachment
downloads and offline Inbox snapshots. Signing out or losing platform admin
permissions ends access. The grant does not extend on use.

Workspace audit logs record when the operator starts, ends and uses a grant.
Entries contain the operator ID, grant ID and route pattern. The start event
records the expiry. Apply migration `0061_support_sessions` before deployment.
