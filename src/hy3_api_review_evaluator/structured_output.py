"""Strict extraction of one JSON object from provider content."""

from __future__ import annotations

import json
import re

from .errors import StructuredOutputError


def parse_json_object(content: str, *, label: str) -> dict[str, object]:
    stripped = content.strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", stripped, re.DOTALL | re.IGNORECASE)
    if fence:
        stripped = fence.group(1).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(
            f"{label} returned invalid JSON; no result was accepted"
        ) from exc
    if not isinstance(value, dict):
        raise StructuredOutputError(f"{label} returned JSON that is not an object")
    return value
