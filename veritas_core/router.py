from __future__ import annotations

import re

from .models import DataSource, DomainType, SourceType


STREAM_CONNECTORS = {"kafka", "mqtt", "modbus", "opcua", "simulator"}
BATCH_CONNECTORS = {"csv", "database", "file"}
POLL_CONNECTORS = {"rest", "api"}

SENSOR_SIGNALS = {
    "columns": {
        "temperature",
        "pressure",
        "vibration",
        "flow",
        "voltage",
        "current",
        "rpm",
        "torque",
        "humidity",
        "sensor",
    },
    "name_fragments": {
        "sensor",
        "telemetry",
        "readings",
        "iot",
        "scada",
        "plc",
        "modbus",
        "opcua",
    },
}

OPERATIONAL_SIGNALS = {
    "columns": {
        "incident_id",
        "ticket",
        "severity",
        "status",
        "owner_team",
        "downtime",
        "defect",
        "resolution",
        "priority",
        "assignee",
        "inventory",
        "units_sold",
        "price",
        "discount",
        "forecast",
    },
    "name_fragments": {
        "incident",
        "jira",
        "erp",
        "bpm",
        "inventory",
        "sales",
        "orders",
        "crm",
        "hr",
    },
}


class VeritasRouter:
    """
    Two-dimensional router.
    Dimension 1: source_type - HOW did data arrive?
    Dimension 2: domain_type - WHAT does data represent?
    """

    def route(
        self,
        connector: str,
        columns: list[str],
        source_name: str = "",
        config: dict | None = None,
    ) -> DataSource:
        source_type = self._detect_source_type(connector)
        domain_type = self._detect_domain_type(columns, source_name)
        source = DataSource(
            name=source_name,
            source_type=source_type,
            domain_type=domain_type,
            connector=connector,
            config=config or {},
        )
        return source

    def _detect_source_type(self, connector: str) -> SourceType:
        c = connector.lower()
        if c in STREAM_CONNECTORS:
            return SourceType.STREAM
        if c in POLL_CONNECTORS:
            return SourceType.POLL
        return SourceType.BATCH

    def _detect_domain_type(
        self,
        columns: list[str],
        source_name: str,
    ) -> DomainType:
        col_lower = {_normalize_signal(c) for c in columns}
        name_lower = source_name.lower()

        sensor_col_hits = _signal_hits(col_lower, SENSOR_SIGNALS["columns"])
        sensor_name_hits = sum(
            1 for f in SENSOR_SIGNALS["name_fragments"] if f in name_lower
        )

        operational_col_hits = _signal_hits(col_lower, OPERATIONAL_SIGNALS["columns"])
        operational_name_hits = sum(
            1 for f in OPERATIONAL_SIGNALS["name_fragments"] if f in name_lower
        )

        sensor_score = sensor_col_hits * 2 + sensor_name_hits
        operational_score = operational_col_hits * 2 + operational_name_hits

        numeric_col_names = sum(1 for c in columns if re.match(r"^\d+$", c))
        if numeric_col_names > len(columns) * 0.5:
            sensor_score += 4

        if sensor_score == 0 and operational_score == 0:
            return DomainType.UNKNOWN
        if sensor_score >= operational_score:
            return DomainType.SENSOR
        return DomainType.OPERATIONAL

    @staticmethod
    def branch(source: DataSource) -> str:
        """
        Returns "a" for stream path, "b" for batch/poll path.
        This is the single routing decision.
        """
        if source.source_type == SourceType.STREAM:
            return "a"
        return "b"


def _normalize_signal(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized


def _signal_hits(columns: set[str], signals: set[str]) -> int:
    hits = 0
    for signal in signals:
        normalized_signal = _normalize_signal(signal)
        for column in columns:
            if column == normalized_signal or normalized_signal in column.split("_"):
                hits += 1
                break
    return hits
