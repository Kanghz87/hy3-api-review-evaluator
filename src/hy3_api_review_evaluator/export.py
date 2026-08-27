"""Sanitized JSON and spreadsheet-safe CSV exports."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import Any

from .models import EvaluationResult, ReviewReport
from .redaction import redact_structure, redact_text
from .spec_loader import LoadedSpec


def build_json_export(spec: LoadedSpec, report: ReviewReport, evaluation: EvaluationResult) -> str:
    payload = {
        "export_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "specification": {
            "label": spec.label,
            "title": spec.title,
            "openapi_version": spec.version,
            "sha256": spec.sha256,
            "operation_count": spec.operation_count,
            "external_refs_not_fetched": list(spec.external_refs),
        },
        "review": report.model_dump(mode="json"),
        "evaluation": evaluation.model_dump(mode="json"),
    }
    return json.dumps(redact_structure(payload), ensure_ascii=False, indent=2) + "\n"


def _safe_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    value = redact_text(value)
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def build_csv_export(report: ReviewReport, evaluation: EvaluationResult) -> str:
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
    ]
    assessments = {item.finding_id: item for item in evaluation.finding_assessments}
    rows: list[dict[str, Any]] = []
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
