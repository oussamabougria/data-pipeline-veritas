"""
Falsification benchmark for VERITAS's C(v).

Question it answers: does C(v) — the multiplicative trust index — actually
separate known-good from known-faulty sensor readings, and does it beat the
baselines a plant already has (staleness, rolling variance, SPC/flatline)?

Method
------
Generate labeled telemetry for several sensor archetypes. Inject faults at
known indices -> per-reading ground truth. Score every reading two ways:
  (a) the canonical ConfidenceScorer (τ_age × δ × α), and
  (b) deterministic baselines (staleness, rolling-variance, flatline/SPC).
Report ROC-AUC + precision/recall at the chosen operating threshold.

Pre-committed kill criteria (written before any run):
  KILL   if C(v) AUC < SPC-style baseline AUC, or < 0.70 overall.
  WEAK   if C(v) beats baselines but AUC < 0.85 — rescope to audit layer.
  PASS   if C(v) AUC >= 0.85 AND > every baseline AUC.

Usage:
    .venv/Scripts/python.exe benchmarks/falsification.py
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from veritas_core.confidence.archetypes.inertial import InertialArchetype
from veritas_core.confidence.archetypes.cumulative import CumulativeArchetype
from veritas_core.confidence.archetypes.binary import BinaryArchetype
from veritas_core.confidence.formula import compute_confidence
from veritas_core.skills.base import SkillContext

# ── Benchmark config ─────────────────────────────────────────────────────────

SEED = 42
N_HEALTHY = 400          # healthy readings per sensor before faults
FAULT_BLOCK = 40         # length of each injected fault episode
SAMPLING_HZ = 1.0        # expected period = 1s
AGE_FRESH_S = 2.0        # typical fresh-reading age

# ── Ground truth labels ──────────────────────────────────────────────────────


@dataclass
class Reading:
    value: float
    age_s: float          # seconds since emission
    n_samples: int        # samples in the rolling window feeding this reading
    sigma_current: float  # rolling std of recent values
    cusum_alarm: bool
    flip: bool            # did the value flip vs previous (binary archetypes)
    label: int            # 1 = faulty, 0 = good
    prev_value: float = 0.0  # previous emitted reading (for monotonic / binary α)


# ── Sensor simulators + fault injectors ──────────────────────────────────────


def _roll_sigma(window: list[float]) -> float:
    if len(window) < 2:
        return 0.0
    m = sum(window) / len(window)
    var = sum((x - m) ** 2 for x in window) / (len(window) - 1)
    return math.sqrt(var)


def _cusum_state(history: list[float], mu_ref: float, k: float = 0.5, h: float = 4.0) -> bool:
    """Rolling CUSUM over the recent window — True if either accumulator exceeds h."""
    sh = sl = 0.0
    for v in history[-20:]:
        dev = v - mu_ref
        sh = max(0.0, sh + dev - k)
        sl = max(0.0, sl - dev - k)
    return sh >= h or sl >= h


def gen_inertial(rng: random.Random, mu: float = 100.0, sigma: float = 2.0) -> list[Reading]:
    """Vibration/IMU-like sensor: Gaussian noise around a slow drift baseline."""
    out: list[Reading] = []
    window: list[float] = []
    drift = 0.0
    i = 0
    fault = None
    fault_end = -1
    n_target = 14
    while i < N_HEALTHY + 3 * FAULT_BLOCK:
        # choose a fault episode every ~N_HEALTHY/3 good readings
        if i in (N_HEALTHY, N_HEALTHY + FAULT_BLOCK, N_HEALTHY + 2 * FAULT_BLOCK):
            fault = rng.choice(["freeze", "drift", "spike", "dropout", "flatline", "jitter"])
            fault_end = i + FAULT_BLOCK
        good = not (i >= N_HEALTHY and i < fault_end)
        # base signal
        drift += rng.gauss(0, 0.02)
        val = mu + drift + rng.gauss(0, sigma)
        drop = False
        if not good:
            if fault == "freeze":
                val = window[-1] if window else mu
            elif fault == "drift":
                # gradual drift off-baseline: σ stays similar, mean shifts ->
                # caught by CUSUM, not by σ-dev
                val = (window[-1] if window else mu) + 1.5 * sigma
            elif fault == "spike":
                val = mu + rng.choice([-1, 1]) * 6 * sigma
            elif fault == "dropout":
                drop = rng.random() < 0.7
            elif fault == "flatline":
                val = mu + rng.gauss(0, 0.001)  # dead sensor: near-zero variance
            elif fault == "jitter":
                val = mu + drift + rng.gauss(0, 3 * sigma)  # high-variance noise
        window.append(val)
        if len(window) > 20:
            window.pop(0)
        n_samples = 0 if drop else min(len(window), n_target)
        # rolling σ over recent emitted values (dropouts excluded — they emit nothing)
        sigma_cur = _roll_sigma(window[-20:]) if not drop else 0.0
        out.append(Reading(
            value=val,
            age_s=AGE_FRESH_S,
            n_samples=n_samples,
            sigma_current=sigma_cur,
            cusum_alarm=_cusum_state(window, mu),
            flip=False,
            label=0 if good else 1,
        ))
        i += 1
    return out


def gen_cumulative(rng: random.Random, rate: float = 1.0, sigma: float = 0.1) -> list[Reading]:
    """Monotonic counter (flow/energy meter)."""
    out: list[Reading] = []
    window: list[float] = []
    acc = 0.0
    i = 0
    fault = None
    fault_end = -1
    while i < N_HEALTHY + 3 * FAULT_BLOCK:
        if i in (N_HEALTHY, N_HEALTHY + FAULT_BLOCK, N_HEALTHY + 2 * FAULT_BLOCK):
            fault = rng.choice(["reset", "reverse", "dropout", "stuck"])
            fault_end = i + FAULT_BLOCK
        good = not (i >= N_HEALTHY and i < fault_end)
        acc += rate + rng.gauss(0, sigma)
        val = acc
        drop = False
        if not good:
            if fault == "reset":
                acc = rng.uniform(0, 5); val = acc
            elif fault == "reverse":
                val = (window[-1] if window else acc) - abs(rate * 2)
            elif fault == "dropout":
                drop = rng.random() < 0.6
            elif fault == "stuck":
                val = window[-1] if window else acc
        window.append(val)
        if len(window) > 20:
            window.pop(0)
        prev = window[-2] if len(window) >= 2 else val
        out.append(Reading(
            value=val,
            age_s=AGE_FRESH_S,
            n_samples=0 if drop else min(len(window), 10),
            sigma_current=_roll_sigma(window[-20:]),
            cusum_alarm=False,
            flip=val < prev,
            label=0 if good else 1,
            prev_value=prev,
        ))
        i += 1
    return out


def gen_binary(rng: random.Random, p_flip: float = 0.05) -> list[Reading]:
    """Limit switch / relay: mostly stable, occasional legitimate transitions."""
    out: list[Reading] = []
    state = 0
    window: list[float] = []
    i = 0
    fault = None
    fault_end = -1
    while i < N_HEALTHY + 3 * FAULT_BLOCK:
        if i in (N_HEALTHY, N_HEALTHY + FAULT_BLOCK, N_HEALTHY + 2 * FAULT_BLOCK):
            fault = rng.choice(["chatter", "stuck_open", "invalid"])
            fault_end = i + FAULT_BLOCK
        good = not (i >= N_HEALTHY and i < fault_end)
        prev = state
        if good:
            if rng.random() < p_flip:
                state = 1 - state
            val = float(state)
        else:
            if fault == "chatter":
                state = 1 - state
                val = float(state)
            elif fault == "stuck_open":
                state = 0
                val = 0.0
            else:  # invalid
                val = rng.choice([0.3, 0.7, 2.0, -1.0])
        window.append(val)
        if len(window) > 20:
            window.pop(0)
        # measured flips/second over the rolling window (1s/sample assumed)
        n_flips = sum(1 for a, b in zip(window, window[1:]) if a != b)
        flip_rate = n_flips / max(len(window) - 1, 1)
        out.append(Reading(
            value=val,
            age_s=AGE_FRESH_S,
            n_samples=min(len(window), 10),
            sigma_current=flip_rate,  # reuse field: measured flips/sec
            cusum_alarm=False,
            flip=(val != prev),
            label=0 if good else 1,
            prev_value=prev,
        ))
        i += 1
    return out


# ── Scorers ──────────────────────────────────────────────────────────────────


def _ctx(archetype: str, skill: SkillContext) -> SkillContext:
    return skill


def _mk_skill(archetype: str, **kw) -> SkillContext:
    base = dict(
        skill_id="bench", skill_version="1.0.0", archetype=archetype,
        decay_lambda=Decimal("0.05"), T_max_s=3600, T_period_s=86400,
        T_hold_s=30, beta_cosine=Decimal("1.0"),
        n_ref=kw.get("n_ref", 14), n_min=1,
        sigma_ref=Decimal(str(kw.get("sigma_ref", 2.0))),
        k_stability=Decimal("0.64"), cycle_amplitude=Decimal("10.0"),
        value_min=Decimal("0"), value_max=Decimal("1e9"), unit="",
        sampling_hz=1, iso_standards=(), quarantine_thr=Decimal("0.6"),
        strict_mode=False, physics_laws=(), llm_prompt_pack=None,
    )
    return SkillContext(**{k: v for k, v in base.items()})


def veritas_score(r: Reading, arch, ctx: SkillContext) -> float:
    obs = ctx.with_observation(
        value=Decimal(str(round(r.value, 6))),
        previous_value=Decimal(str(round(r.prev_value, 6))),
        age_seconds=r.age_s,
        n_samples=r.n_samples,
        metadata={
            "sigma_current": r.sigma_current,
            "cusum_alarm": r.cusum_alarm,
            # flip_rate measured from the observed signal, never the label —
            # for BINARY r.sigma_current carries measured flips/sec.
            "flip_rate": r.sigma_current if arch.archetype_name == "BINARY" else None,
        },
    )
    tau = arch.compute_tau(obs)
    delta = arch.compute_delta(obs)
    alpha = arch.compute_alpha(obs)
    return float(compute_confidence(tau, delta, alpha))


def bl_staleness(r: Reading, half_life: float = 30.0) -> float:
    """Naive freshness baseline: score = exp(-ln2 * age/half_life)."""
    return math.exp(-math.log(2) * r.age_s / half_life)


def bl_rolling_var(r: Reading, sigma_ref: float = 2.0) -> float:
    """Rolling-variance baseline: 1 if sigma close to ref, decays with deviation."""
    if sigma_ref <= 0:
        return 1.0
    ratio = r.sigma_current / sigma_ref
    return math.exp(-abs(ratio - 1.0))


def bl_flatline_spc(r: Reading, sigma_ref: float = 2.0) -> float:
    """SPC-style: bad if stuck (low var), out of ±3σ of mean, or stale."""
    if r.n_samples <= 1:
        return 0.0                      # dropped / starved
    if r.sigma_current < 0.05 * sigma_ref and r.n_samples > 2:
        return 0.0                      # flatline / dead sensor
    if r.cusum_alarm:
        return 0.3
    return 1.0 - min(r.sigma_current / (3 * sigma_ref), 1.0)


# ── Metrics ──────────────────────────────────────────────────────────────────


def roc_auc(scores: list[float], labels: list[int]) -> float:
    """Rank-based AUC — probability a random faulty reading scores below a random good one."""
    good = [s for s, l in zip(scores, labels) if l == 0]
    bad = [s for s, l in zip(scores, labels) if l == 1]
    if not good or not bad:
        return 0.5
    wins = ties = 0
    for b in bad:
        for g in good:
            if b < g:
                wins += 1
            elif b == g:
                ties += 1
    return (wins + 0.5 * ties) / (len(good) * len(bad))


def precision_recall(scores: list[float], labels: list[int], thr: float) -> tuple[float, float, float]:
    """Flag readings with score < thr as faulty. Return (precision, recall, f1)."""
    tp = sum(1 for s, l in zip(scores, labels) if s < thr and l == 1)
    fp = sum(1 for s, l in zip(scores, labels) if s < thr and l == 0)
    fn = sum(1 for s, l in zip(scores, labels) if s >= thr and l == 1)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    rng = random.Random(SEED)
    datasets = {
        "INERTIAL":  (gen_inertial(rng), InertialArchetype(), _mk_skill("INERTIAL", sigma_ref=2.0, n_ref=14)),
        "CUMULATIVE":(gen_cumulative(rng), CumulativeArchetype(), _mk_skill("CUMULATIVE", sigma_ref=0.5, n_ref=10)),
        "BINARY":    (gen_binary(rng), BinaryArchetype(), _mk_skill("BINARY", n_ref=10)),
    }

    print("=" * 78)
    print("VERITAS C(v) FALSIFICATION BENCHMARK")
    print("Labeled fault injection -> per-reading ground truth -> AUC + P/R")
    print("=" * 78)

    grand_scores: dict[str, list[float]] = {}
    grand_labels: list[int] = []
    thr = 0.50  # quarantine flag threshold (score < thr -> 'faulty')

    for name, (readings, arch, ctx) in datasets.items():
        labels = [r.label for r in readings]
        v_scores = [veritas_score(r, arch, ctx) for r in readings]
        s_stale = [bl_staleness(r) for r in readings]
        s_var = [bl_rolling_var(r) for r in readings]
        s_spc = [bl_flatline_spc(r) for r in readings]

        rows = {
            "C(v) veritas": v_scores,
            "staleness":    s_stale,
            "rolling-var":  s_var,
            "flatline/SPC": s_spc,
        }
        for k, sc in rows.items():
            grand_scores.setdefault(k, []).extend(sc)
        grand_labels.extend(labels)

        n_bad = sum(labels)
        print(f"\n-- {name}  (n={len(readings)}, faulty={n_bad}) --")
        print(f"  {'scorer':<16} {'AUC':>6} {'P':>6} {'R':>6} {'F1':>6}")
        for k, sc in rows.items():
            a = roc_auc(sc, labels)
            p, r, f1 = precision_recall(sc, labels, thr)
            print(f"  {k:<16} {a:6.3f} {p:6.3f} {r:6.3f} {f1:6.3f}")

    # ── Verdict ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("OVERALL (all sensors pooled)")
    print("=" * 78)
    print(f"  {'scorer':<16} {'AUC':>6} {'P':>6} {'R':>6} {'F1':>6}")
    auc_map = {}
    for k, sc in grand_scores.items():
        a = roc_auc(sc, grand_labels)
        auc_map[k] = a
        p, r, f1 = precision_recall(sc, grand_labels, thr)
        print(f"  {k:<16} {a:6.3f} {p:6.3f} {r:6.3f} {f1:6.3f}")

    cv = auc_map["C(v) veritas"]
    spc = auc_map["flatline/SPC"]
    best_base = max(v for k, v in auc_map.items() if k != "C(v) veritas")
    print("\n" + "-" * 78)
    if cv < 0.70 or cv < spc:
        verdict = "KILL — C(v) does not beat the SPC-style bar (or AUC<0.70). The formula is not a metric; rescope to the audit/NCR layer."
    elif cv >= 0.85 and cv > best_base:
        verdict = "PASS — C(v) separates faults and beats every baseline. Proceed to calibration + productization."
    else:
        verdict = "WEAK — C(v) helps but doesn't clear the 0.85 bar vs baselines. Narrow the claim."
    print(f"VERDICT: {verdict}")
    print(f"  C(v) AUC={cv:.3f}  | best baseline AUC={best_base:.3f}  | SPC AUC={spc:.3f}")
    print("-" * 78)


if __name__ == "__main__":
    main()
