from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
import uuid


def new_id() -> str:
    return str(uuid.uuid4())


class SourceType(str, Enum):
    STREAM = "stream"
    BATCH = "batch"
    POLL = "poll"


class DomainType(str, Enum):
    SENSOR = "sensor"
    OPERATIONAL = "operational"
    UNKNOWN = "unknown"


class Verdict(str, Enum):
    TRUSTED = "trusted"
    REVIEW = "review"
    QUARANTINE = "quarantine"


class Severity(str, Enum):
    CRITICAL = "Critical"
    MAJOR = "Major"
    MINOR = "Minor"


class AdvisoryStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    APPLIED = "applied"


class AdvisorySource(str, Enum):
    RULES = "rules"
    PHYSICS = "physics"
    LLM = "llm"
    HUMAN = "human"
    SYSTEM = "system"


class ActorType(str, Enum):
    AUTO = "auto"
    HUMAN = "human"


class SourceKind(str, Enum):
    CSV = "csv"
    KAFKA = "kafka"
    API = "api"
    DATABASE = "database"
    SENSOR = "sensor"
    UNKNOWN = "unknown"


class PipelineMode(str, Enum):
    BATCH = "batch"
    STREAM = "stream"


class IssueSeverity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"


class IssueStatus(str, Enum):
    OPEN = "open"
    QUARANTINED = "quarantined"
    RESOLVED = "resolved"
    ACCEPTED_RISK = "accepted_risk"


class CleaningActionType(str, Enum):
    NORMALIZE_CATEGORY = "normalize_category"
    IMPUTE_MISSING = "impute_missing"
    DROP_DUPLICATE = "drop_duplicate"
    COERCE_TYPE = "coerce_type"
    QUARANTINE = "quarantine"
    NOOP = "noop"


class CleaningActionStatus(str, Enum):
    PROPOSED = "proposed"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"


class AuditAction(str, Enum):
    INGESTED = "ingested"
    PROFILED = "profiled"
    ISSUE_DETECTED = "issue_detected"
    ADVISORY_CREATED = "advisory_created"
    CLEANING_PROPOSED = "cleaning_proposed"
    CLEANING_APPLIED = "cleaning_applied"
    QUARANTINED = "quarantined"
    REVIEWED = "reviewed"


class PipelineRunStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class DataSource:
    """
    Carries both routing dimensions.
    Created by the connector before any pipeline stage runs.
    Passed through the entire pipeline unchanged.
    """

    id: str = field(default_factory=new_id)
    name: str = ""
    source_type: SourceType = SourceType.BATCH
    domain_type: DomainType = DomainType.UNKNOWN
    connector: str = "csv"
    config: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: str = "connected"
    kind: SourceKind | str | None = None
    uri: str = ""
    source_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind is not None:
            self.connector = SourceKind(self.kind).value
            self.source_type = SourceType.STREAM if self.connector == SourceKind.KAFKA.value else SourceType.BATCH
        if self.source_id:
            self.id = self.source_id
        else:
            self.source_id = self.id
        if self.uri and not self.config:
            self.config = {"uri": self.uri}
        if self.metadata and not self.config:
            self.config = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.connector,
            "uri": self.uri,
            "source_id": self.id,
            "created_at": self.created_at.isoformat() if hasattr(self.created_at, "isoformat") else self.created_at,
            "metadata": self.metadata or self.config,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DataSource":
        return cls(**payload)


@dataclass
class Dataset:
    id: str = field(default_factory=new_id)
    source_id: str = ""
    name: str = ""
    row_count: int = 0
    column_count: int = 0
    ingested_at: datetime = field(default_factory=datetime.utcnow)
    pipeline_run_id: str = ""
    domain_label: str = ""


@dataclass
class Stream:
    source_id: str = ""
    topic: str = ""
    stream_id: str = field(default_factory=new_id)
    consumer_group: str | None = None
    schema_hint: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ColumnProfile:
    """
    One per column per pipeline run.
    Produced by the profiler stage.
    """

    column: str = ""
    archetype: str = ""
    role: str = ""
    dtype: str = ""
    null_ratio: float = 0.0
    unique_ratio: float = 0.0
    sample_values: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    tau: float = 0.0
    delta: float = 0.0
    alpha: float = 0.0
    confidence: float = 0.0
    verdict: Verdict = Verdict.TRUSTED
    constraint_src: str = ""


