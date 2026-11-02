"""OPC-UA connector — reads node values from an OPC-UA server."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .base import BaseConnector, RawObservation

_vlog = logging.getLogger(__name__)


class OPCUAConnector(BaseConnector):
    """
    Connects to an OPC-UA server and reads configured node values.

    Requires: asyncua >= 1.0
    Config keys: url, sensor_id, site_id, unit, node_id, username, password
    """

    protocol = "OPCUA"

    def __init__(self, config: dict) -> None:
        self._config = config
        self._client: Any = None

    async def connect(self) -> None:
        try:
            from asyncua import Client
        except ImportError:
            raise RuntimeError("asyncua is required for OPCUAConnector: pip install asyncua")

        cfg = self._config
        self._client = Client(url=cfg["url"])
        if cfg.get("username"):
            self._client.set_user(cfg["username"])
            self._client.set_password(cfg.get("password", ""))
        await self._client.connect()
        _vlog.info("OPC-UA connected to %s", cfg["url"])

    async def read_batch(self) -> list[RawObservation]:
        if not self._client:
            return []

        cfg = self._config
        try:
            node = self._client.get_node(cfg["node_id"])
            dv = await node.read_data_value()
            value = Decimal(str(float(dv.Value.Value)))
            ts = dv.SourceTimestamp or datetime.now(timezone.utc)
            return [
                RawObservation(
                    sensor_id=cfg["sensor_id"],
                    site_id=cfg["site_id"],
                    ts=ts,
                    value=value,
                    unit=cfg.get("unit", ""),
                    protocol=self.protocol,
                    raw_payload={"node_id": cfg["node_id"], "status_code": str(dv.StatusCode)},
                )
            ]
        except Exception as exc:
            _vlog.warning("OPC-UA read error for node %s: %s", cfg.get("node_id"), exc)
            return []

    async def close(self) -> None:
        if self._client:
            await self._client.disconnect()
            self._client = None
