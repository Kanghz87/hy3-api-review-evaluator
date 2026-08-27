"""Strict data contracts shared by the reviewer, evaluator, UI, and exports."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Focus(StrEnum):
    ALL = "all"
    SECURITY = "security"
    DESIGN = "design"
    RELIABILITY = "reliability"
    COMPATIBILITY = "compatibility"
    DEVELOPER_EXPERIENCE = "developer_experience"


Severity = Literal["critical", "high", "medium", "low", "info"]
FindingSource = Literal["deterministic", "hy3"]
DimensionName = Literal[
    "factual_accuracy",
    "location_accuracy",
    "severity_reasonableness",
    "evidence_traceability",
    "actionability",
    "hallucination_control",
]


class Usage(StrictModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class EvidenceReference(StrictModel):
    """A claim must point at one exact location in the uploaded document."""

    pointer: str = Field(
        description="RFC 6901 JSON Pointer prefixed with #, for example #/paths/~1pets/get"
    )
    quote: str = Field(default="", max_length=1_000)
    description: str = Field(default="", max_length=1_000)

    @field_validator("pointer")
    @classmethod
    def validate_pointer(cls, value: str) -> str:
        if value != "#" and not value.startswith("#/"):
            raise ValueError("evidence pointer must be '#' or start with '#/'")
        return value


class ReviewFinding(StrictModel):
    finding_id: str = Field(min_length=5, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    title: str = Field(min_length=3, max_length=200)
    category: str = Field(min_length=2, max_length=80)
    severity: Severity
    location: str = Field(min_length=1, max_length=500)
    evidence: list[EvidenceReference] = Field(min_length=1, max_length=5)
    rationale: str = Field(min_length=5, max_length=3_000)
    suggestion: str = Field(min_length=5, max_length=3_000)
    source: FindingSource
    confidence: float = Field(ge=0.0, le=1.0)


class ReviewReport(StrictModel):
    report_version: Literal["1.0"] = "1.0"
    specification_title: str = Field(min_length=1, max_length=300)
    openapi_version: str = Field(min_length=3, max_length=30)
    focus: Focus
    executive_summary: str = Field(min_length=5, max_length=4_000)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=100)
    limitations: list[str] = Field(default_factory=list, max_length=20)
    model: str = "hy3"
    usage: Usage = Field(default_factory=Usage)


class Hy3ReviewPayload(StrictModel):
    """Provider payload before local findings and trusted metadata are attached."""

    executive_summary: str = Field(min_length=5, max_length=4_000)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=80)
    limitations: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("limitations", mode="before")
    @classmethod
    def normalize_single_limitation(cls, value: object) -> object:
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value


class EvidenceCheck(StrictModel):
    pointer: str
    exists: bool
    quote_matches: bool
    resolved_preview: str = Field(default="", max_length=2_000)
    reason: str = Field(max_length=500)


class AntiGamingFlag(StrictModel):
    code: str = Field(min_length=2, max_length=80)
    level: Literal["warning", "severe"]
    detail: str = Field(min_length=5, max_length=1_000)


class FindingAssessment(StrictModel):
    finding_id: str
    severity: Severity
    location_exists: bool
    evidence_checks: list[EvidenceCheck]
    anchor_finding_id: str | None = None
    severity_distance: int | None = Field(default=None, ge=0, le=4)
    suggestion_concrete: bool
    supported_by_local_evidence: bool


class DimensionScore(StrictModel):
    name: DimensionName
    label_zh: str
    weight: int = Field(ge=0, le=100)
    rule_score: int = Field(ge=0, le=4)
    judge_score: int | None = Field(default=None, ge=0, le=4)
    final_score: int = Field(ge=0, le=4)
    reason: str = Field(min_length=5, max_length=2_000)


class EvaluationResult(StrictModel):
    evaluation_version: Literal["1.0"] = "1.0"
    mode: Literal["deterministic", "hybrid"]
    report_sha256: str = Field(min_length=64, max_length=64)
    dimension_scores: list[DimensionScore] = Field(min_length=6, max_length=6)
    total_score: float = Field(ge=0, le=100)
    verdict: Literal["pass", "conditional_pass", "fail"]
    severe_failure: bool
    severe_failure_reasons: list[str]
    anti_gaming_flags: list[AntiGamingFlag]
    finding_assessments: list[FindingAssessment]
    judge_usage: Usage = Field(default_factory=Usage)
    preliminary: bool = True


class JudgeDimensionScore(StrictModel):
    name: DimensionName
    score: int = Field(ge=0, le=4)
    reason: str = Field(min_length=5, max_length=2_000)


class Hy3JudgePayload(StrictModel):
    dimension_scores: list[JudgeDimensionScore] = Field(min_length=6, max_length=6)
    severe_failure: bool
    severe_failure_reasons: list[str] = Field(default_factory=list, max_length=20)
