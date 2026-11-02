"""Cleaning proposal generation for Veritas quality issues."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import (
    Advisory,
    AuditAction,
    AuditEvent,
    CleaningAction,
    CleaningActionType,
    QualityIssue,
)


_RULE_ACTION_TYPES = {
    "negative_downtime": CleaningActionType.QUARANTINE,
    "invalid_severity": CleaningActionType.NORMALIZE_CATEGORY,
    "missing_owner_team": CleaningActionType.IMPUTE_MISSING,
    "unresolved_with_closed_at_missing": CleaningActionType.IMPUTE_MISSING,
    "confidence_quarantine": CleaningActionType.QUARANTINE,
}


_SEVERITY_NORMALIZATION = {
    "urgent": "Critical",
    "blocker": "Critical",
    "severe": "High",
}


@dataclass(slots=True)
class CleaningEngine:
    """Propose cleaning actions and audit events without mutating source data."""

    actor: str = "veritas:cleaning_engine"

    def evaluate(
        self,
        quality_issues: list[dict[str, Any]],
        advisories: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        issues = [QualityIssue.from_dict(item) for item in quality_issues]
        advisory_records = [Advisory.from_dict(item) for item in advisories]
        advisories_by_issue = {item.issue_id: item for item in advisory_records}

        cleaning_actions: list[CleaningAction] = []
        audit_events: list[AuditEvent] = []

        for issue in issues:
            audit_events.append(self._issue_audit(issue))
            advisory = advisories_by_issue.get(issue.issue_id)
            if advisory is not None:
                audit_events.append(self._advisory_audit(advisory))

            action = self._cleaning_action(issue)
            if action is None:
                continue
            cleaning_actions.append(action)
            audit_events.append(self._cleaning_audit(action))

        return {
            "cleaning_actions": [action.to_dict() for action in cleaning_actions],
            "audit_events": [event.to_dict() for event in audit_events],
        }

    def _cleaning_action(self, issue: QualityIssue) -> CleaningAction | None:
        action_type = _RULE_ACTION_TYPES.get(issue.issue_type)
        if action_type is None:
            return None

        evidence = issue.evidence
        column = issue.column_name or _column_for_issue(issue.issue_type)
        original_value = evidence.get("value")
        proposed_value: Any = None
        rationale = "Operator review required before applying this proposal."

        if issue.issue_type == "invalid_severity":
            original_value = evidence.get("value")
            proposed_value = _SEVERITY_NORMALIZATION.get(str(original_value).lower(), "Critical")
            rationale = "Map non-standard severity to the closest approved operational taxonomy value."
        elif issue.issue_type == "missing_owner_team":
            proposed_value = "__REQUIRES_OWNER_ASSIGNMENT__"
            rationale = "Missing owner team cannot be inferred safely; require owner assignment."
        elif issue.issue_type == "unresolved_with_closed_at_missing":
            proposed_value = "__RECOVER_CLOSE_TIMESTAMP__"
            rationale = "Resolved incident is missing close timestamp; recover from source workflow events."
        elif issue.issue_type == "negative_downtime":
            proposed_value = "__RECOMPUTE_FROM_TIMESTAMPS__"
            rationale = "Negative duration is impossible; recompute from opened/closed event timestamps."
        elif issue.issue_type == "confidence_quarantine":
            proposed_value = "__QUARANTINE_COLUMN__"
            rationale = "Column-level confidence is below trust threshold; quarantine before downstream use."

        return CleaningAction(
            issue_id=issue.issue_id,
            action_type=action_type,
            column_name=column,
            row_index=issue.row_index,
            original_value=original_value,
            proposed_value=proposed_value,
            rationale=rationale,
            metadata={"issue_type": issue.issue_type, "target": issue.target},
        )

    def _issue_audit(self, issue: QualityIssue) -> AuditEvent:
        return AuditEvent(
            action=AuditAction.ISSUE_DETECTED,
            actor=self.actor,
            entity_type="quality_issue",
            entity_id=issue.issue_id,
            after_state=issue.to_dict(),
            metadata={"issue_type": issue.issue_type, "target": issue.target},
        )

    def _advisory_audit(self, advisory: Advisory) -> AuditEvent:
        return AuditEvent(
            action=AuditAction.ADVISORY_CREATED,
            actor=self.actor,
            entity_type="advisory",
            entity_id=advisory.advisory_id,
            after_state=advisory.to_dict(),
            metadata={"issue_id": advisory.issue_id, "source": advisory.source.value},
        )

    def _cleaning_audit(self, action: CleaningAction) -> AuditEvent:
        return AuditEvent(
            action=AuditAction.CLEANING_PROPOSED,
            actor=self.actor,
            entity_type="cleaning_action",
            entity_id=action.action_id,
            after_state=action.to_dict(),
            metadata={"issue_id": action.issue_id, "action_type": action.action_type.value},
        )


def _column_for_issue(issue_type: str) -> str:
    if issue_type == "negative_downtime":
        return "downtime_min"
    if issue_type == "invalid_severity":
        return "severity"
    if issue_type == "missing_owner_team":
        return "owner_team"
    if issue_type == "unresolved_with_closed_at_missing":
        return "closed_at"
    return "__dataset__"
