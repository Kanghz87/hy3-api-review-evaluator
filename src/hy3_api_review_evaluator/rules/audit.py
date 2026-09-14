"""Deterministic, bounded checks used as independently verifiable review evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from ..errors import SpecInputError
from ..models import EvidenceReference, ReviewFinding
from ..redaction import redact_structure, redact_text
from ..spec_loader import (
    HTTP_METHODS,
    LoadedSpec,
    escape_pointer_token,
    resolve_local_object_with_pointer,
)

_PATH_PARAMETER = re.compile(r"\{([^{}]+)\}")


def iter_operations(
    document: dict[str, Any],
) -> Iterator[tuple[str, str, dict[str, Any], list[tuple[dict[str, Any], str]], str]]:
    paths = document.get("paths", {})
    if not isinstance(paths, dict):
        return
    for path, raw_path_item in paths.items():
        if not isinstance(raw_path_item, dict):
            continue
        path_pointer = f"#/paths/{escape_pointer_token(str(path))}"
        resolved_path = resolve_local_object_with_pointer(document, raw_path_item, path_pointer)
        path_item, source_pointer = resolved_path or (raw_path_item, path_pointer)
        shared = path_item.get("parameters", [])
        if not isinstance(shared, list):
            shared = []
        for method, operation in path_item.items():
            if str(method).lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_parameters = operation.get("parameters", [])
            if not isinstance(operation_parameters, list):
                operation_parameters = []
            parameters_by_key: dict[tuple[str, str], tuple[dict[str, Any], str]] = {}
            for index, parameter in enumerate(shared):
                if isinstance(parameter, dict):
                    param_pointer = f"{source_pointer}/parameters/{index}"
                    resolved = resolve_local_object_with_pointer(document, parameter, param_pointer)
                    definition, location = resolved or (parameter, param_pointer)
                    parameters_by_key[(str(definition.get("in")), str(definition.get("name")))] = (
                        definition,
                        location,
                    )
            for index, parameter in enumerate(operation_parameters):
                if isinstance(parameter, dict):
                    param_pointer = f"{source_pointer}/{method}/parameters/{index}"
                    resolved = resolve_local_object_with_pointer(document, parameter, param_pointer)
                    definition, location = resolved or (parameter, param_pointer)
                    parameters_by_key[(str(definition.get("in")), str(definition.get("name")))] = (
                        definition,
                        location,
                    )
            yield (
                str(path),
                str(method).lower(),
                operation,
                list(parameters_by_key.values()),
                f"{source_pointer}/{method}",
            )


def _finding_id(category: str, pointer: str, title: str) -> str:
    digest = hashlib.sha256(f"{category}|{pointer}|{title}".encode()).hexdigest()[:12]
    return f"local-{category}-{digest}"


def _quote(value: Any) -> str:
    if isinstance(value, str):
        return redact_text(value)[:1_000]
    return json.dumps(
        redact_structure(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )[:1_000]


def _finding(
    severity: str,
    category: str,
    pointer: str,
    title: str,
    rationale: str,
    suggestion: str,
    value: Any,
) -> ReviewFinding:
    return ReviewFinding(
        finding_id=_finding_id(category, pointer, title),
        title=title,
        category=category,
        severity=severity,  # type: ignore[arg-type]
        location=pointer,
        evidence=[EvidenceReference(pointer=pointer, quote=_quote(value))],
        rationale=rationale,
        suggestion=suggestion,
        source="deterministic",
        confidence=1.0,
    )


def _security_names(requirements: Any) -> set[str]:
    if not isinstance(requirements, list):
        return set()
    return {
        str(name)
        for requirement in requirements
        if isinstance(requirement, dict)
        for name in requirement
    }


def _allows_anonymous(requirements: Any) -> bool:
    return not requirements or (
        isinstance(requirements, list) and any(item == {} for item in requirements)
    )


def audit_spec(spec: LoadedSpec) -> list[ReviewFinding]:
    try:
        return _audit_spec(spec)
    except ValidationError as exc:
        raise SpecInputError(
            "A finding exceeds supported field limits; shorten identifiers or split the input"
        ) from exc


def _audit_spec(spec: LoadedSpec) -> list[ReviewFinding]:
    document = spec.document
    findings: list[ReviewFinding] = []
    info = document.get("info", {})
    if isinstance(info, dict) and not str(info.get("description", "")).strip():
        findings.append(
            _finding(
                "low",
                "documentation",
                "#/info",
                "API description is missing",
                "The info object has no non-empty description, so readers lack API scope "
                "and policy context.",
                "Add info.description covering purpose, audience, authentication, and "
                "version policy.",
                info,
            )
        )

    servers = document.get("servers")
    if not isinstance(servers, list) or not servers:
        findings.append(
            _finding(
                "low",
                "server",
                "#",
                "No server is declared",
                "The document contains no non-empty servers array.",
                "Declare an environment-neutral HTTPS server URL or explain the omission.",
                document,
            )
        )
    else:
        for index, server in enumerate(servers):
            if not isinstance(server, dict):
                continue
            pointer = f"#/servers/{index}/url"
            url = str(server.get("url", ""))
            try:
                parsed = urlsplit(url)
                hostname = parsed.hostname
            except ValueError:
                findings.append(
                    _finding(
                        "medium",
                        "server",
                        pointer,
                        "Server URL is malformed",
                        "The server URL cannot be parsed as a valid URL.",
                        "Replace it with a valid absolute URL or OpenAPI server template.",
                        url,
                    )
                )
                continue
            if parsed.scheme.lower() == "http" and (
                not hostname or hostname.lower() not in {"localhost", "127.0.0.1", "::1"}
            ):
                findings.append(
                    _finding(
                        "high",
                        "transport_security",
                        pointer,
                        "Remote server uses plaintext HTTP",
                        "Credentials and API traffic can be intercepted on a non-local "
                        "HTTP connection.",
                        "Use an HTTPS endpoint and redirect or disable plaintext access.",
                        url,
                    )
                )

    components = document.get("components", {})
    security_schemes = components.get("securitySchemes", {}) if isinstance(components, dict) else {}
    if not isinstance(security_schemes, dict):
        security_schemes = {}
    global_security = document.get("security")
    global_pointer = "#/security"
    for name in sorted(_security_names(global_security) - set(security_schemes)):
        findings.append(
            _finding(
                "high",
                "authentication",
                global_pointer,
                "Security requirement references an undefined scheme",
                f"The global security requirement names '{name}', but "
                "components.securitySchemes does not define it.",
                f"Define components.securitySchemes.{name} or correct the security "
                "requirement name.",
                global_security,
            )
        )

    operation_ids: dict[str, str] = {}
    for path, method, operation, parameters, pointer in iter_operations(document):
        operation_id = str(operation.get("operationId", "")).strip()
        if not operation_id:
            findings.append(
                _finding(
                    "medium",
                    "operation_id",
                    pointer,
                    "operationId is missing",
                    "The operation has no stable identifier for SDK generation and observability.",
                    "Add a unique, stable operationId that will not change with "
                    "documentation wording.",
                    operation,
                )
            )
        elif operation_id in operation_ids:
            id_pointer = f"{pointer}/operationId"
            findings.append(
                _finding(
                    "high",
                    "operation_id",
                    id_pointer,
                    "operationId is duplicated",
                    f"The value '{operation_id}' is already used at {operation_ids[operation_id]}.",
                    "Assign a distinct operationId to every operation.",
                    operation_id,
                )
            )
        else:
            operation_ids[operation_id] = pointer

        if (
            not str(operation.get("summary", "")).strip()
            and not str(operation.get("description", "")).strip()
        ):
            findings.append(
                _finding(
                    "low",
                    "documentation",
                    pointer,
                    "Operation documentation is missing",
                    "The operation has neither a summary nor a description.",
                    "Document behavior, authorization, side effects, and important failures.",
                    operation,
                )
            )

        responses = operation.get("responses")
        responses_pointer = f"{pointer}/responses"
        if not isinstance(responses, dict) or not responses:
            findings.append(
                _finding(
                    "high",
                    "response_contract",
                    pointer,
                    "No responses are declared",
                    "Consumers cannot determine any successful or failure response contract.",
                    "Declare at least one success response and relevant error responses "
                    "with schemas.",
                    operation,
                )
            )
        else:
            if not any(str(code).startswith("2") for code in responses):
                findings.append(
                    _finding(
                        "high",
                        "response_contract",
                        responses_pointer,
                        "No explicit 2xx response is declared",
                        "The response map has no concrete successful status code.",
                        "Declare the successful status code, description, media type, and schema.",
                        responses,
                    )
                )
            if not any(
                str(code).startswith(("4", "5")) or str(code) == "default" for code in responses
            ):
                findings.append(
                    _finding(
                        "low",
                        "response_contract",
                        responses_pointer,
                        "No error response is documented",
                        "The response map contains no 4xx, 5xx, or default response.",
                        "Document important client and server failures with a stable error schema.",
                        responses,
                    )
                )

        placeholders = set(_PATH_PARAMETER.findall(path))
        declared = {
            str(parameter.get("name"))
            for parameter, _ in parameters
            if parameter.get("in") == "path"
        }
        for missing in sorted(placeholders - declared):
            findings.append(
                _finding(
                    "high",
                    "path_parameter",
                    pointer,
                    "Path placeholder has no matching parameter",
                    f"The path contains '{{{missing}}}', but no in:path parameter has that name.",
                    f"Declare '{missing}' as an in:path parameter with required: true "
                    "and a schema.",
                    operation,
                )
            )
        for parameter, parameter_pointer in parameters:
            name = str(parameter.get("name", "?"))
            if parameter.get("in") == "path" and parameter.get("required") is not True:
                findings.append(
                    _finding(
                        "medium",
                        "path_parameter",
                        parameter_pointer,
                        "Path parameter is not required",
                        f"The in:path parameter '{name}' is not marked required: true "
                        "as OpenAPI requires.",
                        "Set required: true on the path parameter.",
                        parameter,
                    )
                )
            if "schema" not in parameter and "content" not in parameter:
                findings.append(
                    _finding(
                        "medium",
                        "parameter",
                        parameter_pointer,
                        "Parameter has no schema or content",
                        f"The parameter '{name}' does not define its accepted representation.",
                        "Add a schema with type, format, and applicable constraints.",
                        parameter,
                    )
                )

        effective_security = operation.get("security", global_security)
        for name in sorted(_security_names(effective_security) - set(security_schemes)):
            security_pointer = f"{pointer}/security" if "security" in operation else global_pointer
            findings.append(
                _finding(
                    "high",
                    "authentication",
                    security_pointer,
                    "Operation references an undefined security scheme",
                    f"The effective security requirement names '{name}', which is not defined.",
                    f"Define components.securitySchemes.{name} or correct the requirement.",
                    effective_security,
                )
            )
        if (
            method in {"post", "put", "patch", "delete"}
            and security_schemes
            and _allows_anonymous(effective_security)
        ):
            findings.append(
                _finding(
                    "medium",
                    "authorization",
                    pointer,
                    "State-changing operation is unauthenticated",
                    "The API defines security schemes, but this state-changing operation "
                    "allows an anonymous alternative in its effective security requirement.",
                    "Declare the intended security requirement or explicitly document "
                    "why the operation is public.",
                    operation,
                )
            )

    if spec.external_refs:
        findings.append(
            _finding(
                "medium",
                "external_reference",
                "#",
                "External references were not resolved",
                "The document contains remote or file references; this application never "
                "downloads them, so dependent evidence is incomplete.",
                "Bundle referenced components locally with #/ pointers before running "
                "a complete review.",
                spec.external_refs[0],
            )
        )

    schemas = components.get("schemas", {}) if isinstance(components, dict) else {}
    if isinstance(schemas, dict):
        for schema_name, schema in schemas.items():
            if not isinstance(schema, dict):
                continue
            # Composition and pattern-based definitions require full JSON Schema evaluation.
            # Do not infer a missing definition from this object's properties alone.
            if any(
                key in schema
                for key in ("$ref", "allOf", "anyOf", "oneOf", "if", "patternProperties")
            ):
                continue
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            if isinstance(required, list) and isinstance(properties, dict):
                for missing in sorted(set(map(str, required)) - set(map(str, properties))):
                    pointer = (
                        f"#/components/schemas/{escape_pointer_token(str(schema_name))}/required"
                    )
                    closed = schema.get("additionalProperties") is False
                    findings.append(
                        _finding(
                            "high" if closed else "low",
                            "schema",
                            pointer,
                            (
                                "Required property is forbidden by the closed schema"
                                if closed
                                else "Required property has no documented constraints"
                            ),
                            f"Schema '{schema_name}' requires '{missing}', but properties "
                            "does not define it. "
                            + (
                                "additionalProperties: false forbids this required field."
                                if closed
                                else "This is valid JSON Schema; its value constraints "
                                "are undocumented, not a proven invalid contract."
                            ),
                            f"Define properties.{missing} with its intended schema; only remove "
                            "it from required if the field is intentionally optional.",
                            required,
                        )
                    )

    groups: dict[str, dict[str, ReviewFinding]] = defaultdict(dict)
    for finding in findings:
        groups[finding.finding_id][finding.rationale] = finding
    unique: list[ReviewFinding] = []
    for finding_id, variants in groups.items():
        for rationale, finding in variants.items():
            if len(variants) > 1:
                suffix = hashlib.sha256(rationale.encode()).hexdigest()[:8]
                finding = finding.model_copy(update={"finding_id": f"{finding_id}-{suffix}"})
            unique.append(finding)
    return sorted(unique, key=lambda item: (item.location, item.category, item.finding_id))
