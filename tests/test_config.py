from __future__ import annotations

from pathlib import Path

import pytest

from hy3_api_review_evaluator.config import Settings
from hy3_api_review_evaluator.errors import ConfigurationError


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HY3_API_KEY",
        "HY3_BASE_URL",
        "HY3_MODEL",
        "HY3_MAX_RETRIES",
        "HY3_REASONING_EFFORT",
        "HY3_TOTAL_TOKEN_BUDGET",
        "HY3_DEFAULT_RUN_TOKEN_BUDGET",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_hy3_and_budget_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    settings = Settings.from_env(env_file=Path("does-not-exist.env"))
    assert settings.model == "hy3"
    assert settings.base_url == "https://tokenhub.tencentmaas.com/v1"
    assert settings.total_token_budget == 850_000
    assert settings.max_retries == 0
    assert settings.api_key is None


def test_rejects_non_hy3_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("HY3_MODEL", "other-model")
    with pytest.raises(ConfigurationError, match="substitution"):
        Settings.from_env(env_file=Path("does-not-exist.env"))


def test_rejects_budget_above_authorized_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("HY3_TOTAL_TOKEN_BUDGET", "850001")
    with pytest.raises(ConfigurationError, match="HY3_TOTAL_TOKEN_BUDGET"):
        Settings.from_env(env_file=Path("does-not-exist.env"))


def test_rejects_automatic_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("HY3_MAX_RETRIES", "1")
    with pytest.raises(ConfigurationError, match="HY3_MAX_RETRIES"):
        Settings.from_env(env_file=Path("does-not-exist.env"))


def test_safe_summary_never_contains_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("HY3_API_KEY", "test-only-secret-value")
    settings = Settings.from_env(env_file=Path("does-not-exist.env"))
    assert "test-only-secret-value" not in str(settings.safe_summary())
