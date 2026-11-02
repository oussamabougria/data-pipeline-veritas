from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .exceptions import DataLoadError
from .stage_result import StageResult


@dataclass(slots=True)
class LoaderConfig:
    source_path: Path
    parse_dates: bool = True


class OperationalDataLoader:
    def __init__(self, config: LoaderConfig):
        self.config = config

    def load(self) -> StageResult:
        started = time.perf_counter()
        path = self.config.source_path
        if not path.exists():
            raise DataLoadError(f"Source file not found: {path}")

        suffix = path.suffix.lower()
        if suffix == ".csv":
            frame = pd.read_csv(path)
        elif suffix == ".tsv":
            frame = pd.read_csv(path, sep="\t")
        elif suffix == ".json":
            frame = pd.read_json(path)
        elif suffix in {".xlsx", ".xls"}:
            frame = pd.read_excel(path)
        else:
            raise DataLoadError(f"Unsupported file type: {suffix}")

        if self.config.parse_dates:
            for col in frame.columns:
                lower = col.lower()
                is_temporal_name = (
                    lower.endswith("_at")
                    or lower.endswith("_date")
                    or lower.endswith("_ts")
                    or lower in {"date", "timestamp", "opened_at", "closed_at", "first_response_at"}
                )
                if is_temporal_name:
                    try:
                        frame[col] = pd.to_datetime(frame[col], utc=True)
                    except (TypeError, ValueError):
                        pass

        return StageResult(
            data=frame,
            metadata={
                "source_path": str(path),
                "rows": int(len(frame)),
                "columns": list(frame.columns),
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
