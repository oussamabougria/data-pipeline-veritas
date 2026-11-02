from __future__ import annotations

from pathlib import Path

from veritas_core.discovery import SemanticDiscovery
from veritas_core.feedback import CorrectionRecord, DatasetRegistry, FeedbackStore, PrecisionTracker
from veritas_operational.data_loader import LoaderConfig, OperationalDataLoader


DATASET_PATH = Path(__file__).resolve().parents[1] / "veritas_operational" / "app_data" / "industrial_incidents.csv"


def _build_profiles():
    frame = OperationalDataLoader(LoaderConfig(source_path=DATASET_PATH)).load().data
    return SemanticDiscovery().discover(frame)


def test_precision_tracker_accuracy_and_curve(tmp_path: Path) -> None:
    profiles = _build_profiles()
    store = FeedbackStore(str(tmp_path / "feedback.json"))
    registry = DatasetRegistry(str(tmp_path / "registry.json"))
    registry.register(str(DATASET_PATH), profiles, sector="aerospace")

    tracker = PrecisionTracker(store, registry)
    report = tracker.evaluate(profiles)
    assert report.accuracy == 1.0

    for column_name, original_role, corrected_role in [
        ("site", "IDENTIFIER", "CATEGORICAL"),
        ("status", "IDENTIFIER", "CATEGORICAL"),
        ("owner_team", "IDENTIFIER", "CATEGORICAL"),
    ]:
        store.add_correction(
            CorrectionRecord(
                column_name=column_name,
                original_role=original_role,
                corrected_role=corrected_role,
                original_archetype="NONE",
                corrected_archetype="NONE",
                corrected_by="operator_01",
                corrected_at="2026-04-28T11:00:00Z",
                dataset_source="industrial_incidents.csv",
                pattern_hint=None,
            )
        )

    corrected_profiles = store.apply_corrections(profiles)
    registry.register(
        str(DATASET_PATH),
        corrected_profiles,
        sector="aerospace",
        corrections_applied=["site", "status", "owner_team"],
    )

    report = tracker.evaluate(profiles)
    assert report.accuracy == (len(profiles) - 3) / len(profiles)

    curve = tracker.calibration_curve()
    high_bucket = next(bucket for bucket in curve if bucket["confidence_range"] == "0.85-1.0")
    assert high_bucket["actual_accuracy"] >= 0.8
