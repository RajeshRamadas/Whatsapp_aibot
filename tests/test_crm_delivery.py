"""Native provider contracts: identity precedence, minimal mapping and uncertain write recovery."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import Conversation, Session, engine, now
from app.integrations.lead_sync.crm import CRM
from app.integrations.lead_sync.http import HTTP, SyncError
from app.models.lead_sync import LeadConnection as Connection
from app.models.lead_sync import LeadDelivery as Delivery
from app.services import crypto, lead_sync_worker
from app.services.leads import save_lead
from tests.test_lead_sync import BASE, CONFIG
from tests.test_lead_sync import sync as sync


@pytest.fixture
def crm(sync, client, tenant, monkeypatch):
    connection, conversation = sync
    with Session.begin() as db:
        for job in db.scalars(select(Delivery).where(Delivery.tenant_id == tenant["tenant_id"])):
            job.state = "complete"
    assert client.put(BASE + "/connections/" + connection, headers=tenant["headers"], json=CONFIG).status_code == 200
    state = {
        "contacts": {},
        "notes": [],
        "writes": [],
        "searches": [],
        "uncertain_contact": False,
        "uncertain_note": False,
        "ambiguous": False,
    }

    def request(http, method, path="", *, payload=None, params=None, data=None):
        assert engine.pool.checkedout() == 0
        assert http.root == "https://api.hubapi.com/"
        assert http.headers["Authorization"] == "Bearer private-hubspot-token"
        if path.endswith("/search"):
            filters = [f for group in payload["filterGroups"] for f in group["filters"]]
            state["searches"].extend(f["propertyName"] for f in filters)
            rows = [
                {"id": k, "properties": v}
                for k, v in state["contacts"].items()
                if any(v.get(f["propertyName"]) == f["value"] for f in filters)
            ]
            if state["ambiguous"]:
                rows = [{"id": str(i), "properties": {"phone": "+919999000001"}} for i in (10, 11)]
            return {"results": rows}
        if path.endswith("/associations/notes"):
            return {"results": [{"id": str(100 + i)} for i in range(len(state["notes"]))]}
        if path.endswith("/notes/batch/read"):
            return {"results": [{"properties": {"hs_note_body": n}} for n in state["notes"]]}
        if path == "crm/v3/objects/notes":
            assert payload["associations"][0]["types"][0]["associationTypeId"] == 202
            state["notes"].append(payload["properties"]["hs_note_body"])
            if state["uncertain_note"]:
                state["uncertain_note"] = False
                raise SyncError("provider_unavailable", retry=True, uncertain=True)
            return {"id": str(99 + len(state["notes"]))}
        if path.startswith("crm/v3/objects/contacts"):
            key = path.rsplit("/", 1)[-1] if method == "PATCH" else str(10 + len(state["contacts"]))
            state["contacts"].setdefault(key, {}).update(payload["properties"])
            state["writes"].append((method, key, payload["properties"]))
            if state["uncertain_contact"]:
                state["uncertain_contact"] = False
                raise SyncError("provider_unavailable", retry=True, uncertain=True)
            return {"id": key}
        pytest.fail("Unexpected provider request")

    monkeypatch.setattr(HTTP, "request", request)

    def capture(**fields):
        with Session.begin() as db:
            save_lead(db, db.get(Conversation, conversation), fields)

    def run():
        with Session.begin() as db:
            for job in db.scalars(
                select(Delivery).where(Delivery.tenant_id == tenant["tenant_id"], Delivery.state == "queued")
            ):
                job.next_attempt_at = now() - timedelta(seconds=1)
        assert lead_sync_worker.process_one()

    state.update(capture=capture, run=run, connection=connection, conversation=conversation)
    return state


def test_phone_precedes_email_updates_only_mapped_fields_and_adds_safe_note(crm):
    crm["contacts"].update(
        {"20": {"phone": "+919999000001", "company": "Keep company"}, "21": {"email": "customer@example.com"}}
    )
    crm["capture"](name="<Demo>", phone="+919999000001", email="customer@example.com", budget="Private unmapped")
    crm["run"]()
    assert crm["writes"][0][:2] == ("PATCH", "20")
    assert "email" not in crm["searches"]
    assert crm["contacts"]["20"]["company"] == "Keep company"
    assert "budget" not in crm["writes"][0][2]
    assert "&lt;Demo&gt;" in crm["notes"][0] and "/dashboard/inbox?conversation=" in crm["notes"][0]
    assert "Private unmapped" not in crm["notes"][0]
    crm["capture"](phone="+919999000002")
    crm["run"]()
    assert crm["writes"][1][:2] == ("PATCH", "20") and len(crm["notes"]) == 2


@pytest.mark.parametrize("uncertain", ["uncertain_contact", "uncertain_note"])
def test_uncertain_creation_and_note_do_not_duplicate(crm, uncertain):
    crm[uncertain] = True
    crm["capture"](name="Demo", phone="+919999000001")
    crm["run"]()
    crm["run"]()
    assert len(crm["contacts"]) == 1 and len(crm["notes"]) == 1
    with Session() as db:
        jobs = db.scalars(select(Delivery).where(Delivery.kind == "sync")).all()
        assert jobs[0].state == "complete" and jobs[0].payload == {}


def test_ambiguous_identity_keeps_local_lead_and_fails_without_mutating(crm):
    crm["ambiguous"] = True
    crm["capture"](name="Demo", phone="+919999000001")
    crm["run"]()
    assert not crm["writes"] and not crm["notes"]
    with Session() as db:
        job = db.scalar(select(Delivery).where(Delivery.kind == "sync"))
        assert job.state == "failed" and job.last_error == "ambiguous_identity"


def test_zoho_refresh_uses_fixed_region_and_exact_matches(client, tenant, sync, monkeypatch):
    connection, conversation = sync
    packet = crypto.encrypt(
        tenant["tenant_id"], json.dumps(dict(client_id="client", client_secret="secret", refresh_token="refresh"))
    )
    with Session.begin() as db:
        c = db.get(Connection, connection)
        c.provider, c.region, c.credentials_enc = "zoho", "in", packet
        c.catalog = {"fields": [{"name": n, "required": n == "Last_Name"} for n in ("Last_Name", "Phone", "Email")]}
        for job in db.scalars(select(Delivery).where(Delivery.connection_id == connection)):
            job.state = "complete"
    writes, refreshes = [], []

    def request(http, method, path="", *, payload=None, params=None, data=None):
        assert engine.pool.checkedout() == 0
        if path == "token":
            assert http.root == "https://accounts.zoho.in/oauth/v2/" and data["refresh_token"] == "refresh"
            refreshes.append(True)
            return {"access_token": "zoho-access", "expires_in": 3600, "api_domain": "https://untrusted.invalid"}
        assert http.root == "https://www.zohoapis.in/crm/v8/"
        assert http.headers["Authorization"] == "Zoho-oauthtoken zoho-access"
        if path == "Leads/search":
            if "Email" in params["criteria"]:
                return {"data": [{"id": "41", "Email": "CUSTOMER@example.com"}]}
            # Zoho equals is fuzzy: a similar phone must not match.
            return {"data": [{"id": "40", "Phone": "+919999000003"}]}
        writes.append((method, path, payload))
        return {"data": [{"code": "SUCCESS", "details": {"id": "41"}}]}

    monkeypatch.setattr(HTTP, "request", request)
    config = {**CONFIG, "mapping": {"name": "Last_Name", "phone": "Phone", "email": "Email"}}
    assert client.put(BASE + "/connections/" + connection, headers=tenant["headers"], json=config).status_code == 200
    with Session.begin() as db:
        save_lead(
            db,
            db.get(Conversation, conversation),
            {"name": "Demo", "phone": "+919999000001", "email": "customer@example.com"},
        )
    assert lead_sync_worker.process_one()
    assert len(refreshes) == 1 and writes[0][:2] == ("PUT", "Leads/41")
    assert writes[1][1] == "Leads/41/Notes" and "/dashboard/inbox?" in writes[1][2]["data"][0]["Note_Content"]
    with Session() as db:
        c = db.get(Connection, connection)
        credentials = bytes(c.credentials_enc)
        db.expunge(c)
    assert "zoho-access" in crypto.decrypt(tenant["tenant_id"], credentials).decode()
    # Cached tokens do not consume another refresh-token grant per lead.
    CRM(tenant["tenant_id"], c, json.loads(crypto.decrypt(tenant["tenant_id"], credentials)))
    assert len(refreshes) == 1


def test_discovery_filters_read_only_fields_and_mapping_requires_identity(sync, tenant, client, monkeypatch):
    identity, _ = sync

    def request(http, method, path="", **kwargs):
        assert engine.pool.checkedout() == 0
        return {
            "results": [
                {"name": "firstname", "type": "string"},
                {"name": "phone", "type": "string"},
                {"name": "generated", "type": "string", "modificationMetadata": {"readOnlyValue": True}},
            ]
        }

    monkeypatch.setattr(HTTP, "request", request)
    assert lead_sync_worker.process_one()
    connection = client.get(BASE, headers=tenant["headers"]).json()["connections"][0]
    assert {f["name"] for f in connection["catalog"]["fields"]} == {"firstname", "phone"}
    assert (
        client.put(
            BASE + "/connections/" + identity,
            headers=tenant["headers"],
            json={**CONFIG, "mapping": {"phone": "firstname"}},
        ).status_code
        == 422
    )


def test_uncertain_contact_not_visible_yet_is_never_blindly_recreated(crm):
    crm["uncertain_contact"] = True
    crm["capture"](name="Demo", phone="+919999000001")
    crm["run"]()
    crm["contacts"].clear()  # Simulate the provider search index lagging behind its successful create.
    crm["run"]()
    assert len(crm["writes"]) == 1
    with Session() as db:
        job = db.scalar(select(Delivery).where(Delivery.kind == "sync"))
        assert job.state == "queued" and job.last_error == "creation_unconfirmed"
