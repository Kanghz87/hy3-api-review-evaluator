"""Deterministic, bounded checks used as independently verifiable review evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

from ..evidence import operation_pointer
from ..models import EvidenceReference, ReviewFinding
from ..redaction import redact_structure, redact_text
from ..spec_loader import HTTP_METHODS, LoadedSpec, escape_pointer_token, resolve_local_object

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
        path_item = resolve_local_object(document, raw_path_item) or raw_path_item
        shared = path_item.get("parameters", [])
        if not isinstance(shared, list):
            shared = []
        for method, operation in path_item.items():
            if str(method).lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_parameters = operation.get("parameters", [])
            if not isinstance(operation_parameters, list):
                operation_parameters = []
            parameters: list[tuple[dict[str, Any], str]] = []
            for index, parameter in enumerate(shared):
                if isinstance(parameter, dict):
                    parameters.append(
                        (
                            resolve_local_object(document, parameter) or parameter,
                            f"{path_pointer}/parameters/{index}",
                        )
                    )
            for index, parameter in enumerate(operation_parameters):
                if isinstance(parameter, dict):
                    parameters.append(
                        (
                            resolve_local_object(document, parameter) or parameter,
                            f"{path_pointer}/{str(method).lower()}/parameters/{index}",
                        )
                    )
            yield (
                str(path),
                str(method).lower(),
                operation,
                parameters,
                operation_pointer(str(path), str(method)),
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


def audit_spec(spec: LoadedSpec) -> list[ReviewFinding]:
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
            and not effective_security
        ):
            findings.append(
                _finding(
                    "medium",
                    "authorization",
                    pointer,
                    "State-changing operation is unauthenticated",
                    "The API defines security schemes, but this state-changing operation "
                    "has no effective requirement.",
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
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            if isinstance(required, list) and isinstance(properties, dict):
                for missing in sorted(set(map(str, required)) - set(map(str, properties))):
                    pointer = (
                        f"#/components/schemas/{escape_pointer_token(str(schema_name))}/required"
                    )
                    findings.append(
                        _finding(
                            "high",
                            "schema",
                            pointer,
                            "Required property is not defined",
                            f"Schema '{schema_name}' requires '{missing}', but properties "
                            "does not define it.",
                            f"Define properties.{missing} or remove it from required.",
                            required,
                        )
                    )

    return sorted(findings, key=lambda item: (item.location, item.category, item.finding_id))
