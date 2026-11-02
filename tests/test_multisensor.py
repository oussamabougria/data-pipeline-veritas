"""
3.2 — Multi-sensor generalization: all 21 CMAPSS sensors.

For each sensor:
  1. Auto-calibrate parameters from synthetic healthy history.
  2. Score fresh healthy observations (age=10 s, n_samples=n_ref,
     sigma_current=sigma_ref exactly).
  3. Assert confidence >= 0.70.

The sigma_current is pinned to sigma_ref (ideal healthy) so the test isolates
calibration quality rather than rolling-window variance.  If a sensor fails,
the calibrator produced parameters that guarantee sub-threshold scores even
for textbook-healthy data — that is the invisible bug this test hunts.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from veritas_core.skills.calibrator import SkillCalibrator
from veritas_core.skills.base import SkillContext
from veritas_core.confidence.archetypes.inertial import InertialArchetype
from veritas_core.confidence.formula import compute_confidence

# ---------------------------------------------------------------------------
# CMAPSS sensor catalogue — (name, typical_mu, typical_sigma)
# sigma == 0.001 marks sensors that are effectively constant in FD001
# (s1, s5, s6, s10, s16, s19); the calibrator must still produce usable params.
# ---------------------------------------------------------------------------

CMAPSS_SENSORS: list[tuple[str, float, float]] = [
    ("s1",   518.67,   0.001),
    ("s2",   641.82,   0.74),
    ("s3",  1589.70,   2.50),
    ("s4",  1400.60,   5.50),
    ("s5",    14.62,   0.001),
    ("s6",    21.61,   0.001),
    ("s7",   554.36,   0.80),
    ("s8",  2388.06,   5.50),
    ("s9",  9046.19,  20.00),
    ("s10",    1.30,   0.001),
    ("s11",   47.47,   0.40),
    ("s12",  521.66,   1.80),
    ("s13", 2388.06,   5.00),
    ("s14", 8138.62,  20.00),
    ("s15",    8.42,   0.015),
    ("s16",    0.03,   0.001),
    ("s17",  392.00,   0.50),
    ("s18", 2388.00,   5.00),
    ("s19",  100.00,   0.001),
    ("s20",   39.06,   0.25),
    ("s21",   23.46,   0.35),
]

_K_STABILITY = Decimal("0.64")
_AGE_FRESH_S = 10.0
_MEAN_AGE_CAL_S = 675.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_calib_rows(mu: float, sigma: float, seed: int, n: int = 300) -> list[dict]:
    """
    300 synthetic rows spanning 30 days.
    Same statistical shape as test_calibrator._build_s2_rows but with
    per-sensor mu/sigma and a per-sensor seed for independent RNG streams.
    """
    rng = random.Random(seed)
    base_ts = datetime(2024, 1, 1)
    rows: list[dict] = []

    for i in range(n):
        ts = base_ts + timedelta(hours=2.4 * i)
        value = rng.gauss(mu, sigma) if sigma > 0.01 else mu
        age_s = max(30.0, rng.gauss(_MEAN_AGE_CAL_S, 30.0))
        n_samp = rng.randint(8, 12) if rng.random() < 0.20 else rng.randint(13, 15)
        rows.append({
            "ts": ts,
            "value": value,
            "confidence": 0.80,
            "age_seconds": age_s,
            "n_samples": n_samp,
        })

    return rows


def _base_ctx(result, sensor_name: str) -> SkillContext:
    return SkillContext(
        skill_id=f"test-{sensor_name}",
        skill_version="1.0.0",
        archetype="INERTIAL",
        decay_lambda=result.decay_lambda,
        T_max_s=3600,
        T_period_s=86400,
        T_hold_s=30,
        beta_cosine=Decimal("1.0"),
        n_ref=result.n_ref,
        n_min=1,
        sigma_ref=result.sigma_ref,
        k_stability=_K_STABILITY,
        cycle_amplitude=Decimal("10.0"),
        value_min=Decimal("0"),
        value_max=Decimal("9999999"),
        unit="",
        sampling_hz=None,
        iso_standards=("ISO9001",),
        quarantine_thr=result.quarantine_thr,
        strict_mode=False,
        physics_laws=(),
        llm_prompt_pack=None,
    )


# ---------------------------------------------------------------------------
# Parametrized test — one case per CMAPSS sensor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sensor_name,mu,sigma", CMAPSS_SENSORS)
def test_healthy_data_scores_above_threshold(
    sensor_name: str, mu: float, sigma: float
) -> None:
    """
    With calibrated parameters, an observation that is genuinely healthy
    (sigma_current == sigma_ref, fresh, n_samples == n_ref) must score >= 0.70.

    Failure means the calibrator generated parameters that make healthy
    sensors look sick — a silent false-positive factory.
    """
    seed = abs(hash(sensor_name)) % (2 ** 31)
    calibrator = SkillCalibrator()
    rows = _build_calib_rows(mu, sigma, seed=seed)
    result = calibrator.calibrate(
        sensor_id=f"unit1_{sensor_name}",
        site_id="00000000-0000-0000-0000-000000000001",
        rows=rows,
    )
    assert result is not None, f"{sensor_name}: calibration returned None"

    ctx = _base_ctx(result, sensor_name)
    archetype = InertialArchetype()

    # Ideal healthy observation: sigma_current is exactly sigma_ref
    # (no rolling-window noise), fresh data, full sample count.
    obs_ctx = ctx.with_observation(
        value=Decimal(str(round(mu, 6))),
        previous_value=Decimal(str(round(mu, 6))),
        age_seconds=_AGE_FRESH_S,
        n_samples=result.n_ref,
        metadata={
            "sigma_current": float(result.sigma_ref),
            "cusum_alarm": False,
        },
    )

    tau = archetype.compute_tau(obs_ctx)
    delta = archetype.compute_delta(obs_ctx)
    alpha = archetype.compute_alpha(obs_ctx)
    confidence = compute_confidence(tau, delta, alpha)

    assert confidence >= Decimal("0.70"), (
        f"{sensor_name} (sigma_ref={result.sigma_ref}, "
        f"decay_lambda={result.decay_lambda}, n_ref={result.n_ref}): "
        f"healthy data confidence={confidence} < 0.70. "
        f"τ={tau}, δ={delta}, α={alpha}. "
        f"Calibration produced parameters that make healthy sensors look sick."
    )
