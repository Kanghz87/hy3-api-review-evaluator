from __future__ import annotations

from types import SimpleNamespace

import pytest

import hy3_api_review_evaluator.hy3_client as client_module
from hy3_api_review_evaluator.budget import TokenBudgetLedger
from hy3_api_review_evaluator.config import Settings
from hy3_api_review_evaluator.errors import ProviderError
from hy3_api_review_evaluator.hy3_client import Hy3Client


class _EmptyCompletions:
    async def create(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(choices=[], usage=None)


class _EmptyProvider:
    def __init__(self, **_: object) -> None:
        self.chat = SimpleNamespace(completions=_EmptyCompletions())


class _SuccessfulCompletions:
    async def create(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="  result  "))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8, total_tokens=20),
        )


class _SuccessfulProvider:
    def __init__(self, **_: object) -> None:
        self.chat = SimpleNamespace(completions=_SuccessfulCompletions())


class _FailingCompletions:
    async def create(self, **_: object) -> SimpleNamespace:
        raise RuntimeError("synthetic provider failure")


class _FailingProvider:
    def __init__(self, **_: object) -> None:
        self.chat = SimpleNamespace(completions=_FailingCompletions())


@pytest.mark.asyncio
async def test_successful_provider_usage_is_recorded(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client_module, "AsyncOpenAI", _SuccessfulProvider)
    ledger = TokenBudgetLedger(None, total_limit=20_000, run_limit=20_000)
    reply = await Hy3Client(settings, ledger).complete(
        system="system",
        user="user",
        purpose="success-test",
    )
    assert reply.content == "result"
    assert reply.usage.total_tokens == 20
    assert ledger.safe_snapshot()["total_used"] == 20


@pytest.mark.asyncio
async def test_empty_provider_response_is_conservatively_charged(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client_module, "AsyncOpenAI", _EmptyProvider)
    ledger = TokenBudgetLedger(None, total_limit=20_000, run_limit=20_000)
    client = Hy3Client(settings, ledger)
    with pytest.raises(ProviderError, match="usage was recorded"):
        await client.complete(system="system", user="user", purpose="empty-test")
    snapshot = ledger.safe_snapshot()
    assert snapshot["call_count"] == 1
    assert snapshot["total_used"] > 0
    assert snapshot["reserved_tokens"] == 0


@pytest.mark.asyncio
async def test_unknown_provider_failure_is_conservatively_charged(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client_module, "AsyncOpenAI", _FailingProvider)
    ledger = TokenBudgetLedger(None, total_limit=20_000, run_limit=20_000)
    with pytest.raises(ProviderError, match="failed safely"):
        await Hy3Client(settings, ledger).complete(
            system="system",
            user="user",
            purpose="failure-test",
        )
    snapshot = ledger.safe_snapshot()
    assert snapshot["call_count"] == 1
    assert snapshot["total_used"] > settings.max_output_tokens
    assert snapshot["reserved_tokens"] == 0
