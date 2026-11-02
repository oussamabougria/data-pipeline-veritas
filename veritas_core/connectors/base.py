"""Base connector interface for all protocol adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass
class RawObservation:
    sensor_id: str
    site_id: str
    ts: datetime
    value: Decimal
    unit: str
    protocol: str
    raw_payload: dict = field(default_factory=dict)


class BaseConnector(ABC):
    protocol: str = ""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the data source."""

    @abstractmethod
    async def read_batch(self) -> list[RawObservation]:
        """Read a batch of raw observations. Returns empty list if none available."""

    @abstractmethod
    async def close(self) -> None:
        """Close the connection cleanly."""

    async def __aenter__(self) -> "BaseConnector":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
