"""Compose deterministic evidence with a strictly validated Hy3 review."""

from __future__ import annotations

import json
import re
from typing import Protocol

from pydantic import ValidationError

from .errors import StructuredOutputError
from .hy3_client import ModelReply
from .models import Focus, Hy3ReviewPayload, ReviewFinding, ReviewReport
from .prompts import REVIEW_SYSTEM
from .redaction import redact_text
from .rules import audit_spec
from .spec_loader import LoadedSpec, compact_for_model
from .structured_output import parse_json_object


class CompletionClient(Protocol):
    async def complete(self, *, system: str, user: str, purpose: str) -> ModelReply: ...


_TITLE_WORD = re.compile(r"[a-z0-9]+")
_TITLE_STOP_WORDS = {"a", "an", "the", "is", "are", "for", "on", "to", "of", "get", "post"}


def _validation_issue_summary(exc: ValidationError) -> str:
    issues: list[str] = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False)[:8]:
        location = ".".join(str(item) for item in error.get("loc", ())) or "root"
        issues.append(f"{redact_text(location)[:160]}:{error.get('type', 'invalid')}")
    return ", ".join(issues)


def _deduplicate(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    seen: set[tuple[str, str, str]] = set()
    result: list[ReviewFinding] = []
    for finding in findings:
        key = (finding.category.casefold(), finding.location, finding.title.casefold())
        if key in seen:
            continue
        title_tokens = set(_TITLE_WORD.findall(finding.title.casefold())) - _TITLE_STOP_WORDS
        cross_source_duplicate = False
        for existing in result:
            if (
                existing.source == finding.source
                or existing.category.casefold() != finding.category.casefold()
                or existing.location != finding.location
            ):
                continue
            existing_tokens = (
                set(_TITLE_WORD.findall(existing.title.casefold())) - _TITLE_STOP_WORDS
            )
            smaller = min(len(title_tokens), len(existing_tokens))
            if smaller and len(title_tokens & existing_tokens) / smaller >= 0.6:
                cross_source_duplicate = True
                break
        if cross_source_duplicate:
            continue
        result.append(finding)
        seen.add(key)
    return result


async def review_spec(
    spec: LoadedSpec,
    *,
    focus: Focus,
    max_model_chars: int,
    client: CompletionClient,
) -> ReviewReport:
    local_findings = audit_spec(spec)
    deterministic_json = json.dumps(
        [finding.model_dump(mode="json") for finding in local_findings],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    user = f"""Review focus: {focus.value}
Specification label: {redact_text(spec.label)}
Specification SHA-256: {spec.sha256}

<UNTRUSTED_DETERMINISTIC_FINDINGS>
{deterministic_json}
</UNTRUSTED_DETERMINISTIC_FINDINGS>

<UNTRUSTED_OPENAPI_DATA>
{compact_for_model(spec, max_model_chars)}
</UNTRUSTED_OPENAPI_DATA>
"""
    reply = await client.complete(system=REVIEW_SYSTEM, user=user, purpose="openapi-review")
    try:
        payload = Hy3ReviewPayload.model_validate(parse_json_object(reply.content, label="Hy3"))
    except ValidationError as exc:
        issues = _validation_issue_summary(exc)
        raise StructuredOutputError(
            f"Hy3 JSON did not match the required review schema ({issues})"
        ) from exc
    if any(
        finding.source != "hy3" or not finding.finding_id.startswith("hy3-")
        for finding in payload.findings
    ):
        raise StructuredOutputError("Hy3 findings used an invalid source or identifier")
    combined = _deduplicate([*local_findings, *payload.findings])
    return ReviewReport(
        specification_title=spec.title,
        openapi_version=spec.version,
        focus=focus,
        executive_summary=payload.executive_summary,
        findings=combined,
        limitations=payload.limitations,
        model="hy3",
        usage=reply.usage,
    )
