"""Alignment utilities for messy industrial time series."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import numpy as np
import pandas as pd

_vlog = logging.getLogger(__name__)


@dataclass(slots=True)
class AlignmentResult:
    """Output of time-series alignment against a reference time column."""

    reference_column: str
    resolution: str
    gaps: list[dict[str, Any]]
    bursts: list[dict[str, Any]]
    aligned_df: pd.DataFrame


class TimeSeriesAligner:
    """Align an irregular dataframe to a uniform time grid."""

    _RESOLUTION_FREQ = {
        "seconds": "s",
        "minutes": "min",
        "hours": "h",
        "days": "D",
    }
    _RESOLUTION_SECONDS = {
        "seconds": 1.0,
        "minutes": 60.0,
        "hours": 3600.0,
        "days": 86400.0,
    }

    def __init__(self, resolution: str | None = None):
        """Create an aligner with optional fixed output resolution."""
        if resolution is not None and resolution not in self._RESOLUTION_FREQ:
            raise ValueError(f"Unsupported resolution: {resolution!r}")
        self.resolution = resolution

    def align(self, df: pd.DataFrame, time_col: str) -> AlignmentResult:
        """Align a dataframe to a uniform time grid based on a reference column."""
        if time_col not in df.columns:
            raise KeyError(f"Unknown time column: {time_col}")

        working = df.copy()
        parsed = pd.to_datetime(working[time_col], utc=True, errors="coerce")
        dropped_nulls = int(parsed.isna().sum())
        working = working.loc[parsed.notna()].copy()
        parsed = parsed.loc[parsed.notna()]
        if working.empty:
            raise ValueError(f"Column {time_col!r} does not contain any parseable timestamps")

        working[time_col] = parsed
        working = working.sort_values(time_col)

        intervals = working[time_col].diff().dropna()
        median_interval = intervals.median() if not intervals.empty else pd.Timedelta(hours=1)
        inferred_resolution = self.resolution or self._infer_resolution(median_interval)
        freq = self._RESOLUTION_FREQ[inferred_resolution]

        aligned = working.set_index(time_col)
        aligned = aligned[~aligned.index.duplicated(keep="last")]
        start = aligned.index.min().floor(freq)
        end = aligned.index.max().ceil(freq)
        target_index = pd.date_range(start=start, end=end, freq=freq, tz=aligned.index.tz)
        aligned_df = aligned.reindex(target_index)
        aligned_df.index.name = time_col

        gaps = self._detect_gaps(working[time_col], median_interval)
        bursts = self._detect_bursts(working[time_col], inferred_resolution)
        if dropped_nulls:
            _vlog.info("Dropped %d null or invalid timestamps from %s before alignment", dropped_nulls, time_col)

        return AlignmentResult(
            reference_column=time_col,
            resolution=inferred_resolution,
            gaps=gaps,
            bursts=bursts,
            aligned_df=aligned_df,
        )

    def _infer_resolution(self, median_interval: pd.Timedelta) -> str:
        seconds = max(median_interval.total_seconds(), 1.0)
        return min(
            self._RESOLUTION_SECONDS,
            key=lambda name: abs(self._RESOLUTION_SECONDS[name] - seconds),
        )

    def _detect_gaps(self, timestamps: pd.Series, median_interval: pd.Timedelta) -> list[dict[str, Any]]:
        threshold = median_interval * 3
        gaps: list[dict[str, Any]] = []
        previous = timestamps.shift(1)
        for current, prior in zip(timestamps.iloc[1:], previous.iloc[1:]):
            delta = current - prior
            if delta > threshold:
                gaps.append(
                    {
                        "start": prior.isoformat(),
                        "end": current.isoformat(),
                        "duration_seconds": float(delta.total_seconds()),
                    }
                )
        return gaps

    def _detect_bursts(self, timestamps: pd.Series, resolution: str) -> list[dict[str, Any]]:
        window_seconds = self._RESOLUTION_SECONDS[resolution]
        ordered = timestamps.sort_values()
        seconds = ordered.astype("int64") / 1_000_000_000
        bursts: list[dict[str, Any]] = []
        left = 0
        for right in range(len(seconds)):
            while seconds[right] - seconds[left] > window_seconds:
                left += 1
            count = right - left + 1
            if count > 5:
                bursts.append(
                    {
                        "timestamp": ordered.iloc[right].isoformat(),
                        "count_in_window": int(count),
                    }
                )
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for burst in bursts:
            key = (burst["timestamp"], burst["count_in_window"])
            if key not in seen:
                seen.add(key)
                deduped.append(burst)
        return deduped
