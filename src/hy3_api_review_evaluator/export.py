"""Sanitized JSON and spreadsheet-safe CSV exports."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import Any

from .models import EvaluationResult, ReviewReport
from .redaction import DocumentRedactor, redact_text
from .spec_loader import LoadedSpec


def build_json_export(spec: LoadedSpec, report: ReviewReport, evaluation: EvaluationResult) -> str:
    redactor = DocumentRedactor(spec.document)
    payload = {
        "export_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "specification": {
            **redactor.redact(
                {
                    "label": spec.label,
                    "title": spec.title,
                    "openapi_version": spec.version,
                    "external_refs_not_fetched": list(spec.external_refs),
                }
            ),
            "sha256": spec.sha256,
            "operation_count": spec.operation_count,
        },
        "review": redactor.redact_model(report).model_dump(mode="json"),
        "evaluation": redactor.redact_model(evaluation).model_dump(mode="json"),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _safe_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    value = redact_text(value)
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def build_csv_export(
    report: ReviewReport,
    evaluation: EvaluationResult,
    *,
    spec: LoadedSpec | None = None,
) -> str:
    if spec is not None:
        redactor = DocumentRedactor(spec.document)
        report = redactor.redact_model(report)
        evaluation = redactor.redact_model(evaluation)
    fields = [
        "record_type",
        "id",
        "category_or_dimension",
        "severity",
        "location",
        "title_or_label",
        "source",
        "rule_score",
        "judge_score",
        "final_score",
        "weight",
        "evidence_valid",
        "suggestion",
        "reason",
        "quote",
        "evidence_index",
    ]
    assessments = {item.finding_id: item for item in evaluation.finding_assessments}
    rows: list[dict[str, Any]] = []
    rows.append(
        {
            "record_type": "summary",
            "id": evaluation.evaluation_version,
            "title_or_label": evaluation.verdict,
            "final_score": evaluation.total_score,
            "reason": "; ".join(evaluation.severe_failure_reasons),
        }
    )
    for reference, check in zip(report.review_coverage, evaluation.coverage_checks, strict=True):
        rows.append(
            {
                "record_type": "coverage",
                "location": reference.pointer,
                "evidence_valid": check.exists and check.quote_matches,
                "quote": reference.quote,
                "reason": reference.description,
            }
        )
    for finding in report.findings:
        assessment = assessments.get(finding.finding_id)
        rows.append(
            {
                "record_type": "finding",
                "id": finding.finding_id,
                "category_or_dimension": finding.category,
                "severity": finding.severity,
                "location": finding.location,
                "title_or_label": finding.title,
                "source": finding.source,
                "evidence_valid": (
                    any(
                        check.exists and check.quote_matches for check in assessment.evidence_checks
                    )
                    if assessment
                    else False
                ),
                "suggestion": finding.suggestion,
                "reason": finding.rationale,
            }
        )
        for index, reference in enumerate(finding.evidence):
            check = (
                assessment.evidence_checks[index]
                if assessment and index < len(assessment.evidence_checks)
                else None
            )
            rows.append(
                {
                    "record_type": "evidence",
                    "id": finding.finding_id,
                    "evidence_index": index + 1,
                    "location": reference.pointer,
                    "quote": reference.quote,
                    "reason": reference.description,
                    "evidence_valid": bool(
                        check
                        and check.pointer == reference.pointer
                        and check.exists
                        and check.quote_matches
                    ),
                }
            )
    for dimension in evaluation.dimension_scores:
        rows.append(
            {
                "record_type": "dimension",
                "id": dimension.name,
                "category_or_dimension": dimension.name,
                "title_or_label": dimension.label_zh,
                "rule_score": dimension.rule_score,
                "judge_score": dimension.judge_score,
                "final_score": dimension.final_score,
                "weight": dimension.weight,
                "reason": dimension.reason,
            }
        )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _safe_cell(row.get(key, "")) for key in fields})
    return stream.getvalue()
