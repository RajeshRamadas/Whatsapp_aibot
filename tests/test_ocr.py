"""Offline native OCR, page provenance, metering, worker isolation and failure behavior."""
import asyncio
import hashlib
import os
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import func, select

from app import ingest, llm
from app.api import knowledge
from app.config import settings
from app.db import ApiKey, Chunk, Session, Source, UsageEvent
from app.jobs.worker import ingest_source
from app.services import ocr
from tests.conftest import FakeEmbedder
from tests.ocr_fixtures import mixed_pdf, scan
from tests.test_media import no_session_io as no_session_io


def recognized(args):
    return b'eng\nhin\n' if '--list-langs' in args else b'Refunds accepted within 30 days.'


def test_real_ocr_preserves_native_pdf_text_and_scanned_page_numbers(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'ocr_languages', 'eng')
    monkeypatch.setenv('PATH', '/usr/bin:' + os.environ['PATH'])
    path = tmp_path / 'policy.pdf'
    path.write_bytes(mixed_pdf())
    pages = []
    blocks = ingest.extract(path, '.pdf', on_ocr_page=pages.append)
    assert [(row.page, row.text) for row in blocks if row.page == 1] == [(1, 'Shipping is free.')]
    assert 'refunds accepted within 30 days' in ' '.join(row.text for row in blocks if row.page == 2).lower()
    assert {row.page for row in blocks} == {1, 2} and pages == [2, 3]
    path = tmp_path / 'policy.png'
    path.write_bytes(scan())
    assert ingest.extract(path, '.png')[0].page == 1


