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
        SELECT FROM pg_depend AS object
        WHERE object.refclassid = 'pg_namespace'::regclass AND object.refobjid = 'public'::regnamespace
            AND object.deptype = 'n'
            AND NOT EXISTS (
                SELECT FROM pg_extension AS extension WHERE extension.extname = 'vector' AND (
                    (object.classid = 'pg_extension'::regclass AND object.objid = extension.oid)
                    OR EXISTS (
                        SELECT FROM pg_depend AS member
                        WHERE member.classid = object.classid AND member.objid = object.objid
                            AND member.objsubid = object.objsubid AND member.deptype = 'e'
                            AND member.refclassid = 'pg_extension'::regclass AND member.refobjid = extension.oid
                    )
                )
            )
    )")
if [[ "$connected" != "$PGDATABASE" || "$empty" != t ]]; then
    printf 'Restore requires an explicitly named, empty recovery database.\n' >&2
    exit 1
fi

pg_restore --exit-on-error --single-transaction --no-owner --no-privileges \
    --dbname="$PGDATABASE" "$backup/database.dump"
