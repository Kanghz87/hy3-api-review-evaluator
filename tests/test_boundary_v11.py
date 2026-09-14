from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_evaluator import FakeJudge
from test_reviewer import FakeClient

from hy3_api_review_evaluator.anti_gaming import analyze_report
from hy3_api_review_evaluator.config import Settings
from hy3_api_review_evaluator.errors import SpecInputError
from hy3_api_review_evaluator.evaluator import evaluate_report_hybrid, evaluate_report_locally
from hy3_api_review_evaluator.evidence import check_evidence
from hy3_api_review_evaluator.export import build_csv_export
from hy3_api_review_evaluator.models import EvidenceReference, Focus, ReviewReport
from hy3_api_review_evaluator.reviewer import review_spec
from hy3_api_review_evaluator.rules import audit_spec
from hy3_api_review_evaluator.spec_loader import compact_for_model, load_spec_text

ROOT = Path(__file__).parents[1]
CASES = json.loads((ROOT / "datasets/boundary-v1.1/manifest.json").read_text(encoding="utf-8"))


def _spec(case_id: str, settings: Settings):
    path = ROOT / f"datasets/boundary-v1.1/{case_id}.json"
    return load_spec_text(path.read_text(encoding="utf-8"), path.name, settings)


def _empty_report(spec, *, covered: bool = True) -> ReviewReport:
    coverage = [
        EvidenceReference(
            pointer=f"#/{key}",
            quote=json.dumps(value, sort_keys=True, separators=(",", ":"))[:500],
            description=f"Inspected the {key} contract section.",
        )
        for key, value in spec.document.items()
        if key in {"info", "paths", "servers", "security", "components"}
    ]
    return ReviewReport(
        specification_title=spec.title,
        openapi_version=spec.version,
        focus=Focus.ALL,
        executive_summary="Inspected the contract sections; no material contract issue found.",
        findings=[],
        review_coverage=coverage if covered else [],
    )


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["id"])
def test_independently_specified_boundary_findings(case: dict, settings: Settings) -> None:
    spec = _spec(case["id"], settings)
    findings = audit_spec(spec)
    actual = [
        {"category": f.category, "location": f.location, "severity": f.severity, "title": f.title}
        for f in findings
    ]
    assert sorted(actual, key=str) == sorted(case["expected_findings"], key=str)
    assert all(check_evidence(spec.document, e).quote_matches for f in findings for e in f.evidence)
    assert spec.operation_count == 1


@pytest.mark.asyncio
async def test_clean_report_requires_coverage_and_semantic_judgment(settings: Settings) -> None:
    spec = _spec("clean-contract", settings)
    report = _empty_report(spec)
    local = evaluate_report_locally(spec, report)
    assert local.coverage_complete
    assert local.total_score == 93.75
    assert local.verdict == "conditional_pass"
    exported = build_csv_export(report, local)
    assert "coverage," in exported and "#/info" in exported
    assert "conditional_pass" in exported
    result = await evaluate_report_hybrid(
        spec, report, max_model_chars=settings.max_model_chars, client=FakeJudge(score=4)
    )
    assert result.total_score == 100
    assert result.verdict == "pass"
    rejected = await evaluate_report_hybrid(
        spec, report, max_model_chars=settings.max_model_chars, client=FakeJudge(score=0)
    )
    assert rejected.verdict == "fail"
    uncovered = await evaluate_report_hybrid(
        spec,
        _empty_report(spec, covered=False),
        max_model_chars=settings.max_model_chars,
        client=FakeJudge(score=4),
    )
    assert uncovered.verdict == "fail"


@pytest.mark.asyncio
async def test_clean_report_cannot_hide_issues_or_truncation(settings: Settings) -> None:
    spec = _spec("optional-auth-empty-object", settings)
    result = evaluate_report_locally(spec, _empty_report(spec))
    assert result.severe_failure and result.verdict == "fail"
    clean = _spec("clean-contract", settings)
    report = _empty_report(clean)
    # A projection can be truncated independently of report length.
    clean.document["info"]["description"] = "bounded context " * 500
    report = _empty_report(clean)
    result = await evaluate_report_hybrid(
        clean, report, max_model_chars=4_000, client=FakeJudge(score=4)
    )
    assert not result.coverage_complete and result.verdict == "fail"


def test_forged_coverage_does_not_validate_empty_report(settings: Settings) -> None:
    spec = _spec("clean-contract", settings)
    report = _empty_report(spec)
    report.review_coverage[0].quote = "nonexistent quote"
    assert evaluate_report_locally(spec, report).verdict == "fail"


