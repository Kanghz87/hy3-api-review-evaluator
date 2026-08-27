"""Hybrid report evaluator: deterministic evidence gates plus a constrained Hy3 judge."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from pydantic import ValidationError

from .anti_gaming import analyze_report, suggestion_is_concrete
from .errors import StructuredOutputError
from .evidence import check_evidence, resolve_json_pointer
from .models import (
    DimensionName,
    DimensionScore,
    EvaluationResult,
    FindingAssessment,
    Hy3JudgePayload,
    ReviewFinding,
    ReviewReport,
)
from .prompts import JUDGE_SYSTEM
from .redaction import redact_structure
from .reviewer import CompletionClient, _validation_issue_summary
from .rubric import DIMENSION_ORDER, load_rubric
from .rules import audit_spec
from .spec_loader import LoadedSpec, compact_for_model
from .structured_output import parse_json_object

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _report_hash(report: ReviewReport) -> str:
    canonical = json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _unique_findings(findings: Sequence[ReviewFinding]) -> list[ReviewFinding]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[ReviewFinding] = []
    for finding in findings:
        key = (
            finding.category.casefold(),
            finding.location,
            finding.title.casefold(),
            finding.rationale.casefold(),
        )
        if key not in seen:
            result.append(finding)
            seen.add(key)
    return result


def _ratio_score(successes: int, total: int) -> int:
    if total <= 0 or successes <= 0:
        return 0
    ratio = successes / total
    if ratio == 1:
        return 4
    if ratio >= 0.9:
        return 3
    if ratio >= 0.6:
        return 2
    return 1


def _score_dimensions(
    assessments: list[FindingAssessment],
    *,
    anchor_count: int,
    matched_anchor_count: int,
    severe_flag_codes: set[str],
) -> tuple[dict[str, int], dict[str, str], list[str]]:
    total = len(assessments)
    supported = sum(item.supported_by_local_evidence for item in assessments)
    locations = sum(item.location_exists for item in assessments)
    traced = sum(
        any(check.exists and check.quote_matches for check in item.evidence_checks)
        for item in assessments
    )
    concrete = sum(item.suggestion_concrete for item in assessments)
    unsupported = total - supported
    high_fake = any(
        not item.location_exists and item.severity in {"high", "critical"} for item in assessments
    )

    support_ratio_score = _ratio_score(supported, total)
    recall_score = _ratio_score(matched_anchor_count, anchor_count) if anchor_count else 4
    factual = min(support_ratio_score, recall_score)

    location = _ratio_score(locations, total)
    evidence = _ratio_score(traced, total)

    matched_severity = [item for item in assessments if item.severity_distance is not None]
    if not matched_severity:
        severity = 2 if total else 0
    else:
        exact = sum(item.severity_distance == 0 for item in matched_severity)
        within_one = sum((item.severity_distance or 0) <= 1 for item in matched_severity)
        if exact == len(matched_severity):
            severity = 4
        elif exact / len(matched_severity) >= 0.9 and within_one == len(matched_severity):
            severity = 3
        elif within_one / len(matched_severity) >= 0.6:
            severity = 2
        elif within_one:
            severity = 1
        else:
            severity = 0

    actionability = _ratio_score(concrete, total)
    if "unsafe_recommendation" in severe_flag_codes:
        actionability = 0

    unsupported_ratio = unsupported / total if total else 0.0
    unsupported_high = any(
        not item.supported_by_local_evidence and item.severity in {"high", "critical"}
        for item in assessments
    )
    if unsupported == 0:
        hallucination = 4
    elif unsupported_ratio <= 0.05 and not unsupported_high:
        hallucination = 3
    elif unsupported_ratio <= 0.2 and not unsupported_high:
        hallucination = 2
    elif unsupported_ratio < 0.5 and not high_fake:
        hallucination = 1
    else:
        hallucination = 0

    scores = {
        "factual_accuracy": factual,
        "location_accuracy": 0 if high_fake else location,
        "severity_reasonableness": severity,
        "evidence_traceability": evidence,
        "actionability": actionability,
        "hallucination_control": hallucination,
    }
    reasons = {
        "factual_accuracy": (
            f"{supported}/{total} unique findings have an existing location and an exact quote; "
            f"{matched_anchor_count}/{anchor_count} deterministic anchors are covered."
        ),
        "location_accuracy": f"{locations}/{total} unique finding locations exist.",
        "severity_reasonableness": (
            f"{len(matched_severity)} findings match deterministic severity anchors."
        ),
        "evidence_traceability": f"{traced}/{total} findings contain an exact quote match.",
        "actionability": f"{concrete}/{total} suggestions name an action and contract target.",
        "hallucination_control": f"{unsupported}/{total} findings are locally unverifiable.",
    }
    severe_reasons: list[str] = []
    if unsupported_ratio >= 0.5 and total:
        severe_reasons.append("At least 50% of material findings are locally unverifiable.")
    return scores, reasons, severe_reasons


def evaluate_report_locally(spec: LoadedSpec, report: ReviewReport) -> EvaluationResult:
    rubric = load_rubric()
    anchors = audit_spec(spec)
    anchors_by_key = {(item.category.casefold(), item.location): item for item in anchors}
    matched_anchor_ids: set[str] = set()
    assessments: list[FindingAssessment] = []
    severe_reasons: list[str] = []
    unique_findings = _unique_findings(report.findings)

    for finding in unique_findings:
        location_exists, _ = resolve_json_pointer(spec.document, finding.location)
        evidence_checks = [check_evidence(spec.document, item) for item in finding.evidence]
        supported = location_exists and any(
            item.exists and item.quote_matches for item in evidence_checks
        )
        anchor = anchors_by_key.get((finding.category.casefold(), finding.location))
        severity_distance = None
        if anchor is not None:
            matched_anchor_ids.add(anchor.finding_id)
            severity_distance = abs(
                SEVERITY_ORDER[finding.severity] - SEVERITY_ORDER[anchor.severity]
            )
        assessments.append(
            FindingAssessment(
                finding_id=finding.finding_id,
                severity=finding.severity,
                location_exists=location_exists,
                evidence_checks=evidence_checks,
                anchor_finding_id=anchor.finding_id if anchor else None,
                severity_distance=severity_distance,
                suggestion_concrete=suggestion_is_concrete(finding.suggestion),
                supported_by_local_evidence=supported,
            )
        )
        if finding.severity in {"high", "critical"} and not location_exists:
            severe_reasons.append(
                f"{finding.finding_id}: high/critical finding location does not exist."
            )
        if finding.severity == "critical" and not any(
            item.exists and item.quote_matches for item in evidence_checks
        ):
            severe_reasons.append(
                f"{finding.finding_id}: critical finding has no exact evidence match."
            )

    flags = analyze_report(report)
    severe_flags = {flag.code for flag in flags if flag.level == "severe"}
    severe_reasons.extend(flag.detail for flag in flags if flag.level == "severe")
    rule_scores, reasons, ratio_severe = _score_dimensions(
        assessments,
        anchor_count=len(anchors),
        matched_anchor_count=len(matched_anchor_ids),
        severe_flag_codes=severe_flags,
    )
    severe_reasons.extend(ratio_severe)

    dimensions: list[DimensionScore] = []
    for name in DIMENSION_ORDER:
        definition = rubric["dimensions"][name]
        score = rule_scores[name]
        dimensions.append(
            DimensionScore(
                name=name,  # type: ignore[arg-type]
                label_zh=definition["label_zh"],
                weight=definition["weight"],
                rule_score=score,
                final_score=score,
                reason=reasons[name],
            )
        )
    total_score = round(sum(item.final_score / 4 * item.weight for item in dimensions), 2)
    severe_failure = bool(severe_reasons)
    thresholds = rubric["thresholds"]
    if severe_failure or total_score < thresholds["conditional_pass"]:
        verdict = "fail"
    elif total_score < thresholds["pass"]:
        verdict = "conditional_pass"
    else:
        verdict = "pass"
    return EvaluationResult(
        mode="deterministic",
        report_sha256=_report_hash(report),
        dimension_scores=dimensions,
        total_score=total_score,
        verdict=verdict,
        severe_failure=severe_failure,
        severe_failure_reasons=severe_reasons,
        anti_gaming_flags=flags,
        finding_assessments=assessments,
        preliminary=True,
    )


def _compact_features(result: EvaluationResult) -> list[dict[str, object]]:
    return [
        {
            "finding_id": item.finding_id,
            "severity": item.severity,
            "location_exists": item.location_exists,
            "evidence": [
                {
                    "pointer": check.pointer,
                    "exists": check.exists,
                    "quote_matches": check.quote_matches,
                }
                for check in item.evidence_checks
            ],
            "anchor_finding_id": item.anchor_finding_id,
            "severity_distance": item.severity_distance,
            "suggestion_concrete": item.suggestion_concrete,
            "supported_by_local_evidence": item.supported_by_local_evidence,
        }
        for item in result.finding_assessments
    ]


async def evaluate_report_hybrid(
    spec: LoadedSpec,
    report: ReviewReport,
    *,
    max_model_chars: int,
    client: CompletionClient,
) -> EvaluationResult:
    local = evaluate_report_locally(spec, report)
    rubric = load_rubric()
    report_json = json.dumps(
        redact_structure(report.model_dump(mode="json")),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(report_json) > max_model_chars:
        raise StructuredOutputError("The review report is too large for bounded evaluation")
    rubric_json = json.dumps(rubric["dimensions"], ensure_ascii=False, separators=(",", ":"))
    features_json = json.dumps(_compact_features(local), ensure_ascii=False, separators=(",", ":"))
    user = f"""Apply the rubric literally.

