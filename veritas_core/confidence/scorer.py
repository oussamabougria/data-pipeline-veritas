"""
ConfidenceScorer — orchestrates archetype detection + formula execution.

Usage:
    scorer = ConfidenceScorer()
    result = scorer.score(ctx)
    # result.tau, result.delta, result.alpha, result.confidence — all Decimal
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from .archetypes import ARCHETYPE_MAP
from .formula import compute_confidence

if TYPE_CHECKING:
    from veritas_core.skills.base import SkillContext


@dataclass(frozen=True)
class ScoreResult:
    tau: Decimal
    delta: Decimal
    alpha: Decimal
    confidence: Decimal
    archetype: str


class ConfidenceScorer:
    def score(self, ctx: "SkillContext") -> ScoreResult:
        archetype_key = ctx.archetype.upper()
        impl = ARCHETYPE_MAP.get(archetype_key)
        if impl is None:
            raise ValueError(f"Unknown archetype: {archetype_key!r}")

        # Python skill can override any of the three factors
        if ctx._skill_instance is not None:
            skill = ctx._skill_instance
            tau = skill.compute_tau(ctx) if hasattr(skill, "compute_tau") else impl.compute_tau(ctx)
            delta = skill.compute_delta(ctx) if hasattr(skill, "compute_delta") else impl.compute_delta(ctx)
            alpha = skill.compute_alpha(ctx) if hasattr(skill, "compute_alpha") else impl.compute_alpha(ctx)
        else:
            tau, delta, alpha = impl.score(ctx)

        confidence = compute_confidence(tau, delta, alpha)

        return ScoreResult(
            tau=tau,
            delta=delta,
            alpha=alpha,
            confidence=confidence,
            archetype=archetype_key,
        )
