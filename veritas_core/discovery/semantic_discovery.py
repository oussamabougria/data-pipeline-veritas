"""Semantic discovery for arbitrary operational datasets."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import logging
import time
from typing import Any

import numpy as np
import pandas as pd

_vlog = logging.getLogger(__name__)


class SemanticRole(str, Enum):
    """High-level semantic role inferred for a dataframe column."""

    IDENTIFIER = "IDENTIFIER"
    TEMPORAL = "TEMPORAL"
    METRIC = "METRIC"
    CATEGORICAL = "CATEGORICAL"
    FLAG = "FLAG"
    TEXT = "TEXT"
    UNKNOWN = "UNKNOWN"


class Archetype(str, Enum):
    """Score archetype assigned to a scoreable column."""

    INERTIAL = "INERTIAL"
    CUMULATIVE = "CUMULATIVE"
    CYCLICAL = "CYCLICAL"
    BINARY = "BINARY"
    NONE = "NONE"


@dataclass(slots=True)
class ColumnProfile:
    """Semantic profile for one dataframe column."""

    column_name: str
    role: SemanticRole
    archetype: Archetype
    confidence: float
    anomalies: list[str]
    notes: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        payload = asdict(self)
        payload["role"] = self.role.value
        payload["archetype"] = self.archetype.value
        return payload


class SemanticDiscovery:
    """Infer semantic roles and score archetypes from a dataframe."""

    _IDENTIFIER_HINTS = (
        "id",
        "uuid",
        "guid",
        "hash",
        "serial",
        "ticket",
        "incident",
        "asset",
        "line",
        "batch",
        "order",
        "ref",
        "code",
    )
    _TEMPORAL_HINTS = (
        "time",
        "date",
        "timestamp",
        "_at",
        "_ts",
    )
    _DURATION_HINTS = (
        "minute",
        "minutes",
        "min",
        "hour",
        "hours",
        "day",
        "days",
        "age",
        "response",
        "elapsed",
        "duration",
        "latency",
    )
    _CUMULATIVE_HINTS = (
        "count",
        "total",
        "sum",
        "counter",
        "ops",
        "quantity",
        "qty",
        "volume",
        "recurrence",
        "attempt",
    )

    def discover(self, df: pd.DataFrame) -> list[ColumnProfile]:
        """Profile each dataframe column semantically."""
        started = time.perf_counter()
        profiles: list[ColumnProfile] = []
        row_count = max(len(df), 1)
        for column_name in df.columns:
            series = df[column_name]
            profile = self._profile_column(column_name, series, row_count)
            profiles.append(profile)
        _vlog.info(
            "Semantic discovery profiled %d columns in %.2f ms",
            len(profiles),
            (time.perf_counter() - started) * 1000,
        )
        return profiles

    def _profile_column(self, column_name: str, series: pd.Series, row_count: int) -> ColumnProfile:
        non_null = series.dropna()
        unique_count = int(non_null.nunique(dropna=True))
        unique_ratio = unique_count / row_count
        lowered_name = column_name.lower()
        anomalies = self._detect_anomalies(column_name, series)

        if self._is_flag(series, non_null):
            return ColumnProfile(
                column_name=column_name,
                role=SemanticRole.FLAG,
                archetype=Archetype.BINARY,
                confidence=0.98,
                anomalies=anomalies,
                notes="boolean or two-state column",
            )

        temporal_kind = self._temporal_kind(column_name, series)
        if temporal_kind is not None:
            archetype = self._temporal_archetype(column_name, series, temporal_kind)
            return ColumnProfile(
                column_name=column_name,
                role=SemanticRole.TEMPORAL,
                archetype=archetype,
                confidence=0.93,
                anomalies=anomalies,
                notes="timestamp-like column",
            )

        if self._is_text(series, non_null, unique_ratio):
            median_length = float(non_null.astype(str).str.len().median()) if not non_null.empty else 0.0
            return ColumnProfile(
                column_name=column_name,
                role=SemanticRole.TEXT,
                archetype=Archetype.NONE,
                confidence=0.94,
                anomalies=anomalies,
                notes=f"free-form text; median length={median_length:.1f}",
            )

        if pd.api.types.is_numeric_dtype(series):
            archetype = self._metric_archetype(column_name, series)
            notes = (
                "numeric duration metric column"
                if self._has_duration_hint(lowered_name)
                else "numeric metric column"
            )
            return ColumnProfile(
                column_name=column_name,
                role=SemanticRole.METRIC,
                archetype=archetype,
                confidence=0.9,
                anomalies=anomalies,
                notes=notes,
            )

        if self._looks_categorical(unique_count, unique_ratio):
            return ColumnProfile(
                column_name=column_name,
                role=SemanticRole.CATEGORICAL,
                archetype=Archetype.NONE,
                confidence=0.92,
                anomalies=anomalies,
                notes=f"finite vocabulary; unique_ratio={unique_ratio:.3f}, unique_count={unique_count}",
            )

        if self._looks_identifier(column_name, unique_ratio, non_null):
            return ColumnProfile(
                column_name=column_name,
                role=SemanticRole.IDENTIFIER,
                archetype=Archetype.NONE,
                confidence=0.91,
                anomalies=anomalies,
                notes=f"near-unique identifier; unique_ratio={unique_ratio:.3f}",
            )

        if unique_count <= max(30, row_count // 10):
            return ColumnProfile(
                column_name=column_name,
                role=SemanticRole.CATEGORICAL,
                archetype=Archetype.NONE,
                confidence=0.75,
                anomalies=anomalies,
                notes="fallback categorical classification",
            )

        return ColumnProfile(
            column_name=column_name,
            role=SemanticRole.UNKNOWN,
            archetype=Archetype.NONE,
            confidence=0.4,
            anomalies=anomalies,
            notes="insufficient evidence for a stronger semantic role",
        )

    def _is_flag(self, series: pd.Series, non_null: pd.Series) -> bool:
        if pd.api.types.is_bool_dtype(series):
            return True
        if non_null.empty:
            return False
        tokens = {str(value).strip().lower() for value in non_null}
        flag_tokens = {"0", "1", "true", "false", "yes", "no", "y", "n", "on", "off"}
        return len(tokens) <= 2 and tokens.issubset(flag_tokens)

    def _is_text(self, series: pd.Series, non_null: pd.Series, unique_ratio: float) -> bool:
        if non_null.empty or pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
            return False
        text_values = non_null.astype(str)
        median_length = float(text_values.str.len().median())
        word_ratio = float(text_values.str.contains(r"\s", regex=True).mean())
        return median_length > 30 and (unique_ratio > 0.5 or word_ratio > 0.9)

    def _temporal_kind(self, column_name: str, series: pd.Series) -> str | None:
        lowered = column_name.lower()
        if pd.api.types.is_datetime64_any_dtype(series):
            return "timestamp"
        parse_ratio = 0.0
        if not pd.api.types.is_numeric_dtype(series):
            parsed = pd.to_datetime(series, utc=True, errors="coerce", format="mixed")
            parse_ratio = float(parsed.notna().mean())
        if self._has_timestamp_hint(lowered) and parse_ratio >= 0.8:
            return "timestamp"
        return None

    def _has_timestamp_hint(self, lowered: str) -> bool:
        return (
            lowered.endswith("_at")
            or lowered.endswith("_ts")
            or lowered.endswith("_time")
            or lowered.endswith("_date")
            or lowered in {"time", "date", "timestamp", "datetime"}
        )

    def _has_duration_hint(self, lowered: str) -> bool:
        return any(hint in lowered for hint in self._DURATION_HINTS)

    def _temporal_archetype(self, column_name: str, series: pd.Series, temporal_kind: str) -> Archetype:
        if temporal_kind == "timestamp":
            return Archetype.INERTIAL
        numeric = pd.to_numeric(series, errors="coerce").dropna()
        if numeric.empty:
            return Archetype.INERTIAL
        mean_value = float(numeric.mean())
        std_value = float(numeric.std(ddof=0))
        coefficient = 0.0 if mean_value == 0 else abs(std_value / mean_value)
        _vlog.debug("Temporal duration CV for %s: %.4f", column_name, coefficient)
        return Archetype.CYCLICAL if coefficient > 0.3 else Archetype.INERTIAL

    def _metric_archetype(self, column_name: str, series: pd.Series) -> Archetype:
        lowered = column_name.lower()
        numeric = pd.to_numeric(series, errors="coerce").dropna()
        if any(hint in lowered for hint in self._CUMULATIVE_HINTS):
            return Archetype.CUMULATIVE
        if numeric.empty:
            return Archetype.NONE
        distinct_values = numeric.nunique(dropna=True)
        if distinct_values <= 2:
            return Archetype.BINARY
        diffs = numeric.diff().dropna()
        if not diffs.empty and bool((diffs >= 0).mean() >= 0.95):
            return Archetype.CUMULATIVE
        mean_value = float(numeric.mean())
        std_value = float(numeric.std(ddof=0))
        coefficient = 0.0 if mean_value == 0 else abs(std_value / mean_value)
        if coefficient <= 0.15:
            return Archetype.INERTIAL
        return Archetype.CYCLICAL

    def _looks_categorical(self, unique_count: int, unique_ratio: float) -> bool:
        return unique_ratio <= 0.15 and unique_count <= 30

    def _looks_identifier(self, column_name: str, unique_ratio: float, non_null: pd.Series) -> bool:
        lowered = column_name.lower()
        if unique_ratio <= 0.5:
            return False
        if any(hint in lowered for hint in self._IDENTIFIER_HINTS):
            return True
        if non_null.empty:
            return False
        tokens = non_null.astype(str)
        code_like_ratio = float(tokens.str.contains(r"^[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+$", regex=True).mean())
        return code_like_ratio >= 0.6

    def _detect_anomalies(self, column_name: str, series: pd.Series) -> list[str]:
        anomalies: list[str] = []
        lowered = column_name.lower()
        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
            numeric = pd.to_numeric(series, errors="coerce").dropna()
            if not numeric.empty:
                negative_values = numeric[numeric < 0]
                if not negative_values.empty and any(
                    hint in lowered for hint in self._DURATION_HINTS + self._CUMULATIVE_HINTS
                ):
                    samples = ", ".join(str(value) for value in negative_values.head(3).tolist())
                    anomalies.append(f"negative_values={samples}")

                q1 = float(numeric.quantile(0.25))
                q3 = float(numeric.quantile(0.75))
                iqr = q3 - q1
                if iqr > 0:
                    lower = q1 - 1.5 * iqr
                    upper = q3 + 1.5 * iqr
                    outliers = numeric[(numeric < lower) | (numeric > upper)]
                    if not outliers.empty:
                        anomalies.append(f"outliers={len(outliers)}")

        if pd.api.types.is_datetime64_any_dtype(series) or self._has_timestamp_hint(lowered):
            parsed = pd.to_datetime(series, utc=True, errors="coerce", format="mixed")
            invalid_count = int(series.notna().sum() - parsed.notna().sum())
            if invalid_count > 0:
                anomalies.append(f"unparseable_timestamps={invalid_count}")
            if parsed.notna().any():
                future_count = int((parsed > pd.Timestamp.now(tz="UTC")).fillna(False).sum())
                if future_count > 0:
                    anomalies.append(f"future_timestamps={future_count}")

        if series.isna().any():
            anomalies.append(f"nulls={int(series.isna().sum())}")
        return anomalies
