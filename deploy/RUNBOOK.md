# Staging and production operations

## Provision each environment

Use an India-region host with Docker Engine and Compose v2, managed PostgreSQL
16 or later with pgvector and point-in-time recovery, durable managed Redis
with persistence and backups, and a private, versioned S3-compatible bucket
with access logging and recoverable backups. Keep snapshots, recovery copies
and database traffic in the same approved region. Give staging its own
database, queue, bucket, keys and provider test assets.

Create the database and its owner, enable pgvector, and keep access restricted
to the deployment and recovery hosts over TLS. The app database role needs
schema ownership for migrations and permission to create and grant the
per-tenant reader roles if spreadsheet queries are enabled. Restrict the
storage identity to its bucket and confirm that staff can reach the private
storage endpoint used by authenticated downloads.

From a checkout of the release, make private copies of the environment files.
Set the image tags to the release SHA, choose a different Compose project name
for each environment, and fill in the app's connection and secret settings.
Use the deployment secret store for actual values and retain the key versions
needed for recovery. The app environment file is relative to `deploy/`. If the
provider uses a private TLS CA, install its trusted CA for the app and operations
clients. [App settings](../.env.example)

```bash
umask 077
cp deploy/compose.env.example deploy/compose.env
cp deploy/app.env.example deploy/app.env

dc() { docker compose --env-file deploy/compose.env -f deploy/compose.yml "$@"; }
dc config --quiet
dc build api web
```

Configure TLS at the host reverse proxy. The [Caddy example](Caddyfile.example)
routes signed provider webhooks, widget requests and public API traffic to the
API and serves the dashboard through the web container. Only the reverse proxy
needs public ports. Restrict forwarding trust to the actual proxy hops. If
using Cloudflare, use Full (strict) TLS and allow signed webhook requests
through the WAF without a browser challenge. Serve `/metrics` only to an
authorized private scraper. [Monitoring configuration](prometheus.yml)

## Deploy and verify

For a fresh environment, start the stack and wait for health checks. Compose
runs `alembic upgrade head` once before starting API, worker and scheduler.
The worker consumes the normal arq queue. The scheduler runs the cron
functions on its own queue. Run one scheduler on this single-host stack.

```bash
dc up -d --wait --wait-timeout 180
dc ps --all
curl -fsS http://127.0.0.1:8088/health
curl -fsS http://127.0.0.1:3000/login >/dev/null
```

Confirm the same routes through staging HTTPS. Verify owner login, a signed
test inbound event, queued work and a private media read with test accounts.
Check worker and scheduler health and confirm the same migration is recorded
in the database. Before launch, connect the real mail backend and complete
its DNS and delivery checks. [Email delivery](../docs/EMAIL_DELIVERY.md)

For an upgrade, record the current image tags and schema revision, take a
backup, build the new tags and put the proxy into maintenance mode. Stop
intake before stopping existing workers. If moving from the development-style
combined process, stop it before introducing the separate scheduler.

```bash
dc stop web api worker scheduler
dc up --force-recreate --exit-code-from migrate migrate
dc up -d --no-deps --wait --wait-timeout 180 worker scheduler
dc up -d --no-deps --wait --wait-timeout 180 api
dc up -d --no-deps --wait --wait-timeout 180 web
```

Run the same smoke checks before reopening traffic. Watch the queue age,
provider errors and webhook acceptance rate. Preserve the prior images and
deployment configuration until the release is stable.

## Back up and restore

Keep managed daily database backups and PITR enabled. Exercise the following
portable backup and restore commands from an operations host with PostgreSQL
client tools at least as new as the server. Configure `PGHOST`, `PGPORT`,
`PGUSER`, `PGSSLMODE=verify-full`, `PGSSLROOTCERT` and a protected
`PGPASSFILE`. Use a controlled recovery host and an explicit database name.
Back up the key versions separately in the secret store.

```bash
PGDATABASE=chatbot bash deploy/backup.sh /srv/private-backups
# Provision an empty, isolated recovery database with pgvector and its app owner.
PGDATABASE=chatbot_recovery bash deploy/restore.sh /srv/private-backups/<backup-set>
```

The backup script publishes a checksummed PostgreSQL archive only after
validation. Move the bundle to encrypted, access-controlled off-host
storage. Record the database backup time and matching object-storage recovery
point. Recover private object versions into an isolated bucket and verify a
sample authenticated download against restored message records. Redis is
backed up through the hosting service. Review pending work against database
state before reconnecting a recovered queue or allowing provider delivery.

The restore script uses one transaction, rejects a nonempty target and assigns
object ownership to the connection role. It restores data and schema without
source-cluster grants. Point the app environment at the recovered database,
queue and bucket before repairing spreadsheet access. Reader roles are derived
from the target database ID and must be rebuilt after a logical restore.

```bash
dc run --rm --no-deps api python -m app.spreadsheets.restore_roles
dc run --rm --no-deps migrate
```

Before enabling traffic or starting production workers, verify restored
tenant counts, one known tenant's encrypted credential decryption, attachment
availability and a read-only spreadsheet query when used. Test against the
isolated environment with provider delivery disabled. Record the revision,
backup time, restore time and smoke-check results.

## Roll back

Take intake offline and stop the new scheduler and worker. Review the migration
notes for the two releases. If the old images accept the existing schema, set
the recorded old tags in the Compose environment and start the old stack
without running an older migration over a newer schema. If schema rollback is
required, test the documented downgrade on a recovery copy first. For an
irreversible migration, recover to an isolated point in time and reconcile work
accepted since that point before promotion. Repeat the smoke checks and monitor
queue and webhook metrics as traffic resumes.

## Rotate secrets and respond to an incident

Stage new provider secrets, update their matching webhook subscriptions or
permissions, deploy the new settings and check a staging request before
retiring the old credential. For master-key rotation, increment
`MASTER_KEY_VERSION`, retain the previous version in `MASTER_KEY_PREVIOUS`
and use the documented rewrap procedure. Keep old versions required by system
ciphertext and retained backups in the recovery secret store. Verify
decryption before retiring a version from live configuration.
[Key rotation](../README.md#tenant-secret-encryption-t18)

For suspected exposure, restrict access and put the affected ingress or
workers into maintenance mode if sending would worsen the incident.
Preserve audit logs, access logs and snapshots in restricted storage. Record
times, affected tenants and credential classes. Revoke exposed sessions and
provider credentials, rotate affected keys, and notify the owners responsible
for incident response and regulatory deadlines. Restore from a verified
recovery point if integrity is uncertain and check tenant isolation and
delivery before reopening traffic.
