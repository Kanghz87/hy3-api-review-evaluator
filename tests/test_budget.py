from __future__ import annotations

import json
from pathlib import Path

import pytest

from hy3_api_review_evaluator.budget import TokenBudgetLedger
from hy3_api_review_evaluator.errors import BudgetExceededError
from hy3_api_review_evaluator.models import Usage


def test_budget_records_only_usage_metadata() -> None:
    path = Path("results/test-token-ledger.json")
    lock_path = path.with_suffix(path.suffix + ".lock")
    path.unlink(missing_ok=True)
    lock_path.unlink(missing_ok=True)
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
        lock_path.unlink(missing_ok=True)


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


def test_provider_usage_above_reservation_is_recorded_before_failure() -> None:
    ledger = TokenBudgetLedger(None, total_limit=10_000, run_limit=10_000)
    reservation = ledger.reserve(prompt="", max_output_tokens=100)
    with pytest.raises(BudgetExceededError, match="usage was recorded"):
        ledger.commit(
            reservation,
            Usage(prompt_tokens=400, completion_tokens=300, total_tokens=700),
            purpose="over-reservation",
        )
    snapshot = ledger.safe_snapshot()
    assert snapshot["total_used"] == 700
    assert snapshot["reserved_tokens"] == 0


def test_concurrent_ledgers_preserve_both_commits() -> None:
    path = Path("results/test-concurrent-token-ledger.json")
    lock_path = path.with_suffix(path.suffix + ".lock")
    path.unlink(missing_ok=True)
    lock_path.unlink(missing_ok=True)
    try:
        first = TokenBudgetLedger(path, total_limit=10_000, run_limit=10_000)
        second = TokenBudgetLedger(path, total_limit=10_000, run_limit=10_000)
        first_reservation = first.reserve(prompt="first", max_output_tokens=100)
        second_reservation = second.reserve(prompt="second", max_output_tokens=100)
        first.commit(
            first_reservation,
            Usage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
            purpose="first",
        )
        second.commit(
            second_reservation,
            Usage(prompt_tokens=40, completion_tokens=10, total_tokens=50),
            purpose="second",
        )
        persisted = json.loads(path.read_text(encoding="utf-8"))
        assert persisted["used_tokens"] == 80
        assert persisted["call_count"] == 2
        assert persisted["reservations"] == []
    finally:
        path.unlink(missing_ok=True)
        lock_path.unlink(missing_ok=True)


def test_persisted_reservation_blocks_competing_call_before_request() -> None:
    path = Path("results/test-reserved-token-ledger.json")
    lock_path = path.with_suffix(path.suffix + ".lock")
    path.unlink(missing_ok=True)
    lock_path.unlink(missing_ok=True)
    try:
        first = TokenBudgetLedger(path, total_limit=1_100, run_limit=1_100)
        second = TokenBudgetLedger(path, total_limit=1_100, run_limit=1_100)
        reservation = first.reserve(prompt="", max_output_tokens=100)
        with pytest.raises(BudgetExceededError, match="total token budget"):
            second.reserve(prompt="", max_output_tokens=100)
        first.release(reservation)
        assert second.safe_snapshot()["reserved_tokens"] == 0
    finally:
        path.unlink(missing_ok=True)
        lock_path.unlink(missing_ok=True)


def test_active_reservation_counts_toward_same_run_limit() -> None:
    ledger = TokenBudgetLedger(None, total_limit=10_000, run_limit=1_100)
    reservation = ledger.reserve(prompt="", max_output_tokens=100)
    with pytest.raises(BudgetExceededError, match="run's token budget"):
        ledger.reserve(prompt="", max_output_tokens=100)
    ledger.release(reservation)
    assert ledger.safe_snapshot()["reserved_tokens"] == 0
