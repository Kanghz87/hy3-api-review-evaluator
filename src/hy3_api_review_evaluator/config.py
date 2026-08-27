"""Validated environment-only configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

from .errors import ConfigurationError

OFFICIAL_HY3_BASE_URL = "https://tokenhub.tencentmaas.com/v1"
OFFICIAL_HY3_MODEL = "hy3"


def _read_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _read_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings whose diagnostic representation never includes the API key."""

    api_key: str | None
    base_url: str
    model: str
    timeout_seconds: float
    max_retries: int
    reasoning_effort: str
    max_file_bytes: int
    max_container_nodes: int
    max_nesting_depth: int
    max_model_chars: int
    max_output_tokens: int
    total_token_budget: int
    default_run_token_budget: int

    @classmethod
    def from_env(cls, *, env_file: Path | None = None) -> Settings:
        load_dotenv(dotenv_path=env_file, override=False)
        base_url = os.getenv("HY3_BASE_URL", OFFICIAL_HY3_BASE_URL).strip().rstrip("/")
        try:
            parsed = urlsplit(base_url)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as exc:
            raise ConfigurationError("HY3_BASE_URL must be a valid URL") from exc
        if parsed.scheme.lower() != "https" or not hostname:
            raise ConfigurationError("HY3_BASE_URL must be an absolute HTTPS URL")
        if parsed.username or parsed.password:
            raise ConfigurationError("HY3_BASE_URL must not contain credentials")

        model = os.getenv("HY3_MODEL", OFFICIAL_HY3_MODEL).strip()
        if model != OFFICIAL_HY3_MODEL:
            raise ConfigurationError("HY3_MODEL must be 'hy3'; model substitution is not allowed")

        reasoning_effort = os.getenv("HY3_REASONING_EFFORT", "high").strip().lower()
        if reasoning_effort not in {"no_think", "low", "high"}:
            raise ConfigurationError("HY3_REASONING_EFFORT must be one of: no_think, low, high")

        raw_key = os.getenv("HY3_API_KEY")
        api_key = raw_key.strip() if raw_key and raw_key.strip() else None
        total_budget = _read_int("HY3_TOTAL_TOKEN_BUDGET", 850_000, 1_000, 850_000)
        default_budget = _read_int("HY3_DEFAULT_RUN_TOKEN_BUDGET", 150_000, 1_000, total_budget)
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=_read_float("HY3_TIMEOUT_SECONDS", 90.0, 1.0, 300.0),
            # Automatic retries can turn one reservation into multiple provider calls.
            # Keep them disabled so the persistent ledger remains a real hard cap.
            max_retries=_read_int("HY3_MAX_RETRIES", 0, 0, 0),
            reasoning_effort=reasoning_effort,
            max_file_bytes=_read_int("HY3_MAX_FILE_BYTES", 2_000_000, 1_024, 10_000_000),
            max_container_nodes=_read_int("HY3_MAX_CONTAINER_NODES", 200_000, 1_000, 1_000_000),
            max_nesting_depth=_read_int("HY3_MAX_NESTING_DEPTH", 100, 10, 200),
            max_model_chars=_read_int("HY3_MAX_MODEL_CHARS", 120_000, 4_000, 500_000),
            max_output_tokens=_read_int("HY3_MAX_OUTPUT_TOKENS", 16_000, 256, 32_000),
            total_token_budget=total_budget,
            default_run_token_budget=default_budget,
        )

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ConfigurationError(
                "HY3_API_KEY is not set. Put it in the local .env file or process environment."
            )
        return self.api_key

    def safe_summary(self) -> dict[str, object]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key_present": bool(self.api_key),
            "timeout_seconds": self.timeout_seconds,
            "max_file_bytes": self.max_file_bytes,
            "total_token_budget": self.total_token_budget,
            "default_run_token_budget": self.default_run_token_budget,
        }
