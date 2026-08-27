"""Safe, bounded OpenAPI 3.x parsing with local-only reference resolution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

import yaml
from yaml.events import AliasEvent

from .config import Settings
from .errors import SpecInputError
from .redaction import redact_structure, redact_text

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
SUPPORTED_SUFFIXES = {".json", ".yaml", ".yml"}
MAX_LOCAL_REF_DEPTH = 16


class _NoAliasSafeLoader(yaml.SafeLoader):
    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(AliasEvent):
            raise SpecInputError("YAML aliases are not supported in OpenAPI input")
        return super().compose_node(parent, index)


@dataclass(frozen=True, slots=True)
class LoadedSpec:
    label: str
    document: dict[str, Any]
    sha256: str
    external_refs: tuple[str, ...]

    @property
    def version(self) -> str:
        return str(self.document.get("openapi", "unknown"))

    @property
    def title(self) -> str:
        info = self.document.get("info")
        return str(info.get("title", self.label)) if isinstance(info, dict) else self.label

    @property
    def operation_count(self) -> int:
        paths = self.document.get("paths", {})
        if not isinstance(paths, dict):
            return 0
        return sum(
            1
            for path_item in paths.values()
            if isinstance(path_item, dict)
            for method in path_item
            if str(method).lower() in HTTP_METHODS
        )


def escape_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _validate_shape(document: dict[str, Any], settings: Settings) -> None:
    stack: list[tuple[Any, int]] = [(document, 0)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        if depth > settings.max_nesting_depth:
            raise SpecInputError(
                "The OpenAPI document exceeds the configured maximum nesting depth"
            )
        if isinstance(value, dict):
            nodes += len(value)
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            nodes += len(value)
            stack.extend((item, depth + 1) for item in value)
        if nodes > settings.max_container_nodes:
            raise SpecInputError("The OpenAPI document contains too many fields")


def _collect_external_refs(document: dict[str, Any]) -> tuple[str, ...]:
    refs: set[str] = set()
    stack: list[Any] = [document]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and not ref.startswith("#/"):
                refs.add(ref)
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return tuple(sorted(refs))


def _parse(text: str, filename: str, settings: Settings) -> dict[str, Any]:
    suffix = PurePath(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise SpecInputError("Only .json, .yaml, and .yml OpenAPI files are supported")
    try:
        value = (
            json.loads(text) if suffix == ".json" else yaml.load(text, Loader=_NoAliasSafeLoader)
        )
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SpecInputError("The OpenAPI input is not valid JSON or YAML") from exc
    if not isinstance(value, dict):
        raise SpecInputError("The OpenAPI document root must be an object")
    _validate_shape(value, settings)
    version = value.get("openapi")
    if not isinstance(version, str) or not version.startswith("3."):
        raise SpecInputError("Only OpenAPI 3.x documents are supported")
    if not isinstance(value.get("info"), dict):
        raise SpecInputError("The OpenAPI document must contain an info object")
    if not isinstance(value.get("paths"), dict):
        raise SpecInputError("The OpenAPI document must contain a paths object")
    return value


def load_spec_bytes(data: bytes, filename: str, settings: Settings) -> LoadedSpec:
    if not data:
        raise SpecInputError("The uploaded OpenAPI file is empty")
    if len(data) > settings.max_file_bytes:
        raise SpecInputError(
            f"The OpenAPI file exceeds HY3_MAX_FILE_BYTES ({settings.max_file_bytes} bytes)"
        )
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SpecInputError("The OpenAPI file must be UTF-8 text") from exc
    document = _parse(text, filename, settings)
    return LoadedSpec(
        label=filename,
        document=document,
        sha256=hashlib.sha256(data).hexdigest(),
        external_refs=_collect_external_refs(document),
    )


def load_spec_text(text: str, filename: str, settings: Settings) -> LoadedSpec:
    return load_spec_bytes(text.encode("utf-8"), filename, settings)


def resolve_local_object(
    document: dict[str, Any], value: Any, *, max_depth: int = MAX_LOCAL_REF_DEPTH
) -> dict[str, Any] | None:
    current = value
    seen: set[str] = set()
    for _ in range(max_depth + 1):
        if not isinstance(current, dict):
            return None
        ref = current.get("$ref")
        if ref is None:
            return current
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            return None
        seen.add(ref)
        target: Any = document
        for raw_token in ref[2:].split("/"):
            token = raw_token.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or token not in target:
                return None
            target = target[token]
        current = target
    return None


def compact_for_model(spec: LoadedSpec, max_chars: int) -> str:
    """Return valid, redacted JSON; oversized projections become a bounded prefix envelope."""
    document = spec.document
    components = document.get("components", {})
    compact = {
        "openapi": document.get("openapi"),
        "info": document.get("info"),
        "servers": document.get("servers"),
        "security": document.get("security"),
        "paths": document.get("paths"),
        "components": {
            key: components.get(key)
            for key in (
                "schemas",
                "parameters",
                "requestBodies",
                "responses",
                "headers",
                "securitySchemes",
            )
            if isinstance(components, dict) and key in components
        },
        "external_refs_not_fetched": list(spec.external_refs),
    }
    serialized = json.dumps(
        redact_structure(compact), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    serialized = redact_text(serialized)
    if len(serialized) <= max_chars:
        return serialized

    envelope: dict[str, Any] = {
        "truncated": True,
        "reason": "model character limit",
        "source_sha256": spec.sha256,
        "prefix": "",
    }
    low, high = 0, len(serialized)
    while low <= high:
        middle = (low + high) // 2
        envelope["prefix"] = serialized[:middle]
        candidate = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        if len(candidate) <= max_chars:
            low = middle + 1
        else:
            high = middle - 1
    envelope["prefix"] = serialized[: max(0, high)]
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
