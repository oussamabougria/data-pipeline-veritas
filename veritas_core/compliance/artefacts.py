"""
Compliance artefact generators.

Each artefact type corresponds to a compliance_action_type in the DB.
Artefacts are data structures; PDF rendering is a separate step (MinIO storage).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .engine import ComplianceResult


@dataclass
class ComplianceArtefact:
    artefact_type: str
    reference_no: str
    standard: str
    description: str
    payload: dict


def _ref_number(prefix: str) -> str:
    """Generate a reference number like NCR-20240101-<short-hash>."""
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    uid = hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:6].upper()
    return f"{prefix}-{date_str}-{uid}"


_GENERATORS = {}


def _register(action_type: str):
    def decorator(fn):
        _GENERATORS[action_type] = fn
        return fn
    return decorator


@_register("NCR")
def _ncr(result: ComplianceResult, **kwargs) -> ComplianceArtefact:
    return ComplianceArtefact(
        artefact_type="NCR",
        reference_no=_ref_number("NCR"),
        standard=result.standard,
        description=(
            f"Non-Conformance Report: C(v)={result.confidence} is below "
            f"ISO9001 minimum {result.threshold} for sensor "
            f"{result.action_payload.get('sensor_id', 'unknown')}."
        ),
        payload=result.action_payload,
    )


@_register("BLOCK_RELEASE")
def _block_release(result: ComplianceResult, **kwargs) -> ComplianceArtefact:
    return ComplianceArtefact(
        artefact_type="BLOCK_RELEASE",
        reference_no=_ref_number("BLK"),
        standard=result.standard,
        description=(
            f"Release BLOCKED: C(v)={result.confidence} is below "
            f"AS9100D minimum {result.threshold}. "
            f"Sensor: {result.action_payload.get('sensor_id', 'unknown')}."
        ),
        payload=result.action_payload,
    )


@_register("PROCESS_FREEZE")
def _process_freeze(result: ComplianceResult, **kwargs) -> ComplianceArtefact:
    return ComplianceArtefact(
        artefact_type="PROCESS_FREEZE",
        reference_no=_ref_number("PF"),
        standard=result.standard,
        description=(
            f"PROCESS FREEZE triggered: C(v)={result.confidence} is below "
            f"NADCAP minimum {result.threshold}. "
            f"All production on sensor "
            f"{result.action_payload.get('sensor_id', 'unknown')} must halt."
        ),
        payload=result.action_payload,
    )


@_register("SPC_ALERT")
def _spc_alert(result: ComplianceResult, **kwargs) -> ComplianceArtefact:
    return ComplianceArtefact(
        artefact_type="SPC_ALERT",
        reference_no=_ref_number("SPC"),
        standard=result.standard,
        description=(
            f"SPC Alert: C(v)={result.confidence} trending below "
            f"IATF16949 threshold {result.threshold}."
        ),
        payload=result.action_payload,
    )


def generate_artefacts(results: list[ComplianceResult]) -> list[ComplianceArtefact]:
    """Generate artefacts for all failing compliance results."""
    artefacts = []
    for result in results:
        if not result.passed and result.action_type:
            generator = _GENERATORS.get(result.action_type)
            if generator:
                artefacts.append(generator(result))
    return artefacts
