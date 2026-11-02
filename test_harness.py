"""
Veritas scorer test harness — benchmarks alpha recall across four fault types.

Usage:
    python test_harness.py

Scenarios
---------
Spike     : single transient spike; CUSUM should NOT alarm (momentary only).
Flat-line : sigma_current collapses below 10 % of sigma_ref (dead sensor).
Drift     : gradual upward drift — CUSUM accumulates and alarms by mid-sequence.
Step      : sudden permanent bias shift — CUSUM detects sustained mean offset.

Recall is defined as: anomalous steps where alpha < RECALL_THR / total anomalous steps.
Target: >= 80 % for all four.
"""

from __future__ import annotations

import math
import statistics
import sys
from decimal import Decimal
from typing import NamedTuple

import numpy as np

sys.path.insert(0, r"c:\Users\oussa\Desktop\veritas2\VERITAS")

from veritas_core.confidence.archetypes.inertial import InertialArchetype
from veritas_core.cusum.engine import update as cusum_update, is_alarm as cusum_is_alarm
from veritas_core.skills.base import SkillContext

# ---------------------------------------------------------------------------
# Constants matching the calibrated InertialDefaultSkill
# ---------------------------------------------------------------------------
SIGMA_REF = 0.74
K_STABILITY = 0.64
DECAY_LAMBDA = 0.001026
N_REF = 14
QUARANTINE_THR = Decimal("0.70")

ROLLING_WINDOW = 20
RECALL_THR = Decimal("0.50")   # alpha below this → anomaly flagged


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_ctx() -> SkillContext:
    return SkillContext(
        skill_id="veritas-inertial-default-v1",
        skill_version="1.0.0",
        archetype="INERTIAL",
        decay_lambda=Decimal(str(DECAY_LAMBDA)),
        T_max_s=3600,
        T_period_s=86400,
        T_hold_s=30,
        beta_cosine=Decimal("1.0"),
        n_ref=N_REF,
        n_min=1,
        sigma_ref=Decimal(str(SIGMA_REF)),
        k_stability=Decimal(str(K_STABILITY)),
        cycle_amplitude=Decimal("10.0"),
        value_min=Decimal("0"),
        value_max=Decimal("9999999"),
        unit="m/s²",
        sampling_hz=100,
        iso_standards=("ISO9001",),
        quarantine_thr=QUARANTINE_THR,
        strict_mode=False,
        physics_laws=(),
        llm_prompt_pack=None,
    )


def _sigma_current(window: list[float]) -> float:
    if len(window) < 2:
        return SIGMA_REF
    return statistics.stdev(window)


class StepResult(NamedTuple):
    step: int
    value: float
    sigma_current: float
    cusum_alarm: bool
    alpha: Decimal
    anomalous: bool


def run_veritas_scorer(
    observations: list[float],
    anomaly_mask: list[bool],
    *,
    cusum_enabled: bool = True,
) -> list[StepResult]:
    """
    Score every observation and return per-step results.

    mu_ref is derived from the first 30 observations (assumed clean baseline),
    matching the calibration contract — the scorer doesn't know the true mean,
    it estimates it from history.
    """
    archetype = InertialArchetype()
    base_ctx = _base_ctx()

    results: list[StepResult] = []
    window: list[float] = []

    s_high, s_low = 0.0, 0.0
    mu_ref = float(np.mean(observations[:30]))
    k_cusum = 0.5

    for i, (v, is_anomalous) in enumerate(zip(observations, anomaly_mask)):
        window.append(v)
        if len(window) > ROLLING_WINDOW:
            window.pop(0)

        sigma_current = _sigma_current(window)

        # update CUSUM accumulators
        s_high, s_low = cusum_update(s_high, s_low, v, mu_ref, k_cusum)
        cusum_alarm = cusum_enabled and cusum_is_alarm(s_high, s_low, h=5.0)

        metadata = {
            "sigma_current": sigma_current,
            "cusum_alarm": cusum_alarm,
        }

        ctx = base_ctx.with_observation(
            value=Decimal(str(v)),
            previous_value=Decimal(str(observations[i - 1] if i > 0 else v)),
            age_seconds=1.0,
            n_samples=len(window),
            metadata=metadata,
        )
        alpha = archetype.compute_alpha(ctx)
        results.append(StepResult(i, v, sigma_current, cusum_alarm, alpha, is_anomalous))

    return results


