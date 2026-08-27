from __future__ import annotations

import pytest

from hy3_api_review_evaluator.metrics import (
    mean_absolute_error,
    repeated_score_std,
    spearman_correlation,
    strict_ranking_accuracy,
)


def test_strict_ranking_counts_ties_as_failures() -> None:
    accuracy, failures = strict_ranking_accuracy(
        {
            "pass": {"good": 90, "medium": 70, "bad": 20},
            "tie": {"good": 80, "medium": 80, "bad": 10},
        }
    )
    assert accuracy == 0.5
    assert failures == ["tie"]


def test_agreement_metrics_are_exact_and_missing_safe() -> None:
    assert spearman_correlation([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)
    assert mean_absolute_error([80, 60], [75, 70]) == pytest.approx(7.5)
    assert repeated_score_std([10, 10, 10]) == 0
    assert spearman_correlation([], []) is None
    assert mean_absolute_error([], []) is None
    assert repeated_score_std([10]) is None
