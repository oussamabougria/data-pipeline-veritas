from __future__ import annotations

from pathlib import Path

from veritas_core.discovery import Archetype, ColumnProfile, SemanticRole
from veritas_core.feedback import CorrectionRecord, FeedbackStore


def test_feedback_store_persists_and_applies_corrections(tmp_path: Path) -> None:
    store_path = tmp_path / "feedback.json"
    store = FeedbackStore(str(store_path))
    record = CorrectionRecord(
        column_name="site",
        original_role="IDENTIFIER",
        corrected_role="CATEGORICAL",
        original_archetype="NONE",
        corrected_archetype="NONE",
        corrected_by="operator_01",
        corrected_at="2026-04-28T11:00:00Z",
        dataset_source="industrial_incidents.csv",
        pattern_hint=None,
    )
    store.add_correction(record)

    profiles = [
        ColumnProfile(
            column_name="site",
            role=SemanticRole.IDENTIFIER,
            archetype=Archetype.NONE,
            confidence=0.91,
            anomalies=[],
            notes="near-unique identifier",
        )
    ]
    corrected = store.apply_corrections(profiles)

    assert corrected[0].role == SemanticRole.CATEGORICAL
    assert corrected[0].confidence == 1.0
    assert "feedback store" in corrected[0].notes

    reloaded = FeedbackStore(str(store_path))
    persisted = reloaded.get_corrections("site")
    assert persisted is not None
    assert persisted.corrected_role == "CATEGORICAL"
