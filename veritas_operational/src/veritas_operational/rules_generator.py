from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from veritas_core.feedback.dataset_registry import DatasetRegistry
from veritas_core.feedback.constraint_store import ConstraintStore
from veritas_core.feedback.feedback_store import FeedbackStore

from .stage_result import StageResult

import anthropic
from dotenv import load_dotenv


def _find_dotenv() -> Path:
    """Walk up from current file until .env is found."""
    p = Path(__file__).resolve()
    for parent in p.parents:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate
    return Path(".env")


load_dotenv(_find_dotenv())

_vlog = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 8000
_LLM_TIMEOUT_SECONDS = 45.0
_MAX_PROFILED_COLUMNS = 100
_MAX_ROW_VIOLATIONS_PER_RULE = 100
_OUTCOME_FILENAME = "veritas_advisory_feedback.json"

_SYSTEM_PROMPT = """
You are a senior data quality engineer analyzing an operational dataset.
You receive a compressed profile of all columns.
Your job is to produce an analysis plan: decide which columns need
constraint reasoning, and for those columns derive the constraints
directly from the data profile - no generic statistics, no fixed thresholds.
Respond with valid JSON only. No explanation, no markdown.
""".strip()


@dataclass(slots=True)
class _ColumnPlan:
    skip: bool
    skip_reason: str | None
    min_valid: float | None
    max_valid: float | None
    allow_negative: bool
    allow_null: bool
    max_null_ratio: float | None
    expected_range_note: str
    severity_if_violated: str


