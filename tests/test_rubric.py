from __future__ import annotations

from hy3_api_review_evaluator.rubric import DIMENSION_ORDER, load_rubric


def test_rubric_has_six_complete_dimensions_and_100_weight() -> None:
    rubric = load_rubric()
    assert tuple(rubric["dimensions"]) == DIMENSION_ORDER
    assert sum(item["weight"] for item in rubric["dimensions"].values()) == 100
    assert all(set(item["criteria"]) == {0, 1, 2, 3, 4} for item in rubric["dimensions"].values())
    assert len(rubric["severe_failure_rules"]) >= 5
