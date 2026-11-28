# WhatsApp lead follow-ups

Workspace owners and admins can open **Leads → Lead follow-ups**, choose an
approved WhatsApp marketing or utility template, fill its fixed values, and
set a delay of 1 to 168 hours. Preview the message before enabling the rule.
Follow-ups start paused. Pausing keeps the saved template and delay.

Each open WhatsApp lead can receive one follow-up. The delay runs from the
later of lead capture and the last change to the enabled rule. Existing leads
wait the full delay when a rule is first enabled. A customer reply after lead
capture, withdrawn consent, a closed conversation, or a won or lost lead
stops eligibility. Only contacts with recorded opt-in can receive a message.

The worker checks the saved template approval and content, WhatsApp channel
quality, consent, and the lead again before claiming a send. A changed or
withdrawn template requires an admin to review and save it again. Campaigns
and follow-ups share the portfolio capacity and phone send rate limits.

The Inbox records each send claim before submitting it to WhatsApp. A worker
restart or uncertain provider response leaves the claim in place, so the
follow-up sends at most once. A later signed receipt can confirm delivery.
A message claimed just before a reply, opt-out or pause can still finish.

Apply migration `0060_lead_follow_ups` and deploy the compatible queue worker
before starting the scheduler for this release. Configuration is per
workspace. There are no additional environment variables.

Before live use, use an opted-in test contact and an approved template to
verify a delivered follow-up and check that a reply or STOP suppresses a
second eligible test lead. [WhatsApp campaigns and consent](CAMPAIGNS.md)
