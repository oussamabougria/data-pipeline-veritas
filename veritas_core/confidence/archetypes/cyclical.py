"""
CYCLICAL archetype — temperature, pressure, humidity, RPM.

τ = |cos(π × T / T_period)|^β
δ = harmonic_mean(recent_n)
α = 1 - (sigma / cycle_amplitude)
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import TYPE_CHECKING

from .base import BaseArchetype, _q

if TYPE_CHECKING:
    from veritas_core.skills.base import SkillContext

_ZERO = Decimal("0")


class CyclicalArchetype(BaseArchetype):
    archetype_name = "CYCLICAL"

    def compute_tau(self, ctx: "SkillContext") -> Decimal:
        T = ctx.age_seconds
        T_period = float(ctx.T_period_s)
        beta = float(ctx.beta_cosine)
        if T_period <= 0:
            return _ZERO
        cos_val = abs(math.cos(math.pi * T / T_period))
        tau = pow(cos_val, beta)
        return _q(Decimal(str(tau)))

    def compute_delta(self, ctx: "SkillContext") -> Decimal:
        n = ctx.n_samples
        n_ref = ctx.n_ref if ctx.n_ref > 0 else 1
        # harmonic_mean of [n, n_ref]; simple two-element form
        if n <= 0:
            return _ZERO
        harmonic = 2.0 / (1.0 / n + 1.0 / n_ref)
        # normalise against n_ref so δ ∈ [0, 1]
        delta = min(1.0, harmonic / n_ref)
        return _q(Decimal(str(delta)))

    def compute_alpha(self, ctx: "SkillContext") -> Decimal:
        sigma = float(ctx.sigma_ref)
        amplitude = float(ctx.cycle_amplitude)
        if amplitude <= 0:
            return _ZERO
        alpha = max(0.0, 1.0 - sigma / amplitude)
        return _q(Decimal(str(alpha)))
