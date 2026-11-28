#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $# != 1 ]]; then
    printf 'Usage: %s BACKUP_DIRECTORY\n' "$0" >&2
    exit 2
fi
: "${PGDATABASE:?Set PGDATABASE to the application database name}"

mkdir -p -- "$1"
destination=$(realpath -- "$1")
temporary=$(mktemp -d "$destination/.postgres.XXXXXXXX")
trap 'rm -rf -- "$temporary"' EXIT
release="postgres-$(date -u +%Y%m%dT%H%M%SZ)-${temporary##*.}"

pg_dump --format=custom --file="$temporary/database.dump"
pg_restore --list "$temporary/database.dump" >/dev/null
(cd "$temporary" && sha256sum database.dump > SHA256SUMS)
mv -- "$temporary" "$destination/$release"
printf '%s\n' "$destination/$release"
