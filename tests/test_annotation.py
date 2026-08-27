from __future__ import annotations

from pathlib import Path

import pytest

from hy3_api_review_evaluator.annotation import (
    blinded_order,
    build_copy_bundle,
    display_id,
    load_annotation_protocol,
    validate_complete_annotations,
    weighted_total,
)
from hy3_api_review_evaluator.models import Focus, ReviewReport
from hy3_api_review_evaluator.rubric import DIMENSION_ORDER

FIXTURES = Path(__file__).parent / "fixtures"


def test_blind_order_is_stable_and_alias_specific() -> None:
    records = [{"record_id": f"record-{index}"} for index in range(10)]
    first = [item["record_id"] for item in blinded_order(records, "owner-01")]
    repeated = [item["record_id"] for item in blinded_order(records, "owner-01")]
    other = [item["record_id"] for item in blinded_order(records, "owner-02")]
    assert first == repeated
    assert first != other
    assert display_id("record-1", "owner-01").startswith("sample-")


def test_annotation_total_uses_rubric_weights() -> None:
    assert weighted_total({name: 4 for name in DIMENSION_ORDER}) == 100
    assert weighted_total({name: 0 for name in DIMENSION_ORDER}) == 0


def test_copy_bundle_contains_blind_material_and_score_template() -> None:
    report = ReviewReport(
        specification_title="Demo API",
        openapi_version="3.1.0",
        focus=Focus.ALL,
        executive_summary="A report prepared for independent human annotation.",
    )
    bundle = build_copy_bundle(
        "sample-1234567890",
        "openapi: 3.1.0\ninfo: {title: Demo API}",
        report,
    )
    assert "sample-1234567890" in bundle
    assert "=== OPENAPI DOCUMENT ===" in bundle
    assert "=== REVIEW REPORT TO ANNOTATE ===" in bundle
    assert "事实准确性 (factual_accuracy): __ / 4" in bundle
    assert "reference_tier" not in bundle
    assert "automatic_score" not in bundle


def test_annotation_protocol_validates_selected_manifest_records() -> None:
    manifest = [
        {
            "record_id": "one",
            "difficulty": "easy",
            "reference_tier": "good",
            "scenario_id": "scenario-one",
            "is_adversarial": False,
            "adversarial_type": None,
        },
        {
            "record_id": "two",
            "difficulty": "hard",
            "reference_tier": "bad",
            "scenario_id": "scenario-two",
            "is_adversarial": True,
            "adversarial_type": "fabrication",
        },
    ]
    protocol = load_annotation_protocol(FIXTURES / "annotation-protocol-valid.json", manifest)
    assert protocol["selected_record_ids"] == ["one", "two"]


def test_annotation_protocol_rejects_unknown_record() -> None:
    with pytest.raises(ValueError, match="unknown record IDs"):
        load_annotation_protocol(
            FIXTURES / "annotation-protocol-invalid-unknown.json",
            [{"record_id": "known"}],
        )


def test_complete_annotations_validate_scores_and_coverage() -> None:
    path = FIXTURES / "annotation-valid.csv"
    rows = validate_complete_annotations(path, {"record-1"})
    assert rows[0]["manual_total"] == "100"


def test_complete_annotations_reject_wrong_total() -> None:
    path = FIXTURES / "annotation-invalid-total.csv"
    with pytest.raises(ValueError, match="manual_total mismatch"):
        validate_complete_annotations(path, {"record-1"})
