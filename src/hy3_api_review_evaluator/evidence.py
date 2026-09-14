"""Evidence resolution and exact quote verification against the parsed specification."""

from __future__ import annotations

import json
import re
from typing import Any

from .models import EvidenceCheck, EvidenceReference
from .redaction import REDACTED, DocumentRedactor, redact_text
from .spec_loader import escape_pointer_token


def resolve_json_pointer(document: Any, pointer: str) -> tuple[bool, Any]:
    if pointer == "#":
        return True, document
    if not pointer.startswith("#/"):
        return False, None
    current = document
    for raw_token in pointer[2:].split("/"):
        if re.search(r"~(?:[^01]|$)", raw_token):
            return False, None
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return False, None
            current = current[token]
        elif isinstance(current, list):
            # Reject non-ASCII digits, leading zeros, and oversized indexes before
            # int conversion. Model-authored pointers must never raise ValueError.
            if (
                not token.isascii()
                or not token.isdecimal()
                or (len(token) > 1 and token.startswith("0"))
                or len(token) > len(str(len(current)))
            ):
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


def check_evidence(
    document: dict[str, Any],
    evidence: EvidenceReference,
    *,
    redacted_document: dict[str, Any] | None = None,
) -> EvidenceCheck:
    exists, value = resolve_json_pointer(document, evidence.pointer)
    if not exists:
        return EvidenceCheck(
            pointer=evidence.pointer,
            exists=False,
            quote_matches=False,
            reason="The JSON Pointer does not exist in the uploaded document.",
        )
    rendered = _render(value)
    if redacted_document is None:
        redacted_document = DocumentRedactor(document).document
    redacted_exists, redacted_value = resolve_json_pointer(redacted_document, evidence.pointer)
    redacted_rendered = redact_text(_render(redacted_value)) if redacted_exists else REDACTED
    quote = evidence.quote.strip()
    quote_matches = bool(quote) and (
        quote in rendered or (redacted_exists and quote in redacted_rendered)
    )
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
