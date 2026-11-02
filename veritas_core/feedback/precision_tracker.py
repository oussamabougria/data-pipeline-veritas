"""Precision and calibration metrics for semantic discovery."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import logging
from typing import Any

from veritas_core.discovery.semantic_discovery import ColumnProfile
from veritas_core.feedback.dataset_registry import DatasetRegistry
from veritas_core.feedback.feedback_store import FeedbackStore

_vlog = logging.getLogger(__name__)


@dataclass(slots=True)
class PrecisionReport:
    """Summary of semantic discovery quality against known corrections."""

    total_columns_evaluated: int
    correct_inferences: int
    incorrect_inferences: int
    accuracy: float
    confidence_calibration: float
    most_confused_roles: list[dict[str, Any]]
    worst_columns: list[str]
    recommendation: str


class PrecisionTracker:
    """Evaluate semantic discovery precision using feedback and registry history."""

    _BUCKETS = [
        ("0-0.5", 0.0, 0.5),
        ("0.5-0.7", 0.5, 0.7),
        ("0.7-0.85", 0.7, 0.85),
        ("0.85-1.0", 0.85, 1.0000001),
    ]

    def __init__(self, feedback_store: FeedbackStore, registry: DatasetRegistry):
        """Bind a feedback store and a dataset registry."""
        self.feedback_store = feedback_store
        self.registry = registry

    def evaluate(self, profiles: list[ColumnProfile]) -> PrecisionReport:
        """Measure current semantic discovery precision against known corrections."""
        total = len(profiles)
        confusion_counter: Counter[tuple[str, str]] = Counter()
        incorrect_by_column: Counter[str] = Counter()
        confidence_pairs: list[tuple[float, bool]] = []
        incorrect = 0

        for profile in profiles:
            correction = self.feedback_store.get_corrections(profile.column_name)
            is_correct = correction is None
            confidence_pairs.append((float(profile.confidence), is_correct))
            if is_correct:
                continue
            incorrect += 1
            confusion_counter[(profile.role.value, correction.corrected_role)] += 1
            incorrect_by_column[profile.column_name] += 1

        correct = total - incorrect
        accuracy = 1.0 if total == 0 else correct / total
        above_threshold = [is_correct for confidence, is_correct in confidence_pairs if confidence > 0.8]
        confidence_calibration = (
            1.0 if not above_threshold else sum(1 for value in above_threshold if value) / len(above_threshold)
        )
        most_confused_roles = [
            {"predicted": predicted, "actual": actual, "count": count}
            for (predicted, actual), count in confusion_counter.most_common(5)
        ]
        worst_columns = [column for column, _ in incorrect_by_column.most_common(5)]
        recommendation = self._build_recommendation(accuracy, confidence_calibration, most_confused_roles)
        _vlog.info("Precision evaluated on %d columns with accuracy %.4f", total, accuracy)
        return PrecisionReport(
            total_columns_evaluated=total,
            correct_inferences=correct,
            incorrect_inferences=incorrect,
            accuracy=accuracy,
            confidence_calibration=confidence_calibration,
            most_confused_roles=most_confused_roles,
            worst_columns=worst_columns,
            recommendation=recommendation,
        )

    def calibration_curve(self) -> list[dict[str, Any]]:
        """Return empirical accuracy by confidence bucket across registered datasets."""
        bucket_stats: dict[str, dict[str, float]] = {
            label: {"n_inferences": 0, "n_correct": 0}
            for label, _, _ in self._BUCKETS
        }
        for record in self.registry.list_datasets():
            corrected_columns = set(record.corrections_applied)
            for profile in record.column_profiles:
                confidence = float(profile["confidence"])
                is_correct = profile["column_name"] not in corrected_columns
                for label, lower, upper in self._BUCKETS:
                    if lower <= confidence < upper or (label == "0.85-1.0" and confidence == 1.0):
                        bucket_stats[label]["n_inferences"] += 1
                        bucket_stats[label]["n_correct"] += 1 if is_correct else 0
                        break

        curve: list[dict[str, Any]] = []
        for label, _, _ in self._BUCKETS:
            total = int(bucket_stats[label]["n_inferences"])
            correct = int(bucket_stats[label]["n_correct"])
            curve.append(
                {
                    "confidence_range": label,
                    "n_inferences": total,
                    "actual_accuracy": 1.0 if total == 0 else correct / total,
                }
            )
        return curve

    def _build_recommendation(
        self,
        accuracy: float,
        confidence_calibration: float,
        most_confused_roles: list[dict[str, Any]],
    ) -> str:
        if accuracy >= 0.95 and confidence_calibration >= 0.9:
            return "Inference quality is stable; focus on expanding feedback coverage across new sectors."
        if most_confused_roles:
            top = most_confused_roles[0]
            return (
                "Feedback indicates recurring confusion between "
                f"{top['predicted']} and {top['actual']}; tighten heuristics and add more correction examples."
            )
        if accuracy < 0.85:
            return "Accuracy is below target; prioritize operator review before trusting automatic semantics."
        return "Confidence is ahead of observed accuracy; recalibrate confidence scoring and review high-confidence columns."
