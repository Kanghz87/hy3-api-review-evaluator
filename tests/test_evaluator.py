from __future__ import annotations

import json

import pytest
from test_spec_loader import VALID

from hy3_api_review_evaluator.config import Settings
from hy3_api_review_evaluator.evaluator import (
    evaluate_report_hybrid,
    evaluate_report_locally,
)
from hy3_api_review_evaluator.hy3_client import ModelReply
from hy3_api_review_evaluator.models import (
    EvidenceReference,
    Focus,
    ReviewFinding,
    ReviewReport,
    Usage,
)
from hy3_api_review_evaluator.rubric import DIMENSION_ORDER
from hy3_api_review_evaluator.rules import audit_spec
from hy3_api_review_evaluator.spec_loader import load_spec_text


class FakeJudge:
    def __init__(self, score: int = 4, severe: bool = False) -> None:
        self.score = score
        self.severe = severe
        self.calls: list[tuple[str, str, str]] = []

    async def complete(self, *, system: str, user: str, purpose: str) -> ModelReply:
        self.calls.append((system, user, purpose))
        return ModelReply(
            content=json.dumps(
                {
                    "dimension_scores": [
                        {
                            "name": name,
                            "score": self.score,
                            "reason": "The supplied evidence meets this exact rubric boundary.",
                        }
                        for name in DIMENSION_ORDER
                    ],
                    "severe_failure": self.severe,
                    "severe_failure_reasons": ["Judge gate"] if self.severe else [],
                }
            ),
            usage=Usage(prompt_tokens=200, completion_tokens=100, total_tokens=300),
        )


def _grounded_report(settings: Settings) -> tuple[object, ReviewReport]:
    spec = load_spec_text(VALID, "demo.yaml", settings)
    return spec, ReviewReport(
        specification_title=spec.title,
        openapi_version=spec.version,
        focus=Focus.ALL,
        executive_summary="The report covers every deterministic contract issue.",
        findings=audit_spec(spec),
        limitations=[],
    )


def _fabricated_report(settings: Settings) -> tuple[object, ReviewReport]:
    spec = load_spec_text(VALID, "demo.yaml", settings)
    finding = ReviewFinding(
        finding_id="hy3-fabricated-admin",
        title="Admin endpoint leaks every password",
        category="security",
        severity="critical",
        location="#/paths/~1admin/delete",
        evidence=[
            EvidenceReference(
                pointer="#/paths/~1admin/delete",
                quote="password leak",
                description="Fabricated evidence.",
            )
        ],
        rationale="The nonexistent endpoint allegedly exposes credentials.",
        suggestion="Improve security.",
        source="hy3",
        confidence=1.0,
    )
    return spec, ReviewReport(
        specification_title=spec.title,
        openapi_version=spec.version,
        focus=Focus.SECURITY,
        executive_summary="A fabricated critical issue dominates this report.",
        findings=[finding],
        limitations=[],
    )


def _semantic_mismatch_report(settings: Settings) -> tuple[object, ReviewReport]:
    spec = load_spec_text(VALID, "demo.yaml", settings)
    finding = ReviewFinding(
        finding_id="hy3-semantic-mismatch",
        title="Title proves every operation exposes credentials",
        category="security",
        severity="high",
        location="#/info/title",
        evidence=[
            EvidenceReference(
                pointer="#/info/title",
                quote="Demo",
                description="The quote exists but does not support the security claim.",
            )
        ],
        rationale="The document title allegedly proves a credential disclosure.",
        suggestion="Remove credential disclosure from every operation response.",
        source="hy3",
        confidence=1.0,
    )
    return spec, ReviewReport(
        specification_title=spec.title,
        openapi_version=spec.version,
        focus=Focus.SECURITY,
        executive_summary="A real quote is attached to an unrelated security claim.",
        findings=[finding],
        limitations=[],
    )


def test_local_evaluator_rewards_grounded_report(settings: Settings) -> None:
    spec, report = _grounded_report(settings)
    result = evaluate_report_locally(spec, report)  # type: ignore[arg-type]
    assert not result.severe_failure
    assert result.total_score >= 80
    assert result.verdict == "pass"
    scores = {item.name: item.rule_score for item in result.dimension_scores}
    assert scores["factual_accuracy"] == 4
    assert scores["evidence_traceability"] == 4
    assert scores["hallucination_control"] == 4
    assert scores["actionability"] == 2


def test_local_evaluator_hard_fails_fabricated_critical_finding(
    settings: Settings,
) -> None:
    spec, report = _fabricated_report(settings)
    result = evaluate_report_locally(spec, report)  # type: ignore[arg-type]
    scores = {item.name: item.final_score for item in result.dimension_scores}
    assert result.severe_failure
    assert result.verdict == "fail"
    assert scores["location_accuracy"] == 0
    assert scores["evidence_traceability"] == 0
    assert scores["hallucination_control"] == 0


def test_duplicates_and_terminology_do_not_raise_score(settings: Settings) -> None:
    spec, report = _fabricated_report(settings)
    original = report.findings[0]
    stuffed = original.model_copy(
        update={
            "rationale": (
                "OAuth2 JWT OWASP zero trust least privilege defense in depth "
                "SOC 2 ISO 27001 are impressive but unsupported."
            )
        }
    )
    report = report.model_copy(update={"findings": [stuffed, stuffed]})
    result = evaluate_report_locally(spec, report)  # type: ignore[arg-type]
    flag_codes = {flag.code for flag in result.anti_gaming_flags}
    assert "duplicate_findings" in flag_codes
    assert "terminology_stuffing" in flag_codes
    assert result.total_score < 65


@pytest.mark.asyncio
async def test_hybrid_judge_cannot_override_hard_evidence_ceiling(settings: Settings) -> None:
    spec, report = _fabricated_report(settings)
    judge = FakeJudge(score=4)
    result = await evaluate_report_hybrid(
        spec,  # type: ignore[arg-type]
        report,
        max_model_chars=settings.max_model_chars,
        client=judge,
    )
    scores = {item.name: item for item in result.dimension_scores}
    assert result.mode == "hybrid"
    assert scores["location_accuracy"].judge_score == 4
    assert scores["location_accuracy"].final_score == 0
    assert scores["hallucination_control"].final_score == 0
    assert result.verdict == "fail"
    assert judge.calls[0][2] == "review-quality-judge"
    assert "UNTRUSTED_REVIEW_REPORT" in judge.calls[0][1]


@pytest.mark.asyncio
async def test_hybrid_judge_can_reject_semantically_unrelated_real_quote(
    settings: Settings,
) -> None:
    spec, report = _semantic_mismatch_report(settings)
    local = evaluate_report_locally(spec, report)  # type: ignore[arg-type]
    local_scores = {item.name: item.final_score for item in local.dimension_scores}
    assert local_scores["location_accuracy"] == 4
    assert local_scores["evidence_traceability"] == 4
    assert local_scores["hallucination_control"] == 4

    result = await evaluate_report_hybrid(
        spec,  # type: ignore[arg-type]
        report,
        max_model_chars=settings.max_model_chars,
        client=FakeJudge(score=0, severe=True),
    )
    final_scores = {item.name: item.final_score for item in result.dimension_scores}
    assert final_scores["location_accuracy"] == 0
    assert final_scores["evidence_traceability"] == 0
    assert final_scores["hallucination_control"] == 0
    assert result.severe_failure
    assert result.verdict == "fail"
