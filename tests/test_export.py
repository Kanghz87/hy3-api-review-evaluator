from __future__ import annotations

import json

from test_spec_loader import VALID

from hy3_api_review_evaluator.config import Settings
from hy3_api_review_evaluator.evaluator import evaluate_report_locally
from hy3_api_review_evaluator.export import build_csv_export, build_json_export
from hy3_api_review_evaluator.models import Focus, ReviewReport
from hy3_api_review_evaluator.rules import audit_spec
from hy3_api_review_evaluator.spec_loader import load_spec_text


def test_exports_are_structured_and_csv_formula_safe(settings: Settings) -> None:
    spec = load_spec_text(VALID, "demo.yaml", settings)
    findings = audit_spec(spec)
    findings[0] = findings[0].model_copy(update={"title": "=DANGEROUS()"})
    report = ReviewReport(
        specification_title=spec.title,
        openapi_version=spec.version,
        focus=Focus.ALL,
        executive_summary="A grounded test report for export.",
        findings=findings,
        limitations=[],
    )
    evaluation = evaluate_report_locally(spec, report)
    exported_json = json.loads(build_json_export(spec, report, evaluation))
    exported_csv = build_csv_export(report, evaluation)
    assert exported_json["specification"]["sha256"] == spec.sha256
    assert exported_json["evaluation"]["total_score"] == evaluation.total_score
    assert "'=DANGEROUS()" in exported_csv
    assert "record_type" in exported_csv


def test_json_export_redacts_spec_metadata_secret(settings: Settings) -> None:
    secret = "abcdefghijklmnop"
    spec = load_spec_text(VALID, f"Bearer {secret}.yaml", settings)
    report = ReviewReport(
        specification_title=spec.title,
        openapi_version=spec.version,
        focus=Focus.ALL,
        executive_summary="A grounded test report for export.",
        findings=audit_spec(spec),
        limitations=[],
    )
    evaluation = evaluate_report_locally(spec, report)
    output = build_json_export(spec, report, evaluation)
    assert secret not in output
    assert "[REDACTED]" in output
