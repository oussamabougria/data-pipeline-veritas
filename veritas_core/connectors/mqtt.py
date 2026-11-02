"""MQTT connector — subscribes to a topic and buffers incoming messages."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

from .base import BaseConnector, RawObservation

_vlog = logging.getLogger(__name__)


class MQTTConnector(BaseConnector):
    """
    Connects to an MQTT broker and subscribes to one or more topics.

    Requires: paho-mqtt >= 2.0
    Config keys: host, port, topic, sensor_id, site_id, unit, client_id, username, password
    """

    protocol = "MQTT"

    def __init__(self, config: dict) -> None:
        self._config = config
        self._client = None
        self._buffer: asyncio.Queue = asyncio.Queue(maxsize=10_000)
        self._connected = False

    async def connect(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            raise RuntimeError("paho-mqtt is required for MQTTConnector: pip install paho-mqtt")

        cfg = self._config
        client = mqtt.Client(client_id=cfg.get("client_id", "veritas-mqtt"))
        if cfg.get("username"):
            client.username_pw_set(cfg["username"], cfg.get("password", ""))

        def _on_message(_client: Any, _userdata: Any, msg: Any) -> None:
            try:
                payload = json.loads(msg.payload.decode())
                obs = RawObservation(
                    sensor_id=cfg["sensor_id"],
                    site_id=cfg["site_id"],
                    ts=datetime.now(timezone.utc),
                    value=Decimal(str(payload.get("value", 0))),
                    unit=cfg.get("unit", ""),
                    protocol=self.protocol,
                    raw_payload=payload,
                )
                self._buffer.put_nowait(obs)
            except Exception as exc:
                _vlog.warning("MQTT message parse error: %s", exc)

        client.on_message = _on_message
        client.connect(cfg["host"], int(cfg.get("port", 1883)))
        client.subscribe(cfg["topic"])
        client.loop_start()
        self._client = client
        self._connected = True
        _vlog.info("MQTT connected to %s:%s topic=%s", cfg["host"], cfg.get("port", 1883), cfg["topic"])

    async def read_batch(self) -> list[RawObservation]:
        batch: list[RawObservation] = []
        try:
            while not self._buffer.empty():
                batch.append(self._buffer.get_nowait())
        except asyncio.QueueEmpty:
            pass
        return batch

    async def close(self) -> None:
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._connected = False
