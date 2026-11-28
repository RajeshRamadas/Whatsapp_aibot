"""Restore the database-specific read grants after a logical backup restore."""
from sqlalchemy import select, text

from ..db import Session
from ..models.spreadsheets import SpreadsheetTable
from .store import provision, q


def restore_roles():
    with Session.begin() as db:
        tables = db.execute(select(SpreadsheetTable.tenant_id, SpreadsheetTable.table_name)
                            .order_by(SpreadsheetTable.tenant_id, SpreadsheetTable.table_name))
        readers = {}
        for tenant_id, table_name in tables:
            if tenant_id not in readers:
                readers[tenant_id] = provision(db, tenant_id)
            namespace, reader = readers[tenant_id]
            qualified = f'{q(namespace)}.{q(table_name)}'
            db.execute(text(f'REVOKE ALL ON {qualified} FROM PUBLIC'))
            db.execute(text(f'GRANT SELECT ON {qualified} TO {q(reader)}'))


if __name__ == '__main__':
    restore_roles()
