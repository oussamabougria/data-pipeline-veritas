from __future__ import annotations

from pathlib import Path

import pandas as pd

from veritas_core.models import DomainType, SourceType
from veritas_core.pipeline import VeritasPipeline
from veritas_core.router import VeritasRouter


def test_batch_csv_industrial() -> None:
    root = Path(__file__).resolve().parents[1] / "veritas_operational"
    csv_path = root / "app_data" / "industrial_incidents.csv"
    rules_path = root / "src" / "veritas_operational" / "config" / "default_rules.yaml"
    frame = pd.read_csv(csv_path)

    source = VeritasRouter().route(
        connector="csv",
        columns=list(frame.columns),
        source_name=csv_path.name,
    )
    run = VeritasPipeline().run(df=frame, source=source, rules_path=rules_path)

    assert source.source_type == SourceType.BATCH
    assert source.domain_type == DomainType.OPERATIONAL
    assert run.status == "completed"
    assert len(run.profiles) == 20
    assert run.branch == "b"
    assert run.violations_total == 14
    assert any(p.verdict.value == "quarantine" for p in run.profiles)


def test_batch_csv_iot() -> None:
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=944, freq="s"),
            "sensor_id": ["s-1"] * 944,
            "temperature": [20 + (idx % 5) for idx in range(944)],
            "pressure": [1.0 + (idx % 3) * 0.1 for idx in range(944)],
            "vibration": [0.2 + (idx % 7) * 0.01 for idx in range(944)],
        }
    )

    source = VeritasRouter().route(
        connector="csv",
        columns=list(frame.columns),
        source_name="data.csv",
    )
    run = VeritasPipeline().run(df=frame, source=source)

    assert source.source_type == SourceType.BATCH
    assert source.domain_type == DomainType.SENSOR
    assert run.status == "completed"
    assert run.branch == "b"


def test_stream_micro_batch() -> None:
    frame = pd.DataFrame(
        {
            "sensor_id": [f"s-{idx}" for idx in range(10)],
            "observed_at": pd.date_range("2026-01-01", periods=10, freq="s"),
            "temperature": [20.0 + idx for idx in range(10)],
            "pressure": [1.0] * 10,
            "value": [float(idx) for idx in range(10)],
        }
    )

    source = VeritasRouter().route(
        connector="kafka",
        columns=list(frame.columns),
        source_name="veritas.raw",
    )
    run = VeritasPipeline().run(df=frame, source=source)

    assert source.source_type == SourceType.STREAM
    assert source.domain_type == DomainType.SENSOR
    assert run.branch == "a"
    assert run.source_type.value == "stream"


def test_audit_trail() -> None:
    root = Path(__file__).resolve().parents[1] / "veritas_operational"
    csv_path = root / "app_data" / "industrial_incidents.csv"
    rules_path = root / "src" / "veritas_operational" / "config" / "default_rules.yaml"
    frame = pd.read_csv(csv_path)
    source = VeritasRouter().route(
        connector="csv",
        columns=list(frame.columns),
        source_name=csv_path.name,
    )

    run = VeritasPipeline().run(df=frame, source=source, rules_path=rules_path)

    assert len(run.audit_events) >= run.violations_total
    assert all(e.pipeline_run_id == run.id for e in run.audit_events)
    assert all(e.timestamp is not None for e in run.audit_events)