def recall(results: list[StepResult]) -> float:
    anomalous = [r for r in results if r.anomalous]
    if not anomalous:
        return 1.0
    flagged = sum(1 for r in anomalous if r.alpha < RECALL_THR)
    return flagged / len(anomalous)


# ---------------------------------------------------------------------------
# Scenario builders
# ---------------------------------------------------------------------------

def scenario_spike(n: int = 200) -> tuple[list[float], list[bool]]:
    """Normal signal; one large spike at step 100. CUSUM should not alarm."""
    import random
    rng = random.Random(42)
    obs = [rng.gauss(0.0, SIGMA_REF) for _ in range(n)]
    mask = [False] * n
    obs[100] = 15.0
    mask[100] = True
    return obs, mask


def scenario_flatline(n: int = 200, flat_start: int = 80) -> tuple[list[float], list[bool]]:
    """Normal then sensor freezes — sigma collapses to zero."""
    import random
    rng = random.Random(7)
    obs = [rng.gauss(0.0, SIGMA_REF) for _ in range(flat_start)]
    obs += [0.0] * (n - flat_start)
    mask = [False] * flat_start + [True] * (n - flat_start)
    return obs, mask


def scenario_drift(n: int = 300, drift_start: int = 100) -> tuple[list[float], list[bool]]:
    """Gradual upward drift — CUSUM accumulates and alarms."""
    import random
    rng = random.Random(99)
    obs = [rng.gauss(0.0, SIGMA_REF) for _ in range(drift_start)]
    for step in range(n - drift_start):
        obs.append(rng.gauss(0.05 * step, SIGMA_REF))
    mask = [False] * drift_start + [True] * (n - drift_start)
    return obs, mask


def scenario_step(n: int = 200, step_at: int = 80) -> tuple[list[float], list[bool]]:
    """Sudden permanent bias shift of +3σ — CUSUM detects sustained mean offset."""
    import random
    rng = random.Random(13)
    obs = [rng.gauss(0.0, SIGMA_REF) for _ in range(step_at)]
    obs += [rng.gauss(3.0 * SIGMA_REF, SIGMA_REF) for _ in range(n - step_at)]
    mask = [False] * step_at + [True] * (n - step_at)
    return obs, mask


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    scenarios = {
        "Spike":     scenario_spike(),
        "Flat-line": scenario_flatline(),
        "Drift":     scenario_drift(),
        "Step":      scenario_step(),
    }

    print(f"\n{'Scenario':<12}  {'Steps':>6}  {'Anomalous':>9}  {'Flagged':>7}  {'Recall':>7}  {'Pass?':>6}")
    print("-" * 58)

    all_pass = True
    for name, (obs, mask) in scenarios.items():
        results = run_veritas_scorer(obs, mask, cusum_enabled=True)
        r = recall(results)
        anomalous_n = sum(mask)
        flagged_n = sum(1 for res in results if res.anomalous and res.alpha < RECALL_THR)
        passed = r >= 0.80
        all_pass = all_pass and passed
        print(
            f"{name:<12}  {len(obs):>6}  {anomalous_n:>9}  {flagged_n:>7}  "
            f"{r:>6.1%}  {'PASS' if passed else 'FAIL':>6}"
        )

    print()
    print("Overall:", "PASS" if all_pass else "FAIL — one or more scenarios below 80 %")
    print()


if __name__ == "__main__":
    main()
