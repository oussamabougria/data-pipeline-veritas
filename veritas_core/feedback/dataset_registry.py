"""Dataset registry used to build Veritas training history."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
from typing import Any
import uuid

import pandas as pd

from veritas_core.discovery.semantic_discovery import ColumnProfile

_vlog = logging.getLogger(__name__)


@dataclass(slots=True)
class DatasetRecord:
    """Persistent record of one processed dataset."""

    dataset_id: str
    source_path: str
    ingested_at: str
    sector: str | None
    n_rows: int
    n_columns: int
    column_profiles: list[dict[str, Any]]
    corrections_applied: list[str]
    anomalies_found: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize the record to a JSON-friendly dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DatasetRecord":
        """Build a dataset record from persisted JSON data."""
        return cls(**payload)


class DatasetRegistry:
    """JSON-backed registry of all datasets processed by Veritas."""

    def __init__(self, registry_path: str = "veritas_registry.json"):
        """Load the dataset registry if it already exists."""
        self.registry_path = Path(registry_path)
        self._lock = threading.RLock()
        self._records: list[DatasetRecord] = []
        self._load()

    def register(
        self,
        source_path: str,
        profiles: list[ColumnProfile],
        sector: str | None = None,
        corrections_applied: list[str] | None = None,
    ) -> str:
        """Persist one dataset processing event and return its generated id."""
        n_rows, n_columns = self._inspect_source(source_path, profiles)
        dataset_id = str(uuid.uuid4())
        record = DatasetRecord(
            dataset_id=dataset_id,
            source_path=source_path,
            ingested_at=datetime.now(UTC).isoformat(),
            sector=sector,
            n_rows=n_rows,
            n_columns=n_columns,
            column_profiles=[profile.to_dict() for profile in profiles],
            corrections_applied=sorted(set(corrections_applied or [])),
            anomalies_found=sum(len(profile.anomalies) for profile in profiles),
        )
        with self._lock:
            self._records.append(record)
            self._persist()
        _vlog.info("Registered dataset %s from %s", dataset_id, source_path)
        return dataset_id

    def get_dataset(self, dataset_id: str) -> DatasetRecord | None:
        """Return one dataset record by id."""
        with self._lock:
            for record in self._records:
                if record.dataset_id == dataset_id:
                    return record
        return None

    def list_datasets(self, sector: str | None = None) -> list[DatasetRecord]:
        """Return all registered datasets, optionally filtered by sector."""
        with self._lock:
            if sector is None:
                return list(self._records)
            return [record for record in self._records if record.sector == sector]

    def export_training_data(self, output_path: str) -> None:
        """Export registry history as JSONL for downstream training."""
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with output.open("w", encoding="utf-8") as handle:
                for record in self.list_datasets():
                    corrected_set = set(record.corrections_applied)
                    for profile in record.column_profiles:
                        correction_target = None
                        if profile["column_name"] in corrected_set:
                            correction_target = {
                                "role": profile["role"],
                                "archetype": profile["archetype"],
                            }
                        line = {
                            "dataset_id": record.dataset_id,
                            "source_path": record.source_path,
                            "sector": record.sector,
                            "column_name": profile["column_name"],
                            "role": profile["role"],
                            "archetype": profile["archetype"],
                            "confidence": profile["confidence"],
                            "was_corrected": profile["column_name"] in corrected_set,
                            "correction_target": correction_target,
                        }
                        handle.write(json.dumps(line, ensure_ascii=True) + "\n")
        except Exception as exc:
            raise RuntimeError(f"Failed to export training data to {output}: {exc}") from exc

    def _inspect_source(self, source_path: str, profiles: list[ColumnProfile]) -> tuple[int, int]:
        path = Path(source_path)
        if not path.exists():
            return 0, len(profiles)
        suffix = path.suffix.lower()
        try:
            if suffix == ".csv":
                frame = pd.read_csv(path)
            elif suffix == ".tsv":
                frame = pd.read_csv(path, sep="\t")
            elif suffix == ".json":
                frame = pd.read_json(path)
            elif suffix in {".xlsx", ".xls"}:
                frame = pd.read_excel(path)
            else:
                return 0, len(profiles)
        except Exception as exc:
            raise RuntimeError(f"Failed to inspect dataset source {source_path}: {exc}") from exc
        return int(len(frame)), int(len(frame.columns))

    def _load(self) -> None:
        with self._lock:
            if not self.registry_path.exists():
                self._records = []
                return
            try:
                payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise RuntimeError(f"Failed to load dataset registry from {self.registry_path}: {exc}") from exc
            if not isinstance(payload, list):
                raise RuntimeError(f"Dataset registry {self.registry_path} must contain a JSON list")
            self._records = [DatasetRecord.from_dict(item) for item in payload]

    def _persist(self) -> None:
        data = [record.to_dict() for record in self._records]
        self._atomic_write(data)

    def _atomic_write(self, payload: list[dict[str, Any]]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=self.registry_path.stem + "_",
            suffix=".tmp",
            dir=str(self.registry_path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.registry_path)
        except Exception as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise RuntimeError(f"Failed to persist dataset registry to {self.registry_path}: {exc}") from exc