class ContextAwareRulesEngine:
    """Generate violations from a single dataset-level LLM analysis plan."""

    def __init__(
        self,
        domain_label: str = "Generic operational data",
        output_dir: str = "",
        api_key: str | None = None,
        feedback_store: FeedbackStore | None = None,
        dataset_registry: DatasetRegistry | None = None,
    ) -> None:
        self.domain_label = domain_label
        self.output_dir = output_dir
        self._api_key = api_key
        default_output_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parents[2] / "app_output"
        self.feedback_store = feedback_store or FeedbackStore(str(default_output_dir / "veritas_feedback.json"))
        self.dataset_registry = dataset_registry or DatasetRegistry(str(default_output_dir / "veritas_registry.json"))

    def evaluate(
        self,
        frame: pd.DataFrame,
        profile_dicts: list[dict[str, Any]],
        discovery_profiles: list[Any] | None = None,
    ) -> StageResult:
        started = time.perf_counter()
        feedback_context = self._load_feedback_context()
        profile_pack = self._build_dataset_profile(
            frame,
            profile_dicts,
            discovery_profiles,
            feedback_context,
        )
        fallback_used = False

        try:
            plan_payload = self._call_meta_llm(frame, profile_pack, feedback_context)
        except Exception as exc:
            _vlog.warning("ContextAwareRulesEngine meta LLM failed: %s", type(exc).__name__)
            plan_payload = self._fallback_plan(list(frame.columns))
            fallback_used = True
        plan_payload = self._merge_known_constraints(plan_payload, profile_pack)

        violations, rule_count, counts = self._apply_plan(
            frame=frame,
            profile_pack=profile_pack,
            plan_payload=plan_payload,
            fallback_used=fallback_used,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000

        metadata = {
            "rule_count": rule_count,
            "violations_total": len(violations),
            "violations_by_severity": self._summarize_severity(violations),
            "llm_calls": 1,
            "columns_analyzed": counts["columns_analyzed"],
            "columns_skipped": counts["columns_skipped"],
            "columns_uncapped": counts["columns_uncapped"],
            "domain_interpretation": str(plan_payload.get("domain_interpretation") or ""),
            "fallback_used": fallback_used,
            "elapsed_ms": round(elapsed_ms, 2),
        }
        return StageResult(
            data={"frame": frame.copy(), "violations": violations},
            metadata=metadata,
            elapsed_ms=elapsed_ms,
        )

    def _build_dataset_profile(
        self,
        frame: pd.DataFrame,
        profile_dicts: list[dict[str, Any]],
        discovery_profiles: list[Any] | None,
        feedback_context: dict[str, Any],
    ) -> dict[str, Any]:
        profiles = [
            self._compressed_column_profile(
                frame,
                column,
                profile_dicts,
                discovery_profiles,
                feedback_context,
            )
            for column in frame.columns
        ]
        if len(profiles) <= _MAX_PROFILED_COLUMNS:
            profiled = profiles
            uncapped = []
        else:
            sorted_profiles = sorted(profiles, key=self._profile_null_ratio)
            profiled = sorted_profiles[:_MAX_PROFILED_COLUMNS]
            profiled_names = {self._profile_name(item) for item in profiled}
            uncapped = [item for item in profiles if self._profile_name(item) not in profiled_names]

        return {
            "row_count": int(len(frame)),
            "total_col_count": int(len(frame.columns)),
            "profiled_col_count": int(len(profiled)),
            "profiled": profiled,
            "uncapped": uncapped,
            "all_profiles_by_name": {self._profile_name(item): item for item in profiles},
        }

    def _compressed_column_profile(
        self,
        frame: pd.DataFrame,
        column: str,
        profile_dicts: list[dict[str, Any]],
        discovery_profiles: list[Any] | None,
        feedback_context: dict[str, Any],
    ) -> dict[str, Any]:
        series = frame[column]
        profile = self._profile_for(column, profile_dicts)
        semantic = self._semantic_for(column, discovery_profiles)
        numeric = pd.to_numeric(series, errors="coerce").dropna()
        correction = feedback_context["corrections_by_column"].get(column)
        acceptance = feedback_context["acceptance_by_column"].get(column, {})

        payload = {
            "n": column,
            "role": str(semantic.get("role") or profile.get("archetype") or "UNKNOWN"),
            "null": round(float(series.isna().mean()), 2),
            "min": self._round_or_none(numeric.min(), digits=2) if not numeric.empty else None,
            "max": self._round_or_none(numeric.max(), digits=2) if not numeric.empty else None,
            "mean": self._round_or_none(numeric.mean(), digits=2) if not numeric.empty else None,
            "sample": [self._json_value(value) for value in series.dropna().head(2).tolist()],
        }
        if correction:
            payload["feedback"] = (
                "operator corrected role from "
                f"{correction['original_role']} to {correction['corrected_role']}"
            )
            payload["role"] = str(correction["corrected_role"])
        if acceptance.get("accepted_ratio") is not None:
            accepted_ratio = float(acceptance["accepted_ratio"])
            if accepted_ratio < 0.20:
                payload["prior_low_acceptance"] = True
            elif accepted_ratio > 0.80:
                payload["prior_high_acceptance"] = True
        return payload

    def _call_meta_llm(
        self,
        frame: pd.DataFrame,
        profile_pack: dict[str, Any],
        feedback_context: dict[str, Any],
    ) -> dict[str, Any]:
        client = self._build_client()
        user_prompt = self._build_meta_prompt(frame, profile_pack, feedback_context)
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            timeout=_LLM_TIMEOUT_SECONDS,
        )
        text = str(response.content[0].text or "").strip()
        payload = json.loads(self._extract_json_object(text))
        if not isinstance(payload, dict):
            raise ValueError("LLM plan response must be a JSON object")
        payload = self._normalize_plan_payload(payload)
        if not isinstance(payload.get("columns"), dict):
            raise ValueError("LLM plan missing columns object")
        return payload

    def _extract_json_object(self, text: str) -> str:
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        if text.startswith("{") and text.endswith("}"):
            return text
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            preview = text[:200].replace("\n", " ")
            raise ValueError(f"LLM response did not contain a JSON object: {preview}")
        return text[start : end + 1]

    def _normalize_plan_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        columns = payload.get("columns")
        if isinstance(columns, list):
            converted: dict[str, Any] = {}
            for item in columns:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("n") or item.get("column") or item.get("column_name")
                if name is not None:
                    converted[str(name)] = item
            payload["columns"] = converted
        return payload

    def _build_client(self) -> Any:
        if not self._api_key and not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        return anthropic.Anthropic(api_key=self._api_key or None)

    def _build_meta_prompt(
        self,
        frame: pd.DataFrame,
        profile_pack: dict[str, Any],
        feedback_context: dict[str, Any],
    ) -> str:
        compressed_profiles_json = json.dumps(
            profile_pack["profiled"],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        feedback_summary = self._build_feedback_summary(feedback_context)
        known_constraints = self._constraint_store().to_prompt_context(
            columns=[self._profile_name(profile) for profile in profile_pack["profiled"]],
            domain_label=self.domain_label,
        )
        known_constraints_block = ""
        if known_constraints:
            known_constraints_block = f"""
{known_constraints}

For columns listed above with operator-confirmed constraints:
  Use those constraints directly in your response.
  Do not override them with statistical inference.
"""
        return f"""
Dataset domain: {self.domain_label}
Total rows: {len(frame)}
Total columns: {profile_pack["total_col_count"]}
Columns profiled below: {profile_pack["profiled_col_count"]}

Column profiles:
{compressed_profiles_json}

Profile field names: "n" is the column name, "null" is the null ratio.

Operator feedback on prior runs:
{feedback_summary}

For columns marked prior_low_acceptance=true:
  Be more conservative - widen allowed ranges or set skip=true.
For columns marked prior_high_acceptance=true:
  Maintain similar constraints to what was accepted before.
For columns with operator role corrections:
  Trust the corrected role over the inferred one.

{known_constraints_block}

Return a JSON object with this exact structure:
{{
  "domain_interpretation": "one sentence describing what this dataset is",
  "columns": {{
    "<column_name>": {{
      "skip": true | false,
      "skip_reason": "why skipped, or null if not skipped",
      "min_valid": number or null,
      "max_valid": number or null,
      "allow_negative": boolean,
      "allow_null": boolean,
      "max_null_ratio": float or null,
      "expected_range_note": "one sentence, specific to this column",
      "severity_if_violated": "Critical" | "Major" | "Minor"
    }}
  }}
}}

Rules for filling this:
- Set skip=true for: columns with null > 0.85, zero-variance columns,
  columns where you have no basis to derive meaningful constraints.
- For non-skipped columns: derive min_valid and max_valid from domain knowledge
  about what this column physically or operationally represents.
  Do not use IQR or percentile formulas. Reason from meaning.
- If a column name is a pure number with no context, set skip=true
  unless the sample values give you enough to reason about.
- Be conservative: if unsure, prefer skip=true over wrong constraints.
""".strip()

    def _load_feedback_context(self) -> dict[str, Any]:
        corrections_by_column = self._load_corrections_by_column()
        outcomes = self._load_advisory_outcomes()
        similar_columns = self._columns_from_similar_datasets()
        acceptance_by_column = self._acceptance_by_column(outcomes, similar_columns)
        return {
            "corrections_by_column": corrections_by_column,
            "acceptance_by_column": acceptance_by_column,
            "similar_columns": similar_columns,
        }

    def _load_corrections_by_column(self) -> dict[str, dict[str, Any]]:
        corrections: dict[str, dict[str, Any]] = {}
        source = getattr(self.feedback_store, "_corrections", None)
        if isinstance(source, dict):
            records = source.values()
        else:
            records = []
        for record in records:
            payload = record.to_dict() if hasattr(record, "to_dict") else dict(record)
            column = str(payload.get("column_name") or "")
            if column:
                corrections[column] = payload
        return corrections

    def _columns_from_similar_datasets(self) -> set[str]:
        columns: set[str] = set()
        try:
            datasets = self.dataset_registry.list_datasets()
        except Exception:
            return columns
        domain_token = self.domain_label.strip().lower()
        for record in datasets:
            payload = record.to_dict() if hasattr(record, "to_dict") else dict(record)
            sector = str(payload.get("sector") or "").lower()
            source_path = str(payload.get("source_path") or "").lower()
            is_similar = (
                not domain_token
                or domain_token == "generic operational data"
                or domain_token in sector
                or any(token and token in source_path for token in re.split(r"\W+", domain_token))
            )
            if not is_similar:
                continue
            for profile in payload.get("column_profiles") or []:
                column = profile.get("column_name") or profile.get("name")
                if column:
                    columns.add(str(column))
        return columns

    def _load_advisory_outcomes(self) -> list[dict[str, Any]]:
        path = self._outcome_store_path()
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return payload if isinstance(payload, list) else []

    def _acceptance_by_column(
        self,
        outcomes: list[dict[str, Any]],
        similar_columns: set[str],
    ) -> dict[str, dict[str, float]]:
        counts: dict[str, dict[str, int]] = {}
        for outcome in outcomes:
            column = str(outcome.get("column_name") or "")
            if not column:
                continue
            if similar_columns and column not in similar_columns:
                continue
            stats = counts.setdefault(column, {"accepted": 0, "total": 0})
            status = str(outcome.get("status") or "").lower()
            stats["total"] += 1
            if status in {"accepted", "applied"}:
                stats["accepted"] += 1
        return {
            column: {
                "accepted_ratio": values["accepted"] / max(values["total"], 1),
                "total": float(values["total"]),
            }
            for column, values in counts.items()
        }

    def _build_feedback_summary(self, feedback_context: dict[str, Any]) -> str:
        lines: list[str] = []
        for column, correction in sorted(feedback_context["corrections_by_column"].items()):
            lines.append(
                f"- {column}: role {correction['original_role']} -> {correction['corrected_role']}; "
                f"archetype {correction['original_archetype']} -> {correction['corrected_archetype']}"
            )
        for column, acceptance in sorted(feedback_context["acceptance_by_column"].items()):
            ratio = float(acceptance["accepted_ratio"])
            if ratio < 0.20:
                lines.append(f"- {column}: prior_low_acceptance=true ({ratio:.0%} accepted)")
            elif ratio > 0.80:
                lines.append(f"- {column}: prior_high_acceptance=true ({ratio:.0%} accepted)")
        return "\n".join(lines) if lines else "No operator feedback recorded yet."

    def record_advisory_outcome(
        self,
        column_name: str,
        rule_name: str,
        status: str,
        feedback_store: FeedbackStore,
    ) -> None:
        normalized = status.strip().lower()
        if normalized not in {"accepted", "rejected", "applied"}:
            raise ValueError("status must be one of: accepted, rejected, applied")
        path = self._outcome_store_path(feedback_store)
        existing: list[dict[str, Any]]
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                existing = loaded if isinstance(loaded, list) else []
            except Exception:
                existing = []
        else:
            existing = []
        existing.append(
            {
                "column_name": column_name,
                "rule_name": rule_name,
                "status": normalized,
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, indent=2, ensure_ascii=True), encoding="utf-8")

    def _outcome_store_path(self, feedback_store: FeedbackStore | None = None) -> Path:
        store = feedback_store or self.feedback_store
        store_path = Path(getattr(store, "store_path", "veritas_feedback.json"))
        return store_path.parent / _OUTCOME_FILENAME

    def _fallback_plan(self, columns: list[str]) -> dict[str, Any]:
        return {
            "domain_interpretation": (
                f"High-dimensional sensor dataset with {len(columns)} anonymous numeric columns. "
                "Constraint reasoning skipped — no semantic column identity available. "
                "Null-ratio validation applied to all columns."
            ),
            "columns": {},
        }

    def _constraint_store(self) -> ConstraintStore:
        output_dir = Path(self.output_dir) if self.output_dir else Path(__file__).resolve().parents[2] / "app_output"
        return ConstraintStore(str(output_dir / "veritas_constraint_store.json"))

    def _merge_known_constraints(
        self,
        plan_payload: dict[str, Any],
        profile_pack: dict[str, Any],
    ) -> dict[str, Any]:
        columns = plan_payload.setdefault("columns", {})
        store = self._constraint_store()
        for profile in profile_pack["profiled"]:
            column = self._profile_name(profile)
            matches = store.lookup(column, self.domain_label)
            if not matches:
                continue
            merged = dict(columns.get(column) or {})
            constraint = dict(matches[0].constraint)
            merged.update(
                {
                    "skip": False,
                    "skip_reason": None,
                    "min_valid": constraint.get("min_valid"),
                    "max_valid": constraint.get("max_valid"),
                    "allow_negative": constraint.get("allow_negative", True),
                    "allow_null": constraint.get("allow_null", True),
                    "max_null_ratio": constraint.get("max_null_ratio"),
                    "expected_range_note": constraint.get(
                        "expected_range_note",
                        "Operator-confirmed constraint from prior feedback.",
                    ),
                    "severity_if_violated": constraint.get("severity_if_violated", "Critical"),
                }
            )
            columns[column] = merged
        return plan_payload

    def _apply_plan(
        self,
        frame: pd.DataFrame,
        profile_pack: dict[str, Any],
        plan_payload: dict[str, Any],
        fallback_used: bool,
    ) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
        plan_columns = plan_payload.get("columns") or {}
        if fallback_used and not plan_columns:
            violations, rule_count = self._fallback_violations(frame, profile_pack)
            return violations, rule_count, {
                "columns_analyzed": 0,
                "columns_skipped": int(len(frame.columns)),
                "columns_uncapped": 0,
            }

        all_profiles = profile_pack["all_profiles_by_name"]
        uncapped_names = {self._profile_name(item) for item in profile_pack["uncapped"]}
        profiled_names = {self._profile_name(item) for item in profile_pack["profiled"]}

        violations: list[dict[str, Any]] = []
        rule_count = 0
        columns_analyzed = 0
        columns_skipped = 0

        for column in frame.columns:
            profile = all_profiles[column]
            if column in uncapped_names:
                column_violations, column_rule_count = self._skip_null_rule(
                    column=column,
                    profile=profile,
                    source="uncapped",
                    reason="Column was beyond the 100-column profile cap.",
                )
                violations.extend(column_violations)
                rule_count += column_rule_count
                continue

            raw_plan = plan_columns.get(column)
            if column not in profiled_names or not isinstance(raw_plan, dict):
                columns_skipped += 1
                column_violations, column_rule_count = self._skip_null_rule(
                    column=column,
                    profile=profile,
                    source="skipped",
                    reason="Column was missing from the LLM plan.",
                )
                violations.extend(column_violations)
                rule_count += column_rule_count
                continue

            column_plan = self._normalize_plan(raw_plan)
            if column_plan.skip:
                columns_skipped += 1
                column_violations, column_rule_count = self._skip_null_rule(
                    column=column,
                    profile=profile,
                    source="skipped",
                    reason=column_plan.skip_reason,
                )
                violations.extend(column_violations)
                rule_count += column_rule_count
                continue

            columns_analyzed += 1
            column_violations, column_rule_count = self._apply_column_constraints(
                frame=frame,
                column=column,
                plan=column_plan,
            )
            violations.extend(column_violations)
            rule_count += column_rule_count

        return violations, rule_count, {
            "columns_analyzed": columns_analyzed,
            "columns_skipped": columns_skipped,
            "columns_uncapped": len(uncapped_names),
        }

    def _normalize_plan(self, raw: dict[str, Any]) -> _ColumnPlan:
        severity = str(raw.get("severity_if_violated") or "Minor").title()
        if severity not in {"Critical", "Major", "Minor"}:
            severity = "Minor"
        return _ColumnPlan(
            skip=self._bool_value(raw.get("skip"), default=True),
            skip_reason=raw.get("skip_reason"),
            min_valid=self._optional_float(raw.get("min_valid")),
            max_valid=self._optional_float(raw.get("max_valid")),
            allow_negative=self._bool_value(raw.get("allow_negative"), default=True),
            allow_null=self._bool_value(raw.get("allow_null"), default=True),
            max_null_ratio=self._optional_ratio(raw.get("max_null_ratio")),
            expected_range_note=str(raw.get("expected_range_note") or "No specific constraint note provided."),
            severity_if_violated=severity,
        )

    def _apply_column_constraints(
        self,
        frame: pd.DataFrame,
        column: str,
        plan: _ColumnPlan,
    ) -> tuple[list[dict[str, Any]], int]:
        series = frame[column]
        numeric = pd.to_numeric(series, errors="coerce")
        violations: list[dict[str, Any]] = []
        rule_count = 0

        if plan.min_valid is not None:
            rule_count += 1
            violations.extend(
                self._row_violations(
                    frame,
                    column,
                    (numeric < plan.min_valid).fillna(False),
                    rule=f"{column}_below_minimum",
                    severity=plan.severity_if_violated,
                    message_builder=lambda value: (
                        f"{column} value {value} is below minimum {plan.min_valid}. "
                        f"{plan.expected_range_note}"
                    ),
                    source="llm_meta",
                    expected_range_note=plan.expected_range_note,
                )
            )

        if plan.max_valid is not None:
            rule_count += 1
            violations.extend(
                self._row_violations(
                    frame,
                    column,
                    (numeric > plan.max_valid).fillna(False),
                    rule=f"{column}_above_maximum",
                    severity=plan.severity_if_violated,
                    message_builder=lambda value: (
                        f"{column} value {value} exceeds maximum {plan.max_valid}. "
                        f"{plan.expected_range_note}"
                    ),
                    source="llm_meta",
                    expected_range_note=plan.expected_range_note,
                )
            )

        if not plan.allow_negative:
            rule_count += 1
            violations.extend(
                self._row_violations(
                    frame,
                    column,
                    (numeric < 0).fillna(False),
                    rule=f"{column}_negative_value",
                    severity=plan.severity_if_violated,
                    message_builder=lambda value: (
                        f"{column} contains negative value {value}. {plan.expected_range_note}"
                    ),
                    source="llm_meta",
                    expected_range_note=plan.expected_range_note,
                )
            )

        if not plan.allow_null:
            rule_count += 1
            violations.extend(
                self._row_violations(
                    frame,
                    column,
                    series.isna(),
                    rule=f"{column}_unexpected_null",
                    severity=plan.severity_if_violated,
                    message_builder=lambda _value: f"{column} is null. {plan.expected_range_note}",
                    source="llm_meta",
                    expected_range_note=plan.expected_range_note,
                )
            )

        if plan.allow_null and plan.max_null_ratio is not None:
            rule_count += 1
            actual_null_ratio = float(series.isna().mean())
            if actual_null_ratio > plan.max_null_ratio:
                violations.append(
                    self._dataset_violation(
                        column=column,
                        rule=f"{column}_excess_nulls",
                        severity=plan.severity_if_violated,
                        message=(
                            f"{column} null ratio {actual_null_ratio:.1%} exceeds allowed "
                            f"{plan.max_null_ratio:.1%}. {plan.expected_range_note}"
                        ),
                        value=round(actual_null_ratio, 4),
                        source="llm_meta",
                        expected_range_note=plan.expected_range_note,
                        extra={
                            "actual_null_ratio": round(actual_null_ratio, 4),
                            "max_null_ratio": plan.max_null_ratio,
                        },
                    )
                )

        if self._is_text_like(series):
            rule_count += 1
            violations.extend(
                self._row_violations(
                    frame,
                    column,
                    self._dirty_text_mask(series),
                    rule=f"{column}_invalid_taxonomy",
                    severity="Minor",
                    message_builder=lambda value: (
                        f"{column} contains taxonomy value with leading/trailing "
                        f"or control whitespace: {value!r}."
                    ),
                    source="rules",
                    expected_range_note="Text taxonomy values must be normalized before validation.",
                )
            )

        return violations, rule_count

    def _is_text_like(self, series: pd.Series) -> bool:
        if pd.api.types.is_string_dtype(series) or pd.api.types.is_object_dtype(series):
            non_null = series.dropna()
            if non_null.empty:
                return False
            return bool(non_null.map(lambda value: isinstance(value, str)).mean() >= 0.8)
        return False

    def _dirty_text_mask(self, series: pd.Series) -> pd.Series:
        def is_dirty(value: Any) -> bool:
            if not isinstance(value, str):
                return False
            return value != value.strip() or any(ord(char) < 32 for char in value)

        return series.map(is_dirty).fillna(False)

    def _skip_null_rule(
        self,
        column: str,
        profile: dict[str, Any],
        source: str,
        reason: str | None,
    ) -> tuple[list[dict[str, Any]], int]:
        null_ratio = self._profile_null_ratio(profile)
        if null_ratio <= 0.50:
            return [], 1
        return [
            self._dataset_violation(
                column=column,
                rule=f"{column}_excessive_nulls",
                severity="Major",
                message=f"{column} null ratio {null_ratio:.1%} exceeds allowed threshold 50.0%.",
                value=round(null_ratio, 4),
                source=source,
                expected_range_note=reason or "Column skipped by dataset-level analysis.",
                extra={"actual_null_ratio": round(null_ratio, 4), "skip_reason": reason},
            )
        ], 1

    def _fallback_violations(
        self,
        frame: pd.DataFrame,
        profile_pack: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], int]:
        violations: list[dict[str, Any]] = []
        all_profiles = profile_pack["all_profiles_by_name"]
        for column in frame.columns:
            null_ratio = self._profile_null_ratio(all_profiles[column])
            if null_ratio > 0.20:
                violations.append(
                    self._dataset_violation(
                        column=column,
                        rule=f"{column}_excessive_nulls",
                        severity="Critical",
                        message=f"{column} null ratio {null_ratio:.1%} exceeds allowed threshold 20.0%.",
                        value=round(null_ratio, 4),
                        source="fallback",
                        expected_range_note="Fallback null-ratio-only rule.",
                        extra={"actual_null_ratio": round(null_ratio, 4)},
                    )
                )
        return violations, len(frame.columns)

    def _row_violations(
        self,
        frame: pd.DataFrame,
        column: str,
        mask: pd.Series,
        rule: str,
        severity: str,
        message_builder: Any,
        source: str,
        expected_range_note: str,
    ) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        matched = frame.loc[mask]
        affected_rows = int(len(matched))
        sampled = affected_rows > _MAX_ROW_VIOLATIONS_PER_RULE
        for idx, row in matched.head(_MAX_ROW_VIOLATIONS_PER_RULE).iterrows():
            value = self._json_value(row.get(column))
            violations.append(
                {
                    "row_index": int(idx),
                    "rule": self._snake_case(rule),
                    "severity": severity,
                    "message": message_builder(value),
                    "column": column,
                    "value": value,
                    column: value,
                    "constraint_source": source,
                    "source": "llm" if source == "llm_meta" else "rules",
                    "expected_range_note": expected_range_note,
                    "affected_rows": affected_rows,
                    "sampled": sampled,
                    "sample_limit": _MAX_ROW_VIOLATIONS_PER_RULE,
                }
            )
        return violations

    def _dataset_violation(
        self,
        column: str,
        rule: str,
        severity: str,
        message: str,
        value: Any,
        source: str,
        expected_range_note: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "row_index": -1,
            "rule": self._snake_case(rule),
            "severity": severity,
            "message": message,
            "column": column,
            "value": self._json_value(value),
            column: self._json_value(value),
            "constraint_source": source,
            "source": "llm" if source == "llm_meta" else "rules",
            "expected_range_note": expected_range_note,
        }
        if extra:
            payload.update(extra)
        return payload

    def _profile_for(self, column: str, profile_dicts: list[dict[str, Any]]) -> dict[str, Any]:
        for profile in profile_dicts:
            if profile.get("name") == column:
                return profile
        return {}

    def _semantic_for(self, column: str, discovery_profiles: list[Any] | None) -> dict[str, Any]:
        for profile in discovery_profiles or []:
            if hasattr(profile, "to_dict"):
                payload = profile.to_dict()
            elif isinstance(profile, dict):
                payload = profile
            else:
                continue
            if payload.get("column_name") == column or payload.get("name") == column:
                return payload
        return {}

    def _summarize_severity(self, violations: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for violation in violations:
            severity = str(violation["severity"])
            counts[severity] = counts.get(severity, 0) + 1
        return counts

    def _optional_float(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _optional_ratio(self, value: Any) -> float | None:
        number = self._optional_float(value)
        if number is None:
            return None
        return max(0.0, min(1.0, number))

    def _bool_value(self, value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        token = str(value).strip().lower()
        if token in {"true", "1", "yes", "y"}:
            return True
        if token in {"false", "0", "no", "n"}:
            return False
        return default

    def _profile_name(self, profile: dict[str, Any]) -> str:
        return str(profile.get("n") or profile.get("name") or "")

    def _profile_null_ratio(self, profile: dict[str, Any]) -> float:
        try:
            return float(profile.get("null", profile.get("null_ratio", 0.0)) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _round_or_none(self, value: Any, digits: int = 4) -> float | None:
        try:
            if pd.isna(value):
                return None
            return round(float(value), digits)
        except (TypeError, ValueError):
            return None

    def _json_value(self, value: Any) -> Any:
        if pd.isna(value):
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        if isinstance(value, np.generic):
            return value.item()
        return value

    def _snake_case(self, value: str) -> str:
        value = re.sub(r"[^0-9A-Za-z_]+", "_", value)
        value = re.sub(r"_+", "_", value).strip("_").lower()
        return value or "constraint_violation"


DynamicRulesEngine = ContextAwareRulesEngine
