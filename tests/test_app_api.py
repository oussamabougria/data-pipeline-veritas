from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
OP_ROOT = ROOT / "veritas_operational"
if str(OP_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(OP_ROOT / "src"))

from veritas_operational.app_pipeline import persist_payload, run_pipeline  # noqa: E402

APP_SPEC = importlib.util.spec_from_file_location("veritas_operational_app_entry", OP_ROOT / "app.py")
assert APP_SPEC is not None
assert APP_SPEC.loader is not None
operational_app = importlib.util.module_from_spec(APP_SPEC)
APP_SPEC.loader.exec_module(operational_app)


def _refresh_results() -> None:
    payload = run_pipeline(
        OP_ROOT / "app_data" / "industrial_incidents.csv",
        OP_ROOT / "src" / "veritas_operational" / "config" / "default_rules.yaml",
    )
    persist_payload(OP_ROOT / "app_output", payload)


def test_operational_api_exposes_quality_lifecycle_endpoints() -> None:
    _refresh_results()
    client = TestClient(operational_app.app)

    endpoints = {
        "/api/quality-issues": 15,
        "/api/advisories": 15,
        "/api/cleaning-actions": 5,
        "/api/audit-events": 35,
    }

    for endpoint, expected_count in endpoints.items():
        response = client.get(endpoint)
        assert response.status_code == 200
        payload = response.json()
        assert isinstance(payload, list)
        assert len(payload) == expected_count


def test_live_endpoint_translates_batch_results_for_dashboard() -> None:
    _refresh_results()
    client = TestClient(operational_app.app)

    response = client.get("/api/live")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"summary", "runtime", "confidence", "advisories"}
    assert payload["summary"]["total_scored"] == 180
    assert payload["summary"]["violations_total"] == 14
    assert payload["summary"]["quality_issues"] == 15
    assert payload["summary"]["cleaning_actions"] == 5
    assert payload["runtime"]["scored"] == 180
    assert "violations=14" in payload["runtime"]["latest_log"]
    assert len(payload["confidence"]) == 20
    assert payload["confidence"][0]["sensor_id"] == "closed_at"
    assert payload["confidence"][0]["unit"] == "C(v)"
    assert payload["confidence"][0]["is_anomaly"] is True
    assert len(payload["advisories"]) == 12
    assert payload["advisories"][0]["message"]
