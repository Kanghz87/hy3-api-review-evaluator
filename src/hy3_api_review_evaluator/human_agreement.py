"""Descriptive agreement statistics; never tune or replace either source score."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from typing import Any

from .metrics import mean_absolute_error, spearman_correlation
from .rubric import DIMENSION_ORDER


def paired_metrics(predicted: Sequence[float], human: Sequence[float]) -> dict[str, Any]:
    if not predicted or len(predicted) != len(human):
        raise ValueError("Agreement requires equally sized non-empty paired scores")
    if not all(math.isfinite(value) for value in (*predicted, *human)):
        raise ValueError("Agreement scores must be finite")
    errors = [left - right for left, right in zip(predicted, human, strict=True)]
    return {
        "sample_count": len(human),
        "spearman": spearman_correlation(predicted, human),
        "mae": mean_absolute_error(predicted, human),
        "mean_signed_error": statistics.fmean(errors),
        "mean_automatic": statistics.fmean(predicted),
        "mean_human": statistics.fmean(human),
        "max_absolute_error": max(abs(error) for error in errors),
        "exact_match_rate": sum(error == 0 for error in errors) / len(errors),
    }


def summarize_pairs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len({row["record_id"] for row in rows}) != len(rows):
        raise ValueError("Agreement rows must have unique record IDs")

    def totals(selected: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            mode: paired_metrics(
                [row[f"{mode}_total"] for row in selected],
                [row["human_total"] for row in selected],
            )
            for mode in ("hybrid", "baseline")
        }

    overall = totals(rows)
    for mode in ("hybrid", "baseline"):
        for tolerance in (5, 10):
            overall[mode][f"within_{tolerance}_points_rate"] = sum(
                abs(row[f"{mode}_total"] - row["human_total"]) <= tolerance for row in rows
            ) / len(rows)
    return {
        "overall": overall,
        "by_difficulty": {
            value: totals([row for row in rows if row["difficulty"] == value])
            for value in sorted({row["difficulty"] for row in rows})
        },
        "by_reference_tier": {
            value: totals([row for row in rows if row["reference_tier"] == value])
            for value in sorted({row["reference_tier"] for row in rows})
        },
        "by_dimension": {
            name: {
                mode: paired_metrics(
                    [row[f"{mode}_{name}"] for row in rows],
                    [row[f"human_{name}"] for row in rows],
                )
                for mode in ("hybrid", "baseline")
            }
            for name in DIMENSION_ORDER
        },
        "largest_hybrid_errors": [
            {
                key: row[key]
                for key in (
                    "record_id",
                    "difficulty",
                    "reference_tier",
                    "human_total",
                    "hybrid_total",
                    "hybrid_signed_error",
                    "hybrid_absolute_error",
                )
            }
            for row in sorted(
                rows, key=lambda row: (-row["hybrid_absolute_error"], row["record_id"])
            )[:5]
        ],
    }
