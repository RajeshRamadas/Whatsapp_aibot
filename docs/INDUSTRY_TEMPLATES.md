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
