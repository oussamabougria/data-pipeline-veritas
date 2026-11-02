"""Modbus TCP connector — reads holding registers or input registers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .base import BaseConnector, RawObservation

_vlog = logging.getLogger(__name__)


class ModbusConnector(BaseConnector):
    """
    Reads Modbus TCP holding registers.

    Requires: pymodbus >= 3.0
    Config keys:
      host, port (default 502), sensor_id, site_id, unit,
      slave_id (default 1), register_address, register_count (default 2),
      scale_factor (default 1.0), function_code ('holding'|'input', default 'holding')
    """

    protocol = "MODBUS"

    def __init__(self, config: dict) -> None:
        self._config = config
        self._client: Any = None

    async def connect(self) -> None:
        try:
            from pymodbus.client import AsyncModbusTcpClient
        except ImportError:
            raise RuntimeError("pymodbus is required for ModbusConnector: pip install pymodbus")

        cfg = self._config
        self._client = AsyncModbusTcpClient(
            host=cfg["host"],
            port=int(cfg.get("port", 502)),
        )
        await self._client.connect()
        _vlog.info("Modbus connected to %s:%s", cfg["host"], cfg.get("port", 502))

    async def read_batch(self) -> list[RawObservation]:
        if not self._client or not self._client.connected:
            return []

        cfg = self._config
        slave = int(cfg.get("slave_id", 1))
        address = int(cfg["register_address"])
        count = int(cfg.get("register_count", 2))
        scale = float(cfg.get("scale_factor", 1.0))
        fn = cfg.get("function_code", "holding")

        try:
            if fn == "input":
                result = await self._client.read_input_registers(address, count=count, slave=slave)
            else:
                result = await self._client.read_holding_registers(address, count=count, slave=slave)

            if result.isError():
                _vlog.warning("Modbus read error: %s", result)
                return []

            # Combine two 16-bit registers into a 32-bit float (big-endian word order)
            import struct
            if count >= 2:
                raw = struct.pack(">HH", result.registers[0], result.registers[1])
                raw_float = struct.unpack(">f", raw)[0]
            else:
                raw_float = float(result.registers[0])

            value = Decimal(str(raw_float * scale))
            return [
                RawObservation(
                    sensor_id=cfg["sensor_id"],
                    site_id=cfg["site_id"],
                    ts=datetime.now(timezone.utc),
                    value=value,
                    unit=cfg.get("unit", ""),
                    protocol=self.protocol,
                    raw_payload={
                        "address": address,
                        "registers": result.registers,
                        "slave": slave,
                    },
                )
            ]
        except Exception as exc:
            _vlog.warning("Modbus exception reading address %s: %s", address, exc)
            return []

    async def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
