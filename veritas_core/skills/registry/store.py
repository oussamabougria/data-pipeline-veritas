"""
SkillStore — CRUD + version management for skill_registry table.

All writes go through here. Activation is a two-step process:
  1. publish() — stores the skill (validated=False, active=False)
  2. activate() — marks it active (deactivates prior version for same skill_id)
"""

from __future__ import annotations

import hashlib
from typing import Any

import asyncpg


class SkillStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def publish(
        self,
        skill_id: str,
        version: str,
        author: str,
        archetype: str,
        tags: list[str],
        fmt: str,
        content: str,
        source: str,
        iso_standards: list[str],
    ) -> dict:
        checksum = hashlib.sha256(content.encode()).hexdigest()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO skill_registry
                    (skill_id, version, author, archetype, tags, format, content,
                     source, iso_standards, checksum_sha256, validated, active)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,FALSE,FALSE)
                ON CONFLICT (skill_id, version) DO NOTHING
                RETURNING *
                """,
                skill_id, version, author, archetype, tags, fmt, content,
                source, iso_standards, checksum,
            )
        return dict(row) if row else {}

    async def activate(self, skill_id: str, version: str) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Deactivate all prior versions
                await conn.execute(
                    "UPDATE skill_registry SET active=FALSE WHERE skill_id=$1",
                    skill_id,
                )
                # Activate requested version
                await conn.execute(
                    "UPDATE skill_registry SET active=TRUE, validated=TRUE "
                    "WHERE skill_id=$1 AND version=$2",
                    skill_id, version,
                )

    async def get(self, skill_id: str, version: str | None = None) -> dict | None:
        async with self._pool.acquire() as conn:
            if version:
                row = await conn.fetchrow(
                    "SELECT * FROM skill_registry WHERE skill_id=$1 AND version=$2",
                    skill_id, version,
                )
            else:
                row = await conn.fetchrow(
                    "SELECT * FROM skill_registry WHERE skill_id=$1 AND active=TRUE",
                    skill_id,
                )
        return dict(row) if row else None

    async def list_for_loader(
        self, sensor_id: str, archetype: str, tags: list[str]
    ) -> list[dict]:
        """Return all candidate skill rows for SkillLoader resolution."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT sr.*, ssa.sensor_id AS assigned_sensor_id
                FROM skill_registry sr
                LEFT JOIN sensor_skill_assignments ssa
                    ON ssa.skill_id = sr.skill_id
                    AND ssa.skill_version = sr.version
                    AND ssa.sensor_id = $1
                    AND ssa.revoked_at IS NULL
                WHERE sr.archetype = $2
                  AND (
                      sr.active = TRUE
                      OR ssa.sensor_id IS NOT NULL
                  )
                ORDER BY ssa.sensor_id NULLS LAST, sr.created_at DESC
                """,
                sensor_id, archetype,
            )
        return [dict(r) for r in rows]

    async def list_all(self, archetype: str | None = None) -> list[dict]:
        async with self._pool.acquire() as conn:
            if archetype:
                rows = await conn.fetch(
                    "SELECT * FROM skill_registry WHERE archetype=$1 ORDER BY created_at DESC",
                    archetype,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM skill_registry ORDER BY created_at DESC"
                )
        return [dict(r) for r in rows]
