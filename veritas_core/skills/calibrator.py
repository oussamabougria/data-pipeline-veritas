"""
SkillCalibrator — derives skill parameters from historical scored_observations.

Runs as an Airflow task (not inline). Output is stored as calibration_runs.
A human must approve before the resulting skill version activates.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

_PLACES = Decimal("0.0001")


@dataclass
class CalibrationResult:
    sensor_id: str
    site_id: str
    days_of_history: int
    sigma_ref: Decimal
    decay_lambda: Decimal
    n_ref: int                   # 80th percentile of observed sample counts
    T_period_s: int | None       # None unless CYCLICAL and FFT succeeds
    quarantine_thr: Decimal      # P5 of good-data confidence scores (after 30 days)


class SkillCalibrator:
    MIN_HISTORY_DAYS = 7

    def calibrate(
        self,
        sensor_id: str,
        site_id: str,
        rows: list[dict],        # rows from scored_observations for this sensor
    ) -> CalibrationResult | None:
        """
        Compute calibrated parameters from scored_observations rows.

        Each row must have: ts, value, confidence, tau, delta, alpha,
                            n_samples (int), age_seconds (float).
        Returns None if fewer than MIN_HISTORY_DAYS of data.
        """
        if not rows:
            return None

        timestamps = sorted(r["ts"] for r in rows)
        days_span = (timestamps[-1] - timestamps[0]).days
        if days_span < self.MIN_HISTORY_DAYS:
            return None

        # Exclude quarantined windows: rows with confidence < 0.5 are treated as noisy
        good_rows = [r for r in rows if float(r["confidence"]) >= 0.5]
        if not good_rows:
            good_rows = rows  # fallback: use all rows

        # sigma_ref: rolling std-dev of values over good rows
        values = [float(r["value"]) for r in good_rows]
        sigma_ref = Decimal(str(statistics.stdev(values))).quantize(_PLACES, rounding=ROUND_HALF_UP) \
            if len(values) >= 2 else Decimal("1.0000")

        # decay_lambda: fitted to observed age distribution
        # Use mean age_seconds to derive lambda such that τ ≈ 0.5 at mean age
        ages = [float(r.get("age_seconds", 0)) for r in good_rows if r.get("age_seconds", 0) > 0]
        if ages:
            mean_age = statistics.mean(ages)
            # exp(-lambda * mean_age) = 0.5 → lambda = ln(2) / mean_age
            decay_lambda = Decimal(str(math.log(2) / mean_age)).quantize(_PLACES, rounding=ROUND_HALF_UP) \
                if mean_age > 0 else Decimal("0.0500")
        else:
            decay_lambda = Decimal("0.0500")

        # n_ref: 80th percentile of observed sample counts
        sample_counts = sorted(int(r.get("n_samples", 1)) for r in good_rows)
        p80_idx = max(0, int(0.8 * len(sample_counts)) - 1)
        n_ref = sample_counts[p80_idx] if sample_counts else 20

        # T_period_s: FFT-based cycle detection (CYCLICAL only)
        T_period_s = self._detect_period(values) if len(values) >= 32 else None

        # quarantine_thr: P5 of good-data confidence scores (conservative after 30 days)
        confidences = sorted(float(r["confidence"]) for r in good_rows)
        if days_span >= 30 and len(confidences) >= 20:
            p5_idx = max(0, int(0.05 * len(confidences)))
            quarantine_thr = Decimal(str(confidences[p5_idx])).quantize(_PLACES, rounding=ROUND_HALF_UP)
        else:
            # Conservative default until 30 days of data
            quarantine_thr = Decimal("0.6000")

        return CalibrationResult(
            sensor_id=sensor_id,
            site_id=site_id,
            days_of_history=days_span,
            sigma_ref=sigma_ref,
            decay_lambda=decay_lambda,
            n_ref=n_ref,
            T_period_s=T_period_s,
            quarantine_thr=quarantine_thr,
        )

    def _detect_period(self, values: list[float]) -> int | None:
        """
        Detect dominant cycle period via FFT.
        Returns period in seconds, or None if no clear peak.
        Assumes 1-second sampling interval for simplicity; caller must adjust.
        """
        try:
            import numpy as np
        except ImportError:
            return None

        n = len(values)
        fft_vals = np.abs(np.fft.rfft(values - np.mean(values)))
        freqs = np.fft.rfftfreq(n, d=1.0)  # d=1s assumed
        # Ignore DC component (index 0)
        if len(fft_vals) < 2:
            return None
        peak_idx = int(np.argmax(fft_vals[1:])) + 1
        dominant_freq = freqs[peak_idx]
        if dominant_freq <= 0:
            return None
        period_s = int(round(1.0 / dominant_freq))
        return period_s if period_s > 0 else None
