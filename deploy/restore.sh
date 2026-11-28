#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 1 ]]; then
    printf 'Usage: %s BACKUP_SET\n' "$0" >&2
    exit 2
fi
: "${PGDATABASE:?Set PGDATABASE to the new, isolated recovery database name}"

backup=$(realpath -- "$1")
expected=$(cat -- "$backup/SHA256SUMS")
actual=$(cd "$backup" && sha256sum database.dump)
if [[ "$expected" != "$actual" ]]; then
    printf 'Backup checksum does not match.\n' >&2
    exit 1
fi

connected=$(psql -X -Atq -v ON_ERROR_STOP=1 -c 'SELECT current_database()')
empty=$(psql -X -Atq -v ON_ERROR_STOP=1 -c "
    SELECT NOT EXISTS (
        SELECT FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN ('public', 'information_schema')
    ) AND NOT EXISTS (
        SELECT FROM pg_class WHERE relnamespace = 'public'::regnamespace
            AND relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
    )")
if [[ "$connected" != "$PGDATABASE" || "$empty" != t ]]; then
    printf 'Restore requires an explicitly named, empty recovery database.\n' >&2
    exit 1
fi

pg_restore --exit-on-error --single-transaction --no-owner --no-privileges \
    --dbname="$PGDATABASE" "$backup/database.dump"
