# Transactional email: SES Mumbai

T8.4 implements EM-1–EM-4. Launch sender: `notifications@marketingadda.com`.
Automated tests use fakes; **live SES delivery and DNS verification are pending**.
No AWS credentials or staging deployment access were available during implementation.
The supplied `https://marketingadda.com` responds, but `/openapi.json` returns 404;
the chatbot API deployment and HTTPS account-screen links still need confirmation.

## Configure the sender

1. In SES **ap-south-1**, create/verify the `marketingadda.com` domain identity.
   Enable Easy DKIM and publish all three CNAME names/values SES generates, exactly
   as shown. Wait for identity and DKIM verification to succeed.
2. Configure a custom MAIL FROM subdomain, for example `mail.marketingadda.com`.
   Publish its MX `10 feedback-smtp.ap-south-1.amazonses.com` and TXT
   `v=spf1 include:amazonses.com -all`. Choose rejection on MAIL FROM MX failure.
   Maintain only one SPF record per hostname; merge mechanisms if already present.
3. Publish TXT at `_dmarc.marketingadda.com`, initially `v=DMARC1; p=none; adkim=r; aspf=r`.
   Optionally add `rua` only for a monitored reporting address you control. Check
   delivered-message authentication headers for DKIM/SPF alignment before moving
   to `p=quarantine` or `p=reject`. Coordinate existing domain mail/DNS policy first.
4. In Mumbai, create an SNS standard topic for SES feedback. Its access policy must
   allow `ses.amazonaws.com` to publish, restricted to your AWS account and SES
   identity. Configure the SES identity's **Bounce and Complaint** SNS topics to
   this same topic; disable duplicate email feedback forwarding after verifying SNS.
   If using `EMAIL_CONFIGURATION_SET`, create its SNS event destination instead
   and enable Bounce and Complaint. Do not configure both paths for duplicate events.
5. Subscribe the public HTTPS endpoint `https://<staging-host>/webhooks/email` to
   that topic. Use SNS signature version 2. The handler accepts only the configured
   topic and verifies AWS signatures against certificates from the exact regional
   SNS hostname. A cache miss returns 503 and queues the public certificate download;
   SNS retries then trigger encrypted, worker-run subscription confirmation. Keep
   the worker running during setup; request confirmation again if SNS stops retrying.

## Deploy settings and permissions

Set on **API and worker**: `EMAIL_BACKEND=ses`, `EMAIL_FROM=notifications@marketingadda.com`,
`EMAIL_REGION=ap-south-1`, `EMAIL_SNS_TOPIC_ARN=<actual topic ARN>`, `PUBLIC_APP_URL=<HTTPS app URL>`
and the same valid 32-byte base64 `MASTER_KEY`/version. Optionally set
`EMAIL_CONFIGURATION_SET`. Startup validates the sender, topic region, encryption
and production HTTPS URL. Apply `alembic upgrade head` before starting either service.

Give the worker an AWS SDK credential-chain identity with `ses:SendEmail` for the
verified identity and `sns:ConfirmSubscription` for the feedback topic. Prefer an IAM
role. Never put access keys in Git or chat. The worker needs outbound HTTPS to AWS
SES/SNS and the regional SNS certificate host; the API makes no provider HTTP calls.
Request SES production access before sending to unverified real recipients. Sandbox
accounts can use verified recipients and the AWS mailbox simulator. Set
`EMAIL_SENDS_PER_SECOND` at or below the regional quota (default 1, shared across workers).

The outbox commits with signup/reset/invitation changes. Addresses and bodies are
encrypted with authenticated row/purpose/version binding; retain previous master
keys until pending mail drains. Claim leases expire after two minutes. Transient
errors retry up to five attempts, with exponential backoff, jitter and numeric
Retry-After support; permanent errors stop immediately. Authentication codes expire
after 15 minutes and invitations after seven days; expired jobs never send. Payloads
are erased on completion/failure/suppression. Delivery is at least once: a crash after
SES accepts a message can cause a duplicate. Suppression cannot recall in-flight mail.

Feedback deduplicates by SNS message ID; bounce/complaint addresses are normalized
and stored as hashes. Queued and future emails to them are suppressed globally,
including account email before a workspace exists. There is no tenant-facing bypass.
An operator should remove a suppression only after resolving the bounce/complaint.
Platform dead letters and `chatbot_failed_jobs{kind="email"}` expose record metadata;
`chatbot_queue_depth{queue="email"}` and SES integration metrics support monitoring.
Reserved handoff/invoice templates do not add new notification triggers in this task.

## Required staging acceptance record (pending)

- Record staging URL, commit, date/operator, SES identity/DKIM status and DNS checks.
- With a consenting verified recipient, complete signup verification, password reset
  and invitation acceptance using the delivered messages; inspect both MIME parts,
  correct HTTPS links and `Authentication-Results` (SPF, DKIM and DMARC pass).
- Send through the configured sender to AWS simulator `bounce@simulator.amazonses.com`
  and `complaint@simulator.amazonses.com`. Confirm authenticated SNS delivery, one
  suppression per address and no subsequent provider send for those addresses.
- Confirm subscription status, successful worker deliveries and absence of bodies,
  recipient addresses or codes in logs. Record SES message IDs only, never codes.
- Keep T8.4 unchecked until these results are recorded. Current result: **not run**.
