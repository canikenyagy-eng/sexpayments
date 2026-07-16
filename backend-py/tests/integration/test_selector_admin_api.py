"""HTTP admin API for the selector — end-to-end through the FastAPI app.

Auth is bypassed by overriding ``require_admin`` since we're not testing
auth here; we test the selector endpoints' shape and wiring.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.selector import (
    MemoryStorage,
    SelectorRegistry,
    bootstrap,
    load_from_dict,
)
from app.modules.users.permissions import require_admin


@pytest.fixture
def admin_client():
    """Client that bypasses admin auth — we test the selector layer only."""
    app.dependency_overrides[require_admin] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.pop(require_admin, None)


@pytest.fixture(autouse=True)
def _selector_registry():
    bootstrap.reset_for_tests()
    cfg = load_from_dict({
        "selectors": {
            "traders": {
                "namespace": "sel:test:traders",
                "metrics": [
                    {
                        "name": "conversion",
                        "weight": 1.0,
                        "min_value": 0.0,
                        "max_value": 1.0,
                    }
                ],
            }
        }
    })
    registry = SelectorRegistry(
        cfg, storage_overrides={"traders": MemoryStorage()}
    )
    bootstrap.set_registry(registry)
    yield registry
    bootstrap.reset_for_tests()


def test_list_selectors_returns_configured_names(admin_client):
    r = admin_client.get("/api/v1/selectors")
    assert r.status_code == 200
    assert r.json() == ["traders"]


def test_503_when_registry_unset(admin_client):
    bootstrap.reset_for_tests()
    r = admin_client.get("/api/v1/selectors")
    assert r.status_code == 503


def test_404_for_unknown_selector(admin_client):
    r = admin_client.get("/api/v1/selectors/nonexistent/stats")
    assert r.status_code == 404


def test_selector_stats(admin_client):
    r = admin_client.get("/api/v1/selectors/traders/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "traders"
    assert body["namespace"] == "sel:test:traders"
    assert body["bandit_strategy"] == "thompson"
    assert body["entity_count"] == 0


def test_upsert_metrics_then_explain_round_trip(admin_client):
    r = admin_client.put(
        "/api/v1/selectors/traders/entities/e1/metrics",
        json={
            "metrics": [
                {"name": "conversion", "value": 0.8, "sample_size": 100}
            ]
        },
    )
    assert r.status_code == 204

    r = admin_client.get("/api/v1/selectors/traders/entities/e1/explain")
    assert r.status_code == 200
    body = r.json()
    assert body["entity_id"] == "e1"
    assert body["quality_score"] == pytest.approx(0.8)
    assert body["public_score"] == 80
    assert body["enabled"] is True
    by_name = {b["name"]: b for b in body["metric_breakdown"]}
    assert "conversion" in by_name


def test_explain_missing_entity_returns_nulls(admin_client):
    r = admin_client.get("/api/v1/selectors/traders/entities/ghost/explain")
    assert r.status_code == 200
    body = r.json()
    assert body["entity_id"] == "ghost"
    assert body["alpha"] is None
    assert body["quality_score"] is None


def test_admin_select_returns_breakdown(admin_client):
    admin_client.put(
        "/api/v1/selectors/traders/entities/a/metrics",
        json={"metrics": [{"name": "conversion", "value": 0.9}]},
    )
    admin_client.put(
        "/api/v1/selectors/traders/entities/b/metrics",
        json={"metrics": [{"name": "conversion", "value": 0.1}]},
    )
    r = admin_client.post(
        "/api/v1/selectors/traders/select",
        json={
            "candidate_ids": ["a", "b"],
            "order_id": "test-order",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["entity_id"] in ("a", "b")
    assert body["reason"] == "selected"
    assert len(body["candidates"]) == 2


def test_admin_feedback_applies_reward(admin_client):
    admin_client.put(
        "/api/v1/selectors/traders/entities/e/metrics",
        json={"metrics": [{"name": "conversion", "value": 0.5}]},
    )
    r = admin_client.post(
        "/api/v1/selectors/traders/feedback",
        json={"order_id": "o-1", "entity_id": "e", "reward": 1.0},
    )
    assert r.status_code == 204

    explain = admin_client.get(
        "/api/v1/selectors/traders/entities/e/explain"
    ).json()
    assert explain["alpha"] > 1.0


def test_patch_state_disables_then_enables(admin_client):
    admin_client.put(
        "/api/v1/selectors/traders/entities/e/metrics",
        json={"metrics": [{"name": "conversion", "value": 0.5}]},
    )
    r = admin_client.patch(
        "/api/v1/selectors/traders/entities/e/state",
        json={"enabled": False},
    )
    assert r.status_code == 204
    body = admin_client.get(
        "/api/v1/selectors/traders/entities/e/explain"
    ).json()
    assert body["enabled"] is False

    r = admin_client.patch(
        "/api/v1/selectors/traders/entities/e/state",
        json={"enabled": True},
    )
    assert r.status_code == 204
    body = admin_client.get(
        "/api/v1/selectors/traders/entities/e/explain"
    ).json()
    assert body["enabled"] is True


def test_reload_endpoint_no_path_is_noop(admin_client):
    r = admin_client.post("/api/v1/selectors/reload")
    # No path stored → noop, 204 (silently OK)
    assert r.status_code == 204


def test_404_on_unknown_selector_in_each_path(admin_client):
    paths = [
        ("POST", "/api/v1/selectors/missing/select", {"candidate_ids": []}),
        (
            "POST",
            "/api/v1/selectors/missing/feedback",
            {"order_id": "o", "entity_id": "e"},
        ),
        (
            "PUT",
            "/api/v1/selectors/missing/entities/e/metrics",
            {"metrics": []},
        ),
        (
            "PATCH",
            "/api/v1/selectors/missing/entities/e/state",
            {"enabled": True},
        ),
        (
            "GET",
            "/api/v1/selectors/missing/entities/e/explain",
            None,
        ),
    ]
    for method, path, body in paths:
        r = admin_client.request(method, path, json=body)
        assert r.status_code == 404, (method, path)
