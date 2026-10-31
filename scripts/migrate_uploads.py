"""Move legacy local source files into configured object storage.

Run once after configuring storage; safe to rerun. No database session is held
while transferring files. Keep legacy files until you verify the migration.
"""
from pathlib import Path

from sqlalchemy import select

from app.db import Session, Source
from app.services.storage import storage


def main():
    with Session() as db:
        sources = [(row.id, row.tenant_id, row.file_path) for row in db.scalars(
            select(Source).where(~Source.file_path.startswith('storage:'))).all()]
    store = storage()
    for source_id, tenant_id, old_path in sources:
        path = Path(old_path)
        if not path.is_file():
            print(f'Skip missing file for source {source_id}')
            continue
        key = f'{tenant_id}/{source_id}{path.suffix.lower()}'
        store.put(key, path.read_bytes())
        with Session.begin() as db:
            row = db.get(Source, source_id)
            if row and row.file_path == old_path:
                row.file_path = 'storage:' + key
        print(f'Migrated source {source_id}')


if __name__ == '__main__':
    main()
