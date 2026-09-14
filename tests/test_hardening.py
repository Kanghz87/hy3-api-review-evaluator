from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml
from test_reviewer import FakeClient

from hy3_api_review_evaluator.errors import SpecInputError, StructuredOutputError
from hy3_api_review_evaluator.evaluator import evaluate_report_hybrid, evaluate_report_locally
from hy3_api_review_evaluator.export import build_csv_export, build_json_export
from hy3_api_review_evaluator.models import EvidenceReference, Focus, ReviewFinding, ReviewReport
from hy3_api_review_evaluator.redaction import DocumentRedactor, redact_text
from hy3_api_review_evaluator.reviewer import _deduplicate, review_spec
from hy3_api_review_evaluator.rubric import DIMENSION_ORDER
from hy3_api_review_evaluator.rules import audit_spec
from hy3_api_review_evaluator.spec_loader import compact_for_model, load_spec_text

ROOT = Path(__file__).parents[1]
MARKER = "SYNTHETIC_REDACTION_PROBE_2026"


def base() -> dict:
    return json.loads((ROOT / "datasets/boundary-v1.1/clean-contract.json").read_text())


def report_for(spec, findings) -> ReviewReport:
    return ReviewReport(
        specification_title=spec.title,
        openapi_version=spec.version,
        focus=Focus.ALL,
        executive_summary="Offline synthetic regression report.",
        findings=findings,
    )


@pytest.mark.parametrize("case", ["inline", "reference", "chain", "header", "custom_header"])
@pytest.mark.asyncio
async def test_document_secrets_are_scrubbed_through_requests_and_exports(case, settings):
    doc = base()
    if case in {"header", "custom_header"}:
        name = "X-API-Key" if case == "header" else "X-Account-Access"
        doc["components"]["securitySchemes"]["customKey"] = {
            "type": "apiKey",
            "in": "header",
            "name": name,
        }
        doc["paths"]["/items"]["get"]["parameters"] = [
            {"name": name, "in": "header", "schema": {"type": "string"}, "example": MARKER}
        ]
        pointer = "#/paths/~1items/get/parameters/0/example"
    else:
        schemas = doc["components"]["schemas"]
        definition = {"type": "string", "example": MARKER}
        if case in {"reference", "chain"}:
            schemas["Value"] = definition
            definition = {"$ref": "#/components/schemas/Value"}
            if case == "chain":
                schemas["Alias"] = definition
                definition = {"$ref": "#/components/schemas/Alias"}
            pointer = "#/components/schemas/Value/example"
        else:
            pointer = "#/components/schemas/Credentials/properties/password/example"
        schemas["Credentials"] = {"type": "object", "properties": {"password": definition}}
    spec = load_spec_text(json.dumps(doc), "probe.json", settings)
    projection = compact_for_model(spec, settings.max_model_chars)
    assert MARKER not in projection
    json.loads(projection)
    client = FakeClient(json.dumps({"executive_summary": "No further findings.", "findings": []}))
    await review_spec(
        spec, focus=Focus.ALL, max_model_chars=settings.max_model_chars, client=client
    )
    assert MARKER not in client.calls[0][1]
    finding = ReviewFinding(
        finding_id="probe-secret",
        title="Sensitive example is exposed",
        category="credential",
        severity="low",
        location=pointer,
        evidence=[EvidenceReference(pointer=pointer, quote=MARKER)],
        rationale=f"The example contains {MARKER}.",
        suggestion=f"Remove schema example {MARKER}.",
        source="hy3",
        confidence=0.8,
    )
    report = report_for(spec, [finding])
    judge = FakeClient(
        json.dumps(
            {
                "dimension_scores": [
                    {"name": name, "score": 3, "reason": "Offline test evidence."}
                    for name in DIMENSION_ORDER
                ],
                "severe_failure": False,
                "severe_failure_reasons": [],
            }
        )
    )
    result = await evaluate_report_hybrid(
        spec, report, max_model_chars=settings.max_model_chars, client=judge
    )
    assert MARKER not in judge.calls[0][1]
    assert MARKER not in build_json_export(spec, report, result)
    assert MARKER not in build_csv_export(report, result, spec=spec)
    assert result.finding_assessments[0].evidence_checks[0].quote_matches


def test_reference_cycles_are_bounded_and_schema_types_preserved():
    doc = base()
    doc["components"]["schemas"].update(
        {
            "Credentials": {"properties": {"password": {"$ref": "#/components/schemas/A"}}},
            "A": {"$ref": "#/components/schemas/B", "type": "string", "example": MARKER},
            "B": {"$ref": "#/components/schemas/A", "type": "string", "default": MARKER},
        }
    )
    redactor = DocumentRedactor(doc)
    assert MARKER not in json.dumps(redactor.document)
    assert redactor.document["components"]["schemas"]["A"]["type"] == "string"
    assert MARKER in json.dumps(doc)  # never mutate the uploaded source


def test_named_sensitive_example_references_are_scrubbed(settings):
    doc = base()
    doc["components"]["examples"] = {"Credential": {"value": MARKER}}
    doc["paths"]["/items"]["get"]["parameters"] = [
        {
            "name": "X-API-Key",
            "in": "header",
            "schema": {"type": "string"},
            "examples": {"demo": {"$ref": "#/components/examples/Credential"}},
        }
    ]
    spec = load_spec_text(json.dumps(doc), "probe.json", settings)
    redactor = DocumentRedactor(spec.document)
    assert MARKER not in compact_for_model(spec, settings.max_model_chars)
    assert redactor.redact(MARKER) == "[REDACTED]"
    assert "#/components/examples/Credential" not in redactor.secrets


