from __future__ import annotations

from copy import deepcopy

import pytest

from hy3_api_review_evaluator.human_agreement import paired_metrics, summarize_pairs
from hy3_api_review_evaluator.metrics import spearman_correlation
from hy3_api_review_evaluator.rubric import DIMENSION_ORDER


def test_paired_metrics_are_directional_and_scale_preserving() -> None:
    result = paired_metrics([100, 80, 0], [95, 85, 0])
    assert result["sample_count"] == 3
    assert result["spearman"] == pytest.approx(1.0)
    assert result["mae"] == pytest.approx(10 / 3)
    assert result["mean_signed_error"] == 0
    assert result["max_absolute_error"] == 5
    assert result["exact_match_rate"] == pytest.approx(1 / 3)


def test_tied_spearman_uses_average_ranks_and_constant_is_undefined() -> None:
    assert spearman_correlation([1, 1, 2], [1, 2, 2]) == pytest.approx(0.5)
    assert paired_metrics([4, 4], [3, 4])["spearman"] is None


@pytest.mark.parametrize("left,right", [([], []), ([1], [1, 2]), ([float("nan")], [1])])
def test_paired_metrics_reject_invalid_pairs(left: list[float], right: list[float]) -> None:
    with pytest.raises(ValueError):
        paired_metrics(left, right)


def _row(record_id: str, human: float, hybrid: float, tier: str) -> dict:
    row = {
        "record_id": record_id,
        "scenario_id": "demo",
        "difficulty": "easy",
        "reference_tier": tier,
        "human_total": human,
        "hybrid_total": hybrid,
        "baseline_total": hybrid,
        "hybrid_signed_error": hybrid - human,
        "hybrid_absolute_error": abs(hybrid - human),
    }
    for name in DIMENSION_ORDER:
        row.update({f"human_{name}": 3, f"hybrid_{name}": 4, f"baseline_{name}": 2})
    return row


def test_grouping_does_not_modify_scores_or_drop_largest_error() -> None:
    rows = [_row("a", 90, 100, "good"), _row("b", 0, 5, "bad")]
    original = deepcopy(rows)
    summary = summarize_pairs(rows)
    assert rows == original
    assert summary["overall"]["hybrid"]["mae"] == 7.5
    assert summary["by_dimension"]["actionability"]["hybrid"]["mae"] == 1
    assert summary["by_reference_tier"]["good"]["hybrid"]["sample_count"] == 1
    assert summary["largest_hybrid_errors"][0]["record_id"] == "a"


def test_duplicate_records_cannot_inflate_agreement() -> None:
    with pytest.raises(ValueError, match="unique record"):
        summarize_pairs([_row("a", 90, 100, "good"), _row("a", 90, 100, "good")])
