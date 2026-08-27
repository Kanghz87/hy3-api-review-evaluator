"""Persistent, secret-free accounting for the user-authorized Hy3 token budget."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import BudgetExceededError
from .models import Usage

LEDGER_VERSION = 1
MESSAGE_OVERHEAD_RESERVE = 512


@dataclass(frozen=True, slots=True)
class Reservation:
    tokens: int


class TokenBudgetLedger:
    """Refuse calls before their conservative maximum could cross either budget cap."""

    def __init__(self, path: Path | None, *, total_limit: int, run_limit: int) -> None:
        if run_limit > total_limit:
            raise ValueError("run_limit cannot exceed total_limit")
        self.path = path
        self.total_limit = total_limit
        self.run_limit = run_limit
        self.run_used = 0
        self._reserved = 0
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        default: dict[str, Any] = {
            "version": LEDGER_VERSION,
            "used_tokens": 0,
            "call_count": 0,
            "entries": [],
        }
        if self.path is None or not self.path.exists():
            return default
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BudgetExceededError("The token ledger is unreadable; refusing Hy3 calls") from exc
        if (
            not isinstance(value, dict)
            or value.get("version") != LEDGER_VERSION
            or not isinstance(value.get("used_tokens"), int)
            or not isinstance(value.get("call_count"), int)
            or not isinstance(value.get("entries"), list)
        ):
            raise BudgetExceededError("The token ledger has an invalid schema; refusing Hy3 calls")
        return value

    @property
    def total_used(self) -> int:
        return int(self._state["used_tokens"])

    def reserve(self, *, prompt: str, max_output_tokens: int) -> Reservation:
        # UTF-8 bytes are a deliberately conservative prompt-token upper bound.
        amount = len(prompt.encode("utf-8")) + max_output_tokens + MESSAGE_OVERHEAD_RESERVE
        if self.total_used + self._reserved + amount > self.total_limit:
            raise BudgetExceededError(
                "Hy3 call refused: its conservative reservation would exceed the total token budget"
            )
        if self.run_used + self._reserved + amount > self.run_limit:
            raise BudgetExceededError(
                "Hy3 call refused: its conservative reservation would exceed this run's "
                "token budget"
            )
        self._reserved += amount
        return Reservation(tokens=amount)

    def release(self, reservation: Reservation) -> None:
        self._reserved = max(0, self._reserved - reservation.tokens)

    def commit(self, reservation: Reservation, usage: Usage, *, purpose: str) -> None:
        self.release(reservation)
        actual = usage.total_tokens
        charged = actual if actual > 0 else reservation.tokens
        if charged > reservation.tokens:
            raise BudgetExceededError(
                "Provider usage exceeded the conservative reservation; refusing further calls"
            )
        self.run_used += charged
        self._state["used_tokens"] = self.total_used + charged
        self._state["call_count"] = int(self._state["call_count"]) + 1
        self._state["entries"].append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "purpose": purpose[:100],
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "reported_total_tokens": usage.total_tokens,
                "charged_tokens": charged,
            }
        )
        self._persist()

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.path)

    def safe_snapshot(self) -> dict[str, int]:
        return {
            "total_limit": self.total_limit,
            "total_used": self.total_used,
            "total_remaining": max(0, self.total_limit - self.total_used),
            "run_limit": self.run_limit,
            "run_used": self.run_used,
            "run_remaining": max(0, self.run_limit - self.run_used),
            "call_count": int(self._state["call_count"]),
        }
