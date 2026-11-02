"""ISO standard threshold definitions and the action they trigger on failure."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ISOStandard:
    name: str
    minimum_confidence: Decimal   # C(v) must be >= this to pass
    action_on_failure: str        # maps to compliance_action_type enum


# Thresholds are intentionally conservative.
# Adjust per-site via SkillContext.iso_standards + quarantine_thr.
STANDARDS: dict[str, ISOStandard] = {
    "ISO9001": ISOStandard(
        name="ISO9001",
        minimum_confidence=Decimal("0.70"),
        action_on_failure="NCR",
    ),
    "AS9100D": ISOStandard(
        name="AS9100D",
        minimum_confidence=Decimal("0.75"),
        action_on_failure="BLOCK_RELEASE",
    ),
    "IATF16949": ISOStandard(
        name="IATF16949",
        minimum_confidence=Decimal("0.75"),
        action_on_failure="SPC_ALERT",
    ),
    "NADCAP": ISOStandard(
        name="NADCAP",
        minimum_confidence=Decimal("0.82"),
        action_on_failure="PROCESS_FREEZE",
    ),
}


def get_standard(name: str) -> ISOStandard | None:
    return STANDARDS.get(name.upper())