@dataclass
class QualityIssue:
    """
    One per detected problem - per column, per row, or per dataset.
    Produced by the validation stage.
    """

    id: str = field(default_factory=new_id)
    dataset_id: str = ""
    column: str = ""
    row_index: int = -1
    issue_type: str = ""
    severity: Severity = Severity.MINOR
    detected_by: str = ""
    value: Any = None
    expected: Any = None
    confidence: float = 1.0
    rule: str = ""
    message: str = ""
    target: str = ""
    issue_id: str = ""
    column_name: str | None = None
    status: IssueStatus | str = IssueStatus.OPEN
    detected_at: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.issue_id:
            self.id = self.issue_id
        else:
            self.issue_id = self.id
        if self.column_name is not None and not self.column:
            self.column = self.column_name
        if self.column and self.column_name is None:
            self.column_name = self.column
        if self.rule and not self.issue_type:
            self.issue_type = self.rule
        if self.issue_type and not self.rule:
            self.rule = self.issue_type
        if not self.detected_at:
            self.detected_at = datetime.utcnow().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.id,
            "dataset_id": self.dataset_id,
            "target": self.target,
            "row_index": self.row_index,
            "column_name": self.column_name,
            "issue_type": self.issue_type,
            "message": self.message,
            "severity": self.severity.value if hasattr(self.severity, "value") else self.severity,
            "status": self.status.value if hasattr(self.status, "value") else self.status,
            "detected_at": self.detected_at,
            "evidence": self.evidence or {"value": self.value, "expected": self.expected},
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "QualityIssue":
        return cls(**payload)


