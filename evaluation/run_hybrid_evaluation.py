"""Run resumable Hy3-judge evaluation over all 60 public dataset records."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hy3_api_review_evaluator.annotation import (
    load_annotation_protocol,
    validate_complete_annotations,
)
from hy3_api_review_evaluator.budget import TokenBudgetLedger
from hy3_api_review_evaluator.config import Settings
from hy3_api_review_evaluator.errors import EvaluatorError
from hy3_api_review_evaluator.evaluator import evaluate_report_hybrid
from hy3_api_review_evaluator.hy3_client import Hy3Client
from hy3_api_review_evaluator.metrics import (
    mean_absolute_error,
    spearman_correlation,
    strict_ranking_accuracy,
)
from hy3_api_review_evaluator.models import ReviewReport
from hy3_api_review_evaluator.spec_loader import load_spec_bytes

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "datasets" / "manifest.jsonl"
PROTOCOL = ROOT / "datasets" / "annotation_protocol.json"
OUTPUT = ROOT / "results" / "hybrid-records.jsonl"
SUMMARY = ROOT / "results" / "hybrid-summary.json"
LEDGER = ROOT / "results" / "private" / "token-ledger.json"
TIER_ORDINAL = {"bad": 1.0, "medium": 2.0, "good": 3.0}
PILOT_RECORD_IDS = (
    "easy-02-plaintext-http-good",
    "easy-04-missing-path-parameter-medium",
    "medium-13-prompt-injection-bad",
    "medium-14-secret-redaction-good",
    "hard-18-terminology-stuffing-medium",
    "hard-20-fabricated-endpoint-bad",
)


def _manifest() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _existing() -> dict[str, dict[str, Any]]:
    if not OUTPUT.exists():
        return {}
    return {
        item["record_id"]: item
        for item in (
            json.loads(line)
            for line in OUTPUT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }


def _complete_annotations(manifest: list[dict[str, Any]]) -> dict[str, float] | None:
    public = ROOT / "datasets" / "annotations" / "human_scores.csv"
    local = ROOT / "datasets" / "annotations" / "human_scores.local.csv"
    path = public if public.exists() else local
    if not path.exists():
        return None
    selected = set(load_annotation_protocol(PROTOCOL, manifest)["selected_record_ids"])
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        observed_ids = {row["record_id"] for row in csv.DictReader(stream) if row.get("record_id")}
    if observed_ids != selected:
        return None
    rows = validate_complete_annotations(path, selected)
    return {row["record_id"]: float(row["manual_total"]) for row in rows}


def _record_row(record: dict[str, Any], result: Any) -> dict[str, Any]:
    return {
        "record_id": record["record_id"],
        "scenario_id": record["scenario_id"],
        "reference_tier": record["reference_tier"],
        "difficulty": record["difficulty"],
        "is_adversarial": record["is_adversarial"],
        "report_adversarial": record["report_adversarial"],
        "adversarial_type": record["adversarial_type"],
        "evaluated_at": datetime.now(UTC).isoformat(),
        "evaluation": result.model_dump(mode="json"),
    }


def _summarize(
    manifest: list[dict[str, Any]],
    completed: dict[str, dict[str, Any]],
    ledger: TokenBudgetLedger,
) -> dict[str, Any]:
    all_complete = len(completed) == len(manifest)
    rankings: dict[str, dict[str, float]] = defaultdict(dict)
    rows: list[dict[str, Any]] = []
    for record in manifest:
        output = completed.get(record["record_id"])
        if output is None:
            continue
        evaluation = output["evaluation"]
        score = float(evaluation["total_score"])
        rankings[record["scenario_id"]][record["reference_tier"]] = score
        rows.append(
            {
                **record,
                "total_score": score,
                "severe_failure": bool(evaluation["severe_failure"]),
            }
        )

    ranking_accuracy: float | None = None
    ranking_failures: list[str] | None = None
    if all_complete:
        ranking_accuracy, ranking_failures = strict_ranking_accuracy(rankings)
    tier_spearman = (
        spearman_correlation(
            [TIER_ORDINAL[row["reference_tier"]] for row in rows],
            [row["total_score"] for row in rows],
        )
        if all_complete
        else None
    )
    adversarial_rows = [row for row in rows if row["report_adversarial"]]
    detected = [row for row in adversarial_rows if row["severe_failure"] or row["total_score"] < 65]
    annotations = _complete_annotations(manifest)
    reported_judge_tokens = [
        int(output["evaluation"]["judge_usage"]["total_tokens"])
        for output in completed.values()
        if int(output["evaluation"]["judge_usage"]["total_tokens"]) > 0
    ]
    mean_judge_tokens = (
        sum(reported_judge_tokens) / len(reported_judge_tokens) if reported_judge_tokens else None
    )
    human_spearman = None
    human_mae = None
    if annotations is not None and all_complete:
        ordered = [row for row in rows if row["record_id"] in annotations]
        automatic = [row["total_score"] for row in ordered]
        human = [annotations[row["record_id"]] for row in ordered]
        human_spearman = spearman_correlation(automatic, human)
        human_mae = mean_absolute_error(automatic, human)

    by_difficulty: dict[str, dict[str, float | None]] = {}
    for difficulty in ("easy", "medium", "hard"):
        by_difficulty[difficulty] = {}
        for tier in ("good", "medium", "bad"):
            selected = [
                row["total_score"]
                for row in rows
                if row["difficulty"] == difficulty and row["reference_tier"] == tier
            ]
            by_difficulty[difficulty][tier] = (
                round(sum(selected) / len(selected), 2) if selected else None
            )

    return {
        "status": ("complete" if all_complete and annotations is not None else "preliminary"),
        "mode": "hybrid",
        "generated_at": datetime.now(UTC).isoformat(),
        "expected_record_count": len(manifest),
        "completed_record_count": len(completed),
        "strict_good_medium_bad_ranking_accuracy": ranking_accuracy,
        "ranking_failure_scenarios": ranking_failures,
        "tier_score_spearman": tier_spearman,
        "human_score_spearman": human_spearman,
        "human_score_mae": human_mae,
        "repeat_judge_score_std": None,
        "adversarial_detection_rate": (
            len(detected) / len(adversarial_rows) if adversarial_rows else None
        ),
        "adversarial_record_count": len(adversarial_rows),
        "by_difficulty_mean_score": by_difficulty,
        "observed_judge_token_count": sum(reported_judge_tokens),
        "mean_reported_judge_tokens": (
            round(mean_judge_tokens, 2) if mean_judge_tokens is not None else None
        ),
        "projected_remaining_judge_tokens": (
            round(mean_judge_tokens * (len(manifest) - len(completed)))
            if mean_judge_tokens is not None
            else None
        ),
        "token_budget": ledger.safe_snapshot(),
        "human_annotation_complete": annotations is not None,
        "human_annotation_scope_count": len(annotations) if annotations is not None else 0,
        "human_annotation_protocol": "datasets/annotation_protocol.json",
        "notes": [
            "Construction-tier metrics are not substitutes for human agreement.",
            "Human agreement uses the frozen stratified subset, not all 60 records.",
            "The status remains preliminary until all automated records and protocol labels exist.",
        ],
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    settings = Settings.from_env(env_file=ROOT / ".env")
    settings.require_api_key()
    if not 1_000 <= args.run_token_budget <= settings.total_token_budget:
        raise ValueError("--run-token-budget must be within the configured total budget")
    ledger = TokenBudgetLedger(
        LEDGER,
        total_limit=settings.total_token_budget,
        run_limit=args.run_token_budget,
    )
    client = Hy3Client(settings, ledger)
    manifest = _manifest()
    if args.pilot:
        available_ids = {record["record_id"] for record in manifest}
        missing_pilot_ids = sorted(set(PILOT_RECORD_IDS) - available_ids)
        if missing_pilot_ids:
            raise RuntimeError(f"Pilot record IDs are missing: {missing_pilot_ids}")
    completed = _existing()
    pending = [record for record in manifest if record["record_id"] not in completed]
    if args.pilot:
        pilot_ids = set(PILOT_RECORD_IDS)
        pending = [record for record in pending if record["record_id"] in pilot_ids]
    elif args.max_records is not None:
        pending = pending[: args.max_records]
    spec_cache: dict[str, Any] = {}
    for index, record in enumerate(pending, start=1):
        spec_path = ROOT / record["spec_path"]
        if record["spec_path"] not in spec_cache:
            spec_cache[record["spec_path"]] = load_spec_bytes(
                spec_path.read_bytes(), spec_path.name, settings
            )
        spec = spec_cache[record["spec_path"]]
        report = ReviewReport.model_validate_json(
            (ROOT / record["report_path"]).read_text(encoding="utf-8")
        )
        result = await evaluate_report_hybrid(
            spec,
            report,
            max_model_chars=settings.max_model_chars,
            client=client,
        )
        output = _record_row(record, result)
        completed[record["record_id"]] = output
        with OUTPUT.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(output, ensure_ascii=False) + "\n")
        print(
            json.dumps(
                {
                    "progress": f"{index}/{len(pending)}",
                    "record_id": record["record_id"],
                    "score": result.total_score,
                    "judge_tokens": result.judge_usage.total_tokens,
                    "budget": ledger.safe_snapshot(),
                },
                ensure_ascii=False,
            )
        )
    summary = _summarize(manifest, completed, ledger)
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-token-budget",
        type=int,
        default=150_000,
        help="Per-process hard cap; never allowed above HY3_TOTAL_TOKEN_BUDGET.",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--pilot",
        action="store_true",
        help="Evaluate six fixed records balanced across tiers, difficulty, and attacks.",
    )
    selection.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Optional smoke-test limit. Existing JSONL records are resumed automatically.",
    )
    return parser


def main() -> None:
    try:
        result = asyncio.run(_run(_parser().parse_args()))
    except EvaluatorError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from None
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error_type": type(exc).__name__},
                ensure_ascii=False,
            )
        )
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
