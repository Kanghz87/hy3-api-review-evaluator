from __future__ import annotations

import csv
import io
import json

import pytest
from test_reviewer import FakeClient

from hy3_api_review_evaluator.evaluator import evaluate_report_hybrid, evaluate_report_locally
from hy3_api_review_evaluator.evidence import check_evidence
from hy3_api_review_evaluator.export import build_csv_export, build_json_export
from hy3_api_review_evaluator.models import (
    EvaluationResult,
    EvidenceReference,
    Focus,
    ReviewFinding,
    ReviewReport,
)
from hy3_api_review_evaluator.redaction import REDACTED, DocumentRedactor
from hy3_api_review_evaluator.rubric import DIMENSION_ORDER
from hy3_api_review_evaluator.spec_loader import load_spec_text


def make_result(secret, settings):
    document = {
        "openapi": "3.0.3",
        "info": {"title": "Boundary probe", "version": "1.0"},
        "paths": {},
        "components": {
            "schemas": {
                "Credentials": {
                    "type": "object",
                    "properties": {"password": {"type": "string", "example": secret}},
                }
            }
        },
    }
    spec = load_spec_text(json.dumps(document), "probe.json", settings)
    finding = ReviewFinding(
        finding_id="finding-probe",
        title="Sensitive example",
        category="security",
        severity="low",
        location="#/components/schemas/Credentials/properties/password/example",
        evidence=[
            EvidenceReference(
                pointer="#/components/schemas/Credentials/properties/password/example",
                quote=secret,
            )
        ],
        rationale=f"The example contains {secret}.",
        suggestion=f"Remove example value {secret} from the schema.",
        source="hy3",
        confidence=0.8,
    )
    report = ReviewReport(
        specification_title=spec.title,
        openapi_version=spec.version,
        focus=Focus.ALL,
        executive_summary=f"Sensitive example value: {secret}.",
        findings=[finding],
    )
    return spec, report, evaluate_report_locally(spec, report)


@pytest.mark.parametrize(
    "secret",
    ["all", "pass", "fail", "low", "hy3", "deterministic", "factual_accuracy", "id", "a", "#"],
)
def test_short_secrets_preserve_protocol_and_redact_narrative(secret, settings):
    spec, report, evaluation = make_result(secret, settings)
    original_report = report.model_dump(mode="json")
    original_evaluation = evaluation.model_dump(mode="json")
    # Test every verdict, regardless of the local scorer's chosen verdict.
    for verdict in ("pass", "conditional_pass", "fail"):
        result = evaluation.model_copy(update={"verdict": verdict})
        payload = json.loads(build_json_export(spec, report, result))
        safe_report = ReviewReport.model_validate(payload["review"])
        safe_result = EvaluationResult.model_validate(payload["evaluation"])
        assert safe_report.focus == Focus.ALL
        assert safe_report.findings[0].source == "hy3"
        assert safe_report.findings[0].severity == "low"
        assert safe_report.findings[0].evidence[0].quote == REDACTED
        assert REDACTED in safe_report.executive_summary
        assert safe_result.verdict == verdict
        assert safe_result.mode == result.mode
        assert safe_result.report_sha256 == result.report_sha256
        assert payload["specification"]["sha256"] == spec.sha256
        assert safe_result.finding_assessments[0].finding_id == safe_report.findings[0].finding_id
        assert [d.name for d in safe_result.dimension_scores] == [
            d.name for d in result.dimension_scores
        ]
        rows = list(csv.DictReader(io.StringIO(build_csv_export(report, result, spec=spec))))
        assert rows[0]["record_type"] == "summary"
        assert rows[0]["title_or_label"] == verdict
        finding_row = next(row for row in rows if row["record_type"] == "finding")
        assert finding_row["source"] == "hy3"
        assert finding_row["severity"] == "low"
        assert finding_row["id"] == safe_report.findings[0].finding_id
        assert REDACTED in finding_row["reason"]
    assert report.model_dump(mode="json") == original_report
    assert evaluation.model_dump(mode="json") == original_evaluation
    # A field named like a protocol field in arbitrary input gets no exemption.
    assert DocumentRedactor(spec.document).redact({"focus": secret}) == {"focus": REDACTED}


def test_redaction_expansion_stays_within_schema_limits(settings):
    spec, report, evaluation = make_result("a", settings)
    report = report.model_copy(update={"executive_summary": "a" * 4_000})
    payload = json.loads(build_json_export(spec, report, evaluation))
    safe = ReviewReport.model_validate(payload["review"])
    assert len(safe.executive_summary) <= 4_000
    assert "a" not in safe.executive_summary


@pytest.mark.asyncio
@pytest.mark.parametrize("secret", ["all", "low", "id"])
async def test_judge_request_preserves_protocol_and_matching_ids(secret, settings):
    spec, report, _ = make_result(secret, settings)
    client = FakeClient(
        json.dumps(
            {
                "dimension_scores": [
                    {"name": name, "score": 3, "reason": "Offline regression fixture."}
                    for name in DIMENSION_ORDER
                ],
                "severe_failure": False,
                "severe_failure_reasons": [],
            }
        )
    )
    await evaluate_report_hybrid(
        spec, report, max_model_chars=settings.max_model_chars, client=client
    )
    request = client.calls[0][1]
    report_json = request.split("<UNTRUSTED_REVIEW_REPORT>\n", 1)[1].split(
        "\n</UNTRUSTED_REVIEW_REPORT>", 1
    )[0]
    safe_report = ReviewReport.model_validate_json(report_json)
    features = json.loads(request.split("<TRUSTED_LOCAL_FEATURES>\n", 1)[1].split("\n", 1)[0])
    assert safe_report.focus == Focus.ALL
    assert safe_report.findings[0].severity == "low"
    assert safe_report.findings[0].evidence[0].quote == REDACTED
    assert features[0]["finding_id"] == safe_report.findings[0].finding_id
    assert features[0]["severity"] == "low"


@pytest.mark.parametrize("reuse_projection", [False, True])
@pytest.mark.parametrize(
    ("value", "quote", "expected"),
    [
        ("synthetic-password", "null", False),
        ("synthetic-password", REDACTED, False),
        (None, "null", True),
        ("synthetic-password", "synthetic-password", True),
    ],
)
def test_removed_redacted_location_cannot_supply_evidence(value, quote, expected, reuse_projection):
    document = {"password": {"examples": [value]}}
    kwargs = {"redacted_document": DocumentRedactor(document).document} if reuse_projection else {}
    result = check_evidence(
        document, EvidenceReference(pointer="#/password/examples/0", quote=quote), **kwargs
    )
    assert result.exists
    assert result.quote_matches is expected
    assert result.resolved_preview == REDACTED


def test_existing_redacted_value_still_accepts_placeholder():
    document = {"password": {"example": "synthetic-password"}}
    result = check_evidence(
        document, EvidenceReference(pointer="#/password/example", quote=REDACTED)
    )
    assert result.exists and result.quote_matches