<TRUSTED_RUBRIC>
{rubric_json}
</TRUSTED_RUBRIC>

<TRUSTED_LOCAL_FEATURES>
{features_json}
</TRUSTED_LOCAL_FEATURES>

<UNTRUSTED_REVIEW_REPORT>
{report_json}
</UNTRUSTED_REVIEW_REPORT>

<UNTRUSTED_OPENAPI_DATA>
{compact_for_model(spec, max_model_chars)}
</UNTRUSTED_OPENAPI_DATA>
"""
    reply = await client.complete(system=JUDGE_SYSTEM, user=user, purpose="review-quality-judge")
    try:
        payload = Hy3JudgePayload.model_validate(
            parse_json_object(reply.content, label="Hy3 judge")
        )
    except ValidationError as exc:
        issues = _validation_issue_summary(exc)
        raise StructuredOutputError(
            f"Hy3 judge JSON did not match the required schema ({issues})"
        ) from exc
    judge_by_name = {item.name: item for item in payload.dimension_scores}
    if set(judge_by_name) != set(DIMENSION_ORDER) or len(judge_by_name) != 6:
        raise StructuredOutputError("Hy3 judge did not score each rubric dimension exactly once")

    hard_dimensions: set[DimensionName] = {
        "location_accuracy",
        "evidence_traceability",
        "hallucination_control",
    }
    dimensions: list[DimensionScore] = []
    for local_dimension in local.dimension_scores:
        judge = judge_by_name[local_dimension.name]
        if local_dimension.name in hard_dimensions:
            ceiling = local_dimension.rule_score
        else:
            ceiling = min(4, local_dimension.rule_score + 1)
        final_score = min(judge.score, ceiling)
        dimensions.append(
            local_dimension.model_copy(
                update={
                    "judge_score": judge.score,
                    "final_score": final_score,
                    "reason": (
                        f"Local: {local_dimension.reason} Judge: {judge.reason} "
                        f"Applied ceiling: {ceiling}."
                    ),
                }
            )
        )

    total_score = round(sum(item.final_score / 4 * item.weight for item in dimensions), 2)
    severe_reasons = [
        *local.severe_failure_reasons,
        *payload.severe_failure_reasons,
    ]
    severe_failure = local.severe_failure or payload.severe_failure
    thresholds = rubric["thresholds"]
    if severe_failure or total_score < thresholds["conditional_pass"]:
        verdict = "fail"
    elif total_score < thresholds["pass"]:
        verdict = "conditional_pass"
    else:
        verdict = "pass"
    return local.model_copy(
        update={
            "mode": "hybrid",
            "dimension_scores": dimensions,
            "total_score": total_score,
            "verdict": verdict,
            "severe_failure": severe_failure,
            "severe_failure_reasons": severe_reasons,
            "judge_usage": reply.usage,
        }
    )
