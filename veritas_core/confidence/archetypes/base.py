"""Base class for all sensor archetype scorers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from veritas_core.skills.base import SkillContext

_PLACES = Decimal("0.0001")
_ZERO = Decimal("0")
_ONE = Decimal("1")


def _q(value: Decimal) -> Decimal:
    """Quantize to 4 decimal places."""
    v = value.quantize(_PLACES, rounding=ROUND_HALF_UP)
    if v < _ZERO:
        return _ZERO
    if v > _ONE:
        return _ONE
    return v


class BaseArchetype(ABC):
    archetype_name: str = ""

    @abstractmethod
    def compute_tau(self, ctx: "SkillContext") -> Decimal:
        """Temporal decay factor τ(T)."""

    @abstractmethod
    def compute_delta(self, ctx: "SkillContext") -> Decimal:
        """Sample density factor δ(n)."""

    @abstractmethod
    def compute_alpha(self, ctx: "SkillContext") -> Decimal:
        """Signal stability factor α(σ)."""

    def score(self, ctx: "SkillContext") -> tuple[Decimal, Decimal, Decimal]:
        """
        Return (τ, δ, α) — each quantized to 4 decimal places.
        The caller (scorer.py) computes C(v) = τ × δ × α.
        """
        return _q(self.compute_tau(ctx)), _q(self.compute_delta(ctx)), _q(self.compute_alpha(ctx))
