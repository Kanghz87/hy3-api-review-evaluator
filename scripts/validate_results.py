"""Validate the committed real Hy3 result files without making provider calls."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from hy3_api_review_evaluator.annotation import (
    load_annotation_protocol,
    validate_complete_annotations,
)
from hy3_api_review_evaluator.metrics import (
    mean_absolute_error,
    repeated_score_std,
    spearman_correlation,
    strict_ranking_accuracy,
)
from hy3_api_review_evaluator.models import EvaluationResult, ReviewReport

ROOT = Path(__file__).parents[1]
RESULTS = ROOT / "results"
MANIFEST = ROOT / "datasets" / "manifest.jsonl"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def validate() -> dict[str, Any]:
    manifest = _jsonl(MANIFEST)
    manifest_ids = {item["record_id"] for item in manifest}
    hybrid = _jsonl(RESULTS / "hybrid-records.jsonl")
    if len(hybrid) != 60 or {item["record_id"] for item in hybrid} != manifest_ids:
        raise ValueError("Hybrid results must cover each of the 60 manifest records exactly once")
    if len({item["record_id"] for item in hybrid}) != len(hybrid):
        raise ValueError("Hybrid results contain duplicate record IDs")

    rankings: dict[str, dict[str, float]] = defaultdict(dict)
    judge_tokens = 0
    for item in hybrid:
        evaluation = EvaluationResult.model_validate(item["evaluation"])
        if evaluation.mode != "hybrid" or evaluation.judge_usage.total_tokens <= 0:
            raise ValueError(f"Invalid real judge result for {item['record_id']}")
        rankings[item["scenario_id"]][item["reference_tier"]] = evaluation.total_score
        judge_tokens += evaluation.judge_usage.total_tokens
    accuracy, failures = strict_ranking_accuracy(rankings)
    if accuracy != 1.0 or failures:
        raise ValueError("Committed hybrid results do not preserve strict tier ordering")

    hybrid_summary = _json(RESULTS / "hybrid-summary.json")
    if hybrid_summary["completed_record_count"] != 60:
        raise ValueError("Hybrid summary is incomplete")
    if hybrid_summary["observed_judge_token_count"] != judge_tokens:
        raise ValueError("Hybrid token total does not match record-level usage")
    if not math.isclose(hybrid_summary["strict_good_medium_bad_ranking_accuracy"], accuracy):
        raise ValueError("Hybrid ranking summary does not match record-level results")
    human_count = 0
    if hybrid_summary.get("human_annotation_complete"):
        protocol = load_annotation_protocol(ROOT / "datasets/annotation_protocol.json", manifest)
        annotations = validate_complete_annotations(
            ROOT / "datasets/annotations/human_scores.csv", set(protocol["selected_record_ids"])
        )
        model_scores = {row["record_id"]: row["evaluation"]["total_score"] for row in hybrid}
        predicted = [float(model_scores[row["record_id"]]) for row in annotations]
        human = [float(row["manual_total"]) for row in annotations]
        human_count = len(human)
        for key, computed in (
            ("human_score_spearman", spearman_correlation(predicted, human)),
            ("human_score_mae", mean_absolute_error(predicted, human)),
        ):
            reported = hybrid_summary[key]
            if computed is None or reported is None:
                if computed != reported:
                    raise ValueError(f"Human metric missingness mismatch: {key}")
            elif not math.isclose(computed, reported, abs_tol=1e-12):
                raise ValueError(f"Human metric mismatch: {key}")
        if hybrid_summary.get("human_annotation_scope_count") != human_count:
            raise ValueError("Human annotation scope count mismatch")

    stability = _jsonl(RESULTS / "stability-records.jsonl")
    stability_keys = {(item["record_id"], item["repeat_index"]) for item in stability}
    if len(stability) != 18 or len(stability_keys) != 18:
        raise ValueError("Stability results must contain 18 unique record/repeat pairs")
    scores_by_record: dict[str, list[float]] = defaultdict(list)
    for item in stability:
        scores_by_record[item["record_id"]].append(float(item["total_score"]))
    if len(scores_by_record) != 6 or any(len(scores) != 3 for scores in scores_by_record.values()):
        raise ValueError("Stability results must contain three repeats for six records")

    stability_summary = _json(RESULTS / "stability-summary.json")
    for record_id, scores in scores_by_record.items():
        expected = repeated_score_std(scores)
        reported = stability_summary["per_record"][record_id]["population_std"]
        if expected is None or not math.isclose(reported, expected, abs_tol=0.0001):
            raise ValueError(f"Stability standard deviation mismatch for {record_id}")

    smoke = _json(RESULTS / "hy3-smoke.json")
    ReviewReport.model_validate(smoke["review"])
    smoke_evaluation = EvaluationResult.model_validate(smoke["evaluation"])
    if smoke["experiment"]["status"] != "complete" or smoke_evaluation.mode != "hybrid":
        raise ValueError("Real Hy3 smoke is incomplete")

    total_used = int(stability_summary["token_budget"]["total_used"])
    total_limit = int(stability_summary["token_budget"]["total_limit"])
    if total_used > total_limit or total_limit > 850_000:
        raise ValueError("Committed result ledger exceeds the authorized hard cap")
    return {
        "valid": True,
        "hybrid_records": len(hybrid),
        "strict_ranking_accuracy": accuracy,
        "hybrid_judge_tokens": judge_tokens,
        "stability_evaluations": len(stability),
        "real_call_total_tokens": total_used,
        "hard_cap": total_limit,
        "human_metrics_preliminary": hybrid_summary["human_score_spearman"] is None,
        "human_annotation_scope_count": human_count,
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
