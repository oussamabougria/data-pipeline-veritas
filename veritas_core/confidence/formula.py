"""
C(v) = τ(T) × δ(n) × α(σ)

This file is append-only. Never modify existing logic.
If different behaviour is needed, add a new archetype.
"""

from decimal import Decimal, ROUND_HALF_UP

_ZERO = Decimal("0")
_ONE = Decimal("1")
_PLACES = Decimal("0.0001")  # 4 decimal places


def _clamp(value: Decimal) -> Decimal:
    """Clamp to [0, 1]. C(v) < 0 or > 1 is a bug; this is the safety net."""
    if value < _ZERO:
        return _ZERO
    if value > _ONE:
        return _ONE
    return value


def compute_confidence(
    tau: Decimal,
    delta: Decimal,
    alpha: Decimal,
    tau_rhythm: Decimal | None = None,
) -> Decimal:
    """
    Compute final confidence score.

    Canonical form (v2):   C(v) = τ_age × τ_rhythm × δ × α
    Legacy 3-factor form:  C(v) = τ × δ × α  (when tau_rhythm is None)

    - τ_age      temporal freshness — how stale the reading is (per-archetype)
    - τ_rhythm   cadence regularity — inter-arrival timing vs expected (archetype-agnostic)
    - δ          sample density — how much data backs the reading (per-archetype)
    - α          signal stability — volatility / drift / dead-sensor (per-archetype)

    Multiplicative: any collapsed factor drives C(v) → 0. Fail-closed.

    All inputs must be Decimal in [0, 1].
    Returns Decimal rounded to 4 decimal places, clamped to [0, 1].
    """
    factors = (tau, delta, alpha) if tau_rhythm is None else (tau, tau_rhythm, delta, alpha)
    if not all(isinstance(x, Decimal) for x in factors):
        raise TypeError("τ, δ, α (and τ_rhythm if given) must all be Decimal — never float")

    raw = _ONE
    for f in factors:
        raw *= f
    result = _clamp(raw).quantize(_PLACES, rounding=ROUND_HALF_UP)
    return result