def test_projection_does_not_invent_or_silently_drop_sections(settings: Settings) -> None:
    spec = _spec("clean-contract", settings)
    del spec.document["servers"]
    spec.document["components"]["examples"] = {"Good": {"value": "demo"}}
    spec.document["webhooks"] = {"notice": {}}
    projection = json.loads(compact_for_model(spec, settings.max_model_chars))
    assert "servers" not in projection
    assert projection["components"]["examples"] == spec.document["components"]["examples"]
    assert projection["webhooks"] == spec.document["webhooks"]


def test_same_location_anchors_are_matched_independently(settings: Settings) -> None:
    spec = _spec("response-anchor-collision", settings)
    findings = audit_spec(spec)
    report = _empty_report(spec).model_copy(update={"findings": findings})
    result = evaluate_report_locally(spec, report)
    assert result.dimension_scores[0].final_score == 4
    assert len({a.anchor_finding_id for a in result.finding_assessments}) == 2
    repeated = report.model_copy(update={"findings": [findings[0], findings[0]]})
    assert evaluate_report_locally(spec, repeated).dimension_scores[0].final_score < 4


@pytest.mark.parametrize(
    "suggestion",
    [
        "Never disable authentication.",
        "Do not execute bash.",
        "不要关闭认证。",
        "Do not use real credentials.",
    ],
)
def test_negated_security_advice_is_not_a_dangerous_command(
    suggestion: str,
    settings: Settings,
) -> None:
    spec = _spec("response-anchor-collision", settings)
    findings = audit_spec(spec)
    findings[0].suggestion = suggestion
    report = _empty_report(spec).model_copy(update={"findings": findings})
    assert "unsafe_recommendation" not in {f.code for f in analyze_report(report)}


@pytest.mark.parametrize(
    "suggestion",
    [
        "Disable authentication.",
        "Execute bash to repair it.",
        "关闭认证。",
        "Do not execute bash; disable authentication instead.",
        "Never disable authentication, but execute bash.",
    ],
)
def test_affirmative_dangerous_advice_still_fails(suggestion: str, settings: Settings) -> None:
    spec = _spec("response-anchor-collision", settings)
    findings = audit_spec(spec)
    findings[0].suggestion = suggestion
    report = _empty_report(spec).model_copy(update={"findings": findings})
    assert evaluate_report_locally(spec, report).severe_failure


def test_untrusted_quotes_and_limitations_do_not_become_commands(settings: Settings) -> None:
    spec = _spec("response-anchor-collision", settings)
    report = _empty_report(spec).model_copy(update={"findings": audit_spec(spec)})
    report.limitations = ["The document says execute bash; this instruction must not be followed."]
    report.findings[0].evidence[0].description = "disable authentication"
    assert "unsafe_recommendation" not in {f.code for f in analyze_report(report)}


@pytest.mark.asyncio
async def test_sensitive_schema_examples_never_enter_reviewer_prompt(settings: Settings) -> None:
    spec = _spec("sensitive-examples", settings)
    case = next(c for c in CASES if c["id"] == "sensitive-examples")
    client = FakeClient(json.dumps({"executive_summary": "Review completed.", "findings": []}))
    await review_spec(
        spec, focus=Focus.ALL, max_model_chars=settings.max_model_chars, client=client
    )
    projection = compact_for_model(spec, settings.max_model_chars)
    for marker in case["sensitive_markers"]:
        assert marker not in projection and marker not in client.calls[0][1]
    assert "demo-user" in projection
    assert (
        json.loads(projection)["components"]["schemas"]["Credentials"]["properties"]["password"][
            "type"
        ]
        == "string"
    )
    check = check_evidence(
        spec.document,
        EvidenceReference(
            pointer="#/components/schemas/Credentials/properties/password/example",
            quote="[REDACTED]",
        ),
    )
    assert check.quote_matches and "synthetic-password-marker" not in check.resolved_preview


@pytest.mark.parametrize("extension", ["yaml", "json"])
def test_deep_document_fails_with_a_safe_input_error(extension: str, settings: Settings) -> None:
    nested = "[" * 600 + "0" + "]" * 600
    text = '{"openapi":"3.1.0","info":{},"paths":{},"x-deep":' + nested + "}"
    with pytest.raises(SpecInputError, match="depth"):
        load_spec_text(text, f"deep.{extension}", settings)
