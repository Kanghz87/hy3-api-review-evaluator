"""Dependency-light validation metrics with explicit missing-label behavior."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence


def _ranks(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(indexed):
        end = start + 1
        while end < len(indexed) and indexed[end][1] == indexed[start][1]:
            end += 1
        average_rank = (start + 1 + end) / 2
        for position in range(start, end):
            ranks[indexed[position][0]] = average_rank
        start = end
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True))
    left_variance = sum((x - left_mean) ** 2 for x in left)
    right_variance = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_variance * right_variance)
    return numerator / denominator if denominator else None


def spearman_correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Return None rather than inventing a value when labels are absent or constant."""
    if len(left) != len(right) or len(left) < 2:
        return None
    return _pearson(_ranks(left), _ranks(right))


def mean_absolute_error(predicted: Sequence[float], observed: Sequence[float]) -> float | None:
    if len(predicted) != len(observed) or not predicted:
        return None
    return statistics.fmean(abs(x - y) for x, y in zip(predicted, observed, strict=True))


def strict_ranking_accuracy(
    scores: Mapping[str, Mapping[str, float]],
) -> tuple[float, list[str]]:
    """A scenario passes only when good > medium > bad; ties count as failures."""
    failures: list[str] = []
    for scenario_id, tiers in scores.items():
        if set(tiers) != {"good", "medium", "bad"}:
            failures.append(scenario_id)
            continue
        if not tiers["good"] > tiers["medium"] > tiers["bad"]:
            failures.append(scenario_id)
    total = len(scores)
    return ((total - len(failures)) / total if total else 0.0), failures


def repeated_score_std(values: Iterable[float]) -> float | None:
    materialized = list(values)
    return statistics.pstdev(materialized) if len(materialized) >= 2 else None
