# Grounded customer replies (T10.5 / BOT-5)

Model-written replies pass an independent, tool-free verification request before
customer delivery, preview or evaluation. Every factual clause and interactive
choice must be supported by its cited evidence. A source marker alone is not
support. One unsupported or uncertain claim blocks the entire draft. Nonfactual
greetings, questions and refusals can pass without sources.

## Evidence and citations

The verifier receives only the proposed text, choices and the snapshots actually
cited by the draft. It receives no conversation history, customer question,
workspace preferences, credentials or tools. All draft and source content is
explicitly untrusted data. Prompt isolation reduces injection risk; it does not
make a model infallible.

- Knowledge uses the retrieved document chunk. Curated Q&A retains its existing
  precedence over document retrieval.
- HTTP actions supply only selected response fields, action name and execution
  state. Raw responses and authentication material are excluded.
- Spreadsheet queries supply returned columns/rows, truncation state, schema and
  the canonical executed SELECT with its bound parameters. A partial result cannot
  substantiate a whole-table total.

All three use one numbered evidence book. Multiple calls to the same source retain
their distinct snapshots. Grouped markers such as `[1, 2]` are supported; unknown,
empty or malformed numeric references and ranges are blocked. Accepted responses
return only the sources their verdict cites. Evidence stays in memory for the turn;
the rejected draft and its evidence are not written to customer history or audit.

## Failure and delivery

Invalid verdicts, unsupported claims, timeouts, transport failures and exhausted
input/output budgets fail closed. Widget and WhatsApp delivery replace the draft
with the existing localized human-help message, move a bot-owned conversation to
Waiting, mark its analytics handoff and create an unanswered staff review. An agent
can find the conversation in Inbox and the question under Knowledge → Improve
answers. Existing conversation ownership and closed-state guards still apply.

The verifier never regenerates the answer or re-executes tools. A completed external
write stays complete even if verification fails; staff should inspect action
history before taking further action. Existing deterministic booking, payment,
approval, code and action-status messages remain server-rendered from recorded
state. They do not need a model to verify their wording.

WhatsApp renders checked source labels without cutting the answer or source
footer. If the complete reply exceeds its 600-character budget, it hands off.
This preserves conditions and negations that truncation could remove. Literal
customer commands and already-requested handoffs retain their existing paths.

## Configuration and operations

The request uses the draft's provider and model by default. `GROUNDING_MODELS`
optionally maps `openai`, `anthropic` or `gemini` to another model on that provider.
Existing provider credentials apply; there is no tenant bypass switch or migration.
Each model-written reply can incur an additional metered chat request.

`GROUNDING_TIMEOUT_SECONDS` defaults to 10 (range 1–30). It sets the provider SDK's
transport timeout; it is not a strict end-to-end wall-clock deadline. Automatic
verifier retries are disabled and output is capped at 1,024 tokens. Drafts are
limited to 12,000 characters, serialized verifier input to 64,000 characters and
verdicts to 2,000 characters. Strict JSON validation rejects extra fields, duplicate
keys, non-integer/duplicate citation IDs and mismatched citations.

`answer.grounding_checked` audit entries contain only acceptance, a fixed reason,
citation count and elapsed milliseconds. They contain no text, credentials or raw
provider error. Provider usage is recorded through the existing usage ledger.

## Acceptance and launch checks

Automated tests use real Postgres/Redis and fake models/receivers. They cover source
selection and FAQ precedence, malformed and unsupported answers, provider failure,
interactive claims, SQL evidence, selected HTTP fields, completed-write replay,
session-free verification, WhatsApp length handling and queued widget handoff.
Browser acceptance verifies delivery, staff Inbox, unanswered review, axe and
320-pixel layout. See the [handoff screenshot](screenshots/t10-5-grounding-handoff.png).

Live-model calibration remains pending credentials. Before launch, evaluate the
selected draft/verifier models on representative English and Indian-language
answers: unsupported numbers, omitted conditions, negation, conflicting sources,
prompt injection, SQL totals and action outcomes. Review false accepts, false
handoffs, latency and cost. Fake-provider checks establish application behavior;
they do not establish a real model's factual accuracy.
