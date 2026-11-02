from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .exceptions import RulesEngineError
from .stage_result import StageResult


class BusinessRulesEngine:
    def __init__(self, rules_path: Path):
        self.rules_path = rules_path
        self.rules = self._load_rules()

    def evaluate(self, frame: pd.DataFrame) -> StageResult:
        started = time.perf_counter()
        enriched = frame.copy()
        violations: list[dict[str, Any]] = []

        for rule in self.rules:
            mask = self._build_mask(enriched, rule)
            failing = enriched.loc[mask]
            if failing.empty:
                continue

            rule_name = rule["name"]
            rule_column = rule["column"]
            flag_column = f"rule__{rule_name}"
            enriched[flag_column] = False
            enriched.loc[mask, flag_column] = True
            for idx, row in failing.iterrows():
                violations.append(
                    {
                        "row_index": int(idx),
                        "incident_id": str(row.get("incident_id", "")),
                        "severity": rule["severity"],
                        "rule": rule_name,
                        "column": rule_column,
                        "value": self._json_value(row.get(rule_column)),
                        "message": rule["message"],
                    }
                )

        metadata = {
            "rule_count": len(self.rules),
            "violations_total": len(violations),
            "violations_by_severity": self._summarize_severity(violations),
        }
        return StageResult(
            data={"frame": enriched, "violations": violations},
            metadata=metadata,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    def _load_rules(self) -> list[dict[str, Any]]:
        if not self.rules_path.exists():
            raise RulesEngineError(f"Rules file not found: {self.rules_path}")
        payload = yaml.safe_load(self.rules_path.read_text(encoding="utf-8")) or {}
        return payload.get("rules", [])

    def _build_mask(self, frame: pd.DataFrame, rule: dict[str, Any]) -> pd.Series:
        required = [
            c
            for c in [
                rule.get("column"),
                rule.get("trigger_column"),
                rule.get("status_column"),
                rule.get("age_column"),
            ]
            if c
        ] + rule.get("columns", [])

        if any(c not in frame.columns for c in required):
            return pd.Series(False, index=frame.index)

        kind = rule["type"]
        column = rule["column"]
        series = frame[column]

        if kind == "greater_than":
            return series > rule["value"]
        if kind == "less_than":
            return series < rule["value"]
        if kind == "equals":
            return series.astype(str) == str(rule["value"])
        if kind == "not_in":
            allowed = {str(v) for v in rule["values"]}
            return ~series.astype(str).isin(allowed)
        if kind == "is_null":
            return series.isna()
        if kind == "contains":
            return series.astype(str).str.contains(rule["value"], case=False, na=False)
        if kind == "expr":
            local_scope = {col: frame[col] for col in frame.columns}
            return eval(rule["expression"], {"__builtins__": {}}, local_scope)
        raise RulesEngineError(f"Unsupported rule type: {kind}")

    def _summarize_severity(self, violations: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for violation in violations:
            severity = str(violation["severity"])
            counts[severity] = counts.get(severity, 0) + 1
        return counts

    def _json_value(self, value: Any) -> Any:
        if pd.isna(value):
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return value
