# White-label agency domains

In the agency billing workspace, open **Billing → Agency branding**. Save the
public brand name, a public HTTPS logo URL, and two colors. Add the custom
hostname and publish the displayed DNS TXT record. Select **Verify domain**
after the record is visible in DNS. Only one agency can verify a hostname.
Changing or clearing the hostname removes its previous verification. Make
hostname changes from the canonical dashboard so settings remain reachable.

Point the custom hostname at the deployment ingress. Caddy can obtain a
certificate on demand after the API verifies the hostname. The example also
checks verification on incoming branded requests, including after a certificate
was issued. Keep the canonical site's explicit host block and merge the global
TLS options with its existing settings. [Caddy example](../deploy/Caddyfile.agency.example)

A verified host applies the logo, name and colors to sign-in and the dashboard.
Cookie sessions stay on their own host. People can use only their existing
memberships in the agency and its linked workspaces on that host. Billing and
branding controls stay with the agency billing workspace owner.

Apply migration `0063_agency_branding`. DNS publication, ingress routing and
TLS issuance are verified in the real deployment after an agency supplies its
domain. Provider emails and integration callbacks continue using the configured
canonical app URL. [Deployment runbook](../deploy/RUNBOOK.md)
