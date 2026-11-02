from __future__ import annotations

from pathlib import Path

from veritas_core.discovery import Archetype, SemanticDiscovery, SemanticRole
from veritas_operational.data_loader import LoaderConfig, OperationalDataLoader


DATASET_PATH = Path(__file__).resolve().parents[1] / "veritas_operational" / "app_data" / "industrial_incidents.csv"


def _profiles_by_name() -> dict[str, object]:
    frame = OperationalDataLoader(LoaderConfig(source_path=DATASET_PATH)).load().data
    profiles = SemanticDiscovery().discover(frame)
    return {profile.column_name: profile for profile in profiles}


def test_site_is_categorical() -> None:
    profile = _profiles_by_name()["site"]
    assert profile.role == SemanticRole.CATEGORICAL


def test_status_is_categorical() -> None:
    profile = _profiles_by_name()["status"]
    assert profile.role == SemanticRole.CATEGORICAL


def test_opened_at_is_temporal_inertial() -> None:
    profile = _profiles_by_name()["opened_at"]
    assert profile.role == SemanticRole.TEMPORAL
    assert profile.archetype == Archetype.INERTIAL


def test_response_minutes_is_metric_not_temporal() -> None:
    profile = _profiles_by_name()["response_minutes"]
    assert profile.role == SemanticRole.METRIC
    assert profile.archetype == Archetype.CYCLICAL
    assert "duration" in profile.notes


def test_description_is_text() -> None:
    profile = _profiles_by_name()["description"]
    assert profile.role == SemanticRole.TEXT
    assert profile.archetype == Archetype.NONE


def test_downtime_negative_value_is_flagged() -> None:
    profile = _profiles_by_name()["downtime_min"]
    assert profile.role == SemanticRole.METRIC
    assert profile.archetype == Archetype.CYCLICAL
    assert any("-12" in anomaly for anomaly in profile.anomalies)


def test_age_hours_is_metric_not_temporal() -> None:
    profile = _profiles_by_name()["age_hours"]
    assert profile.role == SemanticRole.METRIC
    assert profile.archetype == Archetype.CYCLICAL
