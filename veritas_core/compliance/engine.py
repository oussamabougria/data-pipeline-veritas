"""
ISO compliance engine — evaluates C(v) against applicable standards.

Returns a list of ComplianceResult objects.
Called from Airflow async enrichment DAG (never from the Flink critical path).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .standards import get_standard


@dataclass
class ComplianceResult:
    standard: str
    confidence: Decimal
    threshold: Decimal
    passed: bool
    action_type: str | None   # None if passed
    action_payload: dict


class ComplianceEngine:
    def evaluate(
        self,
        confidence: Decimal,
        iso_standards: tuple[str, ...] | list[str],
        sensor_id: str,
        site_id: str,
        scored_obs_id: int | None = None,
    ) -> list[ComplianceResult]:
        """
        Evaluate a confidence score against each applicable ISO standard.

        Returns one ComplianceResult per standard.
        """
        results: list[ComplianceResult] = []
        for std_name in iso_standards:
            std = get_standard(std_name)
            if std is None:
                continue

            passed = confidence >= std.minimum_confidence
            action_type = None if passed else std.action_on_failure
            action_payload: dict = {}

            if not passed:
                action_payload = {
                    "sensor_id": sensor_id,
                    "site_id": site_id,
                    "scored_obs_id": scored_obs_id,
                    "confidence": str(confidence),
                    "threshold": str(std.minimum_confidence),
                    "standard": std_name,
                    "action": action_type,
                }

            results.append(
                ComplianceResult(
                    standard=std_name,
                    confidence=confidence,
                    threshold=std.minimum_confidence,
                    passed=passed,
                    action_type=action_type,
                    action_payload=action_payload,
                )
            )

        return results
