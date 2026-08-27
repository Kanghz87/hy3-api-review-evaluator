"""Repeat Hy3 judge scoring three times on six fixed records and measure score variance."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hy3_api_review_evaluator.budget import TokenBudgetLedger
from hy3_api_review_evaluator.config import Settings
from hy3_api_review_evaluator.errors import EvaluatorError
from hy3_api_review_evaluator.evaluator import evaluate_report_hybrid
from hy3_api_review_evaluator.hy3_client import Hy3Client
from hy3_api_review_evaluator.metrics import repeated_score_std
from hy3_api_review_evaluator.models import ReviewReport
from hy3_api_review_evaluator.spec_loader import load_spec_bytes

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "datasets" / "manifest.jsonl"
OUTPUT = ROOT / "results" / "stability-records.jsonl"
SUMMARY = ROOT / "results" / "stability-summary.json"
LEDGER = ROOT / "results" / "private" / "token-ledger.json"
SELECTED_RECORDS = (
    "easy-01-missing-description-good",
    "easy-01-missing-description-bad",
    "medium-13-prompt-injection-good",
    "medium-13-prompt-injection-bad",
    "hard-18-terminology-stuffing-good",
    "hard-18-terminology-stuffing-bad",
)


def _manifest_by_id() -> dict[str, dict[str, Any]]:
    return {
        item["record_id"]: item
        for item in (
            json.loads(line)
            for line in MANIFEST.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }


def _existing() -> dict[str, dict[str, Any]]:
    if not OUTPUT.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    for line in OUTPUT.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            result[f"{item['record_id']}::{item['repeat_index']}"] = item
    return result


def _summarize(
    completed: dict[str, dict[str, Any]],
    *,
    repeats: int,
    ledger: TokenBudgetLedger,
) -> dict[str, Any]:
    per_record: dict[str, dict[str, Any]] = {}
    standard_deviations: list[float] = []
    for record_id in SELECTED_RECORDS:
        scores = [
            completed[f"{record_id}::{index}"]["total_score"]
            for index in range(1, repeats + 1)
            if f"{record_id}::{index}" in completed
        ]
        deviation = repeated_score_std(scores)
        if deviation is not None:
            standard_deviations.append(deviation)
        per_record[record_id] = {
            "scores": scores,
            "mean": round(sum(scores) / len(scores), 4) if scores else None,
            "population_std": round(deviation, 4) if deviation is not None else None,
        }
    expected = len(SELECTED_RECORDS) * repeats
    return {
        "status": "complete" if len(completed) >= expected else "preliminary",
        "analysis_status": "preliminary",
        "generated_at": datetime.now(UTC).isoformat(),
        "repeat_count": repeats,
        "selected_record_count": len(SELECTED_RECORDS),
        "expected_evaluation_count": expected,
        "completed_evaluation_count": len(completed),
        "per_record": per_record,
        "mean_population_std": (
            round(sum(standard_deviations) / len(standard_deviations), 4)
            if standard_deviations
            else None
        ),
        "max_population_std": (round(max(standard_deviations), 4) if standard_deviations else None),
        "token_budget": ledger.safe_snapshot(),
        "notes": [
            "Every repeat evaluates the identical stored report and OpenAPI document.",
            "Overall analysis remains preliminary until human annotation is complete.",
        ],
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    settings = Settings.from_env(env_file=ROOT / ".env")
    settings.require_api_key()
    if not 1_000 <= args.run_token_budget <= settings.total_token_budget:
        raise ValueError("--run-token-budget must be within the configured total budget")
    if not 2 <= args.repeats <= 5:
        raise ValueError("--repeats must be between 2 and 5")
    ledger = TokenBudgetLedger(
        LEDGER,
        total_limit=settings.total_token_budget,
        run_limit=args.run_token_budget,
    )
    client = Hy3Client(settings, ledger)
    manifest = _manifest_by_id()
    completed = _existing()
    spec_cache: dict[str, Any] = {}
    for record_id in SELECTED_RECORDS:
        record = manifest[record_id]
        spec_path = ROOT / record["spec_path"]
        if record["spec_path"] not in spec_cache:
            spec_cache[record["spec_path"]] = load_spec_bytes(
                spec_path.read_bytes(), spec_path.name, settings
            )
        spec = spec_cache[record["spec_path"]]
        report = ReviewReport.model_validate_json(
            (ROOT / record["report_path"]).read_text(encoding="utf-8")
        )
        for repeat_index in range(1, args.repeats + 1):
            key = f"{record_id}::{repeat_index}"
            if key in completed:
                continue
            evaluation = await evaluate_report_hybrid(
                spec,
                report,
                max_model_chars=settings.max_model_chars,
                client=client,
            )
            item = {
                "record_id": record_id,
                "repeat_index": repeat_index,
                "evaluated_at": datetime.now(UTC).isoformat(),
                "total_score": evaluation.total_score,
                "dimension_scores": {
                    score.name: score.final_score for score in evaluation.dimension_scores
                },
                "judge_usage": evaluation.judge_usage.model_dump(mode="json"),
            }
            completed[key] = item
            with OUTPUT.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")
            print(
                json.dumps(
                    {
                        "record_id": record_id,
                        "repeat_index": repeat_index,
                        "total_score": evaluation.total_score,
                        "budget": ledger.safe_snapshot(),
                    },
                    ensure_ascii=False,
                )
            )
    summary = _summarize(completed, repeats=args.repeats, ledger=ledger)
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--run-token-budget", type=int, default=150_000)
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
