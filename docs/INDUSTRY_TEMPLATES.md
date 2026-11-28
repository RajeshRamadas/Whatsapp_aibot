# Industry templates

Select **Beauty salon** when creating a workspace for a business like Beautiva
that offers salon appointments and AI look previews. The template sets a
friendly front-desk persona, asks for the guest's name, desired service and
preferred time, and adds ten editable questions under **Analytics → Accuracy**.
Choose **Other** to create a general workspace with the default settings.

The creation screen previews sample conversations and suggested knowledge. The
dashboard keeps the suggestions visible until a knowledge source is ready.
Add your current service menu, prices, staff and booking policies. If the salon
offers AI look previews, document the actual steps, photo consent and retention
policy. Review the persona, lead settings, and accuracy questions before going
live.

## Real-model acceptance

1. Create a disposable workspace with the Beauty salon template.
2. Upload [the sample salon reference](examples/beauty-salon-sample.txt), keeping
   its filename. Wait for ingestion to finish.
3. Run the saved questions in **Analytics → Accuracy** using the configured real
   provider. Record the provider, model, score and case results. The acceptance
   target is at least 90% accuracy and both decline cases passing.
4. Use the salon's real documents and edit the tests to match them in the
   actual workspace.

The sample uses invented salon policies for evaluation. The application keeps
each new bot paused for review.

## T8.1 acceptance record

The app's Accuracy worker ran all ten saved questions on November 28, 2026
using the sample salon file in a disposable workspace at revision `fa055da`.
Both runs used llama.cpp b12833 and Nomic Embed Text v1.5 F16 embeddings
(768 dimensions). The Qwen3 run used a 30-second grounding timeout for local
CPU inference.

| Chat model (Q4_K_M) | Accuracy | Knowledge cases with the expected source | Decline cases passed |
|---|---:|---:|---:|
| Qwen2.5-3B-Instruct | 2/10 (20%) | 0/8 | 2/2 |
| Qwen3-4B-Instruct-2507 | 10/10 (100%) | 8/8 | 2/2 |

Qwen3 met the 90% target with zero provider errors. Run ID
`0a435bf1-6455-4fc6-97ff-4a4730fefdbb`. Qwen2.5 failed the source check
on all eight knowledge cases.

| Saved case | Qwen3 result |
|---|---|
| Haircut duration | 45 minutes, with source |
| Blow-dry duration | 30 minutes, with source |
| Monday opening | Closed on Monday, with source |
| Sunday opening | 11 a.m. on Sunday, with source |
| Booking reschedule | Up to 24 hours before the start time, with source |
| Consent for an AI preview photo | Explicit consent required, with source |
| AI preview guarantee | Illustrative only, with source |
| Styling consultation fee | Complimentary, with source |
| Unlisted keratin treatment price | Declined to quote a price |
| Unlisted parking validation code | Handed off without supplying a code |
