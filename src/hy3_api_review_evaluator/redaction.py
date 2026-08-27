"""Best-effort secret redaction before model calls, logs, exports, and UI errors."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._~+\-/=]{8,}"),
    re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+\-/=]{8,}"),
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{12,}\b"),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(r"(?i)(https?://[^:/\s]+:)[^@/\s]+@"),
    re.compile(r"(?i)((?:api[-_ ]?key|client[-_ ]?secret|access[-_ ]?token)\s*[:=]\s*)[^\s,;]{8,}"),
)

_SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "cookie",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "set_cookie",
    "token",
}


def redact_text(value: str, *, exact_secrets: Iterable[str] = ()) -> str:
    result = value
    for secret in exact_secrets:
        if secret:
            result = result.replace(secret, REDACTED)
    for pattern in _PATTERNS:
        if pattern.groups:
            result = pattern.sub(lambda match: f"{match.group(1)}{REDACTED}", result)
        else:
            result = pattern.sub(REDACTED, result)
    return result


def redact_structure(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            if normalized in _SENSITIVE_KEYS and not isinstance(item, (dict, list)):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_structure(item)
        return redacted
    if isinstance(value, list):
        return [redact_structure(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
