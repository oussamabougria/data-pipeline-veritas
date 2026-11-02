"""Structured advisory generation for Veritas quality issues."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import (
    Advisory,
    AdvisorySource,
    IssueSeverity,
    IssueStatus,
    QualityIssue,
)


_SEVERITY_MAP = {
    "critical": IssueSeverity.CRITICAL,
    "major": IssueSeverity.MAJOR,
    "minor": IssueSeverity.MINOR,
    "info": IssueSeverity.INFO,
}


_RULE_ACTIONS = {
    "overdue_open_critical": "Escalate the open critical incident and require an owner review before release.",
    "long_response_major": "Review response SLA handling and verify first-response timestamps.",
    "negative_downtime": "Quarantine the row and correct downtime from source event timestamps.",
    "excessive_scrap": "Investigate the process batch and verify scrap measurement units.",
    "invalid_severity": "Normalize severity to the approved operational taxonomy.",
    "unresolved_with_closed_at_missing": "Recover or enter the missing close timestamp before treating the incident as resolved.",
    "high_defect_ppm": "Trigger quality review and verify defect-rate calculation inputs.",
    "missing_owner_team": "Assign the responsible owner team before downstream reporting.",
}


@dataclass(slots=True)
class AdvisoryEngine:
    """Build structured quality issues and advisories from pipeline findings."""

    confidence_review_threshold: float = 0.85

    def evaluate(
        self,
        violations: list[dict[str, Any]],
        confidence: list[dict[str, Any]],
        dataset_id: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        issues: list[QualityIssue] = []
        advisories: list[Advisory] = []

        for violation in violations:
            issue = self._issue_from_violation(violation, dataset_id)
            issues.append(issue)
            advisories.append(self._advisory_for_violation(issue, violation))

        for score in confidence:
            if str(score.get("verdict", "")).lower() == "trusted":
                continue
            issue = self._issue_from_confidence(score, dataset_id)
            issues.append(issue)
            advisories.append(self._advisory_for_confidence(issue, score))

        return {
            "quality_issues": [issue.to_dict() for issue in issues],
            "advisories": [advisory.to_dict() for advisory in advisories],
        }

    def _issue_from_violation(
        self,
        violation: dict[str, Any],
        dataset_id: str | None,
    ) -> QualityIssue:
        rule_name = str(violation.get("rule", "rule_violation"))
        raw_row_index = _int_or_none(violation.get("row_index"))
        row_index = raw_row_index if raw_row_index is not None and raw_row_index >= 0 else None
        incident_id = str(violation.get("incident_id") or "")
        target = f"row:{row_index}" if row_index is not None else "dataset"
        if incident_id:
            target = f"{target}.incident:{incident_id}"
        return QualityIssue(
            dataset_id=dataset_id,
            target=target,
            row_index=row_index,
            issue_type=rule_name,
            message=str(violation.get("message") or rule_name),
            severity=_severity_from_value(violation.get("severity")),
            status=IssueStatus.OPEN,
            evidence=dict(violation),
        )

    def _issue_from_confidence(
        self,
        score: dict[str, Any],
        dataset_id: str | None,
    ) -> QualityIssue:
        column = str(score.get("column") or "unknown")
        verdict = str(score.get("verdict") or "review").lower()
        severity = IssueSeverity.CRITICAL if verdict == "quarantine" else IssueSeverity.MAJOR
        status = IssueStatus.QUARANTINED if verdict == "quarantine" else IssueStatus.OPEN
        return QualityIssue(
            dataset_id=dataset_id,
            target=f"column:{column}",
            column_name=column,
            issue_type=f"confidence_{verdict}",
            message=f"Column {column} has confidence verdict {verdict}.",
            severity=severity,
            status=status,
            evidence=dict(score),
        )

    def _advisory_for_violation(
        self,
        issue: QualityIssue,
        violation: dict[str, Any],
    ) -> Advisory:
        rule_name = str(violation.get("rule", "rule_violation"))
        violation_message = str(violation.get("message") or issue.message)
        return Advisory(
            issue_id=issue.issue_id,
            issue=issue.message,
            explanation=violation_message,
            recommended_action=_RULE_ACTIONS.get(
                rule_name,
                violation_message,
            ),
            confidence=_advisory_confidence(issue.severity),
            source=AdvisorySource.RULES,
            metadata={"rule": rule_name, "violation": dict(violation)},
        )

    def _advisory_for_confidence(
        self,
        issue: QualityIssue,
        score: dict[str, Any],
    ) -> Advisory:
        column = str(score.get("column") or "unknown")
        verdict = str(score.get("verdict") or "review").lower()
        confidence = float(score.get("confidence") or 0.0)
        return Advisory(
            issue_id=issue.issue_id,
            issue=issue.message,
            explanation=(
                f"Column '{column}' scored C(v)={confidence:.4f}; "
                f"the current verdict is {verdict}."
            ),
            recommended_action=(
                "Quarantine this column for operator review before downstream use."
                if verdict == "quarantine"
                else "Review the column profile and related rule hits before trusting this column."
            ),
            confidence=max(self.confidence_review_threshold, 1.0 - confidence),
            source=AdvisorySource.SYSTEM,
            metadata={"score": dict(score)},
        )


def _severity_from_value(value: Any) -> IssueSeverity:
    key = str(value or "info").strip().lower()
    return _SEVERITY_MAP.get(key, IssueSeverity.INFO)


def _advisory_confidence(severity: IssueSeverity) -> float:
    if severity == IssueSeverity.CRITICAL:
        return 0.96
    if severity == IssueSeverity.MAJOR:
        return 0.9
    if severity == IssueSeverity.MINOR:
        return 0.78
    return 0.65


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
