from __future__ import annotations

from veritas_core.models import (
    Advisory,
    AdvisorySource,
    AdvisoryStatus,
    CleaningAction,
    CleaningActionStatus,
    CleaningActionType,
    DataSource,
    IssueSeverity,
    IssueStatus,
    PipelineMode,
    PipelineRun,
    PipelineRunStatus,
    QualityIssue,
    SourceKind,
)


def test_data_source_serializes_enums_as_strings() -> None:
    source = DataSource(
        name="incident upload",
        kind="csv",
        uri="veritas_operational/app_data/industrial_incidents.csv",
    )

    payload = source.to_dict()

    assert payload["kind"] == "csv"
    assert DataSource.from_dict(payload).kind == SourceKind.CSV


def test_quality_issue_round_trip_preserves_status_and_severity() -> None:
    issue = QualityIssue(
        target="row:5.column:downtime_min",
        issue_type="negative_downtime",
        message="Negative downtime is physically impossible.",
        severity=IssueSeverity.CRITICAL,
        status=IssueStatus.QUARANTINED,
        row_index=5,
        column_name="downtime_min",
        evidence={"value": -12},
    )

    restored = QualityIssue.from_dict(issue.to_dict())

    assert restored.severity == IssueSeverity.CRITICAL
    assert restored.status == IssueStatus.QUARANTINED
    assert restored.evidence["value"] == -12


def test_advisory_clamps_confidence_and_serializes_source() -> None:
    advisory = Advisory(
        issue_id="issue-1",
        issue="missing owner team",
        explanation="The incident has no responsible team.",
        recommended_action="Assign an owner team before closure.",
        confidence=1.4,
        source=AdvisorySource.RULES,
    )

    payload = advisory.to_dict()

    assert payload["confidence"] == 1.0
    assert payload["source"] == "rules"
    assert payload["status"] == AdvisoryStatus.PENDING.value


def test_cleaning_action_round_trip_preserves_action_type() -> None:
    action = CleaningAction(
        issue_id="issue-2",
        action_type=CleaningActionType.NORMALIZE_CATEGORY,
        column_name="severity",
        original_value="Urgent",
        proposed_value="Critical",
        rationale="Map non-standard severity to the closest allowed value.",
        status=CleaningActionStatus.PROPOSED,
    )

    restored = CleaningAction.from_dict(action.to_dict())

    assert restored.action_type == CleaningActionType.NORMALIZE_CATEGORY
    assert restored.status == CleaningActionStatus.PROPOSED


def test_pipeline_run_state_transitions() -> None:
    run = PipelineRun(mode=PipelineMode.BATCH, source_id="source-1")

    run.mark_completed({"rows": 180, "violations": 14})

    assert run.status == PipelineRunStatus.COMPLETED
    assert run.finished_at is not None
    assert run.to_dict()["metrics"]["violations"] == 14
