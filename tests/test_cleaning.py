from __future__ import annotations

from pathlib import Path

from veritas_core.cleaning import CleaningEngine
from veritas_core.models import AuditAction, CleaningActionStatus, CleaningActionType
from veritas_operational.app_pipeline import run_pipeline


def test_cleaning_engine_proposes_action_and_audit_events() -> None:
    quality_issue = {
        "issue_id": "issue-1",
        "dataset_id": "dataset-1",
        "target": "row:41",
        "row_index": 41,
        "column_name": None,
        "issue_type": "invalid_severity",
        "message": "Severity outside allowed operational taxonomy.",
        "severity": "major",
        "status": "open",
        "detected_at": "2026-05-01T00:00:00+00:00",
        "evidence": {"column": "severity", "value": "Urgent"},
    }
    advisory = {
        "advisory_id": "advisory-1",
        "issue_id": "issue-1",
        "issue": "Severity outside allowed operational taxonomy.",
        "explanation": "Rule invalid_severity triggered.",
        "recommended_action": "Normalize severity.",
        "confidence": 0.9,
        "source": "rules",
        "status": "pending",
        "created_at": "2026-05-01T00:00:00+00:00",
        "metadata": {},
    }

    payload = CleaningEngine().evaluate([quality_issue], [advisory])

    action = payload["cleaning_actions"][0]
    audit_actions = {event["action"] for event in payload["audit_events"]}

    assert action["action_type"] == CleaningActionType.NORMALIZE_CATEGORY.value
    assert action["status"] == CleaningActionStatus.PROPOSED.value
    assert action["column_name"] == "severity"
    assert action["proposed_value"] == "Critical"
    assert audit_actions == {
        AuditAction.ISSUE_DETECTED.value,
        AuditAction.ADVISORY_CREATED.value,
        AuditAction.CLEANING_PROPOSED.value,
    }


def test_app_pipeline_includes_cleaning_and_audit_outputs() -> None:
    root = Path(__file__).resolve().parents[1] / "veritas_operational"
    payload = run_pipeline(
        root / "app_data" / "industrial_incidents.csv",
        root / "src" / "veritas_operational" / "config" / "default_rules.yaml",
    )

    assert len(payload["cleaning_actions"]) == 5
    assert len(payload["audit_events"]) == 35
    assert payload["pipeline_run"]["metrics"]["cleaning_actions"] == 5
    assert payload["pipeline_run"]["metrics"]["audit_events"] == 35
