"""Turn uploaded files into searchable chunks.

Files are parsed into blocks (paragraphs, table rows, spreadsheet rows) that
remember their heading and page. Blocks are packed into chunks without
crossing a heading or page, so a section or table row is never cut in half,
and each chunk is embedded with its document and heading as context.
"""
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

from docx import Document as WordDocument
from docx.table import Table
from openpyxl import load_workbook
from pypdf import PdfReader
from sqlalchemy import delete

from . import llm
from .db import Chunk, Session, Source

SUPPORTED_SUFFIXES = {'.pdf', '.docx', '.txt', '.md', '.csv', '.xlsx', '.json'}
EMBED_BATCH = 32
CHUNK_CHARS = 1200


@dataclass
class Block:
    text: str
    heading: str | None = None
    page: int | None = None
    row: bool = False  # a table or spreadsheet row: never overlapped into the next chunk


@dataclass
class Piece:
    content: str
    heading: str | None
    page: int | None


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]


def render_row(record: dict) -> str:
    """'name: Basic; price: 99'. Keeps column names next to values so a lone row still makes sense."""
    parts = []
    for key, value in record.items():
        if value is None or value == '':
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, default=str)
        parts.append(f'{key}: {value}')
    return '; '.join(parts)


class Headings:
    """Tracks the heading path, e.g. 'Policies > Refunds'."""

    def __init__(self):
        self.stack: list[tuple[int, str]] = []

    def push(self, level: int, title: str):
        self.stack = [(lvl, t) for lvl, t in self.stack if lvl < level] + [(level, title.strip())]

    @property
    def path(self) -> str | None:
        return ' > '.join(t for _, t in self.stack) or None


def markdown_blocks(text: str) -> list[Block]:
    blocks, headings, buffer = [], Headings(), []

    def flush():
        blocks.extend(Block(p, headings.path) for p in paragraphs('\n'.join(buffer)))
        buffer.clear()

    for line in text.splitlines():
        m = re.match(r'^(#{1,6})\s+(.+?)\s*#*\s*$', line)
        if m:
            flush()
            headings.push(len(m.group(1)), m.group(2))
        else:
            buffer.append(line)
    flush()
    return blocks


def docx_blocks(path) -> list[Block]:
    blocks, headings = [], Headings()
    for item in WordDocument(path).iter_inner_content():
        if isinstance(item, Table):
            rows = [[cell.text.strip() for cell in row.cells] for row in item.rows]
            if not rows:
                continue
            header, body = rows[0], rows[1:]
            blocks += [Block(render_row(dict(zip(header, r))), headings.path, row=True) for r in body] \
                or [Block(' | '.join(header), headings.path)]
            continue
        text, style = item.text.strip(), (item.style.name if item.style is not None else '')
        if not text:
            continue
        level = 1 if style == 'Title' else int(m.group(1)) if (m := re.match(r'Heading (\d)', style)) else 0
        if level:
            headings.push(level, text)
        else:
            blocks.append(Block(text, headings.path))
    return blocks


def extract(path, suffix) -> list[Block]:
    if suffix == '.pdf':
        return [Block(p, page=n) for n, page in enumerate(PdfReader(path).pages, 1)
                for p in paragraphs(page.extract_text() or '')]
    if suffix == '.docx':
        return docx_blocks(path)
    if suffix == '.md':
        return markdown_blocks(Path(path).read_text(encoding='utf-8-sig'))
    if suffix == '.txt':
        return [Block(p) for p in paragraphs(Path(path).read_text(encoding='utf-8-sig'))]
    if suffix == '.json':
        obj = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        rows = obj if isinstance(obj, list) else [obj]
        return [Block(render_row(r) if isinstance(r, dict) else json.dumps(r, ensure_ascii=False), row=True)
                for r in rows]
    if suffix == '.csv':
        with open(path, encoding='utf-8-sig', newline='') as f:
            return [Block(render_row(row), row=True) for row in csv.DictReader(f)]
    if suffix == '.xlsx':
        wb = load_workbook(path, read_only=True, data_only=True)
        blocks = []
        for ws in wb:
            it = ws.values
            headers = next(it, None)
            if headers:
                names = [str(h) if h is not None else f'column {i + 1}' for i, h in enumerate(headers)]
                blocks += [Block(render_row(dict(zip(names, row))), heading=ws.title, row=True)
                           for row in it if any(v is not None for v in row)]
        wb.close()
        return blocks
    raise ValueError('Unsupported format')


def split_long(text: str, limit: int) -> list[str]:
    """Split an oversized block on lines, then sentences, then characters."""
    if len(text) <= limit:
        return [text]
    for pattern in (r'\n', r'(?<=[.!?])\s+'):
        parts = [p for p in re.split(pattern, text) if p.strip()]
        if len(parts) > 1:
            out, current = [], ''
            for part in parts:
                if current and len(current) + len(part) + 1 > limit:
                    out.append(current)
                    current = ''
                current = f'{current} {part}'.strip() if current else part
            out.append(current)
            return [piece for chunk in out for piece in split_long(chunk, limit)]
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def chunk_blocks(blocks: list[Block], limit: int = CHUNK_CHARS) -> list[Piece]:
    """Pack consecutive blocks with the same heading and page into chunks of up to `limit` characters.

    The last paragraph of a chunk is repeated at the start of the next one in the
    same section, so a sentence that depends on the one before keeps its context.
    """
    pieces: list[Piece] = []
    current: list[Block] = []

    def flush(carry: bool):
        if not current:
            return
        pieces.append(Piece('\n\n'.join(b.text for b in current), current[0].heading, current[0].page))
        last = current[-1]
        current.clear()
        if carry and not last.row and len(last.text) < limit // 4:
            current.append(last)

    for block in blocks:
        for text in split_long(block.text, limit):
            b = Block(text, block.heading, block.page, block.row)
            if current and (b.heading, b.page) != (current[0].heading, current[0].page):
                flush(carry=False)
            if current and sum(len(c.text) + 2 for c in current) + len(text) > limit:
                flush(carry=True)
            current.append(b)
    flush(carry=False)
    return pieces


def source_label(filename: str, heading: str | None, page: int | None) -> str:
    """'policy.pdf > Refunds (page 3)'"""
    return filename + (f' > {heading}' if heading else '') + (f' (page {page})' if page else '')


def embedding_text(filename: str, piece: Piece) -> str:
    return f'{source_label(filename, piece.heading, piece.page)}\n{piece.content}'


def ingest(source_id: str) -> None:
    with Session() as db:
        src = db.get(Source, source_id)
        if not src:
            return
        src.status = 'processing'
        db.commit()
        try:
            pieces = chunk_blocks(extract(src.file_path, Path(src.file_path).suffix.lower()))
            if not pieces:
                raise ValueError('No extractable text; OCR is not implemented')
            embedder = llm.embedder()
            rows = []
            for i in range(0, len(pieces), EMBED_BATCH):
                batch = pieces[i:i + EMBED_BATCH]
                vectors = embedder.embed([embedding_text(src.filename, p) for p in batch])
                rows += [Chunk(tenant_id=src.tenant_id, source_id=src.id, position=i + n, content=p.content,
                               heading=p.heading, page=p.page, embedding=v)
                         for n, (p, v) in enumerate(zip(batch, vectors))]
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