@pytest.mark.parametrize("assignment", ["api_key", "access_token", "client_secret"])
def test_redaction_preserves_json_and_is_idempotent(assignment, settings):
    doc = base()
    doc["info"]["description"] = f"Use {assignment}={MARKER}"
    spec = load_spec_text(json.dumps(doc), "probe.json", settings)
    projection = compact_for_model(spec, settings.max_model_chars)
    assert MARKER not in projection
    json.loads(projection)
    assert redact_text(projection) == projection
    assert redact_text(redact_text(doc["info"]["description"])) == redact_text(
        doc["info"]["description"]
    )


@pytest.mark.parametrize("case", ["parameters", "required", "security_names"])
def test_distinct_issues_cannot_collapse_or_claim_full_recall(case, settings):
    doc = base()
    if case == "parameters":
        doc["paths"]["/items/{first}/{second}"] = doc["paths"].pop("/items")
    elif case == "required":
        doc["components"]["schemas"]["Item"]["required"] = ["first", "second"]
    else:
        doc["security"] = [{"first": [], "second": []}]
    spec = load_spec_text(json.dumps(doc), "probe.json", settings)
    findings = audit_spec(spec)
    expected = 4 if case == "security_names" else 2
    assert len(findings) == len({f.finding_id for f in findings}) == expected
    assert len(_deduplicate(findings)) == expected
    full = evaluate_report_locally(spec, report_for(spec, findings))
    partial = evaluate_report_locally(spec, report_for(spec, findings[:1]))
    assert full.dimension_scores[0].final_score == 4
    assert partial.dimension_scores[0].final_score < 4
    assert partial.total_score < full.total_score


@pytest.mark.asyncio
async def test_local_capacity_rejected_before_provider_call(settings):
    doc = base()
    operation = doc["paths"]["/items"]["get"]
    operation.pop("operationId")
    doc["paths"] = {f"/item{i}": {"get": copy.deepcopy(operation)} for i in range(101)}
    spec = load_spec_text(json.dumps(doc), "probe.json", settings)
    client = FakeClient(json.dumps({"executive_summary": "No further findings.", "findings": []}))
    with pytest.raises(SpecInputError, match="100"):
        await review_spec(
            spec, focus=Focus.ALL, max_model_chars=settings.max_model_chars, client=client
        )
    assert not client.calls


@pytest.mark.parametrize(
    "flag,reasons", [(False, ["A severe fabricated claim exists."]), (True, []), (True, [" "])]
)
@pytest.mark.asyncio
async def test_inconsistent_judge_is_rejected(flag, reasons, settings):
    spec = load_spec_text(json.dumps(base()), "probe.json", settings)
    client = FakeClient(
        json.dumps(
            {
                "dimension_scores": [
                    {"name": name, "score": 4, "reason": "Offline contradiction test."}
                    for name in DIMENSION_ORDER
                ],
                "severe_failure": flag,
                "severe_failure_reasons": reasons,
            }
        )
    )
    with pytest.raises(StructuredOutputError, match="schema"):
        await evaluate_report_hybrid(
            spec, report_for(spec, []), max_model_chars=settings.max_model_chars, client=client
        )


@pytest.mark.parametrize(
    "suffix,extra",
    [
        ("json", ',"security":[],"security":[]'),
        ("yaml", "\nx-test: 1\nx-test: 2\n"),
        ("yaml", "\nx-map:\n  200: first\n  '200': second\n"),
        ("yaml", "\nx-example: !!binary Zm9v\n"),
        ("yaml", "\nx-example: .inf\n"),
        ("yaml", "\nx-example: !!map [a, b]\n"),
        ("json", ',"x-example":NaN'),
        ("json", ',"x-example":' + "9" * 5000),
    ],
)
def test_ambiguous_or_non_json_input_fails_safely(suffix, extra, settings):
    text = (
        json.dumps(base())[:-1] + extra + "}"
        if suffix == "json"
        else yaml.safe_dump(base()) + extra
    )
    with pytest.raises(SpecInputError):
        load_spec_text(text, f"probe.{suffix}", settings)


def test_unquoted_dates_and_integer_response_keys_are_normalized(settings):
    text = yaml.safe_dump(base()) + "\nx-date: 2026-09-14\nx-time: 2026-09-14T12:30:00Z\n"
    text = text.replace("'200':", "200:")
    spec = load_spec_text(text, "probe.yaml", settings)
    assert spec.document["x-date"] == "2026-09-14"
    assert spec.document["x-time"] == "2026-09-14T12:30:00Z"
    assert "200" in spec.document["paths"]["/items"]["get"]["responses"]
    json.loads(compact_for_model(spec, settings.max_model_chars))


def test_long_location_rejected_at_load_time(settings):
    doc = base()
    doc["paths"]["/" + "x" * 510] = doc["paths"].pop("/items")
    with pytest.raises(SpecInputError, match="pointer"):
        load_spec_text(json.dumps(doc), "probe.json", settings)
