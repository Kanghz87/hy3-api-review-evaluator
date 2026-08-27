"""Evidence resolution and exact quote verification against the parsed specification."""

from __future__ import annotations

import json
from typing import Any

from .models import EvidenceCheck, EvidenceReference
from .redaction import redact_structure, redact_text
from .spec_loader import escape_pointer_token


def resolve_json_pointer(document: Any, pointer: str) -> tuple[bool, Any]:
    if pointer == "#":
        return True, document
    if not pointer.startswith("#/"):
        return False, None
    current = document
    for raw_token in pointer[2:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return False, None
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit():
                return False, None
            index = int(token)
            if index >= len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current


def operation_pointer(path: str, method: str) -> str:
    return f"#/paths/{escape_pointer_token(path)}/{method.lower()}"


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def check_evidence(document: dict[str, Any], evidence: EvidenceReference) -> EvidenceCheck:
    exists, value = resolve_json_pointer(document, evidence.pointer)
    if not exists:
        return EvidenceCheck(
            pointer=evidence.pointer,
            exists=False,
            quote_matches=False,
            reason="The JSON Pointer does not exist in the uploaded document.",
        )
    rendered = _render(value)
    redacted_rendered = redact_text(_render(redact_structure(value)))
    quote = evidence.quote.strip()
    quote_matches = bool(quote) and (quote in rendered or quote in redacted_rendered)
    reason = (
        "The pointer exists and the quote occurs in the resolved value."
        if quote_matches
        else "The pointer exists, but the supplied quote is empty or does not occur there."
    )
    return EvidenceCheck(
        pointer=evidence.pointer,
        exists=True,
        quote_matches=quote_matches,
        resolved_preview=redacted_rendered[:2_000],
        reason=reason,
    )
