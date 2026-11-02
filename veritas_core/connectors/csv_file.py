"""CSV file connector — reads sensor observations from a CSV file."""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

from .base import BaseConnector, RawObservation

_vlog = logging.getLogger(__name__)


class CSVConnector(BaseConnector):
    """
    Reads sensor observations from a CSV file.

    Supports one-shot reads (test/batch ingestion) and watch-for-new-rows mode.

    Config keys:
      file_path (str or Path),
      sensor_id, site_id, unit,
      ts_column    (default 'ts' — any parseable datetime string),
      value_column (default 'value'),
      delimiter    (default ','),
      skip_errors  (default True — log bad rows and continue)
    """

    protocol = "CSV"

    def __init__(self, config: dict) -> None:
        self._config = config
        self._path = Path(config["file_path"])
        self._rows_read = 0

    async def connect(self) -> None:
        if not self._path.exists():
            raise FileNotFoundError(f"CSV file not found: {self._path}")
        _vlog.info("CSV connector ready for %s", self._path)

    def _parse_ts(self, raw: str) -> datetime:
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
        ):
            try:
                dt = datetime.strptime(raw.strip(), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        raise ValueError(f"Cannot parse timestamp: {raw!r}")

    def _iter_rows(self) -> Iterator[RawObservation]:
        cfg = self._config
        ts_col = cfg.get("ts_column", "ts")
        val_col = cfg.get("value_column", "value")
        skip_errors = cfg.get("skip_errors", True)

        with self._path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter=cfg.get("delimiter", ","))
            for i, row in enumerate(reader):
                if i < self._rows_read:
                    continue
                try:
                    ts = self._parse_ts(row[ts_col])
                    value = Decimal(row[val_col].strip())
                    raw_payload = dict(row)
                    yield RawObservation(
                        sensor_id=cfg["sensor_id"],
                        site_id=cfg["site_id"],
                        ts=ts,
                        value=value,
                        unit=cfg.get("unit", ""),
                        protocol=self.protocol,
                        raw_payload=raw_payload,
                    )
                except (KeyError, ValueError, InvalidOperation) as exc:
                    if skip_errors:
                        _vlog.warning("CSV row %d parse error (%s): %s", i, exc, dict(row))
                    else:
                        raise

    async def read_batch(self) -> list[RawObservation]:
        observations = list(self._iter_rows())
        self._rows_read += len(observations)
        return observations

    async def close(self) -> None:
        pass  # nothing to close for file-based connector
