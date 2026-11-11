"""Accuracy runs snapshot inputs, isolate workspaces and checkpoint outside provider calls."""

from datetime import timedelta

import pytest

from app import evaluate, rag
from app.config import settings
from app.db import ApiKey, EvalCase, Session, Tenant, engine, now, uid
from app.models.evaluations import EvalRun
from app.services.auth import hash_token
from app.services.evaluation_runs import process_one

BASE = "/api/v1/evals"
CASE = {"question": "What is the refund period?", "expected_keywords": ["30 days"]}


@pytest.fixture
def accuracy(client, tenant, monkeypatch):
    case = client.post(BASE, headers=tenant["headers"], json={"cases": [CASE]}).json()[0]
    calls = []

    def answer(tenant, conversation_id, question):
        assert engine.pool.checkedout() == 0 and conversation_id is None
        calls.append((tenant.name, tenant.bot_rules, question))
        return rag.Answer("Refunds within 30 days.", [], question)

    monkeypatch.setattr(rag, "answer", answer)
    return case, calls


def test_queued_snapshot_survives_case_edits_and_saves_history(client, tenant, accuracy):
    case, calls = accuracy
    key = uid()
    body = {"label": "Baseline", "request_key": key, "case_ids": [case["id"]]}
    result = client.post(BASE + "/run", headers=tenant["headers"], json=body)
    assert result.status_code == 202 and result.json()["state"] == "queued" and not calls
    identity = result.json()["id"]
    assert client.post(BASE + "/run", headers=tenant["headers"], json=body).json()["id"] == identity
    assert client.post(BASE + "/run", headers=tenant["headers"], json={**body, "label": "Different"}).status_code == 409
    assert client.post(BASE + "/run", headers=tenant["headers"]).status_code == 409
    assert (
        client.put(
            BASE + "/" + case["id"], headers=tenant["headers"], json={**CASE, "expected_keywords": ["60 days"]}
        ).status_code
        == 200
    )
    with Session.begin() as db:
        db.get(Tenant, tenant["tenant_id"]).bot_rules = "Changed after queueing"
    assert process_one() and len(calls) == 1 and calls[0][1] != "Changed after queueing"
    detail = client.get(BASE + "/runs/" + identity, headers=tenant["headers"]).json()
    assert detail["state"] == "complete" and detail["accuracy"] == 1 and detail["completed"] == 1
    assert detail["cases"][0]["expected_keywords"] == ["30 days"]
    assert detail["results"][0]["passed"] and detail["context"]["case_set"]
    assert "bot" not in detail["context"] and "request" not in detail["context"]
    assert client.delete(BASE + "/" + case["id"], headers=tenant["headers"]).status_code == 204
    history = client.get(BASE + "/runs", headers=tenant["headers"]).json()
    assert len(history) == 1 and "results" not in history[0]
    assert client.get(BASE + "/runs/" + identity, headers=tenant["headers"]).json()["cases"]


def test_stale_claim_skips_completed_cases_and_cancel_fences_inflight_results(client, tenant, accuracy, monkeypatch):
    case, calls = accuracy
    client.post(BASE, headers=tenant["headers"], json={"cases": [{**CASE, "question": "Second question"}]})
    identity = client.post(BASE + "/run", headers=tenant["headers"]).json()["id"]
    assert process_one()
    with Session.begin() as db:
        run = db.get(EvalRun, identity)
        run.state, run.claimed_at = "running", now() - timedelta(minutes=16)
    original = rag.answer

    def cancel_during_call(*args, **kwargs):
        result = original(*args, **kwargs)
        assert (
            client.post(
                BASE + "/runs/" + identity + "/cancel", headers=tenant["headers"], json={"confirm": True}
            ).status_code
            == 200
        )
        return result

    monkeypatch.setattr(rag, "answer", cancel_during_call)
    assert process_one() and len(calls) == 2 and not process_one()
    detail = client.get(BASE + "/runs/" + identity, headers=tenant["headers"]).json()
    assert detail["state"] == "cancelled" and detail["completed"] == 1 and len(detail["results"]) == 1


def test_changed_retrieval_stops_run_and_provider_errors_are_safe(client, tenant, accuracy, monkeypatch):
    identity = client.post(BASE + "/run", headers=tenant["headers"]).json()["id"]
    previous = settings.retrieval_top_k
    monkeypatch.setattr(settings, "retrieval_top_k", previous + 1)
    assert process_one() and not accuracy[1]
    detail = client.get(BASE + "/runs/" + identity, headers=tenant["headers"]).json()
    assert detail["state"] == "failed" and detail["last_error"] == "configuration_changed"
    monkeypatch.setattr(settings, "retrieval_top_k", previous)

    def unavailable(*args, **kwargs):
        raise RuntimeError("secret-provider-body")

    monkeypatch.setattr(rag, "answer", unavailable)
    identity = client.post(BASE + "/run", headers=tenant["headers"]).json()["id"]
    assert process_one()
    response = client.get(BASE + "/runs/" + identity, headers=tenant["headers"])
    assert "secret-provider-body" not in response.text
    assert response.json()["errors"] == 1 and response.json()["results"][0]["error"] == "provider_failed"


