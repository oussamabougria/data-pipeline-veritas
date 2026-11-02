"""
INERTIAL archetype — accelerometers, gyroscopes, IMUs, vibration sensors.

τ = exp(-lambda × T)
δ = sigmoid((n - n_mid) / s)   centered: δ(0)→0, δ(n_ref)→1
α = 0.15                                    if CUSUM alarm (sustained drift)
α = 0.20                                    if sigma_current < 0.10 × sigma_ref (dead sensor)
α = exp(-k × (sigma_current - sigma_ref)²)  otherwise
"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING

from .base import BaseArchetype, _q

if TYPE_CHECKING:
    from veritas_core.skills.base import SkillContext

_ZERO = Decimal("0")
_ONE = Decimal("1")


class InertialArchetype(BaseArchetype):
    archetype_name = "INERTIAL"

    def compute_tau(self, ctx: "SkillContext") -> Decimal:
        lam = float(ctx.decay_lambda)
        T = ctx.age_seconds
        tau = math.exp(-lam * T)
        return _q(Decimal(str(tau)))

    def compute_delta(self, ctx: "SkillContext") -> Decimal:
        n = ctx.n_samples
        n_ref = ctx.n_ref
        if n <= 0 or n_ref <= 0:
            return _ZERO
        # Centered sigmoid — δ(0)→0, δ(n_ref)→1, transition around n_ref/2.
        # (Previous form sigmoid(n/n_ref) floored at 0.5 even with no data —
        #  a density that never collapses breaks the fail-closed guarantee.)
        n_mid = n_ref / 2.0
        s = max(n_ref / 8.0, 1e-9)   # slope: ~95% of range covered by [0, n_ref]
        sig = 1.0 / (1.0 + math.exp(-(n - n_mid) / s))
        return _q(Decimal(str(sig)))

    def compute_alpha(self, ctx: "SkillContext") -> Decimal:
        sigma_ref = float(ctx.sigma_ref)
        sigma_current = float(ctx.metadata.get("sigma_current", ctx.sigma_ref))
        k = float(ctx.k_stability)
        if ctx.metadata.get("cusum_alarm", False):
            return Decimal("0.15")          # sustained drift — most severe
        if sigma_current < 0.10 * sigma_ref:
            return Decimal("0.20")          # flat-line / dead sensor
        diff = sigma_current - sigma_ref
        alpha = math.exp(-k * diff * diff)
        return _q(Decimal(str(alpha)))
