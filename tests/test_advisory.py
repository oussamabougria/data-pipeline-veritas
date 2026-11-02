from __future__ import annotations

from pathlib import Path

from veritas_core.advisory import AdvisoryEngine
from veritas_core.models import AdvisorySource, IssueSeverity, IssueStatus
from veritas_operational.app_pipeline import run_pipeline


def test_advisory_engine_builds_rule_advisory() -> None:
    payload = AdvisoryEngine().evaluate(
        violations=[
            {
                "row_index": 5,
                "incident_id": "INC-1",
                "severity": "Critical",
                "rule": "negative_downtime",
                "message": "Negative downtime is physically impossible.",
            }
        ],
        confidence=[],
        dataset_id="dataset-1",
    )

    issue = payload["quality_issues"][0]
    advisory = payload["advisories"][0]

    assert issue["dataset_id"] == "dataset-1"
    assert issue["severity"] == IssueSeverity.CRITICAL.value
    assert issue["status"] == IssueStatus.OPEN.value
    assert issue["row_index"] == 5
    assert advisory["source"] == AdvisorySource.RULES.value
    assert advisory["issue_id"] == issue["issue_id"]
    assert "correct downtime" in advisory["recommended_action"].lower()


def test_advisory_engine_builds_confidence_advisory() -> None:
    payload = AdvisoryEngine().evaluate(
        violations=[],
        confidence=[
            {
                "column": "closed_at",
                "archetype": "TEMPORAL",
                "confidence": 0.1076,
                "verdict": "quarantine",
                "related_rule_hits": 1,
            }
        ],
    )

    issue = payload["quality_issues"][0]
    advisory = payload["advisories"][0]

    assert issue["issue_type"] == "confidence_quarantine"
    assert issue["status"] == IssueStatus.QUARANTINED.value
    assert issue["column_name"] == "closed_at"
    assert advisory["source"] == AdvisorySource.SYSTEM.value
    assert "C(v)=0.1076" in advisory["explanation"]


def test_app_pipeline_includes_structured_advisories() -> None:
    root = Path(__file__).resolve().parents[1] / "veritas_operational"
    payload = run_pipeline(
        root / "app_data" / "industrial_incidents.csv",
        root / "src" / "veritas_operational" / "config" / "default_rules.yaml",
    )

    assert len(payload["quality_issues"]) == 15
    assert len(payload["advisories"]) == 15
    assert payload["advisories"][0]["issue"]
    assert payload["advisories"][0]["recommended_action"]
    assert {item["source"] for item in payload["advisories"]} == {"rules", "system"}
