"""Safe, bounded OpenAPI 3.x parsing with local-only reference resolution."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

import yaml
from yaml.events import AliasEvent

from .config import Settings
from .errors import SpecInputError
from .redaction import DocumentRedactor

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
SUPPORTED_SUFFIXES = {".json", ".yaml", ".yml"}
MAX_LOCAL_REF_DEPTH = 16


class _NoAliasSafeLoader(yaml.SafeLoader):
    def construct_mapping(self, node: Any, deep: bool = False) -> dict[str, Any]:
        if not isinstance(node, yaml.MappingNode):
            raise SpecInputError("A YAML mapping tag must contain an object")
        result: dict[str, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if type(key) not in (str, int):
                raise SpecInputError("YAML mapping keys must be strings or integer response codes")
            key = str(key)
            if key in result:
                raise SpecInputError("Duplicate YAML mapping keys are not supported")
            result[key] = self.construct_object(value_node, deep=deep)
        return result

    def __init__(self, stream: str, settings: Settings) -> None:
        super().__init__(stream)
        self._depth = -1
        self._nodes = 0
        self._settings = settings

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(AliasEvent):
            raise SpecInputError("YAML aliases are not supported in OpenAPI input")
        self._depth += 1
        self._nodes += 1
        try:
            if self._depth > self._settings.max_nesting_depth:
                raise SpecInputError("The OpenAPI document exceeds the maximum nesting depth")
            # Mapping keys also count during construction, unlike the post-parse field count.
            if self._nodes > 2 * self._settings.max_container_nodes + 1:
                raise SpecInputError("The OpenAPI document contains too many fields")
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


# Keep lexical date/timestamp examples as strings instead of constructing Python date objects.
_NoAliasSafeLoader.add_constructor("tag:yaml.org,2002:timestamp", yaml.SafeLoader.construct_scalar)


def _unique_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SpecInputError("Duplicate JSON object keys are not supported")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise SpecInputError("Non-finite numbers are not supported in OpenAPI input")


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
            for raw_item in paths.values()
            for path_item in [resolve_local_object(self.document, raw_item)]
            if path_item is not None
            for method in path_item
            if str(method).lower() in HTTP_METHODS
        )


def escape_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _validate_shape(document: dict[str, Any], settings: Settings) -> None:
    stack: list[tuple[Any, int, int]] = [(document, 0, 1)]
    nodes = 0
    while stack:
        value, depth, pointer_length = stack.pop()
        if pointer_length > 500:
            raise SpecInputError("A document location exceeds the 500-character pointer limit")
        if depth > settings.max_nesting_depth:
            raise SpecInputError(
                "The OpenAPI document exceeds the configured maximum nesting depth"
            )
        if isinstance(value, dict):
            nodes += len(value)
            stack.extend(
                (item, depth + 1, pointer_length + 1 + len(escape_pointer_token(key)))
                for key, item in value.items()
            )
        elif isinstance(value, list):
            nodes += len(value)
            stack.extend(
                (item, depth + 1, pointer_length + 1 + len(str(index)))
                for index, item in enumerate(value)
            )
        elif not (value is None or type(value) in (str, int, bool, float)):
            raise SpecInputError("Only JSON-compatible scalar values are supported")
        elif isinstance(value, float) and not math.isfinite(value):
            raise SpecInputError("Non-finite numbers are not supported in OpenAPI input")
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
        if suffix == ".json":
            value = json.loads(
                text, object_pairs_hook=_unique_json_pairs, parse_constant=_reject_constant
            )
        else:
            loader = _NoAliasSafeLoader(text, settings)
            try:
                value = loader.get_single_data()
            finally:
                loader.dispose()
    except RecursionError as exc:
        raise SpecInputError("The OpenAPI document exceeds the safe parser nesting depth") from exc
    except (ValueError, OverflowError, yaml.YAMLError) as exc:
        raise SpecInputError("The OpenAPI input is not valid JSON or YAML") from exc
    if not isinstance(value, dict):
        raise SpecInputError("The OpenAPI document root must be an object")
    _validate_shape(value, settings)
    version = value.get("openapi")
    if not isinstance(version, str) or not version.startswith("3.") or len(version) > 30:
        raise SpecInputError("Only OpenAPI 3.x documents are supported")
    if not isinstance(value.get("info"), dict):
        raise SpecInputError("The OpenAPI document must contain an info object")
    title = value["info"].get("title", filename)
    if not isinstance(title, str) or not 1 <= len(title) <= 300:
        raise SpecInputError("The API title must contain 1 to 300 characters")
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
    resolved = resolve_local_object_with_pointer(document, value, "#", max_depth=max_depth)
    return resolved[0] if resolved is not None else None


def resolve_local_object_with_pointer(
    document: dict[str, Any],
    value: Any,
    pointer: str,
    *,
    max_depth: int = MAX_LOCAL_REF_DEPTH,
) -> tuple[dict[str, Any], str] | None:
    """Resolve local references while retaining the actual source location of the value."""
    current = value
    seen: set[str] = set()
    for _ in range(max_depth + 1):
        if not isinstance(current, dict):
            return None
        ref = current.get("$ref")
        if ref is None:
            return current, pointer
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            return None
        seen.add(ref)
        target: Any = document
        for raw_token in ref[2:].split("/"):
            token = raw_token.replace("~1", "/").replace("~0", "~")
            if isinstance(target, dict) and token in target:
                target = target[token]
            elif isinstance(target, list) and token.isdecimal() and int(token) < len(target):
                target = target[int(token)]
            else:
                return None
        current = target
        pointer = ref
    return None


def compact_for_model(spec: LoadedSpec, max_chars: int) -> str:
    """Return valid, redacted JSON; oversized projections become a bounded prefix envelope."""
    # Preserve every supplied section, including webhooks/extensions/example definitions.
    # Do not synthesize absent top-level sections: coverage must point to the actual input.
    compact = {
        **DocumentRedactor(spec.document).document,
        "external_refs_not_fetched": list(spec.external_refs),
    }
    serialized = json.dumps(compact, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
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
