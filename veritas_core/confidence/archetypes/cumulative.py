"""
CUMULATIVE archetype — flow meters, energy meters, odometers, counters.

τ = max(0, 1 - T / T_max)
δ = min(1, n / n_min)
α = 1.0 if monotonic else 0.0   (hard fail on reversal)
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from .base import BaseArchetype, _q

if TYPE_CHECKING:
    from veritas_core.skills.base import SkillContext

_ZERO = Decimal("0")
_ONE = Decimal("1")


class CumulativeArchetype(BaseArchetype):
    archetype_name = "CUMULATIVE"

    def compute_tau(self, ctx: "SkillContext") -> Decimal:
        T = ctx.age_seconds
        T_max = float(ctx.T_max_s)
        if T_max <= 0:
            return _ZERO
        tau = max(0.0, 1.0 - T / T_max)
        return _q(Decimal(str(tau)))

    def compute_delta(self, ctx: "SkillContext") -> Decimal:
        n = ctx.n_samples
        n_min = ctx.n_min if ctx.n_min > 0 else 1
        delta = min(1.0, n / n_min)
        return _q(Decimal(str(delta)))

    def compute_alpha(self, ctx: "SkillContext") -> Decimal:
        # Hard fail on reversal — any decrease in a cumulative signal is invalid
        if ctx.value < ctx.previous_value:
            return _ZERO
        return _ONE