def test_image_upload_integrity_auth_and_tenant_isolation(client, tenant, monkeypatch):
    monkeypatch.setattr(ocr, 'run', recognized)
    data = scan()
    body = {'filename': 'refunds.png', 'content_type': 'image/png', 'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest()}
    endpoint = '/api/v1/sources/uploads'
    assert client.post(endpoint, json=body).status_code == 401
    with Session.begin() as db:
        db.add(ApiKey(tenant_id=tenant['tenant_id'], name='Reader', key_prefix='fixture', scopes=['viewer'],
                     key_hash=hashlib.sha256(b'ocr-viewer').hexdigest()))
    assert client.post(endpoint, json=body, headers={'Authorization': 'Bearer ocr-viewer'}).status_code == 403
    assert client.post(endpoint, json={**body, 'filename': 'image.svg'}, headers=tenant['headers']).status_code == 422
    response = client.post(endpoint, json=body, headers=tenant['headers'])
    assert response.status_code == 201
    upload = response.json()
    other = client.post('/api/v1/tenants', headers={'X-Bootstrap-Token': 'test-bootstrap'},
                        json={'name': 'Other OCR'}).json()
    foreign = {'X-Admin-Key': other['admin_key']}
    assert client.put(upload['upload_url'], content=data, headers=foreign).status_code == 404
    assert client.put(upload['upload_url'], content=data, headers=tenant['headers']).status_code == 204
    complete = f'/api/v1/sources/{upload["source_id"]}/complete'
    metadata = {'size': len(data), 'sha256': body['sha256']}
    assert client.post(complete, json=metadata, headers=foreign).status_code == 404
    assert client.post(complete, json={**metadata, 'sha256': '0' * 64}, headers=tenant['headers']).status_code == 422
    assert client.post(complete, json=metadata, headers=tenant['headers']).status_code == 202
    answer = client.post(f'/api/v1/chat/{tenant["public_key"]}', json={'message': 'refunds?'}).json()
    assert answer['sources'][0]['filename'] == 'refunds.png' and answer['sources'][0]['page'] == 1
    assert client.get('/api/v1/sources', headers=foreign).json() == []


def test_retry_is_atomic_metered_once_and_does_not_hold_sessions(client, tenant, monkeypatch, no_session_io):
    async def queued(name, **kwargs):
        return kwargs['_job_id']
    monkeypatch.setattr(knowledge, 'enqueue', queued)
    calls = []
    def recognize(args):
        assert no_session_io()
        calls.append(args)
        return recognized(args)
    monkeypatch.setattr(ocr, 'run', recognize)
    source_id = client.post('/api/v1/sources', headers=tenant['headers'], files={
        'file': ('refunds.png', scan(), 'image/png')}).json()['source_id']
    class Flaky(FakeEmbedder):
        failed = False
        def embed(self, texts, **kwargs):
            assert no_session_io()
            if not self.failed:
                self.failed = True
                raise RuntimeError('fixture provider failure')
            return super().embed(texts, **kwargs)
    embedder = Flaky()
    monkeypatch.setattr(llm, 'embedder', lambda: embedder)
    asyncio.run(ingest_source({}, source_id, 'foreign-workspace'))
    assert not calls
    with pytest.raises(RuntimeError):
        asyncio.run(ingest_source({'job_try': 1}, source_id, tenant['tenant_id']))
    with Session() as db:
        assert db.get(Source, source_id).status == 'queued'
        assert db.scalar(select(func.count()).select_from(Chunk)) == 0
    asyncio.run(ingest_source({'job_try': 2}, source_id, tenant['tenant_id']))
    with Session() as db:
        assert db.get(Source, source_id).status == 'ready'
        assert db.scalar(select(func.count()).select_from(Chunk)) == 1
        rows = db.scalars(select(UsageEvent).where(UsageEvent.tenant_id == tenant['tenant_id'],
                                                 UsageEvent.kind == 'ocr')).all()
        assert len(rows) == 1 and rows[0].ocr_pages == 1 and rows[0].cost_usd == 0


def test_limits_corrupt_images_and_native_timeout(tmp_path, monkeypatch):
    path = tmp_path / 'image.png'
    path.write_bytes(b'invalid image')
    with pytest.raises(ocr.OCRError, match='valid'):
        ingest.extract(path, '.png')
    image = Image.new('RGB', (2000, 1000), 'white')
    image.save(path)
    monkeypatch.setattr(settings, 'ocr_max_pixels', 1000000)
    with pytest.raises(ocr.OCRError, match='large'):
        ingest.extract(path, '.png')
    path = tmp_path / 'frames.tiff'
    image = Image.new('RGB', (10, 10), 'white')
    image.save(path, save_all=True, append_images=[image])
    monkeypatch.setattr(settings, 'ocr_max_pages', 1)
    with pytest.raises(ocr.OCRError, match='pages'):
        ingest.extract(path, '.tiff')
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('fixture', 1)
    monkeypatch.setattr(ocr.subprocess, 'run', timeout)
    with pytest.raises(ocr.OCRError, match='too long'):
        ocr.run(['tesseract', 'fixture.png', 'stdout'])


def test_native_process_is_limited_and_never_exposes_stderr(monkeypatch):
    def native(command, **kwargs):
        assert command[:2] == ['prlimit', '--as=536870912'] and '--cpu=30' in command
        assert kwargs['env']['OMP_THREAD_LIMIT'] == '1' and kwargs['stderr'] == subprocess.DEVNULL
        return SimpleNamespace(stdout=b'visible text')
    monkeypatch.setattr(ocr.subprocess, 'run', native)
    assert ocr.run(['tesseract', 'input.png', 'stdout']) == b'visible text'


def test_missing_language_and_unreadable_scan_are_visible_failures(client, tenant, monkeypatch):
    monkeypatch.setattr(ocr, 'run', lambda args: b'eng\n')
    source_id = client.post('/api/v1/sources', headers=tenant['headers'], files={
        'file': ('scan.png', scan(), 'image/png')}).json()['source_id']
    with Session() as db:
        source = db.get(Source, source_id)
        assert source.status == 'failed' and 'language data' in source.error
    monkeypatch.setattr(ocr, 'run', lambda args: b'eng\nhin\n' if '--list-langs' in args else b'')
    source_id = client.post('/api/v1/sources', headers=tenant['headers'], files={
        'file': ('blank.png', scan(), 'image/png')}).json()['source_id']
    with Session() as db:
        assert db.get(Source, source_id).status == 'failed'
        assert db.scalar(select(func.count()).select_from(Chunk)) == 0


def test_deleted_source_is_not_resurrected_after_embedding(client, tenant, monkeypatch):
    monkeypatch.setattr(ocr, 'run', recognized)
    class Deleting(FakeEmbedder):
        def embed(self, texts, **kwargs):
            with Session.begin() as db:
                source = db.scalar(select(Source).where(Source.tenant_id == tenant['tenant_id']))
                db.delete(source)
            return super().embed(texts, **kwargs)
    monkeypatch.setattr(llm, 'embedder', lambda: Deleting())
    response = client.post('/api/v1/sources', headers=tenant['headers'], files={
        'file': ('scan.png', scan(), 'image/png')})
    assert response.status_code == 202
    with Session() as db:
        assert not db.scalar(select(Source)) and not db.scalar(select(Chunk))
