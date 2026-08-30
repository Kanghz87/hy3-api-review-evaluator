"""Persistent, secret-free accounting for the user-authorized Hy3 token budget."""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
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
    reservation_id: str
    tokens: int


_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    """Serialize ledger transactions across threads and local processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    thread_lock = _thread_lock(lock_path)
    with thread_lock, lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


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
        if self.path is None:
            self._state = self._load_unlocked()
        else:
            with _exclusive_file_lock(self.path):
                self._state = self._load_unlocked()

    def _load_unlocked(self) -> dict[str, Any]:
        default: dict[str, Any] = {
            "version": LEDGER_VERSION,
            "used_tokens": 0,
            "call_count": 0,
            "entries": [],
            "reservations": [],
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
        reservations = value.setdefault("reservations", [])
        if not isinstance(reservations, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("reservation_id"), str)
            or not isinstance(item.get("tokens"), int)
            or item["tokens"] <= 0
            for item in reservations
        ):
            raise BudgetExceededError(
                "The token ledger has invalid reservations; refusing Hy3 calls"
            )
        return value

    @property
    def total_used(self) -> int:
        return int(self._state["used_tokens"])

    def reserve(self, *, prompt: str, max_output_tokens: int) -> Reservation:
        # UTF-8 bytes are a deliberately conservative prompt-token upper bound.
        amount = len(prompt.encode("utf-8")) + max_output_tokens + MESSAGE_OVERHEAD_RESERVE
        reservation = Reservation(reservation_id=uuid.uuid4().hex, tokens=amount)
        with self._locked_state():
            persisted_reserved = sum(item["tokens"] for item in self._state["reservations"])
            if self.total_used + persisted_reserved + amount > self.total_limit:
                raise BudgetExceededError(
                    "Hy3 call refused: its conservative reservation would exceed "
                    "the total token budget"
                )
            if self.run_used + self._reserved + amount > self.run_limit:
                raise BudgetExceededError(
                    "Hy3 call refused: its conservative reservation would exceed this run's "
                    "token budget"
                )
            self._state["reservations"].append(
                {
                    "reservation_id": reservation.reservation_id,
                    "tokens": reservation.tokens,
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
            self._reserved += amount
            self._persist_unlocked()
        return reservation

    def release(self, reservation: Reservation) -> None:
        with self._locked_state():
            self._reserved = max(0, self._reserved - reservation.tokens)
            original_count = len(self._state["reservations"])
            self._state["reservations"] = [
                item
                for item in self._state["reservations"]
                if item["reservation_id"] != reservation.reservation_id
            ]
            if len(self._state["reservations"]) != original_count:
                self._persist_unlocked()

    def commit(self, reservation: Reservation, usage: Usage, *, purpose: str) -> None:
        actual = usage.total_tokens
        charged = actual if actual > 0 else reservation.tokens
        with self._locked_state():
            reservation_ids = {item["reservation_id"] for item in self._state["reservations"]}
            if reservation.reservation_id not in reservation_ids:
                raise BudgetExceededError(
                    "The token reservation is missing; refusing further calls"
                )
            self._reserved = max(0, self._reserved - reservation.tokens)
            self.run_used += charged
            self._state["reservations"] = [
                item
                for item in self._state["reservations"]
                if item["reservation_id"] != reservation.reservation_id
            ]
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
            self._persist_unlocked()
        if charged > reservation.tokens:
            raise BudgetExceededError(
                "Provider usage exceeded the conservative reservation; usage was recorded and "
                "this call failed safely"
            )

    @contextmanager
    def _locked_state(self) -> Iterator[None]:
        if self.path is None:
            yield
            return
        with _exclusive_file_lock(self.path):
            self._state = self._load_unlocked()
            yield

    def _persist_unlocked(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.path)

    def safe_snapshot(self) -> dict[str, int]:
        with self._locked_state():
            reserved = sum(item["tokens"] for item in self._state["reservations"])
            return {
                "total_limit": self.total_limit,
                "total_used": self.total_used,
                "total_remaining": max(0, self.total_limit - self.total_used - reserved),
                "reserved_tokens": reserved,
                "run_limit": self.run_limit,
                "run_used": self.run_used,
                "run_remaining": max(0, self.run_limit - self.run_used - self._reserved),
                "call_count": int(self._state["call_count"]),
            }
