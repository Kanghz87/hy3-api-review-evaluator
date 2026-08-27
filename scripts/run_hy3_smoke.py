"""Run one real Hy3 review plus one Hy3 judge call under the persistent token cap."""

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
from hy3_api_review_evaluator.export import build_json_export
from hy3_api_review_evaluator.hy3_client import Hy3Client
from hy3_api_review_evaluator.models import Focus
from hy3_api_review_evaluator.reviewer import review_spec
from hy3_api_review_evaluator.spec_loader import load_spec_bytes

ROOT = Path(__file__).parents[1]
SPEC_PATH = ROOT / "datasets" / "specs" / "medium-13-prompt-injection.yaml"
OUTPUT = ROOT / "results" / "hy3-smoke.json"
LEDGER = ROOT / "results" / "private" / "token-ledger.json"


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if OUTPUT.exists() and not args.force:
        existing = json.loads(OUTPUT.read_text(encoding="utf-8"))
        return {
            "status": "already_complete",
            "result_path": OUTPUT.relative_to(ROOT).as_posix(),
            "total_score": existing["evaluation"]["total_score"],
            "note": "Use --force only when an intentional paid rerun is required.",
        }

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
    spec = load_spec_bytes(SPEC_PATH.read_bytes(), SPEC_PATH.name, settings)
    report = await review_spec(
        spec,
        focus=Focus.SECURITY,
        max_model_chars=settings.max_model_chars,
        client=client,
    )
    evaluation = await evaluate_report_hybrid(
        spec,
        report,
        max_model_chars=settings.max_model_chars,
        client=client,
    )
    payload = json.loads(build_json_export(spec, report, evaluation))
    payload["experiment"] = {
        "status": "complete",
        "kind": "real_hy3_review_and_judge_smoke",
        "completed_at": datetime.now(UTC).isoformat(),
        "token_budget": ledger.safe_snapshot(),
    }
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)
    return {
        "status": "complete",
        "result_path": OUTPUT.relative_to(ROOT).as_posix(),
        "finding_count": len(report.findings),
        "total_score": evaluation.total_score,
        "review_tokens": report.usage.total_tokens,
        "judge_tokens": evaluation.judge_usage.total_tokens,
        "token_budget": ledger.safe_snapshot(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-token-budget", type=int, default=80_000)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Intentionally repeat the paid smoke even when a result already exists.",
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
