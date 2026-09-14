"""Run the no-cost deterministic baseline over every public dataset record."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from hy3_api_review_evaluator import __version__
from hy3_api_review_evaluator.annotation import validate_complete_annotations
from hy3_api_review_evaluator.config import Settings
from hy3_api_review_evaluator.evaluator import evaluate_report_locally
from hy3_api_review_evaluator.metrics import (
    mean_absolute_error,
    spearman_correlation,
    strict_ranking_accuracy,
)
from hy3_api_review_evaluator.models import ReviewReport
from hy3_api_review_evaluator.spec_loader import load_spec_bytes

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "datasets" / "manifest.jsonl"
RECORDS_OUTPUT = ROOT / "results" / __version__ / "local-records.csv"
SUMMARY_OUTPUT = ROOT / "results" / __version__ / "local-summary.json"
TIER_ORDINAL = {"bad": 1.0, "medium": 2.0, "good": 3.0}


def _settings() -> Settings:
    return Settings(
        api_key=None,
        base_url="https://tokenhub.tencentmaas.com/v1",
        model="hy3",
        timeout_seconds=10,
        max_retries=0,
        reasoning_effort="high",
        max_file_bytes=2_000_000,
        max_container_nodes=200_000,
        max_nesting_depth=100,
        max_model_chars=120_000,
        max_output_tokens=6_000,
        total_token_budget=850_000,
        default_run_token_budget=150_000,
    )


def run() -> dict[str, Any]:
    records = [
        json.loads(line)
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    settings = _settings()
    spec_cache: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    rankings: dict[str, dict[str, float]] = defaultdict(dict)
    for record in records:
        spec_path = ROOT / record["spec_path"]
        if record["spec_path"] not in spec_cache:
            spec_cache[record["spec_path"]] = load_spec_bytes(
                spec_path.read_bytes(), spec_path.name, settings
            )
        spec = spec_cache[record["spec_path"]]
        report = ReviewReport.model_validate_json(
            (ROOT / record["report_path"]).read_text(encoding="utf-8")
        )
        result = evaluate_report_locally(spec, report)
        row: dict[str, Any] = {
            "record_id": record["record_id"],
            "scenario_id": record["scenario_id"],
            "reference_tier": record["reference_tier"],
            "difficulty": record["difficulty"],
            "is_adversarial": record["is_adversarial"],
            "report_adversarial": record["report_adversarial"],
            "total_score": result.total_score,
            "verdict": result.verdict,
            "severe_failure": result.severe_failure,
        }
        row.update({f"score_{item.name}": item.final_score for item in result.dimension_scores})
        rows.append(row)
        rankings[record["scenario_id"]][record["reference_tier"]] = result.total_score

    fieldnames = list(rows[0])
    RECORDS_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with RECORDS_OUTPUT.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    ranking_accuracy, ranking_failures = strict_ranking_accuracy(rankings)
    tier_values = [TIER_ORDINAL[row["reference_tier"]] for row in rows]
    scores = [float(row["total_score"]) for row in rows]
    adversarial_rows = [row for row in rows if row["report_adversarial"]]
    adversarial_detected = [
        row for row in adversarial_rows if row["severe_failure"] or row["total_score"] < 65
    ]
    by_difficulty = {
        difficulty: {
            tier: round(
                sum(
                    row["total_score"]
                    for row in rows
                    if row["difficulty"] == difficulty and row["reference_tier"] == tier
                )
                / sum(
                    row["difficulty"] == difficulty and row["reference_tier"] == tier
                    for row in rows
                ),
                2,
            )
            for tier in ("good", "medium", "bad")
        }
        for difficulty in ("easy", "medium", "hard")
    }
    summary = {
        "status": "preliminary",
        "evaluation_version": "1.1",
        "implementation_version": __version__,
        "mode": "deterministic",
        "record_count": len(rows),
        "scenario_count": len(rankings),
        "strict_good_medium_bad_ranking_accuracy": ranking_accuracy,
        "ranking_failure_scenarios": ranking_failures,
        "tier_score_spearman": spearman_correlation(tier_values, scores),
        "human_score_spearman": None,
        "human_score_mae": None,
        "repeat_judge_score_std": None,
        "adversarial_detection_rate": (
            len(adversarial_detected) / len(adversarial_rows) if adversarial_rows else None
        ),
        "adversarial_record_count": len(adversarial_rows),
        "by_difficulty_mean_score": by_difficulty,
        "notes": [
            "Tier Spearman uses construction tiers, not human annotations.",
            "These are current local scores, not new Hy3 hybrid or stability measurements.",
            "Existing human annotations use rubric v1.0; comparison is descriptive only.",
        ],
    }
    annotation_path = ROOT / "datasets/annotations/human_scores.csv"
    if annotation_path.exists():
        protocol = json.loads(
            (ROOT / "datasets/annotation_protocol.json").read_text(encoding="utf-8")
        )
        annotations = validate_complete_annotations(
            annotation_path, set(protocol["selected_record_ids"])
        )
        automatic = {row["record_id"]: row["total_score"] for row in rows}
        human = [float(row["manual_total"]) for row in annotations]
        predicted = [automatic[row["record_id"]] for row in annotations]
        summary["comparison_to_v1_0_human_labels"] = {
            "sample_count": len(human),
            "spearman": spearman_correlation(predicted, human),
            "mae": mean_absolute_error(predicted, human),
            "annotation_version": "1.0",
        }
    previous_path = ROOT / "results/preliminary-local-records.csv"
    if previous_path.exists():
        with previous_path.open(encoding="utf-8-sig", newline="") as stream:
            previous = {
                row["record_id"]: float(row["total_score"]) for row in csv.DictReader(stream)
            }
        summary["changed_records_vs_v1_0"] = [
            {
                "record_id": row["record_id"],
                "old_score": previous[row["record_id"]],
                "new_score": row["total_score"],
            }
            for row in rows
            if row["total_score"] != previous[row["record_id"]]
        ]
    SUMMARY_OUTPUT.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
