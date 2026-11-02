"""Persistent storage for operator corrections to semantic discovery."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
from datetime import datetime
from typing import Any

from veritas_core.discovery.semantic_discovery import Archetype, ColumnProfile, SemanticRole

_vlog = logging.getLogger(__name__)


@dataclass(slots=True)
class CorrectionRecord:
    """Operator-provided correction for one inferred column profile."""

    column_name: str
    original_role: str
    corrected_role: str
    original_archetype: str
    corrected_archetype: str
    corrected_by: str
    corrected_at: str
    dataset_source: str
    pattern_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the record to a JSON-friendly dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CorrectionRecord":
        """Build a correction record from persisted JSON data."""
        return cls(**payload)


class FeedbackStore:
    """JSON-backed store of semantic inference corrections."""

    def __init__(self, store_path: str = "veritas_feedback.json"):
        """Load the correction store from disk if it already exists."""
        self.store_path = Path(store_path)
        self._lock = threading.RLock()
        self._corrections: dict[str, CorrectionRecord] = {}
        self._advisory_outcomes: list[dict[str, Any]] = []
        self._load()

    def add_correction(self, record: CorrectionRecord) -> None:
        """Insert or update a correction and persist it immediately."""
        with self._lock:
            self._corrections[record.column_name] = record
            self._persist()
        _vlog.info("Stored correction for column %s", record.column_name)

    def get_corrections(self, column_name: str) -> CorrectionRecord | None:
        """Return a persisted correction for a given column name."""
        with self._lock:
            return self._corrections.get(column_name)

    def apply_corrections(self, profiles: list[ColumnProfile]) -> list[ColumnProfile]:
        """Override inferred profiles with persisted human corrections."""
        corrected_profiles: list[ColumnProfile] = []
        for profile in profiles:
            correction = self.get_corrections(profile.column_name)
            if correction is None:
                corrected_profiles.append(profile)
                continue
            corrected_profiles.append(
                ColumnProfile(
                    column_name=profile.column_name,
                    role=SemanticRole(correction.corrected_role),
                    archetype=Archetype(correction.corrected_archetype),
                    confidence=1.0,
                    anomalies=list(profile.anomalies),
                    notes=f"{profile.notes} | Correction applied from feedback store",
                )
            )
        return corrected_profiles

    def export_summary(self) -> dict[str, Any]:
        """Return aggregate stats about the correction history."""
        with self._lock:
            role_counter = Counter(
                f"{record.original_role}->{record.corrected_role}"
                for record in self._corrections.values()
            )
            column_counter = Counter(record.column_name for record in self._corrections.values())
        return {
            "total_corrections": len(self._corrections),
            "most_corrected_roles": [
                {"transition": transition, "count": count}
                for transition, count in role_counter.most_common(5)
            ],
            "most_corrected_columns": [
                {"column_name": column_name, "count": count}
                for column_name, count in column_counter.most_common(5)
            ],
        }

    def record_advisory_outcome(
        self,
        advisory_id: str,
        column: str,
        rule: str,
        status: str,
        constraint: dict,
        domain_label: str,
        reason: str = "",
        constraint_store: "ConstraintStore | None" = None,
    ) -> None:
        """
        Called when a human accepts or rejects an advisory.
        Accepted advisories write learnable signals to ConstraintStore.
        Rejected advisories weaken matching stored constraints.
        """
        entry = {
            "advisory_id": advisory_id,
            "column": column,
            "column_name": column,
            "rule": rule,
            "status": status,
            "constraint": constraint,
            "domain_label": domain_label,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
        }
        with self._lock:
            self._advisory_outcomes.append(entry)
            self._persist()

        if constraint_store is None:
            return

        col_lower = column.lower().replace(" ", "_")
        col_parts = col_lower.split("_")
        column_pattern = "|".join(f"{part}*" for part in col_parts if len(part) > 2)
        domain_pattern = domain_label.lower().split()[0] + "*" if domain_label else ""

        if status == "accepted":
            constraint_store.record(
                column_pattern=column_pattern,
                domain_pattern=domain_pattern,
                constraint=constraint,
                source="human",
                reason=reason or f"Human accepted advisory for {column}",
                confidence=1.0,
            )
        elif status == "rejected":
            matches = constraint_store.lookup(column, domain_label)
            for match in matches:
                if match.constraint.get("allow_negative") == constraint.get("allow_negative"):
                    constraint_store.reject(match.id)

    def get_advisory_outcomes(self) -> list[dict]:
        """Returns all recorded advisory outcomes."""
        with self._lock:
            return list(self._advisory_outcomes)

    def _load(self) -> None:
        with self._lock:
            if not self.store_path.exists():
                self._corrections = {}
                return
            try:
                payload = json.loads(self.store_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise RuntimeError(f"Failed to load feedback store from {self.store_path}: {exc}") from exc
            if not isinstance(payload, list):
                raise RuntimeError(f"Feedback store {self.store_path} must contain a JSON list")
            self._corrections = {}
            self._advisory_outcomes = []
            for item in payload:
                if "advisory_id" in item:
                    self._advisory_outcomes.append(item)
                    continue
                if "column_name" in item:
                    self._corrections[item["column_name"]] = CorrectionRecord.from_dict(item)

    def _persist(self) -> None:
        data = [record.to_dict() for record in self._corrections.values()]
        data.extend(self._advisory_outcomes)
        self._atomic_write(data)

    def _atomic_write(self, payload: list[dict[str, Any]]) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=self.store_path.stem + "_",
            suffix=".tmp",
            dir=str(self.store_path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.store_path)
        except Exception as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise RuntimeError(f"Failed to persist feedback store to {self.store_path}: {exc}") from exc
