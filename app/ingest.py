import csv
import json
from pathlib import Path

from docx import Document as WordDocument
from openpyxl import load_workbook
from pypdf import PdfReader
from sqlalchemy import delete

from . import llm
from .db import Chunk, Session, Source

SUPPORTED_SUFFIXES = {'.pdf', '.docx', '.txt', '.md', '.csv', '.xlsx', '.json'}
EMBED_BATCH = 32


def extract(path, suffix):
    if suffix == '.pdf':
        return '\n'.join(page.extract_text() or '' for page in PdfReader(path).pages)
    if suffix == '.docx':
        return '\n'.join(p.text for p in WordDocument(path).paragraphs)
    if suffix in ('.txt', '.md'):
        return Path(path).read_text(encoding='utf-8-sig')
    if suffix == '.json':
        obj = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        rows = obj if isinstance(obj, list) else [obj]
        return '\n'.join(json.dumps(r, ensure_ascii=False) for r in rows)
    if suffix == '.csv':
        with open(path, encoding='utf-8-sig', newline='') as f:
            return '\n'.join(json.dumps(row, ensure_ascii=False) for row in csv.DictReader(f))
    if suffix == '.xlsx':
        wb = load_workbook(path, read_only=True, data_only=True)
        parts = []
        for ws in wb:
            it = ws.values
            headers = next(it, None)
            if headers:
                for row in it:
                    parts.append(json.dumps({'sheet': ws.title, **{str(k): v for k, v in zip(headers, row)}},
                                            default=str, ensure_ascii=False))
        wb.close()
        return '\n'.join(parts)
    raise ValueError('Unsupported format')


def split_text(content, limit=1600, overlap=180):
    # Character-based baseline. Replace with token/structure-aware chunking before scale.
    if not content.strip():
        return []
    return [content[i:i + limit] for i in range(0, len(content), limit - overlap)]


def ingest(source_id: str) -> None:
    with Session() as db:
        src = db.get(Source, source_id)
        if not src:
            return
        src.status = 'processing'
        db.commit()
        try:
            content = extract(src.file_path, Path(src.file_path).suffix.lower())
            chunks = split_text(content)
            if not chunks:
                raise ValueError('No extractable text; OCR is not implemented')
            embedder = llm.embedder()
            rows = []
            for i in range(0, len(chunks), EMBED_BATCH):
                batch = chunks[i:i + EMBED_BATCH]
                rows += [Chunk(tenant_id=src.tenant_id, source_id=src.id, content=value, embedding=vector)
                         for value, vector in zip(batch, embedder.embed(batch))]
            # All chunks land together with the status change, so a failure leaves none behind.
            db.add_all(rows)
            src.status, src.error = 'ready', None
            db.commit()
        except Exception as e:
            db.rollback()
            db.execute(delete(Chunk).where(Chunk.source_id == source_id))
            src = db.get(Source, source_id)
            src.status, src.error = 'failed', str(e)[:500]
            db.commit()
