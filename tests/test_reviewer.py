from __future__ import annotations

import json

import pytest
from test_spec_loader import VALID

from hy3_api_review_evaluator.config import Settings
from hy3_api_review_evaluator.errors import StructuredOutputError
from hy3_api_review_evaluator.hy3_client import ModelReply
from hy3_api_review_evaluator.models import Focus, Usage
from hy3_api_review_evaluator.reviewer import review_spec
from hy3_api_review_evaluator.spec_loader import load_spec_text


class FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[tuple[str, str, str]] = []

    async def complete(self, *, system: str, user: str, purpose: str) -> ModelReply:
        self.calls.append((system, user, purpose))
        return ModelReply(
            content=self.content,
            usage=Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )


def _valid_payload() -> str:
    return json.dumps(
        {
            "executive_summary": "The contract needs a small reliability improvement.",
            "findings": [
                {
                    "finding_id": "hy3-health-errors",
                    "title": "Error response is absent",
                    "category": "reliability_semantics",
                    "severity": "low",
                    "location": "#/paths/~1health/get/responses",
                    "evidence": [
                        {
                            "pointer": "#/paths/~1health/get/responses",
                            "quote": '"200"',
                            "description": "Only a success response is present.",
                        }
                    ],
                    "rationale": "Consumers cannot infer a stable error payload.",
                    "suggestion": "Add a default error response that references an Error schema.",
                    "source": "hy3",
                    "confidence": 0.9,
                }
            ],
            "limitations": [],
        }
    )


@pytest.mark.asyncio
async def test_review_merges_local_and_validated_hy3_findings(settings: Settings) -> None:
    spec = load_spec_text(VALID, "demo.yaml", settings)
    client = FakeClient(_valid_payload())
    report = await review_spec(
        spec,
        focus=Focus.RELIABILITY,
        max_model_chars=settings.max_model_chars,
        client=client,
    )
    assert report.model == "hy3"
    assert report.usage.total_tokens == 150
    assert {finding.source for finding in report.findings} == {"deterministic", "hy3"}
    system, user, purpose = client.calls[0]
    assert "untrusted data" in system
    assert "UNTRUSTED_OPENAPI_DATA" in user
    assert purpose == "openapi-review"


@pytest.mark.asyncio
async def test_review_rejects_invalid_or_mislabelled_output(settings: Settings) -> None:
    spec = load_spec_text(VALID, "demo.yaml", settings)
    with pytest.raises(StructuredOutputError, match="invalid JSON"):
        await review_spec(
            spec,
            focus=Focus.ALL,
            max_model_chars=settings.max_model_chars,
            client=FakeClient("not json"),
        )

    payload = json.loads(_valid_payload())
    payload["findings"][0]["source"] = "deterministic"
    with pytest.raises(StructuredOutputError, match="source or identifier"):
        await review_spec(
            spec,
            focus=Focus.ALL,
            max_model_chars=settings.max_model_chars,
            client=FakeClient(json.dumps(payload)),
        )


@pytest.mark.asyncio
async def test_review_normalizes_provider_short_id_and_single_limitation(
    settings: Settings,
) -> None:
    payload = json.loads(_valid_payload())
    payload["findings"][0]["finding_id"] = "hy3-1"
    payload["limitations"] = "Only the supplied projection was reviewed."
    spec = load_spec_text(VALID, "demo.yaml", settings)
    report = await review_spec(
        spec,
        focus=Focus.ALL,
        max_model_chars=settings.max_model_chars,
        client=FakeClient(json.dumps(payload)),
    )
    assert report.findings[-1].finding_id == "hy3-1"
    assert report.limitations == ["Only the supplied projection was reviewed."]


@pytest.mark.asyncio
async def test_review_suppresses_semantic_duplicate_of_local_finding(settings: Settings) -> None:
    payload = json.loads(_valid_payload())
    payload["findings"][0].update(
        {
            "title": "No error response is documented for GET /health",
            "category": "response_contract",
        }
    )
    spec = load_spec_text(VALID, "demo.yaml", settings)
    report = await review_spec(
        spec,
        focus=Focus.ALL,
        max_model_chars=settings.max_model_chars,
        client=FakeClient(json.dumps(payload)),
    )
    assert all(finding.finding_id != "hy3-health-errors" for finding in report.findings)


@pytest.mark.asyncio
async def test_review_prompt_never_contains_uploaded_bearer_secret(
    settings: Settings,
) -> None:
    secret = "abcdefghijklmnop"
    spec = load_spec_text(
        VALID.replace("description: ok", f"description: 'Bearer {secret}'"),
        "secret.yaml",
        settings,
    )
    client = FakeClient(_valid_payload())
    await review_spec(
        spec,
        focus=Focus.ALL,
        max_model_chars=settings.max_model_chars,
        client=client,
    )
    assert secret not in client.calls[0][1]
    assert "[REDACTED]" in client.calls[0][1]


@pytest.mark.asyncio
async def test_review_prompt_redacts_secret_in_filename(settings: Settings) -> None:
    secret = "abcdefghijklmnop"
    spec = load_spec_text(VALID, f"Bearer {secret}.yaml", settings)
    client = FakeClient(_valid_payload())
    await review_spec(
        spec,
        focus=Focus.ALL,
        max_model_chars=settings.max_model_chars,
        client=client,
    )
    assert secret not in client.calls[0][1]
