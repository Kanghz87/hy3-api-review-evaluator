from __future__ import annotations

import json
from pathlib import Path

import pytest

from hy3_api_review_evaluator.budget import TokenBudgetLedger
from hy3_api_review_evaluator.errors import BudgetExceededError
from hy3_api_review_evaluator.models import Usage


def test_budget_records_only_usage_metadata() -> None:
    path = Path("results/test-token-ledger.json")
    path.unlink(missing_ok=True)
    try:
        ledger = TokenBudgetLedger(path, total_limit=10_000, run_limit=5_000)
        reservation = ledger.reserve(prompt="small prompt", max_output_tokens=100)
        ledger.commit(
            reservation,
            Usage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
            purpose="unit-test",
        )
        assert ledger.safe_snapshot()["total_used"] == 30
        persisted = json.loads(path.read_text(encoding="utf-8"))
        assert persisted["entries"][0]["purpose"] == "unit-test"
        assert "small prompt" not in path.read_text(encoding="utf-8")
    finally:
        path.unlink(missing_ok=True)


def test_budget_refuses_call_before_total_or_run_cap() -> None:
    total = TokenBudgetLedger(None, total_limit=1_000, run_limit=1_000)
    with pytest.raises(BudgetExceededError, match="total token budget"):
        total.reserve(prompt="x" * 600, max_output_tokens=100)

    run = TokenBudgetLedger(None, total_limit=10_000, run_limit=1_000)
    with pytest.raises(BudgetExceededError, match="run's token budget"):
        run.reserve(prompt="x" * 600, max_output_tokens=100)


def test_missing_provider_usage_consumes_conservative_reservation() -> None:
    ledger = TokenBudgetLedger(None, total_limit=10_000, run_limit=10_000)
    reservation = ledger.reserve(prompt="prompt", max_output_tokens=100)
    ledger.commit(reservation, Usage(), purpose="missing-usage")
    assert ledger.total_used == reservation.tokens
