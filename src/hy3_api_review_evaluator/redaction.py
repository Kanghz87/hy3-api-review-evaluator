"""Best-effort secret redaction before model calls, logs, exports, and UI errors."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from enum import Enum
from hashlib import sha256
from typing import Any, Literal, TypeVar, get_origin

from .models import EvaluationResult, StrictModel

ModelT = TypeVar("ModelT", bound=StrictModel)

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
    re.compile(
        r"(?i)((?:api[-_ ]?key|client[-_ ]?secret|access[-_ ]?token)\s*[:=]\s*)"
        r"[^\s,;\"'<>\[\]{}\\]{8,}"
    ),
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
    secrets = sorted({s for s in exact_secrets if s and s != REDACTED}, key=len, reverse=True)
    if secrets:
        pattern = re.compile("|".join(re.escape(s) for s in secrets))
        result = REDACTED.join(pattern.sub(REDACTED, part) for part in result.split(REDACTED))
    for pattern in _PATTERNS:
        if pattern.groups:
            result = pattern.sub(lambda match: f"{match.group(1)}{REDACTED}", result)
        else:
            result = pattern.sub(REDACTED, result)
    return result


def _sensitive_name(value: str) -> bool:
    normalized = re.sub(r"([a-z])([A-Z])", r"\1_\2", value).lower().replace("-", "_")
    return normalized in _SENSITIVE_KEYS or normalized.removeprefix("x_") in _SENSITIVE_KEYS


def redact_structure(
    value: Any,
    *,
    sensitive_context: bool = False,
    sensitive_nodes: frozenset[int] = frozenset(),
    sensitive_names: frozenset[str] = frozenset(),
    example_nodes: frozenset[int] = frozenset(),
    hidden_values: set[str] | None = None,
) -> Any:
    sensitive_context = sensitive_context or id(value) in sensitive_nodes
    if isinstance(value, dict):
        # Parameter/header schemas may carry the field name separately from their definition.
        sensitive_context = sensitive_context or _sensitive_name(str(value.get("name", "")))
        sensitive_context = sensitive_context or value.get("format") == "password"
        sensitive_context = (
            sensitive_context or str(value.get("name", "")).casefold() in sensitive_names
        )
        redacted: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if (
                (sensitive_context and key in {"example", "examples", "default", "enum", "const"})
                or (sensitive_context and id(value) in example_nodes and key == "value")
                or (_sensitive_name(key) and not isinstance(item, (dict, list)))
            ):
                redacted[key] = REDACTED
                if hidden_values is not None:
                    stack = [item]
                    while stack:
                        hidden = stack.pop()
                        if isinstance(hidden, str) and hidden and hidden != REDACTED:
                            hidden_values.add(hidden)
                            hidden_values.add(json.dumps(hidden, ensure_ascii=False)[1:-1])
                        elif isinstance(hidden, dict):
                            stack.extend(v for k, v in hidden.items() if k != "$ref")
                        elif isinstance(hidden, list):
                            stack.extend(hidden)
            else:
                redacted[key] = redact_structure(
                    item,
                    sensitive_context=sensitive_context or _sensitive_name(key),
                    sensitive_nodes=sensitive_nodes,
                    sensitive_names=sensitive_names,
                    example_nodes=example_nodes,
                    hidden_values=hidden_values,
                )
        return redacted
    if isinstance(value, list):
        return [
            redact_structure(
                item,
                sensitive_context=sensitive_context,
                sensitive_nodes=sensitive_nodes,
                sensitive_names=sensitive_names,
                example_nodes=example_nodes,
                hidden_values=hidden_values,
            )
            for item in value
        ]
    if isinstance(value, str):
        return redact_text(value)
    return value


class DocumentRedactor:
    """One bounded local-reference walk plus reusable document-aware output scrubbing."""

    def __init__(self, document: dict[str, Any]) -> None:
        names: set[str] = set()
        components = document.get("components", {})
        examples = components.get("examples", {}) if isinstance(components, dict) else {}
        example_nodes = (
            {id(value) for value in examples.values()} if isinstance(examples, dict) else set()
        )
        stack: list[Any] = [document]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if value.get("type") == "apiKey" and isinstance(value.get("name"), str):
                    names.add(value["name"].casefold())
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
        visited: set[tuple[int, bool, bool]] = set()
        sensitive_nodes: set[int] = set()
        pending: list[tuple[Any, bool]] = [(document, False)]
        while pending:
            value, sensitive = pending.pop()
            if not isinstance(value, (dict, list)):
                continue
            if isinstance(value, dict):
                name = str(value.get("name", ""))
                sensitive = (
                    sensitive
                    or _sensitive_name(name)
                    or name.casefold() in names
                    or value.get("format") == "password"
                )
            state = (id(value), sensitive, id(value) in example_nodes)
            if state in visited:
                continue
            visited.add(state)
            if sensitive:
                sensitive_nodes.add(id(value))
            if isinstance(value, list):
                pending.extend((item, sensitive) for item in value)
                continue
            pending.extend(
                (item, sensitive or _sensitive_name(str(key))) for key, item in value.items()
            )
            ref = value.get("$ref")
            if sensitive and isinstance(ref, str) and ref.startswith("#/"):
                target: Any = document
                for token in ref[2:].split("/"):
                    token = token.replace("~1", "/").replace("~0", "~")
                    if isinstance(target, dict):
                        target = target.get(token)
                    elif isinstance(target, list) and token.isascii() and token.isdecimal():
                        target = (
                            target[int(token)]
                            if len(token) < 10 and int(token) < len(target)
                            else None
                        )
                    else:
                        target = None
                if isinstance(target, (dict, list)):
                    if id(value) in example_nodes:
                        example_nodes.add(id(target))
                    pending.append((target, True))
        self.secrets: set[str] = set()
        self.document = redact_structure(
            document,
            sensitive_nodes=frozenset(sensitive_nodes),
            sensitive_names=frozenset(names),
            example_nodes=frozenset(example_nodes),
            hidden_values=self.secrets,
        )

    def redact(self, value: Any) -> Any:
        """Scrub copied values in report quotes, summaries, suggestions and exports."""

        def scrub(item: Any) -> Any:
            if isinstance(item, dict):
                return {key: scrub(child) for key, child in item.items()}
            if isinstance(item, list):
                return [scrub(child) for child in item]
            if isinstance(item, str):
                return redact_text(item, exact_secrets=self.secrets)
            return item

        return scrub(redact_structure(value))

    def redact_model(self, value: ModelT) -> ModelT:
        """Build a schema-valid output copy, never an input to factual scoring.

        Only typed enums/literals and evaluator-generated metadata are exempt from
        text replacement. Arbitrary dictionaries receive no such exemptions.
        IDs containing secrets become stable opaque IDs so report/assessment joins
        survive. The original objects and their scoring evidence stay untouched.
        """

        def scrub(item: Any) -> Any:
            if isinstance(item, StrictModel):
                return self.redact_model(item)
            if isinstance(item, list):
                return [scrub(child) for child in item]
            return self.redact(item)

        updated: dict[str, Any] = {}
        for name, field in type(value).model_fields.items():
            original = getattr(value, name)
            if (
                get_origin(field.annotation) is Literal
                or isinstance(original, Enum)
                or (
                    isinstance(value, EvaluationResult)
                    and name in {"report_sha256", "implementation_version"}
                )
            ):
                updated[name] = original
                continue
            cleaned = scrub(original)
            if isinstance(original, str):
                if name in {"finding_id", "anchor_finding_id"} and cleaned != original:
                    cleaned = "redacted-" + sha256(original.encode("utf-8")).hexdigest()
                elif name in {"pointer", "location"}:
                    if original == "#":
                        cleaned = "#"
                    elif original.startswith("#/"):
                        cleaned = "#/" + self.redact(original[2:])
                # A short secret repeated many times can expand past the field limit.
                for constraint in field.metadata:
                    limit = getattr(constraint, "max_length", None)
                    if limit is not None:
                        cleaned = cleaned[:limit]
            updated[name] = cleaned
        return type(value).model_validate(updated)
