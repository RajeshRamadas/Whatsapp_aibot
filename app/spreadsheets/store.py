"""Only the importer owns DDL. The query role receives SELECT on its own schema."""
import re
from uuid import UUID

from psycopg import sql
from sqlalchemy import delete, select, text

from ..db import Session, Tenant, engine, uid
from ..models.spreadsheets import SpreadsheetTable

TYPES = {'text': 'TEXT', 'numeric': 'NUMERIC', 'boolean': 'BOOLEAN', 'date': 'DATE'}
q = engine.dialect.identifier_preparer.quote


def schema(tenant_id):
    return 't_' + UUID(tenant_id).hex


def role(db, tenant_id):
    oid = db.scalar(text('SELECT oid FROM pg_database WHERE datname = current_database()'))
    return f'r_{oid}_' + UUID(tenant_id).hex


def provision(db, tenant_id):
    namespace, reader = schema(tenant_id), role(db, tenant_id)
    if not db.scalar(text('SELECT 1 FROM pg_roles WHERE rolname=:name'), {'name': reader}):
        db.execute(text(f'CREATE ROLE {q(reader)} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS'))
        owner = db.scalar(text('SELECT current_user'))
        db.execute(text(f'GRANT {q(reader)} TO {q(owner)}'))
    db.execute(text(f'CREATE SCHEMA IF NOT EXISTS {q(namespace)}'))
    db.execute(text(f'REVOKE ALL ON SCHEMA {q(namespace)} FROM PUBLIC'))
    db.execute(text(f'GRANT USAGE ON SCHEMA {q(namespace)} TO {q(reader)}'))
    return namespace, reader


def remove(db, tenant_id, source_id):
    db.get(Tenant, tenant_id, with_for_update=True)
    rows = db.scalars(select(SpreadsheetTable).where(SpreadsheetTable.tenant_id == tenant_id,
                      SpreadsheetTable.source_id == source_id)).all()
    for row in rows:
        db.execute(text(f'DROP TABLE IF EXISTS {q(schema(tenant_id))}.{q(row.table_name)}'))
    db.execute(delete(SpreadsheetTable).where(SpreadsheetTable.tenant_id == tenant_id,
                                            SpreadsheetTable.source_id == source_id))


def publish(db, tenant_id, source_id, sheets):
    # Caller holds the tenant then source locks, after all parsing/provider IO.
    namespace, reader = provision(db, tenant_id)
    remove(db, tenant_id, source_id)
    for i, sheet in enumerate(sheets):
        table = 's_' + UUID(source_id).hex + f'_{i}'
        fields = ', '.join(f'{q(c["name"])} {TYPES[c["type"]]}' for c in sheet.columns)
        # Cascading data deletion remains atomic even for non-API source/tenant deletes.
        db.execute(text(f'CREATE TABLE {q(namespace)}.{q(table)} ('
            f'_source_id VARCHAR(36) REFERENCES public.sources(id) ON DELETE CASCADE, {fields})'))
        db.execute(text(f'REVOKE ALL ON {q(namespace)}.{q(table)} FROM PUBLIC'))
        db.execute(text(f'GRANT SELECT ON {q(namespace)}.{q(table)} TO {q(reader)}'))
        driver = db.connection().connection.driver_connection
        with driver.cursor() as cursor:
            statement = sql.SQL('COPY {}.{} FROM STDIN').format(sql.Identifier(namespace), sql.Identifier(table))
            with cursor.copy(statement) as copy:
                for row in sheet.rows:
                    copy.write_row([source_id, *row])
        db.add(SpreadsheetTable(id=uid(), tenant_id=tenant_id, source_id=source_id, position=i,
            title=sheet.title, table_name=table, columns=sheet.columns, row_count=len(sheet.rows), enabled=False))


def cleanup(*, reset=False):
    """Discard orphan storage, or reset this feature's storage before a test DB rebuild."""
    with Session.begin() as db:
        db.execute(text("SET LOCAL lock_timeout = '500ms'"))
        namespaces = db.scalars(text("SELECT nspname FROM pg_namespace WHERE nspname ~ '^t_[a-f0-9]{32}$'")).all()
        for namespace in namespaces:
            identity = str(UUID(namespace[2:]))
            active = None if reset else db.scalar(select(Tenant.id).where(Tenant.id == identity)
                                                 .with_for_update(skip_locked=True))
            if active is None:
                if not reset and db.get(Tenant, identity) is not None:
                    continue  # A publisher/deleter owns this tenant; try next sweep.
                db.execute(text(f'DROP SCHEMA {q(namespace)} CASCADE'))
                db.execute(text(f'DROP ROLE IF EXISTS {q(role(db, identity))}'))
            else:
                tables = set(db.scalars(select(SpreadsheetTable.table_name)
                                       .where(SpreadsheetTable.tenant_id == identity)))
                names = db.scalars(text('SELECT tablename FROM pg_tables WHERE schemaname=:schema'),
                                   {'schema': namespace}).all()
                for name in names:
                    if re.fullmatch(r's_[a-f0-9]{32}_[0-9]{1,2}', name) and name not in tables:
                        db.execute(text(f'DROP TABLE {q(namespace)}.{q(name)}'))
