"""
BINARY archetype — limit switches, door sensors, alarms, relays.

τ = 1.0 if T < T_hold else 0.0   (step function)
δ = 1.0 if n >= 1 else 0.0
α = stability vs chatter         (legit transitions OK; penalise oscillation)
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from .base import BaseArchetype

if TYPE_CHECKING:
    from veritas_core.skills.base import SkillContext

_ZERO = Decimal("0")
_ONE = Decimal("1")


class BinaryArchetype(BaseArchetype):
    archetype_name = "BINARY"

    def compute_tau(self, ctx: "SkillContext") -> Decimal:
        T = ctx.age_seconds
        T_hold = float(ctx.T_hold_s)
        return _ONE if T < T_hold else _ZERO

    def compute_delta(self, ctx: "SkillContext") -> Decimal:
        return _ONE if ctx.n_samples >= 1 else _ZERO

    def compute_alpha(self, ctx: "SkillContext") -> Decimal:
        # A binary sensor's job is to change state — a single legitimate
        # transition (0→1) is NOT instability. The real defect is *chatter*:
        # rapid repeated flips inside the expected hold window.
        # metadata["flip_rate"] = observed flips per second (rolling), if the
        # connector supplies it; otherwise fall back to value==previous as a
        # weak stability proxy that does NOT zero a single transition.
        flip_rate = ctx.metadata.get("flip_rate")
        if flip_rate is not None:
            hold_s = float(ctx.T_hold_s) if ctx.T_hold_s > 0 else 30.0
            # expected max flips per second ~ 1/hold_s; excess is chatter
            expected = 1.0 / hold_s
            rate = float(flip_rate)
            if rate <= expected:
                return _ONE
            # decay α with the chatter ratio (rate / expected)
            import math
            return _q(Decimal(str(math.exp(-(rate / expected - 1.0)))))

        current = float(ctx.value)
        previous = float(ctx.previous_value)
        if current == previous:
            return _ONE
        # Single transition: plausible legitimate event — mild discount, not 0.
        return Decimal("0.8000")
