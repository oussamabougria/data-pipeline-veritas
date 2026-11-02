"""
SkillLoader — resolves the right SkillContext for a given sensor.

Resolution order (highest priority first):
  1. Sensor-specific: skill explicitly assigned to this sensor_id
  2. Tag-matched: skill whose tags overlap with sensor tags
  3. Archetype default: veritas-{archetype.lower()}-default-v1

If strict_mode=True on the site and no skill is found, raises SkillResolutionError.
Results are cached in-memory (skill_id:version key) to avoid repeated DB hits.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
import textwrap
from decimal import Decimal
from typing import Any

import yaml

from .base import BaseSkill, SkillContext

_DEFAULT_SKILL_IDS = {
    "INERTIAL": "veritas-inertial-default-v1",
    "CUMULATIVE": "veritas-cumulative-default-v1",
    "CYCLICAL": "veritas-cyclical-default-v1",
    "BINARY": "veritas-binary-default-v1",
}


class SkillResolutionError(Exception):
    pass


def _yaml_to_context(data: dict, skill_id: str, version: str) -> SkillContext:
    """Parse a YAML skill dict into a SkillContext."""
    phys = data.get("physical", {})
    sensor = data.get("sensor", {})
    proc = data.get("process", {})
    return SkillContext(
        skill_id=skill_id,
        skill_version=version,
        archetype=data.get("matches", {}).get("archetype", "INERTIAL"),
        decay_lambda=Decimal(str(phys.get("decay_lambda", "0.05"))),
        T_max_s=int(phys.get("T_max_s", 3600)),
        T_period_s=int(phys.get("T_period_s", 86400)),
        T_hold_s=int(phys.get("T_hold_s", 30)),
        beta_cosine=Decimal(str(phys.get("beta_cosine", "1.0"))),
        n_ref=int(phys.get("n_ref", 20)),
        n_min=int(phys.get("n_min", 1)),
        sigma_ref=Decimal(str(phys.get("sigma_ref", "1.0"))),
        k_stability=Decimal(str(phys.get("k_stability", "1.0"))),
        cycle_amplitude=Decimal(str(phys.get("cycle_amplitude", "10.0"))),
        value_min=Decimal(str(sensor.get("value_min", "0"))),
        value_max=Decimal(str(sensor.get("value_max", "9999999"))),
        unit=str(sensor.get("unit", "")),
        sampling_hz=sensor.get("sampling_hz"),
        iso_standards=tuple(proc.get("iso_standards", ["ISO9001"])),
        quarantine_thr=Decimal(str(proc.get("quarantine_threshold", "0.60"))),
        strict_mode=bool(proc.get("strict_mode", False)),
        physics_laws=tuple(data.get("physics_laws", [])),
        llm_prompt_pack=data.get("llm_prompt_pack"),
        _skill_instance=None,
    )


def _python_to_context(content: str, skill_id: str, version: str) -> SkillContext:
    """Dynamically load a Python skill and call to_skill_context()."""
    mod_name = f"_veritas_skill_{hashlib.sha256(content.encode()).hexdigest()[:16]}"
    spec = importlib.util.spec_from_loader(mod_name, loader=None)
    module = type(sys)("_veritas_skill_dyn")
    exec(compile(content, f"<skill:{skill_id}>", "exec"), module.__dict__)  # noqa: S102
    # Find the BaseSkill subclass
    for obj in module.__dict__.values():
        if (
            isinstance(obj, type)
            and issubclass(obj, BaseSkill)
            and obj is not BaseSkill
        ):
            instance = obj()
            return instance.to_skill_context()
    raise SkillResolutionError(f"No BaseSkill subclass found in Python skill {skill_id!r}")


class SkillLoader:
    """
    Loads and caches SkillContext objects.

    Pass a `db_fetcher` callable with signature:
        fetch(sensor_id, archetype, tags) -> list[dict]
    where each dict has keys: skill_id, version, format, content, tags, active
    """

    def __init__(self, db_fetcher: Any) -> None:
        self._db_fetcher = db_fetcher
        self._cache: dict[str, SkillContext] = {}

    def _cache_key(self, skill_id: str, version: str) -> str:
        return f"{skill_id}:{version}"

    def _build_context(self, row: dict) -> SkillContext:
        key = self._cache_key(row["skill_id"], row["version"])
        if key in self._cache:
            return self._cache[key]

        fmt = row["format"]
        content = row["content"]
        skill_id = row["skill_id"]
        version = row["version"]

        if fmt == "yaml":
            data = yaml.safe_load(content)
            ctx = _yaml_to_context(data, skill_id, version)
        elif fmt == "python":
            ctx = _python_to_context(content, skill_id, version)
        else:
            raise SkillResolutionError(f"Unknown skill format: {fmt!r}")

        self._cache[key] = ctx
        return ctx

    def resolve(
        self,
        sensor_id: str,
        archetype: str,
        tags: list[str],
        strict_mode: bool = False,
    ) -> SkillContext:
        """
        Resolve the best skill for a sensor.
        Resolution order: sensor-specific → tag-matched → archetype default.
        """
        rows = self._db_fetcher(sensor_id=sensor_id, archetype=archetype, tags=tags)

        # 1. Sensor-specific (row has sensor_id match)
        sensor_rows = [r for r in rows if r.get("sensor_id") == sensor_id and r.get("active")]
        if sensor_rows:
            return self._build_context(sensor_rows[0])

        # 2. Tag-matched
        sensor_tag_set = set(tags)
        tag_rows = [
            r for r in rows
            if set(r.get("tags") or []) & sensor_tag_set and r.get("active")
        ]
        if tag_rows:
            return self._build_context(tag_rows[0])

        # 3. Archetype default
        default_id = _DEFAULT_SKILL_IDS.get(archetype.upper())
        default_rows = [
            r for r in rows
            if r.get("skill_id") == default_id and r.get("active")
        ]
        if default_rows:
            return self._build_context(default_rows[0])

        # Nothing found
        if strict_mode:
            raise SkillResolutionError(
                f"No skill found for sensor {sensor_id!r} archetype {archetype!r} "
                f"(strict_mode=True — no silent fallback)"
            )
        raise SkillResolutionError(
            f"No skill found for sensor {sensor_id!r} archetype {archetype!r}"
        )

    def invalidate(self, skill_id: str, version: str) -> None:
        """Remove a skill from the cache (call after a new version is activated)."""
        self._cache.pop(self._cache_key(skill_id, version), None)
