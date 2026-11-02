from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from .exceptions import ProfilingError
from .stage_result import StageResult


class Archetype(str, Enum):
    TEMPORAL = "TEMPORAL"
    IDENTIFIER = "IDENTIFIER"
    NUMERICAL = "NUMERICAL"
    CATEGORICAL = "CATEGORICAL"
    FREE_TEXT = "FREE_TEXT"


@dataclass(slots=True)
class ColumnProfile:
    name: str
    archetype: str
    dtype: str
    null_ratio: float
    unique_ratio: float
    sample_values: list[str]
    stats: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OperationalProfiler:
    def profile(self, frame: pd.DataFrame) -> StageResult:
        started = time.perf_counter()
        if frame.empty:
            raise ProfilingError("Cannot profile an empty dataframe")

        profiles: list[ColumnProfile] = []
        for column in frame.columns:
            series = frame[column]
            archetype = self._infer_archetype(column, series)
            null_ratio = float(series.isna().mean())
            unique_ratio = float(series.nunique(dropna=True) / max(len(series), 1))
            sample_values = [str(v) for v in series.dropna().head(3).tolist()]
            stats = self._build_stats(archetype, series)
            profiles.append(
                ColumnProfile(
                    name=column,
                    archetype=archetype.value,
                    dtype=str(series.dtype),
                    null_ratio=round(null_ratio, 4),
                    unique_ratio=round(unique_ratio, 4),
                    sample_values=sample_values,
                    stats=stats,
                )
            )

        return StageResult(
            data=profiles,
            metadata={
                "column_count": len(profiles),
                "archetype_breakdown": self._count_archetypes(profiles),
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    def _infer_archetype(self, name: str, series: pd.Series) -> Archetype:
        lower_name = name.lower()
        non_null = series.dropna()
        duration_tokens = (
            "downtime",
            "duration",
            "elapsed",
            "latency",
            "response_minutes",
            "age_hours",
        )
        if pd.api.types.is_numeric_dtype(series) and any(token in lower_name for token in duration_tokens):
            return Archetype.NUMERICAL
        if (
            pd.api.types.is_datetime64_any_dtype(series)
            or lower_name.endswith("_at")
            or lower_name.endswith("_ts")
            or lower_name.endswith("_date")
            or lower_name.endswith("_time")
            or lower_name in {"date", "time", "timestamp", "datetime"}
        ):
            return Archetype.TEMPORAL
        if any(token in lower_name for token in ("id", "ticket", "incident")):
            return Archetype.IDENTIFIER
        if pd.api.types.is_bool_dtype(series):
            return Archetype.CATEGORICAL
        if pd.api.types.is_numeric_dtype(series):
            return Archetype.NUMERICAL
        if non_null.empty:
            return Archetype.CATEGORICAL
        avg_len = non_null.astype(str).str.len().mean()
        if series.nunique(dropna=True) <= min(20, max(5, len(series) // 8)):
            return Archetype.CATEGORICAL
        if avg_len > 32:
            return Archetype.FREE_TEXT
        return Archetype.CATEGORICAL

    def _build_stats(self, archetype: Archetype, series: pd.Series) -> dict[str, Any]:
        cleaned = series.dropna()
        if archetype == Archetype.NUMERICAL and not cleaned.empty:
            return {
                "min": round(float(cleaned.min()), 4),
                "max": round(float(cleaned.max()), 4),
                "mean": round(float(cleaned.mean()), 4),
                "std": round(float(cleaned.std(ddof=0)), 4),
                "p95": round(float(cleaned.quantile(0.95)), 4),
            }
        if archetype == Archetype.TEMPORAL and not cleaned.empty:
            parsed = pd.to_datetime(cleaned, utc=True, errors="coerce").dropna()
            return {
                "min": parsed.min().isoformat() if not parsed.empty else None,
                "max": parsed.max().isoformat() if not parsed.empty else None,
                "parse_success_ratio": round(float(parsed.size / max(cleaned.size, 1)), 4),
            }
        top = cleaned.astype(str).value_counts().head(5)
        return {"top_values": top.to_dict()}

    def _count_archetypes(self, profiles: list[ColumnProfile]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for profile in profiles:
            counts[profile.archetype] = counts.get(profile.archetype, 0) + 1
        return counts
