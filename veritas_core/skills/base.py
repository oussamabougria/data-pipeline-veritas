"""
BaseSkill and SkillContext — the contract between skill and engine.

The C(v) engine never imports a skill directly.
It only sees SkillContext, a frozen dataclass produced by SkillLoader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class SkillContext:
    # --- Identity ---
    skill_id: str
    skill_version: str
    archetype: str           # INERTIAL / CUMULATIVE / CYCLICAL / BINARY

    # --- τ parameters ---
    decay_lambda: Decimal    # INERTIAL
    T_max_s: int             # CUMULATIVE
    T_period_s: int          # CYCLICAL
    T_hold_s: int            # BINARY
    beta_cosine: Decimal     # CYCLICAL exponent

    # --- δ parameters ---
    n_ref: int
    n_min: int

    # --- α parameters ---
    sigma_ref: Decimal
    k_stability: Decimal
    cycle_amplitude: Decimal

    # --- Sensor characteristics ---
    value_min: Decimal
    value_max: Decimal
    unit: str
    sampling_hz: int | None

    # --- Process context ---
    iso_standards: tuple[str, ...]
    quarantine_thr: Decimal
    strict_mode: bool

    # --- LLM physics context ---
    physics_laws: tuple[str, ...]
    llm_prompt_pack: str | None

    # --- Runtime fields (set per observation, not stored in skill) ---
    n_samples: int = 0
    value: Decimal = field(default_factory=lambda: Decimal("0"))
    previous_value: Decimal = field(default_factory=lambda: Decimal("0"))
    age_seconds: float = 0.0
    metadata: dict = field(default_factory=dict)

    # --- Python skill override hooks (None for YAML skills) ---
    _skill_instance: object | None = field(default=None, compare=False, hash=False)

    def with_observation(
        self,
        *,
        value: Decimal,
        previous_value: Decimal,
        age_seconds: float,
        n_samples: int,
        metadata: dict | None = None,
    ) -> "SkillContext":
        """Return a new SkillContext with runtime fields populated."""
        return SkillContext(
            **{
                f.name: getattr(self, f.name)
                for f in self.__dataclass_fields__.values()
                if f.name not in ("value", "previous_value", "age_seconds", "n_samples", "metadata")
            },
            value=value,
            previous_value=previous_value,
            age_seconds=age_seconds,
            n_samples=n_samples,
            metadata=metadata or {},
        )


class BaseSkill:
    """
    Base class for Python-format skills.
    Override compute_tau / compute_delta / compute_alpha as needed.
    All override methods receive a SkillContext and must return Decimal.
    """

    skill_id: str = ""
    version: str = "1.0.0"
    archetype: str = ""
    tags: list[str] = []

    # Physical defaults — overridden by calibration when available
    decay_lambda: Decimal = Decimal("0.05")
    T_max_s: int = 3600
    T_period_s: int = 86400
    T_hold_s: int = 30
    beta_cosine: Decimal = Decimal("1.0")
    n_ref: int = 20
    n_min: int = 1
    sigma_ref: Decimal = Decimal("1.0")
    k_stability: Decimal = Decimal("1.0")
    cycle_amplitude: Decimal = Decimal("10.0")
    value_min: Decimal = Decimal("0")
    value_max: Decimal = Decimal("9999999")
    unit: str = ""
    sampling_hz: int | None = None
    iso_standards: tuple[str, ...] = ("ISO9001",)
    quarantine_threshold: Decimal = Decimal("0.60")
    strict_mode: bool = False
    physics_laws: tuple[str, ...] = ()
    llm_prompt_pack: str | None = None

    def to_skill_context(self) -> SkillContext:
        return SkillContext(
            skill_id=self.skill_id,
            skill_version=self.version,
            archetype=self.archetype,
            decay_lambda=Decimal(str(self.decay_lambda)),
            T_max_s=self.T_max_s,
            T_period_s=self.T_period_s,
            T_hold_s=self.T_hold_s,
            beta_cosine=Decimal(str(self.beta_cosine)),
            n_ref=self.n_ref,
            n_min=self.n_min,
            sigma_ref=Decimal(str(self.sigma_ref)),
            k_stability=Decimal(str(self.k_stability)),
            cycle_amplitude=Decimal(str(self.cycle_amplitude)),
            value_min=Decimal(str(self.value_min)),
            value_max=Decimal(str(self.value_max)),
            unit=self.unit,
            sampling_hz=self.sampling_hz,
            iso_standards=tuple(self.iso_standards),
            quarantine_thr=Decimal(str(self.quarantine_threshold)),
            strict_mode=self.strict_mode,
            physics_laws=tuple(self.physics_laws),
            llm_prompt_pack=self.llm_prompt_pack,
            _skill_instance=self,
        )
