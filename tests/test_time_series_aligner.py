from __future__ import annotations

from pathlib import Path

import pandas as pd

from veritas_core.discovery import TimeSeriesAligner
from veritas_operational.data_loader import LoaderConfig, OperationalDataLoader


DATASET_PATH = Path(__file__).resolve().parents[1] / "veritas_operational" / "app_data" / "industrial_incidents.csv"


def test_industrial_incidents_alignment() -> None:
    frame = OperationalDataLoader(LoaderConfig(source_path=DATASET_PATH)).load().data

    result = TimeSeriesAligner().align(frame, time_col="opened_at")

    assert result.reference_column == "opened_at"
    assert result.resolution == "hours"
    assert result.gaps == []
    assert isinstance(result.aligned_df.index, pd.DatetimeIndex)
