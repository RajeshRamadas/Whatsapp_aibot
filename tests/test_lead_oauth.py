"""Google lead exports use PKCE/session-bound drive.file consent, never Calendar tokens."""

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.db import Membership, Session, engine, now
from app.models.lead_sync import LeadConnection as Connection
from app.services import crypto, lead_oauth
from tests.test_google_calendar import connected as connected

BASE = "/api/v1/lead-sync"


@pytest.fixture
def google(connected, monkeypatch):
    monkeypatch.setattr(settings, "lead_sync_google_client_id", "lead-client")
    monkeypatch.setattr(settings, "lead_sync_google_client_secret", "lead-secret")
    monkeypatch.setattr(
        settings, "lead_sync_google_redirect_uri", "http://localhost/api/backend/lead-sync/google/callback"
    )
    calls = []

    def request(method, url, **kwargs):
        assert engine.pool.checkedout() == 0
        assert url == "https://oauth2.googleapis.com/token"
        calls.append(kwargs["data"])
        return httpx.Response(
            200,
            json={
                "access_token": "lead-access",
                "refresh_token": "lead-refresh",
                "expires_in": 3600,
                "scope": lead_oauth.SCOPE,
            },
        )

    monkeypatch.setattr(httpx, "request", request)
    return connected[0], calls, connected[2]


def start(client, headers):
    response = client.post(BASE + "/google/connect", headers=headers, json={"label": "Lead exports"})
    assert response.status_code == 200, response.text
    args = parse_qs(urlsplit(response.json()["url"]).query)
    assert args["scope"] == [lead_oauth.SCOPE] and args["code_challenge_method"] == ["S256"]
    return args["state"][0]


def test_google_lead_oauth_tokens_are_encrypted_and_refresh_early(client, tenant, google):
    headers, calls, _ = google
    state = start(client, headers)
    response = client.get(
        BASE + "/google/callback", params={"state": state, "code": "lead-code"}, follow_redirects=False
    )
    assert response.status_code == 303 and response.headers["location"] == "/dashboard/lead-sync"
    assert response.headers["referrer-policy"] == "no-referrer" and not calls
    c = client.get(BASE, headers=headers).json()["connections"][0]
    lead_oauth.exchange(tenant["tenant_id"], c["id"], 1)
    assert calls[0]["code"] == "lead-code"
    assert client.get(BASE + "/google/callback", params={"state": state, "code": "repeat"}).status_code == 404
    with Session() as db:
        value = db.scalar(
            select(Connection).where(Connection.id == c["id"], Connection.tenant_id == tenant["tenant_id"])
        )
        assert value.oauth_secret_enc is None and b"lead-access" not in value.credentials_enc
        packet = value.credentials_enc
    data = json.loads(crypto.decrypt(tenant["tenant_id"], packet))
    data["expires_at"] = (now() + timedelta(minutes=1)).isoformat()
    packet = crypto.encrypt(tenant["tenant_id"], json.dumps(data))
    with Session.begin() as db:
        db.get(Connection, c["id"]).credentials_enc = packet
    assert lead_oauth.access(tenant["tenant_id"], c["id"], 1) == "lead-access"
    assert calls[-1]["grant_type"] == "refresh_token"
    assert "lead-refresh" not in client.get(BASE, headers=headers).text


def test_google_lead_oauth_roles_bad_states_and_denial(client, tenant, google):
    headers, calls, user_id = google
    assert client.post(BASE + "/google/connect", headers=tenant["headers"], json={"label": "Test"}).status_code == 403
    state = start(client, headers)
    with Session.begin() as db:
        m = db.scalar(
            select(Membership).where(Membership.tenant_id == tenant["tenant_id"], Membership.user_id == user_id)
        )
        m.role = "viewer"
    assert client.get(BASE + "/google/callback", params={"state": state, "code": "bad-role"}).status_code == 403
    assert client.post(BASE + "/google/connect", headers=headers, json={"label": "Test"}).status_code == 403
    with Session.begin() as db:
        db.scalar(
            select(Membership).where(Membership.tenant_id == tenant["tenant_id"], Membership.user_id == user_id)
        ).role = "owner"
    assert (
        client.get(
            BASE + "/google/callback", params={"state": state, "error": "access_denied"}, follow_redirects=False
        ).status_code
        == 303
    )
    assert client.get(BASE + "/google/callback", params={"state": "x" * 43, "code": "unknown"}).status_code == 404
    assert client.get(BASE + "/google/callback", params={"state": "short"}).status_code == 422
    assert client.post(BASE + "/google/connect", headers=headers, json={"label": ""}).status_code == 422
    client.cookies.clear()
    assert client.post(BASE + "/google/connect", json={"label": "Test"}).status_code == 401
    assert client.get(BASE + "/google/callback", params={"state": "x" * 43, "code": "bad"}).status_code == 401
    assert not calls
