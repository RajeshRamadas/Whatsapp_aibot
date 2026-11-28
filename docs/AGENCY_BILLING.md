# Consolidated agency billing

Choose the agency's billing workspace in **Billing**. Under **Agency billing**,
link a client workspace you also own. The agency workspace uses its usual
Razorpay checkout, subscription and GST invoicing for the group. Linking keeps
the client's ordinary workspace memberships and private data separate.

One AI-message quota and overage policy apply to the billing workspace and its
linked clients. The agency owner can see billed usage by client. A client owner
can see the plan and their own usage without accessing the agency's checkout or
other clients' records. Workspace-specific resource limits remain local.

A client with an active or incomplete direct subscription must finish it before
linking. A client can belong to one agency and groups cannot be nested. When a
client is unlinked, new usage returns to its own billing account. Usage already
charged to the agency remains in its monthly total. Historical workspace invoices
stay with the account that purchased them.

Apply migration `0062_agency_billing`. Use the existing Razorpay test-mode flow
before accepting live consolidated charges. [Billing setup](../README.md#razorpay-subscriptions)
