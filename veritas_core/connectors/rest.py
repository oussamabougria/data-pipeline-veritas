"""REST connector — polls a JSON HTTP endpoint at a configured interval."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .base import BaseConnector, RawObservation

_vlog = logging.getLogger(__name__)


class RESTConnector(BaseConnector):
    """
    Polls a REST JSON endpoint and extracts a numeric value via a JSON path.

    Requires: httpx
    Config keys:
      url, sensor_id, site_id, unit,
      value_path (dot-notation, e.g. "data.measurements.0.value"),
      poll_interval_s (default 60),
      headers (dict, optional),
      auth_bearer (token, optional)
    """

    protocol = "REST"

    def __init__(self, config: dict) -> None:
        self._config = config
        self._session: Any = None
        self._last_observation: RawObservation | None = None

    async def connect(self) -> None:
        try:
            import httpx
        except ImportError:
            raise RuntimeError("httpx is required for RESTConnector: pip install httpx")

        cfg = self._config
        headers = dict(cfg.get("headers", {}))
        if cfg.get("auth_bearer"):
            headers["Authorization"] = f"Bearer {cfg['auth_bearer']}"
        self._session = httpx.AsyncClient(headers=headers, timeout=30.0)
        _vlog.info("REST connector configured for %s", cfg["url"])

    async def read_batch(self) -> list[RawObservation]:
        if not self._session:
            return []

        cfg = self._config
        try:
            response = await self._session.get(cfg["url"])
            response.raise_for_status()
            payload = response.json()

            # Navigate dot-notation path
            value_path = cfg.get("value_path", "value")
            node: Any = payload
            for key in value_path.split("."):
                if isinstance(node, list):
                    node = node[int(key)]
                else:
                    node = node[key]

            value = Decimal(str(float(node)))
            obs = RawObservation(
                sensor_id=cfg["sensor_id"],
                site_id=cfg["site_id"],
                ts=datetime.now(timezone.utc),
                value=value,
                unit=cfg.get("unit", ""),
                protocol=self.protocol,
                raw_payload=payload if isinstance(payload, dict) else {"raw": payload},
            )
            self._last_observation = obs
            return [obs]
        except Exception as exc:
            _vlog.warning("REST read error for %s: %s", cfg.get("url"), exc)
            return []

    async def close(self) -> None:
        if self._session:
            await self._session.aclose()
            self._session = None
