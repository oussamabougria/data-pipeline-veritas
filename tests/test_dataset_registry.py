from __future__ import annotations

import json
from pathlib import Path

from veritas_core.discovery import SemanticDiscovery
from veritas_core.feedback import DatasetRegistry
from veritas_operational.data_loader import LoaderConfig, OperationalDataLoader


DATASET_PATH = Path(__file__).resolve().parents[1] / "veritas_operational" / "app_data" / "industrial_incidents.csv"


def test_dataset_registry_registers_and_exports_jsonl(tmp_path: Path) -> None:
    frame = OperationalDataLoader(LoaderConfig(source_path=DATASET_PATH)).load().data
    profiles = SemanticDiscovery().discover(frame)
    registry_path = tmp_path / "registry.json"
    registry = DatasetRegistry(str(registry_path))

    dataset_id = registry.register(
        source_path=str(DATASET_PATH),
        profiles=profiles,
        sector="aerospace",
    )

    assert dataset_id
    results = registry.list_datasets(sector="aerospace")
    assert len(results) == 1
    assert results[0].dataset_id == dataset_id

    export_path = tmp_path / "training.jsonl"
    registry.export_training_data(str(export_path))
    lines = export_path.read_text(encoding="utf-8").strip().splitlines()
    assert lines
    first = json.loads(lines[0])
    assert "column_name" in first
    assert "was_corrected" in first
