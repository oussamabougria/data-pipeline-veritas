from __future__ import annotations

from veritas_core.feedback.constraint_store import ConstraintStore


def test_empty_store_returns_no_matches() -> None:
    store = ConstraintStore(":memory:")

    assert store.lookup("demand_forecast", "retail") == []


def test_record_and_lookup_exact() -> None:
    store = ConstraintStore(":memory:")
    store.record("demand*", "retail*", {"allow_negative": False}, "human", "test")

    matches = store.lookup("demand_forecast", "retail inventory")

    assert len(matches) == 1
    assert matches[0].constraint["allow_negative"] is False


def test_pattern_matches_variant_column_names() -> None:
    store = ConstraintStore(":memory:")
    store.record("demand*|forecast*", "operational*", {"allow_negative": False}, "human", "test")

    assert len(store.lookup("predicted_demand", "operational")) == 1
    assert len(store.lookup("sales_forecast", "operational")) == 1
    assert len(store.lookup("temperature", "operational")) == 0


def test_domain_pattern_filters_correctly() -> None:
    store = ConstraintStore(":memory:")
    store.record("price*", "retail*", {"min_valid": 0}, "human", "test")

    assert len(store.lookup("price", "retail inventory")) == 1
    assert len(store.lookup("price", "sensor telemetry")) == 0


def test_confirm_strengthens_confidence() -> None:
    store = ConstraintStore(":memory:")
    pattern = store.record("demand*", "retail*", {"allow_negative": False}, "human", "test")
    initial = pattern.effective_confidence()

    store.record("demand*", "retail*", {"allow_negative": False}, "human", "confirmed again")
    matches = store.lookup("demand_forecast", "retail")

    assert matches[0].effective_confidence() >= initial


def test_reject_weakens_confidence() -> None:
    store = ConstraintStore(":memory:")
    pattern = store.record("test_col*", "test*", {"allow_negative": False}, "human", "test")

    store.reject(pattern.id)
    matches = store.lookup("test_col", "test domain")

    assert len(matches) == 0 or matches[0].effective_confidence() < 1.0


def test_to_prompt_context_empty_when_no_match() -> None:
    store = ConstraintStore(":memory:")

    result = store.to_prompt_context(["temperature"], "sensor")

    assert result == ""


def test_to_prompt_context_injects_correctly() -> None:
    store = ConstraintStore(":memory:")
    store.record("demand*", "retail*", {"allow_negative": False, "min_valid": 0}, "human", "test")

    result = store.to_prompt_context(["Demand Forecast"], "retail")

    assert "must be >= 0" in result
    assert "operator-confirmed" in result
