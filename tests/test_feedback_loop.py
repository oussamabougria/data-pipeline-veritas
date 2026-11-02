from __future__ import annotations

import pandas as pd
from veritas_core.feedback.constraint_store import ConstraintStore
from veritas_core.feedback.feedback_store import FeedbackStore
from veritas_core.router import VeritasRouter
from veritas_core.pipeline import VeritasPipeline


def test_accept_advisory_writes_to_constraint_store(tmp_path) -> None:
    feedback_store = FeedbackStore(str(tmp_path / "feedback.json"))
    constraint_store = ConstraintStore(str(tmp_path / "constraints.json"))

    feedback_store.record_advisory_outcome(
        advisory_id="adv-1",
        column="Demand Forecast",
        rule="confidence_quarantine",
        status="accepted",
        constraint={"allow_negative": False, "min_valid": 0, "severity_if_violated": "Critical"},
        domain_label="Retail inventory",
        reason="Demand cannot be negative in retail operations",
        constraint_store=constraint_store,
    )

    constraints = constraint_store.list_all()

    assert len(constraints) == 1
    assert "demand" in constraints[0]["column_pattern"]
    assert constraints[0]["constraint"]["allow_negative"] is False


def test_second_run_uses_stored_constraint(tmp_path) -> None:
    frame = pd.DataFrame(
        {
            "Date": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "Demand Forecast": [10.0, -9.99, 12.5],
            "Price": [4.5, 5.0, 5.5],
        }
    )
    output_dir = tmp_path
    ConstraintStore(str(output_dir / "veritas_constraint_store.json")).record(
        "demand*|forecast*",
        "retail*",
        {"allow_negative": False, "min_valid": 0, "severity_if_violated": "Critical"},
        "human",
        "Demand cannot be negative",
    )
    source = VeritasRouter().route(
        connector="csv",
        columns=list(frame.columns),
        source_name="retail_store_inventory.csv",
    )

    run = VeritasPipeline().run(df=frame, source=source, output_dir=output_dir)

    assert run.violations_total > 0
    assert any(issue.column == "Demand Forecast" for issue in run.issues)


def test_reject_weakens_pattern(tmp_path) -> None:
    store = ConstraintStore(str(tmp_path / "constraints.json"))
    pattern = store.record(
        "demand*",
        "retail*",
        {"allow_negative": False},
        "human",
        "test",
    )
    initial = pattern.confidence
    feedback = FeedbackStore(str(tmp_path / "feedback.json"))

    feedback.record_advisory_outcome(
        advisory_id="adv-1",
        column="Demand Forecast",
        rule="confidence_quarantine",
        status="rejected",
        constraint={"allow_negative": False},
        domain_label="Retail inventory",
        reason="bad suggestion",
        constraint_store=store,
    )

    constraints = store.list_all()
    assert constraints == [] or constraints[0]["confidence"] < initial
