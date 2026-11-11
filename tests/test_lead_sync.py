"""Explicit sharing, transactional snapshots and workspace isolation."""

import base64

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.db import ApiKey, Conversation, Lead, Session, uid
from app.models.lead_sync import LeadConnection as Connection
from app.models.lead_sync import LeadDelivery as Delivery
from app.services.auth import hash_token
from app.services.leads import save_lead

BASE = "/api/v1/lead-sync"
CREDENTIALS = {"provider": "hubspot", "label": "Sales contacts", "token": "private-hubspot-token"}
CONFIG = {
    "enabled": True,
    "confirm_sharing": True,
    "mapping": {"name": "firstname", "phone": "phone", "email": "email"},
}


@pytest.fixture
def sync(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "master_key", base64.b64encode(b"l" * 32).decode())
    monkeypatch.setattr(httpx, "request", lambda *a, **k: pytest.fail("HTTP must run in the worker"))
    response = client.post(BASE + "/connections", headers=tenant["headers"], json=CREDENTIALS)
    assert response.status_code == 201, response.text
    identity = response.json()["id"]
    with Session.begin() as db:
        c = db.scalar(select(Connection).where(Connection.id == identity, Connection.tenant_id == tenant["tenant_id"]))
        assert b"private-hubspot-token" not in c.credentials_enc and not c.enabled
        c.status = "connected"
        c.catalog = {
            "fields": [{"name": name, "label": name, "required": False} for name in ("firstname", "phone", "email")]
        }
        conversation_id = uid()
        db.add(Conversation(id=conversation_id, tenant_id=tenant["tenant_id"], channel="web", external_user=uid()))
    return identity, conversation_id


def test_versioned_outbox_keeps_leads_and_snapshots_until_explicitly_enabled(client, tenant, sync):
    identity, conversation_id = sync
    headers = tenant["headers"]
    assert "private-hubspot-token" not in client.get(BASE, headers=headers).text
    with Session.begin() as db:
        conversation = db.get(Conversation, conversation_id)
        save_lead(db, conversation, {"name": "Demo customer", "phone": "+919999000001", "email": "demo@example.test"})
    assert client.get(BASE + "/connections/" + identity + "/jobs", headers=headers).json()[0]["kind"] == "discover"
    assert client.put(BASE + "/connections/" + identity, headers=headers, json=CONFIG).status_code == 200
    body = {"confirm": True, "lead_ids": [conversation_id]}
    for _ in range(2):
        assert (
            client.post(BASE + "/connections/" + identity + "/backfill", headers=headers, json=body).status_code == 200
        )
    with Session.begin() as db:
        lead = db.get(Lead, conversation_id)
        assert lead.sync_version == 1
        conversation = db.get(Conversation, conversation_id)
        assert not save_lead(db, conversation, {"name": "Demo customer"})
        assert lead.sync_version == 1
        save_lead(db, conversation, {"name": "Updated customer"})
    assert (
        client.patch("/api/v1/leads/" + conversation_id, headers=headers, json={"status": "qualified"}).status_code
        == 200
    )
    with Session() as db:
        jobs = db.scalars(
            select(Delivery)
            .where(Delivery.tenant_id == tenant["tenant_id"], Delivery.kind == "sync")
            .order_by(Delivery.lead_version)
        ).all()
        assert [j.lead_version for j in jobs] == [1, 2, 3]
        assert jobs[0].payload["name"] == "Demo customer" and jobs[1].payload["name"] == "Updated customer"
        assert jobs[2].payload["status"] == "qualified" and "notes" not in jobs[2].payload
    assert (
        client.put(BASE + "/connections/" + identity, headers=headers, json={**CONFIG, "enabled": False}).status_code
        == 200
    )
    with Session.begin() as db:
        save_lead(db, db.get(Conversation, conversation_id), {"location": "Pune"})
    with Session() as db:
        assert (
            len(
                db.scalars(
                    select(Delivery).where(Delivery.tenant_id == tenant["tenant_id"], Delivery.kind == "sync")
                ).all()
            )
            == 3
        )
        assert db.get(Lead, conversation_id).fields["location"] == "Pune"


def test_connection_endpoints_validate_roles_and_foreign_records(client, tenant, sync):
    identity, conversation_id = sync
    headers = tenant["headers"]
    assert client.post(BASE + "/connections/" + identity + "/discover", headers=headers, json={}).status_code == 200
    assert (
        client.post(
            BASE + "/connections/" + identity + "/spreadsheet", headers=headers, json={"title": "Leads"}
        ).status_code
        == 422
    )
    assert (
        client.put(
            BASE + "/connections/" + identity, headers=headers, json={**CONFIG, "confirm_sharing": False}
        ).status_code
        == 422
    )
    assert (
        client.post(
            BASE + "/connections", headers=headers, json={"provider": "hubspot", "label": "Missing"}
        ).status_code
        == 422
    )
    with Session.begin() as db:
        job = db.scalar(select(Delivery).where(Delivery.tenant_id == tenant["tenant_id"]))
        job.state, job_id = "failed", job.id
    retry_path = "/connections/" + identity + "/jobs/" + job_id + "/retry"
    assert client.post(BASE + retry_path, headers=headers, json={"confirm": True}).status_code == 200
    other = client.post(
        "/api/v1/tenants", headers={"X-Bootstrap-Token": "test-bootstrap"}, json={"name": "Other workspace"}
    ).json()
    foreign = {"Authorization": "Bearer " + other["admin_key"]}
    assert client.get(BASE, headers=foreign).json()["connections"] == []
    routes = [
        ("GET", "/connections/" + identity + "/jobs", None),
        ("PUT", "/connections/" + identity, CONFIG),
        ("POST", "/connections/" + identity + "/discover", {}),
        ("POST", "/connections/" + identity + "/spreadsheet", {"title": "Leads"}),
        ("POST", "/connections/" + identity + "/backfill", {"confirm": True, "lead_ids": [conversation_id]}),
        ("POST", retry_path, {"confirm": True}),
        ("DELETE", "/connections/" + identity, {"confirm": True}),
    ]
    for method, path, body in routes:
        assert client.request(method, BASE + path, headers=foreign, json=body).status_code == 404
    key = uid()
    with Session.begin() as db:
        db.add(
            ApiKey(
                tenant_id=tenant["tenant_id"],
                key_hash=hash_token(key),
                key_prefix=key[:8],
                name="Agent",
                scopes=["agent"],
            )
        )
    for method, path, body in routes + [("GET", "", None), ("POST", "/connections", CREDENTIALS)]:
        assert client.request(method, BASE + path, json=body).status_code == 401
        assert (
            client.request(method, BASE + path, headers={"Authorization": "Bearer " + key}, json=body).status_code
            == 403
        )
    assert (
        client.request("DELETE", BASE + "/connections/" + identity, headers=headers, json={"confirm": True}).json()[
            "status"
        ]
        == "disconnected"
    )