def test_history_roles_foreign_ids_validation_and_case_edits(client, tenant, accuracy):
    case, _ = accuracy
    identity = client.post(BASE + "/run", headers=tenant["headers"]).json()["id"]
    other = client.post(
        "/api/v1/tenants", headers={"X-Bootstrap-Token": "test-bootstrap"}, json={"name": "Foreign"}
    ).json()
    foreign = {"Authorization": "Bearer " + other["admin_key"]}
    for method, path, body in [
        ("GET", "/runs/" + identity, None),
        ("POST", "/runs/" + identity + "/cancel", {"confirm": True}),
        ("PUT", "/" + case["id"], CASE),
        ("DELETE", "/" + case["id"], None),
        ("POST", "/run", {"case_ids": [case["id"]]}),
    ]:
        assert client.request(method, BASE + path, headers=foreign, json=body).status_code == 404
    assert client.get(BASE + "/runs", headers=foreign).json() == []
    paths = [
        ("GET", "/runs", None),
        ("GET", "/runs/" + identity, None),
        ("GET", "/cases", None),
        ("GET", "", None),
        ("POST", "", {"cases": [CASE]}),
        ("POST", "/run", {}),
        ("POST", "/runs/" + identity + "/cancel", {"confirm": True}),
        ("PUT", "/" + case["id"], CASE),
        ("DELETE", "/" + case["id"], None),
    ]
    token = uid()
    with Session.begin() as db:
        db.add(
            ApiKey(
                tenant_id=tenant["tenant_id"],
                key_hash=hash_token(token),
                key_prefix=token[:8],
                name="Viewer",
                scopes=["viewer"],
            )
        )
    for method, path, body in paths:
        assert client.request(method, BASE + path, json=body).status_code == 401
        response = client.request(method, BASE + path, headers={"Authorization": "Bearer " + token}, json=body)
        assert response.status_code == (200 if method == "GET" else 403)
    assert client.get(BASE + "/runs?limit=101", headers=tenant["headers"]).status_code == 422
    assert client.get(BASE + "/runs/bad-id", headers=tenant["headers"]).status_code == 422
    assert client.post(BASE + "/run", headers=tenant["headers"], json={"case_ids": []}).status_code == 422
    assert (
        client.post(
            BASE + "/runs/" + identity + "/cancel", headers=tenant["headers"], json={"confirm": False}
        ).status_code
        == 422
    )
    assert (
        client.put(BASE + "/" + case["id"], headers=tenant["headers"], json={"question": "No checks"}).status_code
        == 422
    )
    assert (
        client.post(
            BASE, headers=tenant["headers"], json={"cases": [{"question": " ", "expect_no_answer": True}]}
        ).status_code
        == 422
    )


def test_decline_test_cannot_pass_an_uncited_hallucination():
    case = EvalCase(question="Unknown fact?", expect_no_answer=True)
    assert not evaluate.score(case, rag.Answer("An invented fact.", [], "Unknown fact?"))["passed"]
    assert evaluate.score(case, rag.Answer("Please ask our team.", [], "Unknown fact?", uncertain=True))["passed"]


def test_case_cap_and_knowledge_changes_stop_inconsistent_runs(client, tenant, accuracy, monkeypatch):
    from app.db import QAPair

    client.post(BASE, headers=tenant["headers"], json={"cases": [{**CASE, "question": "Second question"}]})
    monkeypatch.setattr(settings, "eval_max_cases_per_run", 1)
    assert client.post(BASE + "/run", headers=tenant["headers"]).status_code == 422
    identity = client.post(BASE + "/run", headers=tenant["headers"], json={"case_ids": [accuracy[0]["id"]]}).json()[
        "id"
    ]
    with Session.begin() as db:
        db.add(
            QAPair(tenant_id=tenant["tenant_id"], question="Refund?", answer="Changed knowledge", embedding=[1, 0, 0])
        )
    assert process_one() and not accuracy[1]
    assert (
        client.get(BASE + "/runs/" + identity, headers=tenant["headers"]).json()["last_error"]
        == "configuration_changed"
    )
    assert (
        client.post(BASE, headers=tenant["headers"], json={"cases": [{**CASE, "expect_no_answer": True}]}).status_code
        == 422
    )
