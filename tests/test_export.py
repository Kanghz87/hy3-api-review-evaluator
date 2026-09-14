from __future__ import annotations

import csv
import io
import json

from test_redaction_boundaries import make_result
from test_spec_loader import VALID

from hy3_api_review_evaluator.config import Settings
from hy3_api_review_evaluator.evaluator import evaluate_report_locally
from hy3_api_review_evaluator.export import build_csv_export, build_json_export
from hy3_api_review_evaluator.models import EvidenceReference, Focus, ReviewReport
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


def test_csv_exports_every_finding_reference_with_matching_status(settings):
    spec, report, _ = make_result("synthetic-csv-password", settings)
    report.findings[0].evidence.append(
        EvidenceReference(pointer="#/missing", quote="=DANGEROUS()", description="False citation")
    )
    evaluation = evaluate_report_locally(spec, report)
    output = build_csv_export(report, evaluation, spec=spec)
    rows = list(csv.DictReader(io.StringIO(output)))
    evidence_rows = [row for row in rows if row["record_type"] == "evidence"]
    assert len(evidence_rows) == 2
    assert [row["evidence_index"] for row in evidence_rows] == ["1", "2"]
    assert evidence_rows[0]["quote"] == "[REDACTED]"
    assert evidence_rows[0]["evidence_valid"] == "True"
    assert evidence_rows[1]["location"] == "#/missing"
    assert evidence_rows[1]["quote"] == "'=DANGEROUS()"
    assert evidence_rows[1]["evidence_valid"] == "False"
    assert evidence_rows[1]["reason"] == "False citation"
    assert all(row["id"] == report.findings[0].finding_id for row in evidence_rows)
    assert "synthetic-csv-password" not in output