@dataclass
class Advisory:
    """
    One per QualityIssue - the AI recommendation.
    Produced by the advisory stage.
    Human interacts with this to accept/reject.
    """

    id: str = field(default_factory=new_id)
    issue_id: str = ""
    dataset_id: str = ""
    column: str = ""
    issue: str = ""
    explanation: str = ""
    recommended_action: str = ""
    confidence: float = 0.0
    source: AdvisorySource = AdvisorySource.LLM
    status: AdvisoryStatus = AdvisoryStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None
    resolved_by: str = ""
    advisory_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.advisory_id:
            self.id = self.advisory_id
        else:
            self.advisory_id = self.id
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        self.source = AdvisorySource(self.source)
        self.status = AdvisoryStatus(self.status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "advisory_id": self.id,
            "issue_id": self.issue_id,
            "dataset_id": self.dataset_id,
            "column": self.column,
            "issue": self.issue,
            "explanation": self.explanation,
            "recommended_action": self.recommended_action,
            "confidence": self.confidence,
            "source": self.source.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if hasattr(self.created_at, "isoformat") else self.created_at,
            "resolved_at": self.resolved_at.isoformat() if hasattr(self.resolved_at, "isoformat") else self.resolved_at,
            "resolved_by": self.resolved_by,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Advisory":
        return cls(**payload)


@dataclass
class CleaningAction:
    """
    One per applied fix - records exactly what changed.
    Original value always preserved.
    """

    id: str = field(default_factory=new_id)
    issue_id: str = ""
    advisory_id: str = ""
    dataset_id: str = ""
    column: str = ""
    row_index: int = -1
    original_value: Any = None
    new_value: Any = None
    method: str = ""
    applied_by: ActorType = ActorType.AUTO
    applied_at: datetime = field(default_factory=datetime.utcnow)
    reason: str = ""
    action_id: str = ""
    action_type: CleaningActionType | str | None = None
    column_name: str = ""
    proposed_value: Any = None
    rationale: str = ""
    status: CleaningActionStatus | str = CleaningActionStatus.PROPOSED
    created_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action_id:
            self.id = self.action_id
        else:
            self.action_id = self.id
        if self.column_name and not self.column:
            self.column = self.column_name
        if self.column and not self.column_name:
            self.column_name = self.column
        if self.proposed_value is not None and self.new_value is None:
            self.new_value = self.proposed_value
        if self.reason and not self.rationale:
            self.rationale = self.reason
        if self.rationale and not self.reason:
            self.reason = self.rationale
        if self.action_type is not None:
            self.action_type = CleaningActionType(self.action_type)
            self.method = self.action_type.value
        self.status = CleaningActionStatus(self.status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.id,
            "issue_id": self.issue_id,
            "advisory_id": self.advisory_id,
            "dataset_id": self.dataset_id,
            "action_type": self.action_type.value if hasattr(self.action_type, "value") else self.method,
            "column_name": self.column_name,
            "row_index": self.row_index,
            "original_value": self.original_value,
            "proposed_value": self.new_value,
            "rationale": self.rationale or self.reason,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if hasattr(self.created_at, "isoformat") else self.created_at,
            "applied_at": self.applied_at.isoformat() if hasattr(self.applied_at, "isoformat") else self.applied_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CleaningAction":
        return cls(**payload)


@dataclass
class AuditEvent:
    """
    Immutable log entry. Written once, never modified.
    Every issue, advisory, and cleaning action produces one.
    """

    id: str = field(default_factory=new_id)
    pipeline_run_id: str = ""
    dataset_id: str = ""
    event_type: str = ""
    entity_id: str = ""
    column: str = ""
    original: Any = None
    updated: Any = None
    reason: str = ""
    actor: str = ""
    actor_type: ActorType = ActorType.AUTO
    timestamp: datetime = field(default_factory=datetime.utcnow)
    audit_id: str = ""
    action: AuditAction | str | None = None
    entity_type: str = ""
    occurred_at: str = ""
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.audit_id:
            self.id = self.audit_id
        else:
            self.audit_id = self.id
        if self.action is not None:
            self.action = AuditAction(self.action)
            self.event_type = self.action.value
        if not self.occurred_at:
            self.occurred_at = self.timestamp.isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.id,
            "action": self.event_type,
            "actor": self.actor,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "occurred_at": self.occurred_at,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "metadata": self.metadata,
        }


@dataclass
class PipelineRun:
    """
    One per pipeline execution - batch or stream micro-batch.
    The top-level object that all other objects reference.
    """

    id: str = field(default_factory=new_id)
    source_id: str = ""
    dataset_id: str = ""
    branch: str = ""
    source_type: SourceType = SourceType.BATCH
    domain_type: DomainType = DomainType.UNKNOWN
    domain_label: str = ""
    domain_interpretation: str = ""
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    status: str = "running"
    stages_completed: list = field(default_factory=list)
    profiles: list[ColumnProfile] = field(default_factory=list)
    issues: list[QualityIssue] = field(default_factory=list)
    advisories: list[Advisory] = field(default_factory=list)
    cleaning_actions: list[CleaningAction] = field(default_factory=list)
    audit_events: list[AuditEvent] = field(default_factory=list)
    rule_count: int = 0
    violations_total: int = 0
    llm_calls: int = 0
    fallback_used: bool = False
    elapsed_ms: float = 0.0
    mode: PipelineMode | str | None = None
    run_id: str = ""
    finished_at: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        if self.run_id:
            self.id = self.run_id
        else:
            self.run_id = self.id
        if self.mode is not None:
            self.mode = PipelineMode(self.mode)
            self.source_type = SourceType.STREAM if self.mode == PipelineMode.STREAM else SourceType.BATCH
            self.branch = "a" if self.source_type == SourceType.STREAM else "b"

    def mark_completed(self, metrics: dict[str, Any] | None = None) -> None:
        self.status = "completed"
        self.completed_at = datetime.utcnow()
        self.finished_at = self.completed_at.isoformat()
        if metrics:
            self.metrics.update(metrics)

    def mark_failed(self, error: str) -> None:
        self.status = "failed"
        self.completed_at = datetime.utcnow()
        self.finished_at = self.completed_at.isoformat()
        self.error = error

    def to_dict(self) -> dict:
        """
        Serialize for results.json and /api/live.
        Called at end of every pipeline run.
        """
        return _jsonify(asdict(self))


def _jsonify(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonify(item) for item in value]
    return value
