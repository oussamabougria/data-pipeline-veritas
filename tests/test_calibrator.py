"""
3.1 — SkillCalibrator unit test: CMAPSS sensor s2, unit 1.

Synthetic rows are generated with statistics that match the manually derived
values (sigma≈0.74, mean_age≈675s → λ≈0.001027, n_samples P80≈14).

If the calibrator breaks silently, these assertions catch it.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from veritas_core.skills.calibrator import SkillCalibrator, CalibrationResult

# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

_S2_MU = 641.82
_S2_SIGMA = 0.74           # target sigma_ref
_MEAN_AGE_S = 675.0        # → decay_lambda = ln(2)/675 ≈ 0.001027


def _build_s2_rows(n: int = 300, seed: int = 42) -> list[dict]:
    """
    Synthetic CMAPSS s2, unit 1 rows with controlled statistics:
      - values ~ N(641.82, 0.74²)
      - age_seconds ~ N(675, 30²)          → mean_age ≈ 675 s
      - n_samples: 80 % in [13, 15],
                   20 % in [ 8, 12]        → P80 ≈ 14-15
      - confidence = 0.80 (all rows pass the calibrator's 0.5 threshold)
      - span = n × 2.4 h ≈ 30 days        (above MIN_HISTORY_DAYS = 7)
    """
    rng = random.Random(seed)
    base_ts = datetime(2024, 1, 1)
    rows: list[dict] = []

    for i in range(n):
        ts = base_ts + timedelta(hours=2.4 * i)
        value = rng.gauss(_S2_MU, _S2_SIGMA)
        age_s = max(30.0, rng.gauss(_MEAN_AGE_S, 30.0))
        if rng.random() < 0.20:
            n_samp = rng.randint(8, 12)
        else:
            n_samp = rng.randint(13, 15)
        rows.append({
            "ts": ts,
            "value": value,
            "confidence": 0.80,
            "age_seconds": age_s,
            "n_samples": n_samp,
        })

    return rows


# ---------------------------------------------------------------------------
# Shared fixture (calibrated once per module)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def s2_result() -> CalibrationResult:
    calibrator = SkillCalibrator()
    rows = _build_s2_rows()
    result = calibrator.calibrate(
        sensor_id="unit1_s2",
        site_id="00000000-0000-0000-0000-000000000001",
        rows=rows,
    )
    assert result is not None, (
        "SkillCalibrator returned None — fewer than MIN_HISTORY_DAYS of data "
        "or some other early-exit bug."
    )
    return result


# ---------------------------------------------------------------------------
# 3.1a — sigma_ref
# ---------------------------------------------------------------------------

def test_s2_sigma_ref(s2_result: CalibrationResult) -> None:
    """
    sigma_ref must reflect the true process stdev ≈ 0.74.
    ±10 % tolerance: [0.67, 0.81].
    A value outside this range means the calibrator is using the wrong rows,
    wrong filter, or the wrong stdev formula.
    """
    sr = float(s2_result.sigma_ref)
    assert 0.67 <= sr <= 0.81, (
        f"sigma_ref={sr:.4f} is outside [0.67, 0.81]. "
        f"Expected ≈ {_S2_SIGMA} for CMAPSS s2 unit 1."
    )


# ---------------------------------------------------------------------------
# 3.1b — decay_lambda
# ---------------------------------------------------------------------------

def test_s2_decay_lambda(s2_result: CalibrationResult) -> None:
    """
    decay_lambda = ln(2) / mean_age_seconds.
    With mean_age ≈ 675 s, expected ≈ 0.001027.
    Tolerance: [0.0009, 0.0012].
    A value outside this range means the calibrator is fitting to the wrong
    age distribution — τ will then decay at the wrong rate in production.
    """
    dl = float(s2_result.decay_lambda)
    assert 0.0009 <= dl <= 0.0012, (
        f"decay_lambda={dl:.6f} is outside [0.0009, 0.0012]. "
        f"Expected ≈ 0.001027 (ln(2)/{_MEAN_AGE_S:.0f}s)."
    )


# ---------------------------------------------------------------------------
# 3.1c — n_ref
# ---------------------------------------------------------------------------

def test_s2_n_ref(s2_result: CalibrationResult) -> None:
    """
    n_ref = P80 of observed n_samples.
    Input distribution is 80 % in [13, 15], so P80 ≈ 14-15.
    Acceptable range: [12, 16].
    A value outside this range means the percentile index formula is wrong or
    n_samples rows are being dropped incorrectly.
    """
    assert 12 <= s2_result.n_ref <= 16, (
        f"n_ref={s2_result.n_ref} is outside [12, 16]. "
        f"P80 of n_samples distribution (80 % in [13,15]) should be ≈ 14-15."
    )
