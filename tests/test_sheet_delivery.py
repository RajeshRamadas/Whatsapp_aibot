"""Append-only Google exports, uncertainty recovery, revision fences and literal cell values."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import Session, engine, now
from app.integrations.lead_sync.http import HTTP, SyncError
from app.models.lead_sync import LeadConnection as Connection
from app.models.lead_sync import LeadDelivery as Delivery
from app.services import crypto, lead_sync_worker
from app.services.leads import save_lead
from tests.test_lead_sync import BASE
from tests.test_lead_sync import sync as sync


@pytest.fixture
def sheet(client, tenant, sync, monkeypatch):
    identity, conversation_id = sync
    packet = crypto.encrypt(
        tenant["tenant_id"],
        json.dumps(
            {
                "access_token": "sheet-token",
                "refresh_token": "sheet-refresh",
                "expires_at": (now() + timedelta(hours=1)).isoformat(),
            }
        ),
    )
    with Session.begin() as db:
        c = db.get(Connection, identity)
        c.provider, c.credentials_enc = "sheets", packet
        c.catalog = {"files": [{"id": "sheet1", "name": "Lead exports", "tabs": [{"id": 0, "title": "Leads"}]}]}
        for job in db.scalars(select(Delivery).where(Delivery.tenant_id == tenant["tenant_id"])):
            job.state = "complete"
    body = {"enabled": True, "confirm_sharing": True, "spreadsheet_id": "sheet1", "tab_id": 0}
    assert client.put(BASE + "/connections/" + identity, headers=tenant["headers"], json=body).status_code == 200
    rows, writes, fail = [], [], [False]

    def request(self, method, path="", **kwargs):
        assert engine.pool.checkedout() == 0
        assert self.headers["Authorization"] == "Bearer sheet-token"
        if path.endswith("/values:batchGet"):
            return {"valueRanges": [{"values": rows[:1]}, {"values": [[r[0]] for r in rows[1:]]}]}
        if method == "GET" and path.startswith("spreadsheets/"):
            return {"sheets": [{"properties": {"sheetId": 0, "title": "Leads"}}]}
        if path == "files":
            return {"files": [{"id": "sheet1", "name": "Lead exports"}]}
        if method == "POST":
            writes.append(kwargs["payload"])
            for request in kwargs["payload"]["requests"]:
                rows.extend(
                    [
                        [c["userEnteredValue"]["stringValue"] for c in r["values"]]
                        for r in request["appendCells"]["rows"]
                    ]
                )
            if fail[0]:
                fail[0] = False
                raise SyncError("provider_unavailable", retry=True, uncertain=True)
            return {"replies": [{}]}
        raise AssertionError("Unexpected provider request")

    monkeypatch.setattr(HTTP, "request", request)
    return identity, conversation_id, rows, writes, fail


def capture(tenant_id, conversation_id, fields):
    from app.db import Conversation

    with Session.begin() as db:
        save_lead(db, db.get(Conversation, conversation_id), fields)


def test_sheet_appends_versions_as_literal_cells_and_clears_delivered_payloads(client, tenant, sheet):
    identity, conversation_id, rows, writes, fail = sheet
    capture(tenant["tenant_id"], conversation_id, {"name": "Customer", "need": '=HYPERLINK("example")'})
    assert lead_sync_worker.process_one()
    capture(tenant["tenant_id"], conversation_id, {"need": "Updated requirement"})
    assert lead_sync_worker.process_one()
    assert len(rows) == 3 and len(writes) == 2
    assert rows[1][6] == '=HYPERLINK("example")' and rows[2][6] == "Updated requirement"
    assert rows[1][0] != rows[2][0]
    with Session() as db:
        jobs = db.scalars(
            select(Delivery).where(Delivery.tenant_id == tenant["tenant_id"], Delivery.kind == "sync")
        ).all()
        assert all(j.state == "complete" and j.payload == {} for j in jobs)


def test_sheet_uncertain_append_reconciles_same_event_without_duplicate_rows(client, tenant, sheet):
    identity, conversation_id, rows, writes, fail = sheet
    capture(tenant["tenant_id"], conversation_id, {"name": "Customer"})
    fail[0] = True
    assert lead_sync_worker.process_one() and len(rows) == 2
    with Session.begin() as db:
        job = db.scalar(select(Delivery).where(Delivery.tenant_id == tenant["tenant_id"], Delivery.kind == "sync"))
        assert job.state == "queued" and job.submitted
        job.next_attempt_at = now()
    assert lead_sync_worker.process_one() and len(rows) == 2 and len(writes) == 1


def test_sheet_revocation_and_changed_headers_fail_closed(client, tenant, sheet):
    identity, conversation_id, rows, writes, fail = sheet
    rows.append(["Existing business columns"])
    capture(tenant["tenant_id"], conversation_id, {"name": "Customer"})
    assert lead_sync_worker.process_one() and not writes
    with Session() as db:
        job = db.scalar(select(Delivery).where(Delivery.tenant_id == tenant["tenant_id"], Delivery.kind == "sync"))
        assert job.state == "failed" and job.last_error == "export_header_mismatch"
    capture(tenant["tenant_id"], conversation_id, {"name": "Updated customer"})
    client.request("DELETE", BASE + "/connections/" + identity, headers=tenant["headers"], json={"confirm": True})
    assert lead_sync_worker.process_one() and not writes
    with Session() as db:
        jobs = db.scalars(
            select(Delivery).where(Delivery.tenant_id == tenant["tenant_id"], Delivery.kind == "sync")
        ).all()
        assert jobs[-1].state == "skipped"


def test_uncertain_spreadsheet_creation_reuses_the_app_file(client, tenant, sheet, monkeypatch):
    identity, _, _, _, _ = sheet
    files, posts = [], []
    original = HTTP.request

    def request(self, method, path="", **kwargs):
        assert engine.pool.checkedout() == 0
        if path == "files":
            if method == "POST":
                posts.append(kwargs["payload"])
                files.append({"id": "created_file", "name": "Lead exports"})
                raise SyncError("provider_unavailable", retry=True, uncertain=True)
            return {"files": files[:]}
        return original(self, method, path, **kwargs)

    monkeypatch.setattr(HTTP, "request", request)
    response = client.post(
        BASE + "/connections/" + identity + "/spreadsheet", headers=tenant["headers"], json={"title": "Lead exports"}
    )
    assert response.status_code == 200
    assert lead_sync_worker.process_one() and len(posts) == 1
    with Session.begin() as db:
        job = db.scalar(
            select(Delivery).where(Delivery.tenant_id == tenant["tenant_id"], Delivery.kind == "create_sheet")
        )
        assert job.submitted and job.state == "queued"
        assert posts[0]["appProperties"]["lead_sync_job"] == job.id
        job.next_attempt_at = now()
    assert lead_sync_worker.process_one() and len(posts) == 1
    with Session() as db:
        c = db.get(Connection, identity)
        assert c.catalog["files"][0]["id"] == "created_file" and c.catalog["files"][0]["tabs"][0]["id"] == 0


def test_worker_retries_five_times_then_keeps_failed_snapshot(client, tenant, sheet, monkeypatch):
    identity, conversation_id, _, _, _ = sheet
    capture(tenant["tenant_id"], conversation_id, {"name": "Customer"})

    def unavailable(*args, **kwargs):
        assert engine.pool.checkedout() == 0
        raise SyncError("provider_unavailable", retry=True, wait=120)

    monkeypatch.setattr(HTTP, "request", unavailable)
    for attempt in range(1, 6):
        assert lead_sync_worker.process_one()
        with Session.begin() as db:
            job = db.scalar(select(Delivery).where(Delivery.tenant_id == tenant["tenant_id"], Delivery.kind == "sync"))
            assert job.attempts == attempt and job.payload["name"] == "Customer"
            assert job.state == ("queued" if attempt < 5 else "failed")
            if attempt < 5:
                assert job.next_attempt_at >= now() + timedelta(seconds=115)
                job.next_attempt_at = now()
    assert not lead_sync_worker.process_one()


def test_revoked_credentials_pause_other_exports_until_connection_test(client, tenant, sheet, monkeypatch):
    identity, conversation_id, _, _, _ = sheet
    capture(tenant["tenant_id"], conversation_id, {"name": "Customer"})

    def revoked(*args, **kwargs):
        assert engine.pool.checkedout() == 0
        raise SyncError("credentials_required")

    monkeypatch.setattr(HTTP, "request", revoked)
    assert lead_sync_worker.process_one()
    capture(tenant["tenant_id"], conversation_id, {"name": "Updated customer"})
    assert lead_sync_worker.process_one()
    with Session() as db:
        c = db.get(Connection, identity)
        jobs = db.scalars(
            select(Delivery)
            .where(Delivery.connection_id == identity, Delivery.kind == "sync")
            .order_by(Delivery.lead_version)
        ).all()
        assert c.status == "needs_attention" and jobs[0].state == "failed"
        assert jobs[1].state == "queued" and jobs[1].attempts == 0
