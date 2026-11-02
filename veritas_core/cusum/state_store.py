"""
CusumStateStore — Redis-first, DB-fallback, replay on cold start.

Redis key  : veritas:cusum:{sensor_id}:{site_id}  (hash: s_high, s_low)
DB table   : veritas.sensor_cusum_state
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

_vlog = logging.getLogger(__name__)


def _log_db_error(task: asyncio.Task) -> None:
    exc = task.exception()
    if exc:
        _vlog.error("CUSUM DB write failed: %s", exc)

import asyncpg

from .engine import is_alarm, replay

if TYPE_CHECKING:
    import redis.asyncio as aioredis


@dataclass
class CusumState:
    s_high: float
    s_low: float
    mu_ref: float
    k_cusum: float
    h_threshold: float

    def alarm(self) -> bool:
        return is_alarm(self.s_high, self.s_low, self.h_threshold)


def _redis_key(sensor_id: str, site_id: str) -> str:
    return f"veritas:cusum:{sensor_id}:{site_id}"


class CusumStateStore:
    """
    History fetcher signature (for cold-start replay):
        async (sensor_id, site_id) -> (observations: list[float], mu_ref: float, k_cusum: float)
    """

    def __init__(
        self,
        redis_client: Any,
        db_pool: asyncpg.Pool,
        history_fetcher: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._redis = redis_client
        self._pool = db_pool
        self._history_fetcher = history_fetcher

    async def get_state(self, sensor_id: str, site_id: str) -> Optional[CusumState]:
        # 1. Redis
        state = await self._from_redis(sensor_id, site_id)
        if state is not None:
            return state

        # 2. DB
        state = await self._from_db(sensor_id, site_id)
        if state is not None:
            await self._write_redis(
                sensor_id, site_id,
                state.s_high, state.s_low,
                state.mu_ref, state.k_cusum, state.h_threshold,
            )
            return state

        # 3. Cold-start replay
        if self._history_fetcher is not None:
            observations, mu_ref, k_cusum = await self._history_fetcher(sensor_id, site_id)
            s_high, s_low = replay(observations, mu_ref, k_cusum)
            state = CusumState(
                s_high=s_high,
                s_low=s_low,
                mu_ref=mu_ref,
                k_cusum=k_cusum,
                h_threshold=5.0,
            )
            await self._write_redis(
                sensor_id, site_id,
                state.s_high, state.s_low,
                state.mu_ref, state.k_cusum, state.h_threshold,
            )
            return state

        return None

    async def set_state(
        self,
        sensor_id: str,
        site_id: str,
        s_high: float,
        s_low: float,
        mu_ref: float,
        k_cusum: float,
        h_threshold: float,
    ) -> None:
        await self._write_redis(sensor_id, site_id, s_high, s_low, mu_ref, k_cusum, h_threshold)
        task = asyncio.create_task(self._write_db(sensor_id, site_id, s_high, s_low, mu_ref, k_cusum, h_threshold))
        task.add_done_callback(_log_db_error)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _from_redis(self, sensor_id: str, site_id: str) -> CusumState | None:
        raw = await self._redis.hgetall(_redis_key(sensor_id, site_id))
        if not raw:
            return None
        return CusumState(
            s_high=float(raw[b"s_high"]),
            s_low=float(raw[b"s_low"]),
            mu_ref=float(raw[b"mu_ref"]),
            k_cusum=float(raw[b"k_cusum"]),
            h_threshold=float(raw[b"h_threshold"]),
        )

    async def _from_db(self, sensor_id: str, site_id: str) -> CusumState | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT s_high, s_low, mu_ref, k_cusum, h_threshold
                FROM veritas.sensor_cusum_state
                WHERE sensor_id = $1 AND site_id = $2
                """,
                sensor_id,
                site_id,
            )
        if row is None:
            return None
        return CusumState(
            s_high=float(row["s_high"]),
            s_low=float(row["s_low"]),
            mu_ref=float(row["mu_ref"]),
            k_cusum=float(row["k_cusum"]),
            h_threshold=float(row["h_threshold"]),
        )

    async def _write_redis(
        self,
        sensor_id: str,
        site_id: str,
        s_high: float,
        s_low: float,
        mu_ref: float,
        k_cusum: float,
        h_threshold: float,
    ) -> None:
        key = _redis_key(sensor_id, site_id)
        await self._redis.hset(
            key,
            mapping={
                "s_high": s_high,
                "s_low": s_low,
                "mu_ref": mu_ref,
                "k_cusum": k_cusum,
                "h_threshold": h_threshold,
            },
        )

    async def _write_db(
        self,
        sensor_id: str,
        site_id: str,
        s_high: float,
        s_low: float,
        mu_ref: float,
        k_cusum: float,
        h_threshold: float,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO veritas.sensor_cusum_state
                    (sensor_id, site_id, s_high, s_low, mu_ref, k_cusum, h_threshold, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                ON CONFLICT (sensor_id, site_id) DO UPDATE
                    SET s_high      = EXCLUDED.s_high,
                        s_low       = EXCLUDED.s_low,
                        mu_ref      = EXCLUDED.mu_ref,
                        k_cusum     = EXCLUDED.k_cusum,
                        h_threshold = EXCLUDED.h_threshold,
                        updated_at  = NOW()
                """,
                sensor_id, site_id, s_high, s_low, mu_ref, k_cusum, h_threshold,
            )
