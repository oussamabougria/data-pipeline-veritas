from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class ConstraintPattern:
    """
    One learnable constraint - derived from human feedback.
    Matched against future columns using fuzzy pattern logic.
    """

    id: str = ""
    column_pattern: str = ""
    domain_pattern: str = ""
    constraint: dict = field(default_factory=dict)
    source: str = "human"
    confidence: float = 1.0
    applied_count: int = 0
    confirmed_count: int = 0
    rejected_count: int = 0
    created_at: str = ""
    last_used_at: str = ""
    reason: str = ""

    def matches_column(self, column_name: str) -> bool:
        name = column_name.lower().replace(" ", "_")
        pattern = self.column_pattern.lower()
        if "*" in pattern or "|" in pattern:
            parts = pattern.replace("*", "").split("|")
            return any(p.strip() in name for p in parts if p.strip())
        return pattern in name

    def matches_domain(self, domain_label: str) -> bool:
        if not self.domain_pattern:
            return True
        label = domain_label.lower()
        pattern = self.domain_pattern.lower()
        if "*" in pattern or "|" in pattern:
            parts = pattern.replace("*", "").split("|")
            return any(p.strip() in label for p in parts if p.strip())
        return pattern in label

    def effective_confidence(self) -> float:
        total = self.confirmed_count + self.rejected_count
        if total == 0:
            return self.confidence
        return self.confirmed_count / total


class ConstraintStore:
    """
    Persistent store of learned constraint patterns.
    Survives across runs, across both branches.
    """

    def __init__(self, store_path: str = "veritas_constraint_store.json"):
        self.path = Path(store_path)
        self.patterns: list[ConstraintPattern] = []
        self._memory_only = store_path == ":memory:"
        self._load()

    def _load(self) -> None:
        if self._memory_only:
            self.patterns = []
            return
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.patterns = [ConstraintPattern(**p) for p in data]
            except Exception:
                self.patterns = []

    def _save(self) -> None:
        if self._memory_only:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([asdict(p) for p in self.patterns], indent=2),
            encoding="utf-8",
        )

    def lookup(
        self,
        column_name: str,
        domain_label: str = "",
    ) -> list[ConstraintPattern]:
        matches = [
            p
            for p in self.patterns
            if p.matches_column(column_name)
            and p.matches_domain(domain_label)
            and p.effective_confidence() >= 0.2
        ]
        for match in matches:
            match.applied_count += 1
            match.last_used_at = datetime.utcnow().isoformat()
        if matches:
            self._save()
        return sorted(matches, key=lambda x: x.effective_confidence(), reverse=True)

    def record(
        self,
        column_pattern: str,
        domain_pattern: str,
        constraint: dict,
        source: str = "human",
        reason: str = "",
        confidence: float = 1.0,
    ) -> ConstraintPattern:
        confidence = max(0.0, min(1.0, confidence))
        for pattern in self.patterns:
            if (
                pattern.column_pattern == column_pattern
                and pattern.domain_pattern == domain_pattern
                and pattern.constraint.get("allow_negative") == constraint.get("allow_negative")
                and pattern.constraint.get("min_valid") == constraint.get("min_valid")
            ):
                pattern.confirmed_count += 1
                pattern.confidence = min(1.0, pattern.confidence + 0.05)
                self._save()
                return pattern

        import uuid

        pattern = ConstraintPattern(
            id=str(uuid.uuid4()),
            column_pattern=column_pattern,
            domain_pattern=domain_pattern,
            constraint=constraint,
            source=source,
            confidence=confidence,
            confirmed_count=1,
            created_at=datetime.utcnow().isoformat(),
            last_used_at=datetime.utcnow().isoformat(),
            reason=reason,
        )
        self.patterns.append(pattern)
        self._save()
        return pattern

    def reject(self, pattern_id: str) -> None:
        for pattern in list(self.patterns):
            if pattern.id == pattern_id:
                pattern.rejected_count += 1
                pattern.confidence = max(0.0, pattern.confidence - 0.15)
                if pattern.effective_confidence() < 0.2:
                    self.patterns.remove(pattern)
                self._save()
                return

    def to_prompt_context(
        self,
        columns: list[str],
        domain_label: str = "",
    ) -> str:
        lines = []
        for column in columns:
            matches = self.lookup(column, domain_label)
            for match in matches:
                constraint = match.constraint
                parts = []
                if constraint.get("allow_negative") is False:
                    parts.append("must be >= 0")
                if constraint.get("min_valid") is not None:
                    parts.append(f"min = {constraint['min_valid']}")
                if constraint.get("max_valid") is not None:
                    parts.append(f"max = {constraint['max_valid']}")
                if constraint.get("allow_null") is False:
                    parts.append("nulls not allowed")
                if parts:
                    lines.append(
                        f"  {column}: {', '.join(parts)}"
                        f" [operator-confirmed, confidence={match.effective_confidence():.2f}]"
                    )
        if not lines:
            return ""
        return (
            "Operator-confirmed constraints from prior runs:\n"
            + "\n".join(lines)
            + "\nApply these constraints directly. Do not override them."
        )

    def list_all(self) -> list[dict]:
        return [asdict(pattern) for pattern in self.patterns]
